# 后处理增强设计文档 — Emoji 清理 + 文件监控 + 标点恢复

> **版本**: v5.0  
> **状态**: 待评审（第 5 轮）  
> **变更**: v4 代码结构重写（修复 P0 致命缩进断裂 + P1 方法缺失）  
> **日期**: 2026-05-02  
> **项目**: voice-input-tool  

---

## 一、需求概述

### 1.1 三个功能点

| # | 功能 | 优先级 | 触发场景 |
|---|------|--------|----------|
| F1 | SenseVoice 输出 emoji 清理 | 高 | SenseVoice 模式下 STT 输出含 emoji 字符 |
| F2 | 热词文件 watchdog 自动 reload | 高 | 用户编辑热词文件后无需重启工具 |
| F3 | ct-punc 标点恢复（SenseVoice） | 中 | SenseVoice 输出无标点，需要自动添加 |

### 1.2 背景与动机

- **F1**：SenseVoice 模型输出常包含 `<|EMO_xxx|>` 标记和实际 emoji 字符（如 😊😂），前者已有 `rich_transcription_postprocess` 处理，但后者未被清除
- **F2**：当前热词文件修改后需要通过 Web 管理界面手动 reload 或重启工具，缺乏文件系统级别的自动检测
- **F3**：SenseVoice 输出无标点（如"你好世界"而非"你好，世界！"），影响可读性；Paraformer 已自带标点无需处理

---

## 二、现有架构分析

### 2.1 TextPipeline 处理链

```
STT 原文 → [标点恢复] → [音素纠错] → [正则替换] → [热词替换] → 注入
                ↑ F3 新增层（第零层）
```

### 2.2 相关文件

| 文件 | 职责 |
|------|------|
| `core/stt_funasr.py` | FunASR 引擎，含 `_postprocess_sensevoice()` |
| `core/text_pipeline.py` | 三层处理链 + reload 机制（`_DEBOUNCE_INTERVAL = 0.5`） |
| `core/hotword.py` | 热词管理（load/rules/replace） |
| `core/engine.py` | 核心调度引擎，初始化/关闭子模块 |
| `core/config.py` | 配置管理 |

### 2.3 现有 reload 机制

- `TextPipeline.reload()` — 从文件重新加载规则和热词
- `TextPipeline.schedule_reload()` — 带防抖（500ms）的 reload
- `TextPipeline.register_reload_callback()` — reload 后回调（同步 FunASR 热词）
- **触发方式**：仅 Web 管理界面的 API 调用

---

## 三、F1：SenseVoice Emoji 清理

### 3.1 设计方案

在 `core/stt_funasr.py` 的 `_postprocess_sensevoice()` 方法中，在 `rich_transcription_postprocess` 处理**之后**，追加 emoji 字符清除步骤。

#### 处理顺序

```
原始输出 → rich_transcription_postprocess → emoji 清除 → 返回
```

#### Emoji 正则定义

```python
# core/stt_funasr.py 模块级常量

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
    "\U0001F680-\U0001F6FF"  # Transport and Map
    "\U0001F700-\U0001F77F"  # Alchemical Symbols
    "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess Symbols
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U0001FAE0-\U0001FAEF"  # Symbols for Legacy Computing (Unicode 13+)
    "\U00002702-\U000027B0"  # Dingbats
    "\U0001F1E0-\U0001F1FF"  # Flags (Regional Indicator)
    "\U0001F3FB-\U0001F3FF"  # Skin Tone Modifiers
    "\U0000200D"              # ZWJ (Zero Width Joiner)
    "]+",
    flags=re.UNICODE,
)
```

#### 修改位置

`core/stt_funasr.py` → `_postprocess_sensevoice()` 方法：

```python
@staticmethod
def _postprocess_sensevoice(raw_text: Optional[str]) -> str:
    if not raw_text:
        return ""
    
    # 第一步：rich_transcription_postprocess（去除 <|EMO_xxx|> 等标记）
    try:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
        text = rich_transcription_postprocess(raw_text)
    except (ImportError, AttributeError):
        KNOWN_MARKERS = r'<\|(?:EMO_\w+|Event_\w+|nospeech|Speech|woitn|BGM|LAUGH|[a-z]{2,3})\|>'
        text = re.sub(KNOWN_MARKERS, '', raw_text)
    
    # 第二步：清除残留 emoji 字符
    text = _EMOJI_PATTERN.sub('', text)
    text = text.strip()
    
    return text
```

