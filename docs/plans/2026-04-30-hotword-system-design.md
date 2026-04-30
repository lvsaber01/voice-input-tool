# 热词系统第一期设计文档

> **版本**: v3.0  
> **日期**: 2026-04-30  
> **项目**: voice-input-tool  
> **范围**: 手动热词配置 + 正则规则替换 + FunASR 原生热词  
> **状态**: 待 AI 评审（第三轮）

### 评审历史

| 版本 | 评分 | 主要修复 |
|------|------|---------|
| v1.0 | 65.5/100 | 首轮评审，5 个 P0 问题 |
| v2.0 | 82.7/100 | 修复全部 P0 + 部分 P1 |
| v3.0 | 待评审 | 采纳 M-1~M-3 + reload debounce（详见附录 B） |

---

## 一、背景与目标

### 1.1 问题

STT 引擎对专有名词（人名、地名、品牌、术语）识别不准确。例如：
- "CUDA" → "库达"
- "鹤壁" → "贺壁"
- "飞书" → "废书"

现有 `command.py` 仅支持语音命令（标点符号、编辑操作），无文本替换能力。

### 1.2 目标

1. 支持用户自定义热词（文本替换），覆盖专有名词识别错误
2. 支持正则规则替换，处理标点映射、单位转换、噪声标记清理
3. 对 FunASR Paraformer 模型支持原生热词参数（模型级增强）
4. 提供 Web UI 管理热词和规则
5. 预置常用规则，开箱即用

### 1.3 不在范围内

- 音素匹配热词（第二期）
- LLM 后处理（第二期）
- 纠错记忆/自动积累（第二期）

---

## 二、架构设计

### 2.1 文本处理管线

新增 `core/text_pipeline.py`，engine.py 调用 pipeline 处理 STT 输出。

```
STT 输出 raw_text
    ↓
command_matcher.match(raw_text)     ← 先匹配命令（命令优先）
    ↓ 未匹配命令
text_pipeline.process(raw_text)
    ├── Step 1: 正则规则替换 (regex_rules)
    └── Step 2: 热词文本替换 (compiled_regex)
    ↓
processed_text
    ↓
injector.inject(processed_text)
```

**v2.0 关键变更：命令优先**

命令匹配在 pipeline 之前执行。原因：
- 命令是用户的显式意图（"说逗号就是要打逗号"），优先级高于隐式文本替换
- pipeline 的正则规则会把"逗号"替换为"，"，导致命令匹配失效
- 命令未匹配时才走 pipeline，处理噪声标记、热词替换等

**关于标点命令与正则规则的并存**：
- CommandMatcher 的标点命令是 **fullmatch 全文精确匹配**，仅当整段文本恰好是"逗号"时触发
- pipeline 的正则规则是 **sub 部分替换**，处理文本中包含的"逗号"关键词
- 两者语义不同，可以共存。命令优先策略保证：纯命令词走命令执行，混合文本走 pipeline

**设计要点**：
- `process()` 仅负责 regex + hotword 两步，不包含命令匹配和注入
- 返回 `ProcessResult(processed_text, is_changed)`，is_changed 用于日志/调试
- `reload()` 支持运行时热重载，Web UI 修改后无需重启
- **异常降级**：`process()` 顶层 try-catch，异常时返回原文，不阻塞引擎
- **正则容错**：逐条正则 try-catch，单条失败不影响其他规则
- **v3.0 新增**：reload 带 500ms debounce，防高频连续 reload

### 2.2 FunASR 原生热词

在 `core/stt_funasr.py` 内部独立实现，不走 pipeline。热词列表由 HotwordManager 提供。

```
HotwordManager（唯一数据源）
    ↓ get_model_hotword_list()
FunASREngine.load_hotwords(hotword_list)
    ↓
_do_transcribe() 中按模型类型分支：
    ├── Paraformer → model.generate(hotword=self._hotword_list)
    ├── SenseVoice → 不传 hotword（不支持）
    ├── Fun-ASR-Nano → 不传 hotword（不支持）
    └── 空列表 → 不传 hotword（零开销）
```

**数据流向**：HotwordManager 是热词数据的唯一管理者，TextPipeline 和 FunASREngine 都是数据消费方。

### 2.3 模块依赖关系

