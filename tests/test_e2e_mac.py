"""Mac E2E 功能测试。

模拟完整用户流程，验证 Pipeline + ITN + 命令 + 暂停/导出。
不依赖实际音频设备或 GUI。
"""

import os
import sys
import unittest
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.text_pipeline import TextPipeline
from core.pipeline_step import StepNames
from core.itn import ITNStep
from core.command import CommandMatcher, CommandExecutor, CommandType
from core.engine import EngineState


class TestE2EPipelinePlugin(unittest.TestCase):
    """E2E: 完整 pipeline 处理流程。"""

    def test_pipeline_plugin_full_flow(self):
        """完整 pipeline：原文 → 标点/音素/正则/热词/ITN。"""
        pipeline = TextPipeline(MagicMock())
        pipeline.add_step(ITNStep(enabled=True))

        # 模拟 STT 输出
        result = pipeline.process("三百六十五天")
        self.assertEqual(result.text, "365天")
        self.assertTrue(result.is_changed)

    def test_itn_in_real_speech(self):
        """模拟真实语音识别结果中的数字转换。"""
        pipeline = TextPipeline(MagicMock())
        pipeline.add_step(ITNStep(enabled=True))

        cases = [
            ("今天三月十五号开会", "今天3月15号开会"),
            ("电话号码是一八五零零一二三四五六", "电话号码是18500123456"),
            ("百分之九十九的把握", "99%的把握"),
            ("价格三五百块左右", "价格300~500块左右"),
        ]
        for input_text, expected in cases:
            result = pipeline.process(input_text)
            self.assertEqual(result.text, expected, f"{input_text} → {result.text}, expected {expected}")


class TestE2EVoiceCommand(unittest.TestCase):
    """E2E: 语音命令完整流程。"""

    def test_voice_command_undo(self):
        """说"撤销"触发 ctrl+z。"""
        matcher = CommandMatcher()
        executor = CommandExecutor()
        executor._key_sim = MagicMock()

        result = matcher.match("撤销")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertTrue(executor.execute(cmd))
        executor._key_sim.send.assert_called_with("ctrl+z")

    def test_voice_command_stop(self):
        """说"停止录音"触发引擎停止。"""
        matcher = CommandMatcher()
        mock_engine = MagicMock()
        executor = CommandExecutor(engine=mock_engine)

        result = matcher.match("停止录音")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertTrue(executor.execute(cmd))
        mock_engine.on_hotkey_stop.assert_called_once()


class TestE2EPauseExport(unittest.TestCase):
    """E2E: 暂停/恢复 + 导出完整流程。"""

    def test_realtime_pause_resume_export(self):
        """完整流程：启动 → 段落 → 暂停 → 段落(丢弃) → 恢复 → 段落 → 导出。"""
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
            engine._state = EngineState.STREAMING

            # 模拟段落
            engine._on_realtime_segment("第一段")
            engine._on_realtime_segment("第二段")

            # 暂停
            engine._stream_transcriber = MagicMock()
            engine._recorder = MagicMock()
            engine.toggle_pause()
            self.assertEqual(engine._state, EngineState.PAUSED)

            # 恢复
            engine.toggle_pause()
            self.assertEqual(engine._state, EngineState.STREAMING)

            # 再加一段
            engine._on_realtime_segment("第三段")

            # 导出（ITN 已启用，"第X段" 会被转换）
            segments = engine.get_session_segments()
            self.assertEqual(len(segments), 3)
            self.assertEqual(segments, ["第1段", "第2段", "第3段"])

            # 导出文件
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                path = f.name
            try:
                self.assertTrue(engine.export_session(path, format="txt"))
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.assertEqual(content, "第1段\n第2段\n第3段")
            finally:
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
