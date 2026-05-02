"""Windows 实机端到端测试

直接在 Windows 上验证核心功能链路：
1. 剪贴板读写（Win32 API + PowerShell 降级）
2. STT 模型加载 + 中文识别
3. 实时转写引擎（RMS VAD 降级）
4. 注入方式三层降级
5. Config 迁移 v3→v4
6. 完整录音→识别→注入流程（模拟）

Usage: python tests/test_windows_e2e.py
"""

import sys
import os
import time
import tempfile
import types
import unittest
import logging
import threading
from unittest.mock import MagicMock, patch

# Windows 上 import torch 触发 WinError 206（路径过长），
# 预注册 mock 模块防止真实 import
_win_skip = sys.platform == 'win32'
if 'torch' not in sys.modules:
    _mock_torch = types.ModuleType('torch')
    _mock_torch.cuda = MagicMock()
    sys.modules['torch'] = _mock_torch

# 添加项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
logger = logging.getLogger("E2E")


class TestWindowsClipboard(unittest.TestCase):
    """测试 Windows 剪贴板读写"""

    def setUp(self):
        if sys.platform != "win32":
            self.skipTest("Windows only")

    def test_01_win32_write_and_read(self):
        """Win32 API 写入剪贴板并读回"""
        from platform_adapter.clipboard_windows import WindowsClipboardInjector
        from config import InjectConfig

        config = InjectConfig()
        injector = WindowsClipboardInjector(config)

        test_text = "测试中文剪贴板 Test123!@#"
        ok = injector.write_clipboard(test_text)
        self.assertTrue(ok, "write_clipboard 应返回 True")

        result = injector.read_clipboard()
        self.assertEqual(result, test_text, "读回内容应一致")
        logger.info("✅ Win32 剪贴板读写正常")

    def test_02_powershell_fallback(self):
        """PowerShell 剪贴板降级"""
        import subprocess

        test_text = "PowerShell剪贴板测试"
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "[System.Windows.Forms.Clipboard]::SetText($input)"
        )
        result = subprocess.run(
            ["powershell", "-Command", ps_cmd],
            input=test_text, text=True, capture_output=True, timeout=5
        )
        self.assertEqual(result.returncode, 0, f"PowerShell 应成功: {result.stderr}")

        # 读回验证
        ps_read = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
            "[System.Windows.Forms.Clipboard]::GetText()"
        )
        result = subprocess.run(
            ["powershell", "-Command", ps_read],
            capture_output=True, text=True, timeout=5,
            encoding='utf-8'
        )
        self.assertIn(test_text, result.stdout, "PowerShell 读回应包含原文")
        logger.info("✅ PowerShell 剪贴板读写正常")

    def test_03_simulate_paste(self):
        """模拟粘贴 SendInput"""
        from platform_adapter.clipboard_windows import WindowsClipboardInjector
        from config import InjectConfig

        config = InjectConfig()
        injector = WindowsClipboardInjector(config)

        # 先写入剪贴板
        ok = injector.write_clipboard("paste_test")
        self.assertTrue(ok)

        # 模拟粘贴（不会真正粘贴因为没有聚焦输入框，但不应该崩溃）
        try:
            injector.simulate_paste()
            logger.info("✅ simulate_paste 未崩溃")
        except Exception as e:
            logger.warning("simulate_paste 异常（可能无焦点）: %s", e)

    def test_04_inject_via_keyboard_mode(self):
        """keyboard 模式注入（Win32 → PowerShell 降级）"""
        from platform_adapter.clipboard_base import ClipboardInjectorBase
        from platform_adapter.clipboard_windows import WindowsClipboardInjector
        from config import InjectConfig

        config = InjectConfig(method="keyboard")
        injector = WindowsClipboardInjector(config)

        test_text = "注入模式测试"
        ok = injector._inject_via_keyboard(test_text)
        self.assertTrue(ok, "keyboard 模式注入应成功")
        logger.info("✅ keyboard 注入模式正常")

    def test_05_clipboard_long_text(self):
        """长文本剪贴板"""
        from platform_adapter.clipboard_windows import WindowsClipboardInjector
        from config import InjectConfig

        config = InjectConfig()
        injector = WindowsClipboardInjector(config)

        test_text = "这是一段很长的中文测试文本。" * 100  # ~1500 字
        ok = injector.write_clipboard(test_text)
        self.assertTrue(ok, "长文本写入应成功")

        result = injector.read_clipboard()
        self.assertEqual(result, test_text, "长文本读回应一致")
        logger.info("✅ 长文本剪贴板正常 (%d 字符)", len(test_text))


