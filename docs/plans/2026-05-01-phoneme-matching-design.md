# 音素匹配热词系统 — 设计文档

> 版本: v5.0  
> 日期: 2026-05-01  
> 状态: 待评审（第 5 轮）  
> 变更: v4 评审反馈修复（测试方案补全 + 性能阈值修正 + 并发测试 + Mock 策略明确化）

## 1. 背景与动机

### 1.1 当前痛点

现有热词系统仅支持**精确文本替换**（`str.replace` / `re.sub`），只能匹配 STT 识别结果中**完全一致**的字符串。

实际使用中大量错误是**同音/近音字**导致的：
- 撒贝你 → 撒贝宁（中文同音字）
- 东方菜富 → 东方财富（中文近音字）
- 科大迅飞 → 科大讯飞（前后鼻音混淆）
- 月清 → 乐清（同音不同字）

### 1.2 错误类型分析

语音识别错误分为三类，需要不同的修复策略：

| 错误类型 | 示例 | 匹配策略 | 复杂度 |
|---------|------|---------|--------|
| **中文同音/近音字** | 撒贝你→撒贝宁、东方菜富→东方财富 | 音素匹配（本方案） | 中 |
| **中文音译→英文原名** | 酷打→CUDA、克劳德→Claude | 规则映射（hot-rules.txt 已有） | 低 |
| **英文拼写错误** | claude→Claude、pythn→Python | 英文字符级 LCS | 中 |

**关键设计决策**：音素匹配**不做跨语言匹配**。原因：
1. 中文拼音空间（声母+韵母+声调）与英文字符空间本质不同，强行统一会导致大量误匹配
2. 中文 STT 输出英文专有名词时，通常输出中文音译（"酷打"而非"CUDA"），需要的是映射而非音素匹配
3. 中文音译→英文原名的映射已有现成方案：`hot-rules.txt` 规则替换（`酷打 = CUDA`）

### 1.3 参考实现

[CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline) 的 `util/hotword/` 模块实现了完整的音素匹配方案，核心创新点：

1. **Phoneme 数据结构**：带语言属性（`lang`）、字边界标记（`is_word_start/end`）、字符位置（`char_start/end`）
2. **音素 RAG 检索**：两阶段——FastRAG 倒排索引粗筛 + 精确模糊匹配
3. **相似音素表**：前后鼻音(`an/ang`)、平翘舌(`z/zh`)、鼻边音(`l/n`) 等
4. **英文处理**：驼峰拆分 + 小写化 + 字符级 LCS 匹配（不跨语言）

**CapsWriter 的局限（本方案改进点）**：
- CapsWriter 同样不做跨语言匹配（`lang` 不同时 cost=1.0），跨语言依赖 `hot-rule.txt`
- CapsWriter 依赖 Numba JIT（我们的规模不需要）
- CapsWriter 阈值硬编码（我们可配置 + 自适应）

## 2. 设计方案

### 2.1 架构概览

在现有 TextPipeline 中**有序级联**增加音素匹配层（作为第一层）：

```
STT 原文
  ↓
[音素匹配纠错]  ← 新增（PhonemeCorrector）— 第一层
  ↓               处理：中文同音字 + 英文拼写错误
[正则规则清理]   ← 现有（hot-rules.txt）— 第二层
  ↓               处理：中文音译→英文映射 + 格式化
[文本替换]       ← 现有（hotwords.txt）— 第三层
  ↓               处理：精确字符串替换（缩写/别名）
注入
```

**级联语义**：三层**有序执行**，上一层输出作为下一层输入。各层职责不重叠：
- 音素匹配：模糊纠错（发音相似但字形不同的错误）
- 正则规则：精确映射（用户显式定义的等价关系）
- 文本替换：精确替换（已知的别名/缩写关系）

> 注：如果音素纠错将"撒贝你"替换为"撒贝宁"，该结果会继续经过正则层和文本层，确保后续规则仍有机会生效。

### 2.2 新增文件

```
core/phoneme/
├── __init__.py           # 导出公开接口
├── phoneme_types.py      # Phoneme 数据结构 + 音素转换 + 文本规范化
├── phoneme_similarity.py # 相似度计算（模糊音权重 + LCS + 编辑距离）
├── phoneme_index.py      # 倒排索引（粗筛）
└── phoneme_corrector.py  # 音素纠错器（编排层）
```

**不引入**：Numba JIT、g2p-en、NumPy（纯 Python 实现，零新依赖）

### 2.3 数据文件

新增 `hotwords-phoneme.txt`（与现有 `hotwords.txt` 分离）：

```
# 音素热词文件 — 基于发音相似度匹配
# 每行一个热词，# 开头为注释
# 
# 中文热词：同音/近音字自动纠错
撒贝宁
东方财富
科大讯飞
乐清

# 英文热词：拼写错误自动纠错（匹配时忽略大小写）
CapsWriter
Claude
Python
PyTorch
Docker
GitHub

# 中英文混合热词
OpenClaw
Hugging Face
```

**与现有文件的关系**：
| 文件 | 匹配方式 | 延迟 | 适用场景 |
|------|---------|------|---------|
| `hotwords.txt` | 精确字符串 | 微秒级 | 缩写/别名（`macOS`、`K8s`） |
| `hot-rules.txt` | 正则/等号规则 | 微秒级 | 中文音译→英文（`酷打 = CUDA`）、格式化（`毫安时 = mAh`） |
| `hotwords-phoneme.txt`（新增） | 音素相似度 | 毫秒级 | 同音字（`撒贝你→撒贝宁`）、拼写错误（`claude→Claude`） |

### 2.4 核心数据结构

#### 2.4.1 Phoneme

```python
from dataclasses import dataclass
from typing import List, Dict, Tuple

@dataclass(frozen=True)
class Phoneme:
    """带语言属性的音素，不可变（hashable，可用作 dict key）"""
    value: str           # 音素值（中文：声母/韵母/声调，英文：整词小写，数字：字符）
    lang: str            # 语言类型：'zh'=中文, 'en'=英文, 'num'=数字
    is_word_start: bool  # 是否是字/词边界起始
    is_word_end: bool    # 是否是字/词边界结束
    char_start: int      # 原文本中的起始字符索引（inclusive）
    char_end: int        # 原文本中的结束字符索引（exclusive）
```

#### 2.4.2 MatchResult & CorrectionResult

```python
@dataclass(frozen=True)
class MatchResult:
    """单次匹配结果"""
    char_start: int      # 原文本中的匹配起始位置
    char_end: int        # 原文本中的匹配结束位置（exclusive）
    score: float         # 相似度分数（0.0 ~ 1.0）
    hotword: str         # 匹配到的热词原文
    original: str        # 被替换的原文片段（方便日志/调试）

@dataclass(frozen=True)
class CorrectionResult:
    """纠错完整结果"""
    text: str                              # 纠错后的文本
    matches: List[MatchResult]             # 实际执行的替换列表
    candidates: List[Tuple[str, str, float]]  # 相似但未替换的候选项 (原词, 热词, 分数)
```

#### 2.4.3 文本→音素转换

