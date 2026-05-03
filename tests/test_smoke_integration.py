"""集成冒烟测试 — 验证新功能已接入主流程。

根据教训 2026-05-03-integration-gap，每个新功能都必须有冒烟测试验证「已安装」。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import EngineState
from core.pipeline_step import StepNames


def _make_engine():
    """创建真实 engine（mock 子模块但不 mock pipeline）。"""
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
    config.itn = MagicMock()
    config.itn.enabled = True

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
        return engine


class TestSmokeITNRegistered(unittest.TestCase):
    """冒烟测试：ITN 在 engine 启动后自动注册到 pipeline。"""

    def test_itn_step_registered(self):
        """ITNStep 已注册到 pipeline。"""
        engine = _make_engine()
        step = engine._text_pipeline.get_step(StepNames.ITN)
        self.assertIsNotNone(step, "ITNStep 未注册到 pipeline")

    def test_itn_step_enabled(self):
        """ITNStep 默认启用。"""
        engine = _make_engine()
        step = engine._text_pipeline.get_step(StepNames.ITN)
        self.assertTrue(step.enabled)

    def test_itn_actually_works(self):
        """ITN 在 pipeline 中实际生效（端到端验证）。"""
        engine = _make_engine()
        result = engine._text_pipeline.process("三百六十五天")
        self.assertEqual(result.text, "365天")


class TestSmokeConfigITN(unittest.TestCase):
    """冒烟测试：config.py 包含 ITNConfig。"""

    def test_config_has_itn(self):
        """AppConfig 包含 itn 字段。"""
        from config import AppConfig
        config = AppConfig()
        self.assertTrue(hasattr(config, 'itn'))

    def test_itn_enabled_by_default(self):
        """ITN 默认启用。"""
        from config import AppConfig
        config = AppConfig()
        self.assertTrue(config.itn.enabled)

    def test_migration_v8_to_v9(self):
        """v8 配置文件迁移后自动添加 itn 字段。"""
        from config import _migrate_v8_to_v9
        raw = {'config_version': 8}
        result = _migrate_v8_to_v9(raw)
        self.assertEqual(result['config_version'], 9)
        self.assertIn('itn', result)
        self.assertTrue(result['itn']['enabled'])


class TestSmokePauseExposed(unittest.TestCase):
    """冒烟测试：toggle_pause 可从外部调用。"""

    def test_toggle_pause_callable(self):
        """toggle_pause 是 engine 的公开方法。"""
        from core.engine import CoreEngine
        self.assertTrue(hasattr(CoreEngine, 'toggle_pause'))

    def test_toggle_pause_works(self):
        """toggle_pause 实际可执行（不崩溃）。"""
        engine = _make_engine()
        engine._state = EngineState.STREAMING
        engine._stream_transcriber = MagicMock()
        engine._recorder = MagicMock()
        engine._sound_player = MagicMock()
        engine.toggle_pause()
        self.assertEqual(engine._state, EngineState.PAUSED)


class TestSmokeExportAPI(unittest.TestCase):
    """冒烟测试：export_session 可用。"""

    def test_export_session_callable(self):
        """export_session 是 engine 的公开方法。"""
        from core.engine import CoreEngine
        self.assertTrue(hasattr(CoreEngine, 'export_session'))

    def test_get_session_segments_callable(self):
        """get_session_segments 是 engine 的公开方法。"""
        from core.engine import CoreEngine
        self.assertTrue(hasattr(CoreEngine, 'get_session_segments'))


class TestSmokePausedStateInTray(unittest.TestCase):
    """冒烟测试：PAUSED 状态在 tray 中有对应显示。"""

    def test_paused_has_color(self):
        """PAUSED 状态有对应颜色。"""
        from gui.tray import _STATE_COLORS
        self.assertIn(EngineState.PAUSED, _STATE_COLORS)

    def test_paused_has_label(self):
        """PAUSED 状态有对应标签。"""
        from gui.tray import _STATE_LABELS
        self.assertIn(EngineState.PAUSED, _STATE_LABELS)
        self.assertEqual(_STATE_LABELS[EngineState.PAUSED], "已暂停")


if __name__ == "__main__":
    unittest.main()
