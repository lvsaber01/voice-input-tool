# 音频采集采样率兼容性修复 — 设计文档专业评审报告

## 评审版本：v1.0 | 评审日期：2026-04-29

---

## 一、逐模块评审

### 1.1 `core/audio_resampler.py`（新增模块）

**优点：**
- 职责单一，设计清晰，仅负责重采样
- 预计算 `up/down` 参数避免重复运算，性能意识好
- 源=目标时的 fast path（零拷贝）设计合理
- 接口简洁，属性暴露 `source_sr` / `target_sr` 便于下游使用

**不足：**
- **(Major)** `resample()` 方法对 scipy 导入失败没有处理。文档说"降级到 `numpy.interp`"但在 `AudioResampler` 类中完全没体现。应在 `__init__` 或 `resample()` 中实现 fallback 逻辑
- **(Minor)** 空音频（`len(audio) == 0`）会传入 `resample_poly`，虽不会崩溃但浪费计算，应在入口直接返回
- **(Minor)** `math.gcd` 未导入，代码示例缺少 `import math`
- **(Minor)** `resample()` 缺少对输入维度的校验（如 `audio.ndim != 1`），非一维数组可能导致意外行为

### 1.2 `core/recorder.py`（修改模块）

**优点：**
- `_detect_device_sample_rate()` 设计了四层回退链（配置 → 查询 → 探测 → 默认），鲁棒性好
- 探测模式下尝试打开/关闭流来验证采样率，思路正确
- 新增 `get_resampled_audio()` 方法封装批量模式的 resample 逻辑
- 诊断日志设计充分，有利于排查问题

**不足：**
- **(Critical)** `_detect_device_sample_rate()` 的探测模式中，`sd.InputStream.start()` 后立即 `stop()/close()` 可能在某些 Windows WASAPI 后端上不可靠——PortAudio 可能延迟初始化音频设备管线。探测采样率不等于"验证设备原生采样率"。**建议**：将探测逻辑改为检查 `test_stream.samplerate` 是否等于请求的 `probe_sr`（代码中已有 `actual_sr = test_stream.samplerate` 但没做判断就 `return` 了）
- **(Major)** 设备回退逻辑（指定设备不可用时 fallback 到默认设备）在修改后仍然使用 `self._device_sr`，但 `sd.query_devices(device)` 查询的是回退前的设备，回退后未重新检测采样率
- **(Major)** `_audio_callback` 仍然将 chunk 同时放入 `_buffer` 和 `_rt_queue`，但 `_buffer` 由 `SilenceDetector` 消费。当采样率从 16kHz 变为 48kHz 后，每个 chunk 的样本数不变（由 `blocksize` 决定），但代表的**时长变短了 3 倍**。SilenceDetector 的 RMS 计算不受影响（float32 归一化），但 `silence_timeout` 的行为精度会因 chunk 时长变短而提高，这不是问题，但**文档没有分析这一变化**
- **(Minor)** `_detect_device_sample_rate` 中 `sd.query_devices(device)` 当 `device=None` 时可能返回一个 list 的摘要而非具体设备。应显式传入 `device` 或 `sd.default.device[0]`

### 1.3 `core/engine.py`（修改模块）

**优点：**
- 批量模式改为 `get_resampled_audio()` 路径清晰
- 设计文档中提及了实时模式将 resampler 传入 VADSegmentTranscriber / StreamingTranscriber

**不足：**
- **(Critical)** 现有代码中 `VADSegmentTranscriber` 构造函数签名是 `(config, stt_engine, on_segment_transcribed)`，`StreamingTranscriber` 构造函数是 `(config)`。设计文档要求在构造时传入 `resampler`，但 **engine.py 的伪代码没有完整展示所有 5 个引擎分支的修改**。尤其是 `FunASRStreamingEngine` + `StreamingTranscriber` 路径（流式模式），需要将 resampler 传入 StreamingTranscriber，但 StreamingTranscriber 内部做的是 chunk-by-chunk 流式处理，**不是分段 flush，无法简单地"flush 时 resample"**
- **(Major)** `_stop_recording_and_transcribe` 中存在录音时长判断 `if len(audio) < 3200`（0.2s @16kHz），但修改后 `get_resampled_audio()` 返回的已经是 16kHz，这个判断仍然正确。但如果后续有人直接用 `stop()` 获取原生采样率音频，这个 3200 样本阈值就不对了（48kHz 下 3200 样本仅 ~0.067s）。**建议**：将此判断改为基于秒数而非样本数
- **(Minor)** `_start_streaming` 中 `_stream_transcriber.start(self._rt_audio_queue)` 的调用方式在不同引擎分支中不一致（流式模式传引擎+回调，VAD 模式只传队列），resampler 传入方式也需要对应区分