```python
def text_to_phonemes(text: str) -> List[Phoneme]:
    """将文本转为音素序列
    
    中文: pypinyin (声母 + 韵母 + 声调) — 每字 2~3 个 Phoneme
    英文: 驼峰拆分后逐单词存储（lang='en'）— 每词 1 个 Phoneme，value 为全小写
    数字: 逐字符（lang='num'）
    其他: 跳过（标点、空白等不产生音素）
    
    大小写处理：
    - 英文 Phoneme.value 统一为小写（Claude → value='claude', lang='en'）
    - 热词和输入都先小写化，确保 'Claude' 和 'claude' 匹配
    - 但 char_start/char_end 指向原文本位置，替换时使用热词原文（保留原始大小写）
    
    示例：
    "撒贝你"  → [Phoneme(s,zh,T,T,0,1), Phoneme(a,zh,F,F,0,1), ..., Phoneme(ni,zh,F,T,2,3)]
    "Claude"  → [Phoneme(claude,en,T,T,0,6)]
    "PyTorch" → normalize → "py torch" → [Phoneme(py,en,T,F,0,2), Phoneme(torch,en,F,T,2,7)]
    """
```

**中文处理细节**（参考 CapsWriter `algo_phoneme.py`）：
- `pypinyin` 提取声母（Style.INITIALS）、韵母（Style.FINALS）、声调（Style.TONE3）
- 每个字产生 2~3 个 Phoneme：声母(is_word_start=T) + 韵母 + 声调(is_word_end=T)
- 声母为空（零声母如"啊"）时跳过声母 Phoneme
- pypinyin 无法处理的字（`errors='ignore'`）降级为单字 Phoneme（value=原字, lang='zh'）

**英文处理细节**：
- 驼峰拆分：正则 `[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|$|\d)|\d+`
  - `PyTorch` → `['Py', 'Torch']` → 小写 → `py`, `torch`
  - `iPhone15Pro` → `['i', 'Phone', '15', 'Pro']` → `i`, `phone`, `15`, `pro`
- 连字符/空格分割：`Hugging Face` → `hugging`, `face`
- 整词作为单个 Phoneme（`lang='en'`），匹配时用 LCS 字符级相似度
- **不做字母→拼音映射**

#### 2.4.4 文本规范化

```python
import re

def normalize_text(text: str) -> str:
    """英文驼峰拆分 + 分隔符统一为空格 + 全小写
    
    CapsWriter   → "caps writer"
    iPhone15Pro  → "iphone 15 pro"
    Hugging Face → "hugging face"  (已空格分隔，不变)
    
    注意：仅用于英文单词的规范化。中文部分不参与规范化。
    """
    # 1. 驼峰拆分：大写字母前插入空格
    result = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    result = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', result)
    # 2. 分隔符统一
    result = result.replace('-', ' ').replace('_', ' ')
    # 3. 合并连续空白 + 小写
    result = ' '.join(result.split()).lower()
    return result
```

### 2.5 相似度计算

#### 2.5.1 相似音素表（预构建字典）

```python
# 启动时从 set 列表构建为扁平字典，O(1) 查找
SIMILAR_PHONEME_SETS = [
    # 前后鼻音
    ('an', 'ang'), ('en', 'eng'), ('in', 'ing'), ('ian', 'iang'), ('uan', 'uang'),
    # 平翘舌
    ('z', 'zh'), ('c', 'ch'), ('s', 'sh'),
    # 鼻音/边音
    ('l', 'n'),
    # 唇齿音/声门音
    ('f', 'h'),
    # 常见易混韵母
    ('ai', 'ei'), ('o', 'uo'), ('e', 'ie'),
    # 清浊音/送气不送气
    ('p', 't'), ('p', 'b'), ('t', 'd'), ('k', 'g'),
    # r/l 混淆（部分方言）
    ('r', 'l'),
]

# 预构建：每个音素 → 其所有相似音素的 set
SIMILAR_PHONEMES: Dict[str, Set[str]] = {}
for a, b in SIMILAR_PHONEME_SETS:
    SIMILAR_PHONEMES.setdefault(a, set()).add(b)
    SIMILAR_PHONEMES.setdefault(b, set()).add(a)

# 查找代价 O(1)，而非遍历 list of set 的 O(n)
def is_similar_phoneme(p1: str, p2: str) -> bool:
    return p2 in SIMILAR_PHONEMES.get(p1, set())
```

#### 2.5.2 音素匹配代价

```python
def phoneme_cost(p1: Phoneme, p2: Phoneme) -> float:
    """计算两个音素的匹配代价 (0.0=完全匹配, 1.0=完全不匹配)
    
    规则（按优先级）：
    1. 不同 lang → 1.0（不跨语言匹配）
    2. 相同 value（已统一小写） → 0.0
    3. 中文相似音素（前后鼻音、平翘舌等） → 0.5
    4. 英文单词 → 1.0 - LCS(s1, s2) / max(len(s1), len(s2))
    5. 声调差异（lang='zh' 且 value 为纯数字） → 0.5
    6. 其他 → 1.0
    """
    if p1.lang != p2.lang:
        return 1.0
    if p1.value == p2.value:
        return 0.0
    # 中文相似音素
    if p1.lang == 'zh':
        if is_similar_phoneme(p1.value, p2.value):
            return 0.5
        # 声调差异：声调 Phoneme 的 value 是数字（'1'~'5'）
        if p1.value.isdigit() and p2.value.isdigit():
            return 0.5
    # 英文单词字符级相似度
    if p1.lang == 'en':
        lcs_len = lcs_length(p1.value, p2.value)
        max_len = max(len(p1.value), len(p2.value))
        if max_len > 0:
            return 1.0 - (lcs_len / max_len)
    return 1.0
```

#### 2.5.3 LCS 长度计算

```python
def lcs_length(s1: str, s2: str) -> int:
    """最长公共子序列长度（滚动数组优化，空间 O(min(m,n))）"""
    if len(s1) < len(s2):
        s1, s2 = s2, s1  # 确保 s1 较长
    m, n = len(s1), len(s2)
    if n == 0:
        return 0
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i-1] == s2[j-1]:
                curr[j] = prev[j-1] + 1
            else:
                curr[j] = max(prev[j], curr[j-1])
        prev, curr = curr, prev
    return prev[n]
```

#### 2.5.4 模糊子串编辑距离（核心算法）

