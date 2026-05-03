"""实时转写暂停/继续测试。

测试 Phase 4:
- EngineState.PAUSED 状态转移
- toggle_pause() 逻辑
- Recorder pause/resume
- StreamingTranscriber pause/resume
- 段落追踪与导出
"""

import os
import sys
import unittest
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import EngineState, _VALID_TRANSITIONS
from core.events import EngineEvent


class TestPausedState(unittest.TestCase):
    """PAUSED 状态与转移矩阵测试。"""

    def test_paused_state_exists(self):
        """PAUSED 状态存在。"""
        self.assertTrue(hasattr(EngineState, 'PAUSED'))

    def test_streaming_to_paused(self):
        """STREAMING → PAUSED 合法转移。"""
        self.assertIn(EngineState.PAUSED, _VALID_TRANSITIONS[EngineState.STREAMING])

    def test_paused_to_streaming(self):
        """PAUSED → STREAMING 合法转移。"""
        self.assertIn(EngineState.STREAMING, _VALID_TRANSITIONS[EngineState.PAUSED])

    def test_paused_to_idle(self):
        """PAUSED → IDLE 合法转移（停止）。"""
        self.assertIn(EngineState.IDLE, _VALID_TRANSITIONS[EngineState.PAUSED])

    def test_paused_to_recording_invalid(self):
        """PAUSED → RECORDING 非法转移。"""
        self.assertNotIn(EngineState.RECORDING, _VALID_TRANSITIONS[EngineState.PAUSED])


class TestPauseResumeEvents(unittest.TestCase):
    """暂停/继续事件测试。"""

    def test_recording_paused_event(self):
        """RECORDING_PAUSED 事件存在。"""
        self.assertTrue(hasattr(EngineEvent, 'RECORDING_PAUSED'))

    def test_recording_resumed_event(self):
        """RECORDING_RESUMED 事件存在。"""
        self.assertTrue(hasattr(EngineEvent, 'RECORDING_RESUMED'))


class TestRecorderPauseResume(unittest.TestCase):
    """AudioRecorder pause/resume 测试。"""

    def test_pause_sets_flag(self):
        """pause() 设置 _paused=True。"""
        from core.recorder import AudioRecorder
        config = MagicMock()
        recorder = AudioRecorder(config)
        recorder._paused = False
        recorder.pause()
        self.assertTrue(recorder._paused)

    def test_resume_clears_flag(self):
        """resume() 设置 _paused=False。"""
        from core.recorder import AudioRecorder
        config = MagicMock()
        recorder = AudioRecorder(config)
        recorder._paused = True
        recorder.resume()
        self.assertFalse(recorder._paused)

    def test_callback_skips_when_paused(self):
        """暂停时 _audio_callback 不写入 buffer。"""
        from core.recorder import AudioRecorder
        import numpy as np
        config = MagicMock()
        recorder = AudioRecorder(config)
        recorder._paused = True
        recorder._stream = MagicMock()

        initial_len = len(recorder._buffer)
        indata = np.ones((160, 1), dtype=np.float32)
        recorder._audio_callback(indata, 160, None, None)
        self.assertEqual(len(recorder._buffer), initial_len)

    def test_callback_writes_when_not_paused(self):
        """非暂停时 _audio_callback 正常写入 buffer。"""
        from core.recorder import AudioRecorder
        import numpy as np
        config = MagicMock()
        recorder = AudioRecorder(config)
        recorder._paused = False
        recorder._stream = MagicMock()

        indata = np.ones((160, 1), dtype=np.float32)
        recorder._audio_callback(indata, 160, None, None)
        self.assertEqual(len(recorder._buffer), 1)


