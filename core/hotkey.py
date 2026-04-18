"""全局热键管理模块

职责：注册/注销全局热键，处理 PTT / Toggle 模式按键事件。
⚠ 依赖 keyboard 库（Windows 专属，需管理员权限）。
Phase 1 骨架，Phase 2 实现完整逻辑。
"""

import threading
import logging
import platform

logger = logging.getLogger(__name__)


class HotkeyManager:
    """全局热键管理器。

    Args:
        config: HotkeyConfig 实例
        on_start: 录音开始回调（由 CoreEngine 提供）
        on_stop: 录音停止回调（由 CoreEngine 提供）
    """

    def __init__(self, config, on_start, on_stop, on_toggle=None):
        self._config = config
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_toggle = on_toggle
        self._lock = threading.Lock()
        self._registered = False
        self._debounce_time = 0.0  # 上次触发时间（防抖）
        self._DEBOUNCE_MS = 0.2    # 防抖间隔（秒）

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
        """注册全局热键。

        检测管理员权限，根据 mode 选择 PTT 或 Toggle 注册方式。
        如果 conflict_check=True，检测常用键冲突。
        """
        # keyboard 库仅在 Windows 可用
        if platform.system() != "Windows":
            logger.warning("非 Windows 平台，热键注册跳过（开发模式）")
            return

        try:
            import keyboard
        except ImportError:
            logger.error("keyboard 库未安装，热键功能不可用")
            return

        # 检测管理员权限（Windows only）
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
            return  # 防抖
        self._debounce_time = now

        # Toggle 模式下由 CoreEngine.on_hotkey_toggle 根据状态判断
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
        """检测快捷键冲突（简化版，警告级别）"""
        conflict_desc = self._CONFLICT_KEYS.get(key)
        if conflict_desc:
            logger.warning("快捷键 '%s' 与 '%s' 可能冲突", key, conflict_desc)
