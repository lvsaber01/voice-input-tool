# FunASR 流式引擎适配设计

**日期**: 2026-04-21  
**状态**: 已确认，待实现  
**作者**: Saber (AI Assistant)

---

## 1. 背景

当前实时转写模式使用 VAD 分段逻辑：
- VAD 检测 → 累积语音段 → 静音阈值触发 → 批量转写 → 输出
- 延迟约 2-4 秒，不是真正的流式

FunASR 提供官方流式模型 `paraformer-zh-streaming`：
- 600ms chunk + 300ms lookahead
- RTF < 0.001（超低延迟）
- 适合实时字幕场景

---

## 2. 目标

- 新增 FunASR 流式引擎支持
- 实现真正的流式转写（600ms/chunk）
- VAD 分段模式保留为降级方案

---

## 3. 架构设计

### 3.1 新增组件

| 组件 | 文件 | 职责 |
|------|------|------|
| FunASRStreamingEngine | `core/stt_funasr_streaming.py` | 封装 FunASR 流式 API |
| StreamingTranscriber | `core/streaming_transcriber.py` | 固定 chunk 切分 + 流式转写 |

### 3.2 修改组件

| 组件 | 变更 |
|------|------|
| StreamTranscriber | 重命名为 VADSegmentTranscriber（降级备用） |
| config.py | 新增 StreamingConfig 配置项 |
| engine.py | 根据引擎类型选择流式/分段模式 |

### 3.3 引擎选择逻辑

```
FunASR + streaming.enabled=True → FunASRStreamingEngine + StreamingTranscriber
FunASR + streaming.enabled=False → FunASREngine + VADSegmentTranscriber  
faster-whisper → STTEngine + VADSegmentTranscriber（不支持流式）
```

---

## 4. 核心接口设计

### 4.1 流式引擎抽象接口

```python
from typing import Protocol

class StreamingEngineProtocol(Protocol):
    """流式引擎抽象接口
    
    所有流式引擎（FunASR、Sherpa-onnx 等）都应实现此接口。
    """
    
    def load_model(self) -> tuple[bool, str]:
        """加载模型"""
        ...
    
    def get_chunk_samples(self) -> int:
        """返回引擎要求的 chunk 样本数"""
        ...
    
    def transcribe_chunk(self, audio_chunk: np.ndarray, is_final: bool) -> str:
        """流式转写一个 chunk"""
        ...
    
    def reset(self):
        """重置流式状态"""
        ...
```

### 4.2 FunASRStreamingEngine

```python
class FunASRStreamingEngine:
    """FunASR 流式引擎
    
    chunk_size = [0, 10, 5] 表示：
    - 0: 前瞻块数
    - 10: 当前块 = 10 * 60ms = 600ms
    - 5: 后瞻块 = 5 * 60ms = 300ms lookahead
    """
    
    CHUNK_SIZE = [0, 10, 5]
    CHUNK_MS = 600  # 固定值，不可配置
    CHUNK_SAMPLES = 9600  # 16000 * 0.6
    
    def __init__(self, config):
        self.config = config
        self.model = None
        self._cache = {}  # 流式状态缓存
        self._lock = threading.Lock()  # 保护 cache 状态
    
    def load_model(self) -> tuple[bool, str]:
        """加载 paraformer-zh-streaming 模型"""
    
    def get_chunk_samples(self) -> int:
        """返回引擎要求的 chunk 样本数"""
        return self.CHUNK_SAMPLES
    
    def transcribe_chunk(self, audio_chunk: np.ndarray, is_final: bool) -> str:
        """流式转写 - 每600ms调用一次
        
        Args:
            audio_chunk: 音频数据（长度由 get_chunk_samples 决定）
            is_final: 最后一个chunk时True，强制输出
            
        Returns:
            当前 chunk 的增量文本（非累积全量）
            - 中间 chunk：可能返回增量，也可能为空（等待更多上下文）
            - is_final=True：返回最后累积的文本
            - 异常时返回空字符串，不中断循环
        """
        try:
            with self._lock:
                result = self.model.generate(
                    input=audio_chunk,
                    cache=self._cache,
                    is_final=is_final,
                    chunk_size=self.CHUNK_SIZE,
                    encoder_chunk_look_back=4,
                    decoder_chunk_look_back=1
                )
                if result and len(result) > 0:
                    return result[0].get('text', '') or ''
                return ''
        except Exception as e:
            logger.error("流式转写异常: %s", e)
            return ''  # 返回空字符串，不中断循环
    
    def reset(self):
        """重置流式状态，开始新会话（线程安全）"""
        with self._lock:
            self._cache = {}
```

