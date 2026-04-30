"""HotwordManager 单元测试。"""

import os
import sys
import tempfile
import unittest

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hotword import HotwordManager, HotwordEntry


class TestHotwordParsing(unittest.TestCase):
    """热词文件解析测试。"""

    def test_arrow_unicode_separator(self):
        """→ (U+2192) 分隔符解析"""
        mgr = HotwordManager.__new__(HotwordManager)
        entry = mgr._parse_hotword_line("CUDA → CUDA")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "CUDA")
        self.assertEqual(entry.target, "CUDA")
        self.assertFalse(entry.model_hotword)  # 有箭头，不传给模型

    def test_arrow_ascii_separator(self):
        """-> ASCII 分隔符解析"""
        mgr = HotwordManager.__new__(HotwordManager)
        entry = mgr._parse_hotword_line("Kubernetes -> K8s")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "Kubernetes")
        self.assertEqual(entry.target, "K8s")
        self.assertFalse(entry.model_hotword)

    def test_no_arrow_word(self):
        """无箭头词：同时用于文本替换和原生热词"""
        mgr = HotwordManager.__new__(HotwordManager)
        entry = mgr._parse_hotword_line("TensorRT")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "TensorRT")
        self.assertEqual(entry.target, "TensorRT")
        self.assertTrue(entry.model_hotword)
        self.assertTrue(entry.text_replace)

    def test_comment_skip(self):
        """注释行跳过"""
        mgr = HotwordManager.__new__(HotwordManager)
        self.assertIsNone(mgr._parse_hotword_line("# 这是注释"))
        self.assertIsNone(mgr._parse_hotword_line("  # 前面有空格"))

    def test_empty_line_skip(self):
        """空行跳过"""
        mgr = HotwordManager.__new__(HotwordManager)
        self.assertIsNone(mgr._parse_hotword_line(""))
        self.assertIsNone(mgr._parse_hotword_line("   "))

    def test_category_marker(self):
        """[分类] 标记"""
        mgr = HotwordManager.__new__(HotwordManager)
        self.assertIsNone(mgr._parse_hotword_line("[技术]"))

    def test_empty_source(self):
        """空 source 返回 None"""
        mgr = HotwordManager.__new__(HotwordManager)
        self.assertIsNone(mgr._parse_hotword_line(" → target"))

    def test_special_characters(self):
        """特殊字符处理"""
        mgr = HotwordManager.__new__(HotwordManager)
        entry = mgr._parse_hotword_line("C++ -> C++")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "C++")
        self.assertEqual(entry.target, "C++")


