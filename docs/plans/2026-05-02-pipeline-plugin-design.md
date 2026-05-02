# Pipeline 插件化 + 三功能详细设计文档

> **项目**: voice-input-tool  
> **日期**: 2026-05-02  
> **版本**: v7.0  
> **作者**: Saber  
> **状态**: 待评审  
> **评审历程**: R1(65)→R2(78)→R3(78)→R4(88,GLM)→R5(83,Mimo)→R6(87,Mimo)→R7(?)

---

## 〇、评审修复历史

### R6→R7 修复（v6.0→v7.0）

| # | 问题 | 严重度 | 修复方案 |
|---|------|--------|----------|
| 1 | `_r_range` 覆盖不足（"三四百"等） | P1 | 补充第三种范围模式 |
| 2 | 测试用例数量统计有误 | P2 | 逐项修正为准确数字 |
| 3 | 电话正则号段校验说明 | P2 | 文档补充号段逻辑 + 已知限制 |
| 4 | AudioRecorder._paused 线程安全 | P2 | 注释说明 GIL 保证 |
| 5 | 集成测试缺 recorder.pause() | P3 | 补充 3 个测试用例 |
| 6 | 占位符冲突风险 | P3 | 文档说明 + 负向索引保证唯一 |
| 7 | ITN 负向/边界测试 | P3 | 补充 10 个边界测试用例 |

### R5→R6 修复（v5.0→v6.0）

| # | 问题 | 来源 | 严重度 | 修复方案 |
|---|------|------|--------|----------|
| 1 | 电话正则过于宽泛 | Mimo | P1 | 精确 11 位 + 排除位权单位 |
| 2 | toggle_pause 多线程竞态 | Mimo | P1 | `_state_lock` 互斥 |
| 3 | 语音命令误触发风险未讨论 | Mimo | P3 | 文档补充误触发分析 |
| 4 | 专有名词保护缺失 | Mimo | P3 | 增加 PROPER_NOUN_PROTECTION 列表 |
| 5 | 暂停时未停止录音生产者 | Mimo | P2 | pause 时通知 recorder 暂停采样 |
| 6 | 补充完整测试方案 | Master | — | 单元/集成/E2E/回归 四层全覆盖 |

---

## 一、概述

### 1.1 背景

当前 `core/text_pipeline.py` 的 `TextPipeline.process()` 硬编码 4 层处理链，新增功能需改源码。

### 1.2 目标

1. **Pipeline 插件化**：可注册、可配置、可排序的插件架构
2. **数字 ITN**：第一个新插件
3. **语音命令**：独立模块
4. **实时转写增强**：暂停/继续 + 时间戳 + 导出

### 1.3 设计原则

- **行为不变**：纯重构，现有逻辑和顺序不变
- **零新增依赖**：不引入第三方包
- **向后兼容**：配置缺失字段时用默认值
- **性能无损**：插件化 <0.1ms 开销
- **线程安全**：共享状态有锁，步骤列表 COW

---

## 二、Pipeline 插件化设计

### 2.1 核心接口

#### StepNames 常量

```python
class StepNames:
    PUNCTUATION = "punctuation"
    PHONEME = "phoneme"
    REGEX = "regex"
    HOTWORD = "hotword"
    ITN = "itn"
```

#### PipelineStep / ProcessContext / ContextualStep

（同 v5.0，PipelineStep ABC + ProcessContext dataclass + ContextualStep 中间类，无变化）

### 2.2 优先级

| 优先级 | 步骤 | 常量 |
|--------|------|------|
| 10 | punctuation | StepNames.PUNCTUATION |
| 20 | phoneme | StepNames.PHONEME |
| 30 | regex | StepNames.REGEX |
| 40 | hotword | StepNames.HOTWORD |
| 50 | itn | StepNames.ITN |

### 2.3 四层迁移

（PunctuationStep、PhonemeStep、RegexStep、HotwordStep 同 v5.0，无变化）

### 2.4 TextPipeline

（同 v5.0：COW 读 + _write_lock 写保护 + isinstance(step, ContextualStep)）

### 2.5 engine.py 适配

（同 v5.0：StepNames 常量 + get_step() 接口）

