"""CommandMatcher 单元测试"""

import unittest
from core.command import CommandMatcher, DEFAULT_COMMANDS


class TestCommandMatcher(unittest.TestCase):

    def setUp(self):
        self.matcher = CommandMatcher()

    def test_default_commands_match(self):
        """所有默认命令正确匹配"""
        cases = {
            "撤销": "撤销",
            "撤回": "撤销",
            "换行": "换行",
            "回车": "换行",
            "退格": "退格",
            "全选": "全选",
            "复制": "复制",
            "粘贴": "粘贴",
            "句号": "句号",
            "逗号": "逗号",
            "问号": "问号",
            "感叹号": "感叹号",
            "叹号": "感叹号",
            "冒号": "冒号",
            "停止录音": "停止录音",
        }
        for text, expected_name in cases.items():
            result = self.matcher.match(text)
            self.assertIsNotNone(result, f"'{text}' 应匹配命令")
            cmd, _ = result
            self.assertEqual(cmd.name, expected_name, f"'{text}' 应匹配 '{expected_name}'")

    def test_empty_string_no_match(self):
        """空串不匹配"""
        self.assertIsNone(self.matcher.match(""))

    def test_single_char_no_match(self):
        """单字符不匹配"""
        self.assertIsNone(self.matcher.match("换"))

    def test_long_sentence_no_false_match(self):
        """长句不误触发"""
        self.assertIsNone(self.matcher.match("我想删除这个文件"))
        self.assertIsNone(self.matcher.match("请帮我复制一下这段内容"))
        self.assertIsNone(self.matcher.match("你好世界"))

    def test_fullmatch_precision(self):
        """fullmatch 精确匹配：'换行' 匹配但 '换行符' 不匹配 '换行' 命令"""
        result = self.matcher.match("换行")
        self.assertIsNotNone(result)
        self.assertEqual(result[0].name, "换行")

        result = self.matcher.match("换行符")
        self.assertIsNone(result, "'换行符' 不应匹配 '换行' 命令")

    def test_whitespace_and_punctuation_stripped(self):
        """前后空白和标点被去除"""
        result = self.matcher.match("换行。")
        self.assertIsNotNone(result)
        self.assertEqual(result[0].name, "换行")


if __name__ == "__main__":
    unittest.main()