```python
def fuzzy_substring_search(
    main_seq: List[Phoneme],   # 输入文本音素序列（长）
    sub_seq: List[Phoneme],    # 热词音素序列（短）
) -> List[Tuple[float, int, int]]:
    """在主序列中搜索子序列的最佳模糊匹配
    
    使用标准 Levenshtein 编辑距离，代价函数为 phoneme_cost。
    
    算法描述（精确）：
    ─────────────────────────────────────────────
    目标：找到 main_seq 中与 sub_seq 最相似的子串片段。
    
    DP 定义：
      dp[i][j] = sub_seq[0:i] 与 main_seq 中以 main_seq[j-1] 结尾的某个子串
                 之间的最小编辑距离。
    
    尺寸：dp 是 (n+1) × (m+1) 矩阵，n=len(sub_seq), m=len(main_seq)。
    
    初始化（第一行 — dp[0][j]）：
      dp[0][0] = 0                          # 空子序列匹配空子串，代价 0
      dp[0][j] = 0  if main_seq[j].is_word_start else +∞   # j ≥ 1
      ↑ 含义：sub_seq 为空时，允许从 main_seq 的任意字边界位置"免费开始"匹配，
              非字边界位置不允许作为起点。
    
    初始化（第一列 — dp[i][0]）：
      dp[i][0] = i   for i ≥ 1
      ↑ 含义：main_seq 为空时，sub_seq 的前 i 个音素全部需要删除，代价 i。
    
    状态转移（i ≥ 1, j ≥ 1）：
      match_cost = phoneme_cost(sub_seq[i-1], main_seq[j-1])
      dp[i][j] = min(
          dp[i-1][j]   + 1.0,     # 删除：跳过 sub_seq 的一个音素
          dp[i][j-1]   + 1.0,     # 插入：跳过 main_seq 的一个音素
          dp[i-1][j-1] + match_cost  # 匹配/替换
      )
    
    结果收集：
      遍历 dp[n][j] (j=1..m)，找到最小距离 min_dist。
      通过回溯 dp 路径确定匹配的起始位置 start_idx。
      
      最终匹配位置：(start_idx, end_pos)，其中 end_pos = j。
      约束：start_idx 必须在字边界上。
    ─────────────────────────────────────────────
    
    优化：使用滚动数组将空间从 O(n×m) 降至 O(n)。
    回溯：路径记录数组 path[i][j] 记录每步的来源方向，用于确定 start_idx。
    
    Returns:
        List[(score, start_idx, end_idx)] — 按分数降序排列
        score = 1.0 - (distance / len(sub_seq))
    """
```

**相似度公式**：
```python
score = 1.0 - (distance / len(sub_seq))
```
- `distance = 0` → `score = 1.0`（完全匹配）
- `distance = len(sub_seq)` → `score = 0.0`（完全不匹配）
- **分母使用 `len(sub_seq)`**（热词长度），语义为"热词中平均每个音素的偏差程度"
- 为什么不用 `max(len_a, len_b)`：避免短匹配（如 2 音素热词 vs 50 音素长文本）被序列长度差异稀释，导致误匹配

#### 2.5.5 自适应阈值

```python
def adaptive_threshold(base_threshold: float, hotword_len: int) -> float:
    """根据热词长度自适应调整阈值
    
    短词需要更高阈值（容忍度低），长词可以适当降低。
    
    公式：threshold = base_threshold + (1.0 - base_threshold) * max(0, (4 - hotword_len)) / 4
    
    示例（base_threshold=0.7）：
      2 字热词 → 0.7 + 0.3 * 2/4 = 0.85  （更严格）
      3 字热词 → 0.7 + 0.3 * 1/4 = 0.775
      4 字热词 → 0.7 + 0.0 = 0.70  （基准）
      5+字热词 → 0.70              （基准）
    
    理由：2 字热词如"乐清"极易误匹配到其他同音片段，
    需要更严格的阈值避免误报。4 字及以上热词信息熵足够，
    使用用户设定的基准阈值即可。
    """
    if hotword_len < 4:
        adjustment = (1.0 - base_threshold) * (4 - hotword_len) / 4
        return min(1.0, base_threshold + adjustment)
    return base_threshold
```

### 2.6 检索策略（两阶段）

#### 2.6.1 阶段一：倒排索引粗筛（PhonemeIndex）

```python
class PhonemeIndex:
    """倒排索引：按热词前 2 个音素的 value 分桶
    
    检索时只匹配音素在输入中出现过的热词，减少 90%+ 的计算量。
    
    索引策略：
    - 中文：取前 2 个音素（通常是第一字的声母+韵母）
    - 英文：取前 2 个音素（容错首音素识别错误，如 klaude→Claude）
    - 同时将相似音素纳入索引（r/l 互索引、an/ang 互索引）
    """
    
    def add(self, hotword: str, phonemes: List[Phoneme]) -> None:
        """添加热词到索引"""
        if not phonemes:
            return
        # 取前 2 个音素 value 作为索引 key
        for i in range(min(2, len(phonemes))):
            key = phonemes[i].value
            self._index[key].append((hotword, phonemes))
            # 同时索引相似音素
            for sim in SIMILAR_PHONEMES.get(key, set()):
                self._index[sim].append((hotword, phonemes))
    
    def get_candidates(self, input_phonemes: List[Phoneme]) -> List[Candidate]:
        """返回候选热词 + 锚点位置（输入中匹配到的音素索引）
        
        去重：同一热词可能被多个索引 key 命中，合并其锚点位置。
        """
```

#### 2.6.2 阶段二：锚点窗口精确匹配

```python
def search(
    input_phonemes: List[Phoneme],
    candidates: List[Candidate],
    base_threshold: float,
) -> List[MatchResult]:
    """对每个候选，在其锚点位置附近开窗做精确编辑距离
    
    窗口计算：
      window_half = len(hw_phonemes)  # 热词长度作为半窗大小
      scan_start = max(0, anchor - 2)
      scan_end   = min(len(input), anchor + len(hw_phonemes) + 3)
      ↑ 允许 ±3 个音素的偏移（容错插入/删除错误）
    
    Early Exit：
      每行 DP 计算后检查 row_min，
      如果 row_min > len(hw_phonemes) * (1.0 - adaptive_threshold) + 2，
      提前终止该候选的计算。
    
    结果过滤：
      score >= adaptive_threshold(hw_len) → 替换
      score >= adaptive_threshold - 0.2   → 候选提示
      score < adaptive_threshold - 0.2    → 丢弃
    """
```

### 2.7 集成方案

#### 2.7.1 TextPipeline 扩展

```python
class TextPipeline:
    def __init__(self, hotword_manager, phoneme_file="hotwords-phoneme.txt"):
        # 现有
        self._regex_rules = ...
        self._hotword_map = ...
        # 新增
        self._phoneme_corrector = PhonemeCorrector(
            threshold=self._config.get('hotword.phoneme_threshold', 0.7),
            enabled=self._config.get('hotword.phoneme_enabled', True),
        )
    
    def reload(self):
        """重新加载所有热词数据（由 HotwordManager 调用，或 Web UI 保存时触发）"""
        # 现有 reload（正则规则 + 文本替换）...
        
        # 新增：加载音素热词
        phoneme_path = self._hotword_manager.resolve_file_path(
            self._config.get('hotword.phoneme_file', 'hotwords-phoneme.txt')
        )
        if phoneme_path and self._phoneme_corrector.enabled:
            try:
                count = self._phoneme_corrector.update_from_file(phoneme_path)
                logger.info(f"音素热词加载完成：{count} 条")
            except Exception as e:
                logger.warning(f"音素热词加载失败，已降级：{e}")
                self._phoneme_corrector.enabled = False
    
    def process(self, text: str) -> ProcessResult:
        original = text
        # 第一层：音素纠错（毫秒级，模糊匹配）
        text = self._apply_phoneme(text)
        # 第二层：正则规则（微秒级，精确映射）
        text = self._apply_regex(text)
        # 第三层：文本替换（微秒级，精确替换）
        text = self._apply_hotwords(text)
        return ProcessResult(
            text=text,
            is_changed=(text != original),
            phoneme_matches=self._last_phoneme_matches,  # 新增：供日志/统计
        )
```