class TestStreamingTranscriberPauseResume(unittest.TestCase):
    """StreamingTranscriber pause/resume 测试。"""

    def test_pause_sets_flag(self):
        """pause() 设置 _paused=True。"""
        from core.streaming_transcriber import StreamingTranscriber
        config = MagicMock()
        transcriber = StreamingTranscriber(config)
        transcriber._paused = False
        transcriber.pause()
        self.assertTrue(transcriber._paused)

    def test_resume_clears_flag(self):
        """resume() 设置 _paused=False。"""
        from core.streaming_transcriber import StreamingTranscriber
        config = MagicMock()
        transcriber = StreamingTranscriber(config)
        transcriber._paused = True
        transcriber.resume()
        self.assertFalse(transcriber._paused)


class TestTogglePauseIntegration(unittest.TestCase):
    """toggle_pause 集成测试（mock 子模块）。"""

    def _make_engine(self):
        """创建测试用 engine（mock 所有子模块）。"""
        from core.engine import CoreEngine

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
            engine = CoreEngine(config, tray=None)
            engine._state = EngineState.STREAMING
            # Mock stream_transcriber
            engine._stream_transcriber = MagicMock()
            engine._recorder = MagicMock()
            engine._sound_player = MagicMock()
            return engine

    def test_toggle_pause_streaming_to_paused(self):
        """STREAMING → toggle_pause → PAUSED。"""
        engine = self._make_engine()
        engine.toggle_pause()
        self.assertEqual(engine._state, EngineState.PAUSED)
        engine._stream_transcriber.pause.assert_called_once()
        engine._recorder.pause.assert_called_once()
        engine._sound_player.play.assert_called_with("pause")

    def test_toggle_pause_paused_to_streaming(self):
        """PAUSED → toggle_pause → STREAMING。"""
        engine = self._make_engine()
        engine._state = EngineState.PAUSED
        engine.toggle_pause()
        self.assertEqual(engine._state, EngineState.STREAMING)
        engine._stream_transcriber.resume.assert_called_once()
        engine._recorder.resume.assert_called_once()
        engine._sound_player.play.assert_called_with("resume")

    def test_toggle_pause_idle_ignored(self):
        """IDLE → toggle_pause → 无变化。"""
        engine = self._make_engine()
        engine._state = EngineState.IDLE
        engine.toggle_pause()
        self.assertEqual(engine._state, EngineState.IDLE)


class TestSessionExport(unittest.TestCase):
    """段落追踪与导出测试。"""

    def _make_engine(self):
        """创建测试用 engine。"""
        from core.engine import CoreEngine

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
            engine = CoreEngine(config, tray=None)
            engine._injector = MagicMock()
            engine._sound_player = MagicMock()
            return engine

    def test_get_session_segments_empty(self):
        """空会话返回空列表。"""
        engine = self._make_engine()
        segments = engine.get_session_segments()
        self.assertEqual(segments, [])

    def test_get_session_segments_with_data(self):
        """有数据时返回段落列表。"""
        engine = self._make_engine()
        engine._session_segments = ["你好", "世界"]
        segments = engine.get_session_segments()
        self.assertEqual(segments, ["你好", "世界"])

    def test_export_session_txt(self):
        """导出 txt 格式。"""
        engine = self._make_engine()
        engine._session_segments = ["你好", "世界"]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            path = f.name
        try:
            result = engine.export_session(path, format="txt")
            self.assertTrue(result)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.assertEqual(content, "你好\n世界")
        finally:
            os.unlink(path)

    def test_export_session_markdown(self):
        """导出 markdown 格式。"""
        engine = self._make_engine()
        engine._session_segments = ["第一段", "第二段"]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            path = f.name
        try:
            result = engine.export_session(path, format="markdown")
            self.assertTrue(result)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.assertIn("# 转写记录", content)
            self.assertIn("1. 第一段", content)
            self.assertIn("2. 第二段", content)
        finally:
            os.unlink(path)

    def test_export_session_empty(self):
        """空段落导出失败。"""
        engine = self._make_engine()
        result = engine.export_session("/tmp/test_export.txt")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
