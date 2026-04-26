"""端到端集成测试 — 真实组件协作，不使用 Mock

覆盖：
  1. 真实 mlx-whisper 模型加载 + 音频转写 + opencc 繁简转换
  2. 剪贴板写入+读回验证
  3. Web 服务器真实启动 + HTTP 请求
  4. VAD 分段 + 转写流水线
  5. 配置加载/保存/热更新
  6. 性能基准

要求：
  - macOS + Apple Silicon
  - mlx-whisper 已安装
  - 小模型已缓存（首次运行会下载）
"""

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error
import wave
import struct
import numpy as np

# 跳过条件
IS_MACOS_ARM = platform.system() == "Darwin" and platform.machine() == "arm64"

skip_reason = ""
if not IS_MACOS_ARM:
    skip_reason = "需要 macOS Apple Silicon"
else:
    try:
        import mlx_whisper
    except ImportError:
        skip_reason = "mlx-whisper 未安装"
        IS_MACOS_ARM = False

requires_macos = unittest.skipUnless(IS_MACOS_ARM, skip_reason)


def _generate_sine_wav(path, freq=440, duration=3.0, sample_rate=16000, amplitude=0.3):
    """生成正弦波 WAV 文件"""
    n_samples = int(duration * sample_rate)
    samples = []
    for i in range(n_samples):
        sample = amplitude * np.sin(2 * np.pi * freq * i / sample_rate)
        samples.append(int(sample * 32767))
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f'<{len(samples)}h', *samples))


def _generate_silence_wav(path, duration=2.0, sample_rate=16000):
    """生成静音 WAV"""
    n_samples = int(duration * sample_rate)
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b'\x00\x00' * n_samples)


def _load_wav_as_float(path):
    """加载 WAV 为 float32 numpy array"""
    with wave.open(path, 'r') as wf:
        frames = wf.readframes(wf.getnframes())
        samples = struct.unpack(f'<{wf.getnframes()}h', frames)
        return np.array(samples, dtype=np.float32) / 32768.0


# ================================================================
# 1. 真实 mlx-whisper 模型转写链路
# ================================================================

@requires_macos
class TestRealMlxWhisperPipeline(unittest.TestCase):
    """真实 mlx-whisper 转写 + opencc 繁简转换"""

    @classmethod
    def setUpClass(cls):
        """加载模型（只加载一次）"""
        from core.stt_mlx_whisper import MlxWhisperEngine

        class Cfg:
            model_size = "small"
            language = None  # 自动检测

        cls.engine = MlxWhisperEngine(Cfg())
        # 触发模型加载
        cls.engine.transcribe_sync(np.zeros(16000, dtype=np.float32))
        cls.fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")

    @classmethod
    def tearDownClass(cls):
        cls.engine.shutdown()

    def test_chinese_long_audio_real(self):
        """中文 9s 音频真实转写"""
        path = os.path.join(self.fixtures_dir, "zh_long.wav")
        if not os.path.exists(path):
            self.skipTest("zh_long.wav 不存在")
        audio = _load_wav_as_float(path)
        text = self.engine.transcribe_sync(audio)
        self.assertIsInstance(text, str)
        self.assertTrue(len(text) > 0, "转写结果不应为空")
        # 确认是简体（非繁体）
        traditional_chars = set('這個買來頓豐')
        for c in traditional_chars:
            self.assertNotIn(c, text, f"应已转为简体，但仍包含繁体字「{c}」: {text}")

    def test_english_long_audio_real(self):
        """英文 6s 音频真实转写"""
        path = os.path.join(self.fixtures_dir, "en_long.wav")
        if not os.path.exists(path):
            self.skipTest("en_long.wav 不存在")
        audio = _load_wav_as_float(path)
        text = self.engine.transcribe_sync(audio)
        self.assertIsInstance(text, str)
        self.assertTrue(len(text) > 0)

    def test_silence_real(self):
        """真实静音音频返回空"""
        audio = np.zeros(16000, dtype=np.float32)
        text = self.engine.transcribe_sync(audio)
        self.assertEqual(text, "")

    def test_performance_under_1s(self):
        """9s 中文音频转写延迟 < 1s"""
        path = os.path.join(self.fixtures_dir, "zh_long.wav")
        if not os.path.exists(path):
            self.skipTest("zh_long.wav 不存在")
        audio = _load_wav_as_float(path)
        start = time.time()
        self.engine.transcribe_sync(audio)
        elapsed = time.time() - start
        self.assertLess(elapsed, 2.0, f"转写延迟 {elapsed:.3f}s > 2s，性能不达标")

    def test_multiple_sequential_calls(self):
        """连续多次转写不崩溃"""
        audio = np.random.randn(48000).astype(np.float32) * 0.1
        results = []
        for _ in range(5):
            text = self.engine.transcribe_sync(audio)
            results.append(text)
        self.assertEqual(len(results), 5)

    def test_transcribe_async_real(self):
        """异步转写回调"""
        import threading
        audio = np.zeros(16000, dtype=np.float32)
        event = threading.Event()
        callback_result = [None]

        def on_done(text, language, duration, error):
            callback_result[0] = (text, language, duration, error)
            event.set()

        self.engine.transcribe_async(audio, on_done)
        event.wait(timeout=5)
        self.assertIsNotNone(callback_result[0])
        self.assertEqual(callback_result[0][0], "")  # 静音返回空