### 4.3 StreamingTranscriber

```python
class StreamingTranscriber:
    """流式转写器
    
    从音频队列消费 → chunk 切分 → 流式引擎转写 → 输出队列解耦
    
    线程模型：
    - _run() 在独立线程中运行
    - _inject_worker() 在另一个线程中运行，解耦回调阻塞
    
    注意：buffer 使用实例变量 self._buffer，确保 stop() 可访问剩余数据。
    """
    
    def __init__(self, config):
        self._config = config
        self._running = False
        self._thread = None
        self._inject_thread = None
        self._inject_queue = queue.Queue(maxsize=20)  # 解耦注入，容量提升
        self._buffer = []  # 实例变量，确保 stop() 可访问
        self._buffer_lock = threading.Lock()  # 保护 buffer
    
    def start(self, audio_queue: queue.Queue, engine: StreamingEngineProtocol, on_segment):
        """启动流式转写
        
        Args:
            audio_queue: AudioRecorder 的输出队列（maxsize 由创建者设置）
            engine: 流式引擎实例（实现 StreamingEngineProtocol）
            on_segment: 文本输出回调
        """
        self._audio_queue = audio_queue
        self._engine = engine
        self._on_segment = on_segment
        self._chunk_samples = engine.get_chunk_samples()  # 从引擎获取
        with self._buffer_lock:
            self._buffer = []  # 重置
        self._running = True
        
        # 启动转写线程
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        
        # 启动注入线程（解耦回调阻塞）
        self._inject_thread = threading.Thread(target=self._inject_worker, daemon=True)
        self._inject_thread.start()
    
    def stop(self):
        """停止流式转写，处理剩余音频"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        # 处理剩余 buffer（线程安全）
        with self._buffer_lock:
            if self._buffer:
                audio_chunk = np.array(self._buffer)
                self._buffer = []
                text = self._engine.transcribe_chunk(audio_chunk, is_final=True)
                if text:
                    self._put_inject(text)
        # 等待注入队列清空
        self._inject_queue.put(None)  # sentinel
        if self._inject_thread:
            self._inject_thread.join(timeout=3)
    
    def _inject_worker(self):
        """独立注入线程，避免阻塞转写循环"""
        while True:
            text = self._inject_queue.get()
            if text is None:
                break
            try:
                self._on_segment(text)
            except Exception as e:
                logger.error("注入回调异常: %s", e)
    
    def _run(self):
        """主循环：累积够 chunk_samples 就处理
        
        注意：buffer 使用 self._buffer 实例变量，而非局部变量，
        确保 stop() 能正确处理剩余音频。
        """
        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.5)
                with self._buffer_lock:
                    self._buffer.extend(chunk)
            except queue.Empty:
                # 超时时如果 buffer 非空，也触发一次处理
                with self._buffer_lock:
                    if len(self._buffer) >= self._chunk_samples:
                        self._flush_chunk()
                continue
            
            # 累积够 chunk_samples 就处理
            with self._buffer_lock:
                while len(self._buffer) >= self._chunk_samples:
                    self._flush_chunk()
    
    def _flush_chunk(self):
        """处理一个 chunk（调用前需持有 _buffer_lock）"""
        audio_chunk = np.array(self._buffer[:self._chunk_samples])
        self._buffer = self._buffer[self._chunk_samples:]
        text = self._engine.transcribe_chunk(audio_chunk, is_final=False)
        if text:
            self._put_inject(text)
    
    def _put_inject(self, text: str):
        """放入注入队列（非阻塞，满时丢弃最旧的）
        
        使用 drop_old 策略，丢弃旧文本而非新文本。
        """
        try:
            if self._inject_queue.full():
                # 队列满时，先取出一个旧的再放入新的
                try:
                    self._inject_queue.get_nowait()
                except queue.Empty:
                    pass
            self._inject_queue.put_nowait(text)
        except Exception as e:
            logger.warning("注入队列异常: %s", e)
```

---

## 5. 配置设计

