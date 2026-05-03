"""文本处理管线 — 插件化架构。

可注册、可配置、可排序的插件步骤，支持 COW 读取和线程安全写入。

内置步骤：
- PunctuationStep（第零层：标点恢复）
- PhonemeStep（第一层：音素纠错）
- RegexStep（第二层：正则替换）
- HotwordStep（第三层：热词替换）

v4.0 — 插件化重构（行为不变）
v3.0 — 大小写不敏感 + reload debounce + 双层容错
v2.0 — 新增音素匹配纠错层（第一层）
v1.0 — 对应设计文档 v3.0
"""

import re
import threading
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from core.hotword import HotwordManager
from core.pipeline_step import (
    PipelineStep,
    ProcessContext,
    ContextualStep,
    StepNames,
)

# 音素纠错器延迟导入（pypinyin 可能不可用）
try:
    from core.phoneme.phoneme_corrector import PhonemeCorrector
    _PHONEME_AVAILABLE = True
except ImportError:
    _PHONEME_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    """管线处理结果"""
    text: str              # 处理后的文本
    is_changed: bool       # 是否被修改（用于日志）
    phoneme_matches: list  # 音素匹配结果（可选，默认空列表）


# ─── 内置步骤实现 ───


class PunctuationStep(ContextualStep):
    """标点恢复步骤（第零层）。

    由 engine.py 注入标点恢复器实例。
    继承 ContextualStep，process_context() 调用 process()。
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name=StepNames.PUNCTUATION, priority=10, enabled=enabled)
        self._restorer = None

    def set_restorer(self, restorer) -> None:
        """设置标点恢复器。"""
        self._restorer = restorer

    def process(self, text: str) -> str:
        if not self._restorer:
            return text
        try:
            return self._restorer.restore(text)
        except Exception as e:
            logger.warning("标点恢复异常，跳过: %s", e)
            return text


class PhonemeStep(ContextualStep):
    """音素纠错步骤（第一层）。

    继承 ContextualStep，重写 process_context() 将匹配结果写入上下文。
    """

    def __init__(self, phoneme_corrector: Optional['PhonemeCorrector'] = None,
                 enabled: bool = True):
        super().__init__(name=StepNames.PHONEME, priority=20, enabled=enabled)
        self._phoneme_corrector = phoneme_corrector

    @property
    def phoneme_corrector(self) -> Optional['PhonemeCorrector']:
        return self._phoneme_corrector

    @phoneme_corrector.setter
    def phoneme_corrector(self, value: Optional['PhonemeCorrector']):
        self._phoneme_corrector = value

    def process(self, text: str) -> str:
        """不带上下文的处理（兼容旧接口）。"""
        if not self._phoneme_corrector or not self._phoneme_corrector.enabled:
            return text
        try:
            result = self._phoneme_corrector.correct(text)
            return result.text
        except Exception as e:
            logger.warning("音素纠错异常，跳过: %s", e)
            return text

    def process_context(self, ctx: ProcessContext) -> str:
        """带上下文处理，将 phoneme_matches 写入 ctx.meta。"""
        if not self._phoneme_corrector or not self._phoneme_corrector.enabled:
            ctx.set("phoneme_matches", [])
            return ctx.text
        try:
            result = self._phoneme_corrector.correct(ctx.text)
            ctx.set("phoneme_matches", result.matches)
            return result.text
        except Exception as e:
            logger.warning("音素纠错异常，跳过: %s", e)
            ctx.set("phoneme_matches", [])
            return ctx.text

    def reload(self) -> None:
        pass  # 热词由 HotwordManager 管理


class RegexStep(PipelineStep):
    """正则替换步骤（第二层）。

    逐条应用正则规则，单条异常不影响其他规则。
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name=StepNames.REGEX, priority=30, enabled=enabled)
        self._regex_rules: List[Tuple[re.Pattern, str]] = []

    def set_rules(self, rules: List[Tuple[re.Pattern, str]]) -> None:
        """设置正则规则列表。"""
        self._regex_rules = rules

    @property
    def rules(self) -> List[Tuple[re.Pattern, str]]:
        return self._regex_rules

    def process(self, text: str) -> str:
        for pattern, replacement in self._regex_rules:
            try:
                text = pattern.sub(replacement, text)
            except Exception as e:
                logger.warning("正则规则执行异常，跳过: pattern=%s, error=%s",
                              pattern.pattern[:50], e)
        return text