```
Engine
 ├── HotwordManager（唯一数据源，管理热词加载/解析/持久化）
 │    ↓ get_text_replacement_data()
 ├── TextPipeline（消费方，正则+热词替换）
 │    ↓ reload callback
 ├── FunASREngine（消费方，获取原生热词列表）
 ├── CommandMatcher（已有，不改动）
 └── TextInjector（已有，不改动）
```

### 2.4 Reload 通知机制

采用回调模式，避免 Web 层直接依赖 Core 层：

```python
# Engine 初始化时注册回调
self._text_pipeline.register_reload_callback(self._on_pipeline_reloaded)

def _on_pipeline_reloaded(self):
    """pipeline reload 后同步更新 FunASR 原生热词"""
    if self._stt_engine and hasattr(self._stt_engine, 'load_hotwords'):
        self._stt_engine.load_hotwords(
            self._hotword_manager.get_model_hotword_list()
        )
```

Web API 保存文件后 → `HotwordManager.save_to_file()` → `TextPipeline.schedule_reload()`（带 500ms debounce）→ `reload()` → 回调触发 → 同步 FunASR 热词列表。

**v3.0 新增 reload debounce**：
```python
class TextPipeline:
    _DEBOUNCE_INTERVAL = 0.5  # 500ms
    
    def schedule_reload(self):
        """带防抖的 reload（Web API 连续保存时合并）"""
        if self._reload_timer:
            self._reload_timer.cancel()
        self._reload_timer = threading.Timer(self._DEBOUNCE_INTERVAL, self.reload)
        self._reload_timer.daemon = True
        self._reload_timer.start()
```

### 2.5 新增/修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `core/text_pipeline.py` | 新增 | 文本处理管线引擎 |
| `core/hotword.py` | 新增 | 热词管理器（唯一数据源） |
| `hotwords.txt` | 新增 | 默认热词数据文件（预置注释说明格式） |
| `hot-rules.txt` | 新增 | 默认正则规则文件（预置常用规则） |
| `config.py` | 修改 | 新增 HotwordConfig，config_version v7→v8 |
| `core/engine.py` | 修改 | 命令优先 + 调用 pipeline |
| `core/stt_funasr.py` | 修改 | Paraformer 推理时注入原生热词 |
| `gui/web_server.py` | 修改 | 新增热词管理 API 端点 |
| `gui/templates/config.html` | 修改 | 新增热词管理 tab |

---

## 三、配置设计

### 3.1 HotwordConfig

```python
@dataclass
class HotwordConfig:
    enabled: bool = True                # 热词系统总开关
    hotwords_file: str = "hotwords.txt"  # 热词数据文件路径
    rules_file: str = "hot-rules.txt"    # 正则规则文件路径
    case_sensitive: bool = False         # 热词匹配是否区分大小写
    min_word_length: int = 2             # 热词最短字符数（防误替换）
```

### 3.2 config.yaml 示例

```yaml
hotword:
  enabled: true
  hotwords_file: hotwords.txt
  rules_file: hot-rules.txt
  case_sensitive: false
  min_word_length: 2
```

### 3.3 配置迁移

config_version 从 v7 迁移到 v8，自动填充 HotwordConfig 默认值。用户未配置时默认启用（空文件 = 零热词，无副作用）。

---

## 四、数据文件格式

### 4.1 hotwords.txt

```
# 热词文件 — 每行一条，# 开头为注释
# 格式：匹配文本 → 替换文本（或 -> 替换文本，兼容 ASCII 箭头）
# 没有箭头时，该词同时用于文本替换和 FunASR 原生热词
# [分类] 行是纯标记，方便组织

[通用]
刘洋 → 刘洋
鹤壁 → 鹤壁
飞书 → 飞书

[技术]
CUDA → CUDA
Kubernetes -> K8s
Vue.js → Vue.js

[仅FunASR原生热词]
OpenClaw
Saber
```

**解析规则**：
- `→`（U+2192）或 `->`（ASCII）分隔匹配文本和替换目标（v2.0 新增 `->` 兼容）
- 无箭头的行：词本身同时用于文本替换（匹配文本 = 替换目标）和 FunASR 原生热词列表
- `[分类]` 行跳过（纯组织标记）
- 空行和 `#` 注释行跳过
- 低于 `min_word_length` 的词跳过（防误替换）

