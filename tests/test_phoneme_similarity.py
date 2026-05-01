"""音素相似度单元测试。

覆盖：
- SIMILAR_PHONEMES 预构建字典
- is_similar_phoneme
- phoneme_cost
- lcs_length
- fuzzy_substring_search
- adaptive_threshold
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.phoneme.phoneme_types import Phoneme, text_to_phonemes
from core.phoneme.phoneme_similarity import (
    SIMILAR_PHONEMES,
    SIMILAR_PHONEME_SETS,
    is_similar_phoneme,
    phoneme_cost,
    lcs_length,
    fuzzy_substring_search,
    adaptive_threshold,
)


# ─── SIMILAR_PHONEMES 字典测试 ───

class TestSimilarPhonemesDict(unittest.TestCase):
    """相似音素预构建字典测试。"""

    def test_build_dict(self):
        """预构建字典包含所有 16 组音素对"""
        self.assertGreaterEqual(len(SIMILAR_PHONEME_SETS), 16)
        # 验证字典非空
        self.assertGreater(len(SIMILAR_PHONEMES), 0)

    def test_symmetric_lookup(self):
        """对称查找：is_similar('l','n') == is_similar('n','l')"""
        self.assertEqual(is_similar_phoneme('l', 'n'), is_similar_phoneme('n', 'l'))

    def test_non_similar(self):
        """不相似的音素返回 False"""
        self.assertFalse(is_similar_phoneme('a', 'b'))
        self.assertFalse(is_similar_phoneme('x', 'q'))

    def test_all_pairs_exhaustive(self):
        """遍历所有 16+ 组，双向验证"""
        for a, b in SIMILAR_PHONEME_SETS:
            self.assertTrue(is_similar_phoneme(a, b),
                           f"Expected {a}~{b} to be similar")
            self.assertTrue(is_similar_phoneme(b, a),
                           f"Expected {b}~{a} to be similar")

    def test_dict_no_false_positive(self):
        """随机抽样非相似音素对，确认返回 False"""
        non_similar_pairs = [
            ('a', 'b'), ('m', 'x'), ('q', 'zh'), ('ou', 'ai'),
            ('d', 'sh'), ('b', 'ch'), ('u', 'i'), ('ang', 'ou'),
            ('ei', 'ong'), ('p', 'r'), ('s', 'l'), ('z', 'c'),
        ]
        for a, b in non_similar_pairs:
            self.assertFalse(is_similar_phoneme(a, b),
                            f"Unexpected similarity: {a}~{b}")

    def test_an_ang_similar(self):
        """前后鼻音 an/ang"""
        self.assertTrue(is_similar_phoneme('an', 'ang'))

    def test_z_zh_similar(self):
        """平翘舌 z/zh"""
        self.assertTrue(is_similar_phoneme('z', 'zh'))

    def test_l_n_similar(self):
        """鼻音/边音 l/n"""
        self.assertTrue(is_similar_phoneme('l', 'n'))

    def test_self_not_similar(self):
        """自身不在相似集合中（除非有其他组定义）"""
        # 'm' 不在 SIMILAR_PHONEMES 中，自身查找应返回 False
        self.assertFalse(is_similar_phoneme('m', 'm'))


# ─── phoneme_cost 测试 ───

class TestPhonemeCost(unittest.TestCase):
    """音素匹配代价测试。"""

    def _zh(self, value, **kw):
        defaults = {'lang': 'zh', 'char_start': 0, 'char_end': 1}
        defaults.update(kw)
        return Phoneme(value=value, **defaults)

    def _en(self, value, **kw):
        defaults = {'lang': 'en', 'char_start': 0, 'char_end': len(value)}
        defaults.update(kw)
        return Phoneme(value=value, **defaults)

    def test_same_lang_same_value(self):
        """相同音素 → cost = 0.0"""
        p = self._zh('s')
        self.assertEqual(phoneme_cost(p, p), 0.0)

    def test_different_lang(self):
        """不同 lang → cost = 1.0"""
        p1 = self._zh('s')
        p2 = self._en('s')
        self.assertEqual(phoneme_cost(p1, p2), 1.0)

    def test_num_vs_zh(self):
        """num vs zh → cost = 1.0"""
        p1 = Phoneme(value='1', lang='num', char_start=0, char_end=1)
        p2 = self._zh('s')
        self.assertEqual(phoneme_cost(p1, p2), 1.0)

    def test_similar_initials(self):
        """平翘舌 z vs zh → cost = 0.5"""
        p1 = self._zh('z')
        p2 = self._zh('zh')
        self.assertEqual(phoneme_cost(p1, p2), 0.5)

    def test_similar_finals(self):
        """前后鼻音 an vs ang → cost = 0.5"""
        p1 = self._zh('an')
        p2 = self._zh('ang')
        self.assertEqual(phoneme_cost(p1, p2), 0.5)

    def test_similar_rl(self):
        """r vs l → cost = 0.5"""
        p1 = self._zh('r')
        p2 = self._zh('l')
        self.assertEqual(phoneme_cost(p1, p2), 0.5)

    def test_tone_difference(self):
        """声调差异 → cost = 0.5"""
        p1 = self._zh('1')
        p2 = self._zh('3')
        self.assertEqual(phoneme_cost(p1, p2), 0.5)

    def test_en_lcs_exact(self):
        """英文完全匹配 → cost = 0.0"""
        p1 = self._en('claude')
        p2 = self._en('claude')
        self.assertEqual(phoneme_cost(p1, p2), 0.0)

    def test_en_lcs_partial(self):
        """英文部分匹配 → cost 基于 LCS"""
        p1 = self._en('claude')
        p2 = self._en('cloud')
        # LCS("claude", "cloud") = 4 (c,l,u,d), max_len=6
        # cost = 1 - 4/6 = 1/3 ≈ 0.333
        cost = phoneme_cost(p1, p2)
        self.assertAlmostEqual(cost, 1.0 - 4.0 / 6.0, places=2)

    def test_en_lcs_zero(self):
        """英文完全不匹配 → cost = 1.0"""
        p1 = self._en('abc')
        p2 = self._en('xyz')
        self.assertEqual(phoneme_cost(p1, p2), 1.0)

    def test_en_empty_string(self):
        """空字符串 vs 非空 → cost = 1.0"""
        p1 = self._en('')
        p2 = self._en('abc')
        self.assertEqual(phoneme_cost(p1, p2), 1.0)

    def test_zh_non_similar(self):
        """中文不相似音素 → cost = 1.0"""
        p1 = self._zh('a')
        p2 = self._zh('k')
        self.assertEqual(phoneme_cost(p1, p2), 1.0)

    def test_num_different(self):
        """数字不同 → 不同 lang（num vs num 不走 zh 分支）"""
        p1 = Phoneme(value='1', lang='num', char_start=0, char_end=1)
        p2 = Phoneme(value='2', lang='num', char_start=0, char_end=1)
        # 同 lang，value 不同，不是 zh 也不是 en → 1.0
        self.assertEqual(phoneme_cost(p1, p2), 1.0)


# ─── lcs_length 测试 ───

class TestLcsLength(unittest.TestCase):
    """最长公共子序列测试。"""

    def test_identical(self):
        """完全相同"""
        self.assertEqual(lcs_length("abc", "abc"), 3)

    def test_subset(self):
        """子集关系"""
        self.assertEqual(lcs_length("abc", "ac"), 2)

    def test_disjoint(self):
        """完全不同"""
        self.assertEqual(lcs_length("abc", "xyz"), 0)

    def test_case_sensitive(self):
        """大小写敏感（未转小写）"""
        self.assertEqual(lcs_length("AbC", "abc"), 1)  # 只有 'b' 匹配

    def test_empty_strings(self):
        """空字符串"""
        self.assertEqual(lcs_length("", ""), 0)
        self.assertEqual(lcs_length("a", ""), 0)
        self.assertEqual(lcs_length("", "a"), 0)

    def test_long_strings(self):
        """长字符串不栈溢出"""
        self.assertEqual(lcs_length("a" * 100, "a" * 100), 100)

    def test_one_char_diff(self):
        """一个字符不同"""
        self.assertEqual(lcs_length("abcd", "abxd"), 3)

    def test_reverse(self):
        """反转字符串"""
        self.assertEqual(lcs_length("abc", "cba"), 1)  # 'b'


# ─── fuzzy_substring_search 测试 ───

class TestFuzzySubstringSearch(unittest.TestCase):
    """模糊子串搜索测试。"""

    def test_exact_match(self):
        """完全匹配 → score = 1.0"""
        main = text_to_phonemes("撒贝宁")
        sub = text_to_phonemes("撒贝宁")
        results = fuzzy_substring_search(main, sub)
        self.assertGreater(len(results), 0)
        self.assertGreaterEqual(results[0][0], 0.9)

    def test_near_match(self):
        """近音匹配（如 撒贝你 vs 撒贝宁）"""
        main = text_to_phonemes("撒贝你")
        sub = text_to_phonemes("撒贝宁")
        results = fuzzy_substring_search(main, sub)
        self.assertGreater(len(results), 0)
        # "你"和"宁"音素可能部分相似
        self.assertGreater(results[0][0], 0.3)

    def test_no_match(self):
        """完全不匹配"""
        main = text_to_phonemes("完全不同的文本")
        sub = text_to_phonemes("Python")
        results = fuzzy_substring_search(main, sub)
        # 不同语言，应该低分或无结果
        if results:
            self.assertLess(results[0][0], 0.5)

    def test_score_non_negative(self):
        """分数永远 >= 0"""
        main = text_to_phonemes("测试文本")
        sub = text_to_phonemes("另一个词")
        results = fuzzy_substring_search(main, sub)
        for score, _, _ in results:
            self.assertGreaterEqual(score, 0.0)

    def test_empty_sub_seq(self):
        """空 sub_seq → 空结果"""
        main = text_to_phonemes("测试")
        results = fuzzy_substring_search(main, [])
        self.assertEqual(len(results), 0)

    def test_empty_main_seq(self):
        """空 main_seq → 空结果"""
        sub = text_to_phonemes("测试")
        results = fuzzy_substring_search([], sub)
        self.assertEqual(len(results), 0)

    def test_start_end_boundary(self):
        """匹配结果中索引合理"""
        main = text_to_phonemes("你好撒贝宁再见")
        sub = text_to_phonemes("撒贝宁")
        results = fuzzy_substring_search(main, sub)
        if results:
            _, start, end = results[0]
            self.assertGreaterEqual(start, 0)
            self.assertLessEqual(end, len(main))

    def test_embedded_match(self):
        """嵌入在长文本中的匹配"""
        main = text_to_phonemes("今天东方菜富发布了财报")
        sub = text_to_phonemes("东方财富")
        results = fuzzy_substring_search(main, sub)
        self.assertGreater(len(results), 0)


# ─── adaptive_threshold 测试 ───

class TestAdaptiveThreshold(unittest.TestCase):
    """自适应阈值测试。"""

    def test_2_char(self):
        """2 字 → threshold ≈ 0.85 (base=0.7)"""
        t = adaptive_threshold(0.7, 2)
        self.assertAlmostEqual(t, 0.85, places=2)

    def test_3_char(self):
        """3 字 → threshold ≈ 0.775"""
        t = adaptive_threshold(0.7, 3)
        self.assertAlmostEqual(t, 0.775, places=2)

    def test_4_char(self):
        """4 字 → threshold = 0.70"""
        t = adaptive_threshold(0.7, 4)
        self.assertEqual(t, 0.7)

    def test_5_plus_char(self):
        """5+ 字 → threshold = 0.70"""
        self.assertEqual(adaptive_threshold(0.7, 5), 0.7)
        self.assertEqual(adaptive_threshold(0.7, 10), 0.7)
        self.assertEqual(adaptive_threshold(0.7, 100), 0.7)

    def test_base_threshold_varies(self):
        """不同 base 阈值"""
        t = adaptive_threshold(0.6, 2)
        # 0.6 + 0.4 * 2/4 = 0.8
        self.assertAlmostEqual(t, 0.8, places=2)

    def test_threshold_clamped(self):
        """任何输入阈值不超过 1.0"""
        t = adaptive_threshold(0.95, 1)
        self.assertLessEqual(t, 1.0)

    def test_1_char(self):
        """1 字 → 最高阈值"""
        t = adaptive_threshold(0.7, 1)
        expected = 0.7 + 0.3 * 3 / 4  # = 0.925
        self.assertAlmostEqual(t, expected, places=2)

    def test_base_zero(self):
        """base=0 时短词仍然有阈值"""
        t = adaptive_threshold(0.0, 2)
        self.assertGreater(t, 0.0)


if __name__ == "__main__":
    unittest.main()
