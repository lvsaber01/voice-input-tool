# Fun-ASR-Nano + Qwen3-ASR 集成设计方案

> **版本**: v5.0  
> **日期**: 2026-04-28  
> **项目**: voice-input-tool  
> **设计者**: Saber  
> **状态**: AI 评审中（v5.0）  
> **评审历程**: v2.0=72 → v3.0=76(含误扣) → v4.0=83 → v5.0=待评  
> **FunASR 版本要求**: ≥ 1.1（当前安装 1.3.1）

---

## 一、设计目标

### 1.1 背景

voice-input-tool 是一个 Windows/macOS 语音输入工具，用户按热键录音，松开后 ASR 转写，文字自动注入到光标位置。

当前支持以下 STT 引擎：

| 引擎 | 中文 CER (AISHELL-1) | CPU 速度 | 语言 | 方言 |
|------|---------------------|---------|------|------|
| Paraformer-zh | 1.95% | ~35x RTF | 中/英 | ❌ |
| SenseVoiceSmall | ~3.0% | ~20x RTF | 50+语言 | 弱 |
| faster-whisper | 4.72% | ~5x RTF | 99+语言 | 弱 |
| mlx-whisper | 4.72% | ~5x RTF (Apple Silicon) | 99+语言 | 弱 |

需引入两款新一代模型作为可选引擎：

**Fun-ASR-Nano-2512**（阿里通义，2025.12）
- 800M 参数，AISHELL-1 CER 1.76%，噪声场景 SOTA
- 支持7种中文方言、26种口音、歌词识别
- CPU 推理速度比 Paraformer 慢（约 0.3~0.5 RTF），但精度大幅提升

**Qwen3-ASR**（阿里 Qwen，2026.01）
- 0.6B（CER ~2.5%）和 1.7B（CER ~1.9%）两个尺寸
- 52种语言+22种中文方言
- CPU 推理 RTF ~0.18（0.6B），纯CPU可用
- 独立 pip 包 `qwen-asr`，API 简洁

### 1.2 目标

1. Fun-ASR-Nano 作为 FunASR 引擎的新模型选项（同生态升级）
2. Qwen3-ASR 作为全新的独立 STT 引擎（新引擎类型 `qwen3_asr`）
3. 用户通过 config.yaml 一行配置即可切换
4. 不影响现有任何引擎的功能

### 1.3 成功标准

1. `stt.engine: funasr` + `model_size: Fun-ASR-Nano` 可正常运行
2. `stt.engine: qwen3_asr` + `model_size: Qwen3-ASR-0.6B` 可正常运行
3. 新引擎接口与现有引擎完全兼容（load_model / transcribe_async / transcribe_sync / shutdown）
4. 模型加载失败时有清晰错误提示（含安装命令）
5. 新引擎未安装依赖时不会崩溃，而是报错并降级提示
6. 现有引擎功能零回归
7. Web UI 展示所有新引擎选项

### 1.4 非目标

- 不实现 Qwen3-ASR 的流式模式（首期仅离线批量）
- 不实现 Qwen3-ASR 的时间戳/强制对齐功能
- 不实现 Fun-ASR-Nano 的流式模式（暂无官方流式支持）

---

## 二、方案设计

### 2.1 架构概览

```
config.yaml
  │
  ├─ stt.engine: auto → macOS: mlx_whisper, Windows/Linux: funasr (SenseVoiceSmall)
  │
  ├─ stt.engine: funasr → FunASREngine
  │    ├─ model_size: paraformer-zh       (现有)
  │    ├─ model_size: SenseVoiceSmall      (现有)
  │    └─ model_size: Fun-ASR-Nano        (新增 ★)
  │
  └─ stt.engine: qwen3_asr → Qwen3ASREngine  (新增 ★)
       ├─ model_size: Qwen3-ASR-0.6B
       └─ model_size: Qwen3-ASR-1.7B
```

### 2.2 引擎选择路由（engine.py 修改）

在现有 `engine.py` 的引擎选择分支中，新增 `qwen3_asr` 分支。

**完整 auto 模式逻辑（含向后兼容）：**