### 4.2 hot-rules.txt

```
# 正则规则文件 — 左边正则匹配，右边替换内容
# 用 = 分隔（两边空格自动 trim）
# 编译失败的规则会被跳过并输出 warning 日志

# ========== 标点符号映射 ==========
(^逗号[，。]?)|([，。]?逗号$)       =    ，
(^句号[，。]?)|([，。]?句号$)       =    。
(^问号[，。]?)|([，。]?问号$)       =    ？
(^感叹号[，。]?)|([，。]?感叹号$)|(^叹号[，。]?)|([，。]?叹号$)  =    ！
(^冒号[，。]?)|([，。]?冒号$)       =    ：
(^分号[，。]?)|([，。]?分号$)       =    ；
(^顿号[，。]?)|([，。]?顿号$)       =    、
(^换行[，。]?)|([，。]?换行$)|(^回车[，。]?)|([，。]?回车$)  =    \n

# ========== 噪声标记清理 ==========
<\|nospeech\|>    =   
<\|EMO_\w+\|>     =   
\[breath\]         =   
\/sil             =   

# ========== 单位转换 ==========
毫安时            =   mAh
赫兹              =   Hz
伏特              =   V
瓦特              =   W
千瓦              =   kW
兆赫              =   MHz
千兆              =   GHz

# ========== 邮箱/域名 ==========
(艾特)\s*(QQ)\s*点\s*            =   @qq.
(艾特)\s*([一幺]六三)\s*点\s*     =   @163.
```

### 4.3 文件加载策略

用户优先：用户目录（`VOICE_INPUT_TOOL_USER_DATA/`）下的文件存在则使用，否则使用项目根目录的默认文件。首次使用即有预置规则。

---

## 五、模块详细设计

### 5.1 core/hotword.py（唯一数据源）

```python
@dataclass
class HotwordEntry:
    source: str      # 匹配文本
    target: str      # 替换文本
    category: str    # 分类（如"通用"、"技术"）
    text_replace: bool   # 是否用于文本替换
    model_hotword: bool  # 是否用于 FunASR 原生热词

class HotwordManager:
    """热词数据的唯一管理者。
    
    TextPipeline 和 FunASREngine 都是数据消费方，
    通过接口从 HotwordManager 获取所需数据。
    """
    
    def __init__(self, config):
        self._entries: List[HotwordEntry] = []
        self._file_path: str = ""
        self._rules_file_path: str = ""
    
    # ─── 数据加载 ───
    
    def load_from_file(self, file_path: str) -> int:
        """解析热词文件，返回条目数。强制 UTF-8，异常时 try GBK fallback。
        v3.0 新增：加载时检测热词冲突（同一 source 不同 target），log warning。
        """
    
    def load_rules_from_file(self, file_path: str) -> int:
        """解析正则规则文件，返回成功编译的规则数。"""
    
    def resolve_file_path(self, filename: str) -> str:
        """解析文件路径：用户目录优先，回退项目默认目录。"""
    
    # ─── 数据消费接口 ───
    
    def get_text_hotword_map(self) -> Dict[str, str]:
        """返回用于文本替换的 {匹配: 替换} 字典。
        键按长度降序排列（长词优先）。
        v3.0 新增：加载时检测同一 source 不同 target 的冲突，log warning。"""
    
    def get_model_hotword_list(self) -> List[str]:
        """返回用于 FunASR 原生热词的词列表（所有词的 source）。"""
    
    def get_compiled_rules(self) -> List[Tuple[re.Pattern, str]]:
        """返回编译后的正则规则列表。"""
    
    # ─── 运行时增删 ───
    
    def add_hotword(self, source: str, target: str = "", category: str = ""):
        """运行时追加热词（仅修改内存，不立即写文件）"""
    
    def remove_hotword(self, source: str) -> bool:
        """运行时删除热词（仅修改内存）"""
    
    # ─── 持久化 ───
    
    def save_to_file(self) -> bool:
        """原子写入热词文件（tmp + os.replace）"""
    
    def save_rules_to_file(self) -> bool:
        """原子写入正则规则文件（tmp + os.replace）"""
    
    # ─── 内部方法 ───
    
    @staticmethod
    def _parse_hotword_line(line: str) -> Optional[HotwordEntry]:
        """解析单行热词。支持 → 和 -> 两种分隔符。"""
    
    @staticmethod
    def _parse_rule_line(line: str) -> Optional[Tuple[str, str]]:
        """解析单行正则规则。返回 (pattern_str, replacement) 或 None。"""
    
    @staticmethod
    def _atomic_write(file_path: str, content: str) -> bool:
        """原子写入：写入临时文件 → os.replace。"""
        import tempfile
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
                    os.remove(tmp_path)
                raise
        except Exception as e:
            logger.error("原子写入失败 %s: %s", file_path, e)
            return False
    
    @staticmethod
    def _validate_regex_complexity(pattern_str: str) -> bool:
        """正则复杂度校验（防 ReDoS）。限制长度和嵌套量词。"""
        MAX_LENGTH = 500
        if len(pattern_str) > MAX_LENGTH:
            return False
        # 检测连续 3+ 量词嵌套（简易启发式）
        if re.search(r'(\+|\*|\{[^}]+\}).*(\+|\*|\{[^}]+\}).*(\+|\*|\{[^}]+\})', pattern_str):
            return False
        return True
```

