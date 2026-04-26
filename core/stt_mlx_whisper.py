"""mlx-whisper 分段转写引擎

macOS 专用，利用 Apple Silicon MLX 加速。
接口与 STTEngine（faster-whisper）兼容，供 VADSegmentTranscriber 调用。

依赖：pip install mlx-whisper
平台：仅 macOS Apple Silicon
"""

import os
import platform
import time
import threading
import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, Future

import numpy as np

logger = logging.getLogger(__name__)


class MlxWhisperEngine:
    """mlx-whisper 分段转写引擎

    使用 ThreadPoolExecutor(max_workers=1) 保证单任务执行。
    异步提交转写任务，通过回调返回结果。

    Args:
        config: STTConfig 实例
    """

    def __init__(self, config):
        self.config = config
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._current_future: Optional[Future] = None
        self._lock = threading.Lock()

    @staticmethod
    def is_available() -> bool:
        """检查 mlx-whisper 是否可用（macOS Apple Silicon only）"""
        if platform.system() != 'Darwin':
            return False
        try:
            import mlx_whisper
            return True
        except ImportError:
            return False

    @staticmethod
    def get_model_repo(model_size: str) -> str:
        """将 faster-whisper 模型名映射到 MLX 格式仓库名"""
        mlx_map = {
            'tiny': 'mlx-community/whisper-tiny',
            'base': 'mlx-community/whisper-base-mlx',
            'small': 'mlx-community/whisper-small-mlx',
            'medium': 'mlx-community/whisper-medium-mlx',
            'large-v3': 'mlx-community/whisper-large-v3-mlx',
            'large-v3-turbo': 'mlx-community/whisper-large-v3-turbo',
        }
        return mlx_map.get(model_size, 'mlx-community/whisper-large-v3-turbo')

    def load_model(self) -> tuple[bool, str]:
        """加载/验证模型（mlx-whisper 首次调用时自动下载 MLX 格式模型）

        Returns:
            (True, '') 成功, (False, '错误信息') 失败
        """
        try:
            import mlx_whisper  # noqa: F401
        except ImportError:
            return False, 'mlx_whisper 未安装。macOS 上请运行: pip install mlx-whisper'

        # 验证：用极短静音测试（不触发完整下载，仅检查 import 正常）
        # 模型在首次 transcribe_sync 时才真正加载/下载
        logger.info("mlx-whisper 引擎就绪，模型将在首次转写时加载")
        return True, ''

    def transcribe_async(self, audio: np.ndarray, callback):
        """异步转写：提交到线程池，完成后回调 callback(text, language, duration_ms, error)"""
        with self._lock:
            if self._current_future and not self._current_future.done():
                callback("", None, 0, RuntimeError("STT 正忙"))
                return

            self._current_future = self._executor.submit(self._do_transcribe, audio)
            self._current_future.add_done_callback(
                lambda f: callback(*self._unpack_result(f))
            )

    def _do_transcribe(self, audio: np.ndarray) -> tuple:
        """实际转写逻辑（线程池中执行）

        Returns:
            (text, language, duration_ms, error) 四元组
        """
        try:
            if audio.size == 0 or len(audio) < 3200:  # <0.2s
                return ("", None, 0, None)

            import mlx_whisper

            t0 = time.monotonic()
            model_repo = self.get_model_repo(self.config.model_size)

            language = self.config.language
            if language == 'auto' or language is None:
                language = None  # mlx-whisper 自动检测

            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=model_repo,
                language=language,
                verbose=False
            )

            duration_ms = int((time.monotonic() - t0) * 1000)

            # 提取文本
            text = result.get('text', '') or ''
            detected_lang = result.get('language', None)

            # 繁简转换（中文）
            if detected_lang == 'zh' and text:
                try:
                    from opencc import OpenCC
                    cc = OpenCC('t2s')
                    text = cc.convert(text)
                except ImportError:
                    pass

            return (text.strip() if text else "", detected_lang, duration_ms, None)
        except Exception as e:
            return ("", None, 0, e)

    def _unpack_result(self, future: Future) -> tuple:
        """解包 Future 结果"""
        try:
            return future.result()
        except Exception as e:
            return ("", None, 0, e)

    def transcribe_sync(self, audio: np.ndarray) -> str:
        """同步转写（阻塞式），供 VADSegmentTranscriber 调用

        Args:
            audio: numpy 音频数组 (16000Hz, float32)

        Returns:
            识别文本（空字符串表示无结果）
        """
        text, _, _, _ = self._do_transcribe(audio)
        return text

    def shutdown(self):
        """关闭线程池"""
        self._executor.shutdown(wait=True)
        logger.info("mlx-whisper STT 引擎已关闭")