### 3.2 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 位置 | `_postprocess_sensevoice()` 而非 TextPipeline | emoji 是 STT 输出问题，应在源头清理 |
| 覆盖范围 | 精确 Unicode 范围 + ZWJ/肤色修饰符/Unicode 13+ | 覆盖常见 emoji 且不误伤 CJK 字符 |
| 性能 | 模块级预编译正则，零额外依赖 | 单次调用，性能影响可忽略 |
| 对非 SenseVoice 影响 | 无 | 仅在 `_postprocess_sensevoice()` 中执行 |

---

## 四、F2：热词文件 Watchdog 自动 Reload

### 4.1 设计方案

新增 `core/file_watcher.py`，封装 watchdog 库的文件监控逻辑。由 `engine.py` 初始化和生命周期管理。

#### 架构

```
engine.py
  └── FileWatcher (daemon thread)
        ├── 监控 hotwords.txt 父目录
        ├── 监控 hotwords-phoneme.txt 父目录
        ├── 监控 rules.txt 父目录
        └── 文件变更/创建 → 防抖 → 回调 reload
              └── TextPipeline.schedule_reload()
```

#### 完整模块代码

```python
# core/file_watcher.py

import os
import logging
import threading
from typing import Dict, Callable, Optional

from watchdog.observers import Observer
from watchdog.events import (
    FileSystemEventHandler,
    FileModifiedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
)

logger = logging.getLogger(__name__)

_FORBIDDEN_DIRS = {"/", os.path.expanduser("~")}


class _FileChangeHandler(FileSystemEventHandler):
    """watchdog 事件处理器。"""

    def __init__(self, on_event: Callable):
        self._on_event = on_event

    def on_modified(self, event):
        self._on_event(event)

    def on_created(self, event):
        self._on_event(event)

    def on_deleted(self, event):
        self._on_event(event)


class FileWatcher:
    """基于 watchdog 的文件变更监控，带防抖。

    生命周期: start() → 运行 → stop()
    线程: daemon thread（不阻塞主线程退出）

    内部使用 watchdog.observers.Observer + FileSystemEventHandler。
    防抖通过 threading.Timer 实现（Timer 内部有锁，cancel/start 线程安全）。

    统一采用「监控父目录 + basename 过滤」模式，无论文件是否存在。
    """

    def __init__(
        self,
        paths: Dict[str, Callable[[], None]],
        debounce_seconds: float = 1.0,
    ):
        """初始化文件监控。

        Args:
            paths: {文件绝对路径: 变更回调} 映射。
                   文件不存在时仍然监控其父目录，等待创建。
            debounce_seconds: 防抖间隔（秒），同一文件多次变更合并为一次。
        """
        self._paths: Dict[str, Callable[[], None]] = dict(paths)
        self._debounce_seconds = debounce_seconds
        self._timers: Dict[str, threading.Timer] = {}
        self._observer: Optional[Observer] = None
        self._started = False
        # {目录路径: {文件 basename: 回调}}
        self._dir_watches: Dict[str, Dict[str, Callable[[], None]]] = {}

    def start(self) -> None:
        """启动文件监控（daemon thread）。

        对每个路径，统一监控其父目录，通过 basename 过滤事件。
        同一目录只 schedule 一次（避免重复）。
        空路径时不启动。
        """
        if not self._paths:
            logger.info("FileWatcher: 无监控路径，不启动")
            return

        self._observer = Observer()
        handler = _FileChangeHandler(self._on_file_event)

        dirs_to_watch: set = set()

        for file_path, callback in self._paths.items():
            abs_path = os.path.abspath(file_path)
            parent = os.path.dirname(abs_path)

            if parent in _FORBIDDEN_DIRS:
                logger.warning("FileWatcher: 路径 %s 的父目录为根目录，跳过", file_path)
                continue

            basename = os.path.basename(abs_path)
            self._dir_watches.setdefault(parent, {})[basename] = callback
            dirs_to_watch.add(parent)

            if not os.path.isfile(abs_path):
                logger.info("FileWatcher: %s 不存在，监控目录 %s 等待创建", basename, parent)

        # 按目录去重，只 schedule 一次
        for dir_path in sorted(dirs_to_watch):
            self._observer.schedule(handler, dir_path, recursive=False)

        if self._dir_watches:
            self._observer.daemon = True
            self._observer.start()
            self._started = True
            logger.info("FileWatcher: 已启动，监控 %d 个目录", len(self._dir_watches))
        else:
            logger.warning("FileWatcher: 无有效监控路径")

    def stop(self) -> None:
        """停止文件监控，等待线程退出（超时 5s）。"""
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None

        self._started = False
        logger.info("FileWatcher: 已停止")

    def _on_file_event(self, event) -> None:
        """处理文件变更事件（由 watchdog handler 调用）。"""
        if event.is_directory:
            return

        basename = os.path.basename(event.src_path)
        parent = os.path.dirname(os.path.abspath(event.src_path))

        watches = self._dir_watches.get(parent, {})
        callback = watches.get(basename)
        if callback is None:
            return

        # 文件删除：不触发 reload，记录日志，继续监控目录等待重建
        if isinstance(event, FileDeletedEvent):
            logger.info("FileWatcher: %s 已删除，继续监控等待重建", basename)
            return

        # 防抖：FileModifiedEvent / FileCreatedEvent
        key = f"{parent}:{basename}"
        if key in self._timers:
            self._timers[key].cancel()

        self._timers[key] = threading.Timer(
            self._debounce_seconds, self._safe_call, args=(callback,)
        )
        self._timers[key].daemon = True
        self._timers[key].start()

    @staticmethod
    def _safe_call(callback: Callable[[], None]) -> None:
        """安全执行回调，异常不影响监控。"""
        try:
            callback()
        except Exception as e:
            logger.error("FileWatcher 回调异常: %s", e)

    @property
    def is_watching(self) -> bool:
        return self._started and self._observer is not None and self._observer.is_alive()
```