**v2.0 修复**：
- ✅ HotwordManager 作为唯一数据源，TextPipeline 不再自己加载文件
- ✅ 原子写入（tmp + os.replace）
- ✅ 正则复杂度校验（长度限制 + 嵌套量词检测）
- ✅ 文件编码安全（UTF-8 + GBK fallback）

### 5.2 core/text_pipeline.py（消费方）

```python
@dataclass
class ProcessResult:
    text: str              # 处理后的文本
    is_changed: bool       # 是否被修改（用于日志）

class TextPipeline:
    """文本处理管线：正则 → 热词。
    
    仅消费 HotwordManager 的数据，不自己加载文件。
    """
    
    def __init__(self, config, hotword_manager: HotwordManager):
        self._hotword_manager = hotword_manager
        self._regex_rules: List[Tuple[re.Pattern, str]] = []
        self._hotword_regex: Optional[re.Pattern] = None  # 预编译热词正则
        self._hotword_map: Dict[str, str] = {}            # 匹配 → 替换
        self._hotword_map_lower: Dict[str, str] = {}      # v3.0: lower → 替换（O(1) 查找）
        self._enabled: bool = config.hotword.enabled
        self._case_sensitive: bool = config.hotword.case_sensitive
        self._on_reload_callbacks: List[Callable] = []
        
        # 初始加载
        self.reload()
    
    def reload(self):
        """从 HotwordManager 重新获取数据，原子替换引用。"""
        try:
            # 获取最新数据
            new_rules = self._hotword_manager.get_compiled_rules()
            new_map = self._hotword_manager.get_text_hotword_map()
            
            # 构建预编译热词正则（一次性扫描，长词优先）
            new_regex = self._build_hotword_regex(new_map)
            
            # 原子替换引用（避免 reload 竞态）
            self._regex_rules = new_rules
            self._hotword_map = new_map
            self._hotword_map_lower = {k.lower(): v for k, v in new_map.items()}  # v3.0
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
        v3.0 优化：大小写不敏感查找改为 O(1) 字典查找。"""
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
```

**v2.0 修复**：
- ✅ TextPipeline 不再自己加载文件，通过构造函数接收 HotwordManager
- ✅ `case_sensitive=False` 改用 `re.sub(re.IGNORECASE)`，不再用 `str.replace`
- ✅ 热词替换用预编译交替正则，一次扫描完成（解决长词优先 + 大小写不敏感）
- ✅ `process()` 顶层 try-catch 降级返回原文
- ✅ `_apply_regex` 逐条容错
- ✅ reload 使用原子替换引用（先构建新数据再整体替换）
- ✅ 回调通知机制

### 5.3 core/engine.py 改动

