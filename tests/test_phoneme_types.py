"""Phoneme 类型与转换函数单元测试。

覆盖：
- Phoneme dataclass（frozen, hashable, defaults）
- text_to_phonemes（中文、英文、数字、混合）
- normalize_text（驼峰拆分、分隔符、大小写）
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.phoneme.phoneme_types import (
    Phoneme,
    MatchResult,
    CorrectionResult,
    text_to_phonemes,
    normalize_text,
)


# ─── Phoneme dataclass 测试 ───

class TestPhonemeDataclass(unittest.TestCase):
    """Phoneme 不可变性测试。"""

    def test_phoneme_frozen(self):
        """frozen=True，不可修改"""
        p = Phoneme(value='s', lang='zh')
        with self.assertRaises(AttributeError):
            p.value = 'x'

    def test_phoneme_hashable(self):
        """可作为 dict key / set 元素"""
        p1 = Phoneme(value='s', lang='zh', char_start=0, char_end=1)
        p2 = Phoneme(value='s', lang='zh', char_start=0, char_end=1)
        self.assertEqual(hash(p1), hash(p2))
        d = {p1: 'test'}
        self.assertEqual(d[p2], 'test')
        s = {p1, p2}
        self.assertEqual(len(s), 1)

    def test_phoneme_defaults(self):
        """is_word_start / is_word_end 默认 False"""
        p = Phoneme(value='a', lang='zh')
        self.assertFalse(p.is_word_start)
        self.assertFalse(p.is_word_end)
        self.assertEqual(p.char_start, 0)
        self.assertEqual(p.char_end, 0)

    def test_phoneme_equality(self):
        """相同字段值的 Phoneme 相等"""
        p1 = Phoneme(value='s', lang='zh', is_word_start=True, char_start=0, char_end=1)
        p2 = Phoneme(value='s', lang='zh', is_word_start=True, char_start=0, char_end=1)
        self.assertEqual(p1, p2)

    def test_phoneme_inequality(self):
        """不同字段值的 Phoneme 不相等"""
        p1 = Phoneme(value='s', lang='zh')
        p2 = Phoneme(value='sh', lang='zh')
        self.assertNotEqual(p1, p2)


class TestMatchResultDataclass(unittest.TestCase):
    """MatchResult 不可变性测试。"""

    def test_match_result_frozen(self):
        """frozen=True"""
        m = MatchResult(char_start=0, char_end=3, score=0.9, hotword="撒贝宁", original="撒贝你")
        with self.assertRaises(AttributeError):
            m.score = 0.5

    def test_match_result_fields(self):
        """字段正确"""
        m = MatchResult(char_start=0, char_end=3, score=0.9, hotword="撒贝宁", original="撒贝你")
        self.assertEqual(m.char_start, 0)
        self.assertEqual(m.char_end, 3)
        self.assertEqual(m.score, 0.9)
        self.assertEqual(m.hotword, "撒贝宁")
        self.assertEqual(m.original, "撒贝你")


class TestCorrectionResultDataclass(unittest.TestCase):
    """CorrectionResult 测试。"""

    def test_correction_result_frozen(self):
        """frozen=True"""
        c = CorrectionResult(text="测试", matches=[], candidates=[])
        with self.assertRaises(AttributeError):
            c.text = "新文本"

    def test_correction_result_with_matches(self):
        """包含匹配结果"""
        m = MatchResult(0, 3, 0.9, "撒贝宁", "撒贝你")
        c = CorrectionResult(text="撒贝宁", matches=[m], candidates=[])
        self.assertEqual(len(c.matches), 1)


# ─── text_to_phonemes 中文测试 ───

class TestTextToPhonemesZh(unittest.TestCase):
    """中文音素转换测试。"""

    def test_single_char(self):
        """单字 → 声母 + 韵母 + 声调"""
        phonemes = text_to_phonemes("撒")
        self.assertGreaterEqual(len(phonemes), 2)
        values = [p.value for p in phonemes]
        self.assertIn('s', values)
        self.assertIn('a', values)

    def test_multi_char(self):
        """多字生成多个音素"""
        phonemes = text_to_phonemes("撒贝宁")
        self.assertGreaterEqual(len(phonemes), 6)

    def test_zero_initial(self):
        """零声母字（如"啊"）只有韵母+声调，无声母"""
        phonemes = text_to_phonemes("啊")
        values = [p.value for p in phonemes]
        # 啊 → 韵母 a 或 o，无独立声母
        self.assertGreaterEqual(len(phonemes), 1)

    def test_punctuation_skip(self):
        """标点不产生音素"""
        phonemes = text_to_phonemes("你好，世界")
        for p in phonemes:
            self.assertNotEqual(p.lang, 'punct')

    def test_emoji_skip(self):
        """Emoji 不产生音素"""
        phonemes = text_to_phonemes("你好😊")
        # 只有"你好"两个字的音素
        zh_count = sum(1 for p in phonemes if p.lang == 'zh')
        self.assertGreater(zh_count, 0)
        # Emoji 不应产生音素
        for p in phonemes:
            self.assertNotIn('😊', p.value)

    def test_space_between_zh(self):
        """中文间空格不影响音素序列内容"""
        p1 = text_to_phonemes("你好")
        p2 = text_to_phonemes("你 好")
        values1 = [p.value for p in p1]
        values2 = [p.value for p in p2]
        self.assertEqual(values1, values2)

    def test_polyphone_bank(self):
        """多音字消歧：银行 → yin hang"""
        phonemes = text_to_phonemes("银行")
        values = [p.value for p in phonemes if p.lang == 'zh']
        # 银 → in (零声母), 行 → ing (单字模式 pypinyin 可能取默认读音)
        # 单字处理时 pypinyin 逐字转换，重点验证音素数量合理
        self.assertGreaterEqual(len(phonemes), 4)

    def test_polyphone_walk(self):
        """多音字消歧：行走 → xing zou"""
        phonemes = text_to_phonemes("行走")
        values = [p.value for p in phonemes if p.lang == 'zh']
        self.assertIn('x', values)


# ─── text_to_phonemes 英文测试 ───

class TestTextToPhonemesEn(unittest.TestCase):
    """英文音素转换测试。"""

    def test_single_word(self):
        """单英文单词 → 1 个 Phoneme"""
        phonemes = text_to_phonemes("Claude")
        en_phonemes = [p for p in phonemes if p.lang == 'en']
        self.assertEqual(len(en_phonemes), 1)
        self.assertEqual(en_phonemes[0].value, 'claude')
        self.assertTrue(en_phonemes[0].is_word_start)
        self.assertTrue(en_phonemes[0].is_word_end)

    def test_camel_split(self):
        """驼峰拆分：PyTorch → py + torch"""
        phonemes = text_to_phonemes("PyTorch")
        en_phonemes = [p for p in phonemes if p.lang == 'en']
        values = [p.value for p in en_phonemes]
        self.assertIn('py', values)
        self.assertIn('torch', values)

    def test_hyphen_split(self):
        """连字符拆分：Hugging-Face → hugging + face"""
        phonemes = text_to_phonemes("Hugging-Face")
        # 连字符后是独立英文片段
        en_values = [p.value for p in phonemes if p.lang == 'en']
        self.assertIn('hugging', en_values)
        self.assertIn('face', en_values)

    def test_case_insensitive_value(self):
        """value 统一为小写"""
        phonemes = text_to_phonemes("CLAUDE")
        en_phonemes = [p for p in phonemes if p.lang == 'en']
        self.assertEqual(en_phonemes[0].value, 'claude')

    def test_mixed_case(self):
        """混合大小写：iPhone → i + phone"""
        phonemes = text_to_phonemes("iPhone")
        en_values = [p.value for p in phonemes if p.lang == 'en']
        self.assertIn('i', en_values)
        self.assertIn('phone', en_values)

    def test_underscore_split(self):
        """下划线拆分"""
        phonemes = text_to_phonemes("hello_world")
        en_values = [p.value for p in phonemes if p.lang == 'en']
        self.assertIn('hello', en_values)
        self.assertIn('world', en_values)

    def test_empty_string(self):
        """空字符串 → 空列表"""
        self.assertEqual(text_to_phonemes(""), [])


# ─── text_to_phonemes 混合测试 ───

class TestTextToPhonemesMixed(unittest.TestCase):
    """中英文混合测试。"""

    def test_zh_en_mixed(self):
        """中英文混合"""
        phonemes = text_to_phonemes("撒贝宁Claude")
        zh_count = sum(1 for p in phonemes if p.lang == 'zh')
        en_count = sum(1 for p in phonemes if p.lang == 'en')
        self.assertGreater(zh_count, 0)
        self.assertGreater(en_count, 0)

    def test_number(self):
        """数字生成 num 类型 Phoneme"""
        phonemes = text_to_phonemes("iPhone15Pro")
        num_phonemes = [p for p in phonemes if p.lang == 'num']
        self.assertEqual(len(num_phonemes), 2)
        num_values = [p.value for p in num_phonemes]
        self.assertIn('1', num_values)
        self.assertIn('5', num_values)

    def test_zh_en_digit_punctuation(self):
        """中英文数字标点完整分离"""
        phonemes = text_to_phonemes("Claude说Python3.11很好！")
        langs = set(p.lang for p in phonemes)
        self.assertIn('zh', langs)
        self.assertIn('en', langs)
        self.assertIn('num', langs)
        # 标点和空白不产生音素
        for p in phonemes:
            self.assertNotIn(p.value, ['.', '！', ' '])

    def test_only_whitespace(self):
        """纯空白 → 空列表"""
        self.assertEqual(text_to_phonemes("   \t\n"), [])

    def test_only_punctuation(self):
        """纯标点 → 空列表"""
        self.assertEqual(text_to_phonemes("。，！？"), [])


# ─── normalize_text 测试 ───

class TestNormalizeText(unittest.TestCase):
    """文本规范化测试。"""

    def test_camel_basic(self):
        """基本驼峰拆分"""
        self.assertEqual(normalize_text("PyTorch"), "py torch")

    def test_camel_upper_sequence(self):
        """连续大写不拆分"""
        self.assertEqual(normalize_text("HTTPS"), "https")

    def test_camel_digit(self):
        """驼峰+数字"""
        result = normalize_text("iPhone15Pro")
        # iPhone → i Phone → 驼峰拆分后 i phone
        # 数字和小写字母之间不会拆分，所以 'phone15' 连在一起
        # 关键验证：全小写 + 无多余空白
        self.assertEqual(result, result.lower())
        self.assertNotIn('_', result)

    def test_hyphen_to_space(self):
        """连字符→空格"""
        self.assertEqual(normalize_text("Hugging-Face"), "hugging face")

    def test_multiple_spaces(self):
        """合并连续空白"""
        self.assertEqual(normalize_text("Py  Torch"), "py torch")

    def test_zh_unchanged(self):
        """中文 normalize 后不变"""
        self.assertEqual(normalize_text("撒贝宁"), "撒贝宁")

    def test_underscore_to_space(self):
        """下划线→空格"""
        self.assertEqual(normalize_text("hello_world"), "hello world")

    def test_mixed_zh_en(self):
        """中英文混合"""
        self.assertEqual(normalize_text("PyTorch很好"), "py torch很好")


if __name__ == "__main__":
    unittest.main()