#### engine.py 集成

```python
# engine.py — _init_hotword_pipeline 中新增

self._file_watcher = None
try:
    from core.file_watcher import FileWatcher

    reload_fn = (
        lambda: self._text_pipeline.schedule_reload()
        if self._text_pipeline
        else None
    )
    watch_paths = {}

    if self._hotword_manager:
        for filename in ["hotwords.txt", "hotwords-phoneme.txt", "rules.txt"]:
            # V5: 构造期望路径（不检查 exists），确保文件不存在也能监控目录
            file_dir = self._hotword_manager.data_dir  # HotwordManager 的数据目录
            if file_dir:
                watch_paths[str(file_dir / filename)] = reload_fn

    if watch_paths:
        self._file_watcher = FileWatcher(watch_paths, debounce_seconds=1.0)
        self._file_watcher.start()
        logger.info("文件监控已启动，监控 %d 个文件", len(watch_paths))
except ImportError:
    logger.warning("watchdog 未安装，文件监控不可用。pip install watchdog")
except Exception as e:
    logger.warning("文件监控启动失败: %s", e)
```

> **V5 说明**：不再调用 `HotwordManager.resolve_file_path()`（该方法文件不存在时返回 None 导致监控失效），改为直接使用 `data_dir` 属性构造路径。需在 `HotwordManager` 中确认或新增 `data_dir` 属性（`pathlib.Path`，指向热词文件所在目录）。

```python
# engine.py — shutdown 中新增
if self._file_watcher:
    self._file_watcher.stop()
    self._file_watcher = None
```

#### HotwordManager 新增 data_dir 属性

```python
# core/hotword.py — HotwordManager 类中新增

@property
def data_dir(self) -> Optional[pathlib.Path]:
    """热词文件所在目录路径。
    
    用于 FileWatcher 构造监控路径（文件可能尚未创建）。
    Returns:
        pathlib.Path 或 None
    """
    if self._data_dir:  # 已有内部属性则直接返回
        return self._data_dir
    # 退回：从已知文件路径推断
    for name in ["hotwords.txt", "hotwords-phoneme.txt", "rules.txt"]:
        p = self.resolve_file_path(name)
        if p:
            return pathlib.Path(p).parent
    return None
```

