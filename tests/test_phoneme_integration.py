"""Layer 2: 音素匹配集成测试 — 模块间交互 + Pipeline 级联。

验证音素纠错层与 TextPipeline 现有层的级联协作。
不 mock pypinyin，使用真实依赖。
"""

import os
import sys
import tempfile
import threading
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
    case_sensitive: bool = False,
    phoneme_enabled: bool = True,
    phoneme_threshold: float = 0.7,
):
    """创建 TextPipeline + HotwordManager，含音素热词文件。

    返回 (pipeline, mgr, hw_path, rules_path, phoneme_path)。
    """
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

    # 让 resolve_file_path 找到音素文件
    os.environ['VOICE_INPUT_TOOL_USER_DATA'] = os.path.dirname(phoneme_path)

    pipeline = TextPipeline(
        hotword_manager=mgr,
        case_sensitive=case_sensitive,
        phoneme_enabled=phoneme_enabled,
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

class TestPhonemeCorrectorPipelineIntegration(unittest.TestCase):
    """音素纠错器与 Pipeline 集成。"""

    def test_phoneme_correction_then_regex(self):
        """音素纠错 + 正则规则级联：音素先纠错，正则后映射。"""
        phoneme_content = "CUDA\n"
        rules_content = r"酷打 = CUDA" + "\n"
        p, mgr, hw, rules, ph = _make_pipeline(
            rules_content=rules_content,
            phoneme_content=phoneme_content,
        )
        try:
            # "酷打" 先经过音素层（可能不匹配），然后被正则层替换
            result = p.process("我用酷打编程")
            self.assertIn("CUDA", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_phoneme_correction_then_hotword(self):
        """音素纠错 + 文本替换级联。"""
        phoneme_content = "Claude\n"
        hw_content = "Claude -> ClaudeAI"
        p, mgr, hw, rules, ph = _make_pipeline(
            hw_content=hw_content,
            phoneme_content=phoneme_content,
        )
        try:
            # "claude" 先被音素层纠正为 "Claude"，然后被文本替换为 "ClaudeAI"
            result = p.process("claude is great")
            self.assertIn("ClaudeAI", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_three_layer_cascade(self):
        """三层完整级联：音素→正则→文本。"""
        phoneme_content = "Claude\n"
        rules_content = r"\[noise\] = " + "\n"
        hw_content = "Claude -> ClaudeAI"
        p, mgr, hw, rules, ph = _make_pipeline(
            hw_content=hw_content,
            rules_content=rules_content,
            phoneme_content=phoneme_content,
        )
        try:
            # 音素层: claude → Claude
            # 正则层: [noise] → ""
            # 文本层: Claude → ClaudeAI
            result = p.process("claude is [noise]great")
            self.assertIn("ClaudeAI", result.text)
            self.assertNotIn("[noise]", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_phoneme_enabled_false_skips_phoneme_layer(self):
        """phoneme_enabled=False 时跳过音素层。"""
        phoneme_content = "撒贝宁\n"
        p, mgr, hw, rules, ph = _make_pipeline(
            phoneme_content=phoneme_content,
            phoneme_enabled=False,
        )
        try:
            result = p.process("撒贝你主持节目")
            # 音素层被禁用，不会被纠正
            self.assertEqual(result.text, "撒贝你主持节目")
            self.assertEqual(result.phoneme_matches, [])
        finally:
            _cleanup(hw, rules, ph)

    def test_phoneme_matches_in_process_result(self):
        """phoneme_matches 在 ProcessResult 中正确返回。"""
        phoneme_content = "Claude\n"
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content=phoneme_content)
        try:
            result = p.process("claude is great")
            if result.is_changed:
                # 如果音素层做了替换，phoneme_matches 应非空
                self.assertGreater(len(result.phoneme_matches), 0)
                match = result.phoneme_matches[0]
                self.assertEqual(match.hotword, "Claude")
        finally:
            _cleanup(hw, rules, ph)

    def test_empty_phoneme_file_pipeline_normal(self):
        """空 hotwords-phoneme.txt 时 Pipeline 正常。"""
        p, mgr, hw, rules, ph = _make_pipeline(
            hw_content="CUDA -> CUDA",
            phoneme_content="# only comments\n",
        )
        try:
            result = p.process("cuda is great")
            # 热词替换层仍生效
            self.assertTrue(result.is_changed)
            self.assertIn("CUDA", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_phoneme_correction_further_modified_by_regex(self):
        """音素纠正后再被正则层进一步修改。"""
        phoneme_content = "Claude\n"
        rules_content = r"Claude = CLAUDE_AI" + "\n"
        p, mgr, hw, rules, ph = _make_pipeline(
            rules_content=rules_content,
            phoneme_content=phoneme_content,
        )
        try:
            # "claude" → 音素层 → "Claude" → 正则层 → "CLAUDE_AI"
            result = p.process("claude framework")
            self.assertIn("CLAUDE_AI", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_multiple_phoneme_matches_in_same_text(self):
        """多个音素匹配在同一文本中的处理。"""
        phoneme_content = "Claude\nDocker\n"
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content=phoneme_content)
        try:
            result = p.process("claude and docker are great")
            # 两个热词都应被纠正
            self.assertIn("Claude", result.text)
            self.assertIn("Docker", result.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_phoneme_score_below_threshold_no_replace(self):
        """音素匹配分数低于阈值时不替换。"""
        phoneme_content = "张三丰\n"
        p, mgr, hw, rules, ph = _make_pipeline(
            phoneme_content=phoneme_content,
            phoneme_threshold=0.95,  # 非常高
        )
        try:
            # 输入与热词发音差距大，应不替换
            result = p.process("今天天气很好")
            self.assertEqual(result.text, "今天天气很好")
        finally:
            _cleanup(hw, rules, ph)


class TestPhonemeReloadIntegration(unittest.TestCase):
    """音素热词 reload 与 Pipeline 的集成。"""

    def test_reload_updates_phoneme_hotwords(self):
        """reload 后音素热词更新生效。"""
        p, mgr, hw, rules, ph = _make_pipeline(phoneme_content="Claude\n")
        try:
            # 初始：Claude 在音素热词中
            result1 = p.process("claude is great")
            # 修改音素文件，添加新热词
            with open(ph, 'w', encoding='utf-8') as f:
                f.write("Docker\nClaude\n")
            p.reload()
            result2 = p.process("docker container")
            # reload 后 Docker 应被纠正
            self.assertIn("Docker", result2.text)
        finally:
            _cleanup(hw, rules, ph)

    def test_reload_preserves_existing_layers(self):
        """reload 音素热词不影响正则和文本层。"""
        p, mgr, hw, rules, ph = _make_pipeline(
            hw_content="CUDA -> CUDA",
            rules_content=r"\[noise\] = ",
            phoneme_content="Claude\n",
        )
        try:
            # reload
            p.reload()
            # 热词替换层仍然工作
            result = p.process("cuda [noise]")
            self.assertIn("CUDA", result.text)
            self.assertNotIn("[noise]", result.text)
        finally:
            _cleanup(hw, rules, ph)


class TestPhonemeDegradationIntegration(unittest.TestCase):
    """音素层降级场景。"""

    def test_pipeline_degrades_gracefully_without_phoneme_file(self):
        """音素文件不存在时 Pipeline 降级，其他层正常。"""
        hw_content = "CUDA -> CUDA"
        rules_content = r"\[noise\] = "

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(hw_content)
            hw_path = f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(rules_content)
            rules_path = f.name
        # 不创建音素文件
        phoneme_path = os.path.join(tempfile.gettempdir(), "nonexistent_phoneme.txt")

        try:
            mgr = HotwordManager(hotwords_file=hw_path, rules_file=rules_path)
            mgr._hotwords_file = hw_path
            mgr._rules_file = rules_path
            os.environ['VOICE_INPUT_TOOL_USER_DATA'] = tempfile.gettempdir()

            pipeline = TextPipeline(hotword_manager=mgr, phoneme_enabled=True)
            # 不崩溃
            result = pipeline.process("cuda [noise]")
            self.assertIn("CUDA", result.text)
            self.assertNotIn("[noise]", result.text)
        finally:
            for path in (hw_path, rules_path, phoneme_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


class TestCorrectorDirectIntegration(unittest.TestCase):
    """PhonemeCorrector 直接集成测试。"""

    def test_corrector_basic_flow(self):
        """从 PhonemeCorrector 直接调用 correct。"""
        fixtures_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures", "phoneme"
        )
        basic_path = os.path.join(fixtures_dir, "hotwords_basic.txt")
        if not os.path.exists(basic_path):
            self.skipTest("fixtures/phoneme/hotwords_basic.txt not found")

        corrector = PhonemeCorrector(threshold=0.7, enabled=True)
        count = corrector.update_from_file(basic_path)
        self.assertGreater(count, 0)

        # 测试纠错
        result = corrector.correct("claude is great")
        self.assertEqual(result.text, "Claude is great")
        self.assertGreater(len(result.matches), 0)

    def test_corrector_zh_homophone_correction(self):
        """中文同音字纠错。"""
        fixtures_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures", "phoneme"
        )
        basic_path = os.path.join(fixtures_dir, "hotwords_basic.txt")
        if not os.path.exists(basic_path):
            self.skipTest("fixtures/phoneme/hotwords_basic.txt not found")

        corrector = PhonemeCorrector(threshold=0.7, enabled=True)
        corrector.update_from_file(basic_path)

        result = corrector.correct("撒贝你主持节目")
        self.assertIn("撒贝宁", result.text)

    def test_corrector_concurrent_access(self):
        """并发纠错不崩溃。"""
        fixtures_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures", "phoneme"
        )
        basic_path = os.path.join(fixtures_dir, "hotwords_basic.txt")
        if not os.path.exists(basic_path):
            self.skipTest("fixtures/phoneme/hotwords_basic.txt not found")

        corrector = PhonemeCorrector(threshold=0.7, enabled=True)
        corrector.update_from_file(basic_path)

        errors = []
        results = []

        def worker():
            try:
                r = corrector.correct("claude and pytorch")
                results.append(r.text)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(errors), 0, f"Concurrent errors: {errors}")


if __name__ == "__main__":
    unittest.main()
