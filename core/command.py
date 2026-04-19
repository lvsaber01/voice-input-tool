"""语音命令模块

提供语音命令匹配和执行功能。
命令表使用完整字符串枚举，禁止 ? 量词，fullmatch 全文精确匹配。
最小 2 字符长度保护。
"""

import re
import logging
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)


class CommandType(Enum):
    KEY_SEQUENCE = auto()
    ENGINE_ACTION = auto()
    TEXT_REPLACE = auto()


@dataclass
class VoiceCommand:
    name: str
    patterns: List[str]
    type: CommandType
    action_data: str
    description: str = ""


# ============================================================
# 默认命令表
# ============================================================

DEFAULT_COMMANDS: List[VoiceCommand] = [
    # 编辑操作
    VoiceCommand("撤销", [r"撤销|撤回|删除上一句"],
                 CommandType.KEY_SEQUENCE, "ctrl+z", "撤销上一次输入"),
    VoiceCommand("换行", [r"换行|回车|下一行"],
                 CommandType.KEY_SEQUENCE, "enter", "插入换行"),
    VoiceCommand("退格", [r"退格|删掉"],
                 CommandType.KEY_SEQUENCE, "backspace", "删除一个字符"),
    VoiceCommand("全选", [r"全选"],
                 CommandType.KEY_SEQUENCE, "ctrl+a", "全选文本"),
    VoiceCommand("复制", [r"复制"],
                 CommandType.KEY_SEQUENCE, "ctrl+c", "复制选中文本"),
    VoiceCommand("粘贴", [r"粘贴"],
                 CommandType.KEY_SEQUENCE, "ctrl+v", "粘贴剪贴板"),
    # 引擎控制
    VoiceCommand("停止录音", [r"停止录音|停止转写"],
                 CommandType.ENGINE_ACTION, "stop", "停止当前录音"),
    # 标点符号
    VoiceCommand("句号", [r"句号"],
                 CommandType.TEXT_REPLACE, "。", "输入句号"),
    VoiceCommand("逗号", [r"逗号"],
                 CommandType.TEXT_REPLACE, "，", "输入逗号"),
    VoiceCommand("问号", [r"问号"],
                 CommandType.TEXT_REPLACE, "？", "输入问号"),
    VoiceCommand("感叹号", [r"感叹号|叹号"],
                 CommandType.TEXT_REPLACE, "！", "输入感叹号"),
    VoiceCommand("冒号", [r"冒号"],
                 CommandType.TEXT_REPLACE, "：", "输入冒号"),
]


class CommandMatcher:
    """语音命令匹配器。

    使用 fullmatch 全文精确匹配，最小 2 字符保护。
    """

    def __init__(self, commands: Optional[List[VoiceCommand]] = None):
        self._commands = commands if commands is not None else list(DEFAULT_COMMANDS)
        # 预编译正则
        self._compiled: List[List[re.Pattern]] = []
        for cmd in self._commands:
            compiled_patterns = []
            for p in cmd.patterns:
                compiled_patterns.append(re.compile(p))
            self._compiled.append(compiled_patterns)

    def match(self, text: str) -> Optional[Tuple[VoiceCommand, re.Match]]:
        """匹配文本是否为命令。

        Returns:
            (VoiceCommand, Match) 或 None
        """
        cleaned = text.strip().strip("。，？！、：；""''")
        # 最小长度保护：单字/空串不匹配命令
        if len(cleaned) < 2:
            return None
        for i, cmd in enumerate(self._commands):
            for pattern in self._compiled[i]:
                m = pattern.fullmatch(cleaned)
                if m:
                    return (cmd, m)
        return None


class CommandExecutor:
    """语音命令执行器。

    按 CommandType 分发执行。key_simulator 懒初始化。
    """

    def __init__(self, engine=None, injector=None):
        self._engine = engine
        self._injector = injector
        self._key_sim = None

    @property
    def key_simulator(self):
        """懒初始化 key_simulator"""
        if self._key_sim is None:
            from platform_adapter import create_key_simulator
            self._key_sim = create_key_simulator()
        return self._key_sim

    def execute(self, command: VoiceCommand) -> bool:
        """执行命令，按 type 分发。

        Returns:
            True=执行成功, False=执行失败
        """
        try:
            if command.type == CommandType.KEY_SEQUENCE:
                return self._execute_key_sequence(command.action_data)
            elif command.type == CommandType.ENGINE_ACTION:
                return self._execute_engine_action(command.action_data)
            elif command.type == CommandType.TEXT_REPLACE:
                return self._execute_text_replace(command.action_data)
            else:
                logger.warning("未知命令类型: %s", command.type)
                return False
        except Exception as e:
            logger.error("执行命令 '%s' 失败: %s", command.name, e)
            return False

    def _execute_key_sequence(self, sequence: str) -> bool:
        """执行按键序列"""
        self.key_simulator.send(sequence)
        return True

    def _execute_engine_action(self, action: str) -> bool:
        """执行引擎动作"""
        if self._engine is None:
            logger.warning("engine 未设置，无法执行引擎动作: %s", action)
            return False

        if action == "stop":
            self._engine.on_hotkey_stop()
            return True
        else:
            logger.warning("未知引擎动作: %s", action)
            return False

    def _execute_text_replace(self, replacement: str) -> bool:
        """执行文本替换（注入标点等）"""
        if self._injector:
            return self._injector.inject(replacement)
        # fallback: 用 key_simulator 输入
        self.key_simulator.type_text(replacement)
        return True