#### watchdog 依赖

- 新增依赖：`watchdog>=3.0.0`（加到 `requirements/base.txt`）
- `watchdog` 导入失败时优雅降级：跳过文件监控，不影响其他功能

### 4.2 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 库选择 | watchdog | Python 标准文件监控库，跨平台，轻量 |
| 防抖 | 1.0s threading.Timer | Timer 内部有锁，cancel/start 线程安全 |
| 回调 | `schedule_reload()` 复用 | 已有 500ms 防抖，避免重复实现 |
| daemon thread | 是 | 不阻塞主线程退出 |
| 降级 | ImportError 时跳过 | watchdog 不可用时系统正常运行 |
| 路径构造 | `data_dir / filename` | V5: 不检查 exists，确保监控始终生效 |
| 目录监控安全 | 禁止监控根目录 | 避免监控 `/` 或 `~` 导致性能问题 |
| 同目录去重 | `dirs_to_watch` 集合 + sorted | 避免 Observer 重复 schedule |

---

## 五、F3：ct-punc 标点恢复

### 5.1 设计方案

新增 `core/punctuation.py`，封装 FunASR 的 ct-punc 模型。仅在 SenseVoice 和 Fun-ASR-Nano 模式下启用。

#### 完整模块代码

```python
# core/punctuation.py

import threading
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class PunctuationRestorer:
    """基于 ct-punc 模型的自动标点恢复。

    延迟加载：首次调用时加载模型。
    线程安全：
      - 加载阶段：threading.Lock + double-check locking
      - 推理阶段：threading.RLock（推理时可能间接触发加载）
    降级友好：模型不可用时返回原文。
    模型复用：优先复用 FunASR 引擎已加载的 ct-punc 实例。
    """

    def __init__(self, enabled: bool = True, device: Optional[str] = None):
        self._lock = threading.RLock()
        self._model = None
        self._loaded = False
        self._enabled = enabled
        self._device = device
        self._shared_model = False

    def set_shared_model(self, model) -> None:
        """注入外部共享的 ct-punc 模型实例。

        Args:
            model: FunASR AutoModel 实例（ct-punc）
        """
        with self._lock:
            self._shared_model = True
            self._model = model
            self._loaded = model is not None
            if model is not None:
                logger.info("PunctuationRestorer: 使用共享 ct-punc 模型实例")

    def restore(self, text: str) -> str:
        """为文本添加标点（线程安全）。

        Args:
            text: 无标点的文本

        Returns:
            添加标点后的文本（模型不可用时返回原文）
        """
        if not self._enabled or not text:
            return text

        with self._lock:
            if not self._loaded:
                self._load_model()
            if not self._loaded:
                return text

            try:
                result = self._model.generate(input=text)
                if result and len(result) > 0:
                    return (
                        result[0]["text"]
                        if isinstance(result[0], dict)
                        else str(result[0])
                    )
            except Exception as e:
                logger.warning("标点恢复失败: %s", e)

        return text

    def _load_model(self) -> None:
        """加载 ct-punc 模型（需在 _lock 内调用）。"""
        try:
            t0 = time.monotonic()
            from funasr import AutoModel

            self._model = AutoModel(model="ct-punc", device=self._device)
            self._loaded = True
            elapsed = time.monotonic() - t0
            logger.info("ct-punc 模型加载完成 (%.1fs)", elapsed)
        except Exception as e:
            logger.warning("ct-punc 模型加载失败，标点恢复不可用: %s", e)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def shutdown(self) -> None:
        """释放模型资源（共享实例不被释放）。"""
        with self._lock:
            if self._model and not self._shared_model:
                self._model = None
            self._loaded = False
            logger.info("PunctuationRestorer: 已关闭")
```

#### TextPipeline 集成