### 5.1 新增 StreamingConfig

```python
@dataclass
class StreamingConfig:
    """流式转写配置"""
    enabled: bool = True              # 是否启用流式
    max_queue_size: int = 300         # 音频队列上限（chunk 数）
    overflow_strategy: str = "drop_old"  # 队列溢出策略：drop_old | block
    
    # 注意：chunk_size 和 lookahead 是模型硬性参数，不可配置
    # FunASR streaming 固定使用 [0, 10, 5] = 600ms + 300ms lookahead
```

### 5.2 config.yaml 示例

```yaml
stt:
  engine: funasr
  model_size: paraformer-zh-streaming
  streaming:
    enabled: true
```

---

## 6. 实现计划

### Phase 1: 基础组件（预计 2小时）
1. 创建 `core/stt_funasr_streaming.py`
2. 创建 `core/streaming_transcriber.py`
3. 重命名 `stream_transcriber.py` → `vad_segment_transcriber.py`

### Phase 2: 配置与集成（预计 1小时）
1. 更新 `config.py` 新增 StreamingConfig
2. 更新 `engine.py` 引擎选择逻辑
3. 创建音频队列时应用 max_queue_size

### Phase 3: 测试验证（预计 1小时）
1. 在 Windows 开发机测试流式模式
2. 验证降级逻辑（VAD 分段模式）

### Phase 4: 打包发布
1. 更新 PyInstaller hook
2. CI 构建 + 发布

---

## 7. 关键技术点

### 7.1 FunASR Lookahead 机制

300ms 后瞻确保不会切断音节边界：
- 模型处理 600ms chunk 时，同时查看后面 300ms
- 利用后瞻信息判断字边界
- 跨 chunk 的 cache 保持语音上下文

### 7.2 音频采样率

FunASR 流式模型要求 16kHz：
- AudioRecorder 已使用 16kHz，无需修改
- chunk 大小固定：16000 * 0.6 = 9600 samples

---

## 8. 依赖说明

**Python 依赖版本**：
- `funasr >= 1.0`（支持流式 API）
- `numpy >= 1.20`
- `threading`（标准库）
- `queue`（标准库）

**audio_queue 契约**：
- 创建者：CoreEngine 在进入 STREAMING 状态时创建
- maxsize：由 `StreamingConfig.max_queue_size` 设置（默认 300）
- 数据格式：numpy.ndarray，16kHz 单声道 float32
- 消费者：StreamingTranscriber

---

## 9. 风险与降级

| 风险 | 应对 |
|------|------|
| 流式模型加载失败 | 自动降级到 VAD 分段模式 |
| chunk 处理超时 | 跳过当前 chunk，继续下一个 |
| 音频队列阻塞 | 设置队列上限，丢弃旧数据 |
| 注入队列阻塞 | drop_old 策略，丢弃旧文本 |

---

## 10. 验收标准

- [ ] 流式模式启动成功（paraformer-zh-streaming 模型加载）
- [ ] 600ms chunk 持续输出文本
- [ ] 结束时剩余音频正确处理（is_final=True）
- [ ] 配置 `streaming.enabled=false` 时降级到 VAD 分段
- [ ] faster-whisper 模式仍使用 VAD 分段
- [ ] cache 状态线程安全（Lock 保护）
- [ ] buffer 状态线程安全（_buffer_lock 保护）
- [ ] transcribe_chunk 异常不中断循环
- [ ] queue.get 超时时 buffer 正确处理
- [ ] 模型名与 streaming.enabled 交叉验证
- [ ] stop() 正确处理剩余音频（self._buffer 可访问）

---

## 11. 配置验证

启动时校验配置一致性：
```python
def validate_streaming_config(config):
    if config.stt.streaming.enabled:
        if config.stt.engine != 'funasr':
            logger.warning("streaming.enabled=true 仅支持 FunASR，自动禁用")
            config.stt.streaming.enabled = False
        if config.stt.model_size != 'paraformer-zh-streaming':
            logger.warning("流式模式建议使用 paraformer-zh-streaming 模型")
```

---

*设计确认时间: 2026-04-21 00:41*
*v2 修订时间: 2026-04-21 00:50（评审第一轮问题修复）*
*v3 修订时间: 2026-04-21 00:52（评审第二轮问题修复：buffer一致性问题）*