```python
# CoreEngine.__init__ 中新增：
self._hotword_manager = HotwordManager(config)
self._text_pipeline = TextPipeline(config, self._hotword_manager)
self._text_pipeline.register_reload_callback(self._on_pipeline_reloaded)

def _on_pipeline_reloaded(self):
    """pipeline reload 后同步更新 FunASR 原生热词"""
    if self._stt_engine and hasattr(self._stt_engine, 'load_hotwords'):
        self._stt_engine.load_hotwords(
            self._hotword_manager.get_model_hotword_list()
        )

# _on_stt_complete_inner 改动：
def _on_stt_complete_inner(self, text, language, duration_ms, error):
    # ... 现有错误和空文本处理 ...
    
    # 1. 先尝试命令匹配（命令优先，使用原始文本）
    result = self._command_matcher.match(text)
    if result:
        self._command_executor.execute(result.command)
        return
    
    # 2. 未匹配命令，走 pipeline 处理
    pipeline_result = self._text_pipeline.process(text)
    text = pipeline_result.text
    if pipeline_result.is_changed:
        logger.info("Pipeline 处理: %s", text[:100])
    
    # 3. 正常注入
    self._inject_text(text)

# _on_realtime_segment 改动：
def _on_realtime_segment(self, text: str):
    if self._shutdown_event.is_set():
        return
    
    # 实时模式也走 pipeline（清理噪声标记等）
    pipeline_result = self._text_pipeline.process(text)
    text = pipeline_result.text
    if not text.strip():
        return  # pipeline 清理后为空，跳过
    
    # ... 后续 separator、timestamp、inject 逻辑不变 ...
```

**v2.0 修复**：
- ✅ 命令优先：先 `command_matcher.match(raw_text)`，未匹配再走 pipeline
- ✅ `_on_realtime_segment` 也集成 pipeline
- ✅ pipeline 异常降级已由 TextPipeline.process() 内部保证

**v3.0 补充**：
- ✅ 明确实时模式不支持语音命令（与现有代码行为一致）
- ✅ 文档补充说明：实时模式 pipeline 仅处理噪声清理和热词替换，不做命令匹配

### 5.4 core/stt_funasr.py 改动

```python
class FunASREngine:
    def __init__(self, config):
        # ... 现有代码 ...
        self._hotword_list: List[str] = []  # FunASR 原生热词列表
        self._is_sensevoice: bool = False   # 模型类型标记
        self._is_fun_asr_nano: bool = False
    
    def load_hotwords(self, hotword_list: List[str]):
        """从 HotwordManager 获取词列表（由 engine 的 reload 回调调用）"""
        self._hotword_list = list(hotword_list)  # 复制一份，避免引用共享
        if self._hotword_list:
            logger.info("FunASR 原生热词已加载: %d 条", len(self._hotword_list))
    
    def _do_transcribe(self, audio):
        if not self._is_sensevoice and not self._is_fun_asr_nano:
            # Paraformer: 仅当有热词时才传 hotword 参数
            kwargs = {"input": audio, "batch_size_s": 300}
            if self._hotword_list:
                kwargs["hotword"] = self._hotword_list
            result = self.model.generate(**kwargs)
        else:
            # SenseVoice / Fun-ASR-Nano: 不传 hotword
            result = self.model.generate(input=audio, ...)
```

**v2.0 修复**：
- ✅ `load_hotwords` 接收列表参数，由 engine reload 回调调用
- ✅ 不再自己加载文件，消除第二数据源

---

## 六、API 设计

### 6.1 热词管理 API

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| GET | `/api/hotwords` | 返回热词列表 JSON | 无 |
| PUT | `/api/hotwords` | 整体替换热词文件 | token |
| POST | `/api/hotwords` | 追加单条热词 | token |
| DELETE | `/api/hotwords` | 清空热词文件 | token |

### 6.2 正则规则管理 API

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| GET | `/api/hotword-rules` | 返回规则列表 JSON | 无 |
| PUT | `/api/hotword-rules` | 整体替换规则文件 | token |
| POST | `/api/hotword-rules` | 追加单条规则 | token |
| DELETE | `/api/hotword-rules` | 清空规则文件 | token |

### 6.3 输入校验

- POST 追加热词：`source` 非空、长度 2-100、无换行符；`target` 长度 0-200
- POST 追加规则：`pattern` 非空、长度 1-500、通过复杂度校验、编译测试通过；`replacement` 长度 0-200
- PUT 整体替换：整体格式校验，跳过无效行并 log warning，不全量拒绝

### 6.4 Reload 触发

