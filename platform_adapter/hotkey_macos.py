"""macOS 热键管理器

使用 pynput 库（Accessibility API），需要辅助功能权限。
包含 Listener watchdog 自动重连机制。
"""

import time
import threading
import logging
import sys

from platform_adapter.hotkey_base import HotkeyManagerBase

logger = logging.getLogger(__name__)


class MacOSHotkeyManager(HotkeyManagerBase):
    """macOS 全局热键管理器。

    使用 pynput.keyboard.Listener 监听全局按键事件。
    支持 PTT 和 Toggle 两种模式。

    已知问题：
    - pynput Listener 在 macOS 12+ 上可能无故停止
    - 通过 watchdog 线程自动重连
    - 需要辅助功能权限（System Preferences → Privacy → Accessibility）
    """

    def __init__(self, config, on_start, on_stop, on_toggle=None):
        super().__init__(config, on_start, on_stop, on_toggle)

        # 锁获取顺序约定: _lock → _keys_lock，不可反向（防止死锁）
        self._lock = threading.Lock()  # 操作锁（rebind 用）
        self._keys_lock = threading.Lock()  # 按键状态锁
        self._registered = False
        self._running = False

        # PTT 模式状态
        self._ptt_active = False

        # Toggle 模式状态（与 PTT 分离，语义清晰）
        self._recording_active = False

        # 按键状态追踪
        self._current_keys = set()
        self._target_keys = set()

        # pynput Listener
        self._listener = None
        self._watchdog_thread = None

        # 防抖
        self._debounce_time = 0.0
        self._DEBOUNCE_MS = 0.2

    def register(self):
        """注册全局热键

        Returns:
            (success: bool, error_msg: Optional[str]) 元组
        """
        # 检查辅助功能权限
        if not self._check_accessibility():
            self._prompt_accessibility()
            logger.warning("辅助功能权限未授予，热键可能无法工作")

        try:
            import pynput.keyboard as pynput_keyboard
        except ImportError:
            logger.error("pynput 未安装，macOS 热键不可用。请运行: pip install pynput")
            return (False, "pynput 未安装")

        # 解析目标热键
        self._target_keys = self._parse_hotkey(self._config.trigger)

        # 创建 Listener
        self._running = True
        self._listener = pynput_keyboard.Listener(
            on_press=self._handle_press,
            on_release=self._handle_release,
            daemon=True
        )
        self._listener.start()

        # 启动 watchdog
        self._watchdog_thread = threading.Thread(
            target=self._listener_watchdog,
            name="pynput-watchdog",
            daemon=True
        )
        self._watchdog_thread.start()

        self._registered = True
        logger.info("macOS 热键已注册: %s, 模式: %s, 目标键: %s",
                     self._config.trigger, self._config.mode, self._target_keys)
        return (True, None)

    def unregister(self):
        """注销热键"""
        self._running = False
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._registered = False
        logger.info("macOS 热键已注销")

    def rebind(self, new_key: str):
        """运行时热更换快捷键"""
        with self._lock:
            self._ptt_active = False
            self._recording_active = False
            with self._keys_lock:
                self._current_keys.clear()
            self._config.trigger = new_key
            self._target_keys = self._parse_hotkey(new_key)
            logger.info("热键更换为: %s (目标键: %s)", new_key, self._target_keys)

    # ============================================================
    # 按键处理
    # ============================================================

    def _handle_press(self, key):
        """按键按下回调"""
        normalized = self._normalize_key(key)
        with self._keys_lock:
            self._current_keys.add(normalized)
            matched = self._target_keys.issubset(self._current_keys)

        if not matched:
            return

        # 防抖
        now = time.monotonic()
        if now - self._debounce_time < self._DEBOUNCE_MS:
            return
        self._debounce_time = now

        if self._config.mode == "push_to_talk":
            if not self._ptt_active:
                self._ptt_active = True
                self._recording_active = True
                self._on_start()
        else:  # toggle
            # Toggle 模式优先走 on_toggle（由 CoreEngine 状态机决策）
            if self._on_toggle:
                self._on_toggle()
            else:
                # 回退：CoreEngine 状态机有非法转换忽略保护
                if not self._recording_active:
                    self._recording_active = True
                    self._on_start()
                else:
                    self._recording_active = False
                    self._on_stop()

    def _handle_release(self, key):
        """按键释放回调"""
        normalized = self._normalize_key(key)
        with self._keys_lock:
            self._current_keys.discard(normalized)
            still_pressed = self._target_keys.issubset(self._current_keys)

        # PTT 模式：目标键全部释放时停止
        if self._config.mode == "push_to_talk" and self._ptt_active and not still_pressed:
            self._ptt_active = False
            self._recording_active = False
            self._on_stop()

    # ============================================================
    # Listener Watchdog
    # ============================================================

    def _listener_watchdog(self):
        """Listener 健康监控线程

        pynput Listener 在 macOS 12+ 上可能无故停止。
        此线程定期检查 Listener 存活状态，自动重连。

        重连流程:
        1. 显式 stop 旧 Listener（释放资源）
        2. 清空 _current_keys（避免残留按键状态）
        3. 重置 _ptt_active / _recording_active
        4. 创建新 Listener
        """
        while self._running:
            time.sleep(10)
            if not self._running:
                break
            if self._listener and not self._listener.is_alive():
                logger.warning("pynput Listener 已停止，尝试重连...")
                try:
                    import pynput.keyboard as pynput_keyboard

                    # 步骤 1: 显式停止旧 Listener
                    old_listener = self._listener
                    self._listener = None
                    try:
                        old_listener.stop()
                    except Exception:
                        pass

                    # 步骤 2: 清空按键状态
                    with self._keys_lock:
                        self._current_keys.clear()

                    # 步骤 3: 重置录音状态
                    self._ptt_active = False
                    self._recording_active = False

                    # 步骤 4: 创建新 Listener
                    self._listener = pynput_keyboard.Listener(
                        on_press=self._handle_press,
                        on_release=self._handle_release,
                        daemon=True
                    )
                    self._listener.start()
                    logger.info("pynput Listener 重连成功，状态已重置")
                except Exception as e:
                    logger.error("pynput Listener 重连失败: %s", e)

    # ============================================================
    # 辅助功能权限
    # ============================================================

    @staticmethod
    def _check_accessibility() -> bool:
        """检查辅助功能权限"""
        try:
            import ctypes
            lib = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            return bool(lib.AXIsProcessTrusted())
        except Exception:
            # fallback: 尝试 osascript 检测
            try:
                import subprocess
                result = subprocess.run(
                    ["osascript", "-e", 'tell application "System Events" to get name of first process'],
                    capture_output=True, timeout=3
                )
                return result.returncode == 0
            except Exception:
                return True  # 无法检测，假设有权限

    @staticmethod
    def _prompt_accessibility():
        """提示用户授予辅助功能权限"""
        try:
            import subprocess
            subprocess.Popen([
                "osascript", "-e",
                'display dialog "语音输入工具需要辅助功能权限才能监听全局按键。\\n\\n'
                '请前往: 系统偏好设置 → 安全性与隐私 → 隐私 → 辅助功能\\n'
                '将本应用添加到允许列表。" buttons {"好的"}'
            ])
        except Exception:
            logger.warning("无法弹出权限提示对话框")

    # ============================================================
    # 热键解析
    # ============================================================

    def _parse_hotkey(self, key_str: str) -> set:
        """解析热键字符串为 pynput 键集合"""
        import pynput.keyboard as pynput_keyboard

        parts = key_str.lower().replace(" ", "").split("+")
        result = set()

        key_map = {
            "f1": pynput_keyboard.Key.f1, "f2": pynput_keyboard.Key.f2,
            "f3": pynput_keyboard.Key.f3, "f4": pynput_keyboard.Key.f4,
            "f5": pynput_keyboard.Key.f5, "f6": pynput_keyboard.Key.f6,
            "f7": pynput_keyboard.Key.f7, "f8": pynput_keyboard.Key.f8,
            "f9": pynput_keyboard.Key.f9, "f10": pynput_keyboard.Key.f10,
            "f11": pynput_keyboard.Key.f11, "f12": pynput_keyboard.Key.f12,
            "ctrl": pynput_keyboard.Key.ctrl, "alt": pynput_keyboard.Key.alt,
            "shift": pynput_keyboard.Key.shift, "cmd": pynput_keyboard.Key.cmd,
            "space": pynput_keyboard.Key.space, "tab": pynput_keyboard.Key.tab,
            "enter": pynput_keyboard.Key.enter, "esc": pynput_keyboard.Key.esc,
        }

        for part in parts:
            if part in key_map:
                result.add(key_map[part])
            else:
                result.add(pynput_keyboard.KeyCode.from_char(part))

        return result

    def _normalize_key(self, key):
        """规范化按键为 pynput 对象，与 _parse_hotkey 返回类型一致"""
        try:
            import pynput.keyboard as pynput_keyboard
            if isinstance(key, pynput_keyboard.Key):
                return key
            elif hasattr(key, 'char') and key.char:
                return pynput_keyboard.KeyCode.from_char(key.char.lower())
            elif hasattr(key, 'vk') and key.vk:
                return pynput_keyboard.KeyCode.from_vk(key.vk)
        except Exception:
            pass
        return str(key)