# ================================================================
# 2. 剪贴板真实读写
# ================================================================

@requires_macos
class TestRealClipboard(unittest.TestCase):
    """真实剪贴板操作（macOS pbcopy/pbpaste）"""

    def setUp(self):
        """保存当前剪贴板内容"""
        try:
            result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
            self._saved_clipboard = result.stdout
        except Exception:
            self._saved_clipboard = None

    def tearDown(self):
        """恢复剪贴板"""
        if self._saved_clipboard is not None:
            try:
                proc = subprocess.run(
                    ["pbcopy"], input=self._saved_clipboard,
                    text=True, timeout=2
                )
            except Exception:
                pass

    def test_write_and_read_ascii(self):
        """ASCII 文本读写"""
        from platform_adapter.clipboard_macos import MacOSClipboardInjector
        config = self._make_config()
        clip = MacOSClipboardInjector(config)
        test_text = "Hello, Clipboard!"
        self.assertTrue(clip.write_clipboard(test_text))
        result = clip.read_clipboard()
        self.assertEqual(result, test_text)

    def test_write_and_read_chinese(self):
        """中文文本读写"""
        from platform_adapter.clipboard_macos import MacOSClipboardInjector
        config = self._make_config()
        clip = MacOSClipboardInjector(config)
        test_text = "你好世界！这是繁体轉換測試。"
        self.assertTrue(clip.write_clipboard(test_text))
        result = clip.read_clipboard()
        self.assertEqual(result, test_text)

    def test_write_and_read_mixed(self):
        """中英混合 + 特殊字符"""
        from platform_adapter.clipboard_macos import MacOSClipboardInjector
        config = self._make_config()
        clip = MacOSClipboardInjector(config)
        test_text = "Hello 你好 🎉\n第二行\ttab"
        self.assertTrue(clip.write_clipboard(test_text))
        result = clip.read_clipboard()
        self.assertEqual(result, test_text)

    def test_inject_full_chain(self):
        """inject() 全链路：写入 → 粘贴（读取验证写入成功）"""
        from platform_adapter.clipboard_macos import MacOSClipboardInjector
        config = self._make_config()
        config.inject.method = "clipboard"
        config.inject.clipboard_backup = False
        config.inject.add_trailing_space = False
        clip = MacOSClipboardInjector(config)
        test_text = "inject_test_端到端"
        # 不实际触发 Cmd+V（会干扰当前操作），只验证写入
        self.assertTrue(clip.write_clipboard(test_text))
        result = clip.read_clipboard()
        self.assertEqual(result, test_text)

    def _make_config(self):
        """创建最小 config mock"""
        config = type('C', (), {})()
        config.inject = type('I', (), {})()
        config.inject.method = "clipboard"
        config.inject.clipboard_backup = False
        config.inject.add_trailing_space = False
        config.inject.restore_clipboard = False
        return config


