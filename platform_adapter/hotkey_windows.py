"""Windows 热键管理器

使用 keyboard 库（LowLevelKeyboardHook），需管理员权限。
从 core/hotkey.py 迁移，继承 HotkeyManagerBase。
"""

import threading
import logging
import platform

from platform_adapter.hotkey_base import HotkeyManagerBase

logger = logging.getLogger(__name__)


class WindowsHotkeyManager(HotkeyManagerBase):
    """Windows 全局热键管理器。

    依赖 keyboard 库，需管理员权限运行。
    支持 PTT（push_to_talk）和 Toggle 两种模式。
    """

    def __init__(self, config, on_start, on_stop, on_toggle=None):
        super().__init__(config, on_start, on_stop, on_toggle)
        self._lock = threading.Lock()
        self._registered = False
        self._debounce_time = 0.0
        self._DEBOUNCE_MS = 0.2

        # 已知的快捷键冲突列表
        self._CONFLICT_KEYS = {
            "f5": "浏览器刷新",
            "f11": "全屏切换",
            "f12": "开发者工具",
            "print screen": "截图",
            "ctrl+f4": "关闭窗口",
            "alt+f4": "关闭程序",
        }

    def register(self):
        """注册全局热键"""
        if platform.system() != "Windows":
            logger.warning("非 Windows 平台，热键注册跳过")
            return

        try:
            import keyboard
        except ImportError:
            logger.error("keyboard 库未安装，热键功能不可用")
            return

        # 检测管理员权限
        try:
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():
                raise PermissionError("需要管理员权限运行本工具")
        except PermissionError:
            raise
        except Exception as e:
            logger.warning("管理员权限检测异常: %s", e)

        key = self._config.trigger.lower()

        # 冲突检测
        if self._config.conflict_check:
            self._check_conflicts(key)

        # 根据模式注册
        if self._config.mode == "push_to_talk":
            keyboard.on_press_key(key, self._on_press)
            keyboard.on_release_key(key, self._on_release)
        else:  # toggle
            keyboard.add_hotkey(key, self._handle_toggle)

        self._registered = True
        logger.info("热键已注册: %s, 模式: %s", key, self._config.mode)

    def unregister(self):
        """注销热键"""
        if not self._registered:
            return
        try:
            import keyboard
            keyboard.unhook_all()
        except (ImportError, Exception) as e:
            logger.debug("注销热键异常: %s", e)
        self._registered = False
        logger.info("热键已注销")

    def rebind(self, new_key: str):
        """运行时热更换快捷键"""
        with self._lock:
            was_registered = self._registered
            self.unregister()
            self._config.trigger = new_key
            if was_registered:
                self.register()
            logger.info("热键更换为: %s", new_key)

    def _handle_toggle(self):
        """Toggle 模式回调"""
        import time
        now = time.monotonic()
        if now - self._debounce_time < self._DEBOUNCE_MS:
            return
        self._debounce_time = now
        callback = self._on_toggle if self._on_toggle else self._on_start
        callback()

    def _on_press(self, event=None):
        """PTT 模式 — 按键按下"""
        import time
        now = time.monotonic()
        if now - self._debounce_time < self._DEBOUNCE_MS:
            return
        self._debounce_time = now
        self._on_start()

    def _on_release(self, event=None):
        """PTT 模式 — 按键释放"""
        self._on_stop()

    def _check_conflicts(self, key: str):
        """检测快捷键冲突"""
        conflict_desc = self._CONFLICT_KEYS.get(key)
        if conflict_desc:
            logger.warning("快捷键 '%s' 与 '%s' 可能冲突", key, conflict_desc)
