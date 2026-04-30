"""热词管理模块 — 热词数据的唯一管理者。

TextPipeline 和 FunASREngine 都是数据消费方，
通过接口从 HotwordManager 获取所需数据。

v1.0 — 对应设计文档 v3.0
"""

import os
import re
import logging
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class HotwordEntry:
    """单条热词条目"""
    source: str           # 匹配文本
    target: str           # 替换文本
    category: str = ""    # 分类（如"通用"、"技术"）
    text_replace: bool = True   # 是否用于文本替换
    model_hotword: bool = True  # 是否用于 FunASR 原生热词


class HotwordManager:
    """热词数据的唯一管理者。

    职责：
    - 加载/解析热词文件（hotwords.txt）
    - 加载/解析正则规则文件（hot-rules.txt）
    - 运行时增删（内存）
    - 原子持久化（tmp + os.replace）
    - 冲突检测（同一 source 不同 target = warning）
    - 正则复杂度校验（防 ReDoS）
    """

    def __init__(self, hotwords_file: str = "hotwords.txt",
                 rules_file: str = "hot-rules.txt",
                 min_word_length: int = 2):
        self._entries: List[HotwordEntry] = []
        self._rules: List[Tuple[str, str]] = []  # [(pattern_str, replacement)]
        self._compiled_rules: List[Tuple[re.Pattern, str]] = []
        self._hotwords_file: str = hotwords_file
        self._rules_file: str = rules_file
        self._min_word_length: int = min_word_length
        self._current_category: str = ""

        # 尝试加载文件
        hotwords_path = self.resolve_file_path(hotwords_file)
        rules_path = self.resolve_file_path(rules_file)
        if hotwords_path:
            self.load_from_file(hotwords_path)
        if rules_path:
            self.load_rules_from_file(rules_path)

    # ─── 文件路径解析 ───

    def resolve_file_path(self, filename: str) -> Optional[str]:
        """解析文件路径：用户目录优先，回退项目默认目录。"""
        # 优先：用户数据目录
        user_data_dir = os.environ.get("VOICE_INPUT_TOOL_USER_DATA", "")
        if user_data_dir:
            user_path = os.path.join(user_data_dir, filename)
            if os.path.exists(user_path):
                return user_path

        # 回退：项目根目录（脚本所在目录的上级或当前工作目录）
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        project_path = os.path.join(project_root, filename)
        if os.path.exists(project_path):
            return project_path

        # 最后回退：当前工作目录
        cwd_path = os.path.join(os.getcwd(), filename)
        if os.path.exists(cwd_path):
            return cwd_path

        return None

    # ─── 数据加载 ───

    def load_from_file(self, file_path: str) -> int:
        """解析热词文件，返回有效条目数。

        强制 UTF-8，异常时 try GBK fallback。
        v3.0 新增：加载时检测热词冲突（同一 source 不同 target）。
        """
        self._entries = []
        self._current_category = ""

        content = self._read_file(file_path)
        if content is None:
            return 0

        # 冲突检测 dict
        seen_sources: Dict[str, Tuple[str, int]] = {}  # source → (target, line_num)
        line_num = 0

        for line in content.splitlines():
            line_num += 1
            entry = self._parse_hotword_line(line)
            if entry is None:
                continue

            # 冲突检测：同一 source 不同 target
            if entry.source in seen_sources:
                prev_target, prev_line = seen_sources[entry.source]
                if prev_target != entry.target:
                    logger.warning(
                        "热词冲突: source=%r 在第 %d 行和第 %d 行有不同的 target "
                        "(%r vs %r)，保留首次出现的值",
                        entry.source, prev_line, line_num,
                        prev_target, entry.target
                    )
                continue  # 保留首次出现

            seen_sources[entry.source] = (entry.target, line_num)
            self._entries.append(entry)

        logger.info("热词加载完成: %d 条 (来自 %s)", len(self._entries), file_path)
        return len(self._entries)

    def load_rules_from_file(self, file_path: str) -> int:
        """解析正则规则文件，返回成功编译的规则数。"""
        self._rules = []
        self._compiled_rules = []

        content = self._read_file(file_path)
        if content is None:
            return 0

        success_count = 0
        for line in content.splitlines():
            parsed = self._parse_rule_line(line)
            if parsed is None:
                continue

            pattern_str, replacement = parsed

            # 正则复杂度校验
            if not self._validate_regex_complexity(pattern_str):
                logger.warning("正则规则过于复杂或过长，已跳过: %s", pattern_str[:50])
                continue

            # 编译
            try:
                compiled = re.compile(pattern_str)
                self._rules.append((pattern_str, replacement))
                self._compiled_rules.append((compiled, replacement))
                success_count += 1
            except re.error as e:
                logger.warning("正则编译失败，已跳过: pattern=%s, error=%s",
                              pattern_str[:50], e)

        logger.info("正则规则加载完成: %d/%d 条 (来自 %s)",
                    success_count, len(self._rules), file_path)
        return success_count

    # ─── 数据消费接口 ───

    def get_text_hotword_map(self) -> Dict[str, str]:
        """返回用于文本替换的 {匹配: 替换} 字典。

        键按长度降序排列（长词优先，避免短词误匹配长词的子串）。
        仅返回 text_replace=True 的条目。
        """
        items = [
            (e.source, e.target)
            for e in self._entries
            if e.text_replace and len(e.source) >= self._min_word_length
        ]
        # 按长度降序排列
        items.sort(key=lambda x: len(x[0]), reverse=True)
        return dict(items)

    def get_model_hotword_list(self) -> List[str]:
        """返回用于 FunASR 原生热词的词列表。

        仅返回 model_hotword=True 的条目的 source。
        """
        return [
            e.source
            for e in self._entries
            if e.model_hotword and len(e.source) >= self._min_word_length
        ]

    def get_compiled_rules(self) -> List[Tuple[re.Pattern, str]]:
        """返回编译后的正则规则列表。"""
        return list(self._compiled_rules)

    def get_all_entries(self) -> List[HotwordEntry]:
        """返回所有热词条目（Web UI 使用）。"""
        return list(self._entries)

    def get_all_rules(self) -> List[Tuple[str, str]]:
        """返回所有正则规则（原始字符串），Web UI 使用。"""
        return list(self._rules)

    # ─── 运行时增删 ───

    def add_hotword(self, source: str, target: str = "",
                    category: str = "", text_replace: bool = True,
                    model_hotword: bool = True):
        """运行时追加热词（仅修改内存，不立即写文件）。

        如果 target 为空，表示 source 自身就是替换目标（同时用于文本替换和原生热词）。
        """
        if not source.strip():
            return

        entry = HotwordEntry(
            source=source.strip(),
            target=target.strip() if target else source.strip(),
            category=category or self._current_category,
            text_replace=text_replace,
            model_hotword=model_hotword,
        )
        self._entries.append(entry)

    def remove_hotword(self, source: str) -> bool:
        """运行时删除热词（仅修改内存）。

        删除第一条匹配的条目。返回是否成功删除。
        """
        for i, entry in enumerate(self._entries):
            if entry.source == source:
                self._entries.pop(i)
                return True
        return False

    def add_rule(self, pattern: str, replacement: str) -> bool:
        """运行时追加正则规则（仅修改内存）。

        返回是否成功编译。
        """
        if not self._validate_regex_complexity(pattern):
            logger.warning("正则规则过于复杂: %s", pattern[:50])
            return False

        try:
            compiled = re.compile(pattern)
            self._rules.append((pattern, replacement))
            self._compiled_rules.append((compiled, replacement))
            return True
        except re.error as e:
            logger.warning("正则编译失败: %s", e)
            return False

    def remove_rule(self, pattern: str) -> bool:
        """运行时删除正则规则（仅修改内存）。"""
        for i, (p, r) in enumerate(self._rules):
            if p == pattern:
                self._rules.pop(i)
                self._compiled_rules.pop(i)
                return True
        return False

    # ─── 持久化 ───

    def save_to_file(self) -> bool:
        """原子写入热词文件。"""
        content = self._format_hotwords_file()
        path = self.resolve_file_path(self._hotwords_file)
        if not path:
            # 写到项目根目录
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(project_root, self._hotwords_file)
        return self._atomic_write(path, content)

    def save_rules_to_file(self) -> bool:
        """原子写入正则规则文件。"""
        content = self._format_rules_file()
        path = self.resolve_file_path(self._rules_file)
        if not path:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(project_root, self._rules_file)
        return self._atomic_write(path, content)

    # ─── 内部方法 ───

    @staticmethod
    def _read_file(file_path: str) -> Optional[str]:
        """读取文件内容，UTF-8 优先，GBK fallback。"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            logger.warning("UTF-8 解码失败，尝试 GBK: %s", file_path)
            try:
                with open(file_path, "r", encoding="gbk") as f:
                    return f.read()
            except Exception as e:
                logger.error("文件读取失败: %s, error: %s", file_path, e)
                return None
        except Exception as e:
            logger.error("文件读取失败: %s, error: %s", file_path, e)
            return None

    @staticmethod
    def _parse_hotword_line(line: str) -> Optional[HotwordEntry]:
        """解析单行热词。支持 → 和 -> 两种分隔符。

        格式：
        - "匹配 → 替换" (U+2192)
        - "匹配 -> 替换" (ASCII)
        - "词" (无箭头，同时用于文本替换和原生热词)
        - "# 注释" → None
        - "[分类]" → None (仅设置分类标记)
        - 空行 → None
        """
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None

        # 分类标记
        if stripped.startswith("[") and stripped.endswith("]"):
            # 返回特殊标记，由调用方处理
            return None  # 分类标记在 load_from_file 中处理

        # 解析分隔符
        target = ""
        text_replace = True
        model_hotword = True

        if "\u2192" in stripped:  # → (U+2192)
            parts = stripped.split("\u2192", 1)
            source = parts[0].strip()
            target = parts[1].strip()
            # 有显式箭头：明确用于文本替换
            model_hotword = False
        elif "->" in stripped:
            parts = stripped.split("->", 1)
            source = parts[0].strip()
            target = parts[1].strip()
            model_hotword = False
        else:
            # 无箭头：词本身同时用于文本替换和原生热词
            source = stripped
            target = stripped

        if not source:
            return None

        return HotwordEntry(
            source=source,
            target=target,
            text_replace=text_replace,
            model_hotword=model_hotword,
        )

    @staticmethod
    def _parse_rule_line(line: str) -> Optional[Tuple[str, str]]:
        """解析单行正则规则。

        格式：正则表达式 = 替换字符串（两边空格自动 trim）
        """
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None

        if "=" not in stripped:
            return None

        idx = stripped.index("=")
        pattern_str = stripped[:idx].rstrip()
        replacement = stripped[idx + 1:].lstrip()

        if not pattern_str:
            return None

        return (pattern_str, replacement)

    @staticmethod
    def _validate_regex_complexity(pattern_str: str) -> bool:
        """正则复杂度校验（防 ReDoS）。

        - 限制正则长度 500 字符
        - 检测连续 3+ 量词嵌套（简易启发式）
        """
        MAX_LENGTH = 500
        if len(pattern_str) > MAX_LENGTH:
            return False
        # 检测连续 3+ 量词嵌套
        if re.search(
            r'(\+|\*|\{[^}]+\})\s*(\+|\*|\{[^}]+\})\s*(\+|\*|\{[^}]+\})',
            pattern_str
        ):
            return False
        return True

    @staticmethod
    def _atomic_write(file_path: str, content: str) -> bool:
        """原子写入：写入临时文件 → os.replace。"""
        try:
            dir_path = os.path.dirname(file_path) or '.'
            os.makedirs(dir_path, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(content)
                os.replace(tmp_path, file_path)  # 原子替换
                return True
            except Exception:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                raise
        except Exception as e:
            logger.error("原子写入失败 %s: %s", file_path, e)
            return False

    def _format_hotwords_file(self) -> str:
        """格式化热词条目为文件内容。"""
        lines = ["# 热词文件 — 每行一条，# 开头为注释", ""]
        current_cat = ""
        for entry in self._entries:
            if entry.category and entry.category != current_cat:
                current_cat = entry.category
                lines.append(f"[{current_cat}]")
            if entry.source == entry.target:
                # 无箭头：同时用于文本替换和原生热词
                lines.append(entry.source)
            elif not entry.model_hotword:
                # 有箭头：仅文本替换
                lines.append(f"{entry.source} -> {entry.target}")
            else:
                lines.append(f"{entry.source} -> {entry.target}")
        return "\n".join(lines) + "\n"

    def _format_rules_file(self) -> str:
        """格式化正则规则为文件内容。"""
        lines = ["# 正则规则文件", ""]
        for pattern_str, replacement in self._rules:
            lines.append(f"{pattern_str} = {replacement}")
        return "\n".join(lines) + "\n"
