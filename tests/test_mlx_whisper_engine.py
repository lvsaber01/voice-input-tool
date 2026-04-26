"""MlxWhisperEngine 单元测试（Mock，无需 mlx-whisper 安装）

测试覆盖：
  T1  is_available() 平台检测
  T2  get_model_repo() 模型名映射
  T3  load_model() 成功/失败
  T4  transcribe_sync 英文
  T5  transcribe_sync 空音频
  T6  transcribe_sync 短音频
  T7  transcribe_async 回调
  T8  繁简转换
  T9  language 参数
  T10 shutdown
"""

import sys
import unittest
import time
from unittest.mock import MagicMock, patch
import numpy as np

# 在 import 之前 mock 掉 mlx_whisper
_mlx_mock = MagicMock()
if 'mlx_whisper' not in sys.modules:
    sys.modules['mlx_whisper'] = _mlx_mock

from core.stt_mlx_whisper import MlxWhisperEngine


# ── helpers ──────────────────────────────────────────────

class FakeConfig:
    model_size = "large-v3-turbo"
    language = None

class FakeConfigZH:
    model_size = "large-v3-turbo"
    language = "zh"

class FakeConfigEN:
    model_size = "large-v3-turbo"
    language = "en"

def _audio(samples=16000):
    """生成测试用 float32 音频"""
    return np.random.randn(samples).astype(np.float32)


# ── T1: is_available ────────────────────────────────────

class TestIsAvailable(unittest.TestCase):

    @patch('core.stt_mlx_whisper.platform')
    def test_macos_available(self, mock_plat):
        mock_plat.system.return_value = 'Darwin'
        self.assertTrue(MlxWhisperEngine.is_available())

    @patch('core.stt_mlx_whisper.platform')
    def test_windows_not_available(self, mock_plat):
        mock_plat.system.return_value = 'Windows'
        self.assertFalse(MlxWhisperEngine.is_available())

    @patch('core.stt_mlx_whisper.platform')
    def test_linux_not_available(self, mock_plat):
        mock_plat.system.return_value = 'Linux'
        self.assertFalse(MlxWhisperEngine.is_available())

    @patch('core.stt_mlx_whisper.platform')
    def test_import_error(self, mock_plat):
        mock_plat.system.return_value = 'Darwin'
        with patch.dict('sys.modules', {'mlx_whisper': None}):
            self.assertFalse(MlxWhisperEngine.is_available())


# ── T2: get_model_repo ──────────────────────────────────

class TestGetModelRepo(unittest.TestCase):

    def _check(self, size, expected):
        self.assertEqual(MlxWhisperEngine.get_model_repo(size), expected)

    def test_all_known_sizes(self):
        cases = {
            'tiny': 'mlx-community/whisper-tiny',
            'base': 'mlx-community/whisper-base-mlx',
            'small': 'mlx-community/whisper-small-mlx',
            'medium': 'mlx-community/whisper-medium-mlx',
            'large-v3': 'mlx-community/whisper-large-v3-mlx',
            'large-v3-turbo': 'mlx-community/whisper-large-v3-turbo',
        }
        for size, expected in cases.items():
            with self.subTest(size=size):
                self._check(size, expected)

    def test_unknown_defaults_to_turbo(self):
        self._check('mega-ultra', 'mlx-community/whisper-large-v3-turbo')

    def test_empty_defaults_to_turbo(self):
        self._check('', 'mlx-community/whisper-large-v3-turbo')


# ── T3: load_model ──────────────────────────────────────

class TestLoadModel(unittest.TestCase):

    def test_success(self):
        eng = MlxWhisperEngine(FakeConfig())
        ok, msg = eng.load_model()
        self.assertTrue(ok)
        self.assertEqual(msg, '')
        eng.shutdown()

    def test_import_error(self):
        eng = MlxWhisperEngine(FakeConfig())
        with patch.dict('sys.modules', {'mlx_whisper': None}):
            ok, msg = eng.load_model()
            self.assertFalse(ok)
            self.assertIn('mlx_whisper 未安装', msg)
        eng.shutdown()


# ── T5/T6: 空音频 / 短音频 ─────────────────────────────

class TestEdgeAudio(unittest.TestCase):

    def test_empty_audio(self):
        eng = MlxWhisperEngine(FakeConfig())
        self.assertEqual(eng.transcribe_sync(np.array([], dtype=np.float32)), "")
        eng.shutdown()

    def test_short_audio(self):
        eng = MlxWhisperEngine(FakeConfig())
        self.assertEqual(eng.transcribe_sync(np.zeros(3199, dtype=np.float32)), "")
        eng.shutdown()

    def test_boundary_3200_triggers_transcribe(self):
        """恰好 3200 samples (0.2s) 走转写逻辑"""
        eng = MlxWhisperEngine(FakeConfig())
        mock = sys.modules['mlx_whisper']
        mock.transcribe.return_value = {'text': '  hi  ', 'language': 'en'}
        result = eng.transcribe_sync(np.zeros(3200, dtype=np.float32))
        self.assertEqual(result, "hi")
        eng.shutdown()