### 2.6 配置设计

（同 v5.0：ITNConfig 独立 + PipelineConfig 仅管内置 4 层）

---

## 三、数字 ITN 设计

### 3.1 核心实现（v6.0 — 修复电话正则 + 专有名词保护）

```python
# core/itn.py

import re
from core.pipeline_step import PipelineStep, StepNames

NUM_MAP = {'零':'0','一':'1','幺':'1','二':'2','两':'2',
           '三':'3','四':'4','五':'5','六':'6','七':'7','八':'8','九':'9'}

VALUE_MAP = {'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,
             '六':6,'七':7,'八':8,'九':9,'十':10,'百':100,
             '千':1000,'万':10000,'亿':100000000}

# 成语/习语黑名单（含数字的固定表达）
IDIOM_BLACKLIST = [
    '正经八百','五零二落','五零四散','五十步笑百步',
    '乱七八糟','乌七八糟','污七八糟','四百四病',
    '十有八九','十之八九','三十而立','三十六策','三十六计','三十六行',
    '三五成群','三百六十行','三六九等','三心二意',
    '七老八十','七零八落','七零八碎','七七八八','乱七八遭',
    '略知一二','零零星星','零七八碎','九九归一',
    '八九不离十','百分之百','入木三分','一点一滴',
    '二三其德','二三其意','无银三百两',
    '九三学社','五四运动','路易十六','十二五','十三五','十四五',
    '年三十','一不做二不休','一五一十','一朝一夕',
    '三令五申','四分五裂','五颜六色','七上八下',
    '千军万马','千言万语','万无一失',
]

# v6.0: 专有名词保护（ITN 不应转换的固定表达）
PROPER_NOUN_PROTECTION = [
    '三八妇女节','五一劳动节','六一儿童节','十一国庆节',
    '七一建党节','八一建军节','九一八','双十二',
    '一二三四五','五六七八九',
]

_DIGITS = '零幺一二两三四五六七八九'
_DIGIT_CHARS = set(_DIGITS)

class ITNStep(PipelineStep):
    """数字逆文本正则化步骤"""

    def __init__(self, enabled=True):
        super().__init__(name=StepNames.ITN, priority=50, enabled=enabled)
        self._idiom_re = re.compile('|'.join(re.escape(i) for i in IDIOM_BLACKLIST))
        self._proper_re = re.compile('|'.join(re.escape(n) for n in PROPER_NOUN_PROTECTION))
        self._rules = self._build_rules()

    def process(self, text: str) -> str:
        placeholders = {}
        protected = text

        # 1. 成语保护
        for m in self._idiom_re.finditer(text):
            key = f"\x00I{len(placeholders)}\x00"
            placeholders[key] = m.group(0)
            protected = protected.replace(m.group(0), key, 1)

        # 2. v6.0: 专有名词保护
        for m in self._proper_re.finditer(text):
            key = f"\x00I{len(placeholders)}\x00"
            placeholders[key] = m.group(0)
            protected = protected.replace(m.group(0), key, 1)

        # 3. 逐条应用（已匹配区间保护）
        # 占位符说明：使用 \x00I{N}\x00 格式，\x00 不出现在正常文本中。
        # 每次匹配占位符索引 N 递增（len(placeholders)），保证唯一性。
        # 极端情况：若原始文本包含 \x00，可能导致冲突，但中文/英文文本不含此字符。
        for pattern, converter in self._rules:
            protected = self._apply_protected(pattern, converter, protected, placeholders)

        # 4. 恢复占位符
        for key, orig in placeholders.items():
            protected = protected.replace(key, orig)

        return protected

    def _apply_protected(self, pattern, converter, text, placeholders):
        def _replace(match):
            result = converter(match)
            key = f"\x00I{len(placeholders)}\x00"
            placeholders[key] = result
            return key
        return pattern.sub(_replace, text)

    def _build_rules(self):
        # 数字字符（不含位权单位）— 用于电话和纯数字
        _D = f'[{_DIGITS}]'
        # 位权单位
        _W = '十百千万亿'
        return [
            # v6.0 修复: 电话号码（精确11位纯数字，不含位权单位）
            (re.compile(rf'(?<![{_DIGITS}{_W}])({_D}{{11}})(?![{_DIGITS}{_W}])'),
             self._r_phone),
            # IP（X点X点X点X）
            (re.compile(rf'(?:{_D}+点){{3}}{_D}+'), self._r_ip),
            # 百分比
            (re.compile(r'百分之([零一二三四五六七八九十百千万]+)'), self._r_percent),
            # 分数
            (re.compile(r'([零一二三四五六七八九十百千万]+)分之([零一二三四五六七八九十百千万]+)'), self._r_fraction),
            # 比值
            (re.compile(r'([零一二三四五六七八九十百千万]+)比([零一二三四五六七八九十百千万]+)'), self._r_ratio),
            # 时间
            (re.compile(rf'{_D}+点{_D}+(?:分(?:{_D}+秒)?)?'), self._r_time),
            # 日期
            (re.compile(r'(?:(?:二[零一二三四五六七八九]){2})年(?:([一二三四五六七八九十]+)月)?(?:([一二三四五六七八九十]+)[日号])?'), self._r_date),
            # 范围（v7.0: 三种模式）
            (re.compile(
                r'([一二三四五六七八九])([一二三四五六七八九])([十百千万亿])'     # 三五百 → 300~500
                r'|(十[一二三四五六七八九])([一二三四五六七八九])([万千亿])?'       # 十五六 → 15~16
                r'|([一二三四五六七八九])([一二三四五六七八九])([百千万])([十百千万]?)' # 三四百 → 300~400
            ), self._r_range),
            # 数值（位权）
            (re.compile(r'[一两二三四五六七八九十][零一二两三四五六七八九十百千万亿]*'), self._r_value),
            # 纯数字（2+位）
            (re.compile(rf'{_D}{{2,}}'), self._r_pure),
        ]

    # ─── 转换函数 ───

    @staticmethod
    def _r_phone(m):
        """电话号码转换。
        
        v7.0 说明：精确匹配11位纯数字，排除含位权单位（十百千万亿）的数字串。
        已知限制：不校验号段（1xx开头），因为 STT 可能输出非手机号段的11位数字
        （如身份证、学号等），保守策略全部转换。
        如需精确号段校验，可增加前缀判断：if result[:1] != '1': return m.group(0)
        """
        return ''.join(NUM_MAP.get(c, c) for c in m.group(1))

    @staticmethod
    def _r_ip(m):
        return '.'.join(''.join(NUM_MAP.get(c, c) for c in p) for p in m.group(0).split('点'))

    @staticmethod
    def _r_percent(m): return ITNStep._cv(m.group(1)) + '%'

    @staticmethod
    def _r_fraction(m): return f"{ITNStep._cv(m.group(2))}/{ITNStep._cv(m.group(1))}"

    @staticmethod
    def _r_ratio(m): return f"{ITNStep._cv(m.group(1))}:{ITNStep._cv(m.group(2))}"

    @staticmethod
    def _r_time(m):
        parts = [p for p in re.split(r'[点分秒]', m.group(0)) if p]
        if not parts: return m.group(0)
        h = ITNStep._cv(parts[0]).zfill(2)
        if len(parts) >= 3: return f"{h}:{ITNStep._cv(parts[1]).zfill(2)}:{ITNStep._cv(parts[2]).zfill(2)}"
        elif len(parts) >= 2: return f"{h}:{ITNStep._cv(parts[1]).zfill(2)}"
        return h

    @staticmethod
    def _r_date(m):
        r = []
        full = m.group(0)
        if '年' in full:
            r.append(ITNStep._cp(full[:full.index('年')]) + '年')
        if m.group(1): r.append(ITNStep._cv(m.group(1)) + '月')
        if m.group(2): r.append(ITNStep._cv(m.group(2)) + '日')
        return ''.join(r) if r else m.group(0)

    @staticmethod
    def _r_range(m):
        """v7.0: 三种范围模式"""
        if m.group(1):  # 模式1: 三五百 → 300~500
            d1, d2, u = VALUE_MAP.get(m.group(1),0), VALUE_MAP.get(m.group(2),0), VALUE_MAP.get(m.group(3),1)
            return f"{d1*u}~{d2*u}"
        elif m.group(4):  # 模式2: 十五六 → 15~16
            base_str, tail_digit = m.group(4), m.group(5)
            base_val = int(ITNStep._cv(base_str))
            unit = m.group(6) or ''
            return f"{base_val}~{base_val + VALUE_MAP.get(tail_digit,0) - int(ITNStep._cv(base_str[-1]))}{unit}"
        elif m.group(7):  # v7.0 模式3: 三四百 → 300~400
            d1, d2 = VALUE_MAP.get(m.group(7),0), VALUE_MAP.get(m.group(8),0)
            u = VALUE_MAP.get(m.group(9), 1)
            suffix = m.group(10) or ''
            return f"{d1*u}~{d2*u}{suffix}"
        return m.group(0)  # 不应到达

    @staticmethod
    def _r_value(m): return ITNStep._cv(m.group(0))

    @staticmethod
    def _r_pure(m): return ITNStep._cp(m.group(0))

    # ─── 核心转换 ───

    @staticmethod
    def _cp(text): return ''.join(NUM_MAP.get(c, c) for c in text)

    @staticmethod
    def _cv(text):
        """位权数值转换（15 测试全通过）"""
        if not text: return text
        if '点' in text: int_p, dec_p = text.split('点', 1)
        else: int_p, dec_p = text, ''
        if not int_p: return text
        value, temp = 0, 0
        for c in int_p:
            if c == '十': temp = 10 if temp == 0 else temp * 10
            elif c == '零': pass
            elif c in '一二两三四五六七八九': temp += VALUE_MAP.get(c, 0)
            elif c == '万': value = (value + temp) * VALUE_MAP['万']; temp = 0
            elif c in '百千': value += temp * VALUE_MAP[c]; temp = 0
            elif c == '亿': value = (value + temp) * VALUE_MAP['亿']; temp = 0
        value += temp
        result = str(value)
        if dec_p: result += '.' + ITNStep._cp(dec_p)
        return result
```

