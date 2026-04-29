"""音频重采样模块

将任意采样率的音频转换为 ASR 模型所需的 16kHz。
使用 scipy.signal.resample_poly（FIR 抗混叠滤波）。
"""

import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

TARGET_SR = 16000  # ASR 模型统一目标采样率


class AudioResampler:
    """音频重采样器

    使用 scipy.signal.resample_poly 进行高质量抗混叠重采样。
    构造时预计算 up/down 参数，运行时零额外开销。
    """

    def __init__(self, source_sr: int, target_sr: int = TARGET_SR):
        """
        Args:
            source_sr: 源采样率（设备原生采样率）
            target_sr: 目标采样率（默认 16000）

        Raises:
            ImportError: scipy 不可用时抛出
            ValueError: source_sr 或 target_sr 无效时抛出
        """
        if source_sr <= 0 or target_sr <= 0:
            raise ValueError(f"采样率必须为正数: source={source_sr}, target={target_sr}")

        self._source_sr = source_sr
        self._target_sr = target_sr

        # 预计算 resample_poly 参数（避免每次调用重复计算）
        self._up = target_sr // math.gcd(source_sr, target_sr)
        self._down = source_sr // math.gcd(source_sr, target_sr)
        self._needs_resample = (source_sr != target_sr)
        self._scipy_signal = None  # 延迟初始化

        if not self._needs_resample:
            logger.info("AudioResampler: %dHz → %dHz, 无需重采样", source_sr, target_sr)
        else:
            # 预加载 scipy.signal 并缓存
            import scipy.signal
            self._scipy_signal = scipy.signal
            logger.info("AudioResampler: %dHz → %dHz, up=%d, down=%d",
                       source_sr, target_sr, self._up, self._down)

    def resample(self, audio: np.ndarray) -> np.ndarray:
        """重采样音频

        Args:
            audio: float32 一维音频数组

        Returns:
            目标采样率的 float32 音频数组

        Raises:
            ImportError: scipy 不可用
        """
        if not self._needs_resample:
            return audio

        if len(audio) == 0:
            return audio

        if audio.ndim != 1:
            logger.warning("AudioResampler: 输入维度=%d, 预期1维, 自动flatten", audio.ndim)
            audio = audio.flatten()

        # 延迟加载 scipy.signal（仅首调用时触发）
        if self._scipy_signal is None:
            import scipy.signal
            self._scipy_signal = scipy.signal

        resampled = self._scipy_signal.resample_poly(audio, self._up, self._down, axis=0)
        return resampled.astype(np.float32)

    @property
    def source_sr(self) -> int:
        return self._source_sr

    @property
    def target_sr(self) -> int:
        return self._target_sr

    @property
    def needs_resample(self) -> bool:
        return self._needs_resample

    def __repr__(self) -> str:
        return (f"AudioResampler(source_sr={self._source_sr}, target_sr={self._target_sr}, "
                f"up={self._up}, down={self._down}, needs_resample={self._needs_resample})")


def create_resampler(source_sr: int, target_sr: int = TARGET_SR) -> AudioResampler:
    """工厂方法：创建重采样器，scipy 不可用时给出明确错误

    Args:
        source_sr: 源采样率
        target_sr: 目标采样率

    Returns:
        AudioResampler 实例

    Raises:
        ImportError: scipy 未安装时，附带安装指引
    """
    # 前置检查 scipy 可用性
    try:
        import scipy.signal  # noqa: F401
    except ImportError:
        raise ImportError(
            "scipy 未安装，音频重采样需要 scipy。"
            "请执行: pip install scipy"
        )
    return AudioResampler(source_sr, target_sr)
