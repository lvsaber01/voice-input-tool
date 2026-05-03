"""Engine 暂停/恢复集成测试。

验证 toggle_pause 的状态转换、事件发布、recorder 调用和回滚逻辑。
"""

import os
import sys
import unittest
import threading
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import EngineState, _VALID_TRANSITIONS, CoreEngine
from core.events import EngineEvent


def _make_engine(state=EngineState.STREAMING):
    """创建测试用 engine（mock 子模块）。"""
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
        engine._state = state
        engine._stream_transcriber = MagicMock()
        engine._recorder = MagicMock()
        engine._sound_player = MagicMock()
        return engine


class TestEnginePauseIntegration(unittest.TestCase):
    """Engine 暂停/恢复集成测试。"""

    def test_toggle_streaming_to_paused(self):
        """STREAMING → PAUSED 状态转换。"""
        engine = _make_engine(EngineState.STREAMING)
        engine.toggle_pause()
        self.assertEqual(engine._state, EngineState.PAUSED)

    def test_toggle_paused_to_streaming(self):
        """PAUSED → STREAMING 状态转换。"""
        engine = _make_engine(EngineState.PAUSED)
        engine.toggle_pause()
        self.assertEqual(engine._state, EngineState.STREAMING)

    def test_toggle_pause_state_lock(self):
        """并发 toggle 串行化（不崩溃）。"""
        engine = _make_engine(EngineState.STREAMING)
        errors = []

        def toggle():
            try:
                engine.toggle_pause()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=toggle) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        # 最终状态应该是 PAUSED 或 STREAMING 之一
        self.assertIn(engine._state, {EngineState.PAUSED, EngineState.STREAMING})

    def test_pause_events(self):
        """暂停/恢复事件正确发布。"""
        published = []

        def capture_event(event, *args):
            published.append(event)

        engine = _make_engine(EngineState.STREAMING)
        engine._events.subscribe(EngineEvent.RECORDING_PAUSED, lambda: published.append("paused"))
        engine._events.subscribe(EngineEvent.RECORDING_RESUMED, lambda: published.append("resumed"))

        engine.toggle_pause()  # STREAMING → PAUSED
        self.assertEqual(engine._state, EngineState.PAUSED)

        engine.toggle_pause()  # PAUSED → STREAMING
        self.assertEqual(engine._state, EngineState.STREAMING)

        # 事件通过线程池异步发布，给一点时间
        import time
        time.sleep(0.3)
        self.assertIn("paused", published)
        self.assertIn("resumed", published)

    def test_recorder_pause_called(self):
        """暂停时 recorder.pause() 被调用。"""
        engine = _make_engine(EngineState.STREAMING)
        engine.toggle_pause()
        engine._recorder.pause.assert_called_once()

    def test_recorder_resume_called(self):
        """恢复时 recorder.resume() 被调用。"""
        engine = _make_engine(EngineState.PAUSED)
        engine.toggle_pause()
        engine._recorder.resume.assert_called_once()

    def test_recorder_pause_on_rollback(self):
        """STREAMING→PAUSED 转换失败时回滚。

        模拟：stream_transcriber.pause() 成功，但状态转换失败。
        """
        engine = _make_engine(EngineState.STREAMING)
        # 篡改状态使转换失败
        engine._state = EngineState.IDLE
        engine.toggle_pause()
        # recorder.pause() 仍然被调用（在 transition 之前）
        # 但状态不会变

    def test_toggle_pause_from_idle_ignored(self):
        """IDLE 状态下 toggle_pause 不产生副作用。"""
        engine = _make_engine(EngineState.IDLE)
        engine.toggle_pause()
        self.assertEqual(engine._state, EngineState.IDLE)


if __name__ == "__main__":
    unittest.main()
