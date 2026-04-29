"""win32_input.py 单元测试

在 macOS 上运行，通过 patch sys.platform 验证逻辑正确性。
ctypes.wintypes 不可用时会自动 fallback 到 mock（win32_input.py 内置）。
"""

import sys
import ctypes
import types
import unittest
from unittest.mock import MagicMock


class _Win32Env:
    """上下文管理器：设置/恢复 Windows mock 环境"""

    def __init__(self):
        self._orig_platform = None
        self._orig_windll = None

    def enter(self):
        # 清除模块缓存
        sys.modules.pop('platform_adapter.win32_input', None)
        sys.modules.pop('ctypes.wintypes', None)
        if hasattr(ctypes, 'wintypes'):
            delattr(ctypes, 'wintypes')

        # mock ctypes.windll（macOS 上不存在）
        self._orig_windll = getattr(ctypes, 'windll', None)
        ctypes.windll = MagicMock()
        ctypes.windll.user32 = MagicMock()

        self._orig_platform = sys.platform
        sys.platform = 'win32'
        import platform_adapter.win32_input as m
        return m

    def exit(self):
        if self._orig_windll is None:
            if hasattr(ctypes, 'windll'):
                delattr(ctypes, 'windll')
        else:
            ctypes.windll = self._orig_windll
        sys.platform = self._orig_platform


class _DarwinEnv:
    """上下文管理器：设置/恢复 macOS mock 环境"""

    def __init__(self):
        self._orig_platform = None

    def enter(self):
        sys.modules.pop('platform_adapter.win32_input', None)
        if hasattr(ctypes, 'wintypes'):
            delattr(ctypes, 'wintypes')
        self._orig_platform = sys.platform
        sys.platform = 'darwin'
        import platform_adapter.win32_input as m
        return m

    def exit(self):
        sys.platform = self._orig_platform


# ---- 基类：setUpClass/tearDownClass 管理环境 ----

class _Win32TestCase(unittest.TestCase):
    """需要 Win32 mock 环境的测试基类"""

    env = None  # 子类覆盖

    @classmethod
    def setUpClass(cls):
        assert cls.env is not None
        cls.mod = cls.env.enter()

    @classmethod
    def tearDownClass(cls):
        cls.env.exit()


class TestWin32InputImport(unittest.TestCase):
    """模块导入测试（不需要实例化 Win32Input）"""

    def test_non_windows_returns_none(self):
        env = _DarwinEnv()
        mod = env.enter()
        try:
            self.assertIsNone(mod.Win32Input)
            self.assertIsNone(mod.get_win32_input())
        finally:
            env.exit()

    def test_windows_returns_class(self):
        env = _Win32Env()
        mod = env.enter()
        try:
            self.assertIsNotNone(mod.Win32Input)
        finally:
            env.exit()


class TestWin32InputStructures(_Win32TestCase):
    env = _Win32Env()

    def test_keybdinput_fields(self):
        for name in ('wVk', 'wScan', 'dwFlags', 'time', 'dwExtraInfo'):
            self.assertTrue(hasattr(self.mod.KEYBDINPUT, name))

    def test_mouseinput_fields(self):
        for name in ('dx', 'dy', 'mouseData', 'dwFlags', 'time', 'dwExtraInfo'):
            self.assertTrue(hasattr(self.mod.MOUSEINPUT, name))

    def test_union_members(self):
        self.assertTrue(hasattr(self.mod.INPUT_UNION, 'mi'))
        self.assertTrue(hasattr(self.mod.INPUT_UNION, 'ki'))

    def test_union_size_correct(self):
        union_sz = ctypes.sizeof(self.mod.INPUT_UNION)
        mouse_sz = ctypes.sizeof(self.mod.MOUSEINPUT)
        keybd_sz = ctypes.sizeof(self.mod.KEYBDINPUT)
        self.assertGreaterEqual(union_sz, mouse_sz)
        self.assertGreaterEqual(union_sz, keybd_sz)

    def test_input_struct(self):
        self.assertTrue(hasattr(self.mod.INPUT, 'type'))
        self.assertTrue(hasattr(self.mod.INPUT, 'union'))

    def test_ulong_ptr_size(self):
        self.assertGreaterEqual(ctypes.sizeof(self.mod.ULONG_PTR), 4)


class TestWin32InputSingleton(_Win32TestCase):
    env = _Win32Env()

    def test_returns_instance(self):
        self.assertIsInstance(self.mod.get_win32_input(), self.mod.Win32Input)

    def test_same_instance(self):
        self.assertIs(self.mod.get_win32_input(), self.mod.get_win32_input())