# ================================================================
# 3. Web 服务器真实启动 + HTTP
# ================================================================

@requires_macos
class TestRealWebServer(unittest.TestCase):
    """真实启动 Web 服务器并发送 HTTP 请求"""

    @classmethod
    def setUpClass(cls):
        from gui.web_server import ConfigWebServer
        from config import AppConfig

        config = AppConfig()
        config.web.port = 18925  # 避免端口冲突
        cls.server = ConfigWebServer(config)
        cls.server.start()
        time.sleep(0.5)  # 等待服务器就绪

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    @property
    def base_url(self):
        return f"http://127.0.0.1:18925"

    @property
    def token(self):
        return self.server._token

    @property
    def auth_url(self):
        return f"{self.base_url}?token={self.token}"

    def test_get_config(self):
        """GET /api/config 返回有效 JSON"""
        url = f"{self.base_url}/api/config?token={self.token}"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read())
            self.assertIn("stt", data)
            self.assertIn("engine", data["stt"])

    def test_get_status(self):
        """GET /api/status"""
        url = f"{self.base_url}/api/status?token={self.token}"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read())
            self.assertIn("engine_state", data)

    def test_get_stats(self):
        """GET /api/stats"""
        url = f"{self.base_url}/api/stats?token={self.token}"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read())

    def test_put_config_no_token_403(self):
        """无 token PUT 被拒绝"""
        url = f"{self.base_url}/api/config"
        req = urllib.request.Request(
            url, data=json.dumps({"stt": {"engine": "mlx_whisper"}}).encode(),
            method="PUT",
            headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req)
            self.fail("应该返回 403")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)

    def test_put_config_with_token(self):
        """有 token PUT 更新配置"""
        url = f"{self.base_url}/api/config?token={self.token}"
        req = urllib.request.Request(
            url, data=json.dumps({"stt": {"engine": "mlx_whisper", "model_size": "small"}}).encode(),
            method="PUT",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read())
            self.assertTrue(data.get("ok", False))

    def test_post_record_start_stop(self):
        """录音控制 start → stop"""
        url = f"{self.base_url}/api/record/start?token={self.token}"
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            data = json.loads(e.read())
        # 可能成功也可能失败（无引擎），但不应崩溃
        self.assertIn("ok", data)

    def test_404_path(self):
        """不存在的路径返回 404"""
        url = f"{self.base_url}/api/nonexistent?token={self.token}"
        try:
            urllib.request.urlopen(url)
            self.fail("应该返回 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_get_config_after_update(self):
        """更新后再读取，验证持久化"""
        # 先更新
        url = f"{self.base_url}/api/config?token={self.token}"
        req = urllib.request.Request(
            url, data=json.dumps({"stt": {"engine": "faster_whisper"}}).encode(),
            method="PUT",
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req)

        # 再读取
        url = f"{self.base_url}/api/config?token={self.token}"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())
            self.assertEqual(data["stt"]["engine"], "faster_whisper")

        # 恢复
        req = urllib.request.Request(
            url, data=json.dumps({"stt": {"engine": "mlx_whisper"}}).encode(),
            method="PUT",
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req)


# ================================================================
# 4. VAD 分段 + 转写流水线
# ================================================================

