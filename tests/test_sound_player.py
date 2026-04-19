"""SoundPlayer 单元测试"""

import unittest
import tempfile
import os
import struct
import wave
import numpy as np

from core.sound_player import SoundPlayer


class FakeSoundConfig:
    enabled = True
    volume = 0.5
    start_sound = True
    end_sound = True
    complete_sound = True


class TestSoundPlayer(unittest.TestCase):

    def _create_wav(self, path, samples, samplerate=16000, channels=1, sampwidth=2):
        """辅助：创建 16bit WAV 文件"""
        with wave.open(path, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sampwidth)
            wf.setframerate(samplerate)
            raw = b''.join(struct.pack('<h', int(s)) for s in samples)
            wf.writeframes(raw)

    def test_load_wav_stdlib_16bit(self):
        """正确解析 16bit WAV"""
        samples = [1000, -2000, 3000, -4000]
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            path = f.name
        try:
            self._create_wav(path, samples, samplerate=16000)
            data, sr = SoundPlayer._load_wav_stdlib(path)
            self.assertEqual(sr, 16000)
            self.assertEqual(len(data), 4)
            # 验证归一化：1000/32768 ≈ 0.0305
            self.assertAlmostEqual(data[0], 1000 / 32768.0, places=4)
        finally:
            os.unlink(path)

    def test_load_missing_file_returns_none(self):
        """缺失文件返回 None（通过 _load_sound）"""
        player = SoundPlayer(FakeSoundConfig())
        result = player._load_sound("nonexistent_sound_xyz")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
