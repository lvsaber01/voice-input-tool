#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模式切换集成测试 — engine + config + tray 集成"""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from core.engine import CoreEngine, EngineState


def _make_engine(config=None, tray=None):
    """创建一个不加载子模块的 mock engine
    
    参考 tests/test_engine.py 中的 _make_engine 模式，
    mock 所有外部子模块以隔离测试。
    """
    if config is None:
        config = MagicMock()
        config.audio.max_duration = 0
        config.realtime = MagicMock()
        config.mode = "batch"
        # 确保 stt 有合理的属性
        config.stt.engine = "faster_whisper"
        config.stt.model_size = "large-v3-turbo"
        config.stt.streaming = MagicMock()
        config.stt.streaming.enabled = False
        config.stt.streaming.max_queue_size = 300
        config.stt.streaming.overflow_strategy = "drop_old"
        config.stt.language = None
        config.stt.device = "auto"
        config.stt.compute_type = "int8"
        config.stt.hf_endpoint = "https://hf-mirror.com"
        config.stt.modelscope_endpoint = ""
        config.stt.beam_size = 5
        config.stt.max_new_tokens = 256
        config.stt.model_path = "./models/"

    if tray is None:
        tray = MagicMock()

    with patch("core.recorder.AudioRecorder") as mock_recorder, \
         patch("core.silence_detector.SilenceDetector") as mock_silence, \
         patch("core.injector.TextInjector") as mock_injector, \
         patch("core.sound_player.SoundPlayer") as mock_sound, \
         patch("core.stt_engine.STTEngine") as mock_stt_engine, \
         patch("core.vad_segment_transcriber.VADSegmentTranscriber") as mock_vad:
        engine = CoreEngine(config, tray)

    return engine


class TestEngineModeConfigSelection(unittest.TestCase):
    """引擎根据 mode 选择 STT 配置"""

    def test_engine_batch_mode_uses_stt_config(self):
        """batch 模式使用 config.stt"""
        from config import AppConfig, STTConfig
        config = AppConfig()
        config.mode = "batch"
        config.stt = STTConfig(engine="faster_whisper", model_size="large-v3-turbo")

        with patch("core.recorder.AudioRecorder"), \
             patch("core.silence_detector.SilenceDetector"), \
             patch("core.injector.TextInjector"), \
             patch("core.sound_player.SoundPlayer"), \
             patch("core.stt_engine.STTEngine") as mock_stt, \
             patch("core.vad_segment_transcriber.VADSegmentTranscriber"):
            engine = CoreEngine(config, MagicMock())

        # batch 模式：stt_config 应该直接使用 config.stt (faster_whisper)
        # 验证 STTEngine 被 patch 且传入了 faster_whisper 配置
        mock_stt.assert_called_once()
        stt_arg = mock_stt.call_args[0][0]
        self.assertEqual(stt_arg.engine, "faster_whisper")

    def test_engine_realtime_mode_uses_stt_realtime_config(self):
        """realtime 模式使用 config.stt_realtime.resolve(config.stt)"""
        from config import AppConfig, STTConfig, STTRealtimeConfig, StreamingConfig
        config = AppConfig()
        config.mode = "realtime"
        config.stt = STTConfig(
            engine="faster_whisper",
            model_size="large-v3-turbo",
            language="zh",
        )
        config.stt_realtime = STTRealtimeConfig(
            engine="funasr",
            model_size="paraformer-zh-streaming",
            streaming=StreamingConfig(enabled=True),
        )

        with patch("core.recorder.AudioRecorder"), \
             patch("core.silence_detector.SilenceDetector"), \
             patch("core.injector.TextInjector"), \
             patch("core.sound_player.SoundPlayer"), \
             patch("core.stt_funasr_streaming.FunASRStreamingEngine") as mock_funasr, \
             patch("core.streaming_transcriber.StreamingTranscriber"):
            engine = CoreEngine(config, MagicMock())

        # realtime 模式：应使用 stt_realtime 配置创建 FunASRStreamingEngine
        mock_funasr.assert_called_once()
        # 验证传入的 stt_config 是 resolve 后的配置
        call_args = mock_funasr.call_args[0][0]
        self.assertEqual(call_args.engine, "funasr")
        self.assertEqual(call_args.model_size, "paraformer-zh-streaming")
        self.assertEqual(call_args.language, "zh")  # fallback 从 stt 继承