```python
# auto 模式：macOS 选 mlx_whisper，其他选 funasr
if stt_engine_type == 'auto':
    import platform
    if platform.system() == 'Darwin':
        stt_engine_type = 'mlx_whisper'
    else:
        # 向后兼容：如果用户之前的 model_size 是 whisper 格式，保持 faster_whisper
        whisper_sizes = ('tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo')
        if config.stt.model_size in whisper_sizes:
            stt_engine_type = 'faster_whisper'
            logger.info("auto 模式：检测到 whisper model_size，保持 faster_whisper")
        else:
            stt_engine_type = 'funasr'
            # auto 模式 Windows/Linux 默认用 SenseVoiceSmall
            if config.stt.model_size not in (
                'paraformer-zh', 'paraformer-zh-streaming', 
                'paraformer-en', 'SenseVoiceSmall', 'Fun-ASR-Nano'
            ):
                config.stt.model_size = 'SenseVoiceSmall'
    logger.info("auto 模式：选择 %s 引擎", stt_engine_type)

# Qwen3-ASR 引擎路由
elif stt_engine_type == 'qwen3_asr':
    from core.stt_qwen3_asr import Qwen3ASREngine, Qwen3ASREngineUnavailableError
    from core.vad_segment_transcriber import VADSegmentTranscriber
    try:
        self._stt_engine = Qwen3ASREngine(config.stt)
        self._stream_transcriber = VADSegmentTranscriber(
            config.realtime, self._stt_engine, self._on_realtime_segment
        )
        self._streaming_mode = False
        logger.info("使用 Qwen3-ASR 引擎")
    except Qwen3ASREngineUnavailableError as e:
        # 降级：提示用户安装依赖，不崩溃
        logger.error("Qwen3-ASR 引擎不可用: %s", e)
        # 向 CoreEngine 报告错误，由 UI 展示
        raise RuntimeError(str(e))
```

### 2.3 Fun-ASR-Nano 集成（FunASREngine 修改）

**策略**：在现有 `FunASREngine` 中新增模型分支，最小改动。

**变更文件**：
1. `core/stt_funasr.py` — load_model() 新增分支 + _do_transcribe() 参数适配
2. `config.py` — valid_funasr_sizes 新增 "Fun-ASR-Nano"

**Fun-ASR-Nano 加载代码（load_model）：**

```python
elif model_name == "Fun-ASR-Nano":
    try:
        from funasr.models.fun_asr_nano.model import FunASRNano  # 注册模型类
    except ImportError:
        return False, "FunASR 版本过低，请升级: pip install 'funasr>=1.1'"
    self.model = AutoModel(
        model="FunAudioLLM/Fun-ASR-Nano-2512",
        trust_remote_code=True,
        # 注意：不指定 remote_code，由 trust_remote_code 自动从 HF 仓库拉取
        device="cpu",
        disable_update=True,
    )
    self._is_sensevoice = False
    self._is_fun_asr_nano = True
    logger.info("Fun-ASR-Nano 模型加载完成")
```

**Fun-ASR-Nano 转写代码（_do_transcribe）：**

```python
if self._is_fun_asr_nano:
    # Fun-ASR-Nano generate 参数
    import time
    t0 = time.monotonic()
    
    result = self.model.generate(
        input=audio_chunk,
        cache={},
        batch_size=1,
        language="auto",   # 自动检测语言（支持中英日）
        itn=True,          # 逆文本规范化（数字、日期格式化）
    )
    
    duration_ms = int((time.monotonic() - t0) * 1000)
    
    if result and len(result) > 0:
        text = result[0].get("text", "") or ""
        return (text.strip(), None, duration_ms, None)
    return ("", None, duration_ms, None)
```

**设计决策说明：**
- `language` 首期硬编码 `"auto"`（自动检测），不暴露给用户配置
- `itn` 默认开启，未来可通过 config 扩展
- 结果提取与 Paraformer 兼容（`result[0]["text"]`）

### 2.4 Qwen3-ASR 集成（新文件）

**策略**：新建 `core/stt_qwen3_asr.py`，完整实现标准引擎接口。

**新建文件**：`core/stt_qwen3_asr.py`

**完整实现（含所有接口方法）：**

