"""Windows 剪贴板注入器

优先使用 PowerShell（Python 3.13+ ctypes 剪贴板 API 不稳定），
Win32 API 作为降级备选。
"""

import time
import ctypes
import logging
import subprocess
from typing import Optional

from platform_adapter.clipboard_base import ClipboardInjectorBase, _tls

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
        """模拟 Ctrl+V（Win32 SendInput 直接调用优先）

        降级链：SendInput → KEYEVENTF_UNICODE → keyboard.send
        keyboard.send 作为最后兜底（会静默成功，放在前面会阻断降级）。
        """
        from platform_adapter.win32_input import get_win32_input

        win32 = get_win32_input()
        if win32 is None:
            return False

        # 主路径：Win32 SendInput 模拟 Ctrl+V
        if win32.simulate_ctrl_v():
            logger.debug("simulate_paste: SendInput Ctrl+V 成功")
            return True
        logger.warning("simulate_paste: SendInput Ctrl+V 失败，尝试 KEYEVENTF_UNICODE")

        # 降级：KEYEVENTF_UNICODE 直输（绕过剪贴板）
        pending = getattr(_tls, 'pending_text', None)
        if pending:
            if win32.send_unicode_text(pending):
                logger.debug("simulate_paste: KEYEVENTF_UNICODE 成功")
                return True
            logger.warning("simulate_paste: KEYEVENTF_UNICODE 失败，尝试 keyboard.send")

        # 兜底：keyboard.send（可能静默成功）
        try:
            import keyboard
            keyboard.send('ctrl+v')
            logger.warning("simulate_paste: 使用 keyboard.send（可能静默成功）")
            return True
        except Exception as e:
            logger.error("simulate_paste: 所有方式均失败: %s", e)
        return False

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
        """SendInput 模拟 Ctrl+V - 已迁移到 win32_input.py"""
        from platform_adapter.win32_input import get_win32_input
        win32 = get_win32_input()
        if win32 is not None:
            return win32.simulate_ctrl_v()
        return False
