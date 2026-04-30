# 热词系统设计文档 v1.0 — 专业评审报告

> **评审人角色**：资深后端架构师  
> **评审日期**：2026-04-30  
> **评审对象**：`docs/plans/2026-04-30-hotword-system-design.md` v1.0  
> **关联源文件**：`core/engine.py`, `core/stt_funasr.py`, `config.py`, `core/command.py`  
> **评审结论**：**有条件通过**（需修复关键问题后方可实施）

---

## 目录

1. [逐模块评审](#1-逐模块评审)
2. [架构评审](#2-架构评审)
3. [技术选型评审](#3-技术选型评审)
4. [安全与稳定性评审](#4-安全与稳定性评审)
5. [测试策略评审](#5-测试策略评审)
6. [综合评分](#6-综合评分)
7. [关键问题清单](#7-关键问题清单)
8. [优化建议](#8-优化建议)

---

## 1. 逐模块评审

### 1.1 TextPipeline（core/text_pipeline.py）

**设计概述**：作为文本处理管线，按正则替换 → 热词替换的顺序依次处理 STT 原始输出。

**优点**：
- 管线模式清晰，单一职责（仅做 regex + hotword）
- `ProcessResult` 返回 `is_changed` 标记便于日志追踪
- `reload()` 支持运行时热重载，符合 Web UI 管理需求
- 异常降级返回原文的设计思路正确

**问题**：

| # | 严重程度 | 问题描述 |
|---|---------|---------|
| TP-1 | 🔴 严重 | **正则执行异常降级机制缺失**。设计文档中提到"pipeline 异常时降级返回原文"，但 `_apply_regex` 和 `_apply_hotwords` 的伪代码中没有 try-catch 包裹。如果某条正则规则 `re.sub()` 抛出异常（如回溯溢出、内存错误），整个 `process()` 会崩溃，导致 STT 输出直接丢失。必须在 `process()` 顶层加 try-catch，且每个 `_apply_regex` 内部也应逐条容错。 |
| TP-2 | 🔴 严重 | **`case_sensitive=False` 实现方案错误**。设计文档注释写"统一转小写比较"，但 `_apply_hotwords` 内部使用 `str.replace()`，该方法**不支持大小写不敏感替换**。例如热词 `CUDA → CUDA`，输入文本 `cuda` 时 `str.replace("cuda", "CUDA")` 看似能匹配，但 `str.replace("CUDA", "CUDA")` 遇到 `cuda` 就无法匹配。正确做法：使用 `re.sub(re.compile(source, re.IGNORECASE), target)` 或者在构建 `_hotword_map` 时预生成大小写不敏感的正则。 |
| TP-3 | 🟡 中等 | **性能假设未经验证**。注释称"500 条遍历微秒级"，但 `str.replace()` 是 O(n*m) 的字符串操作（n=文本长度，m=热词数），在实时模式下高频段落输出时可能成为瓶颈。建议至少做一次基准测试验证，或使用 `re.compile('|'.join(...))` 做批量替换。 |
| TP-4 | 🟢 轻微 | **缺少替换优先级控制**。热词替换顺序是遍历 `_hotword_map` dict 的插入序，但没有"先长词后短词"的排序保证。设计文档风险评估中提到"先长词后短词替换"，但代码设计中未体现排序逻辑。 |

**评分**：7/10 — 整体设计合理，但 `case_sensitive` 实现方案和异常降级是硬伤。

---

### 1.2 HotwordManager（core/hotword.py）

**设计概述**：热词管理器，负责加载、解析、运行时增删热词。

**优点**：
- `HotwordEntry` 数据类设计完善，区分了 `text_replace` 和 `model_hotword` 两种用途
- `add_hotword` / `remove_hotword` 提供运行时操作能力
- 文件格式设计灵活（`→` 分隔、无箭头词、分类标记）

**问题**：

| # | 严重程度 | 问题描述 |
|---|---------|---------|
| HM-1 | 🔴 严重 | **与 TextPipeline 职责严重重叠**。`HotwordManager` 提供了 `get_text_hotwords()` 返回 `{匹配: 替换}` 字典，而 `TextPipeline` 内部也有 `_hotword_map: Dict[str, str]` 和 `load_hotwords()` 方法。两者都能加载热词文件、都能做文本替换。职责边界模糊：谁才是热词数据的"单一真相源"（Single Source of Truth）？如果 `HotwordManager.add_hotword()` 追加了一条热词，`TextPipeline._hotword_map` 是否同步更新？这极易产生数据不一致。 |
| HM-2 | 🔴 严重 | **`save_to_file()` 文件写入非原子性**。设计文档未提及写入策略。如果写入过程中进程崩溃或断电，文件可能损坏（半截写入）。应使用"写临时文件 → rename"的原子写入模式（与 `config.py` 的 `save_config()` 一致）。 |
| HM-3 | 🟡 中等 | **`load_from_file` 和 `save_to_file` 没有编码安全策略**。设计文档风险评估中提到"强制 UTF-8 编码读取，异常时 fallback"，但 `HotwordManager` 的接口设计中没有体现 fallback 机制的具体方案。Windows GBK 环境下需要特别注意。 |
| HM-4 | 🟡 中等 | **运行时增删与文件持久化的同步策略不清晰**。`add_hotword()` 是只修改内存，还是同时调用 `save_to_file()`？如果只修改内存，进程重启后丢失；如果同时写文件，高频操作有性能问题。需要明确策略（建议：内存优先 + 延迟持久化/debounce）。 |
| HM-5 | 🟢 轻微 | **热词冲突检测**。风险评估提到"加载时检测冲突并 log warning"，但没有定义"冲突"的语义：是同一 source 不同 target？还是 source 是另一 source 的子串？后者检测复杂度较高，建议简化为仅检测 key 重复。 |

**评分**：6/10 — 数据模型合理，但与 TextPipeline 的职责划分和文件写入安全性需要重新设计。

---

### 1.3 Engine 集成（core/engine.py 改动）

**设计概述**：在 `_on_stt_complete_inner` 和 `_on_realtime_segment` 中插入 pipeline 调用。

**优点**：
- 改动点少（仅 2 处），侵入性低
- pipeline 结果用 `is_changed` 做日志，不污染核心流程

**问题**：

| # | 严重程度 | 问题描述 |
|---|---------|---------|
| EI-1 | 🔴 严重 | **Pipeline 与 CommandMatcher 的执行顺序问题**。设计文档流程为：

  ```
  raw_text → pipeline.process() → command_matcher.match() → inject
  ```

  这会导致一个严重问题：**正则规则会把语音命令关键词替换掉，使命令匹配失效**。

  具体场景：用户说"逗号"，STT 输出 `"逗号"`。Pipeline 的正则规则 `(^逗号[，。]?)|([，。]?逗号$)` 将其替换为 `"，"`。然后 `command_matcher.match("，")` 尝试匹配命令——但命令匹配器中 `"逗号"` 命令的 pattern 是 `r"逗号"`，对 `"，"` 做 fullmatch 必然失败。

  结果：用户说"逗号"本意是触发命令（TEXT_REPLACE 类型，注入"，"），但 pipeline 先把文本改了，命令匹配器再也匹配不到。

  **根因**：pipeline 和 CommandMatcher 的标点命令功能存在重叠，且执行顺序导致命令失效。

  **建议方案**：
  - 方案 A：**命令优先**。先 `command_matcher.match(raw_text)`，未匹配命令时再 `pipeline.process(text)`。
  - 方案 B：**去重命令表中的标点命令**。既然 pipeline 的正则规则已经处理了标点替换，命令表中的"句号"、"逗号"等标点命令就是冗余的，应删除。
  - 方案 C：**pipeline 感知命令上下文**。pipeline 处理前先检查文本是否匹配命令，匹配则跳过 pipeline。

  **推荐方案 A**，因为命令匹配是高优先级的用户意图（用户明确说了"逗号"就是要打逗号），不应被 pipeline 意外拦截。 |
| EI-2 | 🟡 中等 | **`_on_realtime_segment` 中未集成 pipeline**。当前设计文档只修改了 `_on_stt_complete_inner` 的伪代码，但 `_on_realtime_segment` 的改动也需要明确。实时模式下的每个段落也需要经过 pipeline 处理（清理噪声标记等）。现有代码中 `_on_realtime_segment` 直接 inject，没有命令匹配也没有 pipeline，需要补充设计。 |
| EI-3 | 🟡 中等 | **pipeline 初始化时机不明确**。`TextPipeline` 应该在 `CoreEngine.__init__` 中创建，还是在 `_load_model_async` 中创建？如果在 `__init__` 中创建，config 尚未完全加载可能导致问题。设计文档未说明。 |
| EI-4 | 🟢 轻微 | **pipeline reload 触发机制**。设计文档说"Web UI 修改后无需重启"，但 engine 如何获知 reload 事件？需要明确：是通过 HTTP API 直接调用 `pipeline.reload()`，还是通过事件总线？前者耦合 Web 层和 Core 层，后者更优雅但设计文档未提及。 |

**评分**：5/10 — 执行顺序问题是致命缺陷，必须重新设计流程。

---

### 1.4 FunASR 原生热词（core/stt_funasr.py 改动）

**设计概述**：在 Paraformer 模型推理时传入 `hotword` 参数，SenseVoice 和 Fun-ASR-Nano 不支持。

**优点**：
- 按模型类型分支隔离，不影响其他引擎
- 空列表不传参数，零开销
- 与 pipeline 文本替换互补（模型级增强 + 后处理纠正）

**问题**：

| # | 严重程度 | 问题描述 |
|---|---------|---------|
| FA-1 | 🟡 中等 | **热词列表来源未明确**。设计文档说"从 HotwordManager 提取词列表"，但 `FunASREngine` 是否应该持有 `HotwordManager` 的引用？还是通过 engine 传入？当前 `FunASREngine.__init__` 只接收 `config`，不接收其他依赖。如果让 `FunASREngine` 自己加载热词文件，就绕过了 `HotwordManager`，产生第二份数据源。 |
| FA-2 | 🟡 中等 | **热词列表 reload 时机**。`FunASREngine` 的 `load_hotwords()` 何时调用？模型加载后只调一次？还是 pipeline reload 时也同步更新？如果是后者，`FunASREngine` 需要暴露 reload 接口。 |
| FA-3 | 🟢 轻微 | **Paraformer 热词参数格式**。设计文档写 `kwargs["hotword"] = self._hotword_list`，但 FunASR 的 `hotword` 参数可能需要特定格式（如 `word:weight`），需要确认上游 API 文档。 |

**评分**：7/10 — 设计方向正确，但与 HotwordManager 的数据流向需要理清。

---

### 1.5 API 设计（gui/web_server.py 改动）

**设计概述**：新增热词和正则规则的 CRUD API。

**优点**：
- RESTful 风格统一，与现有 API 一致
- GET 无鉴权，写操作需要 token，安全合理
- 响应格式标准化（`ok` / `message` / `count`）

**问题**：

| # | 严重程度 | 问题描述 |
|---|---------|---------|
| AP-1 | 🟡 中等 | **PUT 整体替换的并发安全**。PUT /api/hotwords 是全量替换热词文件，如果同时有 STT 正在处理，可能出现文件写入与读取的竞态。需要配合 pipeline 的原子 reload 机制。 |
| AP-2 | 🟡 中等 | **缺少 reload 通知机制**。API 保存文件后如何触发 pipeline reload？是 API handler 直接调用 `pipeline.reload()`（强耦合），还是通过某种通知机制？设计文档说"保存成功后自动触发"，但未说明实现路径。 |
| AP-3 | 🟢 轻微 | **缺少输入校验说明**。POST 追加热词时是否校验 source 的合法性（长度、特殊字符）？PUT 整体替换时是否校验格式？如果用户上传了格式错误的文件，会导致 pipeline 加载失败。 |

**评分**：7/10 — API 设计规范，但并发安全和 reload 触发机制需要补充。

---

### 1.6 Web UI 设计

**设计概述**：在配置页新增"热词"标签页，包含文本热词区和正则规则区。

**优点**：
- 测试框功能非常实用（正则即时验证）
- 常用规则快捷按钮降低使用门槛
- 实时重载设计合理（失败降级用旧数据）

**问题**：

| # | 严重程度 | 问题描述 |
|---|---------|---------|
| UI-1 | 🟢 轻微 | **快捷按钮的具体行为未定义**。"标点符号"按钮点击后是追加预置规则到编辑器？还是一键开启/关闭该类别？需要明确交互。 |
| UI-2 | 🟢 轻微 | **缺少热词冲突提示**。Web UI 编辑热词时，如果用户输入了已有的 source，应给予提示。 |

**评分**：8/10 — UI 设计完善，用户体验考虑周到。

---

## 2. 架构评审

### 2.1 整体架构评价

设计采用**管线模式（Pipeline Pattern）**，在 STT 输出和命令匹配之间插入文本处理层。架构图清晰：

```
STT → TextPipeline → CommandMatcher → Injector
```

分层合理，各模块职责在理想状态下是清晰的。但实际实现中存在严重的职责重叠问题。

### 2.2 模块间耦合度

**当前耦合关系**（按设计文档 v1.0）：

```
Engine
 ├── TextPipeline（持有 _hotword_map + _regex_rules）
 ├── HotwordManager（持有 _entries）
 ├── CommandMatcher（持有标点命令）
 └── FunASREngine（需要热词列表）
```

**问题**：

1. **TextPipeline ↔ HotwordManager 双重数据源**：`TextPipeline` 有 `load_hotwords()`，`HotwordManager` 也有 `load_from_file()`。两者读取同一个文件，各自维护内存数据。这是典型的"两个脑袋"问题，极易导致状态不一致。

2. **CommandMatcher 与 TextPipeline 功能重叠**：CommandMatcher 的标点命令（"句号"→"。", "逗号"→"，"）与 TextPipeline 的正则规则完全重复。设计文档没有说明两者的关系和去重策略。

3. **FunASREngine 与 HotwordManager 的依赖路径不清晰**：FunASREngine 需要热词列表，但不清楚从谁获取。

### 2.3 扩展性

**优点**：
- 管线模式天然支持步骤扩展（未来可加 LLM 后处理、纠错记忆等）
- 热词文件格式灵活，支持分类标记

**不足**：
- TextPipeline 的步骤是硬编码的（regex → hotword），不支持动态注册步骤。如果未来需要增加步骤（如 LLM 纠错），需要修改 `process()` 方法。建议采用**责任链模式**或**步骤注册机制**。
- `HotwordEntry` 的 `text_replace` 和 `model_hotword` 布尔标记不够灵活。如果未来有更多热词类型（如音素热词），需要修改数据模型。

### 2.4 架构优化建议

**重构建议：统一数据源**：

```
HotwordManager（唯一数据源）
    ↓ get_text_hotwords()
TextPipeline（消费方，不自己加载）
    ↓ get_model_hotwords()
FunASREngine（消费方）
```

`HotwordManager` 作为热词数据的唯一管理者，`TextPipeline` 和 `FunASREngine` 都是数据的消费方，通过接口获取所需数据。这样：
- 只有一处加载和解析热词文件
- `add_hotword` / `remove_hotword` 只需操作 `HotwordManager`
- reload 时 `HotwordManager` 重新加载，`TextPipeline` 和 `FunASREngine` 从中获取最新数据

**评分**：6/10 — 架构方向正确，但数据源统一和职责边界需要重新梳理。

---

## 3. 技术选型评审

### 3.1 文本替换方案

| 方案 | 优点 | 缺点 | 评审意见 |
|------|------|------|---------|
| `str.replace()` | 简单直接、无依赖 | 不支持大小写不敏感、O(n*m) | ❌ 不推荐 |
| `re.sub()` | 支持大小写不敏感、可批量 | 需要编译正则 | ✅ 推荐 |
| Trie 树 | O(n) 文本扫描 | 实现复杂、维护成本高 | ⚠️ 过度设计 |

**建议**：使用 `re.sub()` + 预编译正则。将所有热词编译为一个大的交替正则 `re.compile('|'.join(sorted(sources, key=len, reverse=True)), re.IGNORECASE)`，一次扫描完成所有替换。这同时解决了大小写不敏感和替换顺序（长词优先）两个问题。

### 3.2 配置管理

沿用现有的 dataclass + YAML 方案，与项目风格一致，合理。

`HotwordConfig` 的字段设计合理，`min_word_length` 是好主意（防止单字误替换）。但 `case_sensitive` 的默认值 `False` 在当前 `str.replace` 方案下无法正确工作。

### 3.3 文件格式

**hotwords.txt**：
- 使用 `→`（U+2192）分隔符有辨识度，但可能在不同编辑器中显示不一致。建议同时支持 `->`（ASCII 箭头）作为 fallback。
- 格式简单直观，用户友好。

**hot-rules.txt**：
- 使用 `=` 分隔，简单有效。
- 正则表达式作为用户可见格式，对非技术用户门槛较高。但 Web UI 的快捷按钮可以弥补。

### 3.4 正则引擎安全

设计文档提到"规则编译时设置超时"防范 ReDoS，但 Python 的 `re` 模块**不支持编译超时**（这是 Python 3.x 的已知限制）。只有 `regex` 第三方库支持超时参数。

**建议**：
- 使用正则复杂度静态分析（如 `re.compile()` 时估算回溯复杂度）
- 限制单条正则长度（如 500 字符）
- 或引入 `regex` 库（`pip install regex`）支持超时

**评分**：6/10 — 选型方向合理，但 `str.replace` 方案和 ReDoS 防护需要调整。

---

## 4. 安全与稳定性评审

### 4.1 数据安全

| 风险 | 严重程度 | 现状 | 建议 |
|------|---------|------|------|
| 热词文件写入非原子性 | 🔴 严重 | `save_to_file()` 未使用原子写入 | 必须使用"写临时文件 + os.replace()" |
| 文件编码异常 | 🟡 中等 | 提到 fallback 但未具体设计 | 读取时强制 UTF-8，异常时 try GBK fallback |
| 配置迁移数据丢失 | 🟢 轻微 | 已有原子写入（config.py 的 save_config） | 热词文件应沿用同样模式 |

### 4.2 运行时稳定性

| 风险 | 严重程度 | 现状 | 建议 |
|------|---------|------|------|
| Pipeline 异常崩溃 | 🔴 严重 | 设计提到降级但未实现 | `process()` 必须 try-catch，异常时返回 `ProcessResult(text=原文本, is_changed=False)` |
| Reload 竞态 | 🟡 中等 | 提到原子替换但未设计 | 使用"构建新数据 → 整体替换引用"模式，避免加锁 |
| 正则 ReDoS | 🟡 中等 | 提到超时但 Python re 不支持 | 限制正则复杂度 + 长度，或换用 regex 库 |

### 4.3 输入安全

| 风险 | 严重程度 | 现状 | 建议 |
|------|---------|------|------|
| API 注入攻击 | 🟢 轻微 | 写操作有 token 保护 | 补充输入校验（长度、格式） |
| 恶意正则规则 | 🟡 中等 | 规则来源是文件和 API | 限制正则长度和复杂度 |

**评分**：5/10 — 文件写入原子性和异常降级是必须修复的安全问题。

---

## 5. 测试策略评审

### 5.1 测试分层

设计文档提出了 5 层测试策略，覆盖率设计优秀：

| 层级 | 范围 | 用例数 | 评审意见 |
|------|------|--------|---------|
| Layer 1：单元测试 | Pipeline + HotwordManager + 规则解析 | ~45 | ✅ 覆盖全面，包含边界用例 |
| Layer 2：集成测试 | Engine + Pipeline 联动 | ~15 | ⚠️ 缺少 pipeline+命令执行顺序的测试 |
| Layer 3：跨平台测试 | 模型分支 + 路径/编码兼容 | — | ✅ 有实际价值 |
| Layer 4：端到端测试 | 完整链路 + Web API | ~15 | ✅ 覆盖了 CRUD 和 reload |
| Layer 5：回归测试 | 现有 159+159 测试 | — | ✅ 保证了不破坏现有功能 |

### 5.2 缺失的测试场景

| # | 缺失场景 | 重要性 |
|---|---------|--------|
| T-1 | **Pipeline 处理后命令匹配失效**的场景测试（说"逗号" → 正则替换为"，" → 命令匹配失败） | 🔴 必须 |
| T-2 | **HotwordManager 与 TextPipeline 数据一致性**测试 | 🔴 必须 |
| T-3 | **并发 reload + 文本处理**的竞态测试 | 🟡 重要 |
| T-4 | **case_sensitive=False** 的各种大小写组合测试 | 🟡 重要 |
| T-5 | **文件写入中途崩溃恢复**测试 | 🟡 重要 |
| T-6 | **超大规则文件**（1000+ 条规则）的性能测试 | 🟢 可选 |

### 5.3 测试可执行性

- ~75 个新测试用例合理，但实际实施可能超时。建议优先实现 Layer 1 和 Layer 2 的关键场景，Layer 3/4 可后续补充。
- "合成音频转写"的端到端测试依赖 TTS + STT，实现成本高，可考虑 mock。

**评分**：7/10 — 测试策略完善，但缺失关键场景（执行顺序问题）需要补充。

---

## 6. 综合评分

| 维度 | 权重 | 得分 | 加权得分 |
|------|------|------|---------|
| 模块设计 | 25% | 7/10 | 1.75 |
| 架构设计 | 20% | 6/10 | 1.20 |
| 技术选型 | 15% | 6/10 | 0.90 |
| 安全稳定 | 15% | 5/10 | 0.75 |
| 测试策略 | 10% | 7/10 | 0.70 |
| 文档质量 | 10% | 9/10 | 0.90 |
| 可实施性 | 5% | 7/10 | 0.35 |
| **综合** | **100%** | | **6.55/10** |

### **综合评分：65.5 / 100**

**评级**：B-（有条件通过）

**通过条件**：
1. 🔴 必须修复 Pipeline 与 CommandMatcher 执行顺序问题
2. 🔴 必须修复 `case_sensitive=False` 的实现方案
3. 🔴 必须统一 TextPipeline 和 HotwordManager 的数据源
4. 🔴 必须实现文件写入原子性
5. 🔴 必须实现 pipeline 异常降级

---

## 7. 关键问题清单

按严重程度排序：

### 🔴 严重（必须修复，阻塞实施）

| # | 问题 | 模块 | 影响 |
|---|------|------|------|
| **P0-1** | **Pipeline 与 CommandMatcher 执行顺序导致命令失效**。正则规则将"逗号"替换为"，"后，命令匹配器无法匹配到原命令文本。用户说"逗号"既不会触发命令，也不会产生任何输出。 | Engine 集成 | 功能失效 |
| **P0-2** | **`case_sensitive=False` 无法用 `str.replace` 实现**。Python 的 `str.replace` 不支持大小写不敏感替换，设计中的"统一转小写比较"方案在保持原文大小写的情况下无法工作。 | TextPipeline | 功能失效 |
| **P0-3** | **TextPipeline 与 HotwordManager 职责重叠、数据源不统一**。两者都加载热词文件、都维护内存数据、都提供热词替换能力。运行时增删操作可能导致内存状态不一致。 | 架构 | 数据不一致 |
| **P0-4** | **`save_to_file()` 非原子写入**。写入过程中断电/崩溃会导致热词文件损坏，下次启动后所有热词丢失。 | HotwordManager | 数据丢失 |
| **P0-5** | **Pipeline 异常无降级**。`_apply_regex` 中某条正则抛异常会导致整个 `process()` 失败，STT 输出文本直接丢失。 | TextPipeline | 数据丢失 |

### 🟡 中等（建议修复）

| # | 问题 | 模块 | 影响 |
|---|------|------|------|
| **P1-1** | **`_on_realtime_segment` 未集成 pipeline**。实时模式下噪声标记（`<|nospeech|>` 等）不会被清理。 | Engine | 功能缺失 |
| **P1-2** | **FunASREngine 热词列表来源不明确**。与 HotwordManager 的数据流向未设计。 | stt_funasr | 架构模糊 |
| **P1-3** | **ReDoS 防护方案不可行**。Python `re` 模块不支持编译超时，设计中的防护无法实现。 | TextPipeline | 安全风险 |
| **P1-4** | **运行时增删的持久化策略缺失**。`add_hotword` 是否同步写文件？高频场景性能如何？ | HotwordManager | 数据丢失/性能 |
| **P1-5** | **热词替换缺少优先级排序**。没有"先长词后短词"保证，可能导致短词误替换长词的一部分。 | TextPipeline | 功能异常 |
| **P1-6** | **API 并发安全未设计**。PUT 全量替换时与 STT 处理可能存在文件读/写竞态。 | API | 数据不一致 |

### 🟢 轻微（可后续优化）

| # | 问题 | 模块 | 影响 |
|---|------|------|------|
| **P2-1** | Pipeline reload 触发机制未明确 | Engine | 可维护性 |
| **P2-2** | 热词文件 `→` 分隔符可能显示异常 | 数据格式 | 用户体验 |
| **P2-3** | API 输入校验说明缺失 | API | 健壮性 |
| **P2-4** | Pipeline 不支持动态步骤注册 | 架构 | 扩展性 |
| **P2-5** | Web UI 快捷按钮行为未定义 | Web UI | 用户体验 |

---

## 8. 优化建议

按优先级排序：

### 🔴 P0 — 阻塞级（必须修复后才能实施）

#### 建议 1：重新设计 Pipeline 和 CommandMatcher 的执行顺序

**推荐方案：命令优先**

```python
def _on_stt_complete_inner(self, text, language, duration_ms, error):
    # ... 错误和空文本处理 ...
    
    # 1. 先尝试命令匹配（使用原始文本）
    if command_enabled:
        result = self._command_matcher.match(text)
        if result:
            # 匹配到命令，直接执行，不经过 pipeline
            self._execute_command(result)
            return
    
    # 2. 未匹配命令，走 pipeline 处理
    pipeline_result = self._text_pipeline.process(text)
    text = pipeline_result.text
    
    # 3. 正常注入
    self._inject_text(text)
```

**理由**：
- 命令是用户的显式意图（"说逗号就是要打逗号"），优先级应高于隐式文本替换
- 避免了 pipeline 规则破坏命令文本的问题
- 不需要修改 CommandMatcher 和 TextPipeline 的内部逻辑

**额外建议**：既然 pipeline 的正则规则已经覆盖了标点替换，可以**考虑移除 CommandMatcher 中的标点命令**（"句号"、"逗号"等 TEXT_REPLACE 类型）。两套系统做同一件事只会增加混乱。但要注意：
- 命令模式下的标点替换是**全文匹配**（只有文本是纯"逗号"时才触发），更精确
- Pipeline 的正则是**部分匹配**（文本中包含"逗号"就会被替换），更宽泛
- 两者的语义不同，需要根据产品需求决定保留哪个

#### 建议 2：使用 `re.sub` 替代 `str.replace` 实现热词替换

```python
def _build_hotword_regex(self):
    """构建大小写不敏感的热词正则"""
    if not self._hotword_map:
        return None
    # 按长度降序排列（长词优先）
    sources = sorted(self._hotword_map.keys(), key=len, reverse=True)
    # 转义特殊字符
    pattern = '|'.join(re.escape(s) for s in sources)
    flags = 0 if self._case_sensitive else re.IGNORECASE
    return re.compile(pattern, flags)

def _apply_hotwords(self, text: str) -> str:
    regex = self._build_hotword_regex()
    if not regex:
        return text
    
    def _replace(match):
        # 找到匹配的热词 source
        matched_text = match.group(0)
        if self._case_sensitive:
            return self._hotword_map.get(matched_text, matched_text)
        # 大小写不敏感：在 map 中查找（忽略大小写）
        for source, target in self._hotword_map.items():
            if source.lower() == matched_text.lower():
                return target
        return matched_text
    
    return regex.sub(_replace, text)
```

**优点**：
- 正确支持大小写不敏感
- 长词优先替换
- 一次扫描完成所有替换（性能优于逐条 str.replace）

#### 建议 3：统一数据源，消除 TextPipeline 和 HotwordManager 的职责重叠

**方案**：`HotwordManager` 是唯一数据管理者

```python
class HotwordManager:
    """热词数据的唯一管理者"""
    def load_from_file(self, path) -> int: ...
    def add_hotword(self, source, target) -> None: ...
    def remove_hotword(self, source) -> bool: ...
    def save_to_file(self) -> bool: ...
    
    # 数据消费者接口
    def get_text_replacement_regex(self) -> Optional[re.Pattern]: ...
    def get_model_hotword_list(self) -> List[str]: ...

class TextPipeline:
    """文本处理管线，仅消费 HotwordManager 的数据"""
    def __init__(self, config, hotword_manager: HotwordManager):
        self._hotword_manager = hotword_manager
    
    def reload(self):
        # 从 HotwordManager 获取最新数据，不自己加载文件
        self._hotword_regex = self._hotword_manager.get_text_replacement_regex()
```

#### 建议 4：实现原子文件写入

```python
import tempfile

def save_to_file(self) -> bool:
    """原子写入热词文件"""
    if not self._file_path:
        return False
    try:
        content = self._serialize_entries()
        dir_path = os.path.dirname(self._file_path) or '.'
        # 使用临时文件 + os.replace 实现原子写入
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(tmp_path, self._file_path)
            return True
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    except Exception as e:
        logger.error("保存热词文件失败: %s", e)
        return False
```

#### 建议 5：Pipeline 异常降级

```python
def process(self, text: str) -> ProcessResult:
    """执行处理链，异常时降级返回原文"""
    if not self._enabled or not text:
        return ProcessResult(text=text, is_changed=False)
    
    original = text
    try:
        text = self._apply_regex(text)
        text = self._apply_hotwords(text)
        return ProcessResult(text=text, is_changed=(text != original))
    except Exception as e:
        logger.error("Pipeline 处理异常，降级返回原文: %s", e, exc_info=True)
        return ProcessResult(text=original, is_changed=False)

def _apply_regex(self, text: str) -> str:
    """逐条应用正则，单条异常不影响其他规则"""
    for pattern, replacement in self._regex_rules:
        try:
            text = pattern.sub(replacement, text)
        except Exception as e:
            logger.warning("正则规则执行异常，跳过: pattern=%s, error=%s", 
                          pattern.pattern[:50], e)
    return text
```

---

### 🟡 P1 — 重要（建议在第一版中修复）

#### 建议 6：明确实时模式的 pipeline 集成

```python
def _on_realtime_segment(self, text: str):
    # 实时模式也走 pipeline（清理噪声标记等）
    pipeline_result = self._text_pipeline.process(text)
    text = pipeline_result.text
    if not text.strip():
        return  # pipeline 清理后为空，跳过注入
    
    separator = self._config.realtime.segment_separator
    # ... 后续注入逻辑不变 ...
```

#### 建议 7：ReDoS 防护方案调整

```python
import re

# 方案 A：限制正则复杂度（推荐，零依赖）
MAX_REGEX_LENGTH = 500
MAX_NESTING_DEPTH = 3

def _validate_regex(pattern_str: str) -> bool:
    if len(pattern_str) > MAX_REGEX_LENGTH:
        return False
    # 简单检测：连续量词嵌套
    if re.search(r'(\+|\*|\{[^}]+\}).*(\+|\*|\{[^}]+\}).*(\+|\*|\{[^}]+\})', pattern_str):
        return False
    return True

# 方案 B：使用 regex 库（如果允许引入依赖）
# import regex
# compiled = regex.compile(pattern_str, timeout=0.5)
```

#### 建议 8：Pipeline reload 通知机制

推荐使用回调/观察者模式，避免 Web 层直接依赖 Core 层：

```python
class TextPipeline:
    def __init__(self, config, hotword_manager):
        self._hotword_manager = hotword_manager
        self._on_reload_callbacks = []
    
    def register_reload_callback(self, callback):
        """注册 reload 完成后的回调"""
        self._on_reload_callbacks.append(callback)
    
    def reload(self):
        # ... 重新加载数据 ...
        for cb in self._on_reload_callbacks:
            try:
                cb()
            except Exception as e:
                logger.warning("Reload callback 异常: %s", e)

# Engine 注册回调
self._text_pipeline.register_reload_callback(
    lambda: self._stt_engine.load_hotwords(self._hotword_manager.get_model_hotword_list())
)
```

---

### 🟢 P2 — 改进（可后续迭代）

#### 建议 9：Pipeline 步骤注册机制

```python
class TextPipeline:
    def __init__(self):
        self._steps: List[ProcessStep] = []
    
    def register_step(self, step: ProcessStep):
        self._steps.append(step)
    
    def process(self, text: str) -> ProcessResult:
        for step in self._steps:
            text = step.apply(text)
        return ProcessResult(text=text, ...)

# 注册步骤
pipeline.register_step(RegexStep(rules))
pipeline.register_step(HotwordStep(hotword_manager))
# 未来：pipeline.register_step(LLMCorrectionStep(...))
```

#### 建议 10：热词文件分隔符兼容

同时支持 `→` 和 `->` 作为分隔符，降低输入门槛。

#### 建议 11：Web UI 热词冲突检测

编辑热词时实时检测 source 重复，高亮提示。

---

## 附录：设计文档整体评价

### 优点
1. **文档质量极高**：结构完整、格式规范、图表清晰，是难得的高质量设计文档
2. **需求分析准确**：问题定义清晰，目标范围合理（第一期聚焦文本替换 + 正则规则）
3. **风险评估到位**：识别了主要的运行时风险并提出了缓解思路
4. **测试策略完善**：5 层测试覆盖，~75 个用例，远超一般项目标准
5. **用户体验考虑周到**：Web UI 的测试框、快捷按钮、实时重载等功能设计贴心

### 核心不足
1. **未分析新模块与现有模块的功能重叠**（Pipeline vs CommandMatcher 的标点处理）
2. **数据管理职责分散**（TextPipeline 和 HotwordManager 都管热词数据）
3. **关键技术方案存在硬伤**（`str.replace` 不支持大小写不敏感）
4. **部分安全设计停留在"提到"层面**（原子写入、异常降级），未给出具体实现

### 总结

这是一份**整体质量优秀的设计文档**，在需求分析、文档规范、测试策略等方面表现出色。但在模块间交互的关键设计点（执行顺序、数据源统一、技术选型）上存在需要修复的硬伤。修复上述 5 个 P0 问题后，即可进入实施阶段。

预计修复工作量：**2-3 小时**（文档修订），不影响整体实施时间线。

---

*评审完成时间：2026-04-30 02:10 CST*  
*评审版本：v1.0*  
*评审人：AI Architecture Reviewer*
