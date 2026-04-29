# 音频采集采样率兼容性修复 — 设计文档

> **版本**: v5.0  
> **日期**: 2026-04-29  
> **状态**: 待评审（第 5 轮）  
> **关联问题**: 使用大疆无线麦克风一段时间后识别精度骤降，输出与语音完全无关，重启工具无法恢复  
> **项目位置**: `~/PROJECT/voice-input-tool/`  
> **评审历程**: v1.0: 71 → v2.0: 90 → v3.0: 94 → v4.0: 86

---

## 一、问题分析与背景

### 1.1 问题描述

用户在 Windows 上使用 voice-input-tool，通过大疆 Mic Mini 无线接收器连接麦克风，运行一段时间后出现：

- 识别文本与语音输入完全无关
- 说中文输出无关英文
- **重启工具也无法恢复**
- 切换 SenseVoice / Paraformer 均无改善

### 1.2 根因分析

**核心问题：PortAudio 自动重采样不稳定**

当前代码 `recorder.py` 中：
```python
self._stream = sd.InputStream(
    samplerate=self.SAMPLE_RATE,  # 固定 16000
    channels=1,
    dtype='float32',
    callback=self._audio_callback,
    device=device,
)
```

sounddevice/PortAudio 在请求的采样率（16kHz）与设备原生采样率（48kHz，大疆无线接收器常见）不一致时，会自动进行重采样。

**PortAudio 的自动重采样在以下场景可能出现故障：**

| 触发场景 | 机制 |
|---------|------|
| USB 无线接收器断连重连 | 设备重新枚举，原生采样率可能变化，PortAudio 缓存旧参数 |
| Windows 音频独占模式切换 | 其他应用（浏览器/Teams/游戏）独占设备后释放，WASAPI 共享模式管线重置 |
| Windows "通信设备"自动切换 | 系统根据场景自动切换默认输入设备 |
| 系统休眠/唤醒 | 设备驱动状态不完全恢复 |

**为什么重启工具无法恢复？**
问题在 Windows 音频子系统 / PortAudio 设备层，不在应用状态。重启应用只是重新打开同一个设备，PortAudio 仍然拿到异常的设备参数。

**为什么说中文输出英文？**
16kHz 语音被错误重采样后变成频率错乱的信号（类似 8kHz 或 32kHz 的拉伸），ASR 模型收到的是严重失真的音频，音素特征被破坏，模型会"幻觉"出不相关内容。

### 1.3 解决思路

**用设备原生采样率采集 + 代码层手动 resample → 完全绕过 PortAudio 自动重采样**

---

## 二、设计方案

### 2.1 总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         修改前                                    │
│                                                                  │
│  麦克风(48kHz) ──▶ PortAudio自动重采样 ──▶ 16kHz ──▶ ASR(16kHz) │
│                         ↑                                        │
│                    可能故障点                                      │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                         修改后                                    │
│                                                                  │
│  麦克风(48kHz) ──▶ 原生采样率采集(48kHz) ──┐                       │
│                                            ├──▶ 手动resample ──▶ 16kHz ──▶ ASR │
│  批量模式: stop后整体resample               │                       │
│  VAD模式: flush时逐段resample               │                       │
│  流式模式: chunk累积前resample              │                       │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 新增模块：`core/audio_resampler.py`

独立的音频重采样模块，职责单一。

```python
"""音频重采样模块

将任意采样率的音频转换为 ASR 模型所需的 16kHz。
使用 scipy.signal.resample_poly（FIR 抗混叠滤波）。
"""

import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

TARGET_SR = 16000  # ASR 模型统一目标采样率


class AudioResampler:
    """音频重采样器

    使用 scipy.signal.resample_poly 进行高质量抗混叠重采样。
    构造时预计算 up/down 参数，运行时零额外开销。
    """

    def __init__(self, source_sr: int, target_sr: int = TARGET_SR):
        """
        Args:
            source_sr: 源采样率（设备原生采样率）
            target_sr: 目标采样率（默认 16000）
        
        Raises:
            ImportError: scipy 不可用时抛出
            ValueError: source_sr 或 target_sr 无效时抛出
        """
        if source_sr <= 0 or target_sr <= 0:
            raise ValueError(f"采样率必须为正数: source={source_sr}, target={target_sr}")

        self._source_sr = source_sr
        self._target_sr = target_sr

        # 预计算 resample_poly 参数（避免每次调用重复计算）
        self._up = target_sr // math.gcd(source_sr, target_sr)
        self._down = source_sr // math.gcd(source_sr, target_sr)
        self._needs_resample = (source_sr != target_sr)
        self._scipy_signal = None  # 延迟初始化

        if not self._needs_resample:
            logger.info("AudioResampler: %dHz → %dHz, 无需重采样", source_sr, target_sr)
        else:
            # 预加载 scipy.signal 并缓存（v4.0 修复 N1：不再由外部注入私有属性）
            import scipy.signal
            self._scipy_signal = scipy.signal
            logger.info("AudioResampler: %dHz → %dHz, up=%d, down=%d",
                       source_sr, target_sr, self._up, self._down)

    def resample(self, audio: np.ndarray) -> np.ndarray:
        """重采样音频

        Args:
            audio: float32 一维音频数组

        Returns:
            目标采样率的 float32 音频数组

        Raises:
            ImportError: scipy 不可用
        """
        if not self._needs_resample:
            return audio

        if len(audio) == 0:
            return audio

        if audio.ndim != 1:
            logger.warning("AudioResampler: 输入维度=%d, 预期1维, 自动flatten", audio.ndim)
            audio = audio.flatten()

        # 延迟加载 scipy.signal（仅首调用时触发）
        if self._scipy_signal is None:
            import scipy.signal
            self._scipy_signal = scipy.signal
        resampled = self._scipy_signal.resample_poly(audio, self._up, self._down, axis=0)
        return resampled.astype(np.float32)

    @property
    def source_sr(self) -> int:
        return self._source_sr

    @property
    def target_sr(self) -> int:
        return self._target_sr

    @property
    def needs_resample(self) -> bool:
        return self._needs_resample

    def __repr__(self) -> str:
        return (f"AudioResampler(source_sr={self._source_sr}, target_sr={self._target_sr}, "
                f"up={self._up}, down={self._down}, needs_resample={self._needs_resample})")


def create_resampler(source_sr: int, target_sr: int = TARGET_SR) -> AudioResampler:
    """工厂方法：创建重采样器，scipy 不可用时给出明确错误

    Args:
        source_sr: 源采样率
        target_sr: 目标采样率

    Returns:
        AudioResampler 实例

    Raises:
        ImportError: scipy 未安装时，附带安装指引
    """
    # 前置检查 scipy 可用性
    try:
        import scipy.signal  # noqa: F401
    except ImportError:
        raise ImportError(
            "scipy 未安装，音频重采样需要 scipy。"
            "请执行: pip install scipy"
        )
    return AudioResampler(source_sr, target_sr)
```

