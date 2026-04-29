"""clipboard_base + clipboard_windows 注入降级链 集成测试

验证 simulate_paste 的 3 层降级链和 _tls 线程安全。
"""

import sys
import types
import unittest
import threading
from unittest.mock import MagicMock, patch

# 预注册 keyboard mock（macOS 上未安装 keyboard）
if 'keyboard' not in sys.modules:
    _mock_keyboard = types.ModuleType('keyboard')
    _mock_keyboard.send = MagicMock()
    sys.modules['keyboard'] = _mock_keyboard

from platform_adapter.clipboard_base import ClipboardInjectorBase, _tls


class FakeConfig:
    restore_clipboard = True
    method = 'keyboard'
    clipboard_backup = True


class TestableInjector(ClipboardInjectorBase):
    """可测试的注入器实现"""

    def __init__(self, config):
        super().__init__(config)
        self.write_results = []
        self.paste_results = []
        self.paste_call_count = 0

    def write_clipboard(self, text):
        self.write_results.append(text)
        return True

    def simulate_paste(self):
        self.paste_call_count += 1
        if self.paste_results:
            return self.paste_results.pop(0)
        return True

    def read_clipboard(self):
        return None


def _clear_tls():
    if hasattr(_tls, 'pending_text'):
        delattr(_tls, 'pending_text')


class TestThreadLocalStorage(unittest.TestCase):

    def setUp(self):
        _clear_tls()

    def tearDown(self):
        _clear_tls()

    def test_tls_is_threading_local(self):
        self.assertIsInstance(_tls, threading.local)

    def test_tls_independent_across_threads(self):
        results = {}

        def thread_a():
            _tls.pending_text = "text_from_a"
            import time
            time.sleep(0.05)
            results['a_sees'] = getattr(_tls, 'pending_text', None)

        def thread_b():
            _tls.pending_text = "text_from_b"
            results['b_sees'] = getattr(_tls, 'pending_text', None)

        t_a = threading.Thread(target=thread_a)
        t_b = threading.Thread(target=thread_b)
        t_a.start()
        t_b.start()
        t_a.join()
        t_b.join()

        self.assertEqual(results['a_sees'], "text_from_a")
        self.assertEqual(results['b_sees'], "text_from_b")

    def test_tls_cleanup_after_success(self):
        injector = TestableInjector(FakeConfig())
        injector._inject_via_keyboard("hello")
        self.assertFalse(hasattr(_tls, 'pending_text'))

    def test_tls_cleanup_on_write_failure(self):
        injector = TestableInjector(FakeConfig())
        injector.write_clipboard = MagicMock(return_value=False)
        injector._inject_via_keyboard("hello")
        self.assertFalse(hasattr(_tls, 'pending_text'))

    def test_tls_cleanup_on_paste_failure(self):
        injector = TestableInjector(FakeConfig())
        injector.paste_results = [False]
        injector._inject_via_keyboard("hello")
        self.assertFalse(hasattr(_tls, 'pending_text'))


class TestInjectViaKeyboard(unittest.TestCase):

    def setUp(self):
        _clear_tls()

    def tearDown(self):
        _clear_tls()

    def test_success_flow(self):
        injector = TestableInjector(FakeConfig())
        result = injector._inject_via_keyboard("hello")
        self.assertTrue(result)
        self.assertEqual(injector.write_results, ["hello"])
        self.assertEqual(injector.paste_call_count, 1)

    def test_write_failure(self):
        injector = TestableInjector(FakeConfig())
        injector.write_clipboard = MagicMock(return_value=False)
        result = injector._inject_via_keyboard("hello")
        self.assertFalse(result)
        self.assertEqual(injector.paste_call_count, 0)

    def test_paste_failure(self):
        injector = TestableInjector(FakeConfig())
        injector.paste_results = [False]
        result = injector._inject_via_keyboard("hello")
        self.assertFalse(result)

    def test_empty_text_skips(self):
        injector = TestableInjector(FakeConfig())
        result = injector.inject("")
        self.assertTrue(result)
        self.assertEqual(injector.write_results, [])

    def test_whitespace_only_skips(self):
        injector = TestableInjector(FakeConfig())
        result = injector.inject("   ")
        self.assertTrue(result)
        self.assertEqual(injector.write_results, [])


class TestDelayConstants(unittest.TestCase):

    def test_settle_delay(self):
        self.assertGreater(ClipboardInjectorBase.CLIPBOARD_SETTLE_SEC, 0)

    def test_post_paste_delay(self):
        self.assertGreater(ClipboardInjectorBase.POST_PASTE_WAIT_SEC, 0)

    def test_restore_delay(self):
        self.assertGreater(ClipboardInjectorBase.CLIPBOARD_RESTORE_SEC, 0)

    def test_settle_ge_post_paste(self):
        self.assertGreaterEqual(
            ClipboardInjectorBase.CLIPBOARD_SETTLE_SEC,
            ClipboardInjectorBase.POST_PASTE_WAIT_SEC
        )


