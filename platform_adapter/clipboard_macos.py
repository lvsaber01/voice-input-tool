"""macOS 剪贴板注入器

使用 pbcopy 写入剪贴板，osascript 模拟 Cmd+V。
失败时回退到 pyautogui。
"""

import subprocess
import logging
from typing import Optional

from platform_adapter.clipboard_base import ClipboardInjectorBase

logger = logging.getLogger(__name__)


class MacOSClipboardInjector(ClipboardInjectorBase):
    """macOS 剪贴板注入器。

    方案:
    - write_clipboard: pbcopy (subprocess)
    - simulate_paste: osascript 模拟 Cmd+V，失败回退 pyautogui
    - read_clipboard: pbpaste (subprocess)

    所有 subprocess 调用有超时保护。
    """

    def write_clipboard(self, text: str) -> bool:
        """通过 pbcopy 写入剪贴板"""
        try:
            proc = subprocess.run(
                ["pbcopy"],
                input=text,
                text=True,
                timeout=2,
                check=True,
            )
            return proc.returncode == 0
        except subprocess.TimeoutExpired:
            logger.error("pbcopy 超时")
            return False
        except FileNotFoundError:
            logger.error("pbcopy 未找到（非 macOS？）")
            return False
        except Exception as e:
            logger.error("写入剪贴板异常: %s", e)
            return False

    def simulate_paste(self) -> bool:
        """模拟 Cmd+V 粘贴

        优先使用 osascript，失败时回退 pyautogui。
        """
        # 方案 1: osascript
        if self._osascript_paste():
            return True

        # 方案 2: pyautogui fallback
        logger.info("osascript 粘贴失败，尝试 pyautogui")
        return self._pyautogui_paste()

    def read_clipboard(self) -> Optional[str]:
        """通过 pbpaste 读取剪贴板"""
        try:
            proc = subprocess.run(
                ["pbpaste"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if proc.returncode == 0:
                return proc.stdout
            return None
        except Exception:
            return None

    def _osascript_paste(self) -> bool:
        """使用 osascript 模拟 Cmd+V"""
        try:
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "v" using command down'],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("osascript 粘贴超时")
            return False
        except Exception as e:
            logger.warning("osascript 粘贴异常: %s", e)
            return False

    def _pyautogui_paste(self) -> bool:
        """使用 pyautogui 模拟 Cmd+V（fallback）"""
        try:
            import pyautogui
            pyautogui.hotkey("command", "v")
            return True
        except ImportError:
            logger.warning("pyautogui 未安装，无法 fallback 粘贴")
            return False
        except Exception as e:
            logger.warning("pyautogui 粘贴异常: %s", e)
            return False