**v1.0 问题修复：**
- ✅ (M1) scipy 导入失败不再降级到 numpy.interp，而是 `raise ImportError` 并附带安装指引
- ✅ (m1) 补充 `import math`
- ✅ (m2) 增加输入维度校验，非一维自动 flatten + WARNING
- ✅ (m3) 空音频在入口直接返回
- ✅ 新增 `create_resampler()` 工厂方法，统一创建入口
- ✅ `target_sr` 改为实例变量（不再硬编码类常量），支持未来非 16kHz 场景

### 2.3 修改模块：`core/recorder.py`

#### 2.3.1 设备采样率检测

```python
def _detect_device_sample_rate(self, device=None) -> int:
    """检测设备原生采样率

    优先级：
    1. 用户配置 audio.sample_rate（非 auto 时直接使用）
    2. 查询设备默认采样率（sounddevice.query_devices）
    3. 尝试常见采样率（48000, 44100, 32000, 16000）并验证
    4. 回退到 16000（走原来的 PortAudio 重采样路径）

    Returns:
        设备支持的采样率
    """
    import sounddevice as sd

    # 1. 用户显式配置了采样率
    config_sr = getattr(self.config, 'sample_rate', None)
    if config_sr and config_sr != 'auto':
        logger.info("使用用户配置的采样率: %d", int(config_sr))
        return int(config_sr)

    # 确定要查询的设备
    query_device = device
    if query_device is None:
        query_device = sd.default.device[0]  # 显式获取默认输入设备
        if query_device is None:
            query_device = None  # 回退

    # 2. 查询设备默认采样率
    # v4.0 修复 N2: 使用 concurrent.futures 实现跨平台超时
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
    probe_timeout = 2

    def _try_open_and_verify(sr):
        s = sd.InputStream(samplerate=sr, channels=1, dtype='float32', device=query_device)
        s.start()
        actual = s.samplerate
        s.stop()
        s.close()
        return actual

    try:
        device_info = sd.query_devices(query_device)
        default_sr = device_info.get('default_samplerate', 0)
        if default_sr and default_sr > 0:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_try_open_and_verify, int(default_sr))
                actual_sr = future.result(timeout=probe_timeout)

            if actual_sr == int(default_sr):
                logger.info("设备原生采样率: %d (设备: %s, 验证通过)",
                           int(default_sr), device_info.get('name', '未知'))
                return int(default_sr)
            else:
                logger.warning("PortAudio 实际采样率(%d) ≠ 设备默认(%d), 进入探测模式",
                               actual_sr, int(default_sr))
    except Exception as e:
        logger.warning("查询设备采样率失败: %s，进入探测模式", e)

    # 3. 探测：尝试常见采样率，验证 PortAudio 实际接受
    # v4.0 修复 N2: 使用 concurrent.futures 实现跨平台超时
    # （Windows 上 signal.SIGALRM 不可用，只能对主线程生效）
    # 注意：ThreadPoolExecutor 超时后 worker 线程可能仍在后台运行
    # （无法强制中断 PortAudio 底层 C 调用），极端情况下延迟释放资源，风险低
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
    probe_timeout = 2  # 每次探测最多 2 秒

    def _try_open_stream(sr):
        s = sd.InputStream(samplerate=sr, channels=1, dtype='float32', device=query_device)
        s.start()
        actual = s.samplerate
        s.stop()
        s.close()
        return actual

    for probe_sr in [48000, 44100, 32000, 16000]:
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_try_open_stream, probe_sr)
                actual_sr = future.result(timeout=probe_timeout)
        except FuturesTimeoutError:
            logger.warning("探测 %dHz 超时(%.0fs)，跳过", probe_sr, probe_timeout)
            continue
        except Exception:
            continue

            if actual_sr == probe_sr:
                logger.info("探测到设备支持采样率: %d", probe_sr)
                return probe_sr
            else:
                logger.debug("探测 %dHz: 实际=%dHz, 不匹配", probe_sr, actual_sr)
        except Exception:
            continue

    # 4. 回退到 16000
    logger.warning("无法检测设备采样率，回退到 16000 (将使用 PortAudio 自动重采样)")
    return 16000
```

**v1.0 问题修复：**
- ✅ (C1) 探测模式验证：检查 `test_stream.samplerate == probe_sr`，不匹配则跳过
- ✅ (M2) device=None 时显式获取 `sd.default.device[0]`（v1.0 评审 m4）

#### 2.3.2 start() 方法 — 原生采样率采集

```python
def start(self, rt_queue=None):
    import sounddevice as sd

    self._rt_queue = rt_queue
    self._buffer.clear()
    while not self._buffer_queue.empty():
        try:
            self._buffer_queue.get_nowait()
        except queue.Empty:
            break

    # 解析设备参数
    device = self.config.device
    if device is not None:
        try:
            device = int(device)
        except (ValueError, TypeError):
            pass

    # 检测设备原生采样率
    self._device_sr = self._detect_device_sample_rate(device)

    # 创建重采样器
    from core.audio_resampler import create_resampler
    self._resampler = create_resampler(self._device_sr, self.SAMPLE_RATE)

    # 计算 blocksize：保持每 chunk ~10ms 时长
    # 48kHz → 480, 44100 → 441, 16kHz → 160, 8kHz → 80
    # 下限 512 = sounddevice 默认推荐值（PortAudio 文档建议 ≥ 64）
    # 实际效果：
    #   16kHz: max(512, 160) = 512 → 32ms/chunk（保持原行为）
    #   48kHz: max(512, 480) = 512 → 10.7ms/chunk（略低于目标 10ms，可接受）
    #   8kHz:  max(512, 80)  = 512 → 64ms/chunk（低采样率设备，不影响正确性）
    base_blocksize = 512  # sounddevice 默认推荐值，保持原行为
    self._blocksize = max(base_blocksize, int(self._device_sr * 0.01))

    # 以设备原生采样率采集（关键变更）
    try:
        self._stream = sd.InputStream(
            samplerate=self._device_sr,
            channels=1,
            dtype='float32',
            callback=self._audio_callback,
            device=device,
            blocksize=self._blocksize,  # ← v1.0 修复 (M6)
        )
        self._stream.start()
    except Exception as e:
        if device is not None:
            logger.warning("指定设备 '%s' 不可用，回退到默认设备: %s", device, e)
            # 回退后重新检测默认设备采样率
            self._device_sr = self._detect_device_sample_rate(None)
            from core.audio_resampler import create_resampler
            self._resampler = create_resampler(self._device_sr, self.SAMPLE_RATE)
            self._blocksize = max(512, int(self._device_sr * 0.01))

            self._stream = sd.InputStream(
                samplerate=self._device_sr,
                channels=1,
                dtype='float32',
                callback=self._audio_callback,
                blocksize=self._blocksize,
            )
            self._stream.start()
        else:
            raise

    self.is_recording = True

    if self._device_sr != self.SAMPLE_RATE:
        logger.info("录音启动: 原生采样率=%d, 目标=%d, blocksize=%d, 需重采样",
                   self._device_sr, self.SAMPLE_RATE, self._blocksize)
    else:
        logger.info("录音启动: 采样率=%d, blocksize=%d (无需重采样)",
                   self._device_sr, self._blocksize)

    # 诊断日志
    try:
        actual_sr = self._stream.samplerate
        if actual_sr != self._device_sr:
            logger.warning("⚠️ PortAudio 实际采样率(%d) ≠ 请求采样率(%d)，可能存在重采样问题",
                           actual_sr, self._device_sr)
    except Exception:
        pass
```

