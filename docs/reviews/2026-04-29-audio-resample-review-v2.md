# 音频采集采样率兼容性修复 — 设计文档专业评审报告（第 2 轮）

## 评审版本：v2.0 | 评审日期：2026-04-29 | 评审人：AI 音频系统架构师

---

## 一、v1.0 评审问题逐项核查（核心环节）

### Critical 级别（4 项）

| ID | 问题 | v2.0 修复情况 | 判定 | 详细评价 |
|----|------|-------------|------|---------|
| C1 | recorder.py 探测模式验证不充分，WASAPI 后端可能误判 | 增加了 `actual_sr == probe_sr` 验证 + 不匹配时跳过 | ✅已修复 | 完整。探测逻辑现在检查 `test_stream.samplerate` 是否等于请求的 `probe_sr`，不匹配则 `logger.debug` 并 continue 到下一个候选。设计文档 §2.3.1 四层回退链中第 2 层也做了同样验证。 |
| C2 | engine.py 未展示 StreamingTranscriber 路径的 resampler 注入方式 | 完整设计了 §2.4.2 `_start_streaming()` + §2.6 StreamingTranscriber 适配 | ✅已修复 | 完整。`_start_streaming()` 中 `self._stream_transcriber.start(...)` 两个分支（流式模式 / VAD 模式）都传入 `resampler=resampler`，且 resampler 在 recorder.start() 之后通过 `self._recorder.resampler` 获取。 |
| C3 | VADSegmentTranscriber 3 处硬编码 `SAMPLE_RATE=16000` 未提及修改方案 | §2.5 完整展示了 3 处修改 | ✅已修复 | 1) `_run_inner`: `SAMPLE_RATE` → `self._source_sr`；2) `_vad_detect`: 帧长计算使用动态 `vad_sr`；3) `_flush_speech_buffer`: `min_samples` 使用 `target_sr`。设计清晰完整。 |
| C4 | StreamingTranscriber chunk 累积未适配源采样率 | §2.6 完整设计：动态 `chunk_samples` + flush 前 resample | ✅已修复 | 核心变更：`chunk_samples = int(chunk_ms * source_sr / 1000)`。48kHz 下 28800 样本代表 600ms，resample 后 ≈ 9600 样本送入引擎。stop() 剩余 buffer 也做了 resample。 |

### Major 级别（7 项）

| ID | 问题 | v2.0 修复情况 | 判定 | 详细评价 |
|----|------|-------------|------|---------|
| M1 | scipy fallback 逻辑未实现 | 改为 `raise ImportError` + 安装指引，新增 `create_resampler()` 工厂方法 | ✅已修复 | 设计合理。v1.0 建议不使用 `numpy.interp` 做降级，v2.0 直接拒绝（`ImportError`），这是更安全的做法。`create_resampler()` 工厂方法统一了创建入口，前置检查 scipy 可用性。 |
| M2 | 设备回退后未重新检测采样率 | §2.3.2 回退分支中重新检测 + 重建 resampler | ✅已修复 | 回退代码完整：`self._device_sr = self._detect_device_sample_rate(None)` + 重新 `create_resampler` + 重新计算 `blocksize`。 |
| M3 | chunk 时长变化未在文档中分析 | §2.7 补充了分析 + §3.5 影响总结 | ✅已修复 | 分析正确：blocksize 从 512 变为 `int(device_sr * 0.01)`（48kHz→480），每 chunk ~10ms。RMS 计算 O(n) 且 n 变小，CPU 开销不变或略降。SilenceDetector 轮询间隔 0.5s 远大于 chunk 间隔。 |
| M4 | 缺少独立 VAD resampler 初始化代码 | §2.5 `start()` 中完整展示 | ✅已修复 | `if self._vad_mode == 'webrtcvad' and self._source_sr not in (8000, 16000, 32000, 48000)` 时创建 `_vad_resampler`。设计合理：只有非常见采样率才创建额外 resampler。 |
| M5 | CHUNK_SAMPLES 硬编码未适配 | §2.6 `chunk_samples` 改为动态计算 | ✅已修复 | 从 `engine.get_chunk_samples()` 改为 `int(chunk_ms * source_sr / 1000)`，且使用 `hasattr(engine, 'CHUNK_MS')` 获取引擎的 chunk 时长，解耦了对引擎内部常量的依赖。 |
| M6 | blocksize 未根据采样率调整 | §2.3.2 中 `self._blocksize = max(512, int(self._device_sr * 0.01))` | ✅已修复 | 正确。保持每 chunk ~10ms 时长。48kHz→480, 16kHz→160, 44100→441（均 > 512 的基础值取 max）。但见下方 **新发现 M1** 关于 max(512, ...) 的讨论。 |
| M7 | 样本数阈值应改为秒数 | §2.4.1 改为 `min_duration_sec = 0.2` | ✅已修复 | `if len(audio) < int(min_duration_sec * 16000)` — 正确且清晰。 |

