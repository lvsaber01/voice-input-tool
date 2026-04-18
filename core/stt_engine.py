"""STT 引擎封装模块

职责：加载 faster-whisper 模型，异步转写音频。
Phase 1 骨架，Phase 2 实现完整逻辑。
"""

import os
import threading
import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, Future

import numpy as np

logger = logging.getLogger(__name__)


class STTEngine:
    """语音识别引擎。

    使用 ThreadPoolExecutor(max_workers=1) 保证单任务执行。
    异步提交转写任务，通过回调返回结果。

    Args:
        config: STTConfig 实例
    """

    def __init__(self, config):
        self.config = config
        self.model = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._current_future: Optional[Future] = None
        self._lock = threading.Lock()

    def load_model(self) -> bool:
        """加载 faster-whisper 模型（耗时操作）。

        Returns:
            True=成功, False=失败
        """
        try:
            from faster_whisper import WhisperModel

            device = self._detect_device()
            compute_type = self._detect_compute_type(device)
            # 拼接完整模型路径: model_path + model_size
            model_path = self.config.model_path
            if not model_path.endswith(('tiny', 'base', 'small', 'medium', 'large-v3')):
                model_path = os.path.join(model_path, self.config.model_size)
            logger.info("加载模型: %s (path=%s), device=%s, compute_type=%s",
                        self.config.model_size, model_path, device, compute_type)

            self.model = WhisperModel(
                model_size_or_path=model_path,
                device=device,
                compute_type=compute_type,
            )
            logger.info("模型加载完成")
            return True
        except Exception as e:
            logger.error("模型加载失败: %s", e)
            return False

    def transcribe_async(self, audio: np.ndarray, callback):
        """异步转写：提交到线程池，完成后回调 callback(text, error)"""
        with self._lock:
            if self._current_future and not self._current_future.done():
                callback("", RuntimeError("STT 正忙"))
                return

            self._current_future = self._executor.submit(self._do_transcribe, audio)
            self._current_future.add_done_callback(
                lambda f: callback(*self._unpack_result(f))
            )

    def _do_transcribe(self, audio: np.ndarray) -> tuple:
        """实际转写逻辑（线程池中执行）"""
        try:
            if audio.size == 0:
                return ("", None)
            if len(audio) < 3200:  # <0.2s 音频
                return ("", None)

            segments, info = self.model.transcribe(
                audio,
                language=self.config.language,
                beam_size=self.config.beam_size,
                vad_filter=True,
            )
            text = " ".join(seg.text for seg in segments).strip()
            return (text if text else "", None)
        except Exception as e:
            return ("", e)

    def _unpack_result(self, future: Future) -> tuple:
        """解包 Future 结果"""
        try:
            return future.result()
        except Exception as e:
            return ("", e)

    def _detect_device(self) -> str:
        """自动检测计算设备"""
        if self.config.device != "auto":
            return self.config.device
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda"
        except Exception:
            pass
        return "cpu"

    def _detect_compute_type(self, device: str) -> str:
        """根据设备选择计算精度"""
        if device == "cuda":
            return "float16"
        return self.config.compute_type

    def shutdown(self):
        """关闭线程池"""
        self._executor.shutdown(wait=True)
        logger.info("STT 引擎已关闭")