class TestSimulatePasteDegradation(unittest.TestCase):
    """simulate_paste 降级链: SendInput → KEYEVENTF_UNICODE → keyboard.send"""

    def _make_injector(self):
        from platform_adapter.clipboard_windows import WindowsClipboardInjector
        config = MagicMock()
        config.method = 'keyboard'
        config.restore_clipboard = False
        return WindowsClipboardInjector(config)

    def setUp(self):
        _clear_tls()

    def tearDown(self):
        _clear_tls()

    @patch('platform_adapter.win32_input.get_win32_input')
    def test_sendinput_success(self, mock_get):
        mock_win32 = MagicMock()
        mock_win32.simulate_ctrl_v.return_value = True
        mock_get.return_value = mock_win32

        injector = self._make_injector()
        result = injector.simulate_paste()
        self.assertTrue(result)
        mock_win32.simulate_ctrl_v.assert_called_once()
        mock_win32.send_unicode_text.assert_not_called()

    @patch('keyboard.send')
    @patch('platform_adapter.win32_input.get_win32_input')
    def test_fallback_to_unicode(self, mock_get, mock_kb):
        mock_win32 = MagicMock()
        mock_win32.simulate_ctrl_v.return_value = False
        mock_win32.send_unicode_text.return_value = True
        mock_get.return_value = mock_win32

        _tls.pending_text = "降级测试文本"
        injector = self._make_injector()
        result = injector.simulate_paste()
        self.assertTrue(result)
        mock_win32.simulate_ctrl_v.assert_called_once()
        mock_win32.send_unicode_text.assert_called_once_with("降级测试文本")
        mock_kb.assert_not_called()

    @patch('keyboard.send')
    @patch('platform_adapter.win32_input.get_win32_input')
    def test_fallback_to_keyboard_send(self, mock_get, mock_kb):
        mock_win32 = MagicMock()
        mock_win32.simulate_ctrl_v.return_value = False
        mock_win32.send_unicode_text.return_value = False
        mock_get.return_value = mock_win32

        _tls.pending_text = "全部降级测试"
        injector = self._make_injector()
        result = injector.simulate_paste()
        self.assertTrue(result)
        mock_win32.send_unicode_text.assert_called_once_with("全部降级测试")
        mock_kb.assert_called_once_with('ctrl+v')

    @patch('keyboard.send', side_effect=Exception("hook failed"))
    @patch('platform_adapter.win32_input.get_win32_input')
    def test_all_fail(self, mock_get, mock_kb):
        mock_win32 = MagicMock()
        mock_win32.simulate_ctrl_v.return_value = False
        mock_win32.send_unicode_text.return_value = False
        mock_get.return_value = mock_win32

        injector = self._make_injector()
        result = injector.simulate_paste()
        self.assertFalse(result)

    @patch('keyboard.send')
    @patch('platform_adapter.win32_input.get_win32_input')
    def test_unicode_skipped_when_no_pending(self, mock_get, mock_kb):
        mock_win32 = MagicMock()
        mock_win32.simulate_ctrl_v.return_value = False
        mock_get.return_value = mock_win32

        _clear_tls()
        injector = self._make_injector()
        result = injector.simulate_paste()
        mock_win32.send_unicode_text.assert_not_called()
        # 走到 keyboard.send 兜底
        mock_kb.assert_called_once_with('ctrl+v')
        self.assertTrue(result)

    @patch('keyboard.send')
    @patch('platform_adapter.win32_input.get_win32_input')
    def test_unicode_uses_pending_text(self, mock_get, mock_kb):
        mock_win32 = MagicMock()
        mock_win32.simulate_ctrl_v.return_value = False
        mock_win32.send_unicode_text.return_value = True
        mock_get.return_value = mock_win32

        _tls.pending_text = "测试文本"
        injector = self._make_injector()
        result = injector.simulate_paste()
        self.assertTrue(result)
        mock_win32.send_unicode_text.assert_called_once_with("测试文本")
        mock_kb.assert_not_called()

    @patch('platform_adapter.win32_input.get_win32_input')
    def test_win32_none_returns_false(self, mock_get):
        mock_get.return_value = None
        injector = self._make_injector()
        result = injector.simulate_paste()
        self.assertFalse(result)


class TestFullInjectChain(unittest.TestCase):
    """完整注入链路集成测试"""

    def setUp(self):
        _clear_tls()

    def tearDown(self):
        _clear_tls()

    @patch('platform_adapter.win32_input.get_win32_input')
    def test_inject_uses_sendinput(self, mock_get):
        mock_win32 = MagicMock()
        mock_win32.simulate_ctrl_v.return_value = True
        mock_get.return_value = mock_win32

        from platform_adapter.clipboard_windows import WindowsClipboardInjector
        config = MagicMock()
        config.method = 'keyboard'
        config.restore_clipboard = False

        injector = WindowsClipboardInjector(config)
        # mock write_clipboard（macOS 上真实调用会失败）
        injector.write_clipboard = MagicMock(return_value=True)
        result = injector.inject("测试文本")
        self.assertTrue(result)
        mock_win32.simulate_ctrl_v.assert_called_once()

    @patch('keyboard.send')
    @patch('platform_adapter.win32_input.get_win32_input')
    def test_inject_full_degradation(self, mock_get, mock_kb):
        mock_win32 = MagicMock()
        mock_win32.simulate_ctrl_v.return_value = False
        mock_win32.send_unicode_text.return_value = False
        mock_get.return_value = mock_win32

        from platform_adapter.clipboard_windows import WindowsClipboardInjector
        config = MagicMock()
        config.method = 'keyboard'
        config.restore_clipboard = False

        injector = WindowsClipboardInjector(config)
        injector.write_clipboard = MagicMock(return_value=True)
        result = injector.inject("hello")

        self.assertTrue(result)
        mock_win32.simulate_ctrl_v.assert_called_once()
        mock_win32.send_unicode_text.assert_called_once_with("hello")
        mock_kb.assert_called_once_with('ctrl+v')


if __name__ == '__main__':
    unittest.main()