**Reload 触发机制**：
- `HotwordManager.reload()` 被调用时，TextPipeline.reload() 同步加载音素热词
- 触发源：Web UI 保存热词 / 配置变更 / 启动时首次加载
- 第二期的 watchdog 文件监控也会触发 reload

#### 2.7.2 PhonemeCorrector 接口

```python
class PhonemeCorrector:
    """音素纠错器 — 编排层"""
    
    def __init__(self, threshold: float = 0.7, enabled: bool = True):
        self.threshold = threshold           # 用户配置的基础阈值
        self.similar_threshold_delta = 0.2   # 候选提示阈值 = threshold - delta
        self.enabled = enabled               # 全局开关
        self._correction_count = 0           # 累计纠错次数
        self._index: Optional[PhonemeIndex] = None
        self._hotwords: Dict[str, List[Phoneme]] = {}  # {热词原文: 音素序列}
        self._lock = threading.Lock()
    
    def update_from_file(self, path: str) -> int:
        """从文件加载热词，线程安全
        
        采用"构建-替换"模式：
        1. 在当前线程构建全新的 PhonemeIndex 和 hotwords dict
        2. 构建完成后加锁，原子替换引用
        3. correct() 读取时无需加锁（Python GIL 保证引用赋值的原子性）
        
        Returns:
            成功加载的热词数量
        Raises:
            FileNotFoundError: 文件不存在
            ValueError: pypinyin 不可用
        """
    
    def correct(self, text: str) -> CorrectionResult:
        """执行纠错（线程安全读，可并发调用）
        
        流程：
        1. text_to_phonemes(text) → 输入音素序列
        2. self._index.get_candidates(input_phonemes) → 粗筛候选（通常 <10 个）
        3. 对每个候选：
           a. 计算自适应阈值 adaptive_threshold(self.threshold, len(hw_phonemes))
           b. 在锚点位置附近开窗
           c. fuzzy_substring_search(local_main, hw_phonemes) → 精确分数
           d. score >= adaptive → 加入 matches
           e. score >= adaptive - 0.2 → 加入 candidates
        4. 冲突去重（同一位置多个匹配，取最高分；同分取长词）
        5. 从后往前替换，避免索引偏移
        6. 更新 self._correction_count
        7. 返回 CorrectionResult
        """
    
    @property
    def hotword_count(self) -> int:
        """当前已加载的热词数量"""
        return len(self._hotwords)
    
    @property
    def correction_count(self) -> int:
        """累计纠错次数（用于 Web UI 统计）"""
        return self._correction_count
```

#### 2.7.3 并发安全（详细）

```
线程模型：
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Main Thread │     │  Reload     │     │  Correct    │
│  (STT回调)   │     │  Thread     │     │  (STT回调)  │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       │              ┌────▼────┐              │
       │              │ Build   │ new_index    │
       │              │ New     │──────────────│──┐
       │              │ Index   │              │  │
       │              └────┬────┘              │  │
       │                   │ Lock             │  │
       │              ┌────▼────┐              │  │
       │              │Atomic   │              │  │
       │              │Swap     │              │  │
       │              └────┬────┘              │  │
       │                   │                  │  │
       │                   ▼                  ▼  ▼
       │              self._index ──→ 读取（无锁）
       │              self._hotwords ──→ 读取（无锁）
```

**安全性分析**：
- Python GIL 保证引用赋值的原子性（`self._index = new_index` 是单条字节码）
- `correct()` 只读取 `self._index` 和 `self._hotwords`，不会修改
- 最坏情况：correct() 使用旧索引完成一次纠错，下次 correct() 使用新索引
- 不存在"读到半构建的索引"的风险（new_index 构建完成后才赋值）

### 2.8 配置扩展

```yaml
# config.yaml 新增
hotword:
  phoneme_enabled: true            # 全局开关（false 则完全跳过音素层）
  phoneme_threshold: 0.7           # 基础替换阈值（0.5 ~ 0.9，短词会自动调高）
  phoneme_file: hotwords-phoneme.txt  # 音素热词文件名
```

### 2.9 Web UI 扩展

设置页面新增：
- 音素热词开关（`phoneme_enabled`）
- 基础阈值滑块（0.5 ~ 0.9，步长 0.05，默认 0.7）
- 音素热词编辑区（复用现有热词编辑组件，提示：支持中文同音字/英文拼写错误）
- 已加载热词数量 + 累计纠错次数（只读统计）

### 2.10 性能评估

#### 2.10.1 典型场景

| 指标 | 预期值 | 说明 |
|------|--------|------|
| 索引构建（首次） | ~50ms | 100 条热词 |
| 索引更新（全量重建） | ~5ms | 热词量级下全量重建足够快 |
| 单次纠错（100 条热词，50 字文本） | ~2-5ms | 倒排索引 → <10 候选 → 窗口 DP |
| 单次纠错（500 条热词，50 字文本） | ~5-15ms | 候选增多但仍可控 |
| 内存占用（100 条热词） | ~2MB | 索引 + 音素数据 |
| 首次加载延迟 | ~200ms | pypinyin 首次 import（后续调用无延迟） |
| pypinyin 依赖 | 0 MB 新增 | funasr 依赖链已包含 |

#### 2.10.2 极端场景分析

| 场景 | 音素序列长度 | 候选数 | 预期耗时 | 缓解措施 |
|------|-------------|--------|---------|---------|
| 10 字短句 | ~30 | <5 | <1ms | — |
| 50 字正常句 | ~150 | <10 | 2-5ms | — |
| 200 字长段落 | ~600 | <20 | 10-30ms | 倒排索引 + early exit |
| 500 字超长段 | ~1500 | <30 | 30-100ms | DP 行级剪枝：row_min > len × (1-t) + 2 时提前终止 |
| 1000 字极端 | ~3000 | <50 | 100-300ms | ⚠️ 同上，可能偶发。建议：超过 200 字时仅对最近 200 字做音素纠错 |

**Worst-case 复杂度**：
- 索引粗筛：O(m)（遍历输入音素序列，m=输入长度）
- 精确 DP：O(c × w²)（c=候选数，w=窗口大小 ≈ 热词长度+6）
- 综合典型：O(m + c × w²)，其中 c<20, w<20，非常可控

**长文本保护机制**：
```python
MAX_PHONEME_LENGTH = 600  # 对应约 200 个汉字
if len(input_phonemes) > MAX_PHONEME_LENGTH:
    # 仅对最后 MAX_PHONEME_LENGTH 个音素做纠错（最近说的话最可能需要纠错）
    input_phonemes = input_phonemes[-MAX_PHONEME_LENGTH:]
    offset = len(original_phonemes) - MAX_PHONEME_LENGTH
```

### 2.11 边界情况

