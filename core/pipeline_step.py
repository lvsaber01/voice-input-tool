"""Pipeline 插件化基础设施 — PipelineStep / ProcessContext / ContextualStep / StepNames。

为 TextPipeline 提供可插拔的步骤抽象，支持优先级排序、动态增删、COW 读取。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class StepNames:
    """内置步骤名称常量。"""
    PUNCTUATION = "punctuation"
    PHONEME = "phoneme"
    REGEX = "regex"
    HOTWORD = "hotword"
    ITN = "itn"


@dataclass
class ProcessContext:
    """管线处理上下文，步骤间传递数据。

    Attributes:
        text: 当前处理中的文本
        meta: 元数据字典（步骤间共享数据）
    """
    text: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> None:
        """存入元数据。"""
        self.meta[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """读取元数据。"""
        return self.meta.get(key, default)


class PipelineStep(ABC):
    """Pipeline 步骤抽象基类。

    Attributes:
        name: 步骤唯一标识（建议使用 StepNames 常量）
        priority: 执行优先级（数字越小越先执行）
        enabled: 是否启用
    """

    def __init__(self, name: str, priority: int, enabled: bool = True):
        self._name = name
        self._priority = priority
        self._enabled = enabled

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    @abstractmethod
    def process(self, text: str) -> str:
        """处理文本，返回处理后的文本。

        Args:
            text: 输入文本
        Returns:
            处理后的文本
        """
        ...

    def reload(self) -> None:
        """重新加载配置/数据。默认空实现。"""
        pass


class ContextualStep(PipelineStep):
    """带上下文的步骤中间类。

    process_context() 提供 ProcessContext 参数，子类可重写它来读写上下文。
    默认 process_context() 调用 process() 并返回修改后的文本。
    默认 process() 返回原文（不修改）。
    """

    def process(self, text: str) -> str:
        """默认不修改文本。"""
        return text

    def process_context(self, ctx: ProcessContext) -> str:
        """处理上下文，默认调用 process()。

        Args:
            ctx: 处理上下文
        Returns:
            处理后的文本
        """
        return self.process(ctx.text)
