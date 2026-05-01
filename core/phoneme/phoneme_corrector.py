"""音素纠错器 — 编排层。

整合 PhonemeIndex（粗筛）+ fuzzy_substring_search（精确匹配），
实现从文本到纠错后文本的完整流程。

线程安全：构建-替换模式，correct() 无锁读取。
"""

import os
import threading
import logging
from typing import Dict, List, Optional, Tuple

from core.phoneme.phoneme_types import (
    Phoneme,
    MatchResult,
    CorrectionResult,
    text_to_phonemes,
)
from core.phoneme.phoneme_similarity import (
    fuzzy_substring_search,
    adaptive_threshold,
)
from core.phoneme.phoneme_index import PhonemeIndex, Candidate

logger = logging.getLogger(__name__)


class PhonemeCorrector:
    """音素纠错器 — 编排层。

    流程：
    1. 从热词文件加载热词，构建 PhonemeIndex
    2. correct(text)：输入文本 → 粗筛候选 → 精确匹配 → 冲突去重 → 替换

    线程安全：
    - update_from_file() 使用构建-替换模式
    - correct() 无锁读取（Python GIL 保证引用赋值原子性）
    """

    # 长文本保护：超过此长度的音素序列仅处理最后部分
    MAX_PHONEME_LENGTH = 600

    def __init__(self, threshold: float = 0.7, enabled: bool = True):
        """初始化纠错器。

        Args:
            threshold: 基础替换阈值（0.0~1.0）
            enabled: 是否启用
        """
        self.threshold = threshold
        self.enabled = enabled
        self._similar_threshold_delta = 0.2
        self._correction_count = 0
        self._index: Optional[PhonemeIndex] = None
        self._hotwords: Dict[str, List[Phoneme]] = {}
        self._lock = threading.Lock()

    def update_from_file(self, path: str) -> int:
        """从文件加载热词，线程安全。

        采用"构建-替换"模式：
        1. 在当前线程构建全新的 PhonemeIndex 和 hotwords dict
        2. 构建完成后加锁，原子替换引用
        3. correct() 读取时无需加锁

        Args:
            path: 热词文件路径

        Returns:
            成功加载的热词数量

        Raises:
            FileNotFoundError: 文件不存在
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"音素热词文件不存在: {path}")

        # 构建
        new_index = PhonemeIndex()
        new_hotwords: Dict[str, List[Phoneme]] = {}
        count = 0

        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    hotword = line
                    phonemes = text_to_phonemes(hotword)
                    if phonemes:
                        new_index.add(hotword, phonemes)
                        new_hotwords[hotword] = phonemes
                        count += 1
        except UnicodeDecodeError:
            logger.warning("UTF-8 解码失败，尝试 GBK: %s", path)
            with open(path, 'r', encoding='gbk') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    hotword = line
                    phonemes = text_to_phonemes(hotword)
                    if phonemes:
                        new_index.add(hotword, phonemes)
                        new_hotwords[hotword] = phonemes
                        count += 1

        # 原子替换
        with self._lock:
            self._index = new_index
            self._hotwords = new_hotwords

        logger.info("音素热词加载完成: %d 条 (阈值 %.2f)", count, self.threshold)
        return count

    def correct(self, text: str) -> CorrectionResult:
        """执行纠错（线程安全读，可并发调用）。

        流程：
        1. text_to_phonemes(text) → 输入音素序列
        2. 粗筛候选
        3. 精确匹配（窗口 DP）
        4. 冲突去重（同一位置取最高分；同分取长词）
        5. 从后往前替换
        6. 返回 CorrectionResult

        Args:
            text: 原始文本

        Returns:
            纠错结果
        """
        if not self.enabled or not text or not self._index:
            return CorrectionResult(text=text, matches=[], candidates=[])

        # 音素转换
        input_phonemes = text_to_phonemes(text)
        if not input_phonemes:
            return CorrectionResult(text=text, matches=[], candidates=[])

        # 长文本截断保护
        offset = 0
        working_phonemes = input_phonemes
        if len(input_phonemes) > self.MAX_PHONEME_LENGTH:
            working_phonemes = input_phonemes[-self.MAX_PHONEME_LENGTH:]
            offset = len(input_phonemes) - self.MAX_PHONEME_LENGTH

        # 粗筛候选
        candidates = self._index.get_candidates(working_phonemes)
        if not candidates:
            return CorrectionResult(text=text, matches=[], candidates=[])

        # 精确匹配
        all_matches: List[MatchResult] = []
        all_candidates_info: List[Tuple[str, str, float]] = []

        for cand in candidates:
            hw_phonemes = cand.phonemes
            hw_text = cand.hotword
            hw_char_len = len(hw_text)

            # 自适应阈值
            threshold = adaptive_threshold(self.threshold, hw_char_len)
            candidate_threshold = threshold - self._similar_threshold_delta

            # 在锚点位置附近开窗
            window_half = len(hw_phonemes)
            scan_start = max(0, cand.anchor_pos - 2)
            scan_end = min(len(working_phonemes), cand.anchor_pos + window_half + 3)

            window_phonemes = working_phonemes[scan_start:scan_end]

            if not window_phonemes:
                continue

            results = fuzzy_substring_search(window_phonemes, hw_phonemes)

            for score, local_start, local_end in results:
                # 映射回 working_phonemes 的索引
                global_start = scan_start + local_start
                global_end = scan_start + local_end

                # 映射回原文本的字符位置
                if global_start < len(working_phonemes) and global_end <= len(working_phonemes):
                    char_start = working_phonemes[global_start].char_start
                    if global_end - 1 < len(working_phonemes):
                        char_end = working_phonemes[global_end - 1].char_end
                    else:
                        char_end = len(text)
                else:
                    continue

                # 映射偏移
                if offset > 0 and char_start == 0 and offset > 0:
                    # 截断后的第一个音素的 char_start 是相对于截断后的
                    # 需要加上原始文本中截断前的偏移
                    pass

                if score >= threshold:
                    # 替换级别
                    original_text = text[char_start:char_end]
                    all_matches.append(MatchResult(
                        char_start=char_start,
                        char_end=char_end,
                        score=score,
                        hotword=hw_text,
                        original=original_text,
                    ))
                elif score >= candidate_threshold:
                    # 候选提示级别
                    original_text = text[char_start:char_end]
                    all_candidates_info.append((original_text, hw_text, score))

        if not all_matches:
            return CorrectionResult(
                text=text, matches=[], candidates=all_candidates_info
            )

        # 冲突去重：同一位置多个匹配，取最高分；同分取长词
        resolved = self._resolve_conflicts(all_matches)

        # 从后往前替换
        result_text = text
        for match in reversed(resolved):
            result_text = (
                result_text[:match.char_start]
                + match.hotword
                + result_text[match.char_end:]
            )

        self._correction_count += len(resolved)

        return CorrectionResult(
            text=result_text,
            matches=resolved,
            candidates=all_candidates_info,
        )

    @property
    def hotword_count(self) -> int:
        """当前已加载的热词数量。"""
        return len(self._hotwords)

    @property
    def correction_count(self) -> int:
        """累计纠错次数。"""
        return self._correction_count

    @staticmethod
    def _resolve_conflicts(matches: List[MatchResult]) -> List[MatchResult]:
        """冲突去重：同一位置多个匹配，取最高分；同分取长词。

        Args:
            matches: 匹配结果列表

        Returns:
            去重后的匹配列表，按 char_start 排序
        """
        if not matches:
            return []

        # 按 char_start 排序
        matches.sort(key=lambda m: (m.char_start, -m.char_end, -m.score))

        resolved: List[MatchResult] = []
        for match in matches:
            # 检查是否与已选中的重叠
            conflict = False
            for existing in resolved:
                if (match.char_start < existing.char_end and
                        match.char_end > existing.char_start):
                    # 重叠
                    if (match.score > existing.score or
                            (match.score == existing.score and
                             (match.char_end - match.char_start) >
                             (existing.char_end - existing.char_start))):
                        # 新匹配更好，替换旧的
                        resolved.remove(existing)
                        conflict = False
                    else:
                        conflict = True
                    break

            if not conflict:
                resolved.append(match)

        return sorted(resolved, key=lambda m: m.char_start)
