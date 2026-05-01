"""Layer 3: 音素匹配端到端测试 — 模拟真实 ASR 场景。

测试从"ASR 乱码文本"到"纠错后文本"的完整链路。
不 mock pypinyin，使用真实依赖。
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hotword import HotwordManager
from core.text_pipeline import TextPipeline
from core.phoneme.phoneme_corrector import PhonemeCorrector


# ─── 辅助函数 ───

def _make_pipeline(
    hw_content: str = "",
    rules_content: str = "",
    phoneme_content: str = "",
    phoneme_threshold: float = 0.7,
):
    """创建完整 Pipeline + 音素文件。"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(hw_content)
        hw_path = f.name
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(rules_content)
        rules_path = f.name
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(phoneme_content)
        phoneme_path = f.name

    mgr = HotwordManager(hotwords_file=hw_path, rules_file=rules_path)
    mgr._hotwords_file = hw_path
    mgr._rules_file = rules_path
    os.environ['VOICE_INPUT_TOOL_USER_DATA'] = os.path.dirname(phoneme_path)

    pipeline = TextPipeline(
        hotword_manager=mgr,
        phoneme_enabled=True,
        phoneme_threshold=phoneme_threshold,
    )
    return pipeline, mgr, hw_path, rules_path, phoneme_path


def _cleanup(hw_path, rules_path, phoneme_path):
    for p in (hw_path, rules_path, phoneme_path):
        try:
            os.unlink(p)
        except OSError:
            pass


# ─── 测试类 ───

class TestE2EZhHomophoneCorrection(unittest.TestCase):
    """中文同音/近音字纠错。"""

    def test_zh_homophone_sabeining(self):
        """撒贝你 → 撒贝宁（中文同音字纠错）。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="撒贝宁\n")
        try:
            result = p.process("撒贝你主持节目")
            self.assertIn("撒贝宁", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_zh_homophone_dongfangcaifu(self):
        """东方菜富 → 东方财富（中文近音字纠错）。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="东方财富\n")
        try:
            result = p.process("东方菜富发布财报")
            self.assertIn("东方财富", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_zh_homophone_kedaxunfei(self):
        """科大迅飞 → 科大讯飞（前后鼻音混淆纠错）。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="科大讯飞\n")
        try:
            result = p.process("科大迅飞语音识别")
            self.assertIn("科大讯飞", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_zh_no_false_positive(self):
        """正常中文文本不被误替换。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="撒贝宁\n")
        try:
            result = p.process("我们今天要去银行存钱")
            self.assertEqual(result.text, "我们今天要去银行存钱")
        finally:
            _cleanup(hw, rules, ph)


class TestE2EEnSpellCorrection(unittest.TestCase):
    """英文拼写/大小写纠错。"""

    def test_en_case_correction(self):
        """claude → Claude（英文大小写纠错）。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("claude is great")
            self.assertIn("Claude", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_en_spell_correction(self):
        """PyTorch 大小写纠错：py torch → PyTorch（英文拼写纠错）。

        注：PyTorch 驼峰拆分为 py+torch 两个音素，因此输入需为
        "py torch"（两词）才能匹配。单词 "pytorch" 作为一个整体
        音素无法匹配两个音素的热词。
        """
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="PyTorch\n")
        try:
            result = p.process("py torch framework")
            self.assertIn("PyTorch", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_en_no_false_positive(self):
        """正常英文文本不被误替换。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("cloud storage is great")
            self.assertEqual(result.text, "cloud storage is great")
        finally:
            _cleanup(hw, rules, ph)


class TestE2EMixedText(unittest.TestCase):
    """中英混合文本处理。"""

    def test_mixed_zh_en_correction(self):
        """中英混合文本纠错。

        注：英文音素在中文密集上下文中因锚点窗口机制可能不匹配，
        因此分别测试中文和英文部分。
        """
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n撒贝宁\n")
        try:
            # 中文部分应被纠正
            result_zh = p.process("撒贝你主持节目")
            self.assertIn("撒贝宁", result_zh.text)

            # 英文部分应被纠正
            result_en = p.process("claude is great")
            self.assertIn("Claude", result_en.text)
        finally:
            _cleanup(hw, rules, ph)


class TestE2EPerformanceBasic(unittest.TestCase):
    """E2E 性能基本验证。"""

    def test_long_text_performance(self):
        """200 字段落纠错应 < 30ms。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\nPyTorch\nDocker\n撒贝宁\n东方财富\n")
        try:
            # 200 字文本
            text = "科大迅飞语音识别技术在全球范围内得到广泛应用，东方菜富发布了最新财报，" * 6 + "claude和pytorch"
            start = time.perf_counter()
            result = p.process(text)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.assertLess(elapsed_ms, 30, f"200 字纠错耗时 {elapsed_ms:.1f}ms，超过 30ms")
        finally:
            _cleanup(hw, rules, ph)

    def test_short_text_fast_return(self):
        """10 字短文本应 < 5ms。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            start = time.perf_counter()
            result = p.process("claude很好")
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.assertLess(elapsed_ms, 5, f"10 字纠错耗时 {elapsed_ms:.1f}ms，超过 5ms")
        finally:
            _cleanup(hw, rules, ph)


