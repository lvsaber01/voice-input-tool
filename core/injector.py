"""文字注入模块

职责：将识别文本写入剪贴板并模拟 Ctrl+V 粘贴。
使用 Win32 API（ctypes），无第三方依赖。
Phase 1 骨架，Phase 2 实现完整逻辑。
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TextInjector:
    """文字注入器。

    通过 Win32 Clipboard API 写入剪贴板，通过 SendInput 模拟粘贴。
    支持剪贴板备份/恢复。

    Args:
        config: InjectConfig 实例
    """

    def __init__(self, config):
        self.config = config
        self._backup: Optional[str] = None

    def inject(self, text: str) -> bool:
        """注入文字到当前光标位置。

        流程: 备份剪贴板 → 写入文本 → 模拟 Ctrl+V → 恢复剪贴板

        Returns:
            True=成功, False=失败（文字保留在剪贴板）
        """
        try:
            # 1. 备份剪贴板
            if self.config.clipboard_backup:
                self._backup = self._read_clipboard()

            # 2. 写入剪贴板
            if not self._write_clipboard(text):
                raise RuntimeError("剪贴板写入失败")

            # 3. 等待延时
            time.sleep(self.config.paste_delay_ms / 1000)

            # 4. 模拟 Ctrl+V
            if self.config.auto_paste:
                self._simulate_paste()

            # 5. 恢复剪贴板
            if self.config.clipboard_restore and self._backup is not None:
                time.sleep(0.2)  # 等待粘贴完成
                self._write_clipboard(self._backup)

            if self.config.add_trailing_space:
                # Phase 2: 注入后追加空格
                pass

            logger.info("文字注入成功")
            return True
        except Exception as e:
            logger.error("注入失败: %s", e)
            self._handle_error(text)
            return False

    def _read_clipboard(self) -> Optional[str]:
        """读取剪贴板文本内容。非文本格式返回 None。"""
        import sys
        if sys.platform == 'win32':
            return self._read_clipboard_win32()
        else:
            try:
                import pyperclip
                return pyperclip.paste()
            except Exception:
                return None

    def _write_clipboard(self, text: str) -> bool:
        """原子写入剪贴板"""
        import sys
        if sys.platform == 'win32':
            return self._write_clipboard_win32(text)
        else:
            try:
                import pyperclip
                pyperclip.copy(text)
                return True
            except Exception as e:
                logger.error("写入剪贴板失败: %s", e)
                return False

    def _simulate_paste(self):
        """模拟 Ctrl+V 粘贴"""
        import sys
        if sys.platform == 'win32':
            self._simulate_paste_win32()
        else:
            logger.info("非 Windows 平台，请手动 Ctrl+V 粘贴")

    def _handle_error(self, text: str):
        """注入失败: 文字保留在剪贴板，通知用户"""
        self._write_clipboard(text)
        logger.info("文字已保留在剪贴板")

    # ============================================================
    # Windows 平台 Win32 API 实现
    # ============================================================

    def _read_clipboard_win32(self) -> Optional[str]:
        """使用 Win32 API 读取剪贴板。非文本格式返回 None。"""
        import ctypes
        from ctypes import wintypes

        CF_UNICODETEXT = 13
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        if not user32.OpenClipboard(0):
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None  # 非文本格式
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return ctypes.c_wchar_p(ptr).value
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def _write_clipboard_win32(self, text: str) -> bool:
        """使用 Win32 API 原子写入剪贴板"""
        import ctypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        if not user32.OpenClipboard(0):
            return False
        try:
            user32.EmptyClipboard()
            # 分配全局内存
            data = text.encode('utf-16-le') + b'\x00\x00'  # 含 null terminator
            buf_size = len(data)
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, buf_size)
            if not handle:
                return False
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                kernel32.GlobalFree(handle)
                return False
            try:
                ctypes.memmove(ptr, data, buf_size)
            finally:
                kernel32.GlobalUnlock(ptr)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
            return True
        finally:
            user32.CloseClipboard()

    def _simulate_paste_win32(self):
        """使用 Win32 SendInput 模拟 Ctrl+V"""
        import ctypes

        VK_CONTROL = 0x11
        VK_V = 0x56
        KEYEVENTF_KEYUP = 0x0002

        INPUT_KEYBOARD = 1

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

        def _make_key_input(vk, flags=0):
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            inp._input.ki.wVk = vk
            inp._input.ki.dwFlags = flags
            return inp

        user32 = ctypes.windll.user32
        extra = ctypes.pointer(ctypes.c_ulong(0))

        inputs = [
            _make_key_input(VK_CONTROL),
            _make_key_input(VK_V),
            _make_key_input(VK_V, KEYEVENTF_KEYUP),
            _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
        ]

        n = len(inputs)
        arr = (INPUT * n)(*inputs)
        sent = user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))
        if sent != n:
            logger.warning("SendInput 失败，请手动 Ctrl+V 粘贴")