```python
# core/text_pipeline.py — 修改

class TextPipeline:
    def __init__(self):
        # V5: 所有外部组件通过 setter 注入
        self._punctuation_restorer = None

    def set_punctuation_restorer(self, restorer) -> None:
        """设置标点恢复器（engine.py 初始化后调用）。"""
        self._punctuation_restorer = restorer

    def process(self, text: str) -> ProcessResult:
        # 第零层：标点恢复（无标点 STT 模式专用）
        text = self._apply_punctuation(text)
        # 第一层：音素纠错
        text, phoneme_matches = self._apply_phoneme(text)
        # 第二层：正则替换
        text = self._apply_regex(text)
        # 第三层：热词替换
        text = self._apply_hotwords(text)
        # ... 返回 ProcessResult ...

    def _apply_punctuation(self, text: str) -> str:
        if not self._punctuation_restorer:
            return text
        try:
            return self._punctuation_restorer.restore(text)
        except Exception as e:
            logger.warning("标点恢复异常，跳过: %s", e)
            return text
```

#### engine.py 集成

```python
# engine.py — _init_hotword_pipeline 中新增

self._punctuation_restorer = None
stt_engine_type = getattr(config.stt, "engine", "auto")
no_punc_models = {
    "SenseVoiceSmall",
    "SenseVoiceMedium",
    "SenseVoiceLarge",
    "Fun-ASR-Nano",
}
model_size = config.stt.model_size

if stt_engine_type == "funasr" and model_size in no_punc_models:
    try:
        from core.punctuation import PunctuationRestorer

        self._punctuation_restorer = PunctuationRestorer(enabled=True)

        # 尝试复用 FunASR 引擎的 ct-punc 实例
        shared = self._get_stt_ct_punc_model()
        if shared is not None:
            self._punctuation_restorer.set_shared_model(shared)

        self._text_pipeline.set_punctuation_restorer(self._punctuation_restorer)
        logger.info("标点恢复已启用（%s 模式）", model_size)
    except ImportError:
        logger.warning("标点恢复模块不可用")
```

```python
# engine.py — 新增辅助方法

def _get_stt_ct_punc_model(self):
    """获取 STT 引擎的 ct-punc 模型实例。

    按优先级尝试：
    1. FunASREngine.get_punctuation_model()（V5 新增公开方法）
    2. 向后兼容：直接访问内部属性

    注意：如果引擎尚未加载模型，可能返回 None（首次标点恢复时会独立加载）。
    """
    if not self._stt_engine:
        return None
    if hasattr(self._stt_engine, "get_punctuation_model"):
        try:
            model = self._stt_engine.get_punctuation_model()
            if model is not None:
                return model
        except Exception:
            pass
    # 向后兼容
    if hasattr(self._stt_engine, "_model") and hasattr(
        self._stt_engine._model, "punc_model"
    ):
        return self._stt_engine._model.punc_model
    return None
```

#### FunASREngine 新增公开方法

```python
# core/stt_funasr.py — FunASREngine 类中新增

def get_punctuation_model(self):
    """获取引擎内部的 ct-punc 模型实例（公开接口）。

    供 PunctuationRestorer 复用，避免重复加载（节省 ~300MB 内存）。

    Returns:
        FunASR AutoModel 实例或 None（引擎未加载 / 无 ct-punc）
    """
    if hasattr(self, "_model") and self._model is not None:
        # Paraformer 内部自动加载的 ct-punc
        punc = getattr(self._model, "punc_model", None)
        if punc is not None:
            return punc
    return None
```

#### shutdown 集成

```python
# engine.py — shutdown（在 FunASR engine shutdown 之前）
if self._punctuation_restorer:
    self._punctuation_restorer.shutdown()
```

### 5.2 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 位置 | TextPipeline 第零层 | 标点恢复应在纠错之前（纠错需要完整句子） |
| 模型 | FunASR ct-punc | 与现有 FunASR 生态一致，无额外依赖 |
| 加载方式 | 延迟加载 | 避免启动时阻塞 |
| 锁类型 | `threading.RLock` | V5: 可重入锁，restore()→_load_model() 调用链安全 |
| 模型复用 | `set_shared_model()` | 复用 FunASR 引擎实例，节省 ~300MB |
| 资源释放 | 区分自有/共享实例 | shutdown 不释放共享实例 |
| 启用条件 | SenseVoice / Fun-ASR-Nano 白名单 | Paraformer 已自带标点 |
| 降级 | 模型不可用时返回原文 | 不影响核心功能 |

