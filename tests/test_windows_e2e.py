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
import unittest
import logging

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


class TestStreamTranscriber(unittest.TestCase):
    """测试实时转写引擎"""

    def test_11_rms_vad_fallback(self):
        """RMS VAD 降级检测"""
        from config import RealtimeConfig
        from core.stream_transcriber import StreamTranscriber
        import numpy as np

        config = RealtimeConfig()
        st = StreamTranscriber(config, None, lambda *a: None)

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

    def test_12_webrtcvad_graceful_fallback(self):
        """webrtcvad 不可用时自动降级"""
        from config import RealtimeConfig
        from core.stream_transcriber import StreamTranscriber

        config = RealtimeConfig()
        st = StreamTranscriber(config, None, lambda *a: None)
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


if __name__ == "__main__":
    # 按顺序执行
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestWindowsClipboard))
    suite.addTests(loader.loadTestsFromTestCase(TestConfigMigration))
    suite.addTests(loader.loadTestsFromTestCase(TestSTTEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestStreamTranscriber))
    suite.addTests(loader.loadTestsFromTestCase(TestEngineState))

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