class TestHotwordManager(unittest.TestCase):
    """HotwordManager 核心功能测试。"""

    def _make_mgr(self, content="", rules_content=""):
        """创建临时文件并构造 HotwordManager。"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            hw_path = f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(rules_content)
            rules_path = f.name
        try:
            mgr = HotwordManager(hotwords_file=hw_path, rules_file=rules_path)
            # 覆盖路径确保用临时文件
            mgr._hotwords_file = hw_path
            mgr._rules_file = rules_path
            yield mgr
        finally:
            os.unlink(hw_path)
            os.unlink(rules_path)

    def test_load_basic(self):
        """基本加载"""
        content = "CUDA\nKubernetes -> K8s\n"
        for mgr in self._make_mgr(content):
            self.assertEqual(len(mgr._entries), 2)

    def test_model_hotword_list(self):
        """原生热词列表生成（仅无箭头词）"""
        content = "CUDA\nKubernetes -> K8s\nTensorRT\n"
        for mgr in self._make_mgr(content):
            model_list = mgr.get_model_hotword_list()
            self.assertIn("CUDA", model_list)
            self.assertIn("TensorRT", model_list)
            self.assertNotIn("Kubernetes", model_list)  # 有箭头，不传模型

    def test_text_hotword_map_sorted(self):
        """文本替换字典按长度降序"""
        content = "K8s\nKubernetes -> K8s\n"
        for mgr in self._make_mgr(content):
            hmap = mgr.get_text_hotword_map()
            keys = list(hmap.keys())
            # Kubernetes (10) 应该在 K8s (3) 前面
            self.assertGreater(len(keys[0]), len(keys[-1]))

    def test_add_remove_hotword(self):
        """运行时增删"""
        for mgr in self._make_mgr(""):
            mgr.add_hotword("TestWord", "TW")
            self.assertEqual(len(mgr._entries), 1)
            self.assertTrue(mgr.remove_hotword("TestWord"))
            self.assertEqual(len(mgr._entries), 0)
            self.assertFalse(mgr.remove_hotword("NotExist"))

    def test_conflict_detection(self):
        """热词冲突检测"""
        content = "CUDA -> CUDA1\nCUDA -> CUDA2\n"
        for mgr in self._make_mgr(content):
            self.assertEqual(len(mgr._entries), 1)
            self.assertEqual(mgr._entries[0].target, "CUDA1")  # 保留首次

    def test_save_to_file_atomic(self):
        """原子写入验证"""
        for mgr in self._make_mgr(""):
            mgr.add_hotword("Test")
            success = mgr.save_to_file()
            self.assertTrue(success)
            # 重新加载验证
            with open(mgr.resolve_file_path(mgr._hotwords_file), 'r') as f:
                content = f.read()
            self.assertIn("Test", content)

    def test_bom_handling(self):
        """BOM 头处理"""
        content = "\ufeffCUDA\n"
        for mgr in self._make_mgr(content):
            self.assertEqual(len(mgr._entries), 1)

    def test_min_word_length(self):
        """最小词长保护"""
        content = "A\nCUDA\n"
        for mgr in self._make_mgr(content):
            mgr._min_word_length = 2
            hmap = mgr.get_text_hotword_map()
            self.assertNotIn("A", hmap)
            self.assertIn("CUDA", hmap)

    def test_long_line_handling(self):
        """超长行处理"""
        long_word = "A" * 1000
        content = f"{long_word}\nCUDA\n"
        for mgr in self._make_mgr(content):
            self.assertEqual(len(mgr._entries), 2)

    def test_empty_target(self):
        """空 target 保持为空字符串"""
        mgr = HotwordManager.__new__(HotwordManager)
        entry = mgr._parse_hotword_line("CUDA -> ")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "CUDA")
        # 箭头格式：空 target 保持为空（不是回退到 source）
        self.assertEqual(entry.target, "")


class TestRuleParsing(unittest.TestCase):
    """正则规则解析测试。"""

    def test_basic_rule(self):
        """基本规则解析"""
        mgr = HotwordManager.__new__(HotwordManager)
        parsed = mgr._parse_rule_line(r"[，。] = ,")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], r"[，。]")
        self.assertEqual(parsed[1], ",")

    def test_empty_replacement(self):
        """空替换（噪声清理）"""
        mgr = HotwordManager.__new__(HotwordManager)
        parsed = mgr._parse_rule_line(r"\[音乐\] = ")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[1], "")

    def test_invalid_rule_no_equals(self):
        """无等号规则"""
        mgr = HotwordManager.__new__(HotwordManager)
        self.assertIsNone(mgr._parse_rule_line("no_equals_here"))

    def test_empty_pattern(self):
        """空 pattern"""
        mgr = HotwordManager.__new__(HotwordManager)
        self.assertIsNone(mgr._parse_rule_line(" = replacement"))

    def test_comment_skip(self):
        """注释行跳过"""
        mgr = HotwordManager.__new__(HotwordManager)
        self.assertIsNone(mgr._parse_rule_line("# 注释"))

    def test_complexity_validation(self):
        """正则复杂度校验"""
        self.assertTrue(HotwordManager._validate_regex_complexity(r"\d+"))
        self.assertFalse(HotwordManager._validate_regex_complexity(r"a" * 501))
        # 连续量词 (adjacent quantifiers)
        self.assertFalse(HotwordManager._validate_regex_complexity(r"a++++b"))
        # 普通正则应通过
        self.assertTrue(HotwordManager._validate_regex_complexity(r"(a+)+b"))


class TestRuleLoading(unittest.TestCase):
    """规则文件加载测试。"""

    def test_default_rules_compile(self):
        """默认预置规则全部可编译"""
        rules_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hot-rules.txt")
        if not os.path.exists(rules_file):
            self.skipTest("hot-rules.txt not found")
        mgr = HotwordManager(rules_file=rules_file)
        compiled = mgr.get_compiled_rules()
        self.assertGreater(len(compiled), 0)

    def test_load_rules_basic(self):
        """基本规则加载"""
        content = r"[，。] = ," + "\n" + r"\[音乐\] = " + "\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            path = f.name
        try:
            mgr = HotwordManager(rules_file=path)
            mgr._rules_file = path
            compiled = mgr.get_compiled_rules()
            self.assertEqual(len(compiled), 2)
        finally:
            os.unlink(path)


class TestHotwordEntry(unittest.TestCase):
    """HotwordEntry dataclass 测试。"""

    def test_entry_creation(self):
        entry = HotwordEntry(source="CUDA", target="CUDA", category="技术")
        self.assertEqual(entry.source, "CUDA")
        self.assertEqual(entry.target, "CUDA")
        self.assertEqual(entry.category, "技术")
        self.assertTrue(entry.text_replace)
        self.assertTrue(entry.model_hotword)


if __name__ == "__main__":
    unittest.main()
