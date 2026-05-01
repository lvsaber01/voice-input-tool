"""音素相似度计算模块。

包含：
- SIMILAR_PHONEMES 预构建字典（O(1) 查找）
- phoneme_cost 音素匹配代价
- lcs_length 最长公共子序列（滚动数组优化）
- fuzzy_substring_search 模糊子串搜索（编辑距离）
- adaptive_threshold 自适应阈值
"""

import math
import logging
from typing import Dict, List, Set, Tuple

from core.phoneme.phoneme_types import Phoneme

logger = logging.getLogger(__name__)


# ─── 相似音素表 ───

SIMILAR_PHONEME_SETS: List[Tuple[str, str]] = [
    # 前后鼻音
    ('an', 'ang'), ('en', 'eng'), ('in', 'ing'), ('ian', 'iang'), ('uan', 'uang'),
    # 平翘舌
    ('z', 'zh'), ('c', 'ch'), ('s', 'sh'),
    # 鼻音/边音
    ('l', 'n'),
    # 唇齿音/声门音
    ('f', 'h'),
    # 常见易混韵母
    ('ai', 'ei'), ('o', 'uo'), ('e', 'ie'),
    # 清浊音/送气不送气
    ('p', 't'), ('p', 'b'), ('t', 'd'), ('k', 'g'),
    # r/l 混淆
    ('r', 'l'),
]

# 预构建：每个音素 → 其所有相似音素的 set（双向）
SIMILAR_PHONEMES: Dict[str, Set[str]] = {}
for _a, _b in SIMILAR_PHONEME_SETS:
    SIMILAR_PHONEMES.setdefault(_a, set()).add(_b)
    SIMILAR_PHONEMES.setdefault(_b, set()).add(_a)


def is_similar_phoneme(p1: str, p2: str) -> bool:
    """判断两个音素值是否相似。

    O(1) 查找预构建字典。

    Args:
        p1: 音素值 1
        p2: 音素值 2

    Returns:
        True 如果 p1 和 p2 是相似音素
    """
    return p2 in SIMILAR_PHONEMES.get(p1, set())


# ─── 音素匹配代价 ───

def phoneme_cost(p1: Phoneme, p2: Phoneme) -> float:
    """计算两个音素的匹配代价。

    0.0 = 完全匹配, 1.0 = 完全不匹配

    规则（按优先级）：
    1. 不同 lang → 1.0
    2. 相同 value → 0.0
    3. 中文相似音素 → 0.5
    4. 声调差异 → 0.5
    5. 英文 LCS 相似度 → 1.0 - lcs/max
    6. 其他 → 1.0
    """
    if p1.lang != p2.lang:
        return 1.0
    if p1.value == p2.value:
        return 0.0
    if p1.lang == 'zh':
        if is_similar_phoneme(p1.value, p2.value):
            return 0.5
        if p1.value.isdigit() and p2.value.isdigit():
            return 0.5
    if p1.lang == 'en':
        max_len = max(len(p1.value), len(p2.value))
        if max_len == 0:
            return 1.0
        lcs_len = lcs_length(p1.value, p2.value)
        return 1.0 - (lcs_len / max_len)
    return 1.0


# ─── LCS 长度 ───

def lcs_length(s1: str, s2: str) -> int:
    """最长公共子序列长度（滚动数组优化，空间 O(min(m,n))）。

    Args:
        s1: 字符串 1
        s2: 字符串 2

    Returns:
        LCS 长度
    """
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    m, n = len(s1), len(s2)
    if n == 0:
        return 0
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev
    return prev[n]


# ─── 模糊子串搜索 ───

def fuzzy_substring_search(
    main_seq: List[Phoneme],
    sub_seq: List[Phoneme],
) -> List[Tuple[float, int, int]]:
    """在主序列中搜索子序列的最佳模糊匹配。

    使用编辑距离，代价函数为 phoneme_cost。
    滚动数组优化空间至 O(n)。

    Args:
        main_seq: 输入文本音素序列（长）
        sub_seq: 热词音素序列（短）

    Returns:
        List[(score, start_idx, end_idx)] — 按分数降序排列
        score = max(0.0, 1.0 - distance / len(sub_seq))
        如果 sub_seq 为空返回空列表
    """
    n = len(sub_seq)
    m = len(main_seq)
    if n == 0 or m == 0:
        return []

    # dp[i][j]: sub_seq[0:i] 与 main_seq[某个子串 ending at j-1] 的最小编辑距离
    # 滚动数组：只保留两行
    prev = [0.0] * (m + 1)
    curr = [0.0] * (m + 1)

    # dp[0][j] 初始化：从任意字边界开始免费
    # dp[0][0] = 0
    for j in range(1, m + 1):
        if main_seq[j - 1].is_word_start:
            prev[j] = 0.0
        else:
            prev[j] = float('inf')

    # 记录每步来源方向用于回溯起始位置
    # source[i][j]: 0=match, 1=delete, 2=insert
    # 为节省内存，仅记录最后一行
    last_source = [[0] * (m + 1) for _ in range(n)]

    for i in range(1, n + 1):
        curr[0] = float(i)  # dp[i][0] = i
        for j in range(1, m + 1):
            match_cost = phoneme_cost(sub_seq[i - 1], main_seq[j - 1])
            d_del = prev[j] + 1.0      # 删除：跳过 sub_seq 的音素
            d_ins = curr[j - 1] + 1.0  # 插入：跳过 main_seq 的音素
            d_match = prev[j - 1] + match_cost  # 匹配/替换

            min_dist = d_match
            source = 0
            if d_del < min_dist:
                min_dist = d_del
                source = 1
            if d_ins < min_dist:
                min_dist = d_ins
                source = 2

            curr[j] = min_dist
            if i <= n:
                last_source[i - 1][j] = source

        prev, curr = curr, prev

    # 收集结果：遍历 dp[n][j]，找最小距离
    # prev 现在是 dp[n] 行
    min_dist = float('inf')
    best_j = -1
    for j in range(1, m + 1):
        if prev[j] < min_dist:
            min_dist = prev[j]
            best_j = j

    if best_j < 0:
        return []

    score = max(0.0, 1.0 - min_dist / n)
    end_idx = best_j

    # 回溯起始位置
    start_idx = end_idx
    i_pos = n - 1
    j_pos = end_idx
    while i_pos >= 0 and j_pos > 0:
        src = last_source[i_pos][j_pos]
        if src == 0:  # match/replace
            start_idx = j_pos - 1
            i_pos -= 1
            j_pos -= 1
        elif src == 1:  # delete (skip sub_seq)
            i_pos -= 1
        else:  # insert (skip main_seq)
            j_pos -= 1

    # 确保起始在字边界上
    while start_idx > 0 and not main_seq[start_idx].is_word_start:
        start_idx -= 1

    return [(score, start_idx, end_idx)]


# ─── 自适应阈值 ───

def adaptive_threshold(base_threshold: float, hotword_len: int) -> float:
    """根据热词长度自适应调整阈值。

    短词需要更高阈值（容忍度低），4 字及以上使用基准阈值。

    Args:
        base_threshold: 基础阈值（如 0.7）
        hotword_len: 热词字数

    Returns:
        调整后的阈值
    """
    if hotword_len < 4:
        adjustment = (1.0 - base_threshold) * (4 - hotword_len) / 4
        return min(1.0, base_threshold + adjustment)
    return base_threshold