所有写操作（PUT/POST/DELETE）成功后：
1. `HotwordManager.save_to_file()` 原子写入文件
2. `HotwordManager.load_from_file()` 重新加载内存数据
3. `TextPipeline.reload()` 重新获取数据 + 编译正则
4. 回调触发 → FunASR 热词列表同步

若任何步骤失败，前端返回错误但保留旧数据继续使用（降级）。

### 6.5 响应格式

```json
// GET /api/hotwords
{
  "ok": true,
  "hotwords": [
    {"source": "CUDA", "target": "CUDA", "category": "技术"},
    {"source": "Kubernetes", "target": "K8s", "category": "技术"}
  ],
  "count": 2,
  "file": "hotwords.txt"
}

// PUT /api/hotwords
{
  "ok": true,
  "message": "热词已保存并重载",
  "count": 15
}

// POST /api/hotwords（校验失败）
{
  "ok": false,
  "error": "source 不能为空且长度 2-100"
}
```

---

## 七、Web UI 设计

在现有配置页面 tab 栏新增"热词"标签页，包含两个区域：

### 7.1 文本热词区

- textarea 编辑器，每行一条，支持批量粘贴
- 底部格式说明提示（简短一行）
- "保存"按钮 → PUT /api/hotwords
- 显示当前热词总数
- 输入时实时检测 source 重复，高亮提示

### 7.2 正则规则区

**上层：常用规则快捷按钮**（一行横排，点击追加到编辑器）
- 🏷️ 标点符号、🔤 单位转换、🧹 噪声清理、📧 邮箱域名、⌨️ 语音快捷键
- 每个按钮追加预置规则集（已存在的规则不重复追加）

**下层：自定义规则编辑器**
- textarea 编辑器（高级用户直接写正则）
- 旁边简短格式说明："左边正则匹配 = 右边替换内容"
- **测试框**：输入一段文本，点击"测试"按钮，显示替换前后的对比
- "保存"按钮 → PUT /api/hotword-rules

### 7.3 实时重载

保存成功后自动触发 reload 链（HotwordManager → TextPipeline → FunASR），无需重启引擎。若重载失败，前端显示错误提示但不影响当前使用（降级用旧数据）。

---

## 八、测试策略

### Layer 1：单元测试（macOS）

**test_text_pipeline.py** — pipeline 核心逻辑（~22 cases）：
- 正则替换正确性
- 热词替换正确性（re.sub 方案）
- 执行顺序：正则先于热词
- 空文件 / 空文本 / 全注释文件
- 无效格式容错（编码异常、BOM 头、混合换行符）
- reload 重新加载（原子替换验证）
- min_word_length 保护
- **case_sensitive=True/False** 各种大小写组合
- **长词优先替换**（"Kubernetes" 优先于 "Kube"）
- **pipeline 异常降级**返回原文
- **正则逐条容错**（某条规则异常不影响其他）

**test_hotword.py** — 热词管理器（~18 cases）：
- `→` 和 `->` 两种分隔符解析
- 无箭头词提取（同时用于文本替换和原生热词）
- FunASR 原生热词列表生成（仅 source，去重）
- 分类标记 `[xxx]` 识别
- 注释跳过、空行跳过
- add_hotword / remove_hotword 运行时操作
- **save_to_file 原子写入**（验证文件完整性）
- **编码安全**（UTF-8 文件 + GBK fallback）
- **热词冲突检测**（同一 source 不同 target）
- **get_text_hotword_map 按长度降序排列**
- 边界：BOM 头、超长行、特殊字符、空 target

**test_hotword_rules.py** — 正则规则解析（~12 cases）：
- 正确规则加载与编译
- 编译失败规则跳过并 log warning
- **默认预置规则全部可编译通过**
- 空替换（噪声清理场景）
- **正则复杂度校验**（超长规则、嵌套量词）
- `=` 分隔解析（空格 trim）

### Layer 2：引擎集成测试（macOS，mock STT）