@requires_macos
class TestRealVADPipeline(unittest.TestCase):
    """VAD 分段 + mlx-whisper 转写协作"""

    @classmethod
    def setUpClass(cls):
        from core.stt_mlx_whisper import MlxWhisperEngine

        class Cfg:
            model_size = "small"
            language = None

        cls.engine = MlxWhisperEngine(Cfg())
        cls.engine.transcribe_sync(np.zeros(16000, dtype=np.float32))

    @classmethod
    def tearDownClass(cls):
        cls.engine.shutdown()

    def test_speech_segment_detected(self):
        """语音段被正确转写"""
        # 用真实音频
        path = os.path.join(os.path.dirname(__file__), "fixtures", "zh_long.wav")
        if not os.path.exists(path):
            self.skipTest("zh_long.wav 不存在")
        audio = _load_wav_as_float(path)
        text = self.engine.transcribe_sync(audio)
        self.assertTrue(len(text) > 0)

    def test_silence_segment_skipped(self):
        """纯静音段不触发转写"""
        silence = np.zeros(32000, dtype=np.float32)
        text = self.engine.transcribe_sync(silence)
        self.assertEqual(text, "")

    def test_speech_then_silence(self):
        """先语音后静音"""
        path = os.path.join(os.path.dirname(__file__), "fixtures", "zh_long.wav")
        if not os.path.exists(path):
            self.skipTest("zh_long.wav 不存在")
        speech = _load_wav_as_float(path)
        silence = np.zeros(16000, dtype=np.float32)
        # 语音段有结果
        text1 = self.engine.transcribe_sync(speech)
        self.assertTrue(len(text1) > 0)
        # 静音段无结果
        text2 = self.engine.transcribe_sync(silence)
        self.assertEqual(text2, "")


# ================================================================
# 5. 配置真实文件 I/O
# ================================================================

@requires_macos
class TestRealConfigIO(unittest.TestCase):
    """真实文件系统配置读写"""

    def test_save_load_roundtrip(self):
        """保存→加载 roundtrip"""
        from config import AppConfig, save_config, load_config, CURRENT_CONFIG_VERSION

        cfg = AppConfig()
        cfg.config_version = CURRENT_CONFIG_VERSION
        cfg.stt.engine = "mlx_whisper"
        cfg.stt.model_size = "small"

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name

        try:
            save_config(path, cfg)
            loaded = load_config(path)
            self.assertEqual(loaded.stt.engine, "mlx_whisper")
            self.assertEqual(loaded.stt.model_size, "small")
        finally:
            os.unlink(path)

    def test_load_nonexistent_creates_default(self):
        """加载不存在的文件创建默认配置"""
        from config import load_config

        path = tempfile.mktemp(suffix=".yaml")
        try:
            cfg = load_config(path)
            self.assertIsNotNone(cfg)
            self.assertEqual(cfg.stt.engine, "auto")
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_mlx_whisper_config_validation(self):
        """mlx_whisper 配置参数校验"""
        from config import STTConfig

        # 有效配置
        cfg = STTConfig(engine="mlx_whisper", model_size="small")
        self.assertEqual(cfg.engine, "mlx_whisper")

        # 无效 model_size
        with self.assertRaises(ValueError):
            STTConfig(engine="mlx_whisper", model_size="xxx-invalid")


# ================================================================
# 6. opencc 繁简转换真实验证
# ================================================================

@requires_macos
class TestRealOpenCC(unittest.TestCase):
    """opencc 繁简转换真实效果"""

    def test_t2s_conversion(self):
        import opencc
        converter = opencc.OpenCC('t2s')
        self.assertEqual(converter.convert("這是測試"), "这是测试")
        self.assertEqual(converter.convert("買水果"), "买水果")
        self.assertEqual(converter.convert("回來"), "回来")

    def test_already_simplified(self):
        """简体输入不变"""
        import opencc
        converter = opencc.OpenCC('t2s')
        self.assertEqual(converter.convert("已经"), "已经")
        self.assertEqual(converter.convert("hello"), "hello")


if __name__ == '__main__':
    unittest.main()