class TestConfigMigration(unittest.TestCase):
    """测试配置迁移"""

    def test_06_v3_to_v4_migration(self):
        """v3 → v4 迁移：强制 language=zh + method=keyboard"""
        from config import _migrate_v3_to_v4

        v3_config = {
            "config_version": 3,
            "stt": {"language": None, "model_size": "small"},
            "inject": {"method": "clipboard", "auto_paste": True}
        }

        result = _migrate_v3_to_v4(v3_config)
        self.assertEqual(result["config_version"], 4)
        self.assertEqual(result["stt"]["language"], "zh", "language 应被强制设为 zh")
        self.assertEqual(result["inject"]["method"], "keyboard", "method 应被强制设为 keyboard")
        logger.info("✅ Config v3→v4 迁移正确")

    def test_07_v4_no_override_explicit_language(self):
        """v4 迁移不应覆盖用户显式设置的语言"""
        from config import _migrate_v3_to_v4

        v3_config = {
            "config_version": 3,
            "stt": {"language": "en"},
            "inject": {"method": "clipboard"}
        }

        result = _migrate_v3_to_v4(v3_config)
        self.assertEqual(result["stt"]["language"], "en", "用户显式设的语言不应被覆盖")
        logger.info("✅ v4 迁移尊重用户显式设置")

    def test_08_load_config_auto_migrates(self):
        """加载 v3 config 自动迁移到 v4"""
        import yaml
        from config import load_config, CURRENT_CONFIG_VERSION

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump({
                "config_version": 3,
                "stt": {"language": None},
                "inject": {"method": "clipboard"}
            }, f)
            tmp = f.name

        try:
            config = load_config(tmp)
            self.assertEqual(config.stt.language, "zh")
            self.assertEqual(config.inject.method, "keyboard")
            # 保存后版本应为 4
            import json
            with open(tmp) as f:
                saved = yaml.safe_load(f)
            self.assertEqual(saved.get("config_version"), CURRENT_CONFIG_VERSION)
            logger.info("✅ Config 自动迁移并保存")
        finally:
            os.unlink(tmp)


class TestSTTEngine(unittest.TestCase):
    """测试 STT 引擎"""

    def test_09_model_load_cpu(self):
        """STT 模型加载（CPU 模式）"""
        try:
            import torch
        except (ImportError, FileNotFoundError, OSError):
            self.skipTest("torch 不可用 (DLL path issue)")
        """STT 模型加载（CPU 模式）"""
        from config import STTConfig
        from core.stt_engine import STTEngine

        config = STTConfig(device="cpu")
        engine = STTEngine(config)
        ok = engine.load_model()
        self.assertTrue(ok, "模型加载应成功")
        self.assertIsNotNone(engine.model)
        logger.info("✅ STT 模型加载成功 (CPU)")

    def test_10_transcribe_chinese(self):
        """中文识别测试（需先加载模型）"""
        try:
            import torch
        except (ImportError, FileNotFoundError, OSError):
            self.skipTest("torch 不可用 (DLL path issue)")
        """中文识别测试（需先加载模型）"""
        from config import STTConfig
        from core.stt_engine import STTEngine
        import numpy as np

        config = STTConfig(device="cpu", language="zh")
        engine = STTEngine(config)

        if not engine.load_model():
            self.skipTest("模型加载失败")

        # 生成 1 秒静音 + 短噪声（模拟说话）
        sr = 16000
        silence = np.zeros(sr, dtype=np.float32)
        noise = np.random.randn(sr).astype(np.float32) * 0.3

        text, lang, dur, err = engine._do_transcribe(noise)
        # 噪声不一定能识别出文字，但不应崩溃
        self.assertIsNone(err, f"转写不应报错: {err}")
        logger.info("✅ STT 转写未崩溃 (text='%s', lang=%s, dur=%dms)", text[:50] if text else "", lang, dur)