```python
"""Qwen3-ASR 分段转写引擎

使用 qwen-asr 官方 pip 包，支持 Qwen3-ASR-0.6B 和 Qwen3-ASR-1.7B。
接口与 STTEngine / FunASREngine 完全兼容。

依赖：pip install qwen-asr torch torchaudio
平台：Windows / Linux / macOS（CPU + GPU）
"""

import time
import threading
import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, Future

import numpy as np

logger = logging.getLogger(__name__)


class Qwen3ASREngineUnavailableError(Exception):
    """qwen-asr 未安装或环境不可用"""
    pass


class Qwen3ASREngine:
    """Qwen3-ASR 分段转写引擎

    使用 ThreadPoolExecutor(max_workers=1) 保证单任务执行。
    异步提交转写任务，通过回调返回结果。

    线程模型与 FunASREngine / MlxWhisperEngine 完全一致。
    """

    def __init__(self, config):
        self.config = config
        self.model = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._current_future: Optional[Future] = None
        self._lock = threading.Lock()

    @staticmethod
    def is_available() -> bool:
        """检查 qwen-asr 是否可用"""
        try:
            import qwen_asr  # noqa: F401
            import torch    # noqa: F401
            return True
        except ImportError:
            return False

    def load_model(self) -> tuple[bool, str]:
        """加载 Qwen3-ASR 模型

        自动检测 CUDA 可用性，选择 device 和 dtype。
        
        GPU dtype 优先级：bfloat16 > float16 > float32
        CPU dtype：float32

        Returns:
            (True, '') 成功, (False, '错误信息') 失败
        """
        # Step 1: 检查依赖是否可用
        try:
            import qwen_asr
        except ImportError:
            return False, (
                'qwen-asr 未安装。请运行: '
                'pip install qwen-asr torch torchaudio'
            )

        try:
            import torch
        except ImportError:
            return False, 'torch 未安装。请运行: pip install torch torchaudio'

        # Step 2: 确定 device 和 dtype
        if torch.cuda.is_available():
            device = "cuda:0"
            # 按优先级尝试 dtype，bfloat16 不支持时降级到 float16
            if torch.cuda.is_bf16_supported():
                dtype = torch.bfloat16
            else:
                dtype = torch.float16
        else:
            device = "cpu"
            dtype = torch.float32

        # Step 3: 加载模型
        try:
            from qwen_asr import Qwen3ASRModel

            model_name = self.config.model_size  # "Qwen3-ASR-0.6B" or "Qwen3-ASR-1.7B"
            
            # 设置 HuggingFace 镜像（中国用户）
            hf_endpoint = getattr(self.config, 'hf_endpoint', '')
            if hf_endpoint:
                import os
                os.environ['HF_ENDPOINT'] = hf_endpoint

            # 从配置读取 max_new_tokens（默认 256）
            max_tokens = getattr(self.config, 'max_new_tokens', 256)
            
            self.model = Qwen3ASRModel.from_pretrained(
                f"Qwen/{model_name}",
                dtype=dtype,
                device_map=device,
                max_new_tokens=max_tokens,
            )
            
            logger.info(
                "Qwen3-ASR 模型加载完成: %s, device=%s, dtype=%s",
                model_name, device, dtype
            )
            return True, ''

        except Exception as e:
            error_msg = f"Qwen3-ASR 模型加载失败: {e}"
            logger.error(error_msg)
            return False, error_msg

    def transcribe_async(self, audio: np.ndarray, callback):
        """异步转写：提交到线程池，完成后回调。

        回调签名与现有引擎完全一致：
            callback(text: str, language: Optional[str], duration_ms: int, error: Optional[Exception])
        """
        with self._lock:
            if self._current_future and not self._current_future.done():
                callback("", None, 0, RuntimeError("STT 正忙"))
                return

            self._current_future = self._executor.submit(self._do_transcribe, audio)
            self._current_future.add_done_callback(
                lambda f: callback(*self._unpack_result(f))
            )

    def _do_transcribe(self, audio: np.ndarray) -> tuple:
        """实际转写逻辑（线程池中执行）

        Returns:
            (text, language, duration_ms, error) 四元组
        """
        try:
            if audio.size == 0 or len(audio) < 3200:  # <0.2s @16kHz
                return ("", None, 0, None)

            if self.model is None:
                return ("", None, 0, RuntimeError("模型未加载"))

            t0 = time.monotonic()

            # Qwen3-ASR 接受 (np.ndarray, sample_rate) 元组
            results = self.model.transcribe(
                audio=(audio, 16000),
                language=None,  # 自动检测语言
            )

            duration_ms = int((time.monotonic() - t0) * 1000)

            if not results or len(results) == 0:
                return ("", None, duration_ms, None)

            text = results[0].text.strip() if results[0].text else ""
            detected_lang = getattr(results[0], 'language', None)

            # 繁简转换（中文输出统一为简体）
            if detected_lang and 'chinese' in detected_lang.lower() and text:
                try:
                    from opencc import OpenCC
                    cc = OpenCC('t2s')
                    text = cc.convert(text)
                except ImportError:
                    pass

            return (text, detected_lang, duration_ms, None)

        except Exception as e:
            logger.error("Qwen3-ASR 转写失败: %s", e, exc_info=True)
            return ("", None, 0, e)

    def _unpack_result(self, future: Future) -> tuple:
        """解包 Future 结果"""
        try:
            return future.result()
        except Exception as e:
            return ("", None, 0, e)

    def transcribe_sync(self, audio: np.ndarray) -> str:
        """同步转写（阻塞式），供 VADSegmentTranscriber 调用

        Args:
            audio: numpy 音频数组 (16000Hz, float32)

        Returns:
            识别文本（空字符串表示无结果）
        """
        text, _, _, _ = self._do_transcribe(audio)
        return text

    def shutdown(self):
        """关闭线程池，释放资源"""
        self._executor.shutdown(wait=True)
        self.model = None
        # 释放 GPU 显存
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        logger.info("Qwen3-ASR 引擎已关闭")
```

