"""音素倒排索引单元测试。

覆盖：
- PhonemeIndex 构建（add）
- PhonemeIndex 检索（get_candidates）
- 相似音素互索引
- 去重
- 边界情况
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.phoneme.phoneme_types import Phoneme, text_to_phonemes
from core.phoneme.phoneme_index import PhonemeIndex, Candidate


class TestPhonemeIndexBuild(unittest.TestCase):
    """索引构建测试。"""

    def test_add_single(self):
        """添加 1 条热词，索引正确"""
        idx = PhonemeIndex()
        phonemes = text_to_phonemes("撒贝宁")
        idx.add("撒贝宁", phonemes)
        self.assertEqual(idx.size, 1)

    def test_add_multiple(self):
        """添加 10 条热词，索引完整"""
        idx = PhonemeIndex()
        words = ["撒贝宁", "东方财富", "科大讯飞", "乐清", "张三丰",
                 "Claude", "PyTorch", "Docker", "GitHub", "HuggingFace"]
        for w in words:
            idx.add(w, text_to_phonemes(w))
        self.assertEqual(idx.size, 10)

    def test_similar_phoneme_indexed(self):
        """'乐清' 同时索引 'l' 和 'n' 桶（l/n 相似）"""
        idx = PhonemeIndex()
        idx.add("乐清", text_to_phonemes("乐清"))
        # l 是 '乐' 的声母（le → l + e），应同时索引到 'n' 桶
        # 验证：输入含 'n' 的音素也能命中
        input_ph = [Phoneme(value='n', lang='zh', is_word_start=True, char_start=0, char_end=1)]
        candidates = idx.get_candidates(input_ph)
        hotwords = [c.hotword for c in candidates]
        self.assertIn("乐清", hotwords)

    def test_en_word_indexed(self):
        """英文单词索引到正确桶"""
        idx = PhonemeIndex()
        idx.add("Claude", text_to_phonemes("Claude"))
        self.assertEqual(idx.size, 1)
        # Claude → phoneme value='claude', 索引 key='claude'
        input_ph = [Phoneme(value='claude', lang='en', is_word_start=True, char_start=0, char_end=6)]
        candidates = idx.get_candidates(input_ph)
        self.assertGreater(len(candidates), 0)

    def test_duplicate_add(self):
        """重复添加同一热词，size 不增加"""
        idx = PhonemeIndex()
        phonemes = text_to_phonemes("测试")
        idx.add("测试", phonemes)
        idx.add("测试", phonemes)
        # size 只记一次（dict key 去重）
        self.assertEqual(idx.size, 1)

    def test_clear_index(self):
        """清空索引后无候选"""
        idx = PhonemeIndex()
        idx.add("测试", text_to_phonemes("测试"))
        idx.clear()
        self.assertEqual(idx.size, 0)
        candidates = idx.get_candidates(text_to_phonemes("测试"))
        self.assertEqual(len(candidates), 0)

    def test_empty_phonemes_skip(self):
        """空音素序列不添加"""
        idx = PhonemeIndex()
        idx.add("空", [])
        self.assertEqual(idx.size, 0)


class TestPhonemeIndexRetrieve(unittest.TestCase):
    """索引检索测试。"""

    def test_get_candidates_exact(self):
        """输入含 'l'，返回 '乐清' 候选"""
        idx = PhonemeIndex()
        idx.add("乐清", text_to_phonemes("乐清"))
        input_ph = text_to_phonemes("乐清市")
        candidates = idx.get_candidates(input_ph)
        hotwords = [c.hotword for c in candidates]
        self.assertIn("乐清", hotwords)

    def test_get_candidates_similar(self):
        """输入含 'n'，也返回 '乐清' 候选（l/n 相似互索引）"""
        idx = PhonemeIndex()
        idx.add("乐清", text_to_phonemes("乐清"))
        # 构造一个含 'n' 的中文音素
        input_ph = [Phoneme(value='n', lang='zh', is_word_start=True, char_start=0, char_end=1)]
        candidates = idx.get_candidates(input_ph)
        hotwords = [c.hotword for c in candidates]
        self.assertIn("乐清", hotwords)

    def test_get_candidates_empty_input(self):
        """空 Phoneme 序列 → 无候选"""
        idx = PhonemeIndex()
        idx.add("测试", text_to_phonemes("测试"))
        candidates = idx.get_candidates([])
        self.assertEqual(len(candidates), 0)

    def test_get_candidates_no_match(self):
        """输入无匹配音素 → 无候选"""
        idx = PhonemeIndex()
        idx.add("测试", text_to_phonemes("测试"))
        # 英文输入不匹配中文热词索引
        input_ph = [Phoneme(value='xyz', lang='en', is_word_start=True, char_start=0, char_end=3)]
        candidates = idx.get_candidates(input_ph)
        # 可能没有匹配（xyz 不在任何中文索引桶中）
        # 注意：相似音素可能产生意外匹配，但 xyz 很安全
        self.assertEqual(len(candidates), 0)

    def test_candidates_with_anchor(self):
        """返回候选 + 锚点位置"""
        idx = PhonemeIndex()
        idx.add("你好", text_to_phonemes("你好"))
        input_ph = text_to_phonemes("世界你好吗")
        candidates = idx.get_candidates(input_ph)
        self.assertGreater(len(candidates), 0)
        for c in candidates:
            self.assertGreaterEqual(c.anchor_pos, 0)

    def test_candidates_deduplicated(self):
        """同一热词被多个 key 命中时，候选去重但锚点合并"""
        idx = PhonemeIndex()
        idx.add("测试", text_to_phonemes("测试"))
        # 输入含重复音素
        input_ph = text_to_phonemes("测试测试")
        candidates = idx.get_candidates(input_ph)
        # 去重：只有一个 "测试" 候选
        hotwords = [c.hotword for c in candidates]
        test_count = hotwords.count("测试")
        self.assertEqual(test_count, 1)


if __name__ == "__main__":
    unittest.main()
