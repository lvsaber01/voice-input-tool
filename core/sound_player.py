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

            data, samplerate = self._load_sound(sound_name)
            if data is None:
                return

            volume = self.config.volume
            sd.play(data * volume, samplerate=samplerate)
            sd.wait()  # 等待播放完成
            logger.debug("播放提示音: %s", sound_name)
        except Exception as e:
            logger.warning("播放提示音失败: %s", e)

    def _load_sound(self, sound_name: str):
        """加载 WAV 音频文件，返回 (data, samplerate) 元组，缓存结果"""
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
            from scipy.io import wavfile
            samplerate, data = wavfile.read(str(wav_path))
            # 转为 float32
            import numpy as np
            if data.dtype != np.float32:
                data = data.astype(np.float32) / np.iinfo(data.dtype).max
            # 如果是立体声，取单声道
            if data.ndim > 1:
                data = data[:, 0]
            self._sounds[sound_name] = (data, samplerate)
            return (data, samplerate)
        except ImportError:
            # scipy 未安装，尝试用 soundfile
            try:
                import soundfile as sf
                data, samplerate = sf.read(str(wav_path), dtype='float32')
                if data.ndim > 1:
                    data = data[:, 0]
                self._sounds[sound_name] = (data, samplerate)
                return (data, samplerate)
            except ImportError:
                logger.warning("scipy 和 soundfile 均未安装，无法加载 WAV 文件")
                self._sounds[sound_name] = None
                return None
        except Exception as e:
            logger.warning("加载提示音失败 %s: %s", sound_name, e)
            self._sounds[sound_name] = None
            return None
