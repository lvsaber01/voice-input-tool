"""TextPipeline 单元测试。"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hotword import HotwordManager
from core.text_pipeline import TextPipeline, ProcessResult


def make_pipeline(hw_content="", rules_content="", case_sensitive=False, min_word_length=2):
    """创建 TextPipeline 实例。返回 (pipeline, mgr, hw_path, rules_path)。"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(hw_content)
        hw_path = f.name
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(rules_content)
        rules_path = f.name
    mgr = HotwordManager(hotwords_file=hw_path, rules_file=rules_path, min_word_length=min_word_length)
    mgr._hotwords_file = hw_path
    mgr._rules_file = rules_path
    pipeline = TextPipeline(hotword_manager=mgr, case_sensitive=case_sensitive)
    return pipeline, mgr, hw_path, rules_path


def cleanup(hw_path, rules_path):
    try:
        os.unlink(hw_path)
    except OSError:
        pass
    try:
        os.unlink(rules_path)
    except OSError:
        pass


class TestPipelineRegex(unittest.TestCase):
    """正则替换测试。"""

    def test_basic_regex_replace(self):
        """基本正则替换"""
        p, mgr, hw, rules = make_pipeline(rules_content=r"[，。] = ,")
        try:
            result = p.process("你好，世界。")
            self.assertEqual(result.text, "你好,世界,")
        finally:
            cleanup(hw, rules)

    def test_empty_replacement(self):
        """空替换（噪声清理）"""
        p, mgr, hw, rules = make_pipeline(rules_content=r"\[音乐\] = ")
        try:
            result = p.process("[音乐]播放歌曲")
            self.assertEqual(result.text, "播放歌曲")
        finally:
            cleanup(hw, rules)

    def test_regex_applied_before_hotwords(self):
        """执行顺序：正则先于热词"""
        p, mgr, hw, rules = make_pipeline(rules_content=r"\[unk\] = CUDA", hw_content="CUDA -> CUDA")
        try:
            mgr.add_hotword("CUDA", "CUDA")
            p.reload()
            result = p.process("我喜欢[unk]")
            self.assertEqual(result.text, "我喜欢CUDA")
        finally:
            cleanup(hw, rules)

    def test_no_rules(self):
        """无规则时不影响文本"""
        p, mgr, hw, rules = make_pipeline(rules_content="")
        try:
            result = p.process("测试文本")
            self.assertEqual(result.text, "测试文本")
            self.assertFalse(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_multiple_rules_sequential(self):
        """多条规则按顺序执行"""
        content = r"[，] = ," + "\n" + r"[。] = ."
        p, mgr, hw, rules = make_pipeline(rules_content=content)
        try:
            result = p.process("你好，世界。")
            self.assertEqual(result.text, "你好,世界.")
        finally:
            cleanup(hw, rules)

    def test_regex_per_rule_error_tolerance(self):
        """正则逐条容错：某条异常不影响其他"""
        p, mgr, hw, rules = make_pipeline(rules_content=r"[，] = ,")
        try:
            # 注入一条会抛异常的 compiled rule（必须是 (pattern, replacement) 元组）
            bad_pattern = MagicMock()
            bad_pattern.sub = MagicMock(side_effect=RuntimeError("test"))
            bad_pattern.pattern = "bad_pattern"
            original = list(p._regex_rules)
            p._regex_rules = [(bad_pattern, "")] + original
            # Should not raise, bad rule is skipped
            result = p.process("你好，")
            self.assertIn(",", result.text)
        finally:
            cleanup(hw, rules)


class TestPipelineHotwords(unittest.TestCase):
    """热词替换测试。"""

    def test_basic_hotword_replace(self):
        """基本热词替换"""
        p, mgr, hw, rules = make_pipeline(hw_content="Kubernetes -> K8s")
        try:
            result = p.process("我学习Kubernetes已经三年了")
            self.assertIn("K8s", result.text)
            self.assertNotIn("Kubernetes", result.text)
        finally:
            cleanup(hw, rules)

    def test_no_match_no_change(self):
        """无匹配时不修改"""
        p, mgr, hw, rules = make_pipeline(hw_content="CUDA -> CUDA")
        try:
            result = p.process("Python很好用")
            self.assertFalse(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_long_word_priority(self):
        """长词优先替换"""
        p, mgr, hw, rules = make_pipeline(hw_content="Kubernetes -> K8s\nKube -> KubeShort")
        try:
            result = p.process("Kubernetes")
            self.assertIn("K8s", result.text)
        finally:
            cleanup(hw, rules)

    def test_case_sensitive_true(self):
        """区分大小写"""
        p, mgr, hw, rules = make_pipeline(hw_content="CUDA -> CUDA", case_sensitive=True)
        try:
            result = p.process("cuda is good")
            self.assertFalse(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_case_sensitive_false(self):
        """不区分大小写"""
        p, mgr, hw, rules = make_pipeline(hw_content="CUDA -> CUDA", case_sensitive=False)
        try:
            result = p.process("cuda is good")
            self.assertTrue(result.is_changed)
            self.assertIn("CUDA", result.text)
        finally:
            cleanup(hw, rules)

    def test_empty_text(self):
        """空文本返回空"""
        p, mgr, hw, rules = make_pipeline(hw_content="CUDA -> CUDA")
        try:
            result = p.process("")
            self.assertEqual(result.text, "")
            self.assertFalse(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_none_input(self):
        """None 输入返回空"""
        p, mgr, hw, rules = make_pipeline(hw_content="CUDA -> CUDA")
        try:
            # process handles None gracefully (returns text="")
            result = p.process(None)
            # None is falsy so enabled check + not text → returns original
            self.assertEqual(result.text, None)
        finally:
            cleanup(hw, rules)


class TestPipelineReload(unittest.TestCase):
    """Pipeline reload 测试。"""

    def test_reload_updates_data(self):
        """reload 更新内部数据"""
        p, mgr, hw, rules = make_pipeline(hw_content="OldWord -> OW")
        try:
            mgr.add_hotword("NewWord", "NW")
            p.reload()
            result = p.process("NewWord")
            self.assertIn("NW", result.text)
        finally:
            cleanup(hw, rules)

    def test_disabled_pipeline_returns_original(self):
        """禁用 pipeline 直接返回原文"""
        p, mgr, hw, rules = make_pipeline(hw_content="CUDA -> CUDA")
        try:
            p.enabled = False
            result = p.process("cuda")
            self.assertFalse(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_reload_callback(self):
        """reload 回调触发"""
        p, mgr, hw, rules = make_pipeline(hw_content="")
        try:
            callback_calls = []
            p.register_reload_callback(lambda: callback_calls.append(1))
            p.reload()
            self.assertEqual(len(callback_calls), 1)
        finally:
            cleanup(hw, rules)


class TestPipelineExceptionHandling(unittest.TestCase):
    """异常降级测试。"""

    def test_process_exception_returns_original(self):
        """process 异常降级返回原文"""
        p, mgr, hw, rules = make_pipeline(hw_content="")
        try:
            original = "测试文本"
            with patch.object(p, '_apply_regex', side_effect=RuntimeError("test")):
                result = p.process(original)
            self.assertEqual(result.text, original)
            self.assertFalse(result.is_changed)
        finally:
            cleanup(hw, rules)


class TestPipelineMinWordLength(unittest.TestCase):
    """最小词长保护测试。"""

    def test_short_word_filtered(self):
        """短于 min_word_length 的词不参与替换"""
        p, mgr, hw, rules = make_pipeline(hw_content="AB -> AB_REPLACED\nCUDA -> CUDA", min_word_length=3)
        try:
            result = p.process("AB and CUDA")
            # AB is 2 chars, min is 3 → should not be replaced
            self.assertNotIn("AB_REPLACED", result.text)
            self.assertIn("AB", result.text)
        finally:
            cleanup(hw, rules)


class TestPipelineEmptyFiles(unittest.TestCase):
    """空文件/全注释文件测试。"""

    def test_all_comments_file(self):
        """全注释热词文件"""
        p, mgr, hw, rules = make_pipeline(hw_content="# comment1\n# comment2\n")
        try:
            result = p.process("test")
            self.assertEqual(result.text, "test")
        finally:
            cleanup(hw, rules)

    def test_empty_rules_file(self):
        """空规则文件"""
        p, mgr, hw, rules = make_pipeline(rules_content="")
        try:
            result = p.process("test")
            self.assertEqual(result.text, "test")
        finally:
            cleanup(hw, rules)


if __name__ == "__main__":
    unittest.main()
