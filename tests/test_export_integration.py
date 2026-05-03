"""会话导出集成测试。

验证导出格式、空会话处理、线程安全。
"""

import os
import sys
import unittest
import tempfile
import threading
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import CoreEngine


def _make_engine():
    """创建测试用 engine。"""
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


class TestExportIntegration(unittest.TestCase):
    """导出集成测试。"""

    def test_export_txt_format(self):
        """TXT 导出格式正确。"""
        engine = _make_engine()
        engine._session_segments = ["第一段话", "第二段话", "第三段话"]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            path = f.name
        try:
            self.assertTrue(engine.export_session(path, format="txt"))
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.assertEqual(content, "第一段话\n第二段话\n第三段话")
        finally:
            os.unlink(path)

    def test_export_markdown_format(self):
        """Markdown 导出格式正确。"""
        engine = _make_engine()
        engine._session_segments = ["你好", "世界"]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            path = f.name
        try:
            self.assertTrue(engine.export_session(path, format="markdown"))
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            lines = content.split('\n')
            self.assertEqual(lines[0], "# 转写记录")
            self.assertIn("1. 你好", content)
            self.assertIn("2. 世界", content)
        finally:
            os.unlink(path)

    def test_export_empty(self):
        """空会话导出返回 False。"""
        engine = _make_engine()
        engine._session_segments = []
        self.assertFalse(engine.export_session("/tmp/test.txt"))

    def test_export_thread_safe(self):
        """写入和导出并发安全（不崩溃）。"""
        engine = _make_engine()
        errors = []

        def writer():
            try:
                for i in range(50):
                    with engine._segments_lock:
                        engine._session_segments.append(f"段落{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(10):
                    engine.get_session_segments()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)

    def test_get_session_segments_returns_copy(self):
        """get_session_segments 返回列表副本，不影响内部数据。"""
        engine = _make_engine()
        engine._session_segments = ["原始"]
        segs = engine.get_session_segments()
        segs.append("新增")
        # 内部数据不受影响
        self.assertEqual(len(engine._session_segments), 1)
        self.assertEqual(engine._session_segments[0], "原始")


if __name__ == "__main__":
    unittest.main()
