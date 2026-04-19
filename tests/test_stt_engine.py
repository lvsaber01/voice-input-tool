"""STTEngine 单元测试"""

import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from core.stt_engine import STTEngine


class FakeConfig:
    model_size = "tiny"
    model_path = "./models/"
    language = None
    device = "cpu"
    compute_type = "int8"
    beam_size = 5


class TestSTTEngine(unittest.TestCase):

    def _make_engine(self):
        engine = STTEngine(FakeConfig())
        engine.model = MagicMock()
        return engine

    def test_empty_audio_returns_empty(self):
        """空音频返回 ("", None, 0, None)"""
        engine = self._make_engine()
        result = engine._do_transcribe(np.array([], dtype=np.float32))
        self.assertEqual(result, ("", None, 0, None))

    def test_short_audio_returns_empty(self):
        """短音频 (<3200 samples) 返回空"""
        engine = self._make_engine()
        audio = np.zeros(1000, dtype=np.float32)
        result = engine._do_transcribe(audio)
        self.assertEqual(result, ("", None, 0, None))

    def test_valid_audio_returns_tuple(self):
        """正常音频返回四元组"""
        engine = self._make_engine()
        audio = np.random.randn(16000).astype(np.float32)

        seg = MagicMock()
        seg.text = "hello world"
        info = MagicMock()
        info.language = "en"
        engine.model.transcribe.return_value = ([seg], info)

        text, lang, dur, err = engine._do_transcribe(audio)
        self.assertEqual(text, "hello world")
        self.assertEqual(lang, "en")
        self.assertIsNone(err)
        self.assertGreaterEqual(dur, -1)  # duration may be 0 on fast machines

    def test_unpack_result_normal(self):
        """_unpack_result 正常解包"""
        engine = self._make_engine()
        future = MagicMock()
        future.result.return_value = ("hi", "en", 100, None)
        self.assertEqual(engine._unpack_result(future), ("hi", "en", 100, None))

    def test_unpack_result_exception(self):
        """_unpack_result 异常处理"""
        engine = self._make_engine()
        future = MagicMock()
        future.result.side_effect = RuntimeError("fail")
        text, lang, dur, err = engine._unpack_result(future)
        self.assertEqual(text, "")
        self.assertIsNone(lang)
        self.assertEqual(dur, 0)
        self.assertIsInstance(err, RuntimeError)

    def test_transcribe_exception_returns_error(self):
        """transcribe 异常返回 error"""
        engine = self._make_engine()
        engine.model.transcribe.side_effect = RuntimeError("model error")
        audio = np.random.randn(16000).astype(np.float32)
        text, lang, dur, err = engine._do_transcribe(audio)
        self.assertEqual(text, "")
        self.assertIsInstance(err, RuntimeError)

    def tearDown(self):
        # Clean up any engines
        pass


if __name__ == "__main__":
    unittest.main()