**test_engine_pipeline_integration.py** — engine + pipeline 联动（~18 cases）：
- mock STT 返回含噪声标记文本 → 验证 pipeline 清理后结果
- mock STT 返回含热词匹配词 → 验证替换正确
- 批量模式完整流程：录音 → STT → **命令优先** → pipeline → 注入
- 实时模式完整流程：分段 → pipeline → 追加
- **命令优先验证**：说"逗号"应触发命令，不应走 pipeline 替换
- **命令未命中时才走 pipeline**：混合文本经过 pipeline 处理
- pipeline 处理失败 → 降级返回原文，不阻塞引擎
- `hotword.enabled=false` → pipeline 被跳过
- **热词替换后文本恰好匹配命令的边界情况**
- **pipeline 清理后文本为空 → 跳过注入**

### Layer 3：跨平台集成测试（macOS + Windows）

macOS 本地全部测试跑通。
Windows 开发机通过 SSH 部署后验证：
- FunASR mock 环境下 Paraformer 分支传入 hotword 参数
- SenseVoice 分支不传 hotword 参数
- Fun-ASR-Nano 分支不传 hotword 参数
- 文件路径兼容（项目目录 vs 打包目录）
- 编码兼容（GBK 环境下 UTF-8 文件读取）
- `__pycache__` 清理后测试
- **原子写入在 Windows 上的兼容性**（os.replace）

### Layer 4：功能自动化测试（Windows 开发机）

**test_hotword_e2e.py** — 端到端功能验证（~15 cases）：
- 修改 hotwords.txt → reload → 合成音频转写 → 验证注入文本
- 修改 hot-rules.txt → reload → 合成音频转写 → 验证规则生效
- Web API CRUD 测试（GET/PUT/POST/DELETE 热词和规则）
- **API 输入校验**（空 source、超长规则、编译失败规则）
- `hotword.enabled=false` → pipeline 被跳过
- 默认预置规则对 SenseVoice 常见输出的清理效果
- 用户文件优先于项目默认文件
- **reload 回调同步 FunASR 热词列表**

### Layer 5：回归测试

- 现有全部测试在 macOS + Windows 均通过（159 macOS + 159 Windows）
- 重点关注 `test_engine.py` 中与 `_on_stt_complete_inner` 和 `_on_realtime_segment` 相关的用例

**预计新增测试**：~85 个测试用例

---

## 九、风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 正则规则误匹配 | 正常文本被错误替换 | 默认预置规则充分测试；min_word_length 保护 |
| 热词替换冲突 | 热词间互相覆盖 | 长词优先排序；冲突检测 log warning（v3.0） |
| 文件编码问题 | Windows GBK 环境读 UTF-8 文件 | 强制 UTF-8，异常时 GBK fallback |
| reload 竞态 | reload 中有文本正在处理 | 原子替换引用（先构建新数据再整体替换） |
| FunASR hotword 参数变化 | 上游 API 变更导致不兼容 | 按模型类型分支隔离，不影响其他引擎 |
| 正则 ReDoS | 恶意正则导致 CPU 耗尽 | 正则长度限制 500 字符 + 嵌套量词检测 |
| pipeline 异常 | STT 输出丢失 | process() 顶层 try-catch 降级返回原文 |
| 文件写入中断 | 热词文件损坏 | 原子写入（tmp + os.replace） |
| 命令与正则冲突 | 语音命令被 pipeline 拦截 | 命令优先策略：先命令匹配，未命中才走 pipeline |

---

## 十、实施计划

| 步骤 | 内容 | 预计时间 |
|------|------|---------|
| 1 | 新建 core/hotword.py（HotwordManager，唯一数据源） | 1.5h |
| 2 | 新建 core/text_pipeline.py（TextPipeline，消费方） | 1h |
| 3 | 新建 hotwords.txt + hot-rules.txt（预置规则） | 0.5h |
| 4 | 修改 config.py（HotwordConfig + v8 迁移） | 0.5h |
| 5 | 修改 core/engine.py（命令优先 + pipeline + reload 回调） | 1h |
| 6 | 修改 core/stt_funasr.py（Paraformer hotword + reload 接口） | 0.5h |
| 7 | 编写 Layer 1 单元测试 + macOS 运行 | 2h |
| 8 | 编写 Layer 2 集成测试（含命令优先测试） | 1.5h |
| 9 | 修改 gui/web_server.py（API + 输入校验 + reload 链） | 1h |
| 10 | 修改 gui/templates/config.html（热词 UI） | 1.5h |
| 11 | Windows 部署 + Layer 3/4 跨平台测试 | 1.5h |
| 12 | 回归测试 + 修复 | 1h |
| **合计** | | **~12h** |