### 1.4 `core/vad_segment_transcriber.py`（修改模块）

**优点：**
- `_flush_speech_buffer` 中在送入 ASR 前统一 resample，设计合理
- VAD 采样率适配考虑了 webrtcvad 的限制

**不足：**
- **(Critical)** 现有代码中 `VADSegmentTranscriber` 硬编码了 `SAMPLE_RATE = 16000`（模块级常量），用于三处：
  1. `_run_inner` 中计算 `_speech_duration += len(chunk) / SAMPLE_RATE` — 采样率变了这里就错了
  2. `_vad_detect` 中 `frame_length = int(self._config.vad_window_ms * SAMPLE_RATE / 1000)` — 48kHz 下帧长度被算错
  3. `_flush_speech_buffer` 中 `min_samples = int(self._config.min_segment_duration * SAMPLE_RATE)` — 16kHz 下的 min_samples 被用于 48kHz 的音频
  
  **设计文档只提到了 VAD 适配（第3.4节），但完全没有提到这三处硬编码 `SAMPLE_RATE` 的修改方案。** 这是最大的遗漏

- **(Major)** webrtcvad 的帧长度要求（10/20/30ms 对应 480/960/1440 样本 @16kHz）在 48kHz 下变为 1440/2880/4320 样本。但 `resample_poly` 的延迟可能不足以在每个 chunk 到达时实时产出精确帧长。设计文档的 VAD 适配方案（先 resample 到最近支持采样率）是对的，但需要新增一个**独立的轻量 resampler 专门用于 VAD**（`_vad_resampler`），文档仅提到概念但没有在 `VADSegmentTranscriber.__init__` 中展示初始化代码
- **(Minor)** `_flush_speech_buffer` 中 resample 后用 `16000` 硬编码计算 `min_samples`，但如果未来目标采样率变化（如 8kHz 模型），需要同步修改。建议使用 `resampler.target_sr`

### 1.5 `core/streaming_transcriber.py`（修改模块）

**优点：**
- 设计文档将其列入变更清单

**不足：**
- **(Critical)** StreamingTranscriber 是 chunk-by-chunk 处理模式（累积到 `chunk_samples` 后调用 `transcribe_chunk`），每个 chunk 只有 600ms（9600 样本 @16kHz）。如果输入是 48kHz，累积到的 9600 样本实际上只代表 200ms 的音频，**远不够一个有效 chunk**。
  
  **正确的做法**：StreamingTranscriber 需要知道源采样率，将 `chunk_samples` 从 16kHz 对应值转换为源采样率对应值（`9600 * 48000/16000 = 28800`），或者在累积后 resample 再判断是否够 chunk。**设计文档完全没有分析 StreamingTranscriber 的适配方案**，只说"类似 VADSegmentTranscriber 的 resample 适配"，但两者的处理模式根本不同

- **(Major)** `FunASRStreamingEngine.CHUNK_SAMPLES = 9600`（硬编码 16kHz × 0.6s），如果上游音频是 48kHz，StreamingTranscriber 传入的 chunk 长度与引擎期望的不匹配。需要在 StreamingTranscriber 中做 resample 后再传给引擎，而不是让引擎收到 48kHz 数据

- **(Minor)** StreamingTranscriber 没有类似 VADSegmentTranscriber 的 `_speech_buffer` flush 模式，resample 需要在 `_flush_chunk` 中逐 chunk 执行

### 1.6 `core/silence_detector.py`（设计为不修改）

**优点：**
- RMS 能量检测确实采样率无关（float32 归一化）

**不足：**
- **(Major)** 虽然不修改 SilenceDetector 本身是正确的，但需要注意：SilenceDetector 消费的 chunk 来自 `recorder._buffer_queue`。修改后每个 chunk 的样本数不变（由 sounddevice `blocksize` 决定），但代表的时长变了。如果 `blocksize` 默认值导致 48kHz 下每个 chunk 极短（如 <10ms），可能增加 CPU 开销。**建议**：在 recorder.py 中根据采样率调整 `blocksize`（如保持每 chunk ~10ms，48kHz 下 blocksize=480）
- **(Minor)** SilenceDetector 的 `silence_check_interval: float = 0.5` 是基于 `queue.get(timeout=0.5)` 的轮询间隔，不受采样率影响。确认没问题

### 1.7 `config.py`（修改模块）

**优点：**
- 新增 `sample_rate` 字段设计合理，支持 `None/"auto"` / 具体值
- 版本迁移 v6→v7 设计完整