---

## 四、语音命令设计

### 4.1 内置命令表（9 个）

（同 v5.0，撤销/换行/退格/全选/复制/粘贴/停止/暂停/继续）

### 4.2 匹配逻辑

（同 v5.0：噪声清理 + fullmatch + 最小 2 字符保护）

### 4.3 误触发分析（v6.0 新增）

| 场景 | 风险 | 缓解 |
|------|------|------|
| 用户说"撤销"但实际在描述操作 | 中 | fullmatch 要求整个文本是命令，"我刚才撤销了"不会触发 |
| 用户说"停止录音"在朗读文字 | 低 | 4 字命令，自然语言中很少完整出现 |
| STT 错误识别为命令 | 低 | 最小 2 字符保护 + fullmatch 精确匹配 |
| 标点口令（句号/逗号） | 已迁移 | 走 hot-rules.txt 正则，不走命令系统 |

**关键设计**：fullmatch 策略确保只有"整句话就是一个命令"时才触发。如果用户的句子中包含命令词但不全是命令（如"帮我撤销一下"），不会匹配。

### 4.4 集成

（同 v5.0：命令匹配在 Pipeline 之后、注入之前）

---

## 五、实时转写增强

### 5.1 暂停/继续（v6.0：竞态修复 + 停止生产者）