**接口一致性对照表：**

| 接口方法 | FunASREngine | MlxWhisperEngine | Qwen3ASREngine | 一致性 |
|----------|--------------|------------------|----------------|--------|
| `__init__(config)` | ✅ | ✅ | ✅ | ✅ |
| `is_available() -> bool` | ❌ 无 | ✅ | ✅ | 与 MlxWhisper 一致 |
| `load_model() -> (bool, str)` | ✅ | ✅ | ✅ | ✅ |
| `transcribe_async(audio, callback)` | ✅ | ✅ | ✅ | ✅ |
| `transcribe_sync(audio) -> str` | ✅ | ✅ | ✅ | ✅ |
| `shutdown()` | ✅ | ✅ | ✅ | ✅ |
| `ThreadPoolExecutor(max_workers=1)` | ✅ | ✅ | ✅ | ✅ |
| `_lock` 保护 | ✅ | ✅ | ✅ | ✅ |
| 正忙检测 | ✅ | ✅ | ✅ | ✅ |
| 短音频跳过（<0.2s） | ✅ | ✅ | ✅ | ✅ |
| 繁简转换（opencc） | ✅ | ✅ | ✅ | ✅ |
| 回调签名 | `(text, lang, dur, err)` | `(text, lang, dur, err)` | `(text, lang, dur, err)` | ✅ |

### 2.5 配置变更

**config.py — STTConfig 修改：**

```python
@dataclass
class STTConfig:
    engine: str = "auto"
    model_size: str = "large-v3-turbo"
    # ... 现有字段不变 ...
    # 新增字段
    max_new_tokens: int = 256       # Qwen3-ASR 最大生成长度（仅 qwen3_asr 引擎使用）

    def __post_init__(self):
        # 扩展有效引擎列表
        valid_engines = (
            "auto", "faster_whisper", "funasr", 
            "mlx_whisper", "qwen3_asr"  # 新增 ★
        )
        if self.engine not in valid_engines:
            raise ValueError(f"stt.engine 无效值 '{self.engine}'，可选: {valid_engines}")

        # faster_whisper / auto / mlx_whisper 的 model_size 校验（不变）
        if self.engine in ("faster_whisper", "auto", "mlx_whisper"):
            valid_sizes = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
            if self.model_size not in valid_sizes:
                if self.model_size in ("paraformer-zh", "paraformer-zh-streaming"):
                    logger.info("model_size '%s' is FunASR-only, auto-switching to large-v3-turbo", self.model_size)
                    self.model_size = "large-v3-turbo"
                elif self.model_size in ("Qwen3-ASR-0.6B", "Qwen3-ASR-1.7B"):
                    logger.info("model_size '%s' is Qwen3-ASR-only, auto-switching to large-v3-turbo", self.model_size)
                    self.model_size = "large-v3-turbo"
                else:
                    raise ValueError(f"stt.model_size 无效值 '{self.model_size}'，可选: {valid_sizes}")

        # FunASR model_size 校验（扩展）
        elif self.engine == 'funasr':
            valid_funasr_sizes = (
                "paraformer-zh", "paraformer-zh-streaming", 
                "paraformer-en", "SenseVoiceSmall", "Fun-ASR-Nano"  # 新增 ★
            )
            if self.model_size not in valid_funasr_sizes:
                logger.info("model_size '%s' 不支持，auto-switching to paraformer-zh", self.model_size)
                self.model_size = "paraformer-zh"

        # Qwen3-ASR model_size 校验（新增 ★）
        elif self.engine == 'qwen3_asr':
            valid_qwen3_sizes = ("Qwen3-ASR-0.6B", "Qwen3-ASR-1.7B")
            if self.model_size not in valid_qwen3_sizes:
                logger.info("model_size '%s' 不支持，auto-switching to Qwen3-ASR-0.6B", self.model_size)
                self.model_size = "Qwen3-ASR-0.6B"

        # ... 其余校验不变 ...
```

**config.yaml 示例：**

