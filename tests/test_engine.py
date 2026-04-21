"""CoreEngine 状态机单元测试"""

import unittest
from unittest.mock import MagicMock, patch
from core.engine import CoreEngine, EngineState, _VALID_TRANSITIONS


class TestEngineTransitions(unittest.TestCase):
    """测试状态转移逻辑"""

    def _make_engine(self):
        """创建一个不加载子模块的 mock engine"""
        with patch("core.recorder.AudioRecorder"), \
             patch("core.silence_detector.SilenceDetector"), \
             patch("core.stt_engine.STTEngine"), \
             patch("core.injector.TextInjector"), \
             patch("core.sound_player.SoundPlayer"), \
             patch("core.streaming_transcriber.StreamingTranscriber"):
            config = MagicMock()
            config.audio.max_duration = 0
            config.realtime = MagicMock()
            tray = MagicMock()
            engine = CoreEngine(config, tray)
        return engine

    def test_valid_transition_loading_to_idle(self):
        engine = self._make_engine()
        engine._state = EngineState.LOADING
        self.assertTrue(engine.transition(EngineState.IDLE))

    def test_valid_transition_idle_to_recording(self):
        engine = self._make_engine()
        engine._state = EngineState.IDLE
        self.assertTrue(engine.transition(EngineState.RECORDING))

    def test_valid_transition_recording_to_processing(self):
        engine = self._make_engine()
        engine._state = EngineState.RECORDING
        self.assertTrue(engine.transition(EngineState.PROCESSING))

    def test_valid_transition_processing_to_idle(self):
        engine = self._make_engine()
        engine._state = EngineState.PROCESSING
        self.assertTrue(engine.transition(EngineState.IDLE))

    def test_invalid_transition_idle_to_processing(self):
        engine = self._make_engine()
        engine._state = EngineState.IDLE
        self.assertFalse(engine.transition(EngineState.PROCESSING))

    def test_invalid_transition_idle_to_injecting(self):
        engine = self._make_engine()
        engine._state = EngineState.IDLE
        self.assertFalse(engine.transition(EngineState.INJECTING))

    def test_invalid_transition_recording_to_idle(self):
        engine = self._make_engine()
        engine._state = EngineState.RECORDING
        self.assertFalse(engine.transition(EngineState.IDLE))

    def test_stop_recording_dedup(self):
        """_stop_recording_and_transcribe 去重：第二次调用返回不执行"""
        engine = self._make_engine()
        engine._state = EngineState.RECORDING

        engine._recorder = MagicMock()
        engine._recorder.stop.return_value = MagicMock()
        engine._silence_detector = MagicMock()
        engine._sound_player = MagicMock()
        engine._stt_engine = MagicMock()

        # First call: RECORDING -> PROCESSING succeeds
        engine._stop_recording_and_transcribe()
        self.assertEqual(engine._state, EngineState.PROCESSING)

        engine._recorder.stop.reset_mock()

        # Second call: dedup
        engine._stop_recording_and_transcribe()
        engine._recorder.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