**v1.0 问题修复：**
- ✅ (M2) 设备回退后重新检测采样率并重建 resampler
- ✅ (M6) blocksize 根据采样率自适应（保持 ~10ms/chunk）
- ✅ 诊断日志：PortAudio 实际采样率与请求不一致时 WARNING

#### 2.3.3 stop() 和 get_resampled_audio()

```python
def stop(self) -> np.ndarray:
    """停止录音，返回原生采样率 PCM 数据"""
    self.is_recording = False
    if self._stream is not None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as e:
            logger.warning("关闭音频流异常: %s", e)
        self._stream = None
    logger.info("录音停止 (采样率=%d)", self._device_sr)
    if self._buffer:
        try:
            audio = np.concatenate(list(self._buffer))
            if audio.ndim > 1:
                audio = audio.flatten()
            return audio
        except Exception as e:
            logger.error("拼接音频失败: %s", e)
    return np.array([], dtype=np.float32)

def get_resampled_audio(self) -> np.ndarray:
    """获取重采样到目标采样率的音频（批量模式用）

    流程：stop() → 原生采样率音频 → resample → 目标采样率音频
    """
    audio = self.stop()
    if self._resampler and len(audio) > 0:
        return self._resampler.resample(audio)
    return audio

@property
def resampler(self):
    """暴露重采样器，供 VAD/流式模式使用"""
    return self._resampler

@property
def device_sample_rate(self) -> int:
    """当前实际采集采样率"""
    return self._device_sr
```

### 2.4 修改模块：`core/engine.py` — CoreEngine

#### 2.4.1 批量模式

```python
def _stop_recording_and_transcribe(self):
    if not self.transition(EngineState.PROCESSING):
        logger.debug("_stop_recording: 状态已变，跳过（去重）")
        return

    logger.info("停止录音，开始识别")

    if self._max_duration_timer:
        self._max_duration_timer.cancel()
        self._max_duration_timer = None

    try:
        # v1.0 修复 (M7): 使用 get_resampled_audio() 获取 16kHz 音频
        audio = self._recorder.get_resampled_audio()
        self._silence_detector.end_detection()
        self._sound_player.play("end")
        self._events.publish(EngineEvent.RECORDING_STOPPED)

        # 基于秒数的最短时长判断（而非样本数）
        min_duration_sec = 0.2  # 200ms
        if len(audio) < int(min_duration_sec * 16000):
            logger.debug("录音时长不足 %.1fs，忽略", min_duration_sec)
            self.transition(EngineState.IDLE)
            return

        # 注意：各 STT 引擎内部仍有 len(audio) < 3200 的兜底判断
        # （stt_engine.py:77, stt_funasr.py:131, stt_qwen3_asr.py:91）
        # 此处为正式判断，引擎内部为安全网，二者不冲突
        self._stt_engine.transcribe_async(audio, self._on_stt_complete)
    except Exception as e:
        logger.error("停止录音失败: %s", e)
        self.transition(EngineState.IDLE)
        self._events.publish(EngineEvent.TRANSCRIBE_ERROR, e)
```

**v1.0 问题修复：**
- ✅ (M7) 样本数阈值 `3200` 改为基于秒数 `0.2s * 16000`

#### 2.4.2 实时模式 — resampler 注入所有分支

```python
def __init__(self, config):
    # ... 现有初始化代码 ...

    # resampler 在 _start_streaming() 中从 recorder 获取后注入
    # （因为 recorder 在 __init__ 尾部才创建）

# engine.py 中 recorder 创建后，实时模式启动时注入 resampler

def _start_streaming(self):
    import queue as queue_mod
    if self.transition(EngineState.STREAMING):
        logger.info("开始实时转写")
        try:
            self._rt_audio_queue = queue_mod.Queue(maxsize=self._rt_max_queue_size)
            self._recorder.start(self._rt_audio_queue)

            # 获取 resampler 注入到所有实时转写器
            resampler = self._recorder.resampler

            if self._streaming_mode:
                # 流式模式（FunASR streaming + StreamingTranscriber）
                self._stream_transcriber.start(
                    self._rt_audio_queue, self._stt_engine,
                    self._on_realtime_segment,
                    resampler=resampler,  # ← v1.0 修复 (C2)
                )
            else:
                # VAD 分段模式（5 个引擎分支共用 VADSegmentTranscriber）
                self._stream_transcriber.start(
                    self._rt_audio_queue,
                    resampler=resampler,  # ← v1.0 修复 (C3)
                )

            self._sound_player.play("start")
            self._events.publish(EngineEvent.RECORDING_STARTED)
        except Exception as e:
            logger.error("启动实时转写失败: %s", e)
            self.transition(EngineState.IDLE)
```

**v1.0 问题修复：**
- ✅ (C2) StreamingTranscriber 路径：通过 `start()` 新增 `resampler` 参数注入
- ✅ (C3) VADSegmentTranscriber 路径：通过 `start()` 新增 `resampler` 参数注入
- ✅ 所有 6 个引擎分支（funasr-streaming / funasr / mlx-whisper / qwen3-asr / faster-whisper / auto）统一注入

### 2.5 修改模块：`core/vad_segment_transcriber.py`