class TestMakeKey(_Win32TestCase):
    env = _Win32Env()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.win32 = cls.mod.Win32Input()

    def test_default_flags(self):
        inp = self.win32._make_key(self.mod.VK_V)
        self.assertEqual(inp.type, self.mod.INPUT_KEYBOARD)
        self.assertEqual(inp.union.ki.wVk, self.mod.VK_V)
        self.assertEqual(inp.union.ki.dwFlags, 0)

    def test_keyup_flag(self):
        inp = self.win32._make_key(self.mod.VK_CONTROL, self.mod.KEYEVENTF_KEYUP)
        self.assertEqual(inp.union.ki.wVk, self.mod.VK_CONTROL)
        self.assertEqual(inp.union.ki.dwFlags, self.mod.KEYEVENTF_KEYUP)

    def test_zero_defaults(self):
        inp = self.win32._make_key(self.mod.VK_V)
        self.assertEqual(inp.union.ki.wScan, 0)
        self.assertEqual(inp.union.ki.time, 0)


class TestSimulateCtrlV(_Win32TestCase):
    env = _Win32Env()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.win32 = cls.mod.Win32Input()

    def test_four_events_in_order(self):
        with patch.object(self.win32, '_send_input', return_value=4) as mock:
            self.assertTrue(self.win32.simulate_ctrl_v())
            inputs = mock.call_args[0][0]
            self.assertEqual(len(inputs), 4)
            self.assertEqual(inputs[0].union.ki.wVk, self.mod.VK_CONTROL)
            self.assertEqual(inputs[1].union.ki.wVk, self.mod.VK_V)
            self.assertEqual(inputs[2].union.ki.dwFlags, self.mod.KEYEVENTF_KEYUP)
            self.assertEqual(inputs[3].union.ki.wVk, self.mod.VK_CONTROL)
            self.assertEqual(inputs[3].union.ki.dwFlags, self.mod.KEYEVENTF_KEYUP)

    def test_partial_send_fails(self):
        with patch.object(self.win32, '_send_input', return_value=3):
            self.assertFalse(self.win32.simulate_ctrl_v())

    def test_error_fails(self):
        with patch.object(self.win32, '_send_input', return_value=-1):
            self.assertFalse(self.win32.simulate_ctrl_v())


class TestSendUnicodeText(_Win32TestCase):
    env = _Win32Env()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.win32 = cls.mod.Win32Input()

    def test_empty_string(self):
        self.assertTrue(self.win32.send_unicode_text(""))

    def test_ascii_key_events(self):
        with patch.object(self.win32, '_send_input', return_value=6) as mock:
            self.assertTrue(self.win32.send_unicode_text("abc"))
            inputs = mock.call_args[0][0]
            self.assertEqual(len(inputs), 6)
            self.assertEqual(inputs[0].union.ki.wScan, ord('a'))
            self.assertEqual(inputs[0].union.ki.dwFlags, self.mod.KEYEVENTF_UNICODE)
            self.assertEqual(inputs[1].union.ki.dwFlags,
                             self.mod.KEYEVENTF_UNICODE | self.mod.KEYEVENTF_KEYUP)

    def test_chinese_codepoints(self):
        with patch.object(self.win32, '_send_input', return_value=4) as mock:
            self.assertTrue(self.win32.send_unicode_text("你好"))
            inputs = mock.call_args[0][0]
            self.assertEqual(inputs[0].union.ki.wScan, ord('你'))
            self.assertEqual(inputs[2].union.ki.wScan, ord('好'))

    def test_batch_splitting(self):
        with patch.object(self.win32, '_send_input', return_value=10) as mock:
            self.win32.send_unicode_text("abcdefghij", batch_size=3)
            self.assertEqual(mock.call_count, 4)

    def test_batch_error(self):
        with patch.object(self.win32, '_send_input', return_value=-1):
            self.assertFalse(self.win32.send_unicode_text("abc", batch_size=1))

    def test_down_up_order(self):
        with patch.object(self.win32, '_send_input', return_value=4) as mock:
            self.win32.send_unicode_text("ab")
            inputs = mock.call_args[0][0]
            for i in range(0, 4, 2):
                self.assertEqual(inputs[i].union.ki.dwFlags, self.mod.KEYEVENTF_UNICODE)
                self.assertEqual(inputs[i + 1].union.ki.dwFlags,
                                 self.mod.KEYEVENTF_UNICODE | self.mod.KEYEVENTF_KEYUP)


class TestSendInput(_Win32TestCase):
    env = _Win32Env()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.win32 = cls.mod.Win32Input()

    def test_empty_list(self):
        self.assertEqual(self.win32._send_input([]), 0)

    def test_success(self):
        mock_user32 = MagicMock()
        mock_user32.SendInput.return_value = 4
        self.win32.user32 = mock_user32
        inp = self.win32._make_key(self.mod.VK_V)
        self.assertEqual(self.win32._send_input([inp]), 4)

    def test_oserror(self):
        mock_user32 = MagicMock()
        mock_user32.SendInput.side_effect = OSError("fail")
        self.win32.user32 = mock_user32
        inp = self.win32._make_key(self.mod.VK_V)
        self.assertEqual(self.win32._send_input([inp]), -1)


# 修复：import 位置
from unittest.mock import patch

if __name__ == '__main__':
    unittest.main()
