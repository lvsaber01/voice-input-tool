# mlx-whisper 引擎集成方案设计

> **版本**: v1.2 (第2轮评审修订)
> **日期**: 2026-04-25
> **设计者**: Saber
> **状态**: 待评审
> **评审历程**: v1.0=78 → v1.1=82 → v1.2=待评

---

## 一、背景与目标

### 1.1 现状

当前 voice-input-tool 支持 3 种 STT 引擎：

| 引擎 | 用途 | 平台 | 实时模式 |
|------|------|------|---------|
| faster-whisper | 默认引擎，全平台 | Win/Mac | ❌ 仅分段 |
| FunASR 分段 | 分段转写 | Win/Mac | ❌ |
| FunASR 流式 | 实时转写 | Win/Mac | ✅ streaming |

**问题**：Mac 上没有原生优化的实时流式引擎。FunASR 的 torch 依赖在 Mac 上体积大、编译困难。

### 1.2 目标

引入 **mlx-whisper** 作为 Mac 专用的实时流式引擎：

| 引擎 | 平台 | 模式 | 打包 |
|------|------|------|------|
| faster-whisper | 全平台 | 分段（兜底） | ✅ 内置 |
| FunASR | Windows | 分段 + 流式 | 运行时安装 |
| **mlx-whisper** | **macOS** | **分段 + 流式** | **直接打包进 .app** |

**用户价值**：
- Mac 用户无需安装额外依赖，开箱即用实时转写
- M 系列芯片上 mlx-whisper 比 faster-whisper 快 2-3 倍
- 模型使用 MLX 格式（`mlx-community/whisper-*`），需额外下载 ~1.5GB（首次使用）

---

## 二、mlx-whisper 技术分析

### 2.1 简介

mlx-whisper 是 Apple MLX 框架优化的 OpenAI Whisper 实现，专为 Apple Silicon 设计。

- GitHub: https://github.com/ml-explore/mlx-examples/tree/main/whisper
- 依赖: `mlx`, `mlx-core`, `huggingface_hub`（无 torch）
- 模型: 基于 OpenAI Whisper 权重转换的 MLX 格式（需单独下载 ~1.5GB，与 faster-whisper 不共享缓存）

### 2.2 API 对比

```python
# faster-whisper (现有)
from faster_whisper import WhisperModel
model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
segments, info = model.transcribe(audio, language="zh")
text = " ".join(seg.text for seg in segments)

# mlx-whisper (新增) — ⚠️ 注意：模型格式和 API 与 faster-whisper 不同
import mlx_whisper
result = mlx_whisper.transcribe(
    audio,                              # numpy array 或文件路径
    path_or_hf_repo="mlx-community/whisper-large-v3-turbo",  # MLX 格式模型
    language="zh",
    verbose=False
)
# 返回 dict: {"text": "...", "segments": [{"text": "...", ...}], ...}
text = result.get("text", "")
# 或拼接 segments:
text = "".join([seg["text"] for seg in result.get("segments", [])])
```

**关键差异**：
| 项目 | faster-whisper | mlx-whisper |
|------|---------------|-------------|
| 模型格式 | CTranslate2 | MLX |
| 模型仓库 | `guillaumekln/faster-whisper-*` | `mlx-community/whisper-*` |
| 模型缓存 | **不共享** | **不共享** |
| 额外下载 | 无（使用已有） | ~1.5GB（首次） |
| 返回结构 | generator of segments | dict with text + segments |

### 2.3 实时流式可行性

mlx-whisper 本身**不原生支持流式 API**（和 faster-whisper 一样是全量转写）。

**方案**：复用现有 `StreamingTranscriber` + `VADSegmentTranscriber` 架构，用 mlx-whisper 替换底层转写引擎。

实时模式有两种实现路径：

| 路径 | 方式 | 延迟 | 质量 | 复杂度 |
|------|------|------|------|--------|
| **A: VAD 分段** | 复用 `VADSegmentTranscriber`，用 mlx-whisper 分段转写 | 中等（VAD 切分后每段 ~1-3s） | 高 | 低 |
| B: 伪流式 chunk | 模拟 FunASR 的 chunk 方式，每 N ms 切片转写 | 低 | 中等 | 高 |

**推荐路径 A**：VAD 分段模式。原因：
1. mlx-whisper 全量转写质量更高（有完整上下文）
2. 复用现有 `VADSegmentTranscriber`，**无需新建流式引擎类**
3. macOS 上 mlx-whisper 转写速度极快（M 芯片），VAD 分段间的延迟可接受
4. 避免实现 chunk 级别的缓存管理（FunASR streaming 的复杂部分）

