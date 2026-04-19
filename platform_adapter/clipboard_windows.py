"""Windows 剪贴板注入器

优先使用 PowerShell（Python 3.13+ ctypes 剪贴板 API 不稳定），
Win32 API 作为降级备选。
"""

import time
import ctypes
import logging
import subprocess
from typing import Optional

from platform_adapter.clipboard_base import ClipboardInjectorBase

logger = logging.getLogger(__name__)

# Win32 常量
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class WindowsClipboardInjector(ClipboardInjectorBase):
    """Windows 剪贴板注入器。

    PowerShell 为主方案（稳定），Win32 API 为备选。
    Python 3.13+ 的 ctypes 剪贴板操作不稳定（OpenClipboard 频繁失败）。
    """

    def write_clipboard(self, text: str) -> bool:
        """写入剪贴板（PowerShell 优先，Win32 降级）"""
        # 方案1: PowerShell
        if self._write_clipboard_powershell(text):
            return True
        # 方案2: Win32 API
        if self._write_clipboard_win32(text):
            return True
        logger.error("所有剪贴板写入方式均失败")
        return False

    def read_clipboard(self) -> Optional[str]:
        """读取剪贴板内容（PowerShell 优先）"""
        # PowerShell
        result = self._read_clipboard_powershell()
        if result is not None:
            return result
        # Win32
        return self._read_clipboard_win32()

    def simulate_paste(self) -> bool:
        """模拟 Ctrl+V
        
        keyboard.send() 需要 Windows 消息循环，在非主线程可能崩溃。
        使用 ctypes windll 调用 SendInput，线程安全。
        """
        return self._simulate_paste_sendinput()

    # ------------------------------------------------------------------
    # PowerShell 实现
    # ------------------------------------------------------------------

    def _write_clipboard_powershell(self, text: str) -> bool:
        """PowerShell 写入剪贴板"""
        try:
            # 用 stdin 传递文本，避免命令行转义问题
            # 使用 STA 模式确保剪贴板访问正常
            ps_cmd = (
                "powershell -Sta -Command \""
                "Add-Type -AssemblyName System.Windows.Forms;"
                "[System.Windows.Forms.Clipboard]::SetText($input)"
                "\""
            )
            result = subprocess.run(
                ["powershell", "-Sta", "-Command",
                 "Add-Type -AssemblyName System.Windows.Forms;"
                 "[Console]::InputEncoding = [System.Text.Encoding]::UTF8;"
                 "[System.Windows.Forms.Clipboard]::SetText([Console]::In.ReadToEnd())"],
                input=text, text=True, capture_output=True, timeout=5,
                encoding='utf-8'
            )
            if result.returncode == 0:
                logger.debug("PowerShell 写入剪贴板成功")
                return True
            logger.warning("PowerShell 写入失败: %s", result.stderr[:200])
        except Exception as e:
            logger.warning("PowerShell 写入异常: %s", e)
        return False

    def _read_clipboard_powershell(self) -> Optional[str]:
        """PowerShell 读取剪贴板"""
        try:
            result = subprocess.run(
                ["powershell", "-Sta", "-Command",
                 "Add-Type -AssemblyName System.Windows.Forms;"
                 "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
                 "if ([System.Windows.Forms.Clipboard]::ContainsText()) {"
                 "  [System.Windows.Forms.Clipboard]::GetText()"
                 "} else { '' }"],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8'
            )
            if result.returncode == 0:
                return result.stdout.rstrip('\r\n')
        except Exception as e:
            logger.warning("PowerShell 读取异常: %s", e)
        return None

    # ------------------------------------------------------------------
    # Win32 API 实现（降级）
    # ------------------------------------------------------------------

    def _write_clipboard_win32(self, text: str) -> bool:
        """Win32 API 写入剪贴板（带重试）"""
        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32

            opened = False
            for attempt in range(3):
                if user32.OpenClipboard(0):
                    opened = True
                    break
                time.sleep(0.05)

            if not opened:
                logger.debug("Win32 OpenClipboard 失败（重试3次）")
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
                    return False
                return True
            finally:
                user32.CloseClipboard()
        except Exception as e:
            logger.debug("Win32 写入异常: %s", e)
        return False

    def _read_clipboard_win32(self) -> Optional[str]:
        """Win32 API 读取剪贴板"""
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

    def _simulate_paste_sendinput(self) -> bool:
        """SendInput 模拟 Ctrl+V"""
        try:
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

            class INPUT_UNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [
                    ("type", ctypes.c_ulong),
                    ("union", INPUT_UNION),
                ]

            def make_key_input(vk, flags=0):
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp.union.ki.wVk = vk
                inp.union.ki.dwFlags = flags
                return inp

            inputs = [
                make_key_input(VK_CONTROL),
                make_key_input(VK_V),
                make_key_input(VK_V, KEYEVENTF_KEYUP),
                make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
            ]
            arr = (INPUT * len(inputs))(*inputs)
            user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
            return True
        except Exception as e:
            logger.error("SendInput 失败: %s", e)
            return False
