"""文字注入模块（委托薄封装）

将实际注入操作委托给 platform_adapter 层。
core 层零感知具体平台实现。
"""

import logging

from platform_adapter import create_clipboard_injector

logger = logging.getLogger(__name__)


class TextInjector:
    """文字注入器（委托模式）。

    内部持有平台对应的 ClipboardInjector 实例，转发所有调用。
    对外接口保持不变，engine.py 无需修改。
    """

    def __init__(self, config):
        self._impl = create_clipboard_injector(config)

    def inject(self, text: str) -> bool:
        return self._impl.inject(text)