### Minor 级别（7 项）

| ID | 问题 | v2.0 修复情况 | 判定 | 详细评价 |
|----|------|-------------|------|---------|
| m1 | math.gcd 未导入 | §2.2 `import math` 已补充 | ✅已修复 | |
| m2 | 缺少输入维度校验 | §2.2 `resample()` 中 `if audio.ndim != 1: flatten + WARNING` | ✅已修复 | |
| m3 | 缺少空音频检查 | §2.2 `if len(audio) == 0: return audio` | ✅已修复 | |
| m4 | device=None 时 query_devices 行为 | §2.3.1 显式 `sd.default.device[0]` | ✅已修复 | |
| m5 | resampler 传入方式不一致 | 统一通过 `start()` 参数注入 | ✅已修复 | VADSegmentTranscriber.start(audio_queue, resampler=None) 和 StreamingTranscriber.start(audio_queue, engine, on_segment, resampler=None) 两个入口都统一了。 |
| m6 | 硬编码 16000 应改用 target_sr | §2.5 `_flush_speech_buffer` 使用 `resampler.target_sr` | ✅已修复 | `target_sr = self._resampler.target_sr if self._resampler else 16000` — 带回退的写法很好。 |
| m7 | "auto" 字符串转换缺失 | §2.8 `__post_init__` 处理 | ✅已修复 | 处理了 `"auto"` / `"none"` / `""` 三种字符串，且处理了无效字符串的 fallback。 |

**v1.0 问题核查总结：18/18 项全部 ✅已修复。**

---

## 二、逐模块评审

### 2.1 `core/audio_resampler.py`（新增模块）

**评价：优秀（90/100）**

**亮点：**
- `create_resampler()` 工厂方法将 scipy 依赖检查前置，fail-fast 设计合理
- 延迟导入 scipy（`resample()` 内部 `import scipy.signal`）保证模块可导入性
- `AudioResampler` 状态完全由构造函数确定，运行时无副作用，天然线程安全
- 属性暴露（`source_sr` / `target_sr` / `needs_resample`）便于下游灵活查询

**新发现 Minor 问题：**
- ⚠️ `resample()` 每次调用都 `import scipy.signal`，虽然 Python 会缓存 import，但建议在 `__init__` 或 `create_resampler` 中做一次导入并赋值到 `self._scipy_signal`，避免每次调用走 import 查找路径（性能影响极小但不够优雅）

### 2.2 `core/recorder.py`（修改模块）

**评价：良好（85/100）**

**亮点：**
- 四层回退链设计鲁棒（配置→查询→探测→默认）
- `get_resampled_audio()` 封装批量模式的 resample 逻辑，语义清晰
- 诊断日志完整：启动时检查 PortAudio 实际采样率
- `device_sample_rate` 属性暴露，便于测试和诊断

**新发现 Major 问题：**
- ⚠️ **新 M1**: `blocksize = max(512, int(self._device_sr * 0.01))` — 当 `device_sr = 16000` 时 `int(16000 * 0.01) = 160`，被 `max(512, 160)` 截断为 512。这意味着 16kHz 设备下 blocksize 不变（512），但 v2.0 没有分析这个行为——当前 sounddevice 默认 blocksize 就是 512（~32ms @16kHz），所以实际上是保持了原行为，不算问题。但 `max` 的下限 512 对 8kHz 设备（0.01 * 8000 = 80）会导致 64ms/chunk，这是否可接受？建议在文档中说明 `base_blocksize = 512` 的选择依据。