| 情况 | 处理策略 |
|------|---------|
| 短词误匹配 | 自适应阈值：2 字热词阈值自动提升至 ~0.85；最小词长 2 字符 |
| 重叠匹配 | 同一位置多个匹配取最高分；同分取长词（更精确） |
| pypinyin 不可用 | 启动时检测，`enabled=False`，跳过音素层，日志 warning |
| 热词文件不存在 | `update_from_file` 抛 FileNotFoundError，Pipeline 捕获后降级 |
| 热词文件为空 | 正常处理，0 个热词，correct() 直接返回原文 |
| 空文本/纯标点 | `text_to_phonemes` 返回空列表，correct() 直接返回 |
| 多音字 | pypinyin 按词组消歧（`errors='ignore'`），准确率 >95% |
| 级联替换 | 有序执行：音素层输出 → 正则层输入 → 文本层输入 |
| 相同分数不同词 | 长词优先（信息熵更高，更可能是正确匹配） |
| 纯英文输入 | 英文 Phoneme 独立匹配，不影响中文热词检索 |
| 纯中文输入 | 中文 Phoneme 独立匹配，不影响英文热词检索 |
| 中英文混合 | 按 lang 隔离，各自在语言内匹配，DP 自动跳过跨语言对 |
| 英文大小写 | 存储和匹配统一小写，替换时使用热词原文（保留原始大小写） |
| Unicode/Emoji | 不产生音素，跳过，不参与匹配 |

### 2.12 日志设计

```python
import logging
logger = logging.getLogger('voice_input_tool.phoneme')

# 级别规范：
# DEBUG — 每次纠错的详细信息（候选列表、分数、替换决策）
# INFO  — 热词加载/更新、纠错统计
# WARNING — 降级（pypinyin 不可用、文件缺失）
# ERROR — 意外异常

# 示例日志：
# [INFO]  音素热词加载完成：42 条（阈值 0.70）
# [DEBUG] correct("撒贝你主持节目") → 候选 3 个：撒贝宁(0.83), ... → 替换 1 处
# [DEBUG]   替换：[0:3] "撒贝你" → "撒贝宁" (score=0.83)
# [WARNING] pypinyin 未安装，音素纠错已禁用
```

### 2.13 依赖

| 包 | 来源 | 大小 | 说明 |
|---|---|---|---|
| pypinyin | funasr 间接依赖 | ~8MB | 中文拼音转换，funasr 安装时已有 |
| threading | stdlib | 0 | reload 构建时的互斥 |
| dataclasses | stdlib | 0 | Phoneme / MatchResult / CorrectionResult |
| re | stdlib | 0 | 文本规范化（驼峰拆分） |
| logging | stdlib | 0 | 日志 |
| typing | stdlib | 0 | 类型注解 |

**零新增依赖**。pypinyin 在 funasr 的依赖链中已包含。

## 3. 与 CapsWriter 的差异

| 方面 | CapsWriter | 我们的方案 |
|------|-----------|-----------|
| Numba JIT | 依赖 | **不引入**（100~500 条热词不需要） |
| g2p-en | 使用 | **不使用**（英文直接字符级 LCS） |
| 纠错历史（LLM） | 使用（hot-rectify.txt） | **不纳入本次**（第二期） |
| watchdog 文件监控 | 使用 | **复用现有 TextPipeline reload** |
| 阈值配置 | 硬编码 0.7 | **可配置 + 自适应**（短词自动调高） |
| 中文音译→英文映射 | hot-rule.txt | **复用现有 hot-rules.txt** |
| 相似音素查找 | Set 列表遍历 O(n) | **预构建字典 O(1)** |
| 长文本保护 | 无 | **音素截断 600（~200 字）** |
| 并发安全 | threading.Lock | **构建-替换模式（无锁读）** |
| 测试覆盖 | \_\_main\_\_ 手动测试 | **单元测试 + 集成测试 + 基准测试** |

## 4. 实施计划

### Phase 1：核心数据结构与音素转换
- [ ] `phoneme_types.py` — Phoneme / MatchResult / CorrectionResult dataclass
- [ ] `phoneme_types.py` — `text_to_phonemes()` + `normalize_text()` + `is_similar_phoneme()`
- [ ] `__init__.py` — 公开接口导出 + pypinyin 可用性检测
- [ ] **Layer 1 单元测试**：`test_phoneme_types.py`（详见 §6.2.1）

### Phase 2：相似度计算
- [ ] `phoneme_similarity.py` — `phoneme_cost()` + `lcs_length()` + `fuzzy_substring_search()`
- [ ] `phoneme_similarity.py` — SIMILAR_PHONEMES 预构建字典 + adaptive_threshold()
- [ ] **Layer 1 单元测试**：`test_phoneme_similarity.py`（详见 §6.2.2）

### Phase 3：倒排索引 + 纠错器
- [ ] `phoneme_index.py` — PhonemeIndex（add + get_candidates + 相似音素互索引）
- [ ] `phoneme_corrector.py` — PhonemeCorrector（update_from_file + correct + 冲突去重）
- [ ] **Layer 1 单元测试**：`test_phoneme_index.py` + `test_phoneme_corrector.py`（详见 §6.2.3~4）
- [ ] **Layer 2 集成测试**：`test_phoneme_integration.py`（详见 §6.3）

### Phase 4：集成 + 测试 + 发布
- [ ] TextPipeline 插入音素层（process 中增加 _apply_phoneme）
- [ ] Config 扩展（phoneme_enabled / phoneme_threshold / phoneme_file）
- [ ] Web API 端点扩展（音素热词编辑 + 统计）
- [ ] **Layer 3 端到端测试**：`test_phoneme_e2e.py`（详见 §6.4）
- [ ] **Layer 4 回归测试**：确保现有功能不受影响（详见 §6.5）
- [ ] 性能基准测试（可选，详见 §6.7）
- [ ] README 更新
- [ ] 提交并推送

## 5. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| pypinyin 多音字错误 | 中 | 低 | `errors='ignore'` + 词组消歧，准确率 >95% |
| 误替换正常词汇 | 低 | 中 | 自适应阈值 + 最小词长 2 + 全局开关 |
| 英文整词 LCS 精度不足 | 低 | 低 | 英文整词匹配，同音拼写错误几乎完美匹配 |
| Windows 兼容性 | 低 | 低 | 纯 Python 无平台相关代码 |
| pypinyin 未安装 | 低 | 中 | 启动检测 + 优雅降级 + 日志 warning |
| 长文本性能退化 | 低 | 中 | 倒排索引 + DP early exit + 600 音素截断 |
| reload 与 correct 竞态 | 极低 | 中 | 构建-替换模式 + GIL 保证引用原子性 |
| 级联替换副作用 | 极低 | 低 | 各层职责不重叠，音素层只改同音字 |

## 6. 测试方案（四层测试金字塔）

遵循 `TESTING_STANDARDS.md` 的四层测试体系，确保音素匹配功能的质量。

### 6.1 测试策略概述