**延迟预期**：
- VAD 检测语音结束：~0.8s（segment_pause_threshold）
- mlx-whisper 转写 3s 音频：~0.5s（M1 Pro 实测预估）
- **总延迟：~1.3s**（对比 FunASR 流式 ~0.3-0.6s）
- 延迟比 FunASR 流式高 2-3 倍，但 MLX 转写质量更高
- 用户可在配置中切换引擎，按需选择

### 2.4 依赖体积

| 包 | 预估大小 | 说明 |
|---|----------|------|
| mlx | ~15MB | Apple MLX 核心库 |
| mlx-core | ~10MB | MLX 后端 |
| mlx-whisper | ~2MB | Whisper 绑定 |
| **合计增量** | **~27MB** | 远小于 FunASR 的 350MB |

---

## 三、架构设计

### 3.1 引擎选择逻辑（更新）

```python
# core/engine.py 修改后的引擎选择逻辑

stt_engine_type = getattr(config.stt, 'engine', 'auto')  # 默认 auto

if stt_engine_type == 'auto':
    # 自动选择最优引擎
    import platform
    if platform.system() == 'Darwin':
        stt_engine_type = 'mlx_whisper'   # Mac 优先 mlx-whisper
    else:
        stt_engine_type = 'faster_whisper'  # Windows 默认 faster-whisper

if stt_engine_type == 'mlx_whisper':
    if platform.system() != 'Darwin':
        logger.warning("mlx_whisper 仅支持 macOS，回退到 faster_whisper")
        stt_engine_type = 'faster_whisper'
```

### 3.2 新增文件

```
core/
└── stt_mlx_whisper.py  # 新增：mlx-whisper 分段引擎（仅此一个文件）
```

> 无需新建 `stt_mlx_whisper_streaming.py`，流式模式直接复用现有 `VADSegmentTranscriber` + `MlxWhisperEngine`。

### 3.3 MlxWhisperEngine（分段模式）

实现与 `STTEngine`（faster-whisper）相同的接口，供 `VADSegmentTranscriber` 调用：

```python
# core/stt_mlx_whisper.py

class MlxWhisperEngine:
    """mlx-whisper 分段转写引擎
    
    接口与 STTEngine 兼容，供 VADSegmentTranscriber 调用。
    macOS 专用，利用 Apple Silicon MLX 加速。
    """
    
    def __init__(self, config):
        self.config = config
        self.model = None  # mlx-whisper 不需要持有模型对象
        self._model_name = None
        self._lock = threading.Lock()
    
    def load_model(self) -> tuple[bool, str]:
        """加载/验证模型（mlx-whisper 首次调用时自动下载）"""
        try:
            import mlx_whisper
            # 验证模型可用：用 1s 静音音频测试调用
            test_audio = np.zeros(16000, dtype=np.float32)
            result = mlx_whisper.transcribe(
                test_audio,
                path_or_hf_repo=self._get_model_repo(),
                language=self.config.language or "en",
                verbose=False
            )
            return True, ''
        except ImportError:
            return False, 'mlx_whisper 未安装，请运行: pip install mlx-whisper'
        except Exception as e:
            return False, str(e)
    
    def transcribe_sync(self, audio: np.ndarray) -> str:
        """同步转写（VADSegmentTranscriber 调用此接口）"""
        import mlx_whisper
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self._get_model_repo(),
            language=self.config.language,
            verbose=False
        )
        text = result.get('text', '') or ''
        # 繁简转换（需在 Step 1 验证 mlx 输出倾向后决定是否保留）
        if text:
            try:
                from opencc import OpenCC
                cc = OpenCC('t2s')
                text = cc.convert(text)
            except ImportError:
                pass
        return text.strip()
    
    def _get_model_repo(self) -> str:
        """获取 MLX 格式模型仓库名"""
        mlx_map = {
            'tiny': 'mlx-community/whisper-tiny',
            'base': 'mlx-community/whisper-base',
            'small': 'mlx-community/whisper-small',
            'medium': 'mlx-community/whisper-medium',
            'large-v3': 'mlx-community/whisper-large-v3',
            'large-v3-turbo': 'mlx-community/whisper-large-v3-turbo',
        }
        return mlx_map.get(self.config.model_size, 'mlx-community/whisper-large-v3-turbo')
    
    def transcribe_async(self, audio, callback):
        """异步转写（批量模式调用）"""
        with self._lock:
            if self._current_future and not self._current_future.done():
                callback("", None, 0, RuntimeError("STT 正忙"))
                return
            self._current_future = self._executor.submit(self.transcribe_sync, audio)
            self._current_future.add_done_callback(
                lambda f: callback(*self._unpack_result(f))
            )
```

