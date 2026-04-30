# 热词系统设计文档 v2.0 — 专业评审报告（第二轮）

> **评审人角色**：资深后端架构师  
> **评审日期**：2026-04-30  
> **评审对象**：`docs/plans/2026-04-30-hotword-system-design.md` v2.0  
> **关联源文件**：`core/engine.py`, `core/stt_funasr.py`, `core/injector.py`, `core/command.py`, `config.py`  
> **评审结论**：**有条件通过**（修复关键问题后可实施，整体架构有改进空间）

---

## 目录

1. [P0 问题修复核查](#1-p0-问题修复核查)
2. [逐模块评审](#2-逐模块评审)
3. [架构评审](#3-架构评审)
4. [技术选型评审](#4-技术选型评审)
5. [安全与稳定性评审](#5-安全与稳定性评审)
6. [测试策略评审](#6-测试策略评审)
7. [综合评分](#7-综合评分)
8. [关键问题清单](#8-关键问题清单)
9. [优化建议](#9-优化建议)
10. [架构改进建议](#10-架构改进建议)

---

## 1. P0 问题修复核查

### 1.1 第一轮 5 个 P0 问题修复状态

| # | 问题 | v1.0 状态 | v2.0 修复方案 | 核查结果 |
|---|------|-----------|---------------|----------|
| **P0-1** | Pipeline 与 CommandMatcher 执行顺序导致命令失效 | 🔴 严重 | **命令优先策略**：先 `command_matcher.match(raw_text)`，未匹配再走 pipeline | ✅ **已修复** |
| **P0-2** | `case_sensitive=False` 无法用 `str.replace` 实现 | 🔴 严重 | **改用 `re.sub(re.IGNORECASE)`**：预编译交替正则，一次扫描完成 | ✅ **已修复** |
| **P0-3** | TextPipeline 与 HotwordManager 职责重叠、数据源不统一 | 🔴 严重 | **统一数据源**：HotwordManager 是唯一管理者，TextPipeline 仅消费数据 | ✅ **已修复** |
| **P0-4** | `save_to_file()` 非原子写入 | 🔴 严重 | **原子写入**：`tempfile.mkstemp()` + `os.replace()` | ✅ **已修复** |
| **P0-5** | Pipeline 异常无降级 | 🔴 严重 | **双层容错**：`process()` 顶层 try-catch + `_apply_regex()` 逐条容错 | ✅ **已修复** |

**结论**：全部 5 个 P0 问题已在 v2.0 中得到妥善修复。修复方案设计合理，实现细节完整。

### 1.2 修复质量评估

| 修复项 | 设计完整性 | 实现细节 | 边界处理 | 评分 |
|--------|-----------|----------|----------|------|
| 命令优先 | ⭐⭐⭐⭐⭐ | 流程图清晰，伪代码完整 | 混合文本、命令未命中场景已考虑 | 9/10 |
| re.sub 方案 | ⭐⭐⭐⭐⭐ | 交替正则 + 长词优先 + 大小写不敏感 | 回调函数处理大小写映射 | 9/10 |
| 统一数据源 | ⭐⭐⭐⭐⭐ | 依赖注入模式，职责边界清晰 | reload 回调机制完整 | 9/10 |
| 原子写入 | ⭐⭐⭐⭐⭐ | mkstemp + os.replace 标准方案 | 异常清理、目录创建 | 9/10 |
| 异常降级 | ⭐⭐⭐⭐⭐ | 双层容错设计 | 日志记录、状态保留 | 9/10 |

---

## 2. 逐模块评审

### 2.1 HotwordManager（core/hotword.py）

**设计概述**：作为热词数据的唯一管理者，负责加载、解析、持久化，为 TextPipeline 和 FunASREngine 提供数据消费接口。

**优点**：
- ✅ **单一职责**：明确区分数据管理（HotwordManager）和数据消费（TextPipeline/FunASREngine）
- ✅ **接口设计合理**：`get_text_hotword_map()` 和 `get_model_hotword_list()` 分离文本替换和模型热词两种用途
- ✅ **文件格式灵活**：支持 `→` 和 `->` 两种分隔符，无箭头词同时用于两种用途
- ✅ **原子写入**：使用 `tempfile.mkstemp()` + `os.replace()` 实现原子写入
- ✅ **编码安全**：强制 UTF-8，异常时 GBK fallback
- ✅ **正则复杂度校验**：长度限制 500 + 嵌套量词检测，防范 ReDoS

**问题**：

| # | 严重程度 | 问题描述 | 建议 |
|---|---------|---------|------|
| HM-1 | 🟡 中等 | **热词冲突检测不完整**。设计文档提到"加载时检测冲突并 log warning"，但没有定义"冲突"的具体语义和检测逻辑。是同一 source 不同 target？还是 source 是另一 source 的子串？ | 明确冲突定义：①同一 source 不同 target（数据冲突，必须 warning）；②source A 是 source B 的子串且 A 长度 < B（优先级冲突，可 info 提示） |
| HM-2 | 🟡 中等 | **`get_text_hotword_map` 返回的排序保证依赖调用方**。虽然文档说"键按长度降序排列"，但这是为了 TextPipeline 的正则构建，如果其他消费方需要不同排序，可能需要重新排序。 | 考虑返回 `OrderedDict` 或添加 `sort_key` 参数，让消费方指定排序方式 |
| HM-3 | 🟢 轻微 | **运行时增删的持久化策略**。`add_hotword()` / `remove_hotword()` 仅修改内存，Web API 保存时才持久化。这可能导致用户困惑："我添加了热词为什么没有生效？" | 文档中明确说明此行为，或在 Web UI 提供"保存到文件"的明确提示 |

**评分**：8.5/10 — 设计优秀，冲突检测策略可更明确。

---

### 2.2 TextPipeline（core/text_pipeline.py）

**设计概述**：文本处理管线，消费 HotwordManager 的数据，执行正则替换 → 热词替换两步处理。

**优点**：
- ✅ **职责单一**：仅负责文本处理，不管理数据
- ✅ **依赖注入**：通过构造函数接收 HotwordManager，解耦数据源
- ✅ **re.sub 方案正确**：预编译交替正则，一次扫描完成所有热词替换
- ✅ **长词优先**：`sorted(by len, reverse=True)` 保证长词优先匹配
- ✅ **大小写不敏感**：`re.IGNORECASE` 标志 + 回调函数处理大小写映射
- ✅ **异常降级**：`process()` 顶层 try-catch，异常时返回原文
- ✅ **reload 原子性**：先构建新数据再整体替换引用，避免竞态
- ✅ **回调机制**：支持 reload 完成后通知其他组件（如同步 FunASR 热词）

**问题**：

| # | 严重程度 | 问题描述 | 建议 |
|---|---------|---------|------|
| TP-1 | 🟡 中等 | **正则替换顺序可能影响结果**。规则文件中的正则按顺序执行，但用户可能期望某些规则优先。例如，噪声清理应该在标点映射之前还是之后？ | 在 `hot-rules.txt` 中明确规则执行顺序的语义（自上而下），或添加 `[priority:N]` 标记支持优先级 |
| TP-2 | 🟡 中等 | **热词替换回调函数性能**。`_replace` 回调在每次匹配时都要遍历 `hotword_map` 做大小写不敏感查找（O(n)），虽然热词数量通常不大，但文本较长时可能有性能问题。 | 考虑预构建 `lower_source → target` 的映射字典，避免每次遍历 |
| TP-3 | 🟢 轻微 | **缺少替换统计**。对于调试和优化，知道哪些热词/规则被频繁使用是有价值的。 | 考虑添加可选的统计功能（命中次数、处理时间） |

**代码审查 - `_apply_hotwords` 大小写不敏感实现**：

```python
def _replace(match):
    matched = match.group(0)
    if self._case_sensitive:
        return hotword_map.get(matched, matched)
    # 大小写不敏感：忽略大小写查找
    matched_lower = matched.lower()
    for source, target in hotword_map.items():
        if source.lower() == matched_lower:
            return target
    return matched
```

**问题**：每次匹配都遍历整个 `hotword_map`，时间复杂度 O(n*m)，其中 n=热词数，m=匹配次数。

**优化建议**：
```python
def __init__(self, ...):
    # ...
    self._hotword_map_lower: Dict[str, str] = {}  # lower_source → target

def reload(self):
    # ...
    self._hotword_map_lower = {k.lower(): v for k, v in new_map.items()}

def _replace(self, match):
    matched = match.group(0)
    if self._case_sensitive:
        return self._hotword_map.get(matched, matched)
    return self._hotword_map_lower.get(matched.lower(), matched)
```

**评分**：8/10 — 整体设计优秀，大小写不敏感查找可优化。

---

### 2.3 Engine 集成（core/engine.py 改动）

**设计概述**：在 `_on_stt_complete_inner` 和 `_on_realtime_segment` 中集成 pipeline，采用"命令优先"策略。

**优点**：
- ✅ **命令优先策略正确**：先匹配命令，未命中再走 pipeline，避免命令被正则替换破坏
- ✅ **批量和实时模式都集成**：`_on_stt_complete_inner` 和 `_on_realtime_segment` 都调用 pipeline
- ✅ **回调注册**：通过 `register_reload_callback` 同步 FunASR 热词列表
- ✅ **降级保证**：pipeline 内部已处理异常降级

**问题**：

| # | 严重程度 | 问题描述 | 建议 |
|---|---------|---------|------|
| EI-1 | 🟡 中等 | **pipeline 初始化时机**。设计文档说在 `CoreEngine.__init__` 中创建，但此时 config 可能尚未完全验证。如果热词文件路径配置错误，初始化会失败。 | 考虑将 pipeline 初始化移到 `_load_model_async` 中，或在 `__init__` 中捕获初始化异常并降级为禁用热词功能 |
| EI-2 | 🟡 中等 | **实时模式下的命令匹配**。`_on_realtime_segment` 直接调用 pipeline 处理文本，但没有命令匹配。这意味着实时模式下语音命令（如"逗号"）不会被识别为命令，而是被 pipeline 替换为标点。 | 这是设计选择，但需要明确：实时模式是否支持语音命令？如果不支持，需要在文档中说明；如果支持，需要添加命令匹配逻辑 |
| EI-3 | 🟢 轻微 | **reload 回调的错误处理**。回调异常被捕获并记录 warning，但如果 FunASR 热词加载失败，用户可能不知道。 | 考虑通过事件总线通知 UI，或在日志中提升为 error 级别 |

**关于 EI-2 的深入分析**：

实时模式下，STT 输出的是分段文本（如"今天天气"→"今天天气很好"→"今天天气很好啊"），不是完整的句子。命令匹配器使用 `fullmatch`，要求整段文本精确匹配命令模式。这意味着：
- 批量模式：说"逗号"，STT 输出 `"逗号"`，`fullmatch(r"逗号")` 成功
- 实时模式：说"逗号"，STT 可能输出 `"逗"` → `"逗号"`，`fullmatch` 对 `"逗"` 失败

**建议**：明确实时模式不支持语音命令（在文档中说明），或修改命令匹配器支持前缀匹配（但这会引入误触发风险）。

**评分**：7.5/10 — 集成设计合理，实时模式的命令支持需要明确。

---

### 2.4 FunASR 原生热词（core/stt_funasr.py 改动）

**设计概述**：在 Paraformer 模型推理时传入 `hotword` 参数，SenseVoice 和 Fun-ASR-Nano 不支持。

**优点**：
- ✅ **模型类型分支隔离**：按 `_is_sensevoice` / `_is_fun_asr_nano` 分支，不影响其他模型
- ✅ **零开销**：空列表时不传 `hotword` 参数
- ✅ **数据流向清晰**：从 HotwordManager 获取，通过 engine reload 回调同步

**问题**：

| # | 严重程度 | 问题描述 | 建议 |
|---|---------|---------|------|
| FA-1 | 🟡 中等 | **热词参数格式未验证**。设计文档写 `kwargs["hotword"] = self._hotword_list`，但 FunASR 的 `hotword` 参数可能需要特定格式（如 `word:weight`）。 | 需要验证 FunASR 文档，确认 `hotword` 参数接受纯字符串列表还是带权重的格式。如果需要权重，考虑在热词文件格式中支持 `word:weight` 语法 |
| FA-2 | 🟡 中等 | **热词列表更新时机**。`load_hotwords()` 在 reload 回调中调用，但模型可能正在推理中。FunASR 的 `generate()` 是否线程安全？热词列表更新是否会影响正在进行的推理？ | 需要验证 FunASR 的线程安全性。如果 `generate()` 正在执行时修改 `self._hotword_list`，可能导致不可预期行为。考虑使用锁保护或使用不可变列表 |
| FA-3 | 🟢 轻微 | **热词效果无反馈**。用户无法知道 FunASR 原生热词是否生效。 | 考虑在日志中记录热词命中情况（如果 FunASR 提供此信息），或提供测试功能 |

**评分**：7.5/10 — 设计方向正确，需要验证 FunASR API 细节和线程安全性。

---

### 2.5 API 设计（gui/web_server.py 改动）

**设计概述**：新增热词和正则规则的 CRUD API，支持运行时 reload。

**优点**：
- ✅ **RESTful 风格统一**：与现有 API 一致
- ✅ **鉴权合理**：GET 无鉴权，写操作需要 token
- ✅ **响应格式标准化**：`ok` / `message` / `count` / `error`
- ✅ **输入校验**：source 长度 2-100、pattern 长度 1-500、正则复杂度校验
- ✅ **reload 链**：保存 → 加载 → reload → 回调，完整链路

**问题**：

| # | 严重程度 | 问题描述 | 建议 |
|---|---------|---------|------|
| AP-1 | 🟡 中等 | **PUT 整体替换的并发安全**。虽然文件写入是原子的，但 `HotwordManager.load_from_file()` 读取文件和 `TextPipeline.reload()` 之间可能有竞态：文件已更新但尚未 reload，此时 STT 输出使用旧数据。 | 这是可接受的（最终一致性），但需要文档说明。如果需要强一致性，考虑在 reload 期间短暂阻塞新请求 |
| AP-2 | 🟡 中等 | **POST 追加热词的幂等性**。如果追加已存在的热词（同一 source），行为是什么？覆盖？忽略？报错？ | 明确语义：同一 source 不同 target → 覆盖并 warning；同一 source 相同 target → 忽略 |
| AP-3 | 🟢 轻微 | **缺少批量校验**。PUT 整体替换时，如果文件中部分行格式错误，是跳过还是全量拒绝？ | 设计文档说"跳过无效行并 log warning"，这是合理的，但需要确保前端能获取到警告信息 |

**评分**：8/10 — API 设计规范，并发语义可更明确。

---

### 2.6 Web UI 设计

**设计概述**：在配置页新增"热词"标签页，包含文本热词区和正则规则区。

**优点**：
- ✅ **测试框功能实用**：正则即时验证，降低用户使用门槛
- ✅ **快捷按钮设计**：常用规则一键追加，提升用户体验
- ✅ **实时重载**：保存后自动触发 reload，无需重启引擎
- ✅ **降级提示**：reload 失败时显示错误但不影响当前使用

**问题**：

| # | 严重程度 | 问题描述 | 建议 |
|---|---------|---------|------|
| UI-1 | 🟡 中等 | **快捷按钮的具体行为**。"标点符号"按钮点击后是追加预置规则到编辑器？还是一键开启/关闭该类别？如果用户已经有一些自定义规则，追加会不会重复？ | 明确行为：①追加预置规则（已存在的规则不重复追加）；②提供"重置为默认"按钮，方便恢复出厂设置 |
| UI-2 | 🟡 中等 | **热词重复检测的实时性**。输入时实时检测 source 重复，但如果热词文件很大（500+条），前端遍历可能有性能问题。 | 考虑后端提供 `/api/hotwords/validate` 接口，前端输入时异步校验 |
| UI-3 | 🟢 轻微 | **缺少热词导入/导出**。用户可能希望备份或分享热词表。 | 考虑添加导入/导出功能（JSON/CSV 格式） |
| UI-4 | 🟢 轻微 | **缺少热词使用统计**。用户不知道哪些热词是高频使用的，哪些从未命中。 | 考虑在 UI 中显示热词命中次数（如果后端实现了统计功能） |

**评分**：8/10 — UI 设计完善，导入导出和统计功能可后续增强。

---

## 3. 架构评审

### 3.1 整体架构评价

v2.0 架构相比 v1.0 有显著改进，核心变化是**统一数据源**：

```
v1.0 架构（问题）：
Engine
 ├── TextPipeline（自己加载文件，持有 _hotword_map）
 ├── HotwordManager（自己加载文件，持有 _entries）  ← 双重数据源
 └── FunASREngine（需要热词，来源不明）

v2.0 架构（修复）：
Engine
 ├── HotwordManager（唯一数据源，管理热词加载/解析/持久化）
 │    ↓ get_text_replacement_data()
 ├── TextPipeline（消费方，通过构造函数接收 HotwordManager）
 │    ↓ reload callback
 ├── FunASREngine（消费方，通过 reload 回调获取热词列表）
 └── CommandMatcher（已有，不改动）
```

**架构优势**：
- ✅ **单一职责**：HotwordManager 负责数据管理，TextPipeline 负责文本处理，FunASREngine 负责模型推理
- ✅ **依赖注入**：TextPipeline 通过构造函数接收 HotwordManager，便于测试和替换
- ✅ **观察者模式**：reload 回调机制解耦 Web 层和 Core 层
- ✅ **数据流向清晰**：HotwordManager → TextPipeline/FunASREngine，无循环依赖

### 3.2 模块间耦合度

| 模块对 | 耦合类型 | 耦合程度 | 评价 |
|--------|----------|----------|------|
| TextPipeline ↔ HotwordManager | 依赖注入（构造函数） | 松散 | ✅ 良好，便于测试 |
| FunASREngine ↔ HotwordManager | 通过 Engine 中转 | 松散 | ✅ 避免直接依赖 |
| Engine ↔ TextPipeline | 组合 | 中等 | ✅ Engine 管理生命周期 |
| Web API ↔ HotwordManager | 通过 Engine 中转 | 松散 | ✅ 避免 Web 层直接依赖 Core 层 |

**耦合度评估**：整体耦合度合理，符合依赖倒置原则。

### 3.3 数据流向

```
文件系统 (hotwords.txt, hot-rules.txt)
    ↓ load_from_file()
HotwordManager（内存数据 _entries）
    ↓ get_text_hotword_map() / get_model_hotword_list() / get_compiled_rules()
TextPipeline（_hotword_regex, _regex_rules）
    ↓ process()
STT 输出文本 → 处理后文本

Web API 修改
    ↓ save_to_file()
文件系统
    ↓ load_from_file()
HotwordManager（更新 _entries）
    ↓ reload()
TextPipeline（更新 _hotword_regex, _regex_rules）
    ↓ callback
FunASREngine（更新 _hotword_list）
```

**数据流向评价**：
- ✅ 流向清晰，无循环
- ✅ reload 时原子替换引用，避免竞态
- ⚠️ Web API 修改后需要经过"保存→加载→reload"三步，任何一步失败都会导致数据不一致

### 3.4 扩展性评估

**为第二期预留的扩展点**：

| 扩展功能 | 预留点 | 实现难度 |
|----------|--------|----------|
| **音素匹配热词** | HotwordEntry 可添加 `phoneme` 字段 | 低 |
| **LLM 后处理** | TextPipeline 后可添加 `LLMStep` | 中 |
| **纠错记忆** | HotwordManager 可添加 `learn_from_correction()` 方法 | 中 |
| **热词权重** | 热词文件格式支持 `word:weight` | 低 |
| **服务热词** | HotwordConfig 添加 `service_hotwords_file` | 低 |

**扩展性评价**：
- ✅ 数据模型（HotwordEntry）有足够的字段扩展空间
- ✅ Pipeline 模式支持步骤扩展（未来可改为责任链模式）
- ⚠️ 当前 Pipeline 步骤是硬编码的（regex → hotword），动态注册步骤需要重构

### 3.5 与现有模块的集成

| 现有模块 | 集成方式 | 评价 |
|----------|----------|------|
| **CommandMatcher** | 命令优先策略：先匹配命令，未命中再走 pipeline | ✅ 避免功能冲突 |
| **TextInjector** | Pipeline 输出直接传入 injector | ✅ 无侵入 |
| **EventBus** | Pipeline reload 可通过事件通知 UI | ⚠️ 设计文档未明确使用 EventBus，建议统一使用事件总线通知 |

**EventBus 集成建议**：

当前 reload 回调是直接函数调用：
```python
# Engine 注册回调
self._text_pipeline.register_reload_callback(self._on_pipeline_reloaded)
```

建议改为 EventBus 发布事件：
```python
# Engine 订阅事件
self._events.subscribe(EngineEvent.HOTWORD_RELOADED, self._on_pipeline_reloaded)

# TextPipeline reload 完成后发布事件
self._events.publish(EngineEvent.HOTWORD_RELOADED)
```

这样 Web UI 也可以订阅此事件，实现 reload 完成的通知。

### 3.6 架构评分

| 维度 | 权重 | 得分 | 加权得分 |
|------|------|------|---------|
| 模块划分 | 25% | 9/10 | 2.25 |
| 耦合度 | 25% | 8/10 | 2.00 |
| 数据流向 | 20% | 8/10 | 1.60 |
| 扩展性 | 20% | 7/10 | 1.40 |
| 与现有模块集成 | 10% | 7/10 | 0.70 |
| **架构总分** | **100%** | | **7.95/10** |

---

## 4. 技术选型评审

### 4.1 re.sub 交替正则方案

**方案设计**：
```python
sources = sorted(hotword_map.keys(), key=len, reverse=True)
pattern = '|'.join(re.escape(s) for s in sources)
regex = re.compile(pattern, re.IGNORECASE)
```

**性能分析**：
- **时间复杂度**：O(n)，其中 n 是文本长度。正则引擎使用 NFA/DFA，交替模式 `A|B|C` 只需扫描文本一次。
- **空间复杂度**：O(m)，其中 m 是热词总数。预编译正则占用内存与热词总长度成正比。
- **与 str.replace 对比**：
  - `str.replace`：O(n*m)，每条热词都要扫描整个文本
  - `re.sub`：O(n)，一次扫描完成所有替换

**正确性分析**：
- ✅ **长词优先**：`sorted(by len, reverse=True)` 保证长词在正则中先出现，正则引擎优先匹配长词
- ✅ **大小写不敏感**：`re.IGNORECASE` 标志正确支持
- ✅ **特殊字符转义**：`re.escape()` 保证热词中的特殊字符被正确处理
- ⚠️ **边界匹配**：当前方案使用默认匹配（非单词边界），可能替换子串。例如热词 `"CUDA"` 会匹配 `"mycuda"` 中的 `"cuda"`

**边界匹配问题**：

如果热词 `"CUDA"` 应该只匹配独立的词，而不是 `"mycuda"` 的一部分，需要添加单词边界：
```python
pattern = '|'.join(r'\b' + re.escape(s) + r'\b' for s in sources)
```

但这也带来问题：
- 中文没有单词边界，`\b` 对中文热词无效
-  `"Vue.js"` 中的 `.` 会被 `\b` 视为单词边界

**建议**：
- 默认使用子串匹配（当前方案），保持简单
- 在热词文件格式中支持 `"word"` 语法表示精确匹配（带单词边界）
- 或在 HotwordConfig 中添加 `word_boundary: bool` 选项

### 4.2 正则复杂度校验

**方案设计**：
```python
@staticmethod
def _validate_regex_complexity(pattern_str: str) -> bool:
    MAX_LENGTH = 500
    if len(pattern_str) > MAX_LENGTH:
        return False
    # 检测连续 3+ 量词嵌套（简易启发式）
    if re.search(r'(\+|\*|\{[^}]+\}).*(\+|\*|\{[^}]+\}).*(\+|\*|\{[^}]+\})', pattern_str):
        return False
    return True
```

**评价**：
- ✅ **长度限制**：有效防止超长正则
- ⚠️ **嵌套量词检测**：简易启发式，可能漏检或误检
- ❌ **无超时保护**：Python `re` 模块不支持编译/匹配超时

**改进建议**：

考虑引入 `regex` 第三方库（兼容 `re` API，支持 timeout）：
```python
import regex

def _compile_with_timeout(pattern_str: str, timeout: float = 0.5):
    try:
        return regex.compile(pattern_str, timeout=timeout)
    except regex.TimeoutError:
        raise ValueError(f"正则编译超时: {pattern_str[:50]}")
```

如果不允许引入新依赖，当前方案是可接受的（长度限制 + 嵌套量词检测）。

### 4.3 原子写入方案

**方案设计**：
```python
@staticmethod
def _atomic_write(file_path: str, content: str) -> bool:
    import tempfile
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
```

**评价**：
- ✅ **标准方案**：`mkstemp` + `os.replace` 是跨平台原子写入的标准做法
- ✅ **异常清理**：临时文件在异常时被清理
- ✅ **目录创建**：`makedirs(exist_ok=True)` 处理目录不存在的情况
- ✅ **Windows 兼容**：`os.replace` 在 Python 3.3+ 支持 Windows 的原子替换

### 4.4 技术选型评分

| 技术点 | 选型 | 评分 | 说明 |
|--------|------|------|------|
| 热词替换 | re.sub 交替正则 | 8/10 | 性能优秀，边界匹配可优化 |
| ReDoS 防护 | 长度限制 + 嵌套量词检测 | 6/10 | 无超时保护，可考虑 regex 库 |
| 文件写入 | mkstemp + os.replace | 9/10 | 标准方案，跨平台兼容 |
| 大小写不敏感 | re.IGNORECASE | 8/10 | 正确，但回调查找可优化 |

---

## 5. 安全与稳定性评审

### 5.1 原子写入与文件完整性

| 场景 | 风险 | 缓解措施 | 评价 |
|------|------|----------|------|
| 写入过程中断电 | 文件损坏 | mkstemp + os.replace | ✅ 原子性保证 |
| 磁盘空间不足 | 临时文件创建失败 | 异常捕获，不替换原文件 | ✅ 安全 |
| 并发写入 | 竞态条件 | 原子替换，后写入者胜 | ⚠️ 可接受 |

### 5.2 异常降级

| 场景 | 行为 | 评价 |
|------|------|------|
| Pipeline process() 异常 | 返回原文，记录 error 日志 | ✅ 安全 |
| 单条正则规则异常 | 跳过该规则，继续其他规则 | ✅ 容错 |
| reload 异常 | 保留旧数据，记录 error 日志 | ✅ 安全 |
| 回调异常 | 捕获并记录 warning，不影响其他回调 | ✅ 容错 |

### 5.3 ReDoS 防护

| 防护层 | 实现 | 评价 |
|--------|------|------|
| 正则长度限制 | MAX_LENGTH = 500 | ✅ 有效 |
| 嵌套量词检测 | 简易启发式 | ⚠️ 可能漏检 |
| 编译超时 | 未实现（Python re 不支持） | ❌ 缺失 |
| 匹配超时 | 未实现 | ❌ 缺失 |

**建议**：在文档中明确说明 ReDoS 防护的限制，建议用户只使用可信来源的正则规则。

### 5.4 Reload 竞态

| 竞态场景 | 风险 | 缓解措施 | 评价 |
|----------|------|----------|------|
| reload 时文本正在处理 | 使用新旧数据混合 | 原子替换引用 | ✅ 安全 |
| Web API 保存与 STT 处理并发 | 文件读取与写入竞态 | 原子写入，最终一致 | ⚠️ 可接受 |
| 多次快速 reload | 资源浪费 | 无 debounce | 🟡 建议添加 |

**建议添加 debounce**：
```python
def reload(self, debounce_ms: int = 100):
    """带防抖的 reload"""
    if hasattr(self, '_reload_timer'):
        self._reload_timer.cancel()
    
    def _do_reload():
        # ... 实际 reload 逻辑 ...
        pass
    
    self._reload_timer = threading.Timer(debounce_ms / 1000.0, _do_reload)
    self._reload_timer.start()
```

### 5.5 安全与稳定性评分

| 维度 | 得分 | 说明 |
|------|------|------|
| 文件完整性 | 9/10 | 原子写入，跨平台兼容 |
| 异常降级 | 9/10 | 双层容错，日志完整 |
| ReDoS 防护 | 6/10 | 长度限制有效，无超时保护 |
| 竞态处理 | 7/10 | 原子替换，缺少 debounce |
| **安全总分** | **7.75/10** | |

---

## 6. 测试策略评审

### 6.1 测试分层设计

| 层级 | 范围 | 用例数 | 评价 |
|------|------|--------|------|
| Layer 1：单元测试 | Pipeline + HotwordManager + 规则解析 | ~52 | ✅ 覆盖全面 |
| Layer 2：集成测试 | Engine + Pipeline 联动 | ~18 | ✅ 包含命令优先测试 |
| Layer 3：跨平台测试 | 模型分支 + 路径/编码兼容 | — | ✅ 有实际价值 |
| Layer 4：端到端测试 | 完整链路 + Web API | ~15 | ✅ 覆盖 CRUD 和 reload |
| Layer 5：回归测试 | 现有 159+159 测试 | — | ✅ 保证不破坏现有功能 |

**预计新增测试**：~85 个测试用例（v2.0 相比 v1.0 增加了命令优先测试）

### 6.2 关键测试场景覆盖

| 场景 | 覆盖情况 | 评价 |
|------|----------|------|
| 命令优先（说"逗号"触发命令） | ✅ Layer 2 集成测试 | 已覆盖 |
| 命令未命中走 pipeline | ✅ Layer 2 集成测试 | 已覆盖 |
| case_sensitive=True/False | ✅ Layer 1 单元测试 | 已覆盖 |
| 长词优先替换 | ✅ Layer 1 单元测试 | 已覆盖 |
| 原子写入验证 | ✅ Layer 1 单元测试 | 已覆盖 |
| 编码安全（UTF-8 + GBK） | ✅ Layer 1 单元测试 | 已覆盖 |
| reload 竞态 | ⚠️ 未明确覆盖 | 建议添加 |
| 并发 reload + 文本处理 | ⚠️ 未明确覆盖 | 建议添加 |
| FunASR 热词参数格式 | ⚠️ 未明确覆盖 | 需要验证 |

### 6.3 缺失的测试场景

| # | 缺失场景 | 重要性 | 建议 |
|---|---------|--------|------|
| T-1 | **并发 reload + 文本处理竞态** | 🟡 重要 | 模拟 reload 过程中高频调用 process()，验证无异常 |
| T-2 | **FunASR hotword 参数格式验证** | 🟡 重要 | 验证 `hotword` 参数被正确传递给 FunASR |
| T-3 | **Windows 原子写入兼容性** | 🟡 重要 | 在 Windows 开发机验证 `os.replace` 行为 |
| T-4 | **超大热词表性能**（500+ 条） | 🟢 可选 | 基准测试验证性能 |
| T-5 | **内存泄漏**（多次 reload） | 🟢 可选 | 验证无内存泄漏 |

### 6.4 测试策略评分

| 维度 | 得分 | 说明 |
|------|------|------|
| 覆盖率 | 8/10 | 关键场景覆盖，部分边界场景缺失 |
| 可执行性 | 8/10 | 85 个用例合理，分层清晰 |
| 跨平台 | 8/10 | macOS + Windows 覆盖 |
| 回归保障 | 9/10 | 159+159 现有测试 |
| **测试总分** | **8.25/10** | |

---

## 7. 综合评分

### 7.1 维度评分

| 维度 | 权重 | 得分 | 加权得分 |
|------|------|------|---------|
| P0 问题修复 | 15% | 10/10 | 1.50 |
| 模块设计 | 20% | 8/10 | 1.60 |
| 架构设计 | 20% | 8/10 | 1.60 |
| 技术选型 | 15% | 7.5/10 | 1.13 |
| 安全稳定 | 15% | 7.75/10 | 1.16 |
| 测试策略 | 10% | 8.25/10 | 0.83 |
| 文档质量 | 5% | 9/10 | 0.45 |
| **综合** | **100%** | | **8.27/10** |

### **综合评分：82.7 / 100**

**评级**：B+（良好，修复关键问题后可实施）

### 7.2 与 v1.0 对比

| 版本 | 评分 | 主要改进 |
|------|------|----------|
| v1.0 | 65.5/100 | 首轮评审，5 个 P0 问题 |
| v2.0 | 82.7/100 | **+17.2 分**，全部 P0 修复 + 架构优化 |

**改进幅度显著**，v2.0 已达到可实施标准。

---

## 8. 关键问题清单

按严重程度排序：

### 🔴 严重（必须修复，阻塞实施）

**无** — 全部 5 个 P0 问题已在 v2.0 中修复。

### 🟡 中等（建议在第一版中修复或明确）

| # | 问题 | 模块 | 影响 | 建议方案 |
|---|------|------|------|----------|
| **M-1** | **热词冲突检测策略不明确** | HotwordManager | 数据一致性 | 明确冲突定义：①同一 source 不同 target（warning）；②source A 是 source B 的子串（info） |
| **M-2** | **大小写不敏感查找性能** | TextPipeline | 性能 | 预构建 `lower_source → target` 映射，避免每次遍历 |
| **M-3** | **实时模式命令支持不明确** | Engine | 用户体验 | 明确文档：实时模式不支持语音命令，或添加命令匹配逻辑 |
| **M-4** | **FunASR 热词参数格式未验证** | stt_funasr | 功能正确性 | 验证 FunASR 文档，确认 `hotword` 参数格式 |
| **M-5** | **FunASR 线程安全性** | stt_funasr | 稳定性 | 验证 `generate()` 是否线程安全，必要时加锁 |
| **M-6** | **reload 缺少 debounce** | TextPipeline | 性能/稳定性 | 添加防抖机制，避免高频 reload |
| **M-7** | **正则边界匹配问题** | TextPipeline | 功能正确性 | 明确子串匹配语义，或支持 `"word"` 精确匹配语法 |

### 🟢 轻微（可后续迭代优化）

| # | 问题 | 模块 | 建议 |
|---|------|------|------|
| L-1 | 热词使用统计缺失 | HotwordManager | 添加可选的统计功能 |
| L-2 | Web UI 导入/导出缺失 | Web UI | 添加 JSON/CSV 导入导出 |
| L-3 | EventBus 未统一使用 | Engine | reload 完成通过 EventBus 通知 |
| L-4 | 超大热词表性能未知 | TextPipeline | 添加基准测试 |

---

## 9. 优化建议

按优先级排序：

### 🔴 P0 — 实施前必须完成

**无**（全部 P0 已修复）

### 🟡 P1 — 建议第一版实现

#### 建议 1：优化大小写不敏感查找性能

```python
class TextPipeline:
    def __init__(self, config, hotword_manager: HotwordManager):
        # ...
        self._hotword_map: Dict[str, str] = {}
        self._hotword_map_lower: Dict[str, str] = {}  # 新增：lower_source → target
        
    def reload(self):
        new_map = self._hotword_manager.get_text_hotword_map()
        self._hotword_map = new_map
        self._hotword_map_lower = {k.lower(): v for k, v in new_map.items()}  # 预构建
        # ...
    
    def _replace(self, match):
        matched = match.group(0)
        if self._case_sensitive:
            return self._hotword_map.get(matched, matched)
        return self._hotword_map_lower.get(matched.lower(), matched)  # O(1) 查找
```

**收益**：将大小写不敏感查找从 O(n) 优化到 O(1)。

#### 建议 2：明确热词冲突检测策略

```python
class HotwordManager:
    def load_from_file(self, file_path: str) -> int:
        # ... 解析条目 ...
        
        # 冲突检测
        source_to_targets: Dict[str, Set[str]] = {}
        for entry in entries:
            if entry.source in source_to_targets:
                if entry.target not in source_to_targets[entry.source]:
                    logger.warning("热词冲突: '%s' 有多个不同目标: %s", 
                                  entry.source, source_to_targets[entry.source])
            source_to_targets.setdefault(entry.source, set()).add(entry.target)
        
        # 子串冲突检测（长词优先提示）
        sorted_sources = sorted((e.source for e in entries if e.text_replace), 
                               key=len, reverse=True)
        for i, source in enumerate(sorted_sources):
            for longer_source in sorted_sources[:i]:
                if source in longer_source:
                    logger.info("热词子串关系: '%s' 是 '%s' 的子串，短词可能无法匹配", 
                               source, longer_source)
                    break
        
        self._entries = entries
        return len(entries)
```

#### 建议 3：添加 reload debounce

```python
class TextPipeline:
    def __init__(self, ...):
        # ...
        self._reload_timer: Optional[threading.Timer] = None
        self._reload_lock = threading.Lock()
    
    def reload(self, debounce_ms: int = 100):
        """带防抖的 reload"""
        with self._reload_lock:
            if self._reload_timer:
                self._reload_timer.cancel()
            
            def _do_reload():
                self._actual_reload()
            
            self._reload_timer = threading.Timer(debounce_ms / 1000.0, _do_reload)
            self._reload_timer.start()
    
    def _actual_reload(self):
        """实际的 reload 逻辑"""
        # ... 原 reload 逻辑 ...
```

#### 建议 4：明确实时模式命令支持

在文档中添加：
```markdown
## 实时模式与语音命令

实时模式下，STT 输出的是分段文本（流式结果），不是完整的句子。
由于命令匹配器使用 `fullmatch` 全文精确匹配，实时模式下**不支持语音命令**。

如果需要使用语音命令（如"逗号"、"句号"），请使用批量模式。
```

或在 `_on_realtime_segment` 中添加命令匹配（如果产品需求要求）：
```python
def _on_realtime_segment(self, text: str):
    # 实时模式命令匹配（可选）
    if self._command_matcher:
        result = self._command_matcher.match(text)
        if result:
            # 注意：实时模式下命令可能只匹配部分文本
            # 需要谨慎处理，避免误触发
            pass
```

### 🟢 P2 — 可后续迭代

#### 建议 5：支持热词权重（为 FunASR 原生热词预留）

```python
# hotwords.txt 格式扩展
CUDA:1.5 → CUDA
Kubernetes:1.2 → K8s
```

```python
def _parse_hotword_line(line: str) -> Optional[HotwordEntry]:
    # 支持 weight 语法
    match = re.match(r'(.+?)(?::(\d+(?:\.\d+)?))?\s*(?:→|->)\s*(.+)', line)
    if match:
        source, weight_str, target = match.groups()
        weight = float(weight_str) if weight_str else 1.0
        return HotwordEntry(source.strip(), target.strip(), weight=weight)
```

#### 建议 6：Pipeline 步骤注册机制（为 LLM 后处理预留）

```python
class ProcessStep(ABC):
    @abstractmethod
    def apply(self, text: str) -> str:
        pass

class TextPipeline:
    def __init__(self, ...):
        self._steps: List[ProcessStep] = []
    
    def register_step(self, step: ProcessStep, index: int = -1):
        """注册处理步骤"""
        if index == -1:
            self._steps.append(step)
        else:
            self._steps.insert(index, step)
    
    def process(self, text: str) -> ProcessResult:
        for step in self._steps:
            text = step.apply(text)
        return ProcessResult(text=text, is_changed=True)

# 使用
pipeline.register_step(RegexStep(rules))
pipeline.register_step(HotwordStep(hotword_manager))
# 未来：pipeline.register_step(LLMCorrectionStep(...))
```

---

## 10. 架构改进建议

### 10.1 当前架构的局限性

虽然 v2.0 架构相比 v1.0 有显著改进，但仍存在一些局限性：

1. **Pipeline 步骤硬编码**：当前 `process()` 方法中步骤是硬编码的（regex → hotword），不支持动态扩展
2. **热词替换与正则替换耦合**：两者都在 TextPipeline 中，如果未来需要独立控制（如只开热词不开正则），需要修改代码
3. **FunASR 热词与文本热词耦合**：虽然数据源统一，但两者的更新时机和方式不同（FunASR 热词在 reload 时更新，文本热词在 process 时实时使用）
4. **缺少插件机制**：如果用户想添加自定义处理步骤（如自定义拼音匹配），需要修改核心代码

### 10.2 改进方向：插件化 Pipeline

**目标**：将 TextPipeline 改造为可扩展的责任链，支持第三方插件。

**新架构**：

```
TextPipeline（责任链调度器）
 ├── RegexStep（正则替换）
 ├── HotwordStep（热词替换）
 ├── LLMStep（LLM 后处理，预留）
 └── CustomStep（用户自定义插件）
```

**实现**：

```python
from abc import ABC, abstractmethod
from typing import List

class ProcessStep(ABC):
    """处理步骤基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass
    
    @abstractmethod
    def apply(self, text: str) -> str:
        pass
    
    def reload(self):
        """可选：reload 时调用"""
        pass

class TextPipeline:
    """文本处理管线 — 责任链模式"""
    
    def __init__(self, config):
        self._config = config
        self._steps: List[ProcessStep] = []
        self._enabled_steps: Set[str] = set()
    
    def register_step(self, step: ProcessStep):
        """注册处理步骤"""
        self._steps.append(step)
        self._enabled_steps.add(step.name)
    
    def enable_step(self, name: str, enabled: bool = True):
        """启用/禁用步骤"""
        if enabled:
            self._enabled_steps.add(name)
        else:
            self._enabled_steps.discard(name)
    
    def reload(self):
        """reload 所有步骤"""
        for step in self._steps:
            try:
                step.reload()
            except Exception as e:
                logger.warning("步骤 %s reload 失败: %s", step.name, e)
    
    def process(self, text: str) -> ProcessResult:
        if not self._enabled or not text:
            return ProcessResult(text=text, is_changed=False)
        
        original = text
        try:
            for step in self._steps:
                if step.name in self._enabled_steps:
                    text = step.apply(text)
            return ProcessResult(text=text, is_changed=(text != original))
        except Exception as e:
            logger.error("Pipeline 处理异常，降级返回原文: %s", e)
            return ProcessResult(text=original, is_changed=False)

# 具体步骤实现
class RegexStep(ProcessStep):
    def __init__(self, hotword_manager: HotwordManager):
        self._hotword_manager = hotword_manager
        self._rules: List[Tuple[re.Pattern, str]] = []
    
    @property
    def name(self) -> str:
        return "regex"
    
    def reload(self):
        self._rules = self._hotword_manager.get_compiled_rules()
    
    def apply(self, text: str) -> str:
        for pattern, replacement in self._rules:
            try:
                text = pattern.sub(replacement, text)
            except Exception as e:
                logger.warning("正则规则异常: %s", e)
        return text

class HotwordStep(ProcessStep):
    def __init__(self, hotword_manager: HotwordManager, config):
        self._hotword_manager = hotword_manager
        self._config = config
        self._regex: Optional[re.Pattern] = None
        self._map: Dict[str, str] = {}
    
    @property
    def name(self) -> str:
        return "hotword"
    
    def reload(self):
        self._map = self._hotword_manager.get_text_hotword_map()
        sources = sorted(self._map.keys(), key=len, reverse=True)
        pattern = '|'.join(re.escape(s) for s in sources)
        flags = 0 if self._config.case_sensitive else re.IGNORECASE
        self._regex = re.compile(pattern, flags) if sources else None
    
    def apply(self, text: str) -> str:
        if not self._regex:
            return text
        # ... 替换逻辑 ...
        return text

# Engine 初始化
pipeline = TextPipeline(config)
pipeline.register_step(RegexStep(hotword_manager))
pipeline.register_step(HotwordStep(hotword_manager, config))
```

**收益**：
- ✅ 步骤可独立启用/禁用
- ✅ 支持第三方插件（注册自定义 Step）
- ✅ 为 LLM 后处理预留扩展点
- ✅ 测试更方便（可单独测试每个 Step）

### 10.3 改进方向：热词系统分层

**当前问题**：热词系统同时服务于两个不同场景：
1. **STT 前增强**（FunASR 原生热词）：影响模型识别过程
2. **STT 后纠正**（文本替换）：纠正识别错误

这两个场景虽然数据源相同，但使用方式和更新时机不同。

**新架构**：

```
HotwordService（热词服务层）
 ├── STTPreProcessor（STT 前处理）
 │    └── FunASRHotwordProvider → FunASREngine
 └── STTPostProcessor（STT 后处理）
      └── TextHotwordProvider → TextPipeline
```

**实现**：

```python
class HotwordService:
    """热词服务层，统一管理热词数据"""
    
    def __init__(self, config):
        self._manager = HotwordManager(config)
        self._providers: List[HotwordProvider] = []
    
    def register_provider(self, provider: 'HotwordProvider'):
        self._providers.append(provider)
        provider.on_reload(self._manager)
    
    def reload(self):
        """reload 后通知所有 provider"""
        for provider in self._providers:
            try:
                provider.on_reload(self._manager)
            except Exception as e:
                logger.warning("Provider reload 失败: %s", e)
    
    def add_hotword(self, source: str, target: str):
        self._manager.add_hotword(source, target)
        # 可选：自动 reload 或延迟 reload

class HotwordProvider(ABC):
    """热词数据消费方接口"""
    
    @abstractmethod
    def on_reload(self, manager: HotwordManager):
        """热词 reload 时调用"""
        pass

class FunASRHotwordProvider(HotwordProvider):
    """FunASR 原生热词提供者"""
    
    def __init__(self, engine: FunASREngine):
        self._engine = engine
    
    def on_reload(self, manager: HotwordManager):
        hotword_list = manager.get_model_hotword_list()
        self._engine.load_hotwords(hotword_list)

class TextHotwordProvider(HotwordProvider):
    """文本热词提供者"""
    
    def __init__(self, pipeline: TextPipeline):
        self._pipeline = pipeline
    
    def on_reload(self, manager: HotwordManager):
        self._pipeline.reload()
```

**收益**：
- ✅ 明确区分 STT 前/后两种热词使用场景
- ✅ 支持更多 Provider（如未来可能的其他 STT 引擎原生热词）
- ✅ 更清晰的职责边界

### 10.4 改进方向：配置驱动热词规则

**当前问题**：热词规则通过文件配置，但某些规则可能是临时的（如会议中的专有名词），需要更灵活的管理方式。

**新功能**：

```python
class HotwordManager:
    def __init__(self, config):
        # 持久化热词（来自文件）
        self._persistent_entries: List[HotwordEntry] = []
        # 临时热词（运行时添加，不持久化）
        self._temporary_entries: List[HotwordEntry] = []
        # 会话热词（本次会话有效，重启后丢失）
        self._session_entries: List[HotwordEntry] = []
    
    def add_temporary_hotword(self, source: str, target: str):
        """添加临时热词（本次运行有效，不保存到文件）"""
        self._temporary_entries.append(HotwordEntry(source, target))
    
    def add_session_hotword(self, source: str, target: str):
        """添加会话热词（来自外部系统，如会议系统的参会人名单）"""
        self._session_entries.append(HotwordEntry(source, target))
    
    def get_all_entries(self) -> List[HotwordEntry]:
        """获取所有热词（按优先级合并）"""
        # 优先级：session > temporary > persistent
        return self._session_entries + self._temporary_entries + self._persistent_entries
```

**使用场景**：
- **临时热词**：用户通过语音命令"添加热词 XXX → YYY"添加的热词
- **会话热词**：从会议系统同步的参会人名单、项目代号等

### 10.5 架构改进实施建议

| 改进方向 | 实施时机 | 工作量 | 优先级 |
|----------|----------|--------|--------|
| 插件化 Pipeline | 第二期（LLM 后处理） | 2-3 天 | P1 |
| 热词系统分层 | 第二期（音素匹配热词） | 1-2 天 | P2 |
| 配置驱动热词规则 | 第一期（当前） | 0.5 天 | P1（可选） |

### 10.6 架构改进总结

v2.0 架构已达到生产可用水平，上述改进建议属于**锦上添花**，而非**雪中送炭**。建议：

1. **第一期**：按 v2.0 设计实施，快速上线核心功能
2. **第二期**：在 LLM 后处理功能开发时，同步引入插件化 Pipeline 架构
3. **持续优化**：根据用户反馈，逐步完善热词冲突检测、使用统计等功能

---

## 附录 A：v1.0 → v2.0 修复清单验证

### P0 严重问题（全部修复 ✅）

| # | 问题 | v2.0 修复方案 | 验证结果 |
|---|------|-------------|----------|
| P0-1 | Pipeline 与 CommandMatcher 执行顺序导致命令失效 | **命令优先策略**：先 `command_matcher.match(raw_text)`，未匹配再走 pipeline | ✅ 修复完整 |
| P0-2 | `case_sensitive=False` 无法用 `str.replace` 实现 | **改用 `re.sub(re.IGNORECASE)`**：预编译交替正则，一次扫描完成 | ✅ 修复完整 |
| P0-3 | TextPipeline 与 HotwordManager 职责重叠、数据源不统一 | **统一数据源**：HotwordManager 是唯一管理者，TextPipeline 仅消费数据 | ✅ 修复完整 |
| P0-4 | `save_to_file()` 非原子写入 | **原子写入**：`tempfile.mkstemp()` + `os.replace()` | ✅ 修复完整 |
| P0-5 | Pipeline 异常无降级 | **双层容错**：`process()` 顶层 try-catch + `_apply_regex()` 逐条容错 | ✅ 修复完整 |

### P1 中等问题（部分修复 ✅）

| # | 问题 | v2.0 修复方案 | 验证结果 |
|---|------|-------------|----------|
| P1-1 | `_on_realtime_segment` 未集成 pipeline | ✅ 实时模式也走 pipeline | ✅ 修复完整 |
| P1-2 | FunASREngine 热词列表来源不明确 | ✅ 由 engine reload 回调调用 `load_hotwords()` | ✅ 修复完整 |
| P1-3 | ReDoS 防护方案不可行 | ✅ 正则长度限制 500 + 嵌套量词启发式检测 | ⚠️ 部分修复（无超时保护） |
| P1-4 | 运行时增删持久化策略 | 📋 仅内存修改，Web API 保存时统一持久化 | ✅ 设计明确 |
| P1-5 | 热词替换缺少优先级排序 | ✅ `sorted(by len, reverse=True)` + 预编译交替正则 | ✅ 修复完整 |
| P1-6 | reload 通知机制不明确 | ✅ 回调模式：`register_reload_callback()` | ✅ 修复完整 |

### P2 轻微问题（部分修复 ✅）

| # | 问题 | v2.0 修复方案 | 验证结果 |
|---|------|-------------|----------|
| P2-1 | Pipeline reload 触发机制 | ✅ 回调模式 | ✅ 修复完整 |
| P2-2 | 热词文件分隔符兼容 | ✅ 同时支持 `→` 和 `->` | ✅ 修复完整 |
| P2-3 | API 输入校验 | ✅ 定义校验规则 | ✅ 修复完整 |

---

## 附录 B：实施建议

### B.1 实施优先级

| 阶段 | 内容 | 预计时间 | 优先级 |
|------|------|----------|--------|
| **Phase 1** | HotwordManager + TextPipeline 核心实现 | 3-4 天 | 🔴 必须 |
| **Phase 2** | Engine 集成 + FunASR 原生热词 | 2-3 天 | 🔴 必须 |
| **Phase 3** | Web API + UI 实现 | 2-3 天 | 🔴 必须 |
| **Phase 4** | 测试（Layer 1-4） | 2-3 天 | 🔴 必须 |
| **Phase 5** | Windows 部署 + 回归测试 | 1-2 天 | 🔴 必须 |
| **优化项** | 大小写不敏感查找优化、debounce | 0.5-1 天 | 🟡 建议 |
| **增强项** | 热词统计、导入导出 | 1-2 天 | 🟢 可选 |

**总计**：约 12-15 天（含优化项）

### B.2 风险缓解

| 风险 | 缓解措施 |
|------|----------|
| FunASR hotword 参数格式错误 | 先在测试环境验证，失败时降级为纯文本热词 |
| 正则规则误匹配 | 默认预置规则充分测试，提供"禁用规则"功能 |
| 热词表过大影响性能 | 建议上限 500 条，提供性能提示 |
| 文件编码问题 | 强制 UTF-8，异常时 GBK fallback，记录 warning |

### B.3 上线后监控

| 指标 | 监控方式 | 告警阈值 |
|------|----------|----------|
| Pipeline 处理耗时 | 日志记录 | > 100ms |
| 热词 reload 失败次数 | 日志统计 | > 0 |
| 正则规则异常次数 | 日志统计 | > 10/小时 |
| 原子写入失败次数 | 日志统计 | > 0 |

---

## 总结

v2.0 设计文档相比 v1.0 有**质的飞跃**：

1. **全部 5 个 P0 问题已修复**：命令优先策略、re.sub 方案、统一数据源、原子写入、异常降级
2. **架构设计合理**：模块职责清晰，耦合度适中，扩展性良好
3. **技术选型正确**：re.sub 交替正则、原子写入、回调通知
4. **安全稳定性有保障**：双层容错、ReDoS 防护、竞态处理
5. **测试策略完善**：5 层测试覆盖，85+ 测试用例

**综合评分：82.7/100**（相比 v1.0 的 65.5/100，提升 17.2 分）

**评审结论**：
- ✅ **可以进入实施阶段**
- ⚠️ **建议先修复 M-1 至 M-7 中等优先级问题**
- 📋 **架构改进建议可在第二期考虑**

这是一个设计质量高、可实施性强的方案，期待看到上线后的效果！

---

*评审完成时间：2026-04-30 09:30 CST*  
*评审版本：v2.0*  
*评审人：AI Architecture Reviewer*