| 层级 | 覆盖范围 | 依赖 | 测试文件 | 目标 |
|------|---------|------|---------|------|
| **Layer 1: 单元测试** | 每个函数/类独立逻辑 | 真实 pypinyin（不可 mock） | `test_phoneme_*.py` | 逻辑正确性、边界条件 |
| **Layer 2: 集成测试** | 模块间交互（Index+Corrector+Pipeline） | 真实模块组合 | `test_phoneme_integration.py` | 接口契约、数据流 |
| **Layer 3: 端到端测试** | STT→Pipeline→注入完整链路 | 真实 STT引擎 | `test_phoneme_e2e.py` | 用户可感知功能 |
| **Layer 4: 回归测试** | 修改后现有功能不受影响 | 全量测试套件 | `tests/test_regression.py` | 功能稳定性 |

### 6.2 Layer 1: 单元测试

**测试文件命名规范**：
```
tests/
├── test_phoneme_types.py      # Phoneme dataclass + text_to_phonemes + normalize_text
├── test_phoneme_similarity.py # phoneme_cost + lcs_length + fuzzy_substring_search + adaptive_threshold
├── test_phoneme_index.py      # PhonemeIndex (add + get_candidates + 相似音素互索引)
├── test_phoneme_corrector.py  # PhonemeCorrector (update_from_file + correct + 冲突去重 + 并发安全)
└── test_phoneme_performance.py # 性能基准测试（Phase 4 必选项，CI perf job）
```

#### 6.2.1 test_phoneme_types.py

| 测试类 | 测试用例 | 验证点 |
|--------|---------|--------|
| **TestPhonemeDataclass** | `test_phoneme_frozen` | frozen=True，不可修改 |
| | `test_phoneme_hashable` | 可作为 dict key / set 元素 |
| | `test_phoneme_defaults` | is_word_start=False, is_word_end=False |
| **TestTextToPhonemesZh** | `test_single_char` | "撒" → [声母s, 韵母a, 声调1] |
| | `test_multi_char` | "撒贝宁" → 6-9 个 Phoneme |
| | `test_zero_initial` | "啊" → 无声母，只有韵母+声调 |
| | `test_pypinyin_error` | 未知字 → 降级为单字 Phoneme |
| | `test_punctuation_skip` | 标点不产生 Phoneme |
| | `test_emoji_skip` | Emoji 不产生 Phoneme |
| | `test_polyphone_disambiguation` | "银行" → yin2 hang2（非 yin4）, "行走" → xing2 zou3（非 hang2） |
| | `test_polyphone_fallback` | 孤立多音字 "乐" → 取 pypinyin 默认读音 |
| | `test_space_between_zh` | 中文间空格不影响音素序列 |
| **TestTextToPhonemesEn** | `test_single_word` | "Claude" → [Phoneme(claude,en,T,T,0,6)] |
| | `test_camel_split` | "PyTorch" → normalize → "py torch" → 2 个 Phoneme |
| | `test_hyphen_split` | "Hugging-Face" → 2 个 Phoneme |
| | `test_case_insensitive` | "CLAUDE" → value='claude' (小写) |
| | `test_mixed_case` | "iPhone" → "i phone" → 2 个 Phoneme |
| | `test_underscore_split` | "hello_world" → 2 个 Phoneme |
| | `test_empty_string` | "" → 空列表 [] |
| **TestTextToPhonemesMixed** | `test_zh_en_mixed` | "撒贝宁主持Claude" → 中文 + 英文 Phoneme 分离 |
| | `test_number` | "iPhone15" → "iPhone" + "15"（数字 Phoneme） |
| | `test_zh_en_digit_punctuation` | "Claude说Python3.11很好，真的！" → 完整多类型分离 |
| **TestNormalizeText** | `test_camel_basic` | "PyTorch" → "py torch" |
| | `test_camel_upper_sequence` | 连续大写 "HTTPS" 不拆分 → "https" |
| | `test_camel_digit` | "iPhone15Pro" → "iphone 15 pro" |
| | `test_hyphen_to_space` | "Hugging-Face" → "hugging face" |
| | `test_multiple_spaces` | "Py  Torch" → "py torch" (合并空白) |
| | `test_zh_unchanged` | 中文文本 normalize 后不变 |

#### 6.2.2 test_phoneme_similarity.py

| 测试类 | 测试用例 | 验证点 |
|--------|---------|--------|
| **TestSimilarPhonemesDict** | `test_build_dict` | 预构建字典包含所有 16 组音素对 |
| | `test_symmetric_lookup` | is_similar('l','n') == is_similar('n','l') |
| | `test_non_similar` | is_similar('a','b') == False |
| | `test_all_pairs_exhaustive` | 遍历 SIMILAR_PHONEME_SETS 所有 16 组，双向验证 |
| | `test_dict_no_false_positive` | 随机抽样 50 个非相似音素对，确认返回 False |
| **TestPhonemeCost** | `test_same_lang_same_value` | cost = 0.0 |
| | `test_different_lang` | zh vs en → cost = 1.0 |
| | `test_num_vs_zh` | num vs zh → cost = 1.0（不同 lang） |
| | `test_similar_initials` | 'z' vs 'zh' → cost = 0.5 |
| | `test_similar_finals` | 'an' vs 'ang' → cost = 0.5 |
| | `test_similar_rl` | 'r' vs 'l' → cost = 0.5 |
| | `test_tone_difference` | 声调 1 vs 3 → cost = 0.5 |
| | `test_en_lcs_match` | 'claude' vs 'claude' → cost = 0.0 |
| | `test_en_lcs_partial` | 'claude' vs 'cloud' → LCS=4, cost ≈ 0.33 |
| | `test_en_lcs_zero` | 'abc' vs 'xyz' → LCS=0, cost = 1.0 |
| | `test_en_empty_string` | '' vs 'abc' → cost = 1.0（max_len 防零除） |
| **TestLcsLength** | `test_identical` | LCS("abc", "abc") = 3 |
| | `test_subset` | LCS("abc", "ac") = 2 |
| | `test_disjoint` | LCS("abc", "xyz") = 0 |
| | `test_case_insensitive` | LCS("AbC", "abc") = 3 |
| | `test_empty_strings` | LCS("", "") = 0, LCS("a", "") = 0 |
| | `test_long_strings` | LCS("a"*100, "a"*100) = 100（无栈溢出） |
| **TestFuzzySubstringSearch** | `test_exact_match` | "撒贝宁" vs "撒贝宁" → dist=0, score=1.0 |
| | `test_near_match` | "撒贝你" vs "撒贝宁" → dist≈1, score≈0.85 |
| | `test_no_match` | "Hello" vs "撒贝宁" → dist=len, score≈0 |
| | `test_start_end_boundary` | 匹配必须在字边界（is_word_start/end） |
| | `test_char_indices` | start_idx/end_idx 对应原文本位置 |
| | `test_score_non_negative` | score = max(0.0, 1.0 - dist/len) 永远 ≥ 0 |
| | `test_empty_sub_seq` | 空 sub_seq → score=0.0（len=0 防零除） |
| **TestAdaptiveThreshold** | `test_2_char` | len=2 → threshold ≈ 0.85 (base=0.7) |
| | `test_3_char` | len=3 → threshold ≈ 0.775 |
| | `test_4_char` | len=4 → threshold = 0.70 |
| | `test_5_plus_char` | len≥5 → threshold = 0.70 |
| | `test_base_threshold_varies` | base=0.6 时，2字 → 0.80 |
| | `test_threshold_clamped` | 任何输入阈值不超过 1.0 |