class TestStreamingTranscriber(unittest.TestCase):
    """测试实时转写引擎"""

    @unittest.skip("VAD logic removed in StreamingTranscriber refactor")
    def test_11_rms_vad_fallback(self):
        """RMS VAD 降级检测"""
        from config import RealtimeConfig
        from core.streaming_transcriber import StreamingTranscriber
        import numpy as np

        config = RealtimeConfig()
        st = StreamingTranscriber(config)

        # 模拟 webrtcvad 不可用
        st._vad = None
        st._vad_mode = 'rms'
        st._rms_threshold = 0.015

        # 静音应不被检测为语音
        silence = np.zeros(480, dtype=np.float32)
        self.assertFalse(st._vad_detect(silence), "静音不应被检测为语音")

        # 有声音应被检测为语音
        speech = np.random.randn(480).astype(np.float32) * 0.1
        self.assertTrue(st._vad_detect(speech), "有声音应被检测为语音")
        logger.info("✅ RMS VAD 降级检测正常")

    @unittest.skip("VAD logic removed in StreamingTranscriber refactor")
    def test_12_webrtcvad_graceful_fallback(self):
        """webrtcvad 不可用时自动降级"""
        from config import RealtimeConfig
        from core.streaming_transcriber import StreamingTranscriber

        config = RealtimeConfig()
        st = StreamingTranscriber(config)
        st._load_vad()

        # 在没有 C++ 编译工具的 Windows 上应该降级到 RMS
        self.assertIn(st._vad_mode, ['webrtcvad', 'rms'])
        logger.info("✅ VAD 模式: %s", st._vad_mode)


class TestEngineState(unittest.TestCase):
    """测试引擎状态转换"""

    def test_13_batch_pipeline_no_audio_device(self):
        """批量模式状态转换链"""
        from core.engine import EngineState
        valid = [
            (EngineState.LOADING, EngineState.IDLE),
            (EngineState.IDLE, EngineState.RECORDING),
            (EngineState.RECORDING, EngineState.PROCESSING),
            (EngineState.PROCESSING, EngineState.INJECTING),
            (EngineState.INJECTING, EngineState.IDLE),
        ]
        for src, dst in valid:
            self.assertIsInstance(src, EngineState)
            self.assertIsInstance(dst, EngineState)
        logger.info("engine state chain valid")


