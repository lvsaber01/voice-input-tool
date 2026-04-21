"""STT 引擎封装模块

职责：加载 faster-whisper 模型，异步转写音频。
Phase 1 骨架，Phase 2 实现完整逻辑。
"""

import os
import time
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

    def load_model(self) -> tuple[bool, str]:
        """加载 faster-whisper 模型（耗时操作）。

        Returns:
            (True, '') 成功, (False, '错误信息') 失败
        """
        try:
            # 设置 HuggingFace 镜像（解决中国用户访问超时问题）
            hf_endpoint = getattr(self.config, 'hf_endpoint', '')
            if hf_endpoint:
                os.environ['HF_ENDPOINT'] = hf_endpoint
                logger.info("使用 HuggingFace 镜像: %s", hf_endpoint)

            from faster_whisper import WhisperModel

            device = self._detect_device()
            compute_type = self._detect_compute_type(device)
            # 拼接完整模型路径: model_path + model_size
            model_path = self.config.model_path
            download_root = None
            # 如果 model_path 是默认值（目录），直接用 model_size 让 faster-whisper 自动找缓存
            # 只有用户指定了实际模型目录时才拼接
            if os.path.isdir(os.path.join(model_path, 'model.bin')) or \
               os.path.isdir(os.path.join(model_path, self.config.model_size, 'model.bin')):
                # 本地已有模型文件，拼接完整路径
                if not os.path.isdir(os.path.join(model_path, 'model.bin')):
                    model_path = os.path.join(model_path, self.config.model_size)
            elif model_path and model_path not in ('./models/', './models', 'models/', 'models'):
                # 用户指定了非默认路径
                model_path = os.path.join(model_path, self.config.model_size)
            else:
                # 默认路径：强制下载到本地 models/ 目录
                model_path = self.config.model_size
                models_dir = os.environ.get('VOICE_INPUT_TOOL_MODELS', os.path.abspath('./models'))
                os.makedirs(models_dir, exist_ok=True)
                download_root = models_dir
                logger.info("模型将下载到: %s", models_dir)
            logger.info("加载模型: %s (path=%s), device=%s, compute_type=%s",
                        self.config.model_size, model_path, device, compute_type)

            self.model = WhisperModel(
                model_size_or_path=model_path,
                device=device,
                compute_type=compute_type,
                download_root=download_root,
            )
            logger.info("模型加载完成")
            return True, ''
        except Exception as e:
            error_msg = str(e)
            logger.error("模型加载失败: %s", error_msg)
            return False, error_msg

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
        """实际转写逻辑（线程池中执行）。

        Returns:
            (text, language, duration_ms, error) 四元组
        """
        try:
            if audio.size == 0 or len(audio) < 3200:  # <0.2s 音频
                return ("", None, 0, None)

            t0 = time.monotonic()
            segments, info = self.model.transcribe(
                audio,
                language=self.config.language,
                beam_size=self.config.beam_size,
                vad_filter=True,
            )
            text = " ".join(seg.text for seg in segments).strip()
            duration_ms = int((time.monotonic() - t0) * 1000)
            language = getattr(info, 'language', None)
            # 繁体转简体（faster-whisper 多语言模型倾向输出繁体中文）
            if language == 'zh' and text:
                try:
                    from opencc import OpenCC
                    cc = OpenCC('t2s')  # traditional to simplified
                    text = cc.convert(text)
                except ImportError:
                    pass
            return (text if text else "", language, duration_ms, None)
        except Exception as e:
            return ("", None, 0, e)

    def _unpack_result(self, future: Future) -> tuple:
        """解包 Future 结果。

        Returns:
            (text, language, duration_ms, error) 四元组
        """
        try:
            return future.result()
        except Exception as e:
            return ("", None, 0, e)

    def _detect_device(self) -> str:
        """自动检测计算设备
        
        默认 CPU，除非明确配置为 cuda。
        ctranslate2.get_cuda_device_count() 可能检测到驱动但缺少 cublas 运行时库。
        """
        if self.config.device != "auto":
            return self.config.device
        # auto 模式默认 CPU，避免 cublas 缺失问题
        # 用户可在 config.yaml 中显式设置 device: cuda
        return "cpu"

    def _detect_compute_type(self, device: str) -> str:
        """根据设备选择计算精度"""
        if device == "cuda":
            return "float16"
        return self.config.compute_type

    def transcribe_sync(self, audio) -> str:
        """同步转写（阻塞式），保证顺序输出。

        用于实时转写模式，区别于 transcribe_async 的线程池异步模式。

        Args:
            audio: numpy 音频数组

        Returns:
            识别文本（空字符串表示无结果）
        """
        text, _, _, _ = self._do_transcribe(audio)
        return text

    def shutdown(self):
        """关闭线程池"""
        self._executor.shutdown(wait=True)
        logger.info("STT 引擎已关闭")