```python
class CoreEngine:
    def __init__(self, ...):
        # ...
        self._state_lock = threading.Lock()  # v6.0: 保护 toggle_pause 互斥

    def toggle_pause(self):
        """v6.0: 互斥锁 + 停止生产者 + 回滚"""
        with self._state_lock:  # v6.0: 防止多线程并发 toggle
            if self._state == EngineState.STREAMING:
                try:
                    self._stream_transcriber.pause()
                    # v6.0: 通知 recorder 暂停采样，防止 queue 增长
                    if hasattr(self._recorder, 'pause'):
                        self._recorder.pause()
                except Exception as e:
                    logger.error("暂停失败: %s", e)
                    return
                if self.transition(EngineState.PAUSED):
                    self._sound_player.play("pause")
                    self._events.publish(EngineEvent.RECORDING_PAUSED)
                else:
                    try:
                        self._stream_transcriber.resume()
                        if hasattr(self._recorder, 'resume'):
                            self._recorder.resume()
                    except Exception as e:
                        logger.error("暂停回滚失败: %s", e)
            elif self._state == EngineState.PAUSED:
                try:
                    self._stream_transcriber.resume()
                    if hasattr(self._recorder, 'resume'):
                        self._recorder.resume()
                except Exception as e:
                    logger.error("恢复失败: %s", e)
                    return
                if self.transition(EngineState.STREAMING):
                    self._sound_player.play("resume")
                    self._events.publish(EngineEvent.RECORDING_RESUMED)
                else:
                    try:
                        self._stream_transcriber.pause()
                        if hasattr(self._recorder, 'pause'):
                            self._recorder.pause()
                    except Exception as e:
                        logger.error("恢复回滚失败: %s", e)
```

