"""Windows 剪贴板注入器

使用 Win32 API (ctypes)，从 core/injector.py 迁移。
"""

import time
import ctypes
import logging
from typing import Optional

from platform_adapter.clipboard_base import ClipboardInjectorBase

logger = logging.getLogger(__name__)

# Win32 常量
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class WindowsClipboardInjector(ClipboardInjectorBase):
    """Windows 剪贴板注入器。

    使用 Win32 Clipboard API 写入剪贴板，通过 SendInput 模拟 Ctrl+V。
    """

    def write_clipboard(self, text: str) -> bool:
        """使用 Win32 API 写入剪贴板（带重试）"""
        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32

            # OpenClipboard 可能被其他程序占用，重试 3 次
            opened = False
            for attempt in range(3):
                if user32.OpenClipboard(0):
                    opened = True
                    break
                logger.debug("OpenClipboard 重试 %d/3", attempt + 1)
                time.sleep(0.05)

            if not opened:
                logger.error("OpenClipboard 失败（重试3次）")
                return False

            try:
                user32.EmptyClipboard()
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, (len(text) + 1) * 2)
                if not h:
                    return False
                p = kernel32.GlobalLock(h)
                if not p:
                    kernel32.GlobalFree(h)
                    return False
                ctypes.memmove(p, text.encode("utf-16-le"), len(text) * 2)
                kernel32.GlobalUnlock(h)
                result = user32.SetClipboardData(CF_UNICODETEXT, h)
                if not result:
                    logger.error("SetClipboardData 失败")
                    return False
                return True
            finally:
                user32.CloseClipboard()
        except Exception as e:
            logger.error("写入剪贴板异常: %s", e)
            return False

    def simulate_paste(self) -> bool:
        """使用 SendInput 模拟 Ctrl+V"""
        try:
            import ctypes
            user32 = ctypes.windll.user32

            INPUT_KEYBOARD = 1
            KEYEVENTF_KEYUP = 0x0002
            VK_CONTROL = 0x11
            VK_V = 0x56

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", ctypes.c_ushort),
                    ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class INPUT(ctypes.Structure):
                class _INPUT(ctypes.Union):
                    _fields_ = [("ki", KEYBDINPUT)]
                _fields_ = [
                    ("type", ctypes.c_ulong),
                    ("_input", _INPUT),
                ]

            def make_key_input(vk, flags=0):
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp._input.ki.wVk = vk
                inp._input.ki.dwFlags = flags
                return inp

            inputs = [
                make_key_input(VK_CONTROL),
                make_key_input(VK_V),
                make_key_input(VK_V, KEYEVENTF_KEYUP),
                make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
            ]
            n = len(inputs)
            user32.SendInput(n, inputs, ctypes.sizeof(INPUT))
            return True
        except Exception as e:
            logger.error("模拟粘贴异常: %s", e)
            return False

    def read_clipboard(self) -> Optional[str]:
        """读取当前剪贴板内容"""
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            if not user32.OpenClipboard(0):
                return None
            try:
                h = user32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return None
                p = kernel32.GlobalLock(h)
                if not p:
                    return None
                text = ctypes.wstring_at(p)
                kernel32.GlobalUnlock(h)
                return text
            finally:
                user32.CloseClipboard()
        except Exception:
            return None
