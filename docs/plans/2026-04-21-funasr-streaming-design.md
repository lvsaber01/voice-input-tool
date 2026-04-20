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

### 4.1 FunASRStreamingEngine

```python
class FunASRStreamingEngine:
    """FunASR 流式引擎
    
    chunk_size = [0, 10, 5] 表示：
    - 0: 前瞻块数
    - 10: 当前块 = 10 * 60ms = 600ms
    - 5: 后瞻块 = 5 * 60ms = 300ms lookahead
    """
    
    CHUNK_SIZE = [0, 10, 5]
    
    def __init__(self, config):
        self.config = config
        self.model = None
        self.cache = {}  # 流式状态缓存
    
    def load_model(self) -> tuple[bool, str]:
        """加载 paraformer-zh-streaming 模型"""
    
    def transcribe_chunk(self, audio_chunk: np.ndarray, is_final: bool) -> str:
        """流式转写 - 每600ms调用一次
        
        Args:
            audio_chunk: 9600 samples (600ms @ 16kHz)
            is_final: 最后一个chunk时True，强制输出
            
        Returns:
            增量文本（可能为空，等待更多chunk）
        """
    
    def reset(self):
        """重置流式状态，开始新会话"""
        self.cache = {}
```

### 4.2 StreamingTranscriber

```python
class StreamingTranscriber:
    """流式转写器
    
    从音频队列消费 → 固定 chunk 切分 → 流式引擎转写 → 输出回调
    """
    
    CHUNK_MS = 600  # FunASR streaming chunk 大小
    CHUNK_SAMPLES = 9600  # 16000 * 0.6
    
    def start(self, audio_queue, engine, on_segment):
        """启动流式转写"""
    
    def stop(self):
        """停止流式转写，处理剩余音频"""
    
    def _run(self):
        """主循环：累积够600ms就处理"""
        buffer = []
        while self._running:
            chunk = self._audio_queue.get(timeout=0.5)
            buffer.extend(chunk)
            
            # 累积够600ms就处理
            while len(buffer) >= self.CHUNK_SAMPLES:
                audio_chunk = np.array(buffer[:self.CHUNK_SAMPLES])
                buffer = buffer[self.CHUNK_SAMPLES:]
                
                text = self._engine.transcribe_chunk(audio_chunk, is_final=False)
                if text:
                    self._on_segment(text)
        
        # 结束时处理剩余
        if buffer:
            text = self._engine.transcribe_chunk(np.array(buffer), is_final=True)
            if text:
                self._on_segment(text)
```

---

## 5. 配置设计

### 5.1 新增 StreamingConfig

```python
@dataclass
class StreamingConfig:
    """流式转写配置（FunASR streaming 模式）"""
    enabled: bool = True              # 是否启用流式
    chunk_size_ms: int = 600          # chunk 大小（官方推荐）
    lookahead_ms: int = 300           # 后瞻大小（官方推荐）
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

## 8. 风险与降级

| 风险 | 应对 |
|------|------|
| 流式模型加载失败 | 自动降级到 VAD 分段模式 |
| chunk 处理超时 | 跳过当前 chunk，继续下一个 |
| 音频队列阻塞 | 设置队列上限，丢弃旧数据 |

---

## 9. 验收标准

- [ ] 流式模式启动成功（paraformer-zh-streaming 模型加载）
- [ ] 600ms chunk 持续输出文本
- [ ] 结束时剩余音频正确处理（is_final=True）
- [ ] 配置 `streaming.enabled=false` 时降级到 VAD 分段
- [ ] faster-whisper 模式仍使用 VAD 分段

---

*设计确认时间: 2026-04-21 00:41*