### 5.3 标点恢复对热词匹配的影响分析

1. **音素纠错层**：标点不是中文/英文字符，自然成为音素边界。**不影响准确性**。
2. **正则替换层**：标点不匹配热词正则。**无影响**。
3. **热词替换层**：`re.escape(热词)` 不匹配含标点文本。**这是正确行为**。
4. **极端场景**："腾讯会议明天开腾讯会议" → "腾讯会议，明天开腾讯会议。" → 两个"腾讯会议"仍能命中。

**结论**：标点恢复不影响热词匹配的准确性和覆盖率。

### 5.4 性能指标

| 指标 | 预期值 | 说明 |
|------|--------|------|
| 模型加载时间（首次） | ~3-5s | ct-punc ~300MB |
| 单次推理延迟 | ~50-200ms | 短文本（<100 字） |
| 内存增量 | ~300MB（独立）/ 0MB（共享） | 共享模式无额外内存 |
| 对实时转写的影响 | 首次 3-5s 卡顿 | 后续推理无感知 |

---

## 六、依赖变更

| 文件 | 变更 | 说明 |
|------|------|------|
| `requirements/base.txt` | +`watchdog>=3.0.0` | 文件监控库 |

---

## 七、新增/修改文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `core/file_watcher.py` | 文件监控模块（~110 行） |
| **新增** | `core/punctuation.py` | 标点恢复模块（~80 行） |
| **新增** | `tests/test_emoji_cleanup.py` | Emoji 清理单元测试 |
| **新增** | `tests/test_file_watcher.py` | 文件监控单元测试 |
| **新增** | `tests/test_punctuation.py` | 标点恢复单元测试 |
| **新增** | `tests/test_postprocess_integration.py` | F1+F3 集成测试 |
| **新增** | `tests/test_file_watcher_engine_integration.py` | F2+engine 集成测试 |
| **修改** | `core/stt_funasr.py` | `_postprocess_sensevoice()` + `_EMOJI_PATTERN` + `get_punctuation_model()` |
| **修改** | `core/text_pipeline.py` | 标点恢复层 + `set_punctuation_restorer()` |
| **修改** | `core/hotword.py` | 新增 `data_dir` 属性 |
| **修改** | `core/engine.py` | 初始化 file_watcher + punctuation + shutdown + `_get_stt_ct_punc_model()` |
| **修改** | `requirements/base.txt` | +`watchdog>=3.0.0` |

---

## 八、测试方案

### 8.1 单元测试

#### `tests/test_emoji_cleanup.py`（F1 — 16 用例）

| # | 测试用例 | 输入 | 预期 | 说明 |
|---|---------|------|------|------|
| 1 | `test_emoji_only` | `"😊😂"` | `""` | 纯 emoji |
| 2 | `test_emoji_in_text` | `"你好😊世界"` | `"你好世界"` | emoji 混中文 |
| 3 | `test_emoji_at_end` | `"你好世界😊"` | `"你好世界"` | emoji 在末尾 |
| 4 | `test_no_emoji` | `"你好世界"` | `"你好世界"` | 无 emoji |
| 5 | `test_multiple_emoji` | `"😊😂🤔你好😊"` | `"你好"` | 多个 emoji |
| 6 | `test_emo_marker` | `"文本<\|EMO_HAPPY\|>"` | `"文本"` | EMO 标记 |
| 7 | `test_mixed_marker_emoji` | `"<\|EMO_HAPPY\|>😊文本"` | `"文本"` | 标记+emoji |
| 8 | `test_english_emoji` | `"Hello😊 World"` | `"Hello World"` | 英文+emoji |
| 9 | `test_null_input` | `None` | `""` | None |
| 10 | `test_empty_input` | `""` | `""` | 空串 |
| 11 | `test_rich_postprocess` | 模拟输出 | emoji 清除 | 主路径 |
| 12 | `test_rich_unavailable` | 模拟输出（mock） | emoji 清除 | fallback |
| 13 | `test_zwj_sequence` | `"👨‍👩‍👧"` | `""` | ZWJ 组合 |
| 14 | `test_skin_tone` | `"👋🏻"` | `""` | 肤色修饰 |
| 15 | `test_flag_emoji` | `"🇨🇳"` | `""` | 国旗 |
| 16 | `test_cjk_not_affected` | `"你好世界"` | `"你好世界"` | 不误伤 CJK |