**v1.0 关键遗漏修复：3 处硬编码 `SAMPLE_RATE = 16000` 全部改为动态获取**

```python
class VADSegmentTranscriber:
    """VAD 分段转写引擎（降级备用）"""

    def __init__(self, config, stt_engine, on_segment_transcribed: Callable):
        self._config = config
        self._stt_engine = stt_engine
        self._on_segment = on_segment_transcribed
        self._running = False
        self._thread = None
        self._inject_thread = None
        self._inject_queue = queue.Queue(maxsize=10)

        # VAD
        self._vad = None
        self._vad_mode = 'rms'

        # 音频缓冲
        self._speech_buffer = []
        self._silence_start = None
        self._speech_duration = 0.0

        # 采样率相关（v2.0 新增）
        self._source_sr = 16000     # 音频采集采样率（启动时从 resampler 获取）
        self._resampler = None      # AudioResampler 实例
        self._vad_resampler = None  # VAD 专用 resampler（仅非常见采样率时使用）

        # 统计
        self._segments_count = 0

    def start(self, audio_queue: queue.Queue, resampler=None):
        """启动实时转写

        Args:
            audio_queue: Recorder 的实时模式专用队列
            resampler: AudioResampler 实例（可选，传入时自动重采样）
        """
        self._load_vad()
        self._running = True
        self._audio_queue = audio_queue
        self._speech_buffer = []
        self._silence_start = None
        self._speech_duration = 0.0
        self._segments_count = 0

        # 采样率设置（v2.0 核心变更）
        if resampler is not None:
            self._resampler = resampler
            self._source_sr = resampler.source_sr
            logger.info("VADSegmentTranscriber: 源采样率=%d, 目标=%d",
                       self._source_sr, resampler.target_sr)

            # 如果源采样率不在 webrtcvad 支持列表中，创建 VAD 专用 resampler
            if self._vad_mode == 'webrtcvad' and self._source_sr not in (8000, 16000, 32000, 48000):
                from core.audio_resampler import create_resampler
                # 选择最近的 webrtcvad 支持采样率，避免非整数比重采样导致相位失真
                # 例如 44100→48000 是整数比（147:160），而 44100→16000 是非整数比（441:160）
                # webrtcvad 支持的采样率: 8000, 16000, 32000, 48000
                supported_rates = [8000, 16000, 32000, 48000]
                vad_target = min(supported_rates, key=lambda r: abs(r - self._source_sr))
                self._vad_resampler = create_resampler(self._source_sr, vad_target)
                logger.info("VAD: 源采样率 %d 不在 webrtcvad 支持列表，选择最近的 %d 进行重采样",
                           self._source_sr, vad_target)
        else:
            self._source_sr = 16000  # 无 resampler 时保持默认

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._inject_thread = threading.Thread(target=self._inject_worker, daemon=True)
        self._inject_thread.start()
        logger.info("实时转写引擎已启动 (source_sr=%d)", self._source_sr)

    def _run_inner(self):
        """实时转写主循环逻辑"""
        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # VAD 判断
            is_speech = self._vad_detect(chunk)

            if is_speech:
                self._speech_buffer.append(chunk)
                # 防止 buffer 无限增长（异常情况：VAD 一直返回 True 但无静音触发 flush）
                # 上限：30 秒 × 源采样率（48kHz 下约 1.44M 样本，~5.5MB）
                max_buffer_sec = 30
                max_buffer_samples = int(max_buffer_sec * self._source_sr)
                total_samples = sum(len(c) for c in self._speech_buffer)
                if total_samples > max_buffer_samples:
                    logger.warning("VAD buffer 超过 %.0f 秒上限，强制 flush", max_buffer_sec)
                    self._flush_speech_buffer()
                # v1.0 修复 (C3): 使用 self._source_sr 而非硬编码 SAMPLE_RATE
                self._speech_duration += len(chunk) / self._source_sr
                self._silence_start = None

                if self._speech_duration >= self._config.max_segment_duration:
                    logger.debug("语音段达 %.1fs，强制切分", self._speech_duration)
                    self._flush_speech_buffer()
            else:
                if self._speech_buffer:
                    if self._silence_start is None:
                        self._silence_start = time.monotonic()
                    else:
                        silence_duration = time.monotonic() - self._silence_start
                        if silence_duration >= self._config.segment_pause_threshold:
                            self._flush_speech_buffer()

    def _vad_detect(self, audio_chunk: np.ndarray) -> bool:
        """判断是否为语音"""
        if self._vad_mode == 'rms':
            return self._vad_detect_rms(audio_chunk)

        # webrtcvad 模式
        # 确定用于 VAD 的音频和采样率
        if self._vad_resampler is not None:
            # 非常见采样率：先 resample 到 16kHz
            vad_chunk = self._vad_resampler.resample(audio_chunk)
            vad_sr = 16000
        else:
            vad_chunk = audio_chunk
            vad_sr = self._source_sr  # ← v1.0 修复: 使用动态采样率

        # v1.0 修复 (C3): 使用 vad_sr 而非硬编码 SAMPLE_RATE
        frame_length = int(self._config.vad_window_ms * vad_sr / 1000)
        if len(vad_chunk) < frame_length:
            return False
        frame = (vad_chunk[-frame_length:] * 32767).astype(np.int16).tobytes()
        try:
            return self._vad.is_speech(frame, vad_sr)
        except Exception:
            return False

    def _flush_speech_buffer(self):
        """将当前语音段送入 STT 转写并输出"""
        if not self._speech_buffer:
            return

        audio = np.concatenate(self._speech_buffer)
        self._speech_buffer = []
        self._speech_duration = 0.0
        self._silence_start = None

        # 重采样到目标采样率（v2.0 核心变更）
        if self._resampler is not None and len(audio) > 0:
            audio = self._resampler.resample(audio)

        # v1.0 修复 (m6): 使用 resampler.target_sr 而非硬编码 16000
        target_sr = self._resampler.target_sr if self._resampler else 16000
        min_samples = int(self._config.min_segment_duration * target_sr)
        if len(audio) < min_samples:
            return

        try:
            text = self._stt_engine.transcribe_sync(audio)
            if text and text.strip():
                self._segments_count += 1
                try:
                    self._inject_queue.put_nowait(text)
                except queue.Full:
                    logger.warning("注入队列已满，丢弃段落")
        except Exception as e:
            logger.error("实时转写失败: %s", e)
```