```yaml
# 方式1：Fun-ASR-Nano（FunASR 引擎内升级）
stt:
  engine: funasr
  model_size: Fun-ASR-Nano

# 方式2：Qwen3-ASR-0.6B（CPU 友好）
stt:
  engine: qwen3_asr
  model_size: Qwen3-ASR-0.6B

# 方式3：Qwen3-ASR-1.7B（GPU 环境，精度最佳）
stt:
  engine: qwen3_asr
  model_size: Qwen3-ASR-1.7B
  max_new_tokens: 512  # 长音频场景可调大
```

**配置版本迁移**：v5 → v6

```python
def _migrate_v5_to_v6(raw: Dict[str, Any]) -> Dict[str, Any]:
    """v5 → v6: 新增 Qwen3-ASR 引擎支持
    
    - 新增 max_new_tokens 字段（默认 256）
    - 无需强制迁移引擎（保持用户现有配置）
    """
    raw["config_version"] = 6
    stt = raw.get("stt", {})
    if "max_new_tokens" not in stt:
        stt["max_new_tokens"] = 256
    raw["stt"] = stt
    return raw
```

### 2.6 依赖变更

**可选依赖文件（新增）：**

```
requirements-funasr-nano.txt:
  funasr>=1.1
  modelscope

requirements-qwen3-asr.txt:
  qwen-asr
  torch>=2.1
  torchaudio>=2.1
```

**安装说明（README 更新）：**

```markdown
### 扩展引擎（可选）

# Fun-ASR-Nano（中文精度最高，支持方言）
pip install -r requirements-funasr-nano.txt

# Qwen3-ASR（多语言，CPU 可用）
pip install -r requirements-qwen3-asr.txt
```

---

## 三、兼容性与风险

### 3.1 向后兼容

| 场景 | 影响 | 处理方式 |
|------|------|---------|
| 现有 Paraformer 用户 | 无影响 | 代码路径不变 |
| 现有 SenseVoice 用户 | 无影响 | 代码路径不变 |
| 现有 faster-whisper 用户 | 无影响 | 代码路径不变 |
| 现有 mlx-whisper 用户 | 无影响 | 代码路径不变 |
| `engine: auto` + `model_size: large-v3-turbo` | 无影响 | 向后兼容：检测到 whisper model_size 保持 faster_whisper |
| `engine: auto` + 无 model_size 或非 whisper model_size | 行为变更 | 改为 funasr + SenseVoiceSmall |
| `engine: qwen3_asr` + 未安装 qwen-asr | 不会崩溃 | load_model 返回 (False, 安装提示) |
| 旧 config.yaml（v5）加载 | 无影响 | 自动迁移 v5→v6，仅新增 max_new_tokens 默认值 |

### 3.2 风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| Fun-ASR-Nano trust_remote_code 下载失败 | 低 | disable_update=True，首次加载后缓存本地 |
| Fun-ASR-Nano generate 参数差异 | 低 | _is_fun_asr_nano 标志位隔离调用路径 |
| qwen-asr import 失败 | 已缓解 | is_available() + load_model 优雅报错 + 安装命令提示 |
| torch dtype 不兼容（bfloat16） | 已缓解 | torch.cuda.is_bf16_supported() 检测 + float16 fallback |
| torch 版本冲突（funasr vs qwen-asr） | 中 | 可选安装，两引擎互斥不同时加载；文档记录测试通过的版本组合 |
| Qwen3-ASR CPU 推理较慢 | 低 | 文档说明 + 0.6B 尺寸 CPU 可用 |
| 首次加载模型超时 | 中 | 设计提示：首次加载需下载模型，约 1-2GB，确保网络通畅 |
| Qwen3-ASR language 输出格式不确定 | 低 | 代码中使用 `getattr(results[0], 'language', None)` 安全访问 |

---

## 四、测试计划

### 4.1 单元测试

**新增测试文件：**
- `tests/test_funasr_nano_engine.py` — Fun-ASR-Nano 分支测试
- `tests/test_qwen3_asr_engine.py` — Qwen3-ASR 完整引擎测试

**Qwen3-ASR 测试用例：**