### 3.4 实时模式（复用 VADSegmentTranscriber）

mlx-whisper 的实时模式**不需要新建流式引擎类**，直接复用现有架构：

```
CoreEngine
  └── VADSegmentTranscriber（现有，已实现 VAD 切分）
        └── MlxWhisperEngine.transcribe_sync()（新增，分段转写）
```

这样 `engine.py` 的引擎选择逻辑更简洁：

```python
stt_engine_type = getattr(config.stt, 'engine', 'auto')

if stt_engine_type == 'auto':
    stt_engine_type = 'mlx_whisper' if platform.system() == 'Darwin' else 'faster_whisper'

# streaming_enabled 对 mlx_whisper 也生效
streaming_enabled = getattr(config.stt.streaming, 'enabled', False) if \
    stt_engine_type in ('funasr', 'mlx_whisper') else False

if stt_engine_type == 'mlx_whisper':
    from core.stt_mlx_whisper import MlxWhisperEngine
    self._stt_engine = MlxWhisperEngine(config.stt)
    if streaming_enabled:
        from core.vad_segment_transcriber import VADSegmentTranscriber
        self._stream_transcriber = VADSegmentTranscriber(config.realtime, self._stt_engine, ...)
    # ...
```

### 3.5 引擎接口统一

所有引擎实现统一接口，`CoreEngine` 通过鸭子类型调用：

```python
# 引擎统一接口（隐式协议）
class EngineInterface:
    def load_model(self) -> tuple[bool, str]: ...
    def transcribe_sync(self, audio: np.ndarray) -> str: ...
    def transcribe_async(self, audio, callback): ...
    def shutdown(self): ...
    def reset(self): ...        # 流式引擎需要
    def get_chunk_samples(self): ...  # 流式引擎需要
    def transcribe_chunk(self, audio, is_final): ...  # 流式引擎需要
    def is_ready(self) -> bool: ...
```

### 3.6 配置更新

```python
# config.py STTConfig 修改

@dataclass
class STTConfig:
    engine: str = "auto"  # auto | faster_whisper | funasr | mlx_whisper
    ...
    
    def __post_init__(self):
        valid_engines = ("auto", "faster_whisper", "funasr", "mlx_whisper")
        if self.engine not in valid_engines:
            raise ValueError(f"stt.engine 无效值 '{self.engine}'，可选: {valid_engines}")
```

### 3.7 Web 配置页更新

引擎下拉框新增选项：
- `auto` — 自动选择（Mac: mlx-whisper, Windows: faster-whisper）
- `faster_whisper` — Whisper (CPU/GPU)
- `funasr` — FunASR（仅 Windows，运行时安装）
- `mlx_whisper` — MLX Whisper（仅 macOS）

macOS 上自动隐藏 FunASR 选项，Windows 上自动隐藏 mlx-whisper 选项。

### 3.8 macOS 打包 spec 更新

```python
# voice-input-tool-mac.spec hiddenimports 补充
hiddenimports += [
    'mlx_whisper',
    'mlx', 'mlx.core',
]

# excludes - 不再需要 FunASR 相关的排除（已改为运行时安装）
# mlx-whisper 会被 PyInstaller 正确收集（纯 Python + mlx C 扩展）
```

### 3.9 requirements 更新

```
# requirements_macos.txt 新增
mlx-whisper>=0.1.0
```

---

## 四、实施步骤

| Step | 内容 | 涉及文件 | 预估时间 |
|------|------|---------|----------|
| 1 | `pip install mlx-whisper`，验证 API 和返回结构 | - | 30min |
| 2 | 创建 `core/stt_mlx_whisper.py` 分段引擎 | 新增文件 | 30min |
| 3 | 修改 `config.py` 支持 auto + mlx_whisper | 修改文件 | 10min |
| 4 | 修改 `core/engine.py` 引擎选择逻辑 | 修改文件 | 20min |
| 5 | 修改 Web 配置页引擎选项 | 修改文件 | 15min |
| 6 | 单元测试 | 新增测试 | 30min |
| 7 | Mac 本地集成测试 + 延迟基准 | 手动 | 30min |
| **合计** | | | **~2.5h** |

---

## 五、测试方案

### 5.1 单元测试

#### 5.1.1 MlxWhisperEngine 测试