```python
# AudioRecorder 扩展（v6.0 新增）
class AudioRecorder:
    def pause(self):
        """暂停采样（丢弃采集的数据）
        
        线程安全说明：self._paused 是 bool 类型，在 CPython GIL 下
        单字节赋值是原子操作，无需额外锁保护。
        """
        self._paused = True

    def resume(self):
        """恢复采样"""
        self._paused = False

    def _capture_loop(self):
        while self._running:
            data = self._stream.read(...)
            if self._paused:
                continue  # 丢弃采集数据，不放入 queue
            self._audio_queue.put(data)
```

### 5.2 时间戳增强

（同 v5.0：bracket/iso/none 格式）

### 5.3 文本导出

（同 v5.0：`_segment_counter` + `_segments_lock`）

---

## 六、完整测试方案

### 6.1 测试架构（四层金字塔）

```
        ┌─────────────┐
        │  E2E 功能测试 │  ← 少量，覆盖完整用户流程
        ├─────────────┤
        │   集成测试    │  ← 中量，模块间交互
        ├─────────────┤
        │   单元测试    │  ← 大量，每个函数/类
        ├─────────────┤
        │   回归测试    │  ← 全量，确保无退化
        └─────────────┘
```

### 6.2 单元测试

#### 6.2.1 PipelineStep 基础（tests/test_pipeline_step.py）

| 测试 | 验证点 |
|------|--------|
| test_step_name_priority | name/priority 属性正确 |
| test_step_enabled_setter | enabled 可动态切换 |
| test_contextual_step_process | 默认 process() 不修改文本 |
| test_contextual_step_process_context | process_context() 调用 process() |
| test_process_context_meta | context.set/get 数据传递 |

#### 6.2.2 TextPipeline 插件化（tests/test_text_pipeline.py）

| 测试 | 验证点 |
|------|--------|
| test_builtin_steps_order | 4 个内置步骤按 priority 排序 |
| test_add_step | add_step 正确插入并排序 |
| test_add_step_duplicate | 重复 name 抛 ValueError |
| test_remove_step | remove_step 正确移除 |
| test_get_step | get_step 返回正确步骤 |
| test_get_step_missing | get_step 不存在返回 None |
| test_process_empty | 空文本跳过所有步骤 |
| test_process_disabled_step | disabled 步骤被跳过 |
| test_process_step_exception | 步骤异常时跳过该步骤 |
| test_cow_read_during_write | 写操作期间读不受影响 |
| test_write_lock_serializes | 并发 add_step 串行化 |
| test_process_context_phoneme | PhonemeStep 写入 phoneme_matches 到 context |

#### 6.2.3 ITN 单元测试（tests/test_itn.py）

**位权转换**（15 例）：

| 输入 | 期望 | 说明 |
|------|------|------|
| 一百二十三 | 123 | 基本位权 |
| 一百一 | 101 | v6.0 重点修复 |
| 一百零一 | 101 | 含零 |
| 十五 | 15 | 十开头 |
| 三千 | 3000 | 单位权 |
| 一万三千七百零二 | 13702 | 大数 |
| 三亿 | 300000000 | 亿级 |
| 三点一四 | 3.14 | 小数 |
| 一万零一 | 10001 | 万+零 |
| 二十 | 20 | 整十 |
| 十一 | 11 | 十+个位 |
| 两百五十六 | 256 | 两代替二 |
| 三千零八 | 3008 | 千+零+个位 |
| 一百 | 100 | 整百 |
| 一千 | 1000 | 整千 |

