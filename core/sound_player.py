"""提示音播放模块

职责：使用 sounddevice 播放提示音。
Phase 1 骨架，Phase 2 实现完整逻辑。
"""

import threading
import logging

logger = logging.getLogger(__name__)


class SoundPlayer:
    """提示音播放器。

    使用 sounddevice 非阻塞播放 WAV 音频文件。

    Args:
        config: SoundConfig 实例
    """

    def __init__(self, config):
        self.config = config
        self._sounds = {}  # 缓存加载的 WAV 数据

    def play(self, sound_name: str):
        """非阻塞播放提示音。

        Args:
            sound_name: 音效名称，如 "start", "end", "complete"
        """
        if not self.config.enabled:
            return

        # 检查该音效是否启用
        enabled_attr = f"{sound_name}_sound"
        if hasattr(self.config, enabled_attr) and not getattr(self.config, enabled_attr):
            return

        threading.Thread(
            target=self._play_sync,
            args=(sound_name,),
            daemon=True,
            name=f"sound-{sound_name}",
        ).start()

    def _play_sync(self, sound_name: str):
        """同步播放（在独立线程中执行）"""
        try:
            import sounddevice as sd

            result = self._load_sound(sound_name)
            if result is None:
                return
            data, samplerate = result
            if data is None:
                return

            volume = self.config.volume
            sd.play(data * volume, samplerate=samplerate)
            sd.wait()  # 等待播放完成
            logger.debug("播放提示音: %s", sound_name)
        except Exception as e:
            logger.warning("播放提示音失败: %s", e)

    @staticmethod
    def _load_wav_stdlib(path) -> tuple:
        """用标准库 wave + struct 读取 WAV 文件，零外部依赖。

        支持 16bit 和 32bit PCM。

        Returns:
            (np.float32 数据, 采样率) 元组
        """
        import wave
        import struct
        import numpy as np

        with wave.open(str(path), 'rb') as wf:
            samplerate = wf.getframerate()
            n_frames = wf.getnframes()
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            raw = wf.readframes(n_frames)

        total_samples = n_frames * n_channels

        if sampwidth == 2:  # 16bit PCM
            fmt = f'<{total_samples}h'
            samples = struct.unpack(fmt, raw)
            data = np.array(samples, dtype=np.float32) / 32768.0
        elif sampwidth == 4:  # 32bit PCM
            fmt = f'<{total_samples}i'
            samples = struct.unpack(fmt, raw)
            data = np.array(samples, dtype=np.float32) / 2147483648.0
        else:
            raise ValueError(f"不支持的采样宽度: {sampwidth}（仅支持 16bit/32bit PCM）")

        # 立体声取单声道
        if n_channels > 1:
            data = data[::n_channels]

        return (data, samplerate)

    def _load_sound(self, sound_name: str):
        """加载 WAV 音频文件，返回 (data, samplerate) 元组，缓存结果。

        优先使用标准库 wave+struct，不再依赖 scipy/soundfile。
        """
        if sound_name in self._sounds:
            return self._sounds[sound_name]

        from pathlib import Path

        assets_dir = Path(__file__).parent.parent / "assets"
        wav_path = assets_dir / f"sound_{sound_name}.wav"

        if not wav_path.exists():
            logger.debug("提示音文件不存在: %s", wav_path)
            self._sounds[sound_name] = None
            return None

        try:
            result = self._load_wav_stdlib(wav_path)
            self._sounds[sound_name] = result
            return result
        except Exception as e:
            logger.warning("加载提示音失败 %s: %s", sound_name, e)
            self._sounds[sound_name] = None
            return None
