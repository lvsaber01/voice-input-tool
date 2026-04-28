"""Qwen3-ASR 分段转写引擎

使用 qwen-asr 官方 pip 包，支持 Qwen3-ASR-0.6B 和 Qwen3-ASR-1.7B。
接口与 STTEngine / FunASREngine / MlxWhisperEngine 完全兼容。

依赖：pip install qwen-asr torch torchaudio
平台：Windows / Linux / macOS（CPU + GPU）
"""

import time
import threading
import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, Future

import numpy as np

logger = logging.getLogger(__name__)


class Qwen3ASREngineUnavailableError(Exception):
    """qwen-asr 未安装或环境不可用"""
    pass


class Qwen3ASREngine:
    """Qwen3-ASR 分段转写引擎

    使用 ThreadPoolExecutor(max_workers=1) 保证单任务执行。
    异步提交转写任务，通过回调返回结果。

    线程模型与 FunASREngine / MlxWhisperEngine 完全一致。
    """

    def __init__(self, config):
        self.config = config
        self.model = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._current_future: Optional[Future] = None
        self._lock = threading.Lock()

    @staticmethod
    def is_available() -> bool:
        """检查 qwen-asr 是否可用"""
        try:
            import qwen_asr  # noqa: F401
            import torch    # noqa: F401
            return True
        except ImportError:
            return False

    def load_model(self) -> tuple[bool, str]:
        """加载 Qwen3-ASR 模型

        自动检测 CUDA 可用性，选择 device 和 dtype。

        GPU dtype 优先级：bfloat16 > float16 > float32
        CPU dtype：float32

        Returns:
            (True, '') 成功, (False, '错误信息') 失败
        """
        # Step 1: 检查依赖是否可用
        try:
            import qwen_asr
        except ImportError:
            return False, (
                'qwen-asr 未安装。请运行: '
                'pip install qwen-asr torch torchaudio'
            )

        try:
            import torch
        except ImportError:
            return False, 'torch 未安装。请运行: pip install torch torchaudio'

        # Step 2: 确定 device 和 dtype
        if torch.cuda.is_available():
            device = "cuda:0"
            # 按优先级尝试 dtype，bfloat16 不支持时降级到 float16
            if torch.cuda.is_bf16_supported():
                dtype = torch.bfloat16
            else:
                dtype = torch.float16
        else:
            device = "cpu"
            dtype = torch.float32

        # Step 3: 加载模型
        try:
            from qwen_asr import Qwen3ASRModel

            model_name = self.config.model_size  # "Qwen3-ASR-0.6B" or "Qwen3-ASR-1.7B"

            # 设置 HuggingFace 镜像（中国用户）
            hf_endpoint = getattr(self.config, 'hf_endpoint', '')
            if hf_endpoint:
                import os
                os.environ['HF_ENDPOINT'] = hf_endpoint

            # 从配置读取 max_new_tokens（默认 256）
            max_tokens = getattr(self.config, 'max_new_tokens', 256)

            self.model = Qwen3ASRModel.from_pretrained(
                f"Qwen/{model_name}",
                dtype=dtype,
                device_map=device,
                max_new_tokens=max_tokens,
            )

            logger.info(
                "Qwen3-ASR 模型加载完成: %s, device=%s, dtype=%s",
                model_name, device, dtype
            )
            return True, ''

        except Exception as e:
            error_msg = f"Qwen3-ASR 模型加载失败: {e}"
            logger.error(error_msg)
            return False, error_msg

    def transcribe_async(self, audio: np.ndarray, callback):
        """异步转写：提交到线程池，完成后回调。

        回调签名与现有引擎完全一致：
            callback(text: str, language: Optional[str], duration_ms: int, error: Optional[Exception])
        """
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
            if audio.size == 0 or len(audio) < 3200:  # <0.2s @16kHz
                return ("", None, 0, None)

            if self.model is None:
                return ("", None, 0, RuntimeError("模型未加载"))

            t0 = time.monotonic()

            # Qwen3-ASR 接受 (np.ndarray, sample_rate) 元组
            results = self.model.transcribe(
                audio=(audio, 16000),
                language=None,  # 自动检测语言
            )

            duration_ms = int((time.monotonic() - t0) * 1000)

            if not results or len(results) == 0:
                return ("", None, duration_ms, None)

            text = results[0].text.strip() if results[0].text else ""
            detected_lang = getattr(results[0], 'language', None)

            # 繁简转换（中文输出统一为简体）
            if detected_lang and 'chinese' in detected_lang.lower() and text:
                try:
                    from opencc import OpenCC
                    cc = OpenCC('t2s')
                    text = cc.convert(text)
                except ImportError:
                    pass

            return (text, detected_lang, duration_ms, None)

        except Exception as e:
            logger.error("Qwen3-ASR 转写失败: %s", e, exc_info=True)
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
        """关闭线程池，释放资源"""
        self._executor.shutdown(wait=True)
        self.model = None
        # 释放 GPU 显存
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        logger.info("Qwen3-ASR 引擎已关闭")
