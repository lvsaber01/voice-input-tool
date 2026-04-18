"""全局热键管理模块（委托薄封装）

将实际热键操作委托给 platform_adapter 层。
core 层零感知具体平台实现。
"""

import logging

from platform_adapter import create_hotkey_manager

logger = logging.getLogger(__name__)


class HotkeyManager:
    """热键管理器（委托模式）。

    内部持有平台对应的 HotkeyManager 实例，转发所有调用。
    对外接口保持不变，engine.py 无需修改。
    """

    def __init__(self, config, on_start, on_stop, on_toggle=None):
        self._impl = create_hotkey_manager(config, on_start, on_stop, on_toggle)

    def register(self):
        self._impl.register()

    def unregister(self):
        self._impl.unregister()

    def rebind(self, new_key: str):
        self._impl.rebind(new_key)