class TestEngineShutdownIdempotent(unittest.TestCase):
    """shutdown 幂等性测试"""

    def test_engine_shutdown_idempotent(self):
        """shutdown 两次不报错"""
        engine = _make_engine()
        engine.shutdown()
        # 第二次调用不应抛异常
        try:
            engine.shutdown()
        except Exception as e:
            self.fail(f"第二次 shutdown 不应抛异常: {e}")

    def test_engine_shutdown_idempotent_with_state(self):
        """shutdown 后再次调用返回而不执行清理"""
        engine = _make_engine()
        # 手动 mock 子模块以追踪调用
        mock_recorder = MagicMock()
        mock_recorder.is_recording = False
        engine._recorder = mock_recorder

        # 第一次 shutdown
        engine.shutdown()
        self.assertTrue(engine._shutdown_event.is_set())

        # 重置 mock 计数
        mock_recorder.stop.reset_mock()

        # 第二次 shutdown — 应直接返回
        engine.shutdown()
        # recorder.stop 不应被再次调用
        mock_recorder.stop.assert_not_called()


class TestCreateSTTEngine(unittest.TestCase):
    """_create_stt_engine 引擎创建测试"""

    @patch("core.stt_funasr_streaming.FunASRStreamingEngine")
    @patch("core.streaming_transcriber.StreamingTranscriber")
    def test_create_stt_engine_funasr_streaming(self, mock_transcriber, mock_funasr):
        """_create_stt_engine 正确创建 FunASRStreamingEngine（streaming 模式）"""
        from config import STTConfig, StreamingConfig
        engine = _make_engine()
        stt_config = STTConfig(
            engine="funasr",
            model_size="paraformer-zh-streaming",
            streaming=StreamingConfig(enabled=True),
        )
        engine._create_stt_engine(stt_config)
        mock_funasr.assert_called_once_with(stt_config)
        self.assertTrue(engine._streaming_mode)

    @patch("core.stt_engine.STTEngine")
    @patch("core.vad_segment_transcriber.VADSegmentTranscriber")
    def test_create_stt_engine_faster_whisper(self, mock_vad, mock_stt):
        """_create_stt_engine 正确创建 STTEngine（faster-whisper）"""
        from config import STTConfig
        engine = _make_engine()
        stt_config = STTConfig(
            engine="faster_whisper",
            model_size="large-v3-turbo",
        )
        engine._create_stt_engine(stt_config)
        mock_stt.assert_called_once_with(stt_config)
        self.assertFalse(engine._streaming_mode)


class TestTrayMenuModeSwitch(unittest.TestCase):
    """Tray 菜单模式切换项可用性测试"""

    def test_tray_menu_mode_switch_enabled_only_in_idle(self):
        """模式切换菜单项仅在 IDLE 时 enabled"""
        try:
            import pystray  # noqa: F401
        except ImportError:
            self.skipTest("pystray 未安装，跳过 tray 菜单测试")

        from gui.tray import TrayIcon

        tray = TrayIcon(
            on_start=MagicMock(),
            on_stop=MagicMock(),
            on_settings=MagicMock(),
            on_quit=MagicMock(),
            on_switch_mode=MagicMock(),
        )

        # 测试 IDLE 状态 — 模式切换应 enabled
        tray._state = EngineState.IDLE
        menu = tray._build_menu()
        mode_switch_item = None
        for item in menu._items:
            if hasattr(item, 'text') and '切换到' in str(getattr(item, 'text', '')):
                mode_switch_item = item
                break
        self.assertIsNotNone(mode_switch_item, "未找到模式切换菜单项")
        self.assertTrue(mode_switch_item.default_enabled)

        # 测试 RECORDING 状态 — 模式切换应 disabled
        tray._state = EngineState.RECORDING
        menu = tray._build_menu()
        mode_switch_item = None
        for item in menu._items:
            if hasattr(item, 'text') and '切换到' in str(getattr(item, 'text', '')):
                mode_switch_item = item
                break
        self.assertIsNotNone(mode_switch_item, "未找到模式切换菜单项")
        self.assertFalse(mode_switch_item.default_enabled)

        # 测试 STREAMING 状态 — 模式切换应 disabled
        tray._state = EngineState.STREAMING
        menu = tray._build_menu()
        mode_switch_item = None
        for item in menu._items:
            if hasattr(item, 'text') and '切换到' in str(getattr(item, 'text', '')):
                mode_switch_item = item
                break
        self.assertIsNotNone(mode_switch_item, "未找到模式切换菜单项")
        self.assertFalse(mode_switch_item.default_enabled)

        # 测试 LOADING 状态 — 不会出现模式切换菜单项
        tray._state = EngineState.LOADING
        menu = tray._build_menu()
        mode_switch_items = [
            item for item in menu._items
            if hasattr(item, 'text') and '切换到' in str(getattr(item, 'text', ''))
        ]
        # LOADING 时 is_loading=True，不会渲染模式切换项
        self.assertEqual(len(mode_switch_items), 0)


if __name__ == "__main__":
    unittest.main()
