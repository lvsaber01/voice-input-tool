"""实时转写引擎

从 AudioRecorder 的专用队列持续消费音频，
使用 webrtcvad 检测句子边界，分段送入 STT 转写，
通过独立注入队列逐段输出文字。

线程模型：
- 主线程（_run）：消费音频 + VAD 分段 + STT 调用
- 注入线程（_inject_worker）：独立执行文字注入，避免阻塞转写
"""

import time
import queue
import logging
import threading
from typing import Optional, Callable

import numpy as np

logger = logging.getLogger(__name__)

# 采样率
SAMPLE_RATE = 16000


class VADSegmentTranscriber:
    """VAD 分段转写引擎（降级备用）。

    职责：
    - 从 audio_queue 持续消费音频
    - 使用 webrtcvad 检测语音段边界
    - 将分段音频送入 STT Engine 转写
    - 通过独立注入队列输出文字

    与批量模式的区别：
    - 批量模式：CoreEngine 手动触发 start/stop，一次性转写
    - 实时模式：StreamTranscriber 自动循环分段，持续输出
    """

    def __init__(self, config, stt_engine, on_segment_transcribed: Callable):
        """
        Args:
            config: RealtimeConfig 实例
            stt_engine: STTEngine 实例（共享）
            on_segment_transcribed: callback(text: str)
                每转写完一段文字就调用，由 CoreEngine 负责注入
        """
        self._config = config
        self._stt_engine = stt_engine
        self._on_segment = on_segment_transcribed
        self._running = False
        self._thread = None
        self._inject_thread = None
        self._inject_queue = queue.Queue(maxsize=10)  # 注入解耦队列

        # VAD
        self._vad = None  # webrtcvad 实例

        # 音频缓冲
        self._speech_buffer = []  # 当前语音段
        self._silence_start = None  # 当前静音开始时间
        self._speech_duration = 0.0  # 当前语音段时长

        # 统计
        self._segments_count = 0

    def start(self, audio_queue: queue.Queue):
        """启动实时转写

        audio_queue: Recorder 的实时模式专用队列
        （IDLE→STREAMING 时创建，与批量模式 buffer_queue 分离，
         SilenceDetector 不消费此队列，避免竞态）
        """
        self._load_vad()
        self._running = True
        self._audio_queue = audio_queue
        self._speech_buffer = []
        self._silence_start = None
        self._speech_duration = 0.0
        self._segments_count = 0

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        # 启动独立注入线程（避免阻塞转写主循环）
        self._inject_thread = threading.Thread(target=self._inject_worker, daemon=True)
        self._inject_thread.start()

        logger.info("实时转写引擎已启动")

    def stop(self):
        """停止实时转写"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        # 处理缓冲区中剩余的语音
        if self._speech_buffer:
            self._flush_speech_buffer()
        # 等待注入队列清空
        if self._inject_thread:
            self._inject_queue.put(None)  # sentinel
            self._inject_thread.join(timeout=3)
        logger.info("实时转写引擎已停止，共转写 %d 段", self._segments_count)

    def _inject_worker(self):
        """独立注入线程，避免阻塞转写主循环"""
        while True:
            text = self._inject_queue.get()
            if text is None:
                break
            try:
                self._on_segment(text)
            except Exception as e:
                logger.error("注入失败: %s", e)

    def _load_vad(self):
        """加载 VAD 模型。
        
        优先 webrtcvad（更精确），不可用时降级为 RMS 能量检测。
        Python 3.14 不支持 pkg_resources（webrtcvad 依赖），自动降级。
        """
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad()
            self._vad.set_mode(self._config.vad_sensitivity)
            self._vad_mode = 'webrtcvad'
            logger.info("webrtcvad 加载完成，灵敏度: %d", self._config.vad_sensitivity)
        except (ImportError, Exception) as e:
            logger.warning("webrtcvad 不可用 (%s)，降级为 RMS 能量检测", e)
            self._vad = None
            self._vad_mode = 'rms'
            # RMS 能量阈值（经验值，0.01 对应静音阈值）
            self._rms_threshold = 0.015

    def _run(self):
        """实时转写主循环（带全局异常防护）"""
        try:
            self._run_inner()
        except Exception as e:
            logger.error("实时转写引擎异常退出: %s", e, exc_info=True)
        finally:
            logger.info("实时转写引擎已停止，共转写 %d 段", self._segments_count)

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
                self._speech_duration += len(chunk) / SAMPLE_RATE
                self._silence_start = None

                # 超长段强制切分
                if self._speech_duration >= self._config.max_segment_duration:
                    logger.debug("语音段达 %.1fs，强制切分", self._speech_duration)
                    self._flush_speech_buffer()
            else:
                if self._speech_buffer:
                    # 语音段中的静音
                    if self._silence_start is None:
                        self._silence_start = time.monotonic()
                    else:
                        silence_duration = time.monotonic() - self._silence_start
                        if silence_duration >= self._config.segment_pause_threshold:
                            # 句子边界，提交转写
                            self._flush_speech_buffer()

    def _vad_detect(self, audio_chunk: np.ndarray) -> bool:
        """判断是否为语音
        
        支持 webrtcvad（精确）和 RMS 能量检测（降级）两种模式。
        """
        if self._vad_mode == 'rms':
            return self._vad_detect_rms(audio_chunk)
        
        # webrtcvad 模式
        frame_length = int(self._config.vad_window_ms * SAMPLE_RATE / 1000)
        if len(audio_chunk) < frame_length:
            return False
        frame = (audio_chunk[-frame_length:] * 32767).astype(np.int16).tobytes()
        try:
            return self._vad.is_speech(frame, SAMPLE_RATE)
        except Exception:
            return False

    def _vad_detect_rms(self, audio_chunk: np.ndarray) -> bool:
        """RMS 能量检测（webrtcvad 降级方案）
        
        简单但有效：计算音频块 RMS 能量，超过阈值视为语音。
        """
        if len(audio_chunk) == 0:
            return False
        rms = float(np.sqrt(np.mean(audio_chunk ** 2)))
        return rms >= self._rms_threshold

    def _flush_speech_buffer(self):
        """将当前语音段送入 STT 转写并输出"""
        if not self._speech_buffer:
            return

        audio = np.concatenate(self._speech_buffer)
        self._speech_buffer = []
        self._speech_duration = 0.0
        self._silence_start = None

        # 超短段跳过
        min_samples = int(self._config.min_segment_duration * SAMPLE_RATE)
        if len(audio) < min_samples:
            return

        # 同步转写（在当前线程中）
        # transcribe_sync: 阻塞式转写，保证顺序输出
        # （区别于 transcribe_async 的线程池异步模式）
        try:
            text = self._stt_engine.transcribe_sync(audio)
            if text and text.strip():
                self._segments_count += 1
                # 通过独立队列解耦注入，避免阻塞转写线程
                try:
                    self._inject_queue.put_nowait(text)
                except queue.Full:
                    logger.warning("注入队列已满，丢弃段落")
        except Exception as e:
            logger.error("实时转写失败: %s", e)
