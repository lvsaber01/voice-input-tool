"""音频录制模块

职责：使用 sounddevice 采集音频，缓冲管理。
Phase 1 骨架，Phase 2 实现完整逻辑。
"""

import queue
import logging
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


class AudioRecorder:
    """音频录制器。

    使用 sounddevice.InputStream 以 16kHz 单声道采集音频，
    通过 deque 缓冲主数据，通过 Queue 传递给静音检测线程。

    ⚠ _audio_callback 在实时音频线程中执行，只做数据拷贝。
    """

    SAMPLE_RATE = 16000  # 常量，Whisper 要求 16kHz

    def __init__(self, config):
        self.config = config
        self.is_recording = False
        self._buffer: deque = deque()
        self._buffer_queue: queue.Queue = queue.Queue()
        self._stream = None

    def start(self):
        """开始录音"""
        import sounddevice as sd

        self._buffer.clear()
        # 清空缓冲队列（丢弃旧数据）
        while not self._buffer_queue.empty():
            try:
                self._buffer_queue.get_nowait()
            except queue.Empty:
                break

        self._stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=self._audio_callback,
        )
        self._stream.start()
        self.is_recording = True
        logger.info("开始录音，采样率: %d", self.SAMPLE_RATE)

    def stop(self) -> np.ndarray:
        """停止录音，返回完整 PCM 数据"""
        self.is_recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.warning("关闭音频流异常: %s", e)
            self._stream = None
        logger.info("录音停止")
        if self._buffer:
            return np.concatenate(list(self._buffer))
        return np.array([], dtype=np.float32)

    def _audio_callback(self, indata, frames, time_info, status):
        """sounddevice 回调（实时线程）

        ⚠ 关键约束：此方法在实时音频线程中执行，只做数据拷贝！
        """
        chunk = indata.copy()
        self._buffer.append(chunk)
        self._buffer_queue.put(chunk)

    def get_buffer_queue(self) -> queue.Queue:
        """返回缓冲队列，供静音检测线程消费"""
        return self._buffer_queue