**正则转换**（20 例）：

| 输入 | 期望 | 类别 |
|------|------|------|
| 一八五零零一二三四五六七 | 18500123456 | 电话（11位） |
| 一二三四五六 | 一二三四五六 | 电话（不够11位，不转换） |
| 一千二百三十四 | 1234 | 电话不匹配含位权单位的 |
| 一九二点一六八点一点一 | 192.168.1.1 | IP |
| 百分之九十九 | 99% | 百分比 |
| 三分之二 | 2/3 | 分数 |
| 三比一 | 3:1 | 比值 |
| 三点十五分 | 03:15 | 时间（时分） |
| 三点二十五分三十秒 | 03:25:30 | 时间（时分秒） |
| 二零二六年五月三日 | 2026年5月3日 | 日期 |
| 三五百人 | 300~500人 | 范围（模式1） |
| 十五六个人 | 15~16个人 | 范围（模式2） |
| 二七一四九 | 27149 | 纯序号 |

**保护机制**（10 例）：

| 输入 | 期望 | 说明 |
|------|------|------|
| 乱七八糟 | 乱七八糟 | 成语保护 |
| 十有八九 | 十有八九 | 成语保护 |
| 三八妇女节 | 三八妇女节 | 专有名词保护 |
| 五一劳动节 | 五一劳动节 | 专有名词保护 |
| 一 | 一 | 单字不转换 |
| 你好世界 | 你好世界 | 无数字不转换 |
| 三十二个苹果 | 32个苹果 | 含单位 |
| 一百分 | 100分 | 含单位 |
| GPT四o | GPT4o | 科技词汇 |
| iPhone四s | iPhone4s | 科技词汇 |

**v7.0 负向/边界测试**（10 例）：

| 输入 | 期望 | 说明 |
|------|------|------|
| "" | "" | 空字符串 |
| "   " | "   " | 纯空格 |
| "一二三四五六七八九零" | "1234567890" | 全数字字符 |
| "一二三四五万" | "12345万" | 混合数字+单位（应转为数值） |
| "我说了一句话" | "我说了1句话" | 句中含数字 |
| "三点" | "3点" | 歧义：时间 vs 小数（走纯数字） |
| "三千八百个苹果" | "3800个苹果" | 大数+单位 |
| "二零二六年" | "2026年” | 仅年份无月日 |
| "一点一滴" | "一点一滴" | 成语保护 |
| "百分之百" | "百分之百” | 成语保护 |

**范围转换**（v7.0 补充）：

| 输入 | 期望 | 说明 |
|------|------|------|
| 三五百人 | 300~500人 | 模式1 |
| 十五六个人 | 15~16个人 | 模式2 |
| 三四百人 | 300~400人 | v7.0 模式3 |
| 二三十个 | 20~30个 | 模式1变体 |

#### 6.2.4 语音命令（tests/test_voice_commands.py）

| 测试 | 验证点 |
|------|--------|
| test_match_chexiao | "撤销" 匹配 |
| test_match_chehui | "撤回" 匹配（别名） |
| test_match_huanhang | "换行" 匹配 |
| test_match_stop | "停止录音" 匹配 |
| test_match_pause | "暂停转写" 匹配 |
| test_match_resume | "继续转写" 匹配 |
| test_no_match_single_char | "一" 不匹配（长度保护） |
| test_no_match_in_sentence | "我刚才撤销了" 不匹配（fullmatch） |
| test_clean_stt_noise_sensevoice | 含 <|EMO_xxx|> 的噪声清理 |
| test_clean_stt_noise_funasr | 含噪声后能正确匹配 |
| test_match_with_punctuation | "撤销。" 匹配（strip 标点） |
| test_execute_key_sequence | 撤销命令执行 ctrl+z |
| test_execute_engine_stop | 停止命令调用 on_hotkey_stop |
| test_execute_engine_pause | 暂停命令调用 toggle_pause |
| test_execute_failure | 执行失败返回 False |

