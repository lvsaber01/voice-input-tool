"""文本处理管线 — 正则替换 → 热词替换。

仅消费 HotwordManager 的数据，不自己加载文件。
Engine 通过 process() 方法调用。

v1.0 — 对应设计文档 v3.0
"""

import re
import threading
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from core.hotword import HotwordManager

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    """管线处理结果"""
    text: str              # 处理后的文本
    is_changed: bool       # 是否被修改（用于日志）


class TextPipeline:
    """文本处理管线：正则 → 热词。

    仅消费 HotwordManager 的数据，不自己加载文件。

    v3.0 特性：
    - 大小写不敏感查找改为预构建字典 O(1)
    - reload 带 500ms debounce
    - 双层容错（process 顶层 + 逐条正则）
    """

    _DEBOUNCE_INTERVAL = 0.5  # 500ms

    def __init__(self, hotword_manager: HotwordManager,
                 enabled: bool = True, case_sensitive: bool = False):
        self._hotword_manager = hotword_manager
        self._regex_rules: List[Tuple[re.Pattern, str]] = []
        self._hotword_regex: Optional[re.Pattern] = None
        self._hotword_map: Dict[str, str] = {}            # 匹配 → 替换
        self._hotword_map_lower: Dict[str, str] = {}      # lower → 替换（O(1) 查找）
        self._enabled: bool = enabled
        self._case_sensitive: bool = case_sensitive
        self._on_reload_callbacks: List[Callable] = []
        self._reload_timer: Optional[threading.Timer] = None

        # 初始加载
        self.reload()

    # ─── 公开接口 ───

    def reload(self):
        """从 HotwordManager 重新获取数据，原子替换引用。"""
        try:
            # 获取最新数据
            new_rules = self._hotword_manager.get_compiled_rules()
            new_map = self._hotword_manager.get_text_hotword_map()

            # 构建预编译热词正则（一次性扫描，长词优先）
            new_regex = self._build_hotword_regex(new_map)

            # 构建大小写不敏感查找字典
            new_map_lower = {k.lower(): v for k, v in new_map.items()}

            # 原子替换引用（避免 reload 竞态）
            self._regex_rules = new_rules
            self._hotword_map = new_map
            self._hotword_map_lower = new_map_lower
            self._hotword_regex = new_regex

            logger.info("Pipeline reload 完成: %d 条规则, %d 条热词",
                       len(new_rules), len(new_map))
        except Exception as e:
            logger.error("Pipeline reload 失败，保留旧数据: %s", e)
            # 不替换引用，保留旧数据继续使用

        # 触发回调（如同步 FunASR 热词）
        for cb in self._on_reload_callbacks:
            try:
                cb()
            except Exception as e:
                logger.warning("Reload callback 异常: %s", e)

    def schedule_reload(self):
        """带防抖的 reload（Web API 连续保存时合并）。"""
        if self._reload_timer:
            self._reload_timer.cancel()
        self._reload_timer = threading.Timer(
            self._DEBOUNCE_INTERVAL, self._do_reload
        )
        self._reload_timer.daemon = True
        self._reload_timer.start()

    def _do_reload(self):
        self._reload_timer = None
        self.reload()

    def register_reload_callback(self, callback: Callable):
        """注册 reload 完成后的回调。"""
        self._on_reload_callbacks.append(callback)

    def process(self, text: str) -> ProcessResult:
        """执行处理链：正则替换 → 热词替换。

        顶层 try-catch，异常时降级返回原文。
        """
        if not self._enabled or not text:
            return ProcessResult(text=text, is_changed=False)

        original = text
        try:
            text = self._apply_regex(text)
            text = self._apply_hotwords(text)
            return ProcessResult(
                text=text,
                is_changed=(text != original)
            )
        except Exception as e:
            logger.error("Pipeline 处理异常，降级返回原文: %s", e, exc_info=True)
            return ProcessResult(text=original, is_changed=False)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        logger.info("Pipeline %s", "启用" if value else "禁用")

    # ─── 内部方法 ───

    def _apply_regex(self, text: str) -> str:
        """逐条应用正则规则。单条异常不影响其他规则。"""
        for pattern, replacement in self._regex_rules:
            try:
                text = pattern.sub(replacement, text)
            except Exception as e:
                logger.warning("正则规则执行异常，跳过: pattern=%s, error=%s",
                              pattern.pattern[:50], e)
        return text

    def _build_hotword_regex(self, hotword_map: Dict[str, str]) -> Optional[re.Pattern]:
        """构建预编译热词正则。

        使用 re.sub + 预编译交替正则，一次扫描完成所有替换。
        长词优先排序，避免短词误匹配长词的子串。
        支持 case_sensitive 开关。
        """
        if not hotword_map:
            return None

        # 按长度降序排列（长词优先匹配）
        sources = sorted(hotword_map.keys(), key=len, reverse=True)
        pattern = '|'.join(re.escape(s) for s in sources)
        flags = 0 if self._case_sensitive else re.IGNORECASE
        return re.compile(pattern, flags)

    def _apply_hotwords(self, text: str) -> str:
        """使用预编译正则替换热词。一次扫描，O(n) 复杂度。

        v3.0 优化：大小写不敏感查找改为 O(1) 字典查找。
        """
        if not self._hotword_regex:
            return text

        if self._case_sensitive:
            hotword_map = self._hotword_map
            def _replace_cs(match):
                return hotword_map.get(match.group(0), match.group(0))
            return self._hotword_regex.sub(_replace_cs, text)
        else:
            hotword_map_lower = self._hotword_map_lower
            def _replace_ci(match):
                return hotword_map_lower.get(match.group(0).lower(), match.group(0))
            return self._hotword_regex.sub(_replace_ci, text)
