"""macOS 平台适配层 + VAD/MlxWhisper 协作 + 注入链路 测试"""

import unittest
from unittest.mock import MagicMock, patch, call
import subprocess
import numpy as np


# ================================================================
# clipboard_macos 测试
# ================================================================

class TestClipboardMacOS(unittest.TestCase):

    @patch('subprocess.run')
    def test_write_clipboard(self, mock_run):
        """pbcopy 写入剪贴板"""
        mock_run.return_value = MagicMock(returncode=0)

        from platform_adapter.clipboard_macos import MacOSClipboardInjector

        config = MagicMock()
        config.inject = MagicMock()
        config.inject.method = "clipboard"

        clip = MacOSClipboardInjector(config)
        result = clip.write_clipboard("hello world")

        self.assertTrue(result)
        mock_run.assert_called_once()
        args = mock_run.call_args
        self.assertEqual(args[0][0][0], "pbcopy")
        self.assertEqual(args[1]["input"], "hello world")
        self.assertTrue(args[1]["text"])

    @patch('subprocess.run')
    def test_write_clipboard_unicode(self, mock_run):
        """pbcopy 支持中文"""
        mock_run.return_value = MagicMock(returncode=0)

        from platform_adapter.clipboard_macos import MacOSClipboardInjector

        config = MagicMock()
        clip = MacOSClipboardInjector(config)
        result = clip.write_clipboard("你好世界")

        self.assertTrue(result)
        self.assertEqual(mock_run.call_args[1]["input"], "你好世界")

    @patch('subprocess.run')
    def test_read_clipboard(self, mock_run):
        """pbpaste 读取剪贴板"""
        mock_run.return_value = MagicMock(stdout="old content", returncode=0)

        from platform_adapter.clipboard_macos import MacOSClipboardInjector
        config = MagicMock()
        clip = MacOSClipboardInjector(config)
        result = clip.read_clipboard()

        mock_run.assert_called_once()
        self.assertEqual(result, "old content")

    def test_simulate_paste_osascript(self):
        """osascript 模拟粘贴"""
        from platform_adapter.clipboard_macos import MacOSClipboardInjector
        config = MagicMock()
        clip = MacOSClipboardInjector(config)

        with patch.object(clip, '_osascript_paste', return_value=True) as mock_paste:
            result = clip.simulate_paste()
            self.assertTrue(result)
            mock_paste.assert_called_once()


# ================================================================
# clipboard_base 注入链路测试
# ================================================================

class TestClipboardInjectorBase(unittest.TestCase):

    def test_inject_empty_returns_true(self):
        """空文本跳过注入但返回 True"""
        from platform_adapter.clipboard_macos import MacOSClipboardInjector

        config = MagicMock()
        config.inject.method = "clipboard"
        config.inject.add_trailing_space = False
        config.inject.clipboard_backup = False

        clip = MacOSClipboardInjector(config)
        with patch.object(clip, 'write_clipboard', return_value=True), \
             patch.object(clip, 'simulate_paste', return_value=True), \
             patch.object(clip, 'read_clipboard', return_value=""):
            result = clip.inject("")
            self.assertTrue(result)  # 空文本也返回 True（跳过注入）

    def test_inject_delegates_to_clipboard(self):
        """clipboard 方式调用 write_clipboard + simulate_paste"""
        from platform_adapter.clipboard_macos import MacOSClipboardInjector
        config = MagicMock()
        config.inject.method = "clipboard"
        config.inject.add_trailing_space = False
        config.inject.clipboard_backup = False

        clip = MacOSClipboardInjector(config)
        with patch.object(clip, 'write_clipboard', return_value=True), \
             patch.object(clip, 'simulate_paste', return_value=True) as mock_paste, \
             patch.object(clip, 'read_clipboard', return_value=""):
            result = clip.inject("test text")
            self.assertTrue(result)
            # write_clipboard 被调用（含备份恢复，不校验参数）
            mock_paste.assert_called_once()


# ================================================================
# key_simulator 测试
# ================================================================

