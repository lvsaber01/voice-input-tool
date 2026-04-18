"""剪贴板注入抽象基类

使用模板方法模式：inject() 流程固定，子类只实现原子操作。
"""

from abc import ABC, abstractmethod
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ClipboardInjectorBase(ABC):
    """剪贴板注入器基类。

    模板方法模式：
    - inject() 是模板方法，定义注入流程
    - write_clipboard / simulate_paste / read_clipboard 是原子操作，由子类实现

    Args:
        config: 注入配置
    """

    def __init__(self, config):
        self.config = config
        self._backup: Optional[str] = None

    def inject(self, text: str) -> bool:
        """注入文字到当前光标位置（模板方法）。

        流程:
        1. 空文本短路处理
        2. 备份剪贴板
        3. 写入剪贴板
        4. 模拟粘贴
        5. 恢复剪贴板（可选）

        Args:
            text: 要注入的文本

        Returns:
            True=成功（至少写入剪贴板），False=失败
        """
        # 空文本短路处理
        if not text or not text.strip():
            logger.debug("空文本，跳过注入")
            return True

        # 备份剪贴板
        try:
            self._backup = self.read_clipboard()
        except Exception:
            self._backup = None

        # 写入剪贴板
        write_ok = False
        try:
            write_ok = self.write_clipboard(text)
            if not write_ok:
                logger.error("写入剪贴板失败")
                return False
        except Exception as e:
            logger.error("写入剪贴板异常: %s", e)
            return False

        # 模拟粘贴
        paste_ok = False
        try:
            paste_ok = self.simulate_paste()
        except Exception as e:
            logger.error("模拟粘贴异常: %s", e)

        # 恢复剪贴板
        if self.config.restore_clipboard and self._backup is not None:
            try:
                import time
                time.sleep(0.1)  # 等待粘贴完成
                self.write_clipboard(self._backup)
            except Exception:
                logger.debug("恢复剪贴板失败（可忽略）")

        return write_ok

    @abstractmethod
    def write_clipboard(self, text: str) -> bool:
        """将文本写入系统剪贴板"""
        pass

    @abstractmethod
    def simulate_paste(self) -> bool:
        """模拟粘贴操作（Ctrl+V / Cmd+V）"""
        pass

    @abstractmethod
    def read_clipboard(self) -> Optional[str]:
        """读取当前剪贴板内容"""
        pass