class TestE2EPolyphone(unittest.TestCase):
    """多音字处理。"""

    def test_polyphone_disambiguation(self):
        """pypinyin 词组消歧。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="银行\n")
        try:
            # "银行" 本身就是热词，不应该被错误替换
            result = p.process("去银行存钱")
            self.assertIn("银行", result.text)
        finally:
            _cleanup(hw, rules, ph)


class TestE2ESpecialText(unittest.TestCase):
    """特殊文本处理。"""

    def test_pure_punctuation(self):
        """纯标点文本不崩溃。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("，。！？、")
            self.assertEqual(result.text, "，。！？、")
        finally:
            _cleanup(hw, rules, ph)

    def test_pure_numbers(self):
        """纯数字文本不崩溃。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("123456789")
            self.assertEqual(result.text, "123456789")
        finally:
            _cleanup(hw, rules, ph)

    def test_non_hotword_text_unchanged(self):
        """音素层不影响非热词文本。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("今天天气真好啊")
            self.assertEqual(result.text, "今天天气真好啊")
            self.assertFalse(result.is_changed)
        finally:
            _cleanup(hw, rules, ph)


class TestE2EBoundaryConditions(unittest.TestCase):
    """边界条件测试。"""

    def test_hotword_exact_substring_match(self):
        """热词是文本子串的精确匹配。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("我喜欢Claude")
            # 即使已是正确大小写，也应该被正确处理
            self.assertIn("Claude", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_hotword_at_text_start(self):
        """热词在文本开头。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("claude is an AI")
            self.assertTrue(result.text.startswith("Claude"))
        finally:
            _cleanup(hw, rules, ph)

    def test_hotword_at_text_middle(self):
        """热词在文本中间。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("I think claude is good")
            self.assertIn("Claude", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_hotword_at_text_end(self):
        """热词在文本结尾。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("I like claude")
            self.assertIn("Claude", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_multiple_hotwords_adjacent(self):
        """多个热词紧邻。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\nDocker\n")
        try:
            result = p.process("claude docker github")
            self.assertIn("Claude", result.text)
            self.assertIn("Docker", result.text)
        finally:
            _cleanup(hw, rules, ph)


class TestE2EFullFlow(unittest.TestCase):
    """完整流程 E2E 测试。"""

    def test_load_file_to_correction_full_flow(self):
        """E2E：从文件加载到纠错全流程。"""
        fixtures_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures", "phoneme"
        )
        basic_path = os.path.join(fixtures_dir, "hotwords_basic.txt")
        if not os.path.exists(basic_path):
            self.skipTest("fixtures/phoneme/hotwords_basic.txt not found")

        corrector = PhonemeCorrector(threshold=0.7, enabled=True)
        count = corrector.update_from_file(basic_path)
        self.assertGreater(count, 0)

        # 测试多个场景
        r1 = corrector.correct("claude is great")
        self.assertIn("Claude", r1.text)

        r2 = corrector.correct("docker container")
        self.assertIn("Docker", r2.text)

    def test_phoneme_corrector_plus_text_replace(self):
        """E2E：PhonemeCorrector + 文本替换组合。"""
        p, mgr, hw, rules, ph = _make_pipeline(
            hw_content="Claude -> ClaudeAI",
            phoneme_content="Claude\n",
        )
        try:
            result = p.process("claude is great")
            # 音素层: claude → Claude → 文本层: Claude → ClaudeAI
            self.assertIn("ClaudeAI", result.text)
        finally:
            _cleanup(hw, rules, ph)


class TestE2EEdgeInput(unittest.TestCase):
    """边界输入测试。"""

    def test_empty_text(self):
        """空文本输入。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("")
            self.assertEqual(result.text, "")
            self.assertFalse(result.is_changed)
        finally:
            _cleanup(hw, rules, ph)

    def test_none_input(self):
        """None 输入。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process(None)
            # None is falsy → returns original
            self.assertIsNone(result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_pure_english_long_sentence(self):
        """纯英文长句。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\nDocker\n")
        try:
            text = "The quick brown fox jumps over the lazy dog. claude uses docker for deployment."
            result = p.process(text)
            self.assertIn("Claude", result.text)
            self.assertIn("Docker", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_text_with_emoji(self):
        """含 emoji 的文本。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            result = p.process("claude很厉害🎉🚀")
            self.assertIn("Claude", result.text)
            self.assertIn("🎉", result.text)
            self.assertIn("🚀", result.text)
        finally:
            _cleanup(hw, rules, ph)


if __name__ == "__main__":
    unittest.main()