| # | 测试项 | 输入 | 预期输出断言 |
|---|--------|------|-------------|
| 1 | is_available() 依赖检查 | 未安装 qwen-asr 的环境 | `result == False` |
| 2 | load_model 依赖缺失 | 无 qwen-asr | `(False, str)` 且 `'qwen-asr' in result[1]` |
| 3 | load_model torch 缺失 | 无 torch | `(False, str)` 且 `'torch' in result[1]` |
| 4 | load_model 成功 | 正常环境 + Qwen3-ASR-0.6B | `(True, '')` |
| 5 | transcribe_async 回调签名 | 10s 标准音频 | callback 收到 4 元组 `(str, str|None, int, Exception|None)` |
| 6 | transcribe_async 正忙检测 | 连续两次 transcribe_async | 第二次 callback 的 `isinstance(error, RuntimeError) and "正忙" in str(error)` |
| 7 | transcribe_sync 返回文本 | 10s 标准普通话音频 | `isinstance(result, str) and len(result) > 0` |
| 8 | transcribe_sync 短音频 | `np.zeros(1600)` (<0.1s) | `result == ""` |
| 9 | transcribe_sync 空音频 | `np.array([])` | `result == ""` |
| 10 | transcribe_sync 未加载 | model=None + 10s 音频 | `result == ""` |
| 11 | shutdown 释放资源 | 加载后调用 shutdown() | `engine.model is None` |
| 12 | shutdown CUDA 缓存 | GPU 环境下加载后 shutdown | `torch.cuda.memory_allocated() < shutdown 前` |
| 13 | 繁简转换 | 含繁体字的音频 | 输出为简体中文（opencc 可用时） |

**Fun-ASR-Nano 测试用例：**

| # | 测试项 | 说明 |
|---|--------|------|
| 1 | load_model Fun-ASR-Nano 分支 | 正确加载 FunASRNano 模型 |
| 2 | _do_transcribe 生成参数 | 使用 itn=True, language="auto" |
| 3 | 结果提取兼容 | result[0]["text"] 格式正确 |
| 4 | 配置校验 Fun-ASR-Nano | valid_funasr_sizes 包含 Fun-ASR-Nano |

### 4.2 集成测试

| # | 测试项 | 说明 |
|---|--------|------|
| 1 | engine.py 路由 — qwen3_asr | 正确路由到 Qwen3ASREngine |
| 2 | engine.py 路由 — funasr + Fun-ASR-Nano | 正确使用 FunASREngine |
| 3 | auto 模式 — 新安装用户 | Windows 默认 funasr + SenseVoiceSmall |
| 4 | auto 模式 — 现有 whisper 用户 | 检测到 whisper model_size，保持 faster_whisper |
| 5 | 批量模式 F8 — Fun-ASR-Nano | 录音→转写→注入 全流程 |
| 6 | 批量模式 F8 — Qwen3-ASR | 录音→转写→注入 全流程 |
| 7 | 实时模式 — Qwen3-ASR | VAD 分段→逐段转写→注入 |
| 8 | 引擎切换 | 不同 engine 间切换不影响 |
| 9 | 配置迁移 v5→v6 | 旧 config 加载正常，新增 max_new_tokens |
| 10 | 依赖缺失降级 | engine=qwen3_asr 但未安装时，错误提示而非崩溃 |

### 4.3 回归测试

现有测试全部通过（`pytest tests/`）：
- `test_stt_engine.py` — faster-whisper 不受影响
- `test_sensevoice_engine.py` — SenseVoice 不受影响
- `test_streaming_engine.py` — FunASR 流式不受影响

---

## 五、实现计划

### Phase 1: Fun-ASR-Nano 集成（预计 45 分钟）

1. `config.py` — valid_funasr_sizes 新增 "Fun-ASR-Nano"
2. `stt_funasr.py` — load_model 新增 Fun-ASR-Nano 分支
3. `stt_funasr.py` — _do_transcribe 新增 _is_fun_asr_nano 分支
4. 编写 Fun-ASR-Nano 单元测试
5. 验证测试通过

### Phase 2: Qwen3-ASR 集成（预计 1.5 小时）

1. 新建 `core/stt_qwen3_asr.py` — 完整引擎实现（所有接口方法）
2. `config.py` — 新增 "qwen3_asr" engine + valid_qwen3_sizes + max_new_tokens
3. `config.py` — 新增 _migrate_v5_to_v6 迁移函数
4. `engine.py` — 新增引擎路由分支 + import 失败处理
5. 编写 Qwen3-ASR 单元测试
6. 验证测试通过

### Phase 3: auto 模式 + Web UI（预计 30 分钟）

1. `engine.py` — 修改 auto 模式逻辑（含向后兼容）
2. Web UI 后端 — 新增引擎/模型选项 API
3. Web UI 前端 — engine 下拉框 + model_size 联动 + 说明文字

### Phase 4: 文档与收尾（预计 30 分钟）

1. 新增 `requirements-funasr-nano.txt` / `requirements-qwen3-asr.txt`
2. 更新 README（安装说明、引擎对比表）
3. 回归测试全量通过（`pytest tests/`）
4. 提交 PR

**总预估**：约 3 小时

---

## 六、已确认决策