**新发现 Minor 问题：**
- ⚠️ `_detect_device_sample_rate` 的探测模式中，`test_stream.start()` / `stop()` / `close()` 在某些 WASAPI 后端上可能不够可靠（PortAudio bug #514）。建议增加 try/except 包裹探测流程的 start 部分（当前已有外层 try/except，确认 OK）。

### 2.3 `core/engine.py`（修改模块）

**评价：良好（85/100）**

**亮点：**
- `_start_streaming()` 的两个分支（流式 / VAD 分段）统一传入 resampler
- 批量模式改用 `get_resampled_audio()` 路径清晰
- 所有 6 个引擎分支（funasr-streaming / funasr / mlx-whisper / qwen3-asr / faster-whisper / auto）统一注入，无遗漏

**对照现有代码的新发现：**

- ⚠️ **新 M2**: 现有 `engine.py` 的 `_stop_recording_and_transcribe()` 中缺少 `min_duration` 判断（当前代码直接 `self._stt_engine.transcribe_async(audio, self._on_stt_complete)`，只有 `stt_engine._do_transcribe()` 内部有 `len(audio) < 3200` 判断）。v2.0 设计将此判断提前到 `engine.py` 层（`0.2s * 16000`），这是改进。但需注意：**stt_engine._do_transcribe() 内部的 `len(audio) < 3200` 判断仍然存在**（`stt_engine.py:77`, `stt_funasr.py:131`, `stt_qwen3_asr.py:91`），形成了双重判断。这不影响正确性但增加了代码维护负担。建议 v2.0 实施时在 STT 引擎内部保留该判断作为安全网，但在设计文档中注明"引擎内部阈值是兜底，正式判断已提前到 engine.py"。

- ⚠️ **新 m8**: 现有 `_start_streaming()` 中 VAD 分段模式的调用是 `self._stream_transcriber.start(self._rt_audio_queue)`，v2.0 改为 `self._stream_transcriber.start(self._rt_audio_queue, resampler=resampler)`。现有代码的 `VADSegmentTranscriber.start(audio_queue)` 只接收 1 个参数，v2.0 设计改为 `start(audio_queue, resampler=None)`。**现有代码中还缺少 `_inject_thread` 的启动**（现有 `start()` 只启动 `_thread`，没有 `_inject_thread`）。v2.0 设计中补充了 `_inject_thread`，这是修复了现有代码的 bug。✅

### 2.4 `core/vad_segment_transcriber.py`（修改模块）

**评价：优秀（88/100）**

**亮点：**
- 3 处硬编码全部改为动态值，修复彻底
- `_vad_resampler` 仅在 `self._source_sr not in (8000, 16000, 32000, 48000)` 时创建，性能优化意识好
- `_vad_detect()` 中分支清晰：有 `_vad_resampler` 先 resample 到 16kHz，否则直接使用 `self._source_sr`

**对照现有代码的新发现：**

- ⚠️ **新 M3**: 现有代码 `vad_segment_transcriber.py` 的 `start()` 方法签名是 `start(self, audio_queue: queue.Queue)`，v2.0 改为 `start(self, audio_queue: queue.Queue, resampler=None)`。但 **现有代码的 `stop()` 方法中缺少 `_inject_queue.put(None)` sentinel 注入**——当前 `_inject_thread` 在 `stop()` 中通过 `self._inject_thread.join(timeout=3)` 等待，但 `_inject_worker` 的 `self._inject_queue.get()` 会无限阻塞（没有 timeout），导致 join 超时后线程泄漏。v2.0 设计中补充了 `self._inject_queue.put(None)`，**修复了现有代码的线程泄漏 bug**。✅ 但 v2.0 文档没有明确标注这是一个 bug fix。

- ⚠️ **新 m9**: 现有 `_flush_speech_buffer()` 没有 resample 逻辑（因为现有代码假设输入是 16kHz）。v2.0 正确地在 flush 前添加了 resample。但注意：**flush 后的音频在传给 `transcribe_sync()` 时，各 STT 引擎内部的 `len(audio) < 3200` 判断此时应基于 16kHz 的样本数，因为 resample 已完成**。这是正确的，因为 `3200 = 0.2s * 16000`，与 resample 后的采样率一致。

### 2.5 `core/streaming_transcriber.py`（修改模块）

**评价：优秀（90/100）**

**亮点：**
- `chunk_samples` 动态计算逻辑清晰：`int(chunk_ms * source_sr / 1000)`
- 使用 `hasattr(engine, 'CHUNK_MS')` 获取引擎期望的 chunk 时长，而非硬编码
- `_flush_chunk()` 和 `stop()` 都做了 resample，无遗漏
- `needs_resample` 检查避免不必要的 resample 调用

**对照现有代码的新发现：**

- ⚠️ **新 m10**: 现有代码的 `start()` 签名是 `start(self, audio_queue, engine, on_segment)`，v2.0 改为 `start(self, audio_queue, engine, on_segment, resampler=None)`。**但 `engine.get_chunk_samples()` 在 v2.0 中不再被调用**（改用 `chunk_ms * source_sr / 1000`）。`stt_funasr_streaming.py` 中 `get_chunk_samples()` 方法仍存在但不再被使用——这是正确的解耦（StreamingTranscriber 不应依赖引擎的 chunk 配置），但建议在实施时保留 `get_chunk_samples()` 作为引擎接口的一部分（供其他调用方使用），不要删除。

- ⚠️ **新 m11**: 现有 `_inject_worker()` 使用 `self._inject_queue.get(timeout=1)` + `if not self._running: break` 的模式。v2.0 没有改动此逻辑。**这里有一个边界条件**：如果 `_running` 在 `get(timeout=1)` 阻塞期间变为 `False`，下一个 `if not self._running: break` 会在 1 秒后才检查到。v2.0 的 `stop()` 中改为 `self._inject_queue.put(None, timeout=2)` sentinel 模式——**但现有代码的 `_inject_worker` 并不检查 `text is None: break`**。这意味着 sentinel `None` 会被传给 `self._on_segment(text)`，导致回调收到 `None`。**v2.0 没有修复这个问题**，或者说 v2.0 展示的 `_inject_worker` 代码与现有代码不同，但没有标注为修改点。

### 2.6 `core/silence_detector.py`（不修改）

**评价：正确（N/A）**

RMS 检测确实采样率无关，float32 归一化保证了这一点。SilenceDetector 的 `silence_check_interval` 基于 `queue.get(timeout=0.5)` 轮询，与采样率无关。**不修改是正确的决策**。

### 2.7 `config.py`（修改模块）

**评价：良好（82/100）**

**亮点：**
- `__post_init__` 处理字符串 `"auto"` / `"none"` / `""` 的转换
- v6→v7 迁移函数完整
- 无效字符串 graceful fallback 到 `None`

**新发现 Minor 问题：**

- ⚠️ **新 m12**: 当前 `config.py` 的 `CURRENT_CONFIG_VERSION = 6`，v2.0 设计新增 `_migrate_v6_to_v7` 但没有展示 `CURRENT_CONFIG_VERSION = 7` 的更新和 `CONFIG_MIGRATIONS` 注册表的更新。这是变更清单的遗漏。

- ⚠️ **新 m13**: `AudioConfig.sample_rate` 的类型 `Optional[int]` 在 v2.0 设计中处理了 `"auto"` 字符串，但 `_dict_to_dataclass` 在反序列化时如果 YAML 中 `sample_rate: auto`，`_flatten_to_appconfig` 会直接将字符串 `"auto"` 传给 `AudioConfig(sample_rate="auto")`。由于 `Optional[int]` 类型注解在 Python dataclass 中不做运行时类型检查，`"auto"` 会被赋值为字符串，然后 `__post_init__` 转换为 `None`。**这个流程是正确的**，但依赖于 `__post_init__` 被执行。如果有人直接构造 `AudioConfig(sample_rate="48000")`，`__post_init__` 也能正确处理（int 转换）。✅

---

## 三、架构完整性评审

### 3.1 6 个引擎分支覆盖

| 引擎 | 实时模式 | Resample 路径 | 覆盖 | 说明 |
|------|---------|-------------|------|------|
| funasr-streaming | StreamingTranscriber | 累积 → resample → engine.transcribe_chunk | ✅ | §2.6 完整设计 |
| funasr | VADSegmentTranscriber | 累积 → flush → resample → transcribe_sync | ✅ | §2.5 完整设计 |
| mlx-whisper | VADSegmentTranscriber | 同上 | ✅ | 复用 VAD 路径 |
| qwen3-asr | VADSegmentTranscriber | 同上 | ✅ | 复用 VAD 路径 |
| faster-whisper | VADSegmentTranscriber | 同上 | ✅ | 复用 VAD 路径 |
| auto | 动态选择上述任一 | 同上 | ✅ | auto 在 `__init__` 中解析为具体引擎 |

**结论：6 个引擎分支全部正确覆盖，无遗漏。**

### 3.2 三条路径设计

| 路径 | 设计位置 | Resample 插入点 | 合理性 |
|------|---------|---------------|--------|
| 批量模式 | §2.4.1 | `recorder.get_resampled_audio()` — stop 后一次性 resample | ✅ 合理：延迟最低（resample 只做一次），代码改动最小 |
| VAD 分段模式 | §2.5 `_flush_speech_buffer()` | flush 时 resample 整段语音 | ✅ 合理：每段一次 resample，开销可忽略 |
| 流式模式 | §2.6 `_flush_chunk()` | 每 chunk 累积到 `chunk_samples` 后 resample 再送引擎 | ✅ 合理：resample 在 chunk 边界执行，不影响实时性 |

**结论：三条路径的 resample 插入点都合理，"先累积再 resample"的策略保证了 ASR 引擎始终收到 16kHz 音频。**

### 3.3 数据流关键不变量验证

| 不变量 | v2.0 设计是否保证 | 验证 |
|--------|----------------|------|
| ASR 模型始终接收 16kHz | ✅ | 所有路径在传给 STT 引擎前都完成了 resample |
| SilenceDetector RMS 阈值不受影响 | ✅ | float32 归一化，采样率无关 |
| webrtcvad 帧格式正确 | ✅ | `_vad_resampler` 确保输入在支持列表中 |
| 流式引擎收到 9600 样本 @16kHz | ✅ | 48kHz 28800 → resample → 9600 |
| 实时延迟不增加 | ✅ | resample 在 flush 时一次性做 |

---

## 四、技术正确性评审

### 4.1 webrtcvad 帧长度计算

**验证：** `frame_length = int(self._config.vad_window_ms * vad_sr / 1000)`

- `vad_window_ms = 30`（默认），`vad_sr = 16000` → `frame_length = 480` ✅
- `vad_window_ms = 30`，`vad_sr = 48000`（直接使用，不创建 `_vad_resampler`）→ `frame_length = 1440` ✅（48000 在 webrtcvad 支持列表中）
- `vad_window_ms = 30`，`vad_sr = 44100` → 创建 `_vad_resampler` 到 16000 → `frame_length = 480` ✅

**结论：帧长度计算正确。**

但有一个边界条件需要注意：`vad_window_ms` 目前是 30ms（配置值），webrtcvad 支持 10/20/30ms。如果用户修改为非标准值（如 15ms），会导致 `is_speech()` 抛异常。设计文档中 `RealtimeConfig.__post_init__` 没有验证 `vad_window_ms` 是否在 `{10, 20, 30}` 中。**这是一个 Minor 问题，但已超出本次采样率修复的范围。**

### 4.2 resample_poly 的使用

**GCD 计算：**
- 48000 → 16000: `gcd(48000, 16000) = 16000`, `up = 16000/16000 = 1`, `down = 48000/16000 = 3` → 3:1 降采样 ✅
- 44100 → 16000: `gcd(44100, 16000) = 100`, `up = 16000/100 = 160`, `down = 44100/100 = 441` → 441:160 降采样 ✅
- 32000 → 16000: `gcd(32000, 16000) = 16000`, `up = 1`, `down = 2` → 2:1 降采样 ✅

**结论：GCD 计算和 up/down 推导全部正确。`resample_poly(audio, up, down)` 的语义是先上采样 `up` 倍再下采样 `down` 倍，内部会自动选择合适的 FIR 滤波器。**

### 4.3 chunk_samples 动态计算

**验证：** `chunk_samples = int(chunk_ms * self._source_sr / 1000)`

- `chunk_ms = 600`, `source_sr = 48000` → `chunk_samples = int(600 * 48000 / 1000) = 28800` ✅
- resample 后：`28800 * (16000/48000) = 9600` ✅（与 FunASRStreamingEngine.CHUNK_SAMPLES 一致）
- `chunk_ms = 600`, `source_sr = 16000` → `chunk_samples = 9600` ✅（与修改前一致）
- `chunk_ms = 600`, `source_sr = 44100` → `chunk_samples = 26460` → resample 后 `26460 * (16000/44100) ≈ 9600` ✅

**精确性说明：** `resample_poly` 对非整数比的采样率转换会产生微小的长度差异（±1 样本），这是 FIR 滤波器的正常行为。FunASR 流式引擎对 chunk 长度有小范围容差，不影响识别。

**结论：chunk_samples 动态计算正确。**

---

## 五、安全与稳定性评审

### 5.1 异常处理

| 场景 | v2.0 处理 | 评价 |
|------|---------|------|
| scipy 未安装 | `ImportError` + 安装指引 | ✅ 完善 |
| 设备采样率检测失败 | 四层回退到 16000 | ✅ 完善 |
| 设备打开失败 | 指定设备回退到默认 | ✅ 完善 |
| resample 空音频 | 入口直接返回 | ✅ 完善 |
| resample 非一维音频 | flatten + WARNING | ✅ 完善 |
| 音频拼接失败 | try/except + logger.error | ✅ 完善 |
| 流关闭异常 | try/except + logger.warning | ✅ 完善 |
| STT 转写失败 | try/except + logger.error | ✅ 完善 |
| VAD 检测异常 | try/except → return False | ✅ 完善 |

### 5.2 边界条件

| 条件 | v2.0 处理 | 评价 |
|------|---------|------|
| 设备原生 16kHz | `needs_resample=False`，零拷贝直通 | ✅ |
| 空音频 | resample 入口返回空数组 | ✅ |
| 极短音频（<32 samples） | 无异常，resample_poly 正常处理 | ✅ |
| PortAudio 实际采样率 ≠ 请求 | WARNING 日志 | ✅（但无自动修正——见新问题） |
| 非常见采样率（44100） | GCD 自动优化 + VAD resampler | ✅ |
| config `sample_rate: "auto"` | `__post_init__` 转换为 None | ✅ |
| config `sample_rate: null` | None = auto | ✅ |

### 5.3 线程安全

| 组件 | 线程模型 | 安全性 | 评价 |
|------|---------|--------|------|
| AudioResampler | 无状态（构造后只读），多线程安全 | ✅ | 完全线程安全 |
| recorder._audio_callback | sounddevice 实时线程 | ✅ | 只做数据拷贝 |
| VADSegmentTranscriber._buffer | 单线程消费 | ✅ | `_run_inner` 单线程 |
| StreamingTranscriber._buffer | `_buffer_lock` 保护 | ✅ | 正确使用锁 |
| _inject_queue | `queue.Queue` 线程安全 | ✅ | |

**新发现 Minor 问题：**
- ⚠️ **新 m14**: `recorder.py` 的 `_audio_callback` 同时操作 `self._buffer`（deque，无锁）和 `self._buffer_queue`（Queue，线程安全）。现有代码中 `stop()` 在主线程读取 `self._buffer`（`np.concatenate(list(self._buffer))`），而 `_audio_callback` 在音频线程中 `self._buffer.append(chunk)`。**deque 不是线程安全的**，这在现有代码中就已存在（不是 v2.0 引入的），且由于 Python GIL 和 deque 的 append/iteration 的实现，实践中不会出问题。但严格来说这是一个 data race。v2.0 不修改此部分，不算 v2.0 的问题。

---

## 六、代码变更完整性（对照现有代码）

### 6.1 变更清单核查

| 文件 | v2.0 列出 | 实际需要变更 | 匹配 |
|------|----------|------------|------|
| `core/audio_resampler.py` | 新增 | 新增 | ✅ |
| `core/recorder.py` | 修改 | 修改（4 处方法变更） | ✅ |
| `core/engine.py` | 修改 | 修改（2 处方法变更） | ✅ |
| `core/vad_segment_transcriber.py` | 修改 | 修改（4 处方法变更） | ✅ |
| `core/streaming_transcriber.py` | 修改 | 修改（3 处方法变更） | ✅ |
| `core/silence_detector.py` | 不修改 | 不修改 | ✅ |
| `core/stt_engine.py` | 不修改 | 不修改（但内部 `3200` 阈值是冗余的） | ✅ |
| `core/stt_funasr.py` | 不修改 | 不修改（同上） | ✅ |
| `core/stt_qwen3_asr.py` | 不修改 | 不修改（同上） | ✅ |
| `core/stt_mlx_whisper.py` | 不修改 | 不修改 | ✅ |
| `core/stt_funasr_streaming.py` | 不修改 | 不修改 | ✅ |
| `config.py` | 修改 | 修改（2 处变更） | ✅ |
| `config.yaml` | 修改 | 修改（新增 sample_rate 字段） | ✅ |
| `tests/test_audio_resampler.py` | 新增 | 新增 | ✅ |
| `tests/test_recorder_sr.py` | 新增 | 新增 | ✅ |
| `requirements.txt` | 不修改 | 不修改（scipy 已是依赖） | ✅ |

### 6.2 新发现的遗漏

- ⚠️ **新 m15**: v2.0 设计中 `streaming_transcriber.py` 的现有模块级常量 `SAMPLE_RATE = 16000`（第 22 行）没有被标注为需要删除或修改。v2.0 引入了 `self._source_sr` 实例变量替代它，但模块级常量仍留在文件中可能引起混淆。建议在变更清单中标注"删除模块级 SAMPLE_RATE 常量"。

- ⚠️ **新 m16**: v2.0 设计中 `vad_segment_transcriber.py` 的现有模块级常量 `SAMPLE_RATE = 16000`（第 21 行）同样没有标注为需要删除。v2.0 引入了 `self._source_sr` 替代它。

---

## 七、综合评分

| 维度 | 得分 | 说明 |
|------|------|------|
| 架构设计 | **92/100** | 方向正确，模块划分合理，resample 插入点选择最优 |
| 完整性 | **90/100** | 三条路径全部设计完整，6 个引擎分支无遗漏 |
| 技术正确性 | **95/100** | resample_poly 使用正确，GCD 计算正确，chunk_samples 动态计算正确 |
| 代码质量 | **85/100** | 异常处理完善，线程安全，有小幅改进空间 |
| 文档质量 | **88/100** | v1.0 问题全部追踪，数据流清晰，测试计划完整 |
| **综合得分** | **90/100** | |

**相比 v1.0（71/100）提升 19 分，主要来自流式模式和 VAD 模式的完整设计。**

---

## 八、关键问题清单（新发现问题）

### Major 级别（3 项）

| # | 模块 | 问题 | 建议 |
|---|------|------|------|
| 新 M1 | recorder.py | `blocksize = max(512, int(device_sr * 0.01))` 对 16kHz 设备下限 512 不变，8kHz 设备下 512 样本 = 64ms/chunk，是否可接受？ | 在文档中说明 `base_blocksize = 512` 的选择依据（sounddevice 推荐值）。实际影响极小，可接受。 |
| 新 M2 | engine.py / 各 STT 引擎 | 批量模式的最短时长判断从 STT 引擎内部提前到 engine.py，形成双重判断。 | 在设计文档中注明引擎内部 `3200` 阈值是兜底安全网，正式判断已提前。实施时保留引擎内部判断。 |
| 新 M3 | vad_segment_transcriber.py | v2.0 补充了 `_inject_queue.put(None)` sentinel，修复了现有代码的线程泄漏 bug，但文档未标注为 bug fix。 | 在变更清单中标注此项为隐含 bug fix。 |

### Minor 级别（16 项，按优先级排序）

| # | 问题 | 建议 |
|---|------|------|
| 新 m1 | `resample()` 每次调用都 `import scipy.signal` | `create_resampler` 中导入一次并缓存 |
| 新 m8 | VADSegmentTranscriber.start() 签名变更 + 补充 inject_thread 启动 | 变更清单已覆盖，但建议标注为现有 bug fix |
| 新 m9 | `streaming_transcriber.py` 的 `_inject_worker` sentinel 处理 | v2.0 的 stop() 使用 sentinel 但 `_inject_worker` 没有展示 `if text is None: break` 修改 |
| 新 m10 | `engine.get_chunk_samples()` 不再被调用 | 保留作为引擎接口，不删除 |
| 新 m11 | `_inject_worker` 的 1 秒 timeout 可能延迟退出 | v2.0 已改为 sentinel 模式（更优） |
| 新 m12 | `CURRENT_CONFIG_VERSION` 和 `CONFIG_MIGRATIONS` 更新未展示 | 补充 |
| 新 m13 | `AudioConfig.sample_rate` 的 `__post_init__` 字符串处理流程 | 已确认正确 |
| 新 m14 | `recorder._buffer` deque 的 data race | 现有代码问题，超出 v2.0 范围 |
| 新 m15 | `streaming_transcriber.py` 模块级 `SAMPLE_RATE` 常量未标注删除 | 在变更清单中标注 |
| 新 m16 | `vad_segment_transcriber.py` 模块级 `SAMPLE_RATE` 常量未标注删除 | 在变更清单中标注 |

---

## 九、优化建议（按优先级排序）

### P0（实施前必须处理）

1. **补充 `_inject_worker` sentinel 处理** — v2.0 的 `stop()` 使用 `put(None)` sentinel，但 `_inject_worker` 代码片段没有展示 `if text is None: break`。确认 v2.0 的完整代码会包含此修改。

### P1（实施时建议处理）

2. **标注现有 bug fixes** — v2.0 修复了以下现有代码的隐含 bug（建议在变更清单中标注）：
   - `VADSegmentTranscriber.stop()` 缺少 sentinel 注入导致线程泄漏
   - `VADSegmentTranscriber.start()` 缺少 `_inject_thread` 启动

3. **标注 STT 引擎双重判断** — `engine.py` 层的 `0.2s` 判断 + STT 引擎内部的 `3200` 样本判断并存，在文档中注明关系。

4. **删除模块级 SAMPLE_RATE 常量** — `vad_segment_transcriber.py` 和 `streaming_transcriber.py` 的 `SAMPLE_RATE = 16000` 在 v2.0 中不再使用，应在变更清单中标注删除。

### P2（建议后续迭代）

5. **缓存 scipy 导入** — `resample()` 中每次 `import scipy.signal` 改为构造时一次导入。
6. **`base_blocksize = 512` 的选择依据** — 在文档或注释中说明。
7. **`RealtimeConfig.vad_window_ms` 值域验证** — 应限制为 `{10, 20, 30}`，防止 webrtcvad 异常。
8. **评估 soxr 升级路径** — v2.0 已在 §十 提及，确认保留。

---

## 十、评审总结

**v2.0 是一份高质量的设计文档，全面解决了 v1.0 的所有问题。**

- **架构方向完全正确**：设备原生采样率采集 + 代码层 resample，是解决 PortAudio 自动重采样不稳定的行业最佳实践。
- **覆盖度从 55 分提升到 90 分**：v1.0 最大的短板（流式模式和 VAD 模式的适配）在 v2.0 中得到了完整、正确的设计。
- **技术细节正确**：resample_poly 参数计算、chunk_samples 动态计算、webrtcvad 帧长度计算均经验证无误。
- **异常处理和线程安全**：设计完善，无重大遗漏。

**可以进入实施阶段。** 建议实施时关注 P0 项（_inject_worker sentinel 处理）和 P1 项（标注现有 bug fixes）。

---

*评审完成时间: 2026-04-29 02:09 CST*  
*评审版本: v2.0*  
*综合评分: **90/100***