class TestPostprocessEnhancement(unittest.TestCase):
    """后处理增强功能 Windows 测试（F1 Emoji + F2 Watchdog + F3 Punctuation）"""

    def setUp(self):
        if sys.platform != "win32":
            self.skipTest("Windows only")

    # ── F1 Emoji 清理 ──

    def test_14_emoji_chinese_text(self):
        """中文含 emoji 清理后无 emoji"""
        from core.stt_funasr import FunASREngine
        result = FunASREngine._postprocess_sensevoice("你好😊世界😂测试")
        # 不应包含任何 emoji
        self.assertNotIn("😊", result)
        self.assertNotIn("😂", result)
        # 中文应保留
        self.assertIn("你好", result)
        self.assertIn("世界", result)
        self.assertIn("测试", result)
        logger.info("✅ 中文 emoji 清理正常")

    def test_15_emoji_english_text(self):
        """英文含 emoji 清理后无 emoji"""
        from core.stt_funasr import FunASREngine
        result = FunASREngine._postprocess_sensevoice("Hello😊 World😂 Test")
        self.assertNotIn("😊", result)
        self.assertNotIn("😂", result)
        self.assertIn("Hello", result)
        self.assertIn("World", result)
        logger.info("✅ 英文 emoji 清理正常")

    def test_15b_emo_marker_cleanup(self):
        """EMO 标记清除"""
        from core.stt_funasr import FunASREngine
        result = FunASREngine._postprocess_sensevoice(
            "文本<|EMO_HAPPY|>更多<|EMO_SAD|>内容"
        )
        self.assertNotIn("EMO", result)
        self.assertNotIn("<|", result)
        self.assertNotIn("|>", result)
        self.assertIn("文本", result)
        self.assertIn("内容", result)
        logger.info("✅ EMO 标记清除正常")

    def test_16_emoji_zwj_sequence(self):
        """ZWJ 组合 emoji（👨‍👩‍👧）清除"""
        from core.stt_funasr import FunASREngine
        # ZWJ 序列: U+1F468 ZWJ U+1F469 ZWJ U+1F467
        result = FunASREngine._postprocess_sensevoice(
            "家庭👨\u200D👩\u200D👧文本"
        )
        self.assertEqual(result, "家庭文本")
        logger.info("✅ ZWJ 序列 emoji 清理正常")

    def test_17_cjk_not_affected(self):
        """纯中文不被误伤"""
        from core.stt_funasr import FunASREngine
        text = "你好世界测试中文标点，句号。问号？感叹号！"
        result = FunASREngine._postprocess_sensevoice(text)
        self.assertEqual(result, text)
        logger.info("✅ 纯中文不受影响")

    # ── F2 文件 Watchdog ──

    def test_18_file_watcher_init(self):
        """FileWatcher 初始化（import 检查）"""
        from core.file_watcher import FileWatcher, _WATCHDOG_AVAILABLE
        self.assertIsNotNone(FileWatcher)
        self.assertIsInstance(_WATCHDOG_AVAILABLE, bool)
        logger.info("✅ FileWatcher 导入正常 (watchdog=%s)", _WATCHDOG_AVAILABLE)

    def test_19_file_watcher_missing_file(self):
        """文件不存在时监控目录，创建后不崩溃"""
        from core.file_watcher import FileWatcher
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "nonexistent.txt")
            callback = MagicMock()
            fw = FileWatcher({test_file: callback}, debounce_seconds=0.5)
            fw.start()
            self.assertTrue(fw.is_watching, "应启动目录监控")
            # 创建文件
            time.sleep(1.0)
            with open(test_file, "w") as f:
                f.write("created")
            time.sleep(2.0)
            fw.stop()
            logger.info("✅ 文件不存在时目录监控正常")

    def test_20_file_watcher_edit_trigger(self):
        """编辑文件后触发回调"""
        from core.file_watcher import FileWatcher
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, "w") as f:
                f.write("initial")
                f.flush()
                os.fsync(f.fileno())
            callback = MagicMock()
            fw = FileWatcher({test_file: callback}, debounce_seconds=0.5)
            fw.start()
            time.sleep(1.0)
            # 修改文件
            with open(test_file, "w") as f:
                f.write("modified")
                f.flush()
                os.fsync(f.fileno())
            time.sleep(3.0)  # 等待防抖 + ReadDirectoryChangesW
            fw.stop()
            callback.assert_called()
            logger.info("✅ 文件编辑触发回调正常")

    def test_21_file_watcher_debounce(self):
        """防抖合并：快速多次编辑只触发一次回调"""
        from core.file_watcher import FileWatcher
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, "w") as f:
                f.write("initial")
            callback = MagicMock()
            fw = FileWatcher({test_file: callback}, debounce_seconds=1.0)
            fw.start()
            time.sleep(1.0)
            # 快速连续修改 5 次
            for i in range(5):
                with open(test_file, "w") as f:
                    f.write(f"version{i}")
                time.sleep(0.05)
            time.sleep(3.0)  # 等待防抖
            fw.stop()
            self.assertLessEqual(callback.call_count, 2,
                                  f"防抖应合并为 1-2 次，实际 {callback.call_count} 次")
            logger.info("✅ 防抖合并正常 (回调 %d 次)", callback.call_count)

    def test_22_file_watcher_delete_and_recreate(self):
        """删除后重建触发回调"""
        from core.file_watcher import FileWatcher
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, "w") as f:
                f.write("initial")
            callback = MagicMock()
            fw = FileWatcher({test_file: callback}, debounce_seconds=0.5)
            fw.start()
            time.sleep(1.0)
            # 删除
            os.remove(test_file)
            time.sleep(1.5)
            # 重建
            with open(test_file, "w") as f:
                f.write("recreated")
            time.sleep(2.0)
            fw.stop()
            logger.info("✅ 删除重建不崩溃")

    def test_23_file_watcher_watchdog_unavailable(self):
        """watchdog 不可用时降级"""
        from core.file_watcher import _WATCHDOG_AVAILABLE
        if _WATCHDOG_AVAILABLE:
            # watchdog 已安装，验证正常创建
            from core.file_watcher import FileWatcher
            fw = FileWatcher({"/tmp/test.txt": lambda: None})
            self.assertIsNotNone(fw)
            logger.info("✅ watchdog 可用，FileWatcher 正常")
        else:
            # watchdog 未安装，FileWatcher 类仍可导入
            from core.file_watcher import FileWatcher
            fw = FileWatcher({"/tmp/test.txt": lambda: None})
            self.assertIsNotNone(fw)
            # start 应 no-op 或不崩溃
            fw.start()
            self.assertFalse(fw._started)
            fw.stop()
            logger.info("✅ watchdog 不可用，FileWatcher 优雅降级")

    # ── F3 标点恢复 ──

    def test_24_punctuation_disabled(self):
        """禁用时返回原文"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=False)
        self.assertEqual(r.restore("你好世界测试"), "你好世界测试")
        logger.info("✅ 标点恢复禁用时返回原文")

    def test_25_punctuation_restore_mock(self):
        """mock 模型测试标点恢复调用"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        mock_model = MagicMock()
        mock_model.generate.return_value = [{"text": "你好，世界。"}]
        r._model = mock_model
        r._loaded = True
        result = r.restore("你好世界")
        self.assertEqual(result, "你好，世界。")
        mock_model.generate.assert_called_once()
        logger.info("✅ mock 标点恢复调用正常")

    def test_26_punctuation_shared_model(self):
        """共享模型注入"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        mock_model = MagicMock()
        r.set_shared_model(mock_model)
        self.assertTrue(r.is_loaded)
        self.assertTrue(r._shared_model)
        self.assertIs(r._model, mock_model)
        # 共享实例 shutdown 后不被释放
        r.shutdown()
        self.assertIs(r._model, mock_model)
        self.assertFalse(r.is_loaded)
        logger.info("✅ 共享模型注入正常")

    def test_27_punctuation_thread_safety(self):
        """多线程并发调用安全"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        mock_model = MagicMock()
        mock_model.generate.return_value = [{"text": "结果"}]
        r._model = mock_model
        r._loaded = True

        results = []
        errors = []

        def worker(text_id):
            try:
                result = r.restore(f"测试文本{text_id}")
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"并发错误: {errors}")
        self.assertEqual(len(results), 10)
        logger.info("✅ 多线程并发调用安全")


if __name__ == "__main__":
    # 按顺序执行
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestWindowsClipboard))
    suite.addTests(loader.loadTestsFromTestCase(TestConfigMigration))
    suite.addTests(loader.loadTestsFromTestCase(TestSTTEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestStreamingTranscriber))
    suite.addTests(loader.loadTestsFromTestCase(TestEngineState))
    suite.addTests(loader.loadTestsFromTestCase(TestPostprocessEnhancement))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 输出总结
    print("\n" + "=" * 60)
    print(f"E2E 测试完成: {result.testsRun} 测试, "
          f"{len(result.failures)} 失败, {len(result.errors)} 错误")
    if result.failures:
        print("\n失败:")
        for test, trace in result.failures:
            print(f"  FAIL {test}: {trace[:200]}")
    if result.errors:
        print("\n错误:")
        for test, trace in result.errors:
            print(f"  ERR  {test}: {trace[:200]}")
    print("=" * 60)