1. **Fun-ASR-Nano 的 trust_remote_code**：无需手动下载，`funasr` AutoModel 自动从 HF 仓库拉取 `model.py`。需确保 `funasr>=1.1`。
2. **qwen-asr 的 torch 依赖**：两个引擎互斥使用，不会同时加载。冲突风险低。
3. **auto 模式**：`stt.engine: auto` 默认改为 FunASR SenseVoiceSmall，但向后兼容现有 whisper 用户。
4. **Web UI**：需展示所有新引擎选项（engine 下拉框 + model_size 联动 + 说明文字）。
5. **Qwen3-ASR dtype 策略**：GPU 优先 bfloat16，不支持时 fallback float16；CPU 使用 float32。
6. **Qwen3-ASR language 输出**：使用 `getattr` 安全访问，不依赖固定格式。

---

## 七、Web UI 变更

### 后端 API（web_server.py 或对应文件）

新增/修改配置 API 返回值，包含引擎和模型选项：

```python
ENGINE_OPTIONS = [
    {"value": "auto", "label": "自动选择"},
    {"value": "faster_whisper", "label": "Faster Whisper"},
    {"value": "funasr", "label": "FunASR"},
    {"value": "qwen3_asr", "label": "Qwen3-ASR"},
    {"value": "mlx_whisper", "label": "MLX Whisper (macOS)"},
]

MODEL_SIZE_OPTIONS = {
    "funasr": [
        {"value": "paraformer-zh", "label": "Paraformer 中文", "desc": "速度快，CPU最优"},
        {"value": "SenseVoiceSmall", "label": "SenseVoice", "desc": "50+语言，多任务"},
        {"value": "Fun-ASR-Nano", "label": "Fun-ASR-Nano 800M", "desc": "新一代，中文精度最高，支持7种方言，CPU推理较慢"},
    ],
    "qwen3_asr": [
        {"value": "Qwen3-ASR-0.6B", "label": "Qwen3-ASR 0.6B", "desc": "52种语言+22种方言，CPU可用，精度适中"},
        {"value": "Qwen3-ASR-1.7B", "label": "Qwen3-ASR 1.7B", "desc": "精度最佳，建议GPU环境使用"},
    ],
    # ... 其他引擎现有选项 ...
}
```

### 前端（配置页面）

**联动状态流转：**
```
用户选择 engine → 前端读取 MODEL_SIZE_OPTIONS[engine] → 更新 model_size 下拉框选项
→ 若 engine 从 funasr 切换到 qwen3_asr → model_size 重置为 "Qwen3-ASR-0.6B"
→ 若 engine 从 qwen3_asr 切换到 funasr → model_size 重置为 "paraformer-zh"
```

**未安装依赖时的 UI 提示：**
- 当 engine=qwen3_asr 但后端返回「qwen-asr 未安装」时
- model_size 下拉框下方显示黄色警告：「Qwen3-ASR 需要额外安装依赖：pip install -r requirements-qwen3-asr.txt」
- 保存按钮仍然可用，但启动时会报错退出

**加载状态展示：**
- 首次加载 Fun-ASR-Nano 或 Qwen3-ASR 时，需要下载模型（1-2GB）
- Web 页面底部显示进度提示：「首次加载需要下载模型，请耐心等待...」

---

## 八、性能验证计划

### 8.1 测试方法

**RTF 测试：**
```python
import time, numpy as np

test_audio = np.random.randn(160000).astype(np.float32)  # 10s 静音/白噪声
t0 = time.monotonic()
result = engine.transcribe_sync(test_audio)
rtf = (time.monotonic() - t0) / 10.0
# 记录：engine_name, model_size, rtf, text_length
```

**内存/显存测试：**
```python
import torch, tracemalloc

tracemalloc.start()
# 加载前 baseline
baseline_mem = tracemalloc.get_traced_memory()[0]
baseline_gpu = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0

engine.load_model()

# 加载后增量
peak_mem = tracemalloc.get_traced_memory()[1] - baseline_mem
gpu_mem = torch.cuda.memory_allocated() - baseline_gpu if torch.cuda.is_available() else 0
tracemalloc.stop()
# 记录：peak_mem_mb, gpu_mem_mb
```

### 8.2 测试矩阵

| 引擎+模型 | 测试平台 | 音频类型 | 指标 |
|-----------|---------|----------|------|
| funasr + Fun-ASR-Nano | win-dev (CPU) | 10s 普通话 | RTF, 内存 |
| funasr + Fun-ASR-Nano | win-dev (CPU) | 10s 噪声 | RTF, CER |
| qwen3_asr + Qwen3-ASR-0.6B | win-dev (CPU) | 10s 普通话 | RTF, 内存 |
| qwen3_asr + Qwen3-ASR-1.7B | win-dev (GPU) | 10s 普通话 | RTF, 显存 |