# ── T4: 正常转写 ────────────────────────────────────────

class TestTranscribeSync(unittest.TestCase):

    def setUp(self):
        self.eng = MlxWhisperEngine(FakeConfig())
        self.mock = sys.modules['mlx_whisper']

    def tearDown(self):
        self.eng.shutdown()

    def test_chinese(self):
        self.mock.transcribe.return_value = {'text': '你好世界', 'language': 'zh'}
        self.assertEqual(self.eng.transcribe_sync(_audio()), '你好世界')

    def test_english(self):
        self.mock.transcribe.return_value = {'text': 'hello world', 'language': 'en'}
        self.assertEqual(self.eng.transcribe_sync(_audio()), 'hello world')

    def test_text_stripped(self):
        self.mock.transcribe.return_value = {'text': '  hello  ', 'language': 'en'}
        self.assertEqual(self.eng.transcribe_sync(_audio()), 'hello')

    def test_none_text(self):
        self.mock.transcribe.return_value = {'text': None, 'language': 'en'}
        self.assertEqual(self.eng.transcribe_sync(_audio()), "")

    def test_missing_text_key(self):
        self.mock.transcribe.return_value = {'language': 'en'}
        self.assertEqual(self.eng.transcribe_sync(_audio()), "")

    def test_exception_returns_empty(self):
        self.mock.transcribe.side_effect = RuntimeError("MLX error")
        self.assertEqual(self.eng.transcribe_sync(_audio()), "")
        self.mock.transcribe.side_effect = None


# ── _do_transcribe 四元组 ───────────────────────────────

class TestDoTranscribe(unittest.TestCase):

    def setUp(self):
        self.eng = MlxWhisperEngine(FakeConfig())
        self.mock = sys.modules['mlx_whisper']

    def tearDown(self):
        self.eng.shutdown()

    def test_returns_four_tuple(self):
        self.mock.transcribe.return_value = {'text': 'test', 'language': 'en'}
        result = self.eng._do_transcribe(_audio())
        self.assertEqual(len(result), 4)
        text, lang, dur, err = result
        self.assertEqual(text, 'test')
        self.assertEqual(lang, 'en')
        self.assertIsInstance(dur, int)
        self.assertIsNone(err)

    def test_empty_tuple(self):
        self.assertEqual(
            self.eng._do_transcribe(np.array([], dtype=np.float32)),
            ("", None, 0, None))

    def test_exception_tuple(self):
        self.mock.transcribe.side_effect = ValueError("bad")
        text, lang, dur, err = self.eng._do_transcribe(_audio())
        self.assertEqual(text, "")
        self.assertIsNone(lang)
        self.assertEqual(dur, 0)
        self.assertIsInstance(err, ValueError)
        self.mock.transcribe.side_effect = None


# ── T9: language 参数 ───────────────────────────────────

class TestLanguage(unittest.TestCase):

    def setUp(self):
        self.mock = sys.modules['mlx_whisper']
        self.mock.transcribe.return_value = {'text': 'ok', 'language': 'en'}

    def tearDown(self):
        self.eng.shutdown()

    def test_none_means_auto(self):
        self.eng = MlxWhisperEngine(FakeConfig())
        self.eng._do_transcribe(_audio())
        kw = self.mock.transcribe.call_args[1]
        self.assertIsNone(kw['language'])

    def test_zh_passed(self):
        self.eng = MlxWhisperEngine(FakeConfigZH())
        self.eng._do_transcribe(_audio())
        kw = self.mock.transcribe.call_args[1]
        self.assertEqual(kw['language'], 'zh')

    def test_auto_string_means_none(self):
        cfg = FakeConfig()
        cfg.language = 'auto'
        self.eng = MlxWhisperEngine(cfg)
        self.eng._do_transcribe(_audio())
        kw = self.mock.transcribe.call_args[1]
        self.assertIsNone(kw['language'])


# ── T8: 繁简转换 ───────────────────────────────────────