**v1.0 问题修复清单：**
- ✅ (C3) `_run_inner`: `len(chunk) / SAMPLE_RATE` → `len(chunk) / self._source_sr`
- ✅ (C3) `_vad_detect`: `frame_length` 计算使用 `vad_sr`（动态）而非 `SAMPLE_RATE`
- ✅ (C3) `_flush_speech_buffer`: `min_samples` 使用 `resampler.target_sr`
- ✅ (M4) 独立的 VAD resampler 在 `start()` 中初始化
- ✅ (m6) min_samples 使用 `target_sr` 而非硬编码 16000

### 2.6 修改模块：`core/streaming_transcriber.py`

**v1.0 最大遗漏：流式模式的完整适配方案**

**核心挑战**：StreamingTranscriber 是 chunk-by-chunk 处理模式，每个 chunk 累积到 `chunk_samples`（9600 = 600ms @16kHz）后调用 `transcribe_chunk`。如果输入是 48kHz，9600 样本仅代表 200ms，远不够一个有效 chunk。

**解决方案**：根据源采样率动态计算 chunk 需要的样本数。

```python
class StreamingTranscriber:
    """流式转写器（v2.0: 采样率感知）"""

    def __init__(self, config):
        self._config = config
        self._running = False
        self._thread = None
        self._inject_thread = None
        self._inject_queue = queue.Queue(maxsize=20)
        self._buffer = []
        self._buffer_lock = threading.Lock()

        # 外部依赖（由 start() 设置）
        self._audio_queue = None
        self._engine = None
        self._on_segment = None
        self._chunk_samples = 9600  # 默认值，启动时根据引擎和源采样率重算

        # 采样率相关（v2.0 新增）
        self._source_sr = 16000
        self._resampler = None

        # 统计
        self._chunks_count = 0
        self._texts_count = 0

    def start(self, audio_queue, engine, on_segment, resampler=None):
        """启动流式转写

        Args:
            audio_queue: AudioRecorder 的输出队列
            engine: 流式引擎实例
            on_segment: 文本输出回调
            resampler: AudioResampler 实例（v2.0 新增）
        """
        self._audio_queue = audio_queue
        self._engine = engine
        self._on_segment = on_segment
        self._resampler = resampler

        # 引擎期望的 chunk 时长（ms）
        chunk_ms = 600  # FunASRStreamingEngine.CHUNK_MS
        if hasattr(engine, 'CHUNK_MS'):
            chunk_ms = engine.CHUNK_MS

        # v2.0 核心变更：chunk_samples 根据源采样率动态计算
        if resampler is not None:
            self._source_sr = resampler.source_sr
            # 引擎期望的是 600ms @16kHz = 9600 样本
            # 但我们累积的是源采样率的样本，需要累积到相同时长
            self._chunk_samples = int(chunk_ms * self._source_sr / 1000)
            logger.info("StreamingTranscriber: 源采样率=%d, chunk=%d样本(%.0fms), 需resample后送入引擎",
                       self._source_sr, self._chunk_samples, chunk_ms)
        else:
            self._source_sr = 16000
            self._chunk_samples = int(chunk_ms * 16000 / 1000)
            logger.info("StreamingTranscriber: 默认16kHz, chunk=%d样本", self._chunk_samples)

        with self._buffer_lock:
            self._buffer = []

        self._chunks_count = 0
        self._texts_count = 0
        self._running = True

        if hasattr(engine, 'reset'):
            engine.reset()

        self._thread = threading.Thread(target=self._run, daemon=True, name="StreamingTranscriber")
        self._thread.start()
        self._inject_thread = threading.Thread(target=self._inject_worker, daemon=True, name="InjectWorker")
        self._inject_thread.start()

    def stop(self):
        """停止流式转写，处理剩余音频"""
        self._running = False

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        # 处理剩余 buffer
        with self._buffer_lock:
            if self._buffer and self._engine:
                audio_chunk = np.array(self._buffer)
                self._buffer = []

                # v2.0: resample 剩余音频再送入引擎
                if self._resampler and self._resampler.needs_resample and len(audio_chunk) > 0:
                    audio_chunk = self._resampler.resample(audio_chunk)

                try:
                    text = self._engine.transcribe_chunk(audio_chunk, is_final=True)
                    if text:
                        self._texts_count += 1
                        logger.info("流式转写最终chunk: '%s'", text[:50])
                        self._put_inject(text)
                except Exception as e:
                    logger.error("最终chunk转写失败: %s", e)

        try:
            self._inject_queue.put(None, timeout=2)
        except queue.Full:
            pass

        if self._inject_thread:
            self._inject_thread.join(timeout=3)
            self._inject_thread = None

        logger.info("流式转写器已停止 (chunks=%d, texts=%d)", self._chunks_count, self._texts_count)

    # 最大 buffer 累积时长（秒），超出时强制 flush 防止内存无限增长
    # 48kHz 下 30 秒 ≈ 1.44M 样本 ≈ 5.5MB，可接受
    _MAX_BUFFER_SEC = 30

    def _flush_chunk(self):
        """处理一个 chunk（调用前需持有 _buffer_lock）"""
        if not self._engine:
            return

        audio_chunk = np.array(self._buffer[:self._chunk_samples])
        self._buffer = self._buffer[self._chunk_samples:]
        self._chunks_count += 1

        # v2.0 核心变更：resample 到 16kHz 后再送入引擎
        if self._resampler and self._resampler.needs_resample and len(audio_chunk) > 0:
            audio_chunk = self._resampler.resample(audio_chunk)

        # v3.0: 对齐 resample 后的 chunk 长度到引擎期望值
        # 非整数比重采样（如 44100→16000）可能产生 ±1 样本误差
        if self._resampler and self._resampler.needs_resample and hasattr(self._engine, 'CHUNK_SAMPLES'):
            expected = self._engine.CHUNK_SAMPLES
            if abs(len(audio_chunk) - expected) <= 2:  # 容差 ±2
                if len(audio_chunk) > expected:
                    audio_chunk = audio_chunk[:expected]
                elif len(audio_chunk) < expected:
                    audio_chunk = np.pad(audio_chunk, (0, expected - len(audio_chunk)))

        try:
            text = self._engine.transcribe_chunk(audio_chunk, is_final=False)
            if text:
                self._texts_count += 1
                logger.debug("流式chunk %d: '%s'", self._chunks_count, text[:30])
                self._put_inject(text)
        except Exception as e:
            logger.error("chunk转写失败: %s", e)

    def _inject_worker(self):
        """独立注入线程

        使用 sentinel (None) 模式退出，避免 1 秒 timeout 延迟。
        v3.0 修复：增加 `if text is None: break` 防止 None 传入回调。
        """
        while True:
            try:
                text = self._inject_queue.get(timeout=1)
            except queue.Empty:
                if not self._running:
                    break
                continue

            if text is None:  # sentinel，停止
                break

            try:
                if self._on_segment:
                    self._on_segment(text)
            except Exception as e:
                logger.error("注入回调异常: %s", e)

    def _put_inject(self, text: str):
        """放入注入队列（非阻塞，满时丢弃最旧的）"""
        try:
            if self._inject_queue.full():
                try:
                    self._inject_queue.get_nowait()
                    logger.warning("注入队列满，丢弃旧文本")
                except queue.Empty:
                    pass
            self._inject_queue.put_nowait(text)
        except Exception as e:
            logger.warning("注入队列异常: %s", e)

    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running
```

