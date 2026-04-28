"""FunASR Paraformer / SenseVoice STT 引擎

阿里达摩院 Paraformer 模型，CPU 上极快（10x 实时速度）。
SenseVoice 模型支持 50+ 语言自动检测（20x 实时速度）。
"""

import time
import threading
import logging
import re
import os
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, Future

import numpy as np

logger = logging.getLogger(__name__)


class FunASREngine:
    """FunASR 语音识别引擎。

    支持 Paraformer（中文/英文）和 SenseVoice（多语言）两种模型。
    使用 ThreadPoolExecutor(max_workers=1) 保证单任务执行。
    与 STTEngine 相同的接口：load_model / transcribe_async / shutdown。
    """

    def __init__(self, config):
        self.config = config
        self.model = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._current_future: Optional[Future] = None
        self._lock = threading.Lock()
        self._is_sensevoice = False  # SenseVoice 模式标志，在 load_model() 中设置
        self._is_fun_asr_nano = False  # Fun-ASR-Nano 标志位

    def load_model(self) -> tuple[bool, str]:
        """加载 FunASR 模型（Paraformer 或 SenseVoice）"""
        try:
            from funasr import AutoModel

            # 设置 ModelScope 镜像（使用后恢复原始值）
            modelscope_endpoint = getattr(self.config, 'modelscope_endpoint', '')
            original_endpoint = os.environ.get('MODELSCOPE_ENDPOINT')
            try:
                if modelscope_endpoint:
                    os.environ['MODELSCOPE_ENDPOINT'] = modelscope_endpoint
                    logger.info("使用 ModelScope 镜像: %s", modelscope_endpoint)

                model_name = self.config.model_size or "paraformer-zh"
                logger.info("加载 FunASR 模型: %s", model_name)

                if model_name == "SenseVoiceSmall":
                    # SenseVoice 多语言模型
                    self.model = AutoModel(
                        model="iic/SenseVoiceSmall",
                        trust_remote_code=True,  # SenseVoice 必要参数（官方模型 iic/SenseVoiceSmall）
                        vad_model="fsmn-vad",
                        vad_kwargs={"max_single_segment_time": 30000},
                        device="cpu",
                        disable_update=True,
                    )
                    self._is_sensevoice = True
                    self._is_fun_asr_nano = False
                    logger.info("SenseVoice 模型加载完成（多语言，50+ 语言）")
                elif model_name == "Fun-ASR-Nano":
                    # Fun-ASR-Nano 新一代模型（800M 参数，支持方言）
                    try:
                        from funasr.models.fun_asr_nano.model import FunASRNano  # 注册模型类
                    except ImportError:
                        return False, "FunASR 版本过低，请升级: pip install 'funasr>=1.1'"
                    self.model = AutoModel(
                        model="FunAudioLLM/Fun-ASR-Nano-2512",
                        trust_remote_code=True,
                        device="cpu",
                        disable_update=True,
                    )
                    self._is_sensevoice = False
                    self._is_fun_asr_nano = True
                    logger.info("Fun-ASR-Nano 模型加载完成（中文精度最高，支持7种方言）")
                else:
                    # Paraformer 模型（原逻辑不变）
                    self.model = AutoModel(
                        model=model_name,
                        punc_model="ct-punc",
                        device="cpu",
                        disable_update=True,
                    )
                    self._is_sensevoice = False
                    self._is_fun_asr_nano = False
                    logger.info("Paraformer 模型加载完成")

                return True, ''
            finally:
                # 恢复原始环境变量
                if original_endpoint is not None:
                    os.environ['MODELSCOPE_ENDPOINT'] = original_endpoint
                elif 'MODELSCOPE_ENDPOINT' in os.environ:
                    del os.environ['MODELSCOPE_ENDPOINT']
        except ImportError:
            logger.error("funasr 未安装。请运行: pip install funasr modelscope")
            return False, 'funasr 未安装，请运行: pip install funasr modelscope'
        except Exception as e:
            error_msg = str(e)[:200]  # 截断防止日志注入
            logger.error("FunASR 模型加载失败: %s", error_msg)
            if "fsmn-vad" in error_msg:
                return False, 'VAD 模型下载失败，请检查网络连接后重试'
            if "SenseVoiceSmall" in error_msg:
                return False, f'SenseVoice 模型加载失败: {error_msg}'
            return False, error_msg

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
            if self.model is None:
                return ("", None, 0, RuntimeError("模型未加载"))

            if audio.size == 0 or len(audio) < 3200:  # <0.2s
                return ("", None, 0, None)

            t0 = time.monotonic()

            if self._is_sensevoice:
                # SenseVoice 转写
                result = self.model.generate(
                    input=audio,
                    language="auto",  # 自动语言检测
                    use_itn=True,     # 逆文本规范化
                )
                duration_ms = int((time.monotonic() - t0) * 1000)

                raw_text = self._extract_text(result)
                text = self._postprocess_sensevoice(raw_text)
                detected_lang = "auto"
            elif self._is_fun_asr_nano:
                # Fun-ASR-Nano 转写
                result = self.model.generate(
                    input=audio,
                    cache={},
                    batch_size=1,
                    language="auto",   # 自动检测语言（支持中英日）
                    itn=True,          # 逆文本规范化（数字、日期格式化）
                )
                duration_ms = int((time.monotonic() - t0) * 1000)

                raw_text = self._extract_text(result)
                text = self._postprocess_sensevoice(raw_text)
                detected_lang = "auto"
            else:
                # Paraformer 转写（原逻辑不变）
                result = self.model.generate(
                    input=audio,
                    batch_size_s=300,
                )
                duration_ms = int((time.monotonic() - t0) * 1000)

                text = self._extract_text(result)
                detected_lang = "zh"

            # 共用：去除中文间多余空格
            text = self._normalize_chinese_spaces(text.strip())

            logger.info("FunASR 转写完成: '%s' (%dms)", text[:50], duration_ms)
            return (text, detected_lang, duration_ms, None)

        except Exception as e:
            logger.error("FunASR 转写失败: %s", str(e)[:200])
            return ("", None, 0, e)

    @staticmethod
    def _extract_text(result) -> str:
        """从 FunASR 结果中提取文本（Paraformer 和 SenseVoice 共用）"""
        if not result or len(result) == 0:
            return ""
        item = result[0]
        if isinstance(item, dict):
            return item.get("text", "")
        elif hasattr(item, "text"):
            return item.text
        elif isinstance(item, str):
            return item
        return ""

    @staticmethod
    def _normalize_chinese_spaces(text: str, max_iterations: int = 10) -> str:
        """去除中文字符之间的多余空格，保留英文空格。

        Args:
            text: 输入文本
            max_iterations: 最大迭代次数（防御性保护，默认 10 次）
        """
        for _ in range(max_iterations):
            new_text = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', text)
            if new_text == text:
                break
            text = new_text
        return text

    @staticmethod
    def _postprocess_sensevoice(raw_text: Optional[str]) -> str:
        """SenseVoice 输出后处理：去除特殊标记（带 fallback）。

        优先使用 funasr.utils.postprocess_utils.rich_transcription_postprocess
        （FunASR 1.3.1 已验证可用），如果不可用则使用正则 fallback。
        """
        if raw_text is None:
            return ""
        if not isinstance(raw_text, str):
            logger.warning("Unexpected text type: %s", type(raw_text))
            return str(raw_text) if raw_text else ""

        # 优先使用官方函数
        try:
            from funasr.utils.postprocess_utils import rich_transcription_postprocess
            return rich_transcription_postprocess(raw_text)
        except (ImportError, AttributeError):
            logger.warning("rich_transcription_postprocess 不可用，使用 fallback")

        # Fallback：精确匹配已知标记类型
        # 覆盖：EMO_*(情感)、Event_*(事件)、nospeech/Speech/woitn(语音状态)、
        #        BGM/LAUGH(音频事件)、2-3字母语言代码(zh/en/ja/ko/yue等)
        KNOWN_MARKERS = r'<\|(?:EMO_\w+|Event_\w+|nospeech|Speech|woitn|BGM|LAUGH|[a-z]{2,3})\|>'
        text = re.sub(KNOWN_MARKERS, '', raw_text)
        return text.strip()

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