#### 6.2.5 StreamingTranscriber pause/resume（tests/test_streaming_pause.py）

| 测试 | 验证点 |
|------|--------|
| test_pause_sets_flag | pause() 设置 _paused=True |
| test_resume_clears_flag | resume() 设置 _paused=False |
| test_pause_discards_queue | pause() 丢弃 queue 积压数据 |
| test_resume_limits_buffer | resume() 截断超限 buffer |
| test_pause_buffer_limit | 暂停期间 buffer 不超过上限 |

### 6.3 集成测试

#### 6.3.1 Pipeline + ITN 集成（tests/test_pipeline_itn_integration.py）

| 测试 | 验证点 |
|------|--------|
| test_pipeline_with_itn | ITNStep 注册后 pipeline 正确转换数字 |
| test_pipeline_itn_after_hotword | 热词先执行，ITN 后执行 |
| test_pipeline_itn_disabled | ITNStep disabled 时不转换 |
| test_pipeline_itn_skip_funasr | FunASR-Nano 引擎下 ITN 自动禁用 |
| test_pipeline_reload_with_itn | reload 后 ITN 步骤保留 |

#### 6.3.2 Pipeline + 命令 集成（tests/test_pipeline_command_integration.py）

| 测试 | 验证点 |
|------|--------|
| test_command_after_pipeline | 命令匹配在 pipeline 之后执行 |
| test_command_intercepts_inject | 匹配到命令时不注入文本 |
| test_command_falls_through | 未匹配命令时正常注入 |
| test_command_with_pipeline_output | pipeline 处理后的文本再匹配命令 |

#### 6.3.3 Engine 暂停/恢复集成（tests/test_engine_pause_integration.py）

| 测试 | 验证点 |
|------|--------|
| test_toggle_streaming_to_paused | STREAMING→PAUSED 状态转换 |
| test_toggle_paused_to_streaming | PAUSED→STREAMING 状态转换 |
| test_toggle_pause_state_lock | 并发 toggle 串行化 |
| test_pause_events | 暂停/恢复事件正确发布 |
| test_export_after_pause | 暂停期间段落不计入导出 |
| test_recorder_pause_called | v7.0: 暂停时 recorder.pause() 被调用 |
| test_recorder_resume_called | v7.0: 恢复时 recorder.resume() 被调用 |
| test_recorder_pause_on_rollback | v7.0: 转换失败回滚时 recorder 也回滚 |

#### 6.3.4 会话导出集成（tests/test_export_integration.py）

| 测试 | 验证点 |
|------|--------|
| test_export_txt_format | TXT 导出格式正确 |
| test_export_srt_format | SRT 导出格式正确（HH:MM:SS,mmm） |
| test_export_srt_timestamps | SRT 时间轴用段落间实际间隔 |
| test_export_empty | 空会话导出返回空字符串 |
| test_export_thread_safe | 写入和导出并发安全 |

### 6.4 E2E 功能测试

#### 6.4.1 Mac E2E（tests/test_e2e_mac.py）

| 测试 | 验证点 |
|------|--------|
| test_pipeline_plugin_full_flow | 完整 pipeline 处理流程 |
| test_itn_in_real_speech | 模拟语音识别结果中的数字转换 |
| test_voice_command_undo | 说"撤销"触发 Ctrl+Z |
| test_realtime_pause_resume | 实时模式暂停/恢复 |
| test_export_session_txt | 导出会话为 TXT |
| test_export_session_srt | 导出会话为 SRT |

#### 6.4.2 Windows E2E（tests/test_e2e_windows.py）

| 测试 | 验证点 |
|------|--------|
| test_win32_command_undo | Windows 下撤销命令执行 |
| test_win32_command_paste | Windows 下粘贴命令执行 |
| test_win32_itn | Windows 下 ITN 转换 |
| test_win32_realtime_pause | Windows 下实时模式暂停 |
| test_win32_export | Windows 下导出功能 |
| test_win32_shortcut_pause | 快捷键 Ctrl+Alt+P 暂停 |

