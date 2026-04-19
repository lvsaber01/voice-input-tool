"""VAD 指示器 — 跨平台抽象类

Windows 使用 Win32 API 实现，macOS/Linux 静默跳过。
"""

import sys
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class VADIndicator(ABC):
    """语音活动可视化指示器抽象类"""

    @abstractmethod
    def show(self):
        """显示指示器"""
        ...

    @abstractmethod
    def hide(self):
        """隐藏指示器"""
        ...

    @abstractmethod
    def update(self, level: float, is_speech: bool):
        """更新 RMS 级别和语音状态

        Args:
            level: RMS 能量级别 (0.0 ~ 1.0)
            is_speech: 是否检测到语音
        """
        ...


class DummyVADIndicator(VADIndicator):
    """空实现（不支持的平台）"""

    def show(self):
        logger.debug("VADIndicator: show — 平台不支持或已禁用")

    def hide(self):
        logger.debug("VADIndicator: hide — 平台不支持或已禁用")

    def update(self, level: float, is_speech: bool):
        pass  # 高频调用，不记录日志


def create_vad_indicator() -> VADIndicator:
    """工厂函数：根据平台创建 VADIndicator 实例"""
    if sys.platform == "win32":
        try:
            from gui.vad_indicator_win32 import Win32VADWindow
            return Win32VADWindow()
        except Exception as e:
            logger.warning("Win32 VAD 指示器创建失败，降级为空实现: %s", e)
            return DummyVADIndicator()
    else:
        logger.info("非 Windows 平台，VAD 指示器使用空实现")
        return DummyVADIndicator()