#### 6.2.3 test_phoneme_index.py

| 测试类 | 测试用例 | 验证点 |
|--------|---------|--------|
| **TestPhonemeIndexBuild** | `test_add_single` | 添加 1 条热词，索引正确 |
| | `test_add_multiple` | 添加 10 条热词，索引完整 |
| | `test_similar_phoneme_indexed` | "乐清" 同时索引 'l' 和 'n' 桶 |
| | `test_en_word_indexed` | "Claude" 索引 'claude' 桶 |
| | `test_duplicate_add` | 重复添加同一热词，索引不重复 |
| | `test_clear_index` | 清空索引后候选为空 |
| **TestPhonemeIndexRetrieve** | `test_get_candidates_exact` | 输入含 'l'，返回 "乐清" 候选 |
| | `test_get_candidates_similar` | 输入含 'n'，也返回 "乐清" 候选（相似音素互索引） |
| | `test_get_candidates_empty_input` | 空 Phoneme 序列 → 无候选 |
| | `test_get_candidates_no_match` | 输入无匹配音素 → 无候选 |
| | `test_candidates_with_anchor` | 返回候选 + 锚点位置 |
| | `test_candidates_deduplicated` | 同一热词被多个 key 命中时，候选去重但锚点合并 |

#### 6.2.4 test_phoneme_corrector.py

| 测试类 | 测试用例 | 验证点 |
|--------|---------|--------|
| **TestUpdateFromFile** | `test_load_valid_file` | 加载热词文件，hotword_count 正确 |
| | `test_load_empty_file` | 空文件 → count=0 |
| | `test_load_nonexistent_file` | FileNotFoundError |
| | `test_load_malformed_file` | 含非法行（空行/纯空白）→ 跳过，不崩溃 |
| | `test_load_file_with_comments` | # 注释行和空行被正确跳过 |
| | `test_reload_replaces_index` | 第二次 load 替换第一次索引 |
| **TestCorrectBasic** | `test_correct_zh_homophone` | "撒贝你" → "撒贝宁" (score>threshold) |
| | `test_correct_en_spell_error` | "claude" → "Claude" |
| | `test_correct_no_match` | 无匹配时返回原文 |
| | `test_correct_below_threshold` | score<threshold 不替换，但记录候选 |
| | `test_correct_preserves_casing` | "CLAUDE is great" → "Claude is great"（保留热词原文大小写） |
| **TestCorrectAdvanced** | `test_multiple_matches` | 多个匹配，从后往前替换 |
| | `test_conflict_resolution` | 同一位置多匹配，取最高分 |
| | `test_same_score_longer_word` | 同分取长词 |
| | `test_short_word_high_threshold` | 2字热词阈值自动升高 |
| | `test_candidate_logging` | score 在 [threshold-0.2, threshold) 的记录为候选 |
| **TestCorrectEdge** | `test_empty_text` | 空文本 → 直接返回 |
| | `test_pure_punctuation` | 纯标点 → 直接返回 |
| | `test_long_text_truncate` | >600 音素 → 截断处理 |
| | `test_truncate_offset_preserved` | 截断后的 char_start/char_end 偏移量正确映射回原文本 |
| | `test_unicode_text` | 含各种 Unicode 字符的文本不崩溃 |

#### 6.2.5 并发安全测试（详细）

**放在 `test_phoneme_corrector.py` 的 TestConcurrentSafety 类中。**

| 测试用例 | 验证点 | 实现方式 |
|---------|--------|---------|
| `test_concurrent_reload_and_correct` | 多线程同时 reload + correct 不崩溃 | 启动 4 个 correct 线程 + 1 个 reload 线程，运行 100ms，无异常 |
| `test_reload_atomic_swap` | reload 期间 correct 始终使用完整的索引 | 验证每次 correct 结果一致（不出现半索引状态） |
| `test_concurrent_correct_only` | 多个 correct 并发调用结果一致 | 10 个线程同时 correct 同一文本，结果全部相同 |
| `test_reload_performance` | reload 期间 correct 延迟不显著增加 | reload 100 次期间测量 correct 耗时，P99 < 50ms |

### 6.3 Layer 2: 集成测试

**测试文件**：`tests/test_phoneme_integration.py`

| 测试类 | 测试用例 | 验证点 |
|--------|---------|--------|
| **TestIndexCorrectorIntegration** | `test_index_to_corrector_dataflow` | Index 返回候选 → Corrector 接收并计算分数 |
| | `test_anchor_window_correctness` | 锚点位置 + 窗口大小符合预期 |
| | `test_full_index_to_correction_flow` | 完整数据流：输入文本 → 索引检索 → 锚点定位 → 窗口 DP → 替换结果 |
| **TestCorrectorPipelineIntegration** | `test_corrector_output_to_regex` | 音素纠错后 → 正则层输入 |
| | `test_corrector_output_to_hotword` | 音素纠错后 → 文本替换层输入 |
| | `test_cascade_order` | 音素→正则→文本 三层顺序执行 |
| | `test_cascade_phoneme_result_reachable` | 音素层替换结果能被正则层/文本层继续处理 |
| **TestPhonemeWithExistingPipeline** | `test_phoneme_enabled_true` | enabled=True 时音素层生效 |
| | `test_phoneme_enabled_false` | enabled=False 时跳过音素层 |
| | `test_phoneme_degradation` | pypinyin 不可用时降级，不影响其他层 |
| | `test_phoneme_disabled_pipeline_passthrough` | disabled 时 Pipeline 输出 = 仅正则+文本层输出 |

### 6.4 Layer 3: 端到端功能测试

**测试文件**：`tests/test_phoneme_e2e.py`

| 测试类 | 测试用例 | 验证点 |
|--------|---------|--------|
| **TestE2EZhHomophone** | `test_real_stt_output_1` | 模拟 STT 输出 "撒贝你主持节目" → 纠错 "撒贝宁" |
| | `test_real_stt_output_2` | 模拟 STT 输出 "东方菜富" → 纠错 "东方财富" |
| | `test_real_stt_output_3` | 模拟 STT 输出 "科大迅飞" → 纠错 "科大讯飞" |
| | `test_no_false_positive_zh` | "我们今天要去银行" → 不误替换为其他词 |
| **TestE2EEnSpellError** | `test_real_stt_output_4` | 模拟 STT 输出 "claude is great" → 纠错 "Claude" |
| | `test_real_stt_output_5` | 模拟 STT 输出 "pytorch framework" → 纠错 "PyTorch" |
| | `test_no_false_positive_en` | "cloud storage" → 不误替换为 "Claude" |
| **TestE2EMixed** | `test_real_stt_output_6` | 模拟 STT 输出 "撒贝宁说claude很厉害" → 双语纠错 |
| **TestE2EWithHotRules** | `test_phoneme_plus_rule` | 音素纠错 "撒贝你" + 规则映射 "酷打=CUDA" 组合 |
| | `test_phoneme_output_feeds_rule` | 音素层输出含中文音译词，正则层可继续映射 |
| **TestE2EPerformance** | `test_50_chars_100_hotwords` | 50 字 + 100 热词，耗时在合理范围（见 §6.7 基准） |
| | `test_200_chars_500_hotwords` | 200 字 + 500 热词，耗时在合理范围 |
| **TestE2EDynamicReload** | `test_reload_hotwords_mid_session` | 运行中 reload 热词文件，新热词立即生效 |
| | `test_disable_mid_session` | 运行中切换 enabled=False，音素层立即停止 |
| **TestE2EWebUI** | `test_web_api_get_config` | GET /api/hotword/phoneme 返回当前配置 |
| | `test_web_api_enable_disable` | POST 切换 enabled 状态 |
| | `test_web_api_threshold_adjust` | POST 调整阈值并生效 |
| | `test_web_api_hotword_crud` | POST 增删改音素热词 |
| | `test_web_api_stats` | GET 统计纠错次数 |
| | `test_web_api_backward_compat` | 现有 /api/hotword 端点不受新增端点影响 |
| | `test_web_api_error_handling` | 无效参数/不存在的文件返回 400/404 |