### 8.3 测试音频样本

使用 FunASR 官方提供的标准测试音频：
- 中文普通话：`https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ASR/test_audio/asr_example_zh.wav`
- 自录噪声场景：office 环境 + 空调背景 60dB

## 九、依赖版本矩阵

### 9.1 测试通过的组合

| 组合 | torch | funasr | qwen-asr | 备注 |
|------|-------|--------|----------|------|
| Fun-ASR-Nano 专用 | 2.3.0 | 1.3.1 | — | 当前 win-dev 环境 |
| Qwen3-ASR 专用 | 2.3.0 | — | 0.0.5 | 需验证 |
| 全量安装 | 2.3.0 | 1.3.1 | 0.0.5 | 需验证兼容性 |

### 9.2 版本约束

```text
torch>=2.1.0,<3.0       # CPU/GPU 基础依赖
funasr>=1.1.0            # Fun-ASR-Nano 需要 FunASRNano 类
qwen-asr>=0.0.5          # Qwen3ASRModel 稳定 API
transformers>=4.40.0    # 两个引擎共同依赖
```

### 9.3 已知冲突

| 依赖 | 冲突方 | 问题 | 解决方案 |
|------|--------|------|---------|
| funasr | qwen-asr | 无已知冲突 | 两引擎互斥加载，不在同一进程同时使用 |
| torch | — | 无 | 共享 torch 版本即可 |

## 十、故障排查指南

### 10.1 模型下载失败

**症状**：`load_model()` 返回超时错误，或首次启动卡住

**排查步骤**：
1. 检查网络连通性：`ping huggingface.co`
2. 如果在中国大陆，确认 `stt.hf_endpoint` 已设置为 `https://hf-mirror.com`
3. 手动下载模型：
   - Fun-ASR-Nano：`pip install modelscope && modelscope download FunAudioLLM/Fun-ASR-Nano-2512`
   - Qwen3-ASR：`pip install qwen-asr && python -c "from qwen_asr import Qwen3ASRModel; m = Qwen3ASRModel.from_pretrained('Qwen/Qwen3-ASR-0.6B', dtype='float32', device_map='cpu')"`
4. 设置环境变量使用镜像：`export HF_ENDPOINT=https://hf-mirror.com`

### 10.2 CUDA OOM

**症状**：`torch.cuda.OutOfMemoryError`

**排查步骤**：
1. 确认 GPU 显存：`nvidia-smi`（Qwen3-ASR-1.7B 需要 ~8GB）
2. 降级到 0.6B 模型：修改 `model_size: Qwen3-ASR-0.6B`
3. 确认无其他程序占用显存
4. 回退到 CPU 模式：临时设置 `device: cpu`（需代码支持，或使用较小模型）

### 10.3 trust_remote_code 安全说明

`trust_remote_code=True` 会从 HuggingFace 下载并执行模型仓库中的 Python 代码。这意味着：
- Fun-ASR-Nano：下载 `FunAudioLLM/Fun-ASR-Nano-2512` 仓库中的 `model.py`
- 这些代码来自阿里通义实验室（可信来源）
- 如果担心安全，可手动下载模型文件后使用本地路径

### 10.4 FunASR 版本不兼容

**症状**：`ImportError: cannot import name 'FunASRNano'`

**排查步骤**：
1. 检查版本：`pip show funasr`
2. 升级：`pip install 'funasr>=1.1'`
3. 如果升级导致其他问题，检查 `requirements.txt` 中的依赖版本

## 十一、回滚方案

### 11.1 配置回退

如果新引擎有问题，用户可快速回退：

**步骤**：
1. 打开 `config.yaml`
2. 将 `stt.engine` 改回 `faster_whisper`
3. 将 `model_size` 改回 `large-v3-turbo`（或其他之前使用的模型）
4. 重启应用

### 11.2 自动备份策略

应用在每次配置保存时自动备份：
- 备份路径：`config.yaml.bak`（覆盖式，保留上一次）
- 迁移前自动备份（config.py 中 `save_config` 已实现 `.tmp` 原子写入）

### 11.3 启动失败自动回退

如果 `load_model()` 失败：
1. 日志记录错误详情
2. 系统托盘弹窗提示：「模型加载失败：{错误信息}。请检查配置或切换到其他引擎。」
3. 不自动回退引擎（避免用户困惑），由用户手动修改配置

---

*v5.0 — 补充 Web UI 前端设计、性能验证计划、依赖版本矩阵、故障排查指南、回滚方案、测试断言具体化。*