#### `tests/test_file_watcher.py`（F2 — 14 用例）

| # | 测试用例 | 说明 |
|---|---------|------|
| 1 | `test_init_with_paths` | 初始化正确设置路径 |
| 2 | `test_start_stop` | 启停无报错 |
| 3 | `test_file_change_triggers_callback` | 修改触发回调 |
| 4 | `test_debounce_multiple_changes` | 多次变更合并一次 |
| 5 | `test_missing_file_no_error` | 文件不存在不报错 |
| 6 | `test_empty_paths_no_start` | 空路径不启动 |
| 7 | `test_stop_without_start` | 未启动 stop 无报错 |
| 8 | `test_is_watching` | 属性正确 |
| 9 | `test_watchdog_unavailable` | ImportError 降级 |
| 10 | `test_callback_exception_handled` | 回调异常不影响 |
| 11 | `test_file_deleted_and_recreated` | 删除重建触发回调 |
| 12 | `test_missing_file_monitor_directory` | 不存在→监控目录→创建触发 |
| 13 | `test_timer_thread_safety` | 并发变更无异常 |
| 14 | `test_root_directory_skipped` | 根目录跳过 |

#### `tests/test_punctuation.py`（F3 — 14 用例）

| # | 测试用例 | 说明 |
|---|---------|------|
| 1 | `test_disabled` | 禁用时不处理 |
| 2 | `test_empty_text` | 空文本 |
| 3 | `test_none_text` | None |
| 4 | `test_model_unavailable` | 模型不可用降级 |
| 5 | `test_restore_basic` | 基础恢复（需模型，@slow） |
| 6 | `test_restore_long_text` | 长文本（@slow） |
| 7 | `test_already_has_punctuation` | 已有标点（@slow） |
| 8 | `test_shutdown` | 资源释放 |
| 9 | `test_is_loaded` | 状态查询 |
| 10 | `test_double_restore` | 重复调用安全 |
| 11 | `test_concurrent_restore` | 多线程并发安全 |
| 12 | `test_shared_model_injection` | 模型复用 |
| 13 | `test_shared_model_not_freed` | 共享实例不被 shutdown 释放 |
| 14 | `test_performance_latency` | 首次/后续耗时基准 |

### 8.2 集成测试

#### `tests/test_postprocess_integration.py`（F1+F3 — 6 用例）

| # | 测试用例 | 说明 |
|---|---------|------|
| 1 | `test_pipeline_with_emoji` | TextPipeline 正确清除 emoji |
| 2 | `test_pipeline_with_punctuation` | TextPipeline 正确添加标点 |
| 3 | `test_pipeline_full_chain` | emoji→标点→纠错→正则→热词 |
| 4 | `test_punctuation_then_correction` | 标点后纠错正常 |
| 5 | `test_punctuation_does_not_break_hotword` | 标点后热词仍匹配 |
| 6 | `test_emoji_then_punctuation_chain` | emoji+标点+热词全链路 |

#### `tests/test_file_watcher_engine_integration.py`（F2+engine — 3 用例）

| # | 测试用例 | 说明 |
|---|---------|------|
| 1 | `test_engine_starts_watcher` | engine 初始化后 watcher 运行 |
| 2 | `test_engine_stops_watcher_on_shutdown` | shutdown 后 watcher 停止 |
| 3 | `test_file_edit_triggers_pipeline_reload` | 编辑文件→pipeline reload |

### 8.3 功能测试（手动/E2E）