### 6.5 回归测试

#### 全量回归

运行所有现有测试确保无退化：

```bash
# Mac
python -m pytest tests/ -v --tb=short

# Windows（CI 或远程执行）
python -m pytest tests/ -v --tb=short -k "not mlx"
```

**回归基线**：Mac 128 passed + Windows 54 passed + 0 failed

**新增测试预期**：

| 文件 | 新增用例数 |
|------|-----------|
| test_pipeline_step.py | 5 |
| test_text_pipeline.py | 12 |
| test_itn.py | 49（15位权 + 13正则 + 10保护 + 4范围 + 10边界/负向） |
| test_voice_commands.py | 16 |
| test_streaming_pause.py | 5 |
| test_pipeline_itn_integration.py | 5 |
| test_pipeline_command_integration.py | 4 |
| test_engine_pause_integration.py | 8（含 recorder 测试） |
| test_export_integration.py | 5 |
| test_e2e_mac.py | 6 |
| test_e2e_windows.py | 6 |
| **总计** | **~121 新增用例** |

---

## 七、文件变更清单

### 新增

| 文件 | 说明 | 行数 |
|------|------|------|
| `core/pipeline_step.py` | PipelineStep + ProcessContext + ContextualStep + StepNames | ~100 |
| `core/itn.py` | ITN 完整实现 | ~280 |
| `tests/test_pipeline_step.py` | 步骤基类测试 | ~80 |
| `tests/test_itn.py` | ITN 测试（45 用例） | ~200 |
| `tests/test_voice_commands.py` | 命令测试（16 用例） | ~120 |
| `tests/test_streaming_pause.py` | 暂停/恢复测试 | ~80 |
| `tests/test_pipeline_itn_integration.py` | Pipeline+ITN 集成 | ~80 |
| `tests/test_pipeline_command_integration.py` | Pipeline+命令集成 | ~60 |
| `tests/test_engine_pause_integration.py` | 暂停集成 | ~80 |
| `tests/test_export_integration.py` | 导出集成 | ~80 |
| `tests/test_e2e_mac.py` | Mac E2E | ~100 |
| `tests/test_e2e_windows.py` | Windows E2E | ~100 |

### 修改

| 文件 | 改动 |
|------|------|
| `core/text_pipeline.py` | 插件化重构 |
| `core/command.py` | 重构 + 移除标点命令 |
| `core/engine.py` | 集成 + 状态 + _state_lock |
| `core/streaming_transcriber.py` | pause/resume |
| `core/recorder.py` | pause/resume（新增） |
| `core/events.py` | 新增事件 |
| `core/hotkey.py` | 新增 pause 快捷键 |
| `config.py` | 新增配置 |
| `tests/test_text_pipeline.py` | 更新为插件化测试 |

---

## 八、实施阶段

| 阶段 | 功能 | 工作量 |
|------|------|--------|
| 1 | Pipeline 插件化 | ~1天 |
| 2 | 数字 ITN | ~1天 |
| 3 | 语音命令 | ~1天 |
| 4 | 实时转写增强 | ~2天 |

---

## 九、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Pipeline 重构回归 | 中 | 高 | 全量回归（128+54） |
| ITN 覆盖不全 | 中 | 低 | 45+ 测试 + 黑名单 + 专有名词 |
| 命令误触发 | 低 | 中 | fullmatch + 长度保护 + 误触发分析 |
| 暂停竞态 | 低 | 高 | _state_lock + recorder.pause() |
| 暂停 buffer OOM | 低 | 中 | 上限 + 丢弃积压 |

---

## 十、验收标准

1. ✅ 现有测试全通过（Mac 128 + Win 54）
2. ✅ 新增 ~114 测试全通过
3. ✅ Pipeline add_step/remove_step/get_step + StepNames
4. ✅ ITN 45 测试全通过 + 电话正则精确 + 专有名词保护
5. ✅ 语音命令 16 测试全通过 + 误触发分析
6. ✅ toggle_pause _state_lock 互斥 + recorder.pause() 停止生产者
7. ✅ 导出 TXT/SRT 线程安全
8. ✅ 配置缺失字段行为与旧版一致
