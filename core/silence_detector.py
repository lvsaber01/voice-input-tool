"""静音检测模块

职责：独立消费者线程，检测录音中的静音超时。
Phase 1 骨架，Phase 2 实现完整逻辑。
"""

import time
import queue
import threading
import logging

import numpy as np

logger = logging.getLogger(__name__)


class SilenceDetector:
    """静音检测器。

    持续运行在独立消费者线程中，通过 _detecting flag 控制是否执行检测。
    CoreEngine 只需设置 flag，无需 join 线程，避免死锁。

    Args:
        config: AudioConfig 实例
        on_silence_timeout: 静音超时回调（由 CoreEngine 提供）
    """

    def __init__(self, config, on_silence_timeout, on_rms_update=None):
        self.config = config
        self._on_silence_timeout = on_silence_timeout
        self._on_rms_update = on_rms_update  # RMS 更新回调 (rms, is_speech)
        self._queue: queue.Queue = None
        self._running = True
        self._detecting = False
        self._last_sound_time = 0.0
        self._last_rms_publish = 0.0
        self._rms_throttle_interval = 0.1  # 100ms 节流
        self._thread: threading.Thread = None

    def start(self, buffer_queue: queue.Queue):
        """启动消费者线程（程序启动时调用一次）"""
        self._queue = buffer_queue
        self._thread = threading.Thread(target=self._run, name="silence-detector", daemon=True)
        self._thread.start()
        logger.info("静音检测线程已启动")

    def begin_detection(self):
        """开始一次静音检测（每次录音开始时调用）"""
        self._last_sound_time = time.monotonic()
        self._detecting = True
        logger.debug("静音检测开始")

    def end_detection(self):
        """结束静音检测（录音停止时调用，只设 flag 不 join）"""
        self._detecting = False
        logger.debug("静音检测结束")

    def shutdown(self):
        """关闭线程（程序退出时调用）"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("静音检测线程已关闭")

    def _run(self):
        """消费者主循环（持续运行，不自行退出）"""
        self._last_sound_time = time.monotonic()

        while self._running:
            try:
                chunk = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not self._detecting:
                continue

            rms = self._calculate_rms(chunk)
            is_speech = rms >= self.config.silence_threshold

            # RMS 回调（带节流）
            if self._on_rms_update:
                now = time.monotonic()
                if now - self._last_rms_publish >= self._rms_throttle_interval:
                    self._on_rms_update(rms, is_speech)
                    self._last_rms_publish = now

            if is_speech:
                self._last_sound_time = time.monotonic()
            else:
                silence_duration = time.monotonic() - self._last_sound_time
                if self.config.silence_timeout > 0 and silence_duration >= self.config.silence_timeout:
                    self._detecting = False
                    logger.info("静音超时 (%.1fs)", silence_duration)
                    self._on_silence_timeout()

    @staticmethod
    def _calculate_rms(data: np.ndarray) -> float:
        """计算音频 RMS 能量"""
        return float(np.sqrt(np.mean(data ** 2)))
