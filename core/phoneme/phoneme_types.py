"""音素核心数据结构与转换函数。

定义 Phoneme、MatchResult、CorrectionResult 数据结构，
以及 text_to_phonemes / normalize_text 转换函数。

依赖 pypinyin 进行中文拼音转换（funasr 依赖链已包含）。
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Tuple

import pypinyin
from pypinyin import pinyin as _pinyin, Style

# ─── 日志 ───
import logging
logger = logging.getLogger(__name__)


# ─── 数据结构 ───

@dataclass(frozen=True)
class Phoneme:
    """带语言属性的音素，不可变（hashable，可用作 dict key）。

    Attributes:
        value: 音素值（中文：声母/韵母/声调，英文：整词小写，数字：字符）
        lang: 语言类型：'zh'=中文, 'en'=英文, 'num'=数字
        is_word_start: 是否是字/词边界起始
        is_word_end: 是否是字/词边界结束
        char_start: 原文本中的起始字符索引（inclusive）
        char_end: 原文本中的结束字符索引（exclusive）
    """
    value: str
    lang: str
    is_word_start: bool = False
    is_word_end: bool = False
    char_start: int = 0
    char_end: int = 0


@dataclass(frozen=True)
class MatchResult:
    """单次匹配结果。

    Attributes:
        char_start: 原文本中的匹配起始位置
        char_end: 原文本中的匹配结束位置（exclusive）
        score: 相似度分数（0.0 ~ 1.0）
        hotword: 匹配到的热词原文
        original: 被替换的原文片段
    """
    char_start: int
    char_end: int
    score: float
    hotword: str
    original: str


@dataclass(frozen=True)
class CorrectionResult:
    """纠错完整结果。

    Attributes:
        text: 纠错后的文本
        matches: 实际执行的替换列表
        candidates: 相似但未替换的候选项 (原词, 热词, 分数)
    """
    text: str
    matches: List[MatchResult]
    candidates: List[Tuple[str, str, float]]


# ─── 文本规范化 ───

# 驼峰拆分正则：大写字母前插入空格
_CAMEL_SPLIT_RE_1 = re.compile(r'([a-z0-9])([A-Z])')
_CAMEL_SPLIT_RE_2 = re.compile(r'([A-Z]+)([A-Z][a-z])')

# 判断字符是否为中文（CJK 统一汉字）
_ZH_CHAR_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')

# 判断是否为英文字母
_EN_CHAR_RE = re.compile(r'[a-zA-Z]')

# 判断是否为数字
_NUM_CHAR_RE = re.compile(r'[0-9]')


def normalize_text(text: str) -> str:
    """英文驼峰拆分 + 分隔符统一为空格 + 全小写。

    仅处理英文字符的规范化，中文部分不参与。

    Examples:
        >>> normalize_text("PyTorch")
        'py torch'
        >>> normalize_text("iPhone15Pro")
        'iphone 15 pro'
        >>> normalize_text("Hugging-Face")
        'hugging face'
        >>> normalize_text("撒贝宁")
        '撒贝宁'
    """
    result = _CAMEL_SPLIT_RE_1.sub(r'\1 \2', text)
    result = _CAMEL_SPLIT_RE_2.sub(r'\1 \2', result)
    result = result.replace('-', ' ').replace('_', ' ')
    result = ' '.join(result.split()).lower()
    return result


# ─── 文本→音素转换 ───

def text_to_phonemes(text: str) -> List[Phoneme]:
    """将文本转为音素序列。

    中文: pypinyin (声母 + 韵母 + 声调) — 每字 2~3 个 Phoneme
    英文: 驼峰拆分后逐单词存储（lang='en'）— 每词 1 个 Phoneme，value 为全小写
    数字: 逐字符（lang='num'）
    其他: 跳过（标点、空白、Emoji等不产生音素）

    Args:
        text: 原始文本

    Returns:
        音素列表，每个 Phoneme 标记了在原文本中的字符位置
    """
    if not text:
        return []

    phonemes: List[Phoneme] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        # 中文：逐字处理
        if _is_chinese(ch):
            _process_chinese_char(text, i, phonemes)
            i += 1

        # 英文：收集连续英文，按单词拆分
        elif _EN_CHAR_RE.match(ch):
            word_start = i
            while i < n and _EN_CHAR_RE.match(text[i]):
                i += 1
            word_end = i
            word = text[word_start:word_end]
            # 驼峰拆分后生成 Phoneme
            _process_english_word(word, word_start, phonemes)

        # 数字：逐字符
        elif _NUM_CHAR_RE.match(ch):
            phonemes.append(Phoneme(
                value=ch,
                lang='num',
                is_word_start=True,
                is_word_end=True,
                char_start=i,
                char_end=i + 1,
            ))
            i += 1

        # 其他（标点、空白、Emoji 等）：跳过
        else:
            i += 1

    return phonemes


def _is_chinese(ch: str) -> bool:
    """判断字符是否为中文字符。"""
    return bool(_ZH_CHAR_RE.match(ch))


def _process_chinese_char(text: str, pos: int, phonemes: List[Phoneme]) -> None:
    """处理单个中文字符，生成 2~3 个 Phoneme（声母+韵母+声调）。

    pypinyin 提供词组消歧能力，但此处逐字处理。
    对于多音字取 pypinyin 默认读音。
    """
    ch = text[pos]

    try:
        # 获取声母
        initials = _pinyin(ch, style=Style.INITIALS, errors='ignore')
        # 获取韵母
        finals = _pinyin(ch, style=Style.FINALS, errors='ignore')
        # 获取声调（数字表示）
        tones = _pinyin(ch, style=Style.TONE3, errors='ignore')
    except Exception:
        # pypinyin 异常，降级为单字 Phoneme
        phonemes.append(Phoneme(
            value=ch,
            lang='zh',
            is_word_start=True,
            is_word_end=True,
            char_start=pos,
            char_end=pos + 1,
        ))
        return

    if not initials and not finals:
        # pypinyin 无法处理，降级为单字 Phoneme
        phonemes.append(Phoneme(
            value=ch,
            lang='zh',
            is_word_start=True,
            is_word_end=True,
            char_start=pos,
            char_end=pos + 1,
        ))
        return

    initial = initials[0][0] if initials and initials[0] else ''
    final = finals[0][0] if finals and finals[0] else ''
    tone_raw = tones[0][0] if tones and tones[0] else ''

    # 提取声调数字
    tone_digit = ''
    if tone_raw:
        for c in reversed(tone_raw):
            if c.isdigit():
                tone_digit = c
                break

    # 声母 Phoneme
    if initial:
        phonemes.append(Phoneme(
            value=initial,
            lang='zh',
            is_word_start=True,
            is_word_end=False,
            char_start=pos,
            char_end=pos + 1,
        ))

    # 韵母 Phoneme（如果没有声母，即零声母，韵母标记 is_word_start=True）
    if final:
        has_initial = bool(initial)
        phonemes.append(Phoneme(
            value=final,
            lang='zh',
            is_word_start=not has_initial,
            is_word_end=False,
            char_start=pos,
            char_end=pos + 1,
        ))

    # 声调 Phoneme
    if tone_digit:
        phonemes.append(Phoneme(
            value=tone_digit,
            lang='zh',
            is_word_start=False,
            is_word_end=True,
            char_start=pos,
            char_end=pos + 1,
        ))


def _process_english_word(word: str, word_start: int, phonemes: List[Phoneme]) -> None:
    """处理英文单词/片段，可能拆分为多个 Phoneme（驼峰拆分）。

    每个子单词作为一个 Phoneme，value 为小写。
    """
    # 驼峰拆分
    sub_words = _split_camel_case(word)

    offset = 0
    for sub in sub_words:
        # 在原 word 中找到 sub 的位置
        sub_pos = word.lower().find(sub.lower(), offset)
        if sub_pos == -1:
            sub_pos = offset
        actual_start = word_start + sub_pos
        actual_end = actual_start + len(sub)

        phonemes.append(Phoneme(
            value=sub.lower(),
            lang='en',
            is_word_start=True,
            is_word_end=True,
            char_start=actual_start,
            char_end=actual_end,
        ))
        offset = sub_pos + len(sub)


def _split_camel_case(word: str) -> List[str]:
    """驼峰拆分：将连续大写字母与紧跟的小写字母组分离。

    Examples:
        >>> _split_camel_case("PyTorch")
        ['Py', 'Torch']
        >>> _split_camel_case("iPhone")
        ['i', 'Phone']
        >>> _split_camel_case("CLAUDE")
        ['CLAUDE']
        >>> _split_camel_case("iPhone15Pro")
        ['i', 'Phone', '15', 'Pro']
    """
    # 先分离数字
    parts = re.findall(r'[a-zA-Z]+|\d+', word)
    result = []
    for part in parts:
        if part.isdigit():
            continue  # 数字会在外层单独处理
        # 驼峰拆分
        sub = _CAMEL_SPLIT_RE_1.sub(r'\1 \2', part)
        sub = _CAMEL_SPLIT_RE_2.sub(r'\1 \2', sub)
        result.extend(sub.split())
    return result
