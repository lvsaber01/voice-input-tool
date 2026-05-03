"""音频录制模块

职责：使用 sounddevice 采集音频，缓冲管理。
支持设备原生采样率采集 + 可选 resample 到目标采样率。
"""

import queue
import logging
import math
from collections import deque
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class AudioRecorder:
    """音频录制器。

    使用 sounddevice.InputStream 以设备原生采样率采集音频，
    通过 deque 缓冲主数据，通过 Queue 传递给静音检测线程。
    可选通过 AudioResampler 将原生采样率重采样到目标采样率。

    ⚠ _audio_callback 在实时音频线程中执行，只做数据拷贝。
    """

    SAMPLE_RATE = 16000  # ASR 模型目标采样率

    def __init__(self, config):
        self.config = config
        self.is_recording = False
        self._paused = False  # 暂停标志（GIL 下 bool 赋值原子，无需锁）
        self._buffer: deque = deque()
        self._buffer_queue: queue.Queue = queue.Queue()
        self._rt_queue = None  # 实时模式专用队列（可选）
        self._stream = None
        self._device_sr = 16000  # 设备原生采样率（启动时检测）
        self._resampler = None   # AudioResampler 实例（源 ≠ 目标时创建）

    def _detect_device_sample_rate(self, device=None) -> int:
        """检测设备原生采样率

        优先级：
        1. 用户配置 audio.sample_rate（非 auto 时直接使用）
        2. 查询设备默认采样率（sounddevice.query_devices）
        3. 尝试常见采样率（48000, 44100, 32000, 16000）并验证
        4. 回退到 16000（走原来的 PortAudio 重采样路径）

        Returns:
            设备支持的采样率
        """
        import sounddevice as sd

        # 1. 用户显式配置了采样率
        config_sr = getattr(self.config, 'sample_rate', None)
        if config_sr and config_sr != 'auto':
            logger.info("使用用户配置的采样率: %d", int(config_sr))
            return int(config_sr)

        # 确定要查询的设备
        query_device = device
        if query_device is None:
            query_device = sd.default.device[0]  # 显式获取默认输入设备
            if query_device is None:
                query_device = None  # 回退

        # 2. 查询设备默认采样率
        # 使用 concurrent.futures 实现跨平台超时
        # （Windows 上 signal.SIGALRM 不可用，只能对主线程生效）
        # 注意：ThreadPoolExecutor 超时后 worker 线程可能仍在后台运行
        # （无法强制中断 PortAudio 底层 C 调用），极端情况下延迟释放资源，风险低
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
        probe_timeout = 2

        def _try_open_and_verify(sr):
            s = sd.InputStream(samplerate=sr, channels=1, dtype='float32', device=query_device)
            s.start()
            actual = s.samplerate
            s.stop()
            s.close()
            return actual

        try:
            device_info = sd.query_devices(query_device)
            default_sr = device_info.get('default_samplerate', 0)
            if default_sr and default_sr > 0:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_try_open_and_verify, int(default_sr))
                    actual_sr = future.result(timeout=probe_timeout)

                if actual_sr == int(default_sr):
                    logger.info("设备原生采样率: %d (设备: %s, 验证通过)",
                               int(default_sr), device_info.get('name', '未知'))
                    return int(default_sr)
                else:
                    logger.warning("PortAudio 实际采样率(%d) ≠ 设备默认(%d), 进入探测模式",
                                   actual_sr, int(default_sr))
        except Exception as e:
            logger.warning("查询设备采样率失败: %s，进入探测模式", e)

        # 3. 探测：尝试常见采样率，验证 PortAudio 实际接受
        for probe_sr in [48000, 44100, 32000, 16000]:
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_try_open_and_verify, probe_sr)
                    actual_sr = future.result(timeout=probe_timeout)

                if actual_sr == probe_sr:
                    logger.info("探测成功: 设备支持 %dHz", probe_sr)
                    return probe_sr
                else:
                    logger.warning("探测 %dHz: PortAudio 实际使用 %dHz", probe_sr, actual_sr)
            except Exception:
                continue

        # 4. 回退到 16000
        logger.warning("采样率探测失败，回退到 16000Hz（PortAudio 自动重采样）")
        return 16000

    @property
    def resampler(self):
        """返回 AudioResampler 实例（仅当需要重采样时非 None）"""
        return self._resampler

    @property
    def device_sample_rate(self) -> int:
        """返回设备原生采样率"""
        return self._device_sr

    def start(self, rt_queue=None):
        """开始录音

        Args:
            rt_queue: 实时模式专用队列（可选）。如提供，音频 chunk 也会 put 到此队列。
        """
        import sounddevice as sd

        self._rt_queue = rt_queue
        self._buffer.clear()
        # 清空缓冲队列（丢弃旧数据）
        while not self._buffer_queue.empty():
            try:
                self._buffer_queue.get_nowait()
            except queue.Empty:
                break

        # 解析设备参数
        device = self.config.device
        if device is not None:
            try:
                device = int(device)
            except (ValueError, TypeError):
                pass  # 保留字符串设备名

        # 检测设备原生采样率
        self._device_sr = self._detect_device_sample_rate(device)

        # 创建 resampler（如果源 ≠ 目标）
        if self._device_sr != self.SAMPLE_RATE:
            try:
                from core.audio_resampler import create_resampler
                self._resampler = create_resampler(self._device_sr, self.SAMPLE_RATE)
            except ImportError as e:
                logger.error("无法创建 resampler: %s，回退到 %dHz", e, self.SAMPLE_RATE)
                self._device_sr = self.SAMPLE_RATE
                self._resampler = None
        else:
            self._resampler = None

        # 计算 blocksize：保持每 chunk ~10ms 时长
        # 48kHz → 480, 44100 → 441, 16kHz → 160, 8kHz → 80
        # 下限 512 = sounddevice 默认推荐值（PortAudio 文档建议 ≥ 64）
        base_blocksize = 512
        self._blocksize = max(base_blocksize, int(self._device_sr * 0.01))

        try:
            self._stream = sd.InputStream(
                samplerate=self._device_sr,
                channels=1,
                dtype='float32',
                callback=self._audio_callback,
                device=device,
                blocksize=self._blocksize,
            )
            self._stream.start()
        except Exception as e:
            if device is not None:
                logger.warning("指定设备 '%s' 不可用，回退到默认设备: %s", device, e)
                # 回退后重新检测默认设备的采样率
                self._device_sr = self._detect_device_sample_rate(None)
                if self._device_sr != self.SAMPLE_RATE:
                    try:
                        from core.audio_resampler import create_resampler
                        self._resampler = create_resampler(self._device_sr, self.SAMPLE_RATE)
                    except ImportError:
                        self._resampler = None
                else:
                    self._resampler = None
                self._blocksize = max(512, int(self._device_sr * 0.01))

                self._stream = sd.InputStream(
                    samplerate=self._device_sr,
                    channels=1,
                    dtype='float32',
                    callback=self._audio_callback,
                    blocksize=self._blocksize,
                )
                self._stream.start()
            else:
                raise

        self.is_recording = True
        logger.info("开始录音，设备采样率: %d, 目标: %d, 设备: %s, resample: %s",
                    self._device_sr, self.SAMPLE_RATE, device or "默认",
                    self._resampler is not None)

    def stop(self) -> np.ndarray:
        """停止录音，返回完整 PCM 数据（已 resample 到 SAMPLE_RATE）"""
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
            try:
                audio = np.concatenate(list(self._buffer))
                if audio.ndim > 1:
                    audio = audio.flatten()
                # resample 到目标采样率
                if self._resampler and self._resampler.needs_resample and len(audio) > 0:
                    audio = self._resampler.resample(audio)
                return audio
            except Exception as e:
                logger.error("拼接/重采样音频失败: %s", e)
        return np.array([], dtype=np.float32)

    def pause(self):
        """暂停采样（丢弃采集的数据，不放入 buffer/queue）。

        线程安全说明：self._paused 是 bool 类型，在 CPython GIL 下
        单字节赋值是原子操作，无需额外锁保护。
        """
        self._paused = True
        logger.debug("录音器已暂停")

    def resume(self):
        """恢复采样。"""
        self._paused = False
        logger.debug("录音器已恢复")

    def _audio_callback(self, indata, frames, time_info, status):
        """sounddevice 回调（实时线程）

        ⚠ 关键约束：此方法在实时音频线程中执行，只做数据拷贝！
        """
        if self._paused:
            return  # 暂停时丢弃采集数据
        chunk = indata.copy()
        self._buffer.append(chunk)
        self._buffer_queue.put(chunk)
        # 实时模式：非阻塞放入专用队列
        if self._rt_queue is not None:
            try:
                self._rt_queue.put_nowait(chunk)
            except queue.Full:
                pass  # 丢弃（背压保护）

    def get_buffer_queue(self) -> queue.Queue:
        """返回缓冲队列，供静音检测线程消费"""
        return self._buffer_queue