class HotwordStep(PipelineStep):
    """热词替换步骤（第三层）。

    使用预编译正则一次扫描完成所有热词替换。
    """

    def __init__(self, case_sensitive: bool = False, enabled: bool = True):
        super().__init__(name=StepNames.HOTWORD, priority=40, enabled=enabled)
        self._hotword_regex: Optional[re.Pattern] = None
        self._hotword_map: Dict[str, str] = {}
        self._hotword_map_lower: Dict[str, str] = {}
        self._case_sensitive = case_sensitive

    def set_hotwords(self, hotword_map: Dict[str, str],
                     hotword_regex: Optional[re.Pattern] = None) -> None:
        """设置热词数据。"""
        self._hotword_map = hotword_map
        self._hotword_map_lower = {k.lower(): v for k, v in hotword_map.items()}
        self._hotword_regex = hotword_regex

    def process(self, text: str) -> str:
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


# ─── TextPipeline（插件化） ───


class TextPipeline:
    """文本处理管线 — 插件化架构。

    步骤按 priority 排序执行，支持动态 add/remove/get。
    COW（Copy-on-Write）读取：process() 读取快照，写操作在锁保护下修改。

    v4.0 特性：
    - 插件化步骤管理（add_step/remove_step/get_step）
    - COW + _write_lock 线程安全
    - ProcessContext 上下文传递
    - 保持所有公开接口不变

    v3.0 特性（保留）：
    - 大小写不敏感查找改为预构建字典 O(1)
    - reload 带 500ms debounce
    - 双层容错（process 顶层 + 逐条正则）
    """

    _DEBOUNCE_INTERVAL = 0.5  # 500ms

    def __init__(self, hotword_manager: HotwordManager,
                 enabled: bool = True, case_sensitive: bool = False,
                 phoneme_threshold: float = 0.7, phoneme_enabled: bool = True):
        self._hotword_manager = hotword_manager
        self._enabled: bool = enabled
        self._case_sensitive: bool = case_sensitive
        self._on_reload_callbacks: List[Callable] = []
        self._reload_timer: Optional[threading.Timer] = None
        self._last_phoneme_matches: list = []

        # 标点恢复器（由 engine.py 通过 setter 注入）— 保留兼容
        self._punctuation_restorer = None

        # COW 步骤列表 + 写锁
        self._steps: List[PipelineStep] = []
        self._write_lock = threading.Lock()

        # 音素纠错器
        self._phoneme_corrector: Optional['PhonemeCorrector'] = None
        if _PHONEME_AVAILABLE and phoneme_enabled:
            try:
                self._phoneme_corrector = PhonemeCorrector(
                    threshold=phoneme_threshold,
                    enabled=True,
                )
            except Exception as e:
                logger.warning("音素纠错器创建失败，已降级: %s", e)
                self._phoneme_corrector = None
        elif not _PHONEME_AVAILABLE:
            logger.debug("pypinyin 不可用，音素纠错已禁用")

        # 初始化内置步骤
        self._init_builtin_steps()

        # 初始加载
        self.reload()

    def _init_builtin_steps(self) -> None:
        """初始化 4 个内置步骤。"""
        punctuation = PunctuationStep(enabled=True)
        phoneme = PhonemeStep(
            phoneme_corrector=self._phoneme_corrector,
            enabled=self._phoneme_corrector is not None,
        )
        regex = RegexStep(enabled=True)
        hotword = HotwordStep(
            case_sensitive=self._case_sensitive,
            enabled=True,
        )
        self._steps = [punctuation, phoneme, regex, hotword]

        # 向后兼容别名（测试中直接访问这些属性）
        self._regex_rules = regex.rules
        self._hotword_regex = None
        self._hotword_map = {}
        self._hotword_map_lower = {}

    # ─── 插件管理接口 ───

    def add_step(self, step: PipelineStep) -> None:
        """添加步骤（检查 name 唯一性，按 priority 排序）。

        Args:
            step: 要添加的步骤
        Raises:
            ValueError: 步骤 name 已存在
        """
        with self._write_lock:
            for s in self._steps:
                if s.name == step.name:
                    raise ValueError(f"步骤 '{step.name}' 已存在")
            self._steps.append(step)
            self._steps.sort(key=lambda s: s.priority)

    def remove_step(self, name: str) -> bool:
        """移除步骤。

        Args:
            name: 步骤名称
        Returns:
            是否成功移除
        """
        with self._write_lock:
            for i, s in enumerate(self._steps):
                if s.name == name:
                    self._steps.pop(i)
                    return True
            return False

    def get_step(self, name: str) -> Optional[PipelineStep]:
        """获取步骤（COW 读取，无需加锁）。

        Args:
            name: 步骤名称（建议使用 StepNames 常量）
        Returns:
            步骤实例，不存在返回 None
        """
        for s in self._steps:
            if s.name == name:
                return s
        return None

    @property
    def steps(self) -> List[PipelineStep]:
        """返回当前步骤列表的快照（COW 读取）。"""
        return list(self._steps)

    # ─── 公开接口（保持不变） ───

    def set_punctuation_restorer(self, restorer) -> None:
        """设置标点恢复器（engine.py 初始化后调用）。"""
        self._punctuation_restorer = restorer
        # 同步到 PunctuationStep
        punct_step = self.get_step(StepNames.PUNCTUATION)
        if punct_step and isinstance(punct_step, PunctuationStep):
            punct_step.set_restorer(restorer)

    def reload(self):
        """从 HotwordManager 重新获取数据，原子替换引用。"""
        try:
            # 获取最新数据
            new_rules = self._hotword_manager.get_compiled_rules()
            new_map = self._hotword_manager.get_text_hotword_map()

            # 构建预编译热词正则（一次性扫描，长词优先）
            new_regex = self._build_hotword_regex(new_map)

            # 更新步骤数据
            regex_step = self.get_step(StepNames.REGEX)
            if regex_step and isinstance(regex_step, RegexStep):
                regex_step.set_rules(new_rules)
                self._regex_rules = regex_step.rules  # 同步别名

            hotword_step = self.get_step(StepNames.HOTWORD)
            if hotword_step and isinstance(hotword_step, HotwordStep):
                hotword_step.set_hotwords(new_map, new_regex)
                self._hotword_regex = hotword_step._hotword_regex  # 同步别名
                self._hotword_map = hotword_step._hotword_map
                self._hotword_map_lower = hotword_step._hotword_map_lower

            logger.info("Pipeline reload 完成: %d 条规则, %d 条热词",
                       len(new_rules), len(new_map))
        except Exception as e:
            logger.error("Pipeline reload 失败，保留旧数据: %s", e)
            # 不替换引用，保留旧数据继续使用

        # 加载音素热词
        self._load_phoneme_hotwords()

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
        """执行处理链：按 priority 顺序执行所有启用的步骤。

        顶层 try-catch，异常时降级返回原文。
        """
        if not self._enabled or not text:
            return ProcessResult(text=text, is_changed=False, phoneme_matches=[])

        original = text
        try:
            # 创建上下文
            ctx = ProcessContext(text=text)

            # COW 快照读取
            steps_snapshot = self._steps

            for step in steps_snapshot:
                if not step.enabled:
                    continue
                try:
                    if isinstance(step, ContextualStep):
                        ctx.text = step.process_context(ctx)
                    else:
                        ctx.text = step.process(ctx.text)
                except Exception as e:
                    logger.warning("步骤 '%s' 执行异常，跳过: %s", step.name, e)

            phoneme_matches = ctx.get("phoneme_matches", [])
            return ProcessResult(
                text=ctx.text,
                is_changed=(ctx.text != original),
                phoneme_matches=phoneme_matches,
            )
        except Exception as e:
            logger.error("Pipeline 处理异常，降级返回原文: %s", e, exc_info=True)
            return ProcessResult(text=original, is_changed=False, phoneme_matches=[])

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        logger.info("Pipeline %s", "启用" if value else "禁用")

    # ─── 向后兼容内部方法 ───

    def _apply_punctuation(self, text: str) -> str:
        """向后兼容：标点恢复层。"""
        punct_step = self.get_step(StepNames.PUNCTUATION)
        if punct_step and isinstance(punct_step, PunctuationStep):
            return punct_step.process(text)
        return text

    def _apply_phoneme(self, text: str) -> tuple:
        """向后兼容：音素纠错层。"""
        phoneme_step = self.get_step(StepNames.PHONEME)
        if phoneme_step and isinstance(phoneme_step, PhonemeStep):
            result_text = phoneme_step.process(text)
            return result_text, self._last_phoneme_matches
        return text, []

    def _apply_regex(self, text: str) -> str:
        """向后兼容：正则替换层。"""
        regex_step = self.get_step(StepNames.REGEX)
        if regex_step and isinstance(regex_step, RegexStep):
            return regex_step.process(text)
        return text

    def _apply_hotwords(self, text: str) -> str:
        """向后兼容：热词替换层。"""
        hotword_step = self.get_step(StepNames.HOTWORD)
        if hotword_step and isinstance(hotword_step, HotwordStep):
            return hotword_step.process(text)
        return text

    # ─── 内部方法 ───

    def _load_phoneme_hotwords(self) -> None:
        """加载音素热词文件。"""
        if not self._phoneme_corrector or not self._phoneme_corrector.enabled:
            return
        phoneme_path = self._hotword_manager.resolve_file_path('hotwords-phoneme.txt')
        if phoneme_path:
            try:
                count = self._phoneme_corrector.update_from_file(phoneme_path)
                logger.info("音素热词加载完成: %d 条", count)
            except Exception as e:
                logger.warning("音素热词加载失败，已降级: %s", e)

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
