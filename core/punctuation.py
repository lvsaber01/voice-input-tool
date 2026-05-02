"""标点恢复模块 — 基于 FunASR ct-punc 模型。

仅在 SenseVoice 和 Fun-ASR-Nano 模式下启用（这些模型输出无标点）。
Paraformer 已自带标点，无需处理。

设计文档：2026-05-02-postprocess-enhancement-design.md 第五章
"""

import threading
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class PunctuationRestorer:
    """基于 ct-punc 模型的自动标点恢复。

    延迟加载：首次调用时加载模型。
    线程安全：
      - 加载阶段：threading.RLock + double-check locking
      - 推理阶段：同一把 RLock 保护（推理时可能间接触发加载）
    降级友好：模型不可用时返回原文。
    模型复用：优先复用 FunASR 引擎已加载的 ct-punc 实例。
    """

    def __init__(self, enabled: bool = True, device: Optional[str] = None):
        self._lock = threading.RLock()
        self._model = None
        self._loaded = False
        self._enabled = enabled
        self._device = device
        self._shared_model = False

    def set_shared_model(self, model) -> None:
        """注入外部共享的 ct-punc 模型实例。

        Args:
            model: FunASR AutoModel 实例（ct-punc）
        """
        with self._lock:
            self._shared_model = True
            self._model = model
            self._loaded = model is not None
            if model is not None:
                logger.info("PunctuationRestorer: 使用共享 ct-punc 模型实例")

    def restore(self, text: str) -> str:
        """为文本添加标点（线程安全）。

        Args:
            text: 无标点的文本

        Returns:
            添加标点后的文本（模型不可用时返回原文）
        """
        if not self._enabled or not text:
            return text

        with self._lock:
            if not self._loaded:
                self._load_model()
            if not self._loaded:
                return text

            try:
                result = self._model.generate(input=text)
                if result and len(result) > 0:
                    return (
                        result[0]["text"]
                        if isinstance(result[0], dict)
                        else str(result[0])
                    )
            except Exception as e:
                logger.warning("标点恢复失败: %s", e)

        return text

    def _load_model(self) -> None:
        """加载 ct-punc 模型（需在 _lock 内调用）。"""
        try:
            t0 = time.monotonic()
            from funasr import AutoModel

            self._model = AutoModel(model="ct-punc", device=self._device)
            self._loaded = True
            elapsed = time.monotonic() - t0
            logger.info("ct-punc 模型加载完成 (%.1fs)", elapsed)
        except Exception as e:
            logger.warning("ct-punc 模型加载失败，标点恢复不可用: %s", e)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def shutdown(self) -> None:
        """释放模型资源（共享实例不被释放）。"""
        with self._lock:
            if self._model and not self._shared_model:
                self._model = None
            self._loaded = False
            logger.info("PunctuationRestorer: 已关闭")
