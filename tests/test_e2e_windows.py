"""Windows E2E 功能测试。

验证 Windows 平台特有功能。
实际运行在 Mac 上做逻辑验证，不依赖 Windows API。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.command import CommandMatcher, CommandExecutor, CommandType
from core.itn import ITNStep
from core.engine import EngineState


class TestWin32CommandE2E(unittest.TestCase):
    """Windows 命令 E2E。"""

    def test_win32_command_undo(self):
        """Windows 下撤销命令执行。"""
        matcher = CommandMatcher()
        executor = CommandExecutor()
        executor._key_sim = MagicMock()

        result = matcher.match("撤销")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertTrue(executor.execute(cmd))
        executor._key_sim.send.assert_called_with("ctrl+z")

    def test_win32_command_paste(self):
        """Windows 下粘贴命令执行。"""
        matcher = CommandMatcher()
        executor = CommandExecutor()
        executor._key_sim = MagicMock()

        result = matcher.match("粘贴")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertTrue(executor.execute(cmd))
        executor._key_sim.send.assert_called_with("ctrl+v")


class TestWin32ITNE2E(unittest.TestCase):
    """Windows 下 ITN 转换。"""

    def test_win32_itn_basic(self):
        """Windows 下基本 ITN 转换。"""
        step = ITNStep(enabled=True)
        self.assertEqual(step.process("三百六十五"), "365")

    def test_win32_itn_phone(self):
        """Windows 下电话转换。"""
        step = ITNStep(enabled=True)
        self.assertEqual(step.process("一八五零零一二三四五六"), "18500123456")

    def test_win32_itn_date(self):
        """Windows 下日期转换。"""
        step = ITNStep(enabled=True)
        self.assertEqual(step.process("二零二六年五月三日"), "2026年5月3日")


class TestWin32RealtimeE2E(unittest.TestCase):
    """Windows 下实时模式 E2E。"""

    def test_win32_realtime_pause(self):
        """Windows 下实时模式暂停状态转换。"""
        config = MagicMock()
        config.stt.engine = 'funasr'
        config.stt.model_size = 'SenseVoiceSmall'
        config.stt.streaming.enabled = False
        config.realtime.auto_timestamp = False
        config.realtime.segment_separator = '\n'
        config.command = MagicMock()
        config.command.enabled = False
        config.audio.max_duration = 0
        config.audio.device = None

        with patch('core.recorder.AudioRecorder'), \
             patch('core.silence_detector.SilenceDetector'), \
             patch('core.injector.TextInjector'), \
             patch('core.sound_player.SoundPlayer'), \
             patch('core.stt_funasr.FunASREngine'), \
             patch('core.vad_segment_transcriber.VADSegmentTranscriber'):
            from core.engine import CoreEngine
            engine = CoreEngine(config, tray=None)
            engine._state = EngineState.STREAMING
            engine._stream_transcriber = MagicMock()
            engine._recorder = MagicMock()
            engine._sound_player = MagicMock()

            # 暂停
            engine.toggle_pause()
            self.assertEqual(engine._state, EngineState.PAUSED)

            # 恢复
            engine.toggle_pause()
            self.assertEqual(engine._state, EngineState.STREAMING)

    def test_win32_export(self):
        """Windows 下导出功能。"""
        import tempfile
        config = MagicMock()
        config.stt.engine = 'funasr'
        config.stt.model_size = 'SenseVoiceSmall'
        config.stt.streaming.enabled = False
        config.realtime.auto_timestamp = False
        config.realtime.segment_separator = '\n'
        config.command = MagicMock()
        config.command.enabled = False
        config.audio.max_duration = 0
        config.audio.device = None

        with patch('core.recorder.AudioRecorder'), \
             patch('core.silence_detector.SilenceDetector'), \
             patch('core.injector.TextInjector'), \
             patch('core.sound_player.SoundPlayer'), \
             patch('core.stt_funasr.FunASREngine'), \
             patch('core.vad_segment_transcriber.VADSegmentTranscriber'):
            from core.engine import CoreEngine
            engine = CoreEngine(config, tray=None)
            engine._injector = MagicMock()
            engine._sound_player = MagicMock()
            engine._session_segments = ["测试段落"]

            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                path = f.name
            try:
                self.assertTrue(engine.export_session(path))
                with open(path, 'r', encoding='utf-8') as f:
                    self.assertEqual(f.read(), "测试段落")
            finally:
                os.unlink(path)

    def test_win32_shortcut_pause(self):
        """快捷键暂停功能验证（状态机逻辑）。"""
        # 验证 PAUSED 状态转移矩阵正确
        from core.engine import _VALID_TRANSITIONS
        self.assertIn(EngineState.PAUSED, _VALID_TRANSITIONS[EngineState.STREAMING])
        self.assertIn(EngineState.STREAMING, _VALID_TRANSITIONS[EngineState.PAUSED])
        self.assertIn(EngineState.IDLE, _VALID_TRANSITIONS[EngineState.PAUSED])


if __name__ == "__main__":
    unittest.main()
