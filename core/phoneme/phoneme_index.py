"""音素倒排索引模块。

按热词前 2 个音素的 value 分桶，检索时只匹配音素在输入中出现过的热词。
同时将相似音素纳入索引（如 r/l 互索引、an/ang 互索引）。
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from core.phoneme.phoneme_types import Phoneme
from core.phoneme.phoneme_similarity import SIMILAR_PHONEMES

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    """倒排索引返回的候选热词。

    Attributes:
        hotword: 热词原文
        phonemes: 热词的音素序列
        anchor_pos: 输入中匹配到的音素索引（锚点）
    """
    hotword: str
    phonemes: List[Phoneme]
    anchor_pos: int


class PhonemeIndex:
    """倒排索引：按热词前 N 个音素的 value 分桶。

    索引策略：
    - 中文：取前 2 个音素（通常是第一字的声母+韵母）
    - 英文：取前 2 个音素（容错首音素识别错误）
    - 同时将相似音素纳入索引
    """

    def __init__(self):
        self._index: Dict[str, List[Tuple[str, List[Phoneme]]]] = defaultdict(list)
        self._hotwords: Dict[str, List[Phoneme]] = {}  # {热词原文: 音素序列}

    def add(self, hotword: str, phonemes: List[Phoneme]) -> None:
        """添加热词到索引。

        取前 min(2, len) 个音素 value 作为索引 key，
        同时索引相似音素。

        Args:
            hotword: 热词原文
            phonemes: 热词的音素序列
        """
        if not phonemes:
            return

        self._hotwords[hotword] = phonemes

        for i in range(min(2, len(phonemes))):
            key = phonemes[i].value
            self._index[key].append((hotword, phonemes))
            # 相似音素互索引
            for sim in SIMILAR_PHONEMES.get(key, set()):
                self._index[sim].append((hotword, phonemes))

    def get_candidates(self, input_phonemes: List[Phoneme]) -> List[Candidate]:
        """返回候选热词 + 锚点位置（输入中匹配到的音素索引）。

        去重：同一热词可能被多个索引 key 命中，合并其锚点位置。

        Args:
            input_phonemes: 输入文本的音素序列

        Returns:
            去重后的候选列表
        """
        if not input_phonemes:
            return []

        # 收集所有命中的候选，key=hotword, value=(phonemes, set of anchor positions)
        seen: Dict[str, Tuple[List[Phoneme], Set[int]]] = {}

        for anchor_pos, ph in enumerate(input_phonemes):
            key = ph.value
            for hotword, hw_phonemes in self._index.get(key, []):
                if hotword not in seen:
                    seen[hotword] = (hw_phonemes, set())
                seen[hotword][1].add(anchor_pos)

        # 也查相似音素
        for anchor_pos, ph in enumerate(input_phonemes):
            for sim in SIMILAR_PHONEMES.get(ph.value, set()):
                for hotword, hw_phonemes in self._index.get(sim, []):
                    if hotword not in seen:
                        seen[hotword] = (hw_phonemes, set())
                    seen[hotword][1].add(anchor_pos)

        # 构建候选列表（每个热词只保留最小锚点）
        candidates = []
        for hotword, (phonemes, anchors) in seen.items():
            min_anchor = min(anchors)
            candidates.append(Candidate(
                hotword=hotword,
                phonemes=phonemes,
                anchor_pos=min_anchor,
            ))

        return candidates

    @property
    def size(self) -> int:
        """已索引的热词数量。"""
        return len(self._hotwords)

    def clear(self) -> None:
        """清空索引。"""
        self._index.clear()
        self._hotwords.clear()
