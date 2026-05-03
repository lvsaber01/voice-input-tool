"""流式转写器

从音频队列消费 → chunk 切分 → 流式引擎转写 → 输出队列解耦

线程模型：
- _run() 在独立线程中运行
- _inject_worker() 在另一个线程中运行，解耦回调阻塞
- _buffer + _buffer_lock 线程安全
"""

import queue
import threading
import logging
import time
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


class StreamingTranscriber:
    """流式转写器
    
    从音频队列消费 → chunk 切分 → 流式引擎转写 → 输出队列解耦
    
    线程模型：
    - _run() 在独立线程中运行
    - _inject_worker() 在另一个线程中运行，解耦回调阻塞
    
    注意：buffer 使用实例变量 self._buffer，确保 stop() 可访问剩余数据。
    """
    
    def __init__(self, config):
        """初始化流式转写器
        
        Args:
            config: StreamingConfig 实例
        """
        self._config = config
        self._running = False
        self._paused = False  # 暂停标志
        self._thread: Optional[threading.Thread] = None
        self._inject_thread: Optional[threading.Thread] = None
        self._inject_queue = queue.Queue(maxsize=20)  # 解耦注入，容量提升
        self._buffer = []  # 实例变量，确保 stop() 可访问
        self._buffer_lock = threading.Lock()  # 保护 buffer
        
        # 外部依赖（由 start() 设置）
        self._audio_queue: Optional[queue.Queue] = None
        self._engine = None
        self._on_segment: Optional[Callable] = None
        self._chunk_samples = 9600  # 默认值

        # 采样率相关（由 start() 初始化）
        self._source_sr = 16000
        self._resampler = None
        
        # 统计
        self._chunks_count = 0
        self._texts_count = 0
    
    def start(self, audio_queue: queue.Queue, engine, on_segment: Callable, resampler=None):
        """启动流式转写

        Args:
            audio_queue: AudioRecorder 的输出队列（maxsize 由创建者设置）
            engine: 流式引擎实例（实现 get_chunk_samples 和 transcribe_chunk）
            on_segment: 文本输出回调
            resampler: AudioResampler 实例（可选，源采样率 ≠ 16000 时传入）
        """
        self._audio_queue = audio_queue
        self._engine = engine
        self._on_segment = on_segment
        self._resampler = resampler

        # 引擎期望的 chunk 时长（ms）
        chunk_ms = 600  # FunASRStreamingEngine.CHUNK_MS
        if hasattr(engine, 'CHUNK_MS'):
            chunk_ms = engine.CHUNK_MS

        # chunk_samples 根据源采样率动态计算
        if resampler is not None:
            self._source_sr = resampler.source_sr
            # 引擎期望的是 600ms @16kHz = 9600 样本
            # 但我们累积的是源采样率的样本，需要累积到相同时长
            self._chunk_samples = int(chunk_ms * self._source_sr / 1000)
            logger.info("StreamingTranscriber: 源采样率=%d, chunk=%d样本(%.0fms), 需resample后送入引擎",
                       self._source_sr, self._chunk_samples, chunk_ms)
        else:
            self._source_sr = 16000
            self._chunk_samples = engine.get_chunk_samples()
            logger.info("StreamingTranscriber: 默认16kHz, chunk=%d样本", self._chunk_samples)
        
        with self._buffer_lock:
            self._buffer = []  # 重置
        
        self._chunks_count = 0
        self._texts_count = 0
        self._running = True
        
        # 重置引擎状态
        if hasattr(engine, 'reset'):
            engine.reset()
        
        # 启动转写线程
        self._thread = threading.Thread(target=self._run, daemon=True, name="StreamingTranscriber")
        self._thread.start()
        
        # 启动注入线程（解耦回调阻塞）
        self._inject_thread = threading.Thread(target=self._inject_worker, daemon=True, name="InjectWorker")
        self._inject_thread.start()
        
        logger.info("流式转写器已启动 (chunk_samples=%d)", self._chunk_samples)

    def pause(self):
        """暂停转写处理（音频仍会采集但不送入引擎）。"""
        self._paused = True
        logger.debug("流式转写器已暂停")

    def resume(self):
        """恢复转写处理。"""
        self._paused = False
        logger.debug("流式转写器已恢复")

    def stop(self):
        """停止流式转写，处理剩余音频"""
        self._running = False
        
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        
        # 处理剩余 buffer（线程安全）
        with self._buffer_lock:
            if self._buffer and self._engine:
                audio_chunk = np.array(self._buffer)
                self._buffer = []

                # resample 剩余音频再送入引擎
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
        
        # 等待注入队列清空
        try:
            self._inject_queue.put(None, timeout=2)  # sentinel
        except queue.Full:
            logger.warning("注入队列满，跳过sentinel")
        
        if self._inject_thread:
            self._inject_thread.join(timeout=3)
            self._inject_thread = None
        
        logger.info("流式转写器已停止 (chunks=%d, texts=%d)", self._chunks_count, self._texts_count)
    
    def _inject_worker(self):
        """独立注入线程，避免阻塞转写循环"""
        while True:
            try:
                text = self._inject_queue.get(timeout=1)
            except queue.Empty:
                if not self._running:
                    break
                continue
            
            if text is None:
                break
            
            try:
                if self._on_segment:
                    self._on_segment(text)
            except Exception as e:
                logger.error("注入回调异常: %s", e)
    
    def _run(self):
        """主循环：累积够 chunk_samples 就处理
        
        注意：buffer 使用 self._buffer 实例变量，而非局部变量，
        确保 stop() 能正确处理剩余音频。
        """
        logger.debug("流式转写主循环开始")
        
        while self._running:
            if self._paused:
                # 暂停时丢弃队列中的音频，防止恢复后积压
                try:
                    self._audio_queue.get(timeout=0.1)
                except queue.Empty:
                    pass
                continue
            try:
                chunk = self._audio_queue.get(timeout=0.5)
                with self._buffer_lock:
                    self._buffer.extend(chunk)
            except queue.Empty:
                # 超时时如果 buffer 非空，也触发一次检查
                with self._buffer_lock:
                    if len(self._buffer) >= self._chunk_samples:
                        self._flush_chunk()
                continue
            except Exception as e:
                logger.error("音频队列读取异常: %s", e)
                continue
            
            # 累积够 chunk_samples 就处理
            with self._buffer_lock:
                while len(self._buffer) >= self._chunk_samples:
                    self._flush_chunk()
        
        logger.debug("流式转写主循环结束")
    
    def _flush_chunk(self):
        """处理一个 chunk（调用前需持有 _buffer_lock）"""
        if not self._engine:
            return
        
        audio_chunk = np.array(self._buffer[:self._chunk_samples])
        self._buffer = self._buffer[self._chunk_samples:]
        self._chunks_count += 1

        # resample 到 16kHz 后再送入引擎
        if self._resampler and self._resampler.needs_resample and len(audio_chunk) > 0:
            audio_chunk = self._resampler.resample(audio_chunk)

        # 对齐 resample 后的 chunk 长度到引擎期望值
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
    
    def _put_inject(self, text: str):
        """放入注入队列（非阻塞，满时丢弃最旧的）
        
        使用 drop_old 策略，丢弃旧文本而非新文本。
        """
        try:
            if self._inject_queue.full():
                # 队列满时，先取出一个旧的再放入新的
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