**不足：**
- **(Minor)** 设计文档中 `AudioConfig.sample_rate` 类型为 `Optional[int]`，但 YAML 配置示例写的是 `sample_rate: auto`（字符串）。需要在 `_flatten_to_appconfig` 或 `AudioConfig.__post_init__` 中处理字符串 `"auto"` 到 `None` 的转换。文档提到了但没有代码
- **(Minor)** 迁移函数中设置 `audio["sample_rate"] = None`，但如果用户的 YAML 加载后得到 `None`，`_dict_to_dataclass` 会正确使用默认值。确认没问题，但建议显式注释说明 `None` = `auto`

---

## 二、架构评审

### 2.1 整体架构评价

**整体思路正确**：以设备原生采样率采集 + 代码层手动 resample，完全绕过 PortAudio 的黑盒重采样，是解决该类问题的标准方案。架构方向无异议。

### 2.2 模块间耦合度

**评分：良好**

- `AudioResampler` 作为独立模块，耦合度极低
- Resampler 通过构造函数注入（`VADSegmentTranscriber.__init__` 接收 `resampler`），符合依赖注入原则
- `recorder.py` 通过属性 `resampler` 暴露，engine 通过此属性传递，层次清晰

**改进建议**：考虑在 `AudioResampler` 中增加 `@staticmethod create(source_sr, target_sr=16000)` 工厂方法，统一创建入口和 scipy 不可用时的 fallback 选择。

### 2.3 扩展性

**评分：中等**

- 新增 STT 引擎时只需确保输入 16kHz，resampler 透明处理，扩展性良好
- 如果未来需要支持非 16kHz 目标采样率（如某些 VAD 模型需要 8kHz），需要修改 `AudioResampler.TARGET_SR` 为参数，但当前设计已经支持（构造函数传入 source_sr，target_sr 为类常量），改为实例变量即可

### 2.4 多 STT 引擎影响分析

| 引擎 | 当前采样率假设 | 影响评估 | 设计覆盖 |
|------|--------------|---------|---------|
| faster-whisper | 16kHz（硬编码 3200 样本阈值） | ✅ resample 后为 16kHz，兼容 | ✅ 已覆盖 |
| funasr (Paraformer) | 16kHz（`model.generate(input=audio)`） | ✅ resample 后为 16kHz，兼容 | ✅ 已覆盖 |
| funasr (SenseVoice) | 16kHz（同上） | ✅ 同上 | ✅ 已覆盖 |
| funasr (Fun-ASR-Nano) | 16kHz（同上） | ✅ 同上 | ✅ 已覆盖 |
| mlx-whisper | 16kHz | ✅ resample 后为 16kHz，兼容 | ✅ 已覆盖 |
| qwen3-asr | **16kHz（显式传入 `audio=(audio, 16000)`）** | ✅ resample 后为 16kHz，兼容 | ✅ 已覆盖 |
| funasr-streaming | 16kHz（`CHUNK_SAMPLES = 9600`） | ⚠️ StreamingTranscriber 需在 chunk 级 resample | ❌ 未完整覆盖 |

**关键遗漏**：`stt_qwen3_asr.py` 第155行硬编码了 `audio=(audio, 16000)`。虽然 resampler 保证输入是 16kHz，但如果有人忘记 resample 直接传入，这里会静默出错。建议增加运行时断言 `assert source_sr == 16000` 或至少加日志 warning。

### 2.5 批量模式 vs 实时模式覆盖

| 路径 | 设计覆盖 | 完整性 |
|------|---------|--------|
| 批量模式（热键录音 → STT） | ✅ `get_resampled_audio()` | 完整 |
| VAD 分段模式（VADSegmentTranscriber） | ⚠️ flush 时 resample | 有遗漏（硬编码 SAMPLE_RATE） |
| FunASR 流式模式（StreamingTranscriber） | ❌ 仅一句话提及 | **严重不完整** |

---

## 三、技术选型评审

### 3.1 `scipy.signal.resample_poly` 选型

**评价：恰当，但有更优替代方案**

| 维度 | scipy.resample_poly | resampy (soxr) | torchaudio |
|------|---------------------|-----------------|------------|
| 音频质量 | 良好（FIR 抗混叠） | **优秀**（SoXR 库，专业级） | 良好 |
| CPU 开销 | 中等 | **极低**（C 实现） | 低（GPU 可用） |
| 依赖 | 已有（funasr 依赖 scipy） | 需新装 ~2MB | 仅 torch 引擎可用 |
| 实时场景延迟 | ~5ms / 480 帧 | **<1ms / 480 帧** | 低 |

**结论**：当前选型 `scipy.resample_poly` 是**合理的首选**（零新增依赖，质量达标）。但文档中应补充一个**"未来升级路径"**：如果实时场景出现可感知的延迟，可切换到 `resampy`（纯 C 实现，CPU 开销降低 10x+，且体积仅 ~2MB）。