### 6.5 Layer 4: 回归测试

**测试文件**：`tests/test_regression.py`（复用现有回归测试框架）

| 测试类 | 测试用例 | 验证点 |
|--------|---------|--------|
| **TestPhonemeRegression** | `test_existing_pipeline_unaffected` | 音素层 disabled 时，现有 Pipeline 功能不变 |
| | `test_existing_hotword_unaffected` | 现有 hotwords.txt 功能不受音素层影响 |
| | `test_existing_rules_unaffected` | 现有 hot-rules.txt 功能不受音素层影响 |
| | `test_config_default_backward_compat` | 新 config 字段有默认值，旧配置文件兼容 |
| | `test_full_existing_test_suite_passes` | 运行现有 tests/ 全量测试，无新增失败 |
| **TestRegressionAfterModify** | `test_modify_threshold_no_side_effect` | 修改阈值后，其他功能正常 |
| | `test_modify_hotword_file_no_side_effect` | 修改音素热词文件后，其他热词层正常 |
| | `test_reload_preserves_state` | reload 后，correction_count 等状态正确 |
| **TestWebAPIRegression** | `test_existing_web_api_endpoints` | 所有现有 Web API 端点响应格式不变 |
| | `test_web_api_new_endpoints_no_conflict` | 新增端点不影响现有端点 |

### 6.6 Mock 策略

| 模块 | Mock 策略 | 原因 |
|------|---------|------|
| `pypinyin` | **不 mock**。测试用真实 pypinyin 转换，确保音素转换结果反映真实行为。多音字场景用 pypinyin 的词组消歧（传入完整词组） | pypinyin 是核心依赖，mock 后测试失去意义 |
| `PhonemeIndex` | 单元测试中 Mock 返回固定候选（隔离 Corrector 测试 Index 逻辑） | 正确的分层隔离 |
| `TextPipeline` | 集成测试中 Mock `_apply_regex` 和 `_apply_hotwords`（隔离音素层） | 正确的分层隔离 |
| `STT Engine` | E2E 中 Mock `Engine.transcribe` 返回固定文本 | 不依赖真实模型 |
| `Web Server` | E2E 中用 `flask.testing.TestClient` | 无需启动真实 HTTP 服务 |
| `time.sleep` | 并发测试中可适当 sleep 增加竞态暴露概率 | 提高并发测试有效性 |

### 6.7 性能基准测试

**测试文件**：`tests/test_phoneme_performance.py`（Phase 4 必选项，CI 中作为独立 perf job）

> 以下阈值为**设计预期值**，Phase 4 实施阶段基准测试后将校准为实测值。
> 校准规则：取 P95 实测值的 2 倍作为正式阈值。

| 测试场景 | 输入长度 | 热词数 | 预期耗时（设计值） | 内存峰值 |
|---------|---------|--------|-------------------|--------|
| Short-10 | 10 字 | 100 | < 5ms | < 10MB |
| Normal-50 | 50 字 | 100 | < 20ms | < 15MB |
| Long-200 | 200 字 | 100 | < 100ms | < 20MB |
| Heavy-500 | 500 字 | 500 | < 500ms | < 50MB |
| Stress-1000 | 1000 字 | 500 | < 1000ms | < 80MB |

**每个场景执行 50 次取 P95。**

**回归触发条件**（量化标准）：
- 单次纠错 P95 耗时超过校准阈值的 1.5 倍 → 触发性能调查
- 内存峰值超过校准阈值的 1.2 倍 → 触发内存泄漏调查
- 新增 100 条热词后 P95 耗时增长 > 30% → 触发可扩展性调查

**CI 集成**：
```yaml
# .github/workflows/test.yml 新增 job
phoneme-perf:
  runs-on: ubuntu-latest
  steps:
    - run: pytest tests/test_phoneme_performance.py -v --benchmark-only
  # 失败时发出 warning（不阻塞主分支合并）
```

### 6.8 测试数据 Fixture

**文件位置**：`tests/fixtures/phoneme/`

```
tests/fixtures/phoneme/
├── hotwords_basic.txt        # 基础热词（10 条，中英文混合）
├── hotwords_empty.txt        # 空文件
├── hotwords_comments.txt     # 含注释和空行的热词文件
├── hotwords_large.txt        # 大量热词（500 条，用于性能测试）
└── expected_corrections.json # 预期纠错结果（输入→输出映射）
```

**`expected_corrections.json` 示例**：
```json
{
  "homophone_zh": [
    {"input": "撒贝你主持节目", "output": "撒贝宁主持节目", "hotword": "撒贝宁"},
    {"input": "东方菜富发布财报", "output": "东方财富发布财报", "hotword": "东方财富"}
  ],
  "spell_error_en": [
    {"input": "claude is great", "output": "Claude is great", "hotword": "Claude"}
  ],
  "no_replace": [
    {"input": "我们今天要去银行", "output": "我们今天要去银行", "reason": "无匹配热词"},
    {"input": "cloud storage", "output": "cloud storage", "reason": "score < threshold"}
  ]
}
```

### 6.9 测试执行计划

**开发阶段（Phase 1-3）**：
- 每完成一个模块，立即运行对应单元测试
- 单元测试通过后才能进入下一 Phase

**集成阶段（Phase 4）**：
- 运行 Layer 1 全量单元测试
- 运行 Layer 2 集成测试
- 运行 Layer 3 端到端测试
- 运行 Layer 4 回归测试（确保现有功能不受影响）
- 运行性能基准测试，校准阈值

**提交前强制检查**：
```bash
# 全量测试（包括回归）
python tests/run_tests.py

# 或分层执行
pytest tests/test_phoneme_*.py -v            # Layer 1 + 性能
pytest tests/test_phoneme_integration.py -v  # Layer 2
pytest tests/test_phoneme_e2e.py -v          # Layer 3
pytest tests/test_regression.py -v          # Layer 4
```

**覆盖率目标**（pytest-cov）：
- 行覆盖率 > 80%（核心模块 phoneme_similarity.py > 90%）
- 分支覆盖率 > 70%
- 集成测试覆盖所有模块间交互路径
- 端到端测试覆盖所有用户可感知场景
- 回归测试覆盖所有现有功能点
