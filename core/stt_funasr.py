"""FunASR Paraformer STT 引擎

阿里达摩院 Paraformer 模型，CPU 上极快（10x 实时速度）。
中文识别准确率高于 Whisper，支持中英文混合。
"""

import time
import threading
import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, Future

import numpy as np

logger = logging.getLogger(__name__)


class FunASREngine:
    """FunASR Paraformer 语音识别引擎。

    使用 ThreadPoolExecutor(max_workers=1) 保证单任务执行。
    与 STTEngine 相同的接口：load_model / transcribe_async / shutdown。
    """

    def __init__(self, config):
        self.config = config
        self.model = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._current_future: Optional[Future] = None
        self._lock = threading.Lock()

    def load_model(self) -> bool:
        """加载 FunASR Paraformer 模型"""
        try:
            from funasr import AutoModel

            # 从配置获取模型名称
            model_name = self.config.model_size or "paraformer-zh"
            logger.info("加载 FunASR 模型: %s", model_name)

            self.model = AutoModel(
                model=model_name,
                device="cpu",
                disable_update=True,
            )
            logger.info("FunASR 模型加载完成")
            return True
        except ImportError:
            logger.error("funasr 未安装。请运行: pip install funasr modelscope")
            return False
        except Exception as e:
            logger.error("FunASR 模型加载失败: %s", e)
            return False

    def transcribe_async(self, audio: np.ndarray, callback):
        """异步转写：提交到线程池，完成后回调 callback(text, language, duration_ms, error)"""
        with self._lock:
            if self._current_future and not self._current_future.done():
                logger.warning("STT 正忙，跳过本次转写")
                callback("", None, 0, RuntimeError("STT 正忙"))
                return

            self._current_future = self._executor.submit(self._do_transcribe, audio)
            self._current_future.add_done_callback(
                lambda f: callback(*self._unpack_result(f))
            )

    def _do_transcribe(self, audio: np.ndarray) -> tuple:
        """实际转写逻辑（线程池中执行）。

        Returns:
            (text, language, duration_ms, error) 四元组
        """
        try:
            if audio.size == 0 or len(audio) < 3200:  # <0.2s
                return ("", None, 0, None)

            t0 = time.monotonic()

            # FunASR 需要 16kHz float32 音频
            result = self.model.generate(
                input=audio,
                batch_size_s=300,
            )

            duration_ms = int((time.monotonic() - t0) * 1000)

            # 提取文本
            text = ""
            if result and len(result) > 0:
                item = result[0]
                if isinstance(item, dict):
                    text = item.get("text", "")
                elif hasattr(item, "text"):
                    text = item.text
                elif isinstance(item, str):
                    text = item

            text = text.strip()
            logger.info("FunASR 转写完成: '%s' (%dms)", text[:50], duration_ms)
            return (text, "zh", duration_ms, None)

        except Exception as e:
            logger.error("FunASR 转写失败: %s", e)
            return ("", None, 0, e)

    @staticmethod
    def _unpack_result(future: Future) -> tuple:
        """解包 Future 结果，处理异常"""
        try:
            return future.result()
        except Exception as e:
            return ("", None, 0, e)

    def transcribe_sync(self, audio) -> str:
        """同步转写（阻塞式），保证顺序输出"""
        result = [None]

        def callback(text, language, duration_ms, error):
            result[0] = (text, language, duration_ms, error)

        with self._lock:
            future = self._executor.submit(self._do_transcribe, audio)
            future.add_done_callback(lambda f: callback(*self._unpack_result(f)))

        future.result(timeout=30)
        if result[0]:
            return result[0][0] or ""
        return ""

    def shutdown(self):
        """关闭线程池"""
        self._executor.shutdown(wait=True)
        logger.info("FunASR 引擎已关闭")