### 3.2 `numpy.interp` 降级方案

**评价：不推荐作为 fallback**

`numpy.interp` 是线性插值，会产生严重的混叠和频率失真。48kHz→16kHz 用线性插值后，高频信号会折叠到低频，ASR 模型收到的音频质量远差于 PortAudio 的重采样（即使是出故障的 PortAudio）。

**建议**：如果 scipy 不可用，更好的 fallback 是：
1. 尝试 `import soxr`（如果用户装了）
2. 直接 `raise ImportError` 并提示安装 scipy（因为 funasr 本身就依赖 scipy，所以实际上 scipy 不可用的概率极低）
3. **不要用 numpy.interp 做音频降采样**

---

## 四、问题汇总与评分

### 4.1 Critical 级别问题（必须修复）

| # | 模块 | 问题 | 影响 |
|---|------|------|------|
| C1 | recorder.py | 探测模式验证不充分，WASAPI 后端可能误判 | Windows 设备采样率检测失败 |
| C2 | engine.py | 未展示 StreamingTranscriber 路径的 resampler 注入方式 | 流式模式无法工作 |
| C3 | vad_segment_transcriber.py | 3处硬编码 `SAMPLE_RATE=16000` 未在文档中提及修改方案 | VAD 分段时长/帧长/min_samples 全部计算错误 |
| C4 | streaming_transcriber.py | chunk 累积逻辑未适配源采样率，9600样本阈值含义变化 | 流式 ASR 只产出 200ms 的 chunk，识别率暴跌 |

### 4.2 Major 级别问题（应当修复）

| # | 模块 | 问题 |
|---|------|------|
| M1 | audio_resampler.py | scipy fallback 逻辑未实现 |
| M2 | recorder.py | 设备回退后未重新检测采样率 |
| M3 | recorder.py | chunk 时长变化未在文档中分析 |
| M4 | vad_segment_transcriber.py | 缺少独立的 VAD resampler 初始化代码 |
| M5 | streaming_transcriber.py | CHUNK_SAMPLES 硬编码未适配 |
| M6 | silence_detector.py | blocksize 未根据采样率调整 |
| M7 | engine.py | 样本数阈值应改为秒数 |

### 4.3 Minor 级别问题（建议改进）

| # | 模块 | 问题 |
|---|------|------|
| m1 | audio_resampler.py | 缺少 math.gcd 导入 |
| m2 | audio_resampler.py | 缺少输入维度校验 |
| m3 | audio_resampler.py | 缺少空音频检查 |
| m4 | recorder.py | device=None 时 query_devices 行为不确定 |
| m5 | engine.py | resampler 传入方式在各引擎分支不一致 |
| m6 | vad_segment_transcriber.py | 硬编码 16000 应改用 resampler.target_sr |
| m7 | config.py | 字符串 "auto" 到 None 的转换逻辑缺失代码 |

---

## 五、综合评分

| 维度 | 得分 | 说明 |
|------|------|------|
| 架构设计 | **85/100** | 方向正确，模块划分合理，耦合度低 |
| 完整性 | **55/100** | 批量模式完整，VAD 模式有遗漏，流式模式严重不完整 |
| 技术选型 | **80/100** | scipy 选型合理，fallback 方案需改进 |
| 代码质量 | **70/100** | 缺少输入校验、fallback 逻辑、维度检查 |
| 文档质量 | **65/100** | 批量路径详尽，但遗漏了关键模块的硬编码适配分析 |
| **综合得分** | **71/100** | |

### 评分说明

- 批量模式的设计完整且可落地（~85分）
- VAD 模式遗漏了硬编码 SAMPLE_RATE 的适配（减10分）
- **流式模式几乎未设计**（减15分），这是最大的短板
- numpy.interp fallback 方案不可用（减5分）

---

## 六、改进建议优先级

### P0（必须，阻塞实现）
1. **补充 StreamingTranscriber 完整适配方案** — chunk 累积逻辑需感知源采样率
2. **补充 VADSegmentTranscriber 3处硬编码 SAMPLE_RATE 的修改方案**
3. **修复 recorder.py 探测模式的验证逻辑**

### P1（应当，实现前完成）
4. 实现 scipy fallback 逻辑（改用 ImportError 提示而非 numpy.interp）
5. 设备回退后重新检测采样率
6. 补充 blocksize 采样率自适应
7. 样本数阈值改为基于秒数

### P2（建议，可后续迭代）
8. 输入维度校验 + 空音频检查
9. 字符串 "auto" 配置解析
10. 评估 resampy/soxr 作为实时场景升级路径