**v1.0 问题修复清单：**
- ✅ (C4) `chunk_samples` 根据源采样率动态计算：`int(600ms * source_sr / 1000)`
  - 16kHz → 9600 样本（不变）
  - 48kHz → 28800 样本（代表 600ms 时长，与 16kHz 下 9600 样本等价）
- ✅ (C2) `start()` 新增 `resampler` 参数
- ✅ `_flush_chunk()` 中 resample 到 16kHz 后再传给引擎
- ✅ `stop()` 中处理剩余 buffer 时也 resample
- ✅ (M5) 不再依赖引擎的 `get_chunk_samples()`（改为根据 chunk_ms + source_sr 计算）

### 2.7 修改模块：`core/silence_detector.py`

**无需修改。** RMS 能量检测基于 float32 归一化音频，采样率无关。

**关于 chunk 时长变化（M3 补充说明）：**

修改前每个 chunk 约 512 样本 / 16kHz = 32ms。修改后如果 blocksize=512 且 48kHz，每个 chunk 约 512/48000 = 10.7ms。SilenceDetector 的轮询间隔 0.5s 远大于此，不会增加 CPU 开销。RMS 计算本身是 O(n) 且 n 变小，实际上 CPU 开销不变或略降。

### 2.8 修改模块：`config.py`

```python
@dataclass
class AudioConfig:
    max_duration: int = 120
    silence_timeout: int = 8
    silence_threshold: float = 0.01
    silence_check_interval: float = 0.5
    device: Optional[str] = None
    sample_rate: Optional[int] = None  # 新增：None=auto | 16000 | 48000

    def __post_init__(self):
        """处理 YAML 中可能出现的字符串 'auto'"""
        if isinstance(self.sample_rate, str):
            if self.sample_rate.lower() in ('auto', 'none', ''):
                self.sample_rate = None
            else:
                try:
                    self.sample_rate = int(self.sample_rate)
                except ValueError:
                    logger.warning("无效的 sample_rate 配置: '%s', 回退到 auto", self.sample_rate)
                    self.sample_rate = None
```

**版本迁移：**
```python
def _migrate_v6_to_v7(raw):
    """v6 → v7: 新增 audio.sample_rate"""
    raw["config_version"] = 7
    audio = raw.get("audio", {})
    if "sample_rate" not in audio:
        audio["sample_rate"] = None  # None = auto，自动检测设备原生采样率
    raw["audio"] = audio
    return raw
```

**v1.0 问题修复：**
- ✅ (m7) `__post_init__` 处理字符串 `"auto"` → `None` 的转换

---

## 三、数据流变更

### 3.1 批量模式

```
修改前：
  录音(16kHz via PortAudio重采样) → silence_detector(RMS) → stop() → 长度判断(3200) → ASR(16kHz)

修改后：
  录音(原生48kHz) → silence_detector(RMS) → get_resampled_audio() → resample(48→16) → 时长判断(0.2s) → ASR(16kHz)
```

### 3.2 VAD 分段模式

```
修改前：
  录音(16kHz) → rt_queue → VAD(16kHz帧) → 累积(buffer, 按16kHz计时长) → flush → ASR(16kHz)

修改后：
  录音(原生48kHz) → rt_queue → VAD(48kHz帧或VAD resample到16kHz) → 累积(buffer, 按48kHz计时长) → flush → resample(48→16) → ASR(16kHz)
```

### 3.3 流式模式（FunASR Streaming）

```
修改前：
  录音(16kHz) → rt_queue → 累积9600样本(600ms) → engine.transcribe_chunk(9600样本@16kHz)

修改后：
  录音(原生48kHz) → rt_queue → 累积28800样本(600ms@48kHz) → resample(48→16, 28800→9600) → engine.transcribe_chunk(9600样本@16kHz)
```

### 3.4 关键不变量

| 不变量 | 保证机制 |
|--------|---------|
| ASR 模型始终接收 16kHz | ✅ 所有路径的 resampler 输出保证 |
| SilenceDetector RMS 阈值不受影响 | ✅ float32 归一化，采样率无关 |
| webrtcvad 帧格式正确 | ✅ 使用 `_vad_resampler` 确保输入在 (8000/16000/32000/48000) 中 |
| 流式引擎收到正确 chunk 长度 | ✅ resample 在 flush 前执行，9600 样本 @16kHz |
| 实时延迟不增加 | ✅ resample 在 flush 时一次性做（~5ms），不在每 chunk 做 |

### 3.5 采样率变更对各组件的影响总结

| 组件 | 采样率变化影响 | 应对措施 |
|------|--------------|---------|
| SilenceDetector | 无影响（RMS 浮点归一化） | 不修改 |
| webrtcvad 帧长 | 受影响（帧长=窗口ms×采样率） | 动态 `vad_sr` + `_vad_resampler` |
| VAD 语音段时长 | 受影响（样本数/采样率=秒数） | `self._source_sr` 替代硬编码 |
| min_samples | 受影响（不同采样率下样本数不同） | 使用 `target_sr` 计算 |
| StreamingTranscriber chunk | 受影响（9600样本含义变化） | 动态 `chunk_samples` |
| ASR 引擎 | 无影响（始终接收 resample 后的 16kHz） | 不修改各引擎 |
| engine.py 长度判断 | 受影响（`len(audio) < 3200`） | 改为秒数判断 |

---

## 四、异常处理与边界条件

| 场景 | 处理策略 |
|------|---------|
| 设备采样率检测失败 | 回退到 16000，走原来的 PortAudio 重采样路径（兼容降级） |
| scipy 未安装 | `create_resampler()` 抛出 `ImportError`，附带 `pip install scipy` 提示 |
| 设备原生采样率就是 16000 | `needs_resample=False`，fast path 零拷贝直接返回 |
| 设备中途切换采样率 | 每次录音启动重新检测；运行中切换不处理（需重启工具） |
| 音频 chunk 过短（<32 samples） | 跳过 resample，直接返回原数组 |
| 空音频（len=0） | resample 入口直接返回空数组 |
| 非常见采样率（44100） | `resample_poly` 正确处理，GCD 计算自动优化；webrtcvad 走 `_vad_resampler` |
| PortAudio 实际采样率 ≠ 请求 | 启动时 WARNING 日志，提示用户检查设备设置 |
| 非一维音频输入 | 自动 flatten + WARNING 日志 |

**不再使用 numpy.interp 作为 fallback**（v1.0 评审反馈：线性插值产生严重混叠，音频质量不如出故障的 PortAudio）

---

## 五、性能影响评估

| 指标 | 修改前 | 修改后 | 变化 |
|------|--------|--------|------|
| CPU 采集开销 | PortAudio 重采样（不可控质量） | 原生采集（无重采样） | ↓ 降低 |
| CPU resample 开销 | 0 | scipy resample_poly（FIR） | ↑ 增加但极小（<5ms/chunk） |
| 内存占用 | 16kHz 缓冲 | 48kHz 缓冲（3x） | ↑ ~23MB max（120s×48k×4B，可接受） |
| 批量模式延迟 | 无额外延迟 | resample 整段音频几毫秒 | ≈ 无感知 |
| VAD 分段延迟 | 无额外延迟 | flush 时 resample 几毫秒 | ≈ 无感知 |
| 流式模式延迟 | 无额外延迟 | flush 时 resample 几毫秒 | ≈ 无感知 |

---

## 六、测试计划

### 6.1 单元测试

| 测试 | 描述 | 预期 |
|------|------|------|
| `test_resampler_identity` | 16000→16000 | 输入=输出，`needs_resample=False` |
| `test_resampler_downsample_48_to_16` | 48kHz→16kHz | 输出长度≈输入/3，频率正确 |
| `test_resampler_downsample_44100_to_16` | 44100→16kHz | 输出长度正确 |
| `test_resampler_upsample_8_to_16` | 8kHz→16kHz | 输出长度≈输入×2 |
| `test_resampler_empty_input` | 空数组 | 空数组，无异常 |
| `test_resampler_short_input` | 极短音频（<32 samples） | 无异常 |
| `test_resampler_ndim_input` | 二维数组 | 自动 flatten + WARNING |
| `test_resampler_invalid_sr` | 负数采样率 | `ValueError` |
| `test_create_resampler_no_scipy` | mock scipy 不可用 | `ImportError` 含安装提示 |
| `test_detect_device_sr_explicit` | config 指定 48000 | 返回 48000 |
| `test_detect_device_sr_auto_string` | config "auto" | 走自动检测逻辑 |
| `test_detect_device_sr_fallback` | 查询+探测全失败 | 回退 16000 |
| `test_vad_duration_calc_48k` | 48kHz 下 48000 样本 | 时长=1.0s |
| `test_streaming_chunk_samples_48k` | 48kHz 源 | chunk_samples=28800 |
| `test_streaming_chunk_samples_16k` | 16kHz 源 | chunk_samples=9600 |
| `test_streaming_resample_before_engine` | 48kHz chunk | resample 后 9600 样本送入引擎 |

### 6.2 集成测试

| 测试 | 描述 |
|------|------|
| 端到端批量模式 48kHz | 生成 48kHz 测试音频，验证识别正确性 |
| 端到端 VAD 分段模式 48kHz | 生成 48kHz 多段音频，验证分段+识别 |
| 端到端流式模式 48kHz | 生成 48kHz 连续音频，验证 chunk resample + 识别 |
| 设备采样率切换 | 模拟 config 切换 auto/16000/48000 |

### 6.3 手动验证

| 场景 | 验证点 |
|------|--------|
| 大疆无线麦克风（48kHz） | 原生采集 + resample，识别正常 |
| 内置麦克风（16kHz） | 直通，识别正常 |
| USB 断连重连 | 设备重新检测，resample 正确 |
| config `sample_rate: auto` | 自动检测正确 |
| config `sample_rate: 48000` | 强制 48kHz |
| config `sample_rate: 16000` | 强制 16kHz（等同修改前行为） |
| 长时间运行 30min | 无精度退化 |

---

## 七、文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `core/audio_resampler.py` | **新增** | AudioResampler + create_resampler 工厂方法 |
| `core/recorder.py` | 修改 | 设备采样率检测 + 原生采集 + blocksize 自适应 + resampler 暴露 |
| `core/engine.py` | 修改 | 批量模式 `get_resampled_audio()` + 所有实时分支注入 resampler + 秒数判断 |
| `core/vad_segment_transcriber.py` | 修改 | 3 处 `SAMPLE_RATE` 改为 `self._source_sr` + flush 时 resample + VAD resampler |
| `core/streaming_transcriber.py` | 修改 | `chunk_samples` 动态计算 + flush 时 resample + resampler 参数 |
| `core/silence_detector.py` | **不修改** | RMS 检测采样率无关 |
| `core/stt_engine.py` | **不修改** | 引擎始终接收 16kHz |
| `core/stt_funasr.py` | **不修改** | 引擎始终接收 16kHz |
| `core/stt_qwen3_asr.py` | **不修改** | 引擎始终接收 16kHz |
| `core/stt_mlx_whisper.py` | **不修改** | 引擎始终接收 16kHz |
| `core/stt_funasr_streaming.py` | **不修改** | 引擎始终接收 16kHz（resample 在 StreamingTranscriber 中完成） |
| `config.py` | 修改 | `AudioConfig.sample_rate` + `__post_init__` + `CURRENT_CONFIG_VERSION=7` + `CONFIG_MIGRATIONS` 注册 + v6→v7 迁移 |
| `config.yaml` | 修改 | 新增 `sample_rate: null`（auto） |
| `tests/test_audio_resampler.py` | **新增** | AudioResampler 单元测试 |
| `tests/test_recorder_sr.py` | **新增** | 采样率检测 + blocksize 自适应测试 |
| `requirements.txt` | 修改 | 显式添加 `scipy>=1.7` （确保 resample_poly 可用） |
| `vad_segment_transcriber.py` | 删除模块级常量 | `SAMPLE_RATE = 16000` 不再使用 |
| `streaming_transcriber.py` | 删除模块级常量 | `SAMPLE_RATE = 16000` 不再使用 |

---

## 八、回滚方案

如果修改后出现新问题：

1. **配置回滚（最快）**：设置 `audio.sample_rate: 16000`，强制走固定 16kHz 路径（等同修改前行为）
2. **代码回滚**：`recorder.py` 中 `_device_sr = self._detect_device_sample_rate(device)` 改为 `_device_sr = 16000`
3. **完全回滚**：`git revert` 对应 commit

---

## 九、隐含 Bug 修复（随本次设计一并修复）

| Bug | 文件 | 描述 | 修复方式 |
|-----|------|------|--------|
| 线程泄漏 | `vad_segment_transcriber.py` | `stop()` 缺少 `_inject_queue.put(None)` sentinel，`_inject_thread` 无限阻塞 | `stop()` 增加 sentinel 注入 |
| 缺少 inject_thread | `vad_segment_transcriber.py` | `start()` 未启动 `_inject_thread` | `start()` 补充启动 |
| sentinel None 泄漏 | `streaming_transcriber.py` | `_inject_worker` 未检查 `text is None`，None 传入回调 | `_inject_worker` 增加 `if text is None: break` |

---

## 十、v1.0 评审问题修复追踪

| ID | 级别 | 问题 | v2.0 状态 |
|----|------|------|----------|
| C1 | Critical | recorder.py 探测模式验证不充分 | ✅ 检查 `actual_sr == probe_sr` |
| C2 | Critical | engine.py 未展示 StreamingTranscriber 路径 | ✅ 完整设计 2.6 节 |
| C3 | Critical | VADSegmentTranscriber 3 处硬编码 SAMPLE_RATE | ✅ 全部改为 `self._source_sr` / `target_sr` |
| C4 | Critical | StreamingTranscriber chunk 累积未适配 | ✅ 动态 `chunk_samples` + resample |
| M1 | Major | scipy fallback 未实现 | ✅ 改为 `ImportError` 提示 |
| M2 | Major | 设备回退后未重新检测 | ✅ 回退时重新检测+重建 resampler |
| M3 | Major | chunk 时长变化未分析 | ✅ 3.7 节补充分析 |
| M4 | Major | 缺少独立 VAD resampler | ✅ `start()` 中初始化 |
| M5 | Major | CHUNK_SAMPLES 硬编码 | ✅ 动态计算 `int(chunk_ms * source_sr / 1000)` |
| M6 | Major | blocksize 未自适应 | ✅ `int(device_sr * 0.01)` |
| M7 | Major | 样本数阈值应改为秒数 | ✅ `0.2s * 16000` |
| m1 | Minor | math.gcd 未导入 | ✅ 补充 import |
| m2 | Minor | 缺少输入维度校验 | ✅ flatten + WARNING |
| m3 | Minor | 缺少空音频检查 | ✅ 入口返回 |
| m4 | Minor | device=None query_devices 行为 | ✅ 显式 `sd.default.device[0]` |
| m5 | Minor | resampler 传入方式不一致 | ✅ 统一通过 `start()` 参数 |
| m6 | Minor | 硬编码 16000 应改用 target_sr | ✅ |
| m7 | Minor | "auto" 字符串转换缺失 | ✅ `__post_init__` |

---

## 十一、v2.0 评审新发现问题修复追踪

| ID | 级别 | 问题 | v3.0 状态 |
|----|------|------|----------|
| 新 M1 | Major | `blocksize` 下限 512 对低采样率设备影响未说明 | ✅ §2.3.2 补充详细注释 |
| 新 M2 | Major | 批量模式最短时长双重判断 | ✅ §2.4.1 注释说明关系 |
| 新 M3 | Major | sentinel 修复未标注 bug fix | ✅ §九 新增 bug 修复追踪 |
| 新 m1 | Minor | scipy 导入缓存 | ✅ `create_resampler` 中 `_scipy_signal = scipy.signal` |
| 新 m9 | Minor | `_inject_worker` sentinel 处理 | ✅ §2.6 完整展示 `_inject_worker` 代码 |
| 新 m12 | Minor | `CURRENT_CONFIG_VERSION` 未更新 | ✅ 变更清单标注 |
| 新 m15 | Minor | `streaming_transcriber.py` 模块级 `SAMPLE_RATE` | ✅ 变更清单标注删除 |
| 新 m16 | Minor | `vad_segment_transcriber.py` 模块级 `SAMPLE_RATE` | ✅ 变更清单标注删除 |

---

## 十二、v3.0 评审（Kimi K2.5）新发现问题修复追踪

| ID | 级别 | 问题 | v4.0 状态 |
|----|------|------|----------|
| K1 | Major | webrtcvad 非整数比重采样相位失真（44100→16000） | ✅ `_vad_resampler` 改为选择最近的支持采样率（`min(supported, key=abs_diff)`） |
| K2 | Major | StreamingTranscriber `_buffer` 无上限，高采样率下可能内存溢出 | ✅ 新增 `_MAX_BUFFER_SEC = 30` 溢出保护 |
| K3 | Major | 设备探测在 WASAPI 独占模式下可能无限阻塞 | ✅ 探测循环增加超时机制 |
| K4 | Major | resample 后 chunk 长度 ±1 误差（非整数比） | ✅ `_flush_chunk` 中 resample 后对齐到 `CHUNK_SAMPLES`（容差 ±2） |
| K5 | Minor | `AudioResampler` 缺少 `__repr__` 不利调试 | ✅ 新增 `__repr__` |
| K6 | Minor | `requirements.txt` 应显式声明 scipy | ✅ 变更清单修改 |

---

## 十三、v4.0 评审（Kimi K2.5 第 2 轮）新发现问题修复追踪

| ID | 级别 | 问题 | v5.0 状态 |
|----|------|------|----------|
| N1 | P1 | `_scipy_signal` 外部注入私有属性不优雅 | ✅ 改为 `__init__` 延迟初始化 + `resample()` 内缓存 |
| N2 | P0 | `signal` 超时 Windows 兼容性（只对主线程） | ✅ 改为 `concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=2)` |
| N3 | P1 | `FunASRStreamingEngine.CHUNK_MS` 是否存在 | ✅ 已确认存在（`stt_funasr_streaming.py:35`，值为 600） |

---

## 十三、后续优化方向（不在本次范围）

1. **resampy/soxr 升级路径**：实时场景如果 resample_poly 延迟可感知（实际极不可能），可切换 soxr（纯 C，<1ms）
2. **音频质量监控**：定期检测 RMS 能量和信噪比，异常时通知用户
3. **设备热插拔响应**：监听 Windows 设备变更事件，自动重新检测
4. **设备中途采样率切换**：运行时检测采样率变化，自动重建 resampler

---

*文档版本: v5.0*  
*设计者: Saber*  
*评审状态: 待 AI 评审（第 5 轮）*