| # | 测试项 | 方法 | 预期 |
|---|--------|------|------|
| T1 | load_model 成功 | 调用 load_model() | (True, "") |
| T2 | load_model 非 macOS | mock platform.system()="Windows" | 回退提示 |
| T3 | transcribe_sync 中文 | 录制 3s 中文音频 | 返回非空中文字符串 |
| T4 | transcribe_sync 英文 | 录制 3s 英文音频 | 返回非空英文字符串 |
| T5 | transcribe_sync 空音频 | 传入空数组 | 返回空字符串 |
| T6 | transcribe_sync 短音频 | 传入 <0.2s 音频 | 返回空字符串 |
| T7 | transcribe_async 回调 | 提交转写任务 | callback 正确触发 |
| T8 | 繁简转换 | 中文繁体音频 | 输出简体中文（opencc） |
| T9 | language=None 自动检测 | 中英混合音频 | 正确检测语言 |
| T10 | shutdown | 关闭引擎 | 无异常 |

##### 5.1.2 VADSegmentTranscriber + MlxWhisperEngine 集成测试

| # | 测试项 | 方法 | 预期 |
|---|--------|------|------|
| T11 | VAD 切分 + mlx 转写 | 播放 10s 语音 | VAD 正确切分，每段转写正确 |
| T12 | 实时模式启动 | engine="mlx_whisper" + streaming=true | VADSegmentTranscriber 正常启动 |
| T13 | 实时模式停止 | stop() 后 | buffer 中剩余音频被正确转写 |
| T14 | 实时模式 reset | 多次 toggle | 每次重新开始，无残留 |

#### 5.1.3 集成测试

| # | 测试项 | 方法 | 预期 |
|---|--------|------|------|
| T15 | CoreEngine auto 选择 Mac | macOS 启动 engine="auto" | 选择 mlx_whisper |
| T16 | CoreEngine auto 选择 Win | Windows 启动 engine="auto" | 选择 faster_whisper |
| T17 | 手动选 mlx_whisper | 配置 engine="mlx_whisper" | 正常启动 |
| T18 | 配置验证 | engine="mlx_whisper" + 非法 model_size | 报错或自动修正 |
| T19 | streaming_enabled 生效 | mlx_whisper + streaming=true | 启用 VADSegmentTranscriber |
| T20 | 模型下载失败 | 断网状态首次启动 | 优雅报错，不崩溃 |
| T21 | 模型下载恢复 | 中断后重试 | 从断点继续 |
| T22 | 繁简转换 | 中文繁体音频 | 输出简体中文（验证 mlx 输出倾向） |
| T23 | mlx-whisper 返回结构 | 调用 transcribe | 正确解析 text 和 segments |

### 5.2 性能基准测试

在 M 系列芯片上对比三种引擎：

| 指标 | faster-whisper (CPU) | mlx-whisper (MLX) | FunASR |
|------|---------------------|-------------------|--------|
| 3s 音频转写延迟 | ~2s | ~0.5s | ~1.5s |
| 10s 音频转写延迟 | ~5s | ~1s | ~3s |
| 内存占用 | ~500MB | ~400MB | ~1.5GB |
| 首次加载时间 | ~3s | ~2s | ~5s |
| 模型下载大小 | ~1.5GB | ~1.5GB（共享） | ~2GB |

> 实际数据待测试后填入

### 5.3 实时转写端到端测试

| # | 测试项 | 方法 | 预期 |
|---|--------|------|------|
| E1 | 持续中文语音 | 对着麦克风说 30s 中文 | 实时输出，延迟 < 3s |
| E2 | 持续英文语音 | 对着麦克风说 30s 英文 | 实时输出，延迟 < 3s |
| E3 | 中英混合 | 中英交替说 | 自动检测切换 |
| E7 | 延迟基准 | 计时 VAD 切分到文字输出 | 记录实际延迟数据 |
| E4 | 环境噪音 | 有背景噪音 | 不产生大量误识别 |
| E5 | 长时间运行 | 持续 5 分钟 | 无内存泄漏，无卡顿 |
| E6 | 切换引擎 | 运行中切换 mlx_whisper → faster_whisper | 平滑切换 |

---

## 六、风险

| 风险 | 影响 | 概率 | 缓解 |
|------|------|------|------|
| mlx-whisper 无流式 API | 需自建 VAD 分段方案 | 已知 | 用 VAD 分段模式替代 |
| mlx C 扩展 PyInstaller 收集 | 运行时 ImportError | 中 | spec 使用 `--collect-all mlx` + 构建后验证 |
| 模型需额外下载 1.5GB | 首次使用等待 | 确定 | huggingface_hub `resume_download=True` 断点续传 + 进度回调 |
| VAD 分段延迟 > FunASR 流式 | 用户体验差异 | 中 | MLX 转写快可弥补；提供引擎切换 |
| mlx-whisper 中文输出繁/简未验证 | 繁简转换逻辑需调整 | 低 | T22 测试验证后决定是否调整 opencc |

---

*设计完成，待 Master 评审确认后实施。*
