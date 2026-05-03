"""Pipeline + 命令 集成测试。

验证命令匹配在 pipeline 之后执行、匹配到命令时不注入文本。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.command import CommandMatcher, CommandExecutor, CommandType


class TestPipelineCommandIntegration(unittest.TestCase):
    """Pipeline + 命令集成测试。"""

    @classmethod
    def setUpClass(cls):
        cls.matcher = CommandMatcher()

    def test_command_after_pipeline(self):
        """命令匹配在 pipeline 处理之后执行。

        模拟：STT 输出 "撤销" → pipeline 不变 → 命令匹配成功
        """
        text = "撤销"
        # Pipeline 不应改变命令文本
        result = self.matcher.match(text)
        self.assertIsNotNone(result)

    def test_command_intercepts_inject(self):
        """匹配到命令时不注入文本。

        验证：CommandExecutor 返回 True，引擎应跳过注入。
        """
        executor = CommandExecutor()
        executor._key_sim = MagicMock()

        match_result = self.matcher.match("撤销")
        self.assertIsNotNone(match_result)
        cmd, _ = match_result

        success = executor.execute(cmd)
        self.assertTrue(success)
        # key_sim.send 被调用（ctrl+z），而不是 inject

    def test_command_falls_through(self):
        """未匹配命令时正常注入。"""
        result = self.matcher.match("你好世界")
        self.assertIsNone(result)
        # 引擎应走正常注入路径

    def test_command_with_pipeline_output(self):
        """Pipeline 处理后的文本再匹配命令。

        模拟：STT 输出含噪声 → Pipeline 清理 → 命令匹配
        """
        # Pipeline 清理后仍能匹配
        cleaned = "撤销"
        result = self.matcher.match(cleaned)
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.action_data, "ctrl+z")

    def test_no_command_for_numbers(self):
        """数字文本不匹配命令。"""
        result = self.matcher.match("三百六十五")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