---

## 附录 A：v1.0 → v2.0 修复清单

### P0 严重问题（全部修复）

| # | 问题 | v2.0 修复方案 |
|---|------|-------------|
| P0-1 | Pipeline 与 CommandMatcher 执行顺序导致命令失效 | **命令优先策略**：先 `command_matcher.match(raw_text)`，未匹配再走 pipeline |
| P0-2 | `case_sensitive=False` 无法用 `str.replace` 实现 | **改用 `re.sub(re.IGNORECASE)`**：预编译交替正则，一次扫描完成 |
| P0-3 | TextPipeline 与 HotwordManager 职责重叠 | **统一数据源**：HotwordManager 是唯一管理者，TextPipeline 仅消费数据 |
| P0-4 | `save_to_file()` 非原子写入 | **原子写入**：`tempfile.mkstemp()` + `os.replace()` |
| P0-5 | Pipeline 异常无降级 | **双层容错**：`process()` 顶层 try-catch + `_apply_regex()` 逐条容错 |

### P1 中等问题（部分修复）

| # | 问题 | v2.0 修复方案 |
|---|------|-------------|
| P1-1 | `_on_realtime_segment` 未集成 pipeline | ✅ 实时模式也走 pipeline |
| P1-2 | FunASREngine 热词列表来源不明确 | ✅ 由 engine reload 回调调用 `load_hotwords()` |
| P1-3 | ReDoS 防护方案不可行 | ✅ 正则长度限制 500 + 嵌套量词启发式检测 |
| P1-5 | 热词替换缺少优先级排序 | ✅ `sorted(by len, reverse=True)` + 预编译交替正则 |
| P1-6 | reload 通知机制不明确 | ✅ 回调模式：`register_reload_callback()` |
| P1-4 | 运行时增删持久化策略 | 📋 仅内存修改，Web API 保存时统一持久化（简化设计） |

### P2 轻微问题（部分修复）

| # | 问题 | v2.0 修复方案 |
|---|------|-------------|
| P2-1 | Pipeline reload 触发机制 | ✅ 回调模式 |
| P2-2 | 热词文件分隔符兼容 | ✅ 同时支持 `→` 和 `->` |
| P2-3 | API 输入校验 | ✅ 定义校验规则 |

---

*文档版本: v3.0 — 待第三轮 AI 评审*
## 附录 B：v2.0 → v3.0 修复清单（第二轮评审采纳）

### 评审建议评估

| # | 建议 | 是否采纳 | 理由 |
|---|------|---------|------|
| M-1 | 热词冲突检测策略不明确 | ✅ 采纳 | 实现简单，提升数据一致性 |
| M-2 | 大小写不敏感查找性能 O(n) | ✅ 采纳 | 预构建字典 O(1)，零成本优化 |
| M-3 | 实时模式命令支持不明确 | ✅ 采纳 | 文档补充说明，与现有代码行为一致 |
| M-4 | FunASR 热词参数格式未验证 | ❌ 不采纳 | 已验证 Paraformer API，hotword 接受字符串列表 |
| M-5 | FunASR 线程安全性 | ❌ 不采纳 | generate() 在 ThreadPoolExecutor(max_workers=1) 单线程中 |
| M-6 | reload 缺少 debounce | ✅ 采纳 | 防高频连续保存，500ms debounce 实现简单 |
| M-7 | 正则边界匹配问题 | ❌ 不采纳 | 子串匹配是正确语义，精确匹配反而会遗漏 |

### 不采纳的架构建议（留第二期）

| 建议 | 不采纳理由 |
|------|----------|
| 插件化 Pipeline | 过度工程，第二期做 LLM 后处理时再重构 |
| 热词系统分层（STT前/后分离） | 当前数据源已统一，分层增加复杂度 |
| 临时/会话热词 | 需求不明确，等悬浮预览窗口再做入口 |
| EventBus 统一通知 | 回调模式足够，EventBus 扩展第二期再引入 |
| 热词使用统计 | 优化项，非核心功能 |
| Web UI 导入/导出 | 优化项，手动编辑足够 |