class TestKeySimulator(unittest.TestCase):

    def test_create_returns_simulator(self):
        """create_key_simulator 返回非 None"""
        from platform_adapter.key_simulator import create_key_simulator
        sim = create_key_simulator()
        self.assertIsNotNone(sim)

    def test_dummy_send_no_error(self):
        """DummyKeySimulator 不崩溃"""
        from platform_adapter.key_simulator import DummyKeySimulator
        dummy = DummyKeySimulator()
        dummy.send("ctrl+v")  # 不应抛异常
        dummy.type_text("hello")  # 不应抛异常


# ================================================================
# VADSegmentTranscriber + MlxWhisperEngine 协作（Mock）
# ================================================================

class TestVADWithMlxWhisper(unittest.TestCase):
    """VAD 分段 + MlxWhisperEngine 的协作链路

    注意：因为 mlx_whisper 已经真实安装，
    需要彻底 mock 才能控制返回值。
    """

    def test_segment_calls_transcribe_sync(self):
        """音频分段调用 transcribe_sync"""
        # 彻底 mock mlx_whisper.transcribe
        with patch('mlx_whisper.transcribe', return_value={'text': 'hello', 'language': 'en'}):
            from core.stt_mlx_whisper import MlxWhisperEngine

            class Cfg:
                model_size = "small"
                language = "en"  # 指定语言避免自动检测延迟

            engine = MlxWhisperEngine(Cfg())
            audio = np.random.randn(48000).astype(np.float32)
            text = engine.transcribe_sync(audio)
            self.assertEqual(text, "hello")
            engine.shutdown()

    def test_multiple_segments(self):
        """多个分段依次转写"""
        with patch('mlx_whisper.transcribe', side_effect=[
            {'text': 'first', 'language': 'en'},
            {'text': 'second', 'language': 'en'},
            {'text': 'third', 'language': 'en'},
        ]):
            from core.stt_mlx_whisper import MlxWhisperEngine

            class Cfg:
                model_size = "small"
                language = "en"

            engine = MlxWhisperEngine(Cfg())
            results = []
            for _ in range(3):
                text = engine.transcribe_sync(np.random.randn(48000).astype(np.float32))
                results.append(text)

            self.assertEqual(results, ['first', 'second', 'third'])
            engine.shutdown()

    def test_silence_segment_returns_empty(self):
        """静音段（<0.2s）返回空"""
        from core.stt_mlx_whisper import MlxWhisperEngine

        class Cfg:
            model_size = "small"
            language = None

        engine = MlxWhisperEngine(Cfg())
        text = engine.transcribe_sync(np.zeros(1000, dtype=np.float32))
        self.assertEqual(text, "")
        engine.shutdown()

    def test_chinese_with_t2s(self):
        """中文繁体 → 简体转换链路"""
        with patch('mlx_whisper.transcribe', return_value={'text': '這是一個測試', 'language': 'zh'}):
            with patch('opencc.OpenCC') as mock_cc_cls:
                mock_cc = MagicMock()
                mock_cc.convert.return_value = '这是一个测试'
                mock_cc_cls.return_value = mock_cc

                from core.stt_mlx_whisper import MlxWhisperEngine

                class Cfg:
                    model_size = "small"
                    language = "zh"

                engine = MlxWhisperEngine(Cfg())
                text = engine.transcribe_sync(np.random.randn(48000).astype(np.float32))
                self.assertEqual(text, '这是一个测试')
                engine.shutdown()


# ================================================================
# FunASR 引擎基础测试
# ================================================================

class TestFunASREngine(unittest.TestCase):

    def test_funasr_config(self):
        from config import STTConfig
        cfg = STTConfig(engine="funasr")
        self.assertEqual(cfg.model_size, "paraformer-zh")

    def test_funasr_streaming_config(self):
        from config import STTConfig, StreamingConfig
        cfg = STTConfig(engine="funasr", streaming=StreamingConfig(enabled=True))
        self.assertTrue(cfg.streaming.enabled)


# ================================================================
# Recorder 基础测试
# ================================================================

class TestRecorderMock(unittest.TestCase):

    def test_max_duration_config(self):
        from config import AudioConfig
        cfg = AudioConfig(max_duration=30)
        self.assertEqual(cfg.max_duration, 30)

    def test_silence_timeout_config(self):
        from config import AudioConfig
        cfg = AudioConfig(silence_timeout=5)
        self.assertEqual(cfg.silence_timeout, 5)


if __name__ == '__main__':
    unittest.main()
