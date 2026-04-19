"""按键模拟平台适配层

KeySimulator 基类 + create_key_simulator 工厂函数。
Windows 实现封装 keyboard.send()，其他平台提供空实现。
"""

import sys
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class KeySimulator(ABC):
    """按键模拟器基类"""

    @abstractmethod
    def send(self, key_sequence: str) -> None:
        """发送按键序列（如 'ctrl+z', 'enter'）"""
        ...

    @abstractmethod
    def type_text(self, text: str) -> None:
        """输入文本"""
        ...


class WindowsKeySimulator(KeySimulator):
    """Windows 按键模拟器，封装 keyboard 库"""

    def send(self, key_sequence: str) -> None:
        import keyboard
        keyboard.send(key_sequence)

    def type_text(self, text: str) -> None:
        import keyboard
        keyboard.write(text)


class DummyKeySimulator(KeySimulator):
    """空实现（不支持的平台）"""

    def send(self, key_sequence: str) -> None:
        logger.debug("KeySimulator: send(%s) — 平台不支持", key_sequence)

    def type_text(self, text: str) -> None:
        logger.debug("KeySimulator: type_text(%s) — 平台不支持", text[:30])


def create_key_simulator() -> KeySimulator:
    """工厂函数：根据平台创建 KeySimulator 实例"""
    if sys.platform == "win32":
        try:
            import keyboard  # noqa: F401
            return WindowsKeySimulator()
        except ImportError:
            logger.warning("keyboard 库不可用，按键模拟功能不可用")
            return DummyKeySimulator()
    else:
        logger.debug("非 Windows 平台，按键模拟使用空实现")
        return DummyKeySimulator()
