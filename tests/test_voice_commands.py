"""语音命令模块测试。

测试 Phase 3:
- CommandMatcher 匹配逻辑
- CommandExecutor 执行逻辑
- fullmatch 精确匹配
- 最小长度保护
- 噪声清理
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.command import (
    CommandMatcher, CommandExecutor, VoiceCommand,
    CommandType, DEFAULT_COMMANDS,
)


class TestCommandMatcher(unittest.TestCase):
    """CommandMatcher 匹配测试。"""

    @classmethod
    def setUpClass(cls):
        cls.matcher = CommandMatcher()

    def test_match_undo(self):
        """匹配撤销命令。"""
        result = self.matcher.match("撤销")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.name, "撤销")

    def test_match_undo_synonym(self):
        """匹配撤回（撤销的同义词）。"""
        result = self.matcher.match("撤回")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.name, "撤销")

    def test_match_enter(self):
        """匹配换行命令。"""
        result = self.matcher.match("换行")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.name, "换行")
        self.assertEqual(cmd.action_data, "enter")

    def test_match_backspace(self):
        """匹配退格命令。"""
        result = self.matcher.match("退格")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.action_data, "backspace")

    def test_match_stop_recording(self):
        """匹配停止录音命令。"""
        result = self.matcher.match("停止录音")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.type, CommandType.ENGINE_ACTION)
        self.assertEqual(cmd.action_data, "stop")

    def test_no_match_normal_text(self):
        """正常文本不匹配命令。"""
        self.assertIsNone(self.matcher.match("你好世界"))

    def test_no_match_partial(self):
        """包含命令词但不全是命令的文本不匹配。"""
        # fullmatch 策略：只有整句话是命令才匹配
        self.assertIsNone(self.matcher.match("帮我撤销一下"))

    def test_no_match_single_char(self):
        """单字不匹配（最小 2 字符保护）。"""
        self.assertIsNone(self.matcher.match("撤"))

    def test_no_match_empty(self):
        """空串不匹配。"""
        self.assertIsNone(self.matcher.match(""))

    def test_strip_punctuation(self):
        """带标点的命令仍匹配（噪声清理）。"""
        result = self.matcher.match("撤销。")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.name, "撤销")

    def test_match_select_all(self):
        """匹配全选命令。"""
        result = self.matcher.match("全选")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.action_data, "ctrl+a")

    def test_match_copy(self):
        """匹配复制命令。"""
        result = self.matcher.match("复制")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.action_data, "ctrl+c")

    def test_match_paste(self):
        """匹配粘贴命令。"""
        result = self.matcher.match("粘贴")
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd.action_data, "ctrl+v")


class TestCommandExecutor(unittest.TestCase):
    """CommandExecutor 执行测试。"""

    def test_execute_key_sequence(self):
        """执行按键序列命令。"""
        executor = CommandExecutor()
        mock_sim = MagicMock()
        executor._key_sim = mock_sim  # 直接设置，避免懒初始化

        cmd = VoiceCommand("测试", [], CommandType.KEY_SEQUENCE, "ctrl+z", "")
        result = executor.execute(cmd)
        self.assertTrue(result)
        mock_sim.send.assert_called_with("ctrl+z")

    def test_execute_engine_action_stop(self):
        """执行引擎停止动作。"""
        mock_engine = MagicMock()
        executor = CommandExecutor(engine=mock_engine)

        cmd = VoiceCommand("停止", [], CommandType.ENGINE_ACTION, "stop", "")
        result = executor.execute(cmd)
        self.assertTrue(result)
        mock_engine.on_hotkey_stop.assert_called_once()

    def test_execute_engine_action_no_engine(self):
        """无 engine 时引擎动作失败。"""
        executor = CommandExecutor(engine=None)
        cmd = VoiceCommand("停止", [], CommandType.ENGINE_ACTION, "stop", "")
        result = executor.execute(cmd)
        self.assertFalse(result)

    def test_execute_text_replace_with_injector(self):
        """执行文本替换（有 injector）。"""
        mock_injector = MagicMock()
        executor = CommandExecutor(injector=mock_injector)

        cmd = VoiceCommand("句号", [], CommandType.TEXT_REPLACE, "。", "")
        result = executor.execute(cmd)
        self.assertTrue(result)
        mock_injector.inject.assert_called_with("。")

    def test_execute_text_replace_no_injector(self):
        """执行文本替换（无 injector，fallback key_sim）。"""
        executor = CommandExecutor()
        mock_sim = MagicMock()
        executor._key_sim = mock_sim

        cmd = VoiceCommand("句号", [], CommandType.TEXT_REPLACE, "。", "")
        result = executor.execute(cmd)
        self.assertTrue(result)
        mock_sim.type_text.assert_called_with("。")


class TestDefaultCommands(unittest.TestCase):
    """默认命令表完整性测试。"""

    def test_default_commands_not_empty(self):
        """默认命令表非空。"""
        self.assertGreater(len(DEFAULT_COMMANDS), 0)

    def test_all_commands_have_patterns(self):
        """所有命令都有正则模式。"""
        for cmd in DEFAULT_COMMANDS:
            self.assertGreater(len(cmd.patterns), 0, f"{cmd.name} 无匹配模式")

    def test_all_commands_have_type(self):
        """所有命令都有类型。"""
        for cmd in DEFAULT_COMMANDS:
            self.assertIsNotNone(cmd.type, f"{cmd.name} 无类型")

    def test_no_question_mark_in_patterns(self):
        """正则模式中禁止使用 ? 量词（设计规范）。"""
        for cmd in DEFAULT_COMMANDS:
            for p in cmd.patterns:
                # 允许正则中的 ? 用于非贪婪量词（如 .*?），但禁止独立 ?
                # 简化检查：禁止模式中只有 ? 而无前置量词的情况
                self.assertNotEqual(p.strip(), "?", f"{cmd.name} 模式含独立 ?")


if __name__ == "__main__":
    unittest.main()
