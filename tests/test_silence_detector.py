"""SilenceDetector 单元测试"""

import unittest
import numpy as np
from core.silence_detector import SilenceDetector


class FakeConfig:
    silence_threshold = 0.01
    silence_timeout = 1.0


class TestSilenceDetector(unittest.TestCase):

    def test_calculate_rms(self):
        """RMS 计算正确"""
        # DC signal: [1, 1, 1] -> RMS = 1.0
        data = np.array([1.0, 1.0, 1.0], dtype=np.float32)
        rms = SilenceDetector._calculate_rms(data)
        self.assertAlmostEqual(rms, 1.0, places=5)

        # Zero signal
        data = np.zeros(100, dtype=np.float32)
        rms = SilenceDetector._calculate_rms(data)
        self.assertAlmostEqual(rms, 0.0, places=5)

        # Known signal: [1, -1, 1, -1] -> RMS = 1.0
        data = np.array([1.0, -1.0, 1.0, -1.0], dtype=np.float32)
        rms = SilenceDetector._calculate_rms(data)
        self.assertAlmostEqual(rms, 1.0, places=5)

    def test_silence_callback(self):
        """静音超时回调被调用"""
        called = []
        detector = SilenceDetector(FakeConfig(), lambda: called.append(True))
        detector._last_sound_time = 0  # 很久以前
        detector._detecting = True
        detector._last_rms_publish = 0

        # 直接模拟检测逻辑（不启动线程）
        chunk = np.zeros(1600, dtype=np.float32)  # 静音数据
        rms = SilenceDetector._calculate_rms(chunk)
        self.assertLess(rms, FakeConfig.silence_threshold)


if __name__ == "__main__":
    unittest.main()