class TestT2S(unittest.TestCase):

    def setUp(self):
        self.eng = MlxWhisperEngine(FakeConfigZH())
        self.mock = sys.modules['mlx_whisper']

    def tearDown(self):
        self.eng.shutdown()

    def test_with_opencc(self):
        self.mock.transcribe.return_value = {'text': '這是一個測試', 'language': 'zh'}
        mock_cc = MagicMock()
        mock_cc.convert.return_value = '这是一个测试'
        with patch.dict('sys.modules', {'opencc': MagicMock(OpenCC=MagicMock(return_value=mock_cc))}):
            self.assertEqual(self.eng.transcribe_sync(_audio()), '这是一个测试')

    def test_without_opencc(self):
        self.mock.transcribe.return_value = {'text': '這是一個測試', 'language': 'zh'}
        with patch.dict('sys.modules', {'opencc': None}):
            self.assertEqual(self.eng.transcribe_sync(_audio()), '這是一個測試')

    def test_english_no_conversion(self):
        self.mock.transcribe.return_value = {'text': 'hello', 'language': 'en'}
        self.assertEqual(self.eng.transcribe_sync(_audio()), 'hello')


# ── T7: transcribe_async ────────────────────────────────

class TestTranscribeAsync(unittest.TestCase):

    def setUp(self):
        self.eng = MlxWhisperEngine(FakeConfig())
        self.mock = sys.modules['mlx_whisper']
        self.mock.reset_mock()
        # 显式清除 side_effect（reset_mock 不一定清子 mock）
        self.mock.transcribe.side_effect = None
        self.mock.transcribe.return_value = {'text': '', 'language': 'en'}
        self.results = []

    def _cb(self, text, lang, dur, err):
        self.results.append((text, lang, dur, err))

    def tearDown(self):
        self.eng.shutdown()

    def test_callback_triggered(self):
        self.mock.transcribe.return_value = {'text': 'async ok', 'language': 'en'}
        self.eng.transcribe_async(_audio(), self._cb)
        for _ in range(50):
            if self.results:
                break
            time.sleep(0.1)
        self.assertEqual(len(self.results), 1)
        self.assertEqual(self.results[0][0], 'async ok')
        self.assertIsNone(self.results[0][3])

    def test_busy_rejects(self):
        """STT 正忙时拒绝新请求"""
        import threading
        block_event = threading.Event()

        def slow_transcribe(*a, **k):
            block_event.wait(timeout=10)
            return {'text': 'slow', 'language': 'en'}

        self.mock.transcribe.side_effect = slow_transcribe

        # 提交第一个任务（会阻塞在 block_event）
        self.eng.transcribe_async(_audio(), self._cb)
        time.sleep(0.3)  # 确保线程池已开始执行

        # 第二个请求应被拒绝
        self.eng.transcribe_async(_audio(), self._cb)
        time.sleep(0.2)

        # 释放第一个任务
        block_event.set()

        for _ in range(50):
            if len(self.results) >= 2:
                break
            time.sleep(0.1)

        self.assertGreaterEqual(len(self.results), 2)
        # 找到包含 error 的那个回调
        errors = [r for r in self.results if r[3] is not None]
        self.assertTrue(len(errors) > 0, "应至少有一个回调包含错误")
        self.assertIsInstance(errors[0][3], RuntimeError)
        self.assertIn("正忙", str(errors[0][3]))
        self.mock.transcribe.side_effect = None

    def test_empty_audio_async(self):
        self.eng.transcribe_async(np.array([], dtype=np.float32), self._cb)
        for _ in range(20):
            if self.results:
                break
            time.sleep(0.1)
        self.assertEqual(len(self.results), 1)
        self.assertEqual(self.results[0][0], "")


# ── T10: shutdown ───────────────────────────────────────

class TestShutdown(unittest.TestCase):

    def test_clean_shutdown(self):
        eng = MlxWhisperEngine(FakeConfig())
        eng.shutdown()

    def test_after_transcribe(self):
        eng = MlxWhisperEngine(FakeConfig())
        mock = sys.modules['mlx_whisper']
        mock.transcribe.return_value = {'text': 'ok', 'language': 'en'}
        eng.transcribe_sync(_audio())
        eng.shutdown()


# ── model_repo 正确传递 ────────────────────────────────

class TestModelRepoArg(unittest.TestCase):

    def setUp(self):
        self.mock = sys.modules['mlx_whisper']
        self.mock.transcribe.return_value = {'text': 'ok', 'language': 'en'}

    def tearDown(self):
        self.eng.shutdown()

    def test_turbo(self):
        self.eng = MlxWhisperEngine(FakeConfig())
        self.eng._do_transcribe(_audio())
        kw = self.mock.transcribe.call_args[1]
        self.assertEqual(kw['path_or_hf_repo'], 'mlx-community/whisper-large-v3-turbo')

    def test_small(self):
        cfg = FakeConfig()
        cfg.model_size = 'small'
        self.eng = MlxWhisperEngine(cfg)
        self.eng._do_transcribe(_audio())
        kw = self.mock.transcribe.call_args[1]
        self.assertEqual(kw['path_or_hf_repo'], 'mlx-community/whisper-small-mlx')


if __name__ == '__main__':
    unittest.main()
