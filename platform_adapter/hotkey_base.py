"""热键管理抽象基类

定义跨平台热键管理器的统一接口。
所有平台实现必须继承此类并实现抽象方法。
"""

from abc import ABC, abstractmethod


class HotkeyManagerBase(ABC):
    """热键管理器基类。

    Args:
        config: 热键配置（包含 trigger、mode 等）
        on_start: 录音开始回调
        on_stop: 录音停止回调
        on_toggle: Toggle 模式回调（可选）
    """

    def __init__(self, config, on_start, on_stop, on_toggle=None):
        self._config = config
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_toggle = on_toggle

    @abstractmethod
    def register(self):
        """注册全局热键"""
        pass

    @abstractmethod
    def unregister(self):
        """注销全局热键"""
        pass

    @abstractmethod
    def rebind(self, new_key: str):
        """运行时更换热键"""
        pass
