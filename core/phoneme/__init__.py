"""音素匹配模块 — 公开接口导出。

包含 pypinyin 可用性检测，不可用时优雅降级。
"""

import logging

logger = logging.getLogger(__name__)

# pypinyin 可用性检测
_PYPINYIN_AVAILABLE = True
try:
    import pypinyin  # noqa: F401
except ImportError:
    _PYPINYIN_AVAILABLE = False
    logger.warning("pypinyin 未安装，音素纠错功能已禁用")


# ─── 公开接口 ───

from core.phoneme.phoneme_types import (
    Phoneme,
    MatchResult,
    CorrectionResult,
    text_to_phonemes,
    normalize_text,
)

# 延迟导入，避免循环依赖
def _get_corrector_class():
    from core.phoneme.phoneme_corrector import PhonemeCorrector
    return PhonemeCorrector

# 方便使用
def create_corrector(threshold: float = 0.7, enabled: bool = True):
    """创建 PhonemeCorrector 实例。

    如果 pypinyin 不可用，返回 None。
    """
    if not _PYPINYIN_AVAILABLE:
        return None
    cls = _get_corrector_class()
    return cls(threshold=threshold, enabled=enabled)


__all__ = [
    'Phoneme',
    'MatchResult',
    'CorrectionResult',
    'Phoneme',
    'text_to_phonemes',
    'normalize_text',
    'create_corrector',
    '_PYPINYIN_AVAILABLE',
]