| # | 场景 | 步骤 | 预期 |
|---|------|------|------|
| 1 | SenseVoice emoji | 语音含情感 | 输出无 emoji |
| 2 | 热词热更新 | 启动→编辑 hotwords.txt→语音 | 新热词生效 |
| 3 | 音素文件热更新 | 编辑 hotwords-phoneme.txt | 新音素生效 |
| 4 | 规则文件热更新 | 编辑 rules.txt | 新规则生效 |
| 5 | SenseVoice 标点 | SenseVoice 语音输入 | 输出含标点 |
| 6 | Paraformer 无标点恢复 | Paraformer 语音 | 不执行标点恢复 |

### 8.4 回归测试

```bash
.venv/bin/python -m pytest tests/ -v \
  --ignore=tests/integration \
  --ignore=tests/test_windows_e2e.py \
  --ignore=tests/test_win32_input.py
```

重点：`test_text_pipeline.py`、`test_hotword.py`、`test_engine.py`

---

## 九、风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| ct-punc 模型下载失败 | 中 | 标点恢复不可用 | 优雅降级，返回原文 |
| ct-punc 模型体积（~300MB） | 中 | 首次延迟 | 延迟加载 + 日志提示 |
| watchdog 跨平台兼容性 | 低 | 文件监控不可用 | 降级跳过 |
| FunASR AutoModel.punc_model 属性路径不确定 | 低 | 共享注入失败 | 多级 fallback + 独立加载兜底 |
| HotwordManager.data_dir 属性需新增 | 中 | 实施时需同步修改 | 代码量小（~10 行） |

---

## 十、实施计划

### Phase 1：F1 Emoji 清理（~20min）
1. `core/stt_funasr.py` 新增 `_EMOJI_PATTERN` + 修改 `_postprocess_sensevoice()`
2. `tests/test_emoji_cleanup.py`
3. 本地测试

### Phase 2：F3 标点恢复（~40min）
1. `core/punctuation.py` 新建
2. `core/stt_funasr.py` 新增 `get_punctuation_model()`
3. `core/text_pipeline.py` 新增标点恢复层
4. `core/hotword.py` 新增 `data_dir` 属性
5. `core/engine.py` 集成
6. `tests/test_punctuation.py` + `tests/test_postprocess_integration.py`
7. 本地测试

### Phase 3：F2 文件监控（~40min）
1. `core/file_watcher.py` 新建
2. `core/engine.py` 集成
3. `requirements/base.txt` 新增 watchdog
4. `tests/test_file_watcher.py` + `tests/test_file_watcher_engine_integration.py`
5. 本地测试

### Phase 4：回归测试（~15min）
1. 全量测试通过
2. git commit

---

## 十一、评审历程

| 轮次 | 版本 | 分数 | 模型 | 关键改动 |
|------|------|------|------|----------|
| R1 | v1.0 | 78 | mimo-v2.5-pro | 初始设计 |
| R2 | v2.0 | 88 | mimo-v2.5-pro | 修复 9 项 |
| R3 | v3.0 | 85 | glm-5.1 | 修复 6 项 |
| R4 | v4.0 | 74 | glm-5.1 | 代码结构断裂（P0） |
| R5 | v5.0 | 待评 | glm-5.1 | 完整重写代码块 |

### R4 问题修复清单

| # | 问题 | 严重程度 | 修复方式 |
|---|------|----------|----------|
| 1 | FileWatcher.start() 缩进断裂，只监控最后一个文件 | P0 | V5 完整重写 start() 方法 |
| 2 | get_expected_file_path() 方法不存在 | P1 | 改用 `data_dir / filename` 构造路径 |
| 3 | get_punctuation_model() 属性路径未验证 | P1 | FunASREngine 新增公开方法 + 多级 fallback |
| 4 | 推理锁粒度过大 | P2 | 改用 RLock 统一保护（加载+推理可重入） |
| 5 | F2+engine 集成测试未列出 | P2 | 新增 test_file_watcher_engine_integration.py 方案 |
| 6 | shutdown 顺序未确认 | P2 | 明确标注"在 FunASR engine shutdown 之前" |

### 累计修复统计

- R1→R2：9 项全部修复
- R2→R3：6 项全部修复
- R3→R4：7 项，4 项修复、3 项部分修复（代码结构问题）
- R4→R5：6 项全部修复（完整重写）

---

*设计文档 v5.0 — 2026-05-02*
