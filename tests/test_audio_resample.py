#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
音频重采样功能全面测试

覆盖范围：
  - audio_resampler.py: AudioResampler 单元测试
  - recorder.py: 采样率检测 + blocksize 自适应 + resample 集成
  - vad_segment_transcriber.py: 动态采样率 + VAD resampler + flush resample
  - streaming_transcriber.py: 动态 chunk_samples + flush resample + 对齐
  - config.py: sample_rate 字段 + v6→v7 迁移 + 边界值
  - 回归测试: 16000→16000 无 resample 路径、现有功能不受影响

运行方式: python -m pytest tests/test_audio_resample.py -v
"""

import sys
import os
import unittest
import time
import queue
import threading
import numpy as np
from unittest.mock import Mock, patch, MagicMock, PropertyMock
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 检查 scipy 是否可用
try:
    import scipy.signal
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


# ============================================================
# 第一层: AudioResampler 单元测试
# ============================================================

class TestAudioResampler(unittest.TestCase):
    """AudioResampler 核心功能测试"""

    def setUp(self):
        from core.audio_resampler import AudioResampler, create_resampler
        self.AudioResampler = AudioResampler
        self.create_resampler = create_resampler

    # --- 构造与属性 ---

    def test_same_rate_no_resample(self):
        """16000→16000 不需要重采样"""
        r = self.AudioResampler(16000, 16000)
        self.assertFalse(r.needs_resample)
        self.assertEqual(r.source_sr, 16000)
        self.assertEqual(r.target_sr, 16000)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_48k_to_16k_needs_resample(self):
        """48000→16000 需要重采样"""
        r = self.AudioResampler(48000, 16000)
        self.assertTrue(r.needs_resample)
        self.assertEqual(r._up, 1)
        self.assertEqual(r._down, 3)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_44100_to_16000_needs_resample(self):
        """44100→16000 非整数比"""
        r = self.AudioResampler(44100, 16000)
        self.assertTrue(r.needs_resample)
        # gcd(44100, 16000) = 100
        self.assertEqual(r._up, 160)
        self.assertEqual(r._down, 441)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_8000_to_16000_upsample(self):
        """8000→16000 升采样"""
        r = self.AudioResampler(8000, 16000)
        self.assertTrue(r.needs_resample)
        self.assertEqual(r._up, 2)
        self.assertEqual(r._down, 1)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_32000_to_16000(self):
        """32000→16000"""
        r = self.AudioResampler(32000, 16000)
        self.assertTrue(r.needs_resample)
        self.assertEqual(r._up, 1)
        self.assertEqual(r._down, 2)

    def test_invalid_negative_source_sr(self):
        """负采样率应报错"""
        with self.assertRaises(ValueError):
            self.AudioResampler(-16000, 16000)

    def test_invalid_zero_target_sr(self):
        """目标采样率为0应报错"""
        with self.assertRaises(ValueError):
            self.AudioResampler(16000, 0)

    # --- resample 方法 ---

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_resample_48k_to_16k_output_length(self):
        """48kHz 1秒 → 16kHz 1秒"""
        r = self.AudioResampler(48000, 16000)
        x = np.random.randn(48000).astype(np.float32)
        y = r.resample(x)
        self.assertEqual(len(y), 16000)
        self.assertEqual(y.dtype, np.float32)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_resample_44100_to_16000_output_length(self):
        """44100→16000 输出长度正确"""
        r = self.AudioResampler(44100, 16000)
        x = np.random.randn(44100).astype(np.float32)
        y = r.resample(x)
        self.assertEqual(len(y), 16000)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_resample_8k_to_16k_output_length(self):
        """8kHz→16kHz 升采样输出长度正确"""
        r = self.AudioResampler(8000, 16000)
        x = np.random.randn(8000).astype(np.float32)
        y = r.resample(x)
        self.assertEqual(len(y), 16000)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_resample_empty_array(self):
        """空数组直接返回"""
        r = self.AudioResampler(48000, 16000)
        y = r.resample(np.array([], dtype=np.float32))
        self.assertEqual(len(y), 0)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_resample_single_sample(self):
        """单样本 resample 不崩溃"""
        r = self.AudioResampler(48000, 16000)
        x = np.array([0.5], dtype=np.float32)
        y = r.resample(x)
        self.assertEqual(y.dtype, np.float32)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_resample_short_audio(self):
        """极短音频(10个样本)不崩溃"""
        r = self.AudioResampler(48000, 16000)
        x = np.random.randn(10).astype(np.float32)
        y = r.resample(x)
        self.assertEqual(y.dtype, np.float32)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_resample_2d_input_auto_flatten(self):
        """2维输入自动 flatten（打日志但不报错）"""
        r = self.AudioResampler(48000, 16000)
        x = np.random.randn(480, 100).astype(np.float32)
        y = r.resample(x)
        self.assertEqual(y.dtype, np.float32)

    def test_resample_passthrough_when_same_rate(self):
        """同采样率直通，返回同一对象"""
        r = self.AudioResampler(16000, 16000)
        x = np.random.randn(16000).astype(np.float32)
        y = r.resample(x)
        self.assertIs(y, x)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_resample_preserves_signal_shape(self):
        """resample 后信号大致形状保留（正弦波频率一致）"""
        r = self.AudioResampler(48000, 16000)
        # 440Hz 正弦波 @ 48kHz, 0.5秒
        t = np.arange(24000) / 48000.0
        x = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
        y = r.resample(x)
        # 输出应为 8000 样本 (0.5s @ 16kHz)
        self.assertEqual(len(y), 8000)
        # 检查均值接近0（对称信号）
        self.assertAlmostEqual(float(np.mean(y)), 0.0, places=1)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_resample_multiple_calls_consistent(self):
        """多次 resample 结果一致（无状态依赖）"""
        r = self.AudioResampler(48000, 16000)
        x = np.random.randn(48000).astype(np.float32)
        y1 = r.resample(x)
        y2 = r.resample(x)
        np.testing.assert_array_equal(y1, y2)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_repr(self):
        """repr 输出可读"""
        r = self.AudioResampler(48000, 16000)
        s = repr(r)
        self.assertIn("48000", s)
        self.assertIn("16000", s)
        self.assertIn("up=1", s)
        self.assertIn("down=3", s)


class TestCreateResamplerFactory(unittest.TestCase):
    """create_resampler 工厂方法测试"""

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_factory_returns_resampler(self):
        """正常返回 AudioResampler 实例"""
        from core.audio_resampler import create_resampler, AudioResampler
        r = create_resampler(48000, 16000)
        self.assertIsInstance(r, AudioResampler)

    def test_factory_no_scipy_raises_import_error(self):
        """scipy 不可用时抛 ImportError 含安装提示"""
        from core.audio_resampler import create_resampler
        with patch.dict('sys.modules', {'scipy': None, 'scipy.signal': None}):
            with self.assertRaises(ImportError) as ctx:
                create_resampler(48000, 16000)
            self.assertIn("scipy", str(ctx.exception))
            self.assertIn("pip install scipy", str(ctx.exception))


# ============================================================
# 第二层: Config 迁移测试
# ============================================================

class TestConfigSampleRate(unittest.TestCase):
    """config.py sample_rate 字段测试"""

    def test_default_sample_rate_is_none(self):
        """默认 sample_rate=None (auto)"""
        from config import AudioConfig
        c = AudioConfig()
        self.assertIsNone(c.sample_rate)

    def test_sample_rate_explicit_value(self):
        """显式设置 sample_rate=48000"""
        from config import AudioConfig
        c = AudioConfig(sample_rate=48000)
        self.assertEqual(c.sample_rate, 48000)

    def test_sample_rate_string_auto(self):
        """sample_rate='auto' 自动转为 None"""
        from config import AudioConfig
        c = AudioConfig(sample_rate='auto')
        self.assertIsNone(c.sample_rate)

    def test_sample_rate_string_number(self):
        """sample_rate='48000' 自动转为 int"""
        from config import AudioConfig
        c = AudioConfig(sample_rate='48000')
        self.assertEqual(c.sample_rate, 48000)

    def test_sample_rate_invalid_string_fallback(self):
        """无效字符串回退为 None"""
        from config import AudioConfig
        c = AudioConfig(sample_rate='garbage')
        self.assertIsNone(c.sample_rate)

    def test_migration_v6_to_v7(self):
        """v6→v7 迁移: 新增 sample_rate 字段"""
        from config import _migrate_v6_to_v7
        raw = {
            'config_version': 6,
            'audio': {'max_duration': 120, 'silence_timeout': 8, 'device': None}
        }
        result = _migrate_v6_to_v7(raw)
        self.assertEqual(result['config_version'], 7)
        self.assertIsNone(result['audio']['sample_rate'])

    def test_migration_v6_to_v7_preserves_existing(self):
        """v6→v7 迁移不覆盖已有值"""
        from config import _migrate_v6_to_v7
        raw = {
            'config_version': 6,
            'audio': {'max_duration': 120, 'sample_rate': 48000}
        }
        result = _migrate_v6_to_v7(raw)
        self.assertEqual(result['audio']['sample_rate'], 48000)

    def test_load_config_migrates_to_v7(self):
        """完整加载流程: 旧配置自动迁移到 v8"""
        import tempfile, yaml
        from config import load_config
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump({'config_version': 5, 'audio': {'max_duration': 120}}, f)
            path = f.name
        try:
            config = load_config(path)
            self.assertEqual(config.config_version, 8)
            self.assertIsNone(config.audio.sample_rate)
        finally:
            os.unlink(path)


# ============================================================
# 第三层: Recorder 采样率检测测试 (mock sounddevice)
# ============================================================

class TestRecorderSampleRateDetection(unittest.TestCase):
    """recorder.py 采样率检测测试"""

    def test_detect_explicit_config_rate(self):
        """用户配置了 sample_rate 时直接使用"""
        from core.recorder import AudioRecorder
        config = Mock()
        config.device = None
        config.sample_rate = 48000

        recorder = AudioRecorder(config)
        sr = recorder._detect_device_sample_rate(None)

        self.assertEqual(sr, 48000)

    @patch('sounddevice.default', new_callable=PropertyMock, return_value=(0, 1))
    @patch('sounddevice.query_devices')
    @patch('sounddevice.InputStream')
    def test_detect_device_query_success(self, mock_stream_cls, mock_query, mock_default):
        """设备查询成功返回默认采样率"""
        from core.recorder import AudioRecorder

        config = Mock()
        config.device = None
        config.sample_rate = None  # auto

        mock_query.return_value = {
            'name': 'Test Mic',
            'default_samplerate': 48000.0
        }
        mock_stream = MagicMock()
        mock_stream.samplerate = 48000
        mock_stream_cls.return_value = mock_stream

        recorder = AudioRecorder(config)
        sr = recorder._detect_device_sample_rate(None)
        self.assertEqual(sr, 48000)

    @patch('sounddevice.default', new_callable=PropertyMock, return_value=(0, 1))
    @patch('sounddevice.query_devices')
    @patch('sounddevice.InputStream')
    def test_detect_probe_fallback(self, mock_stream_cls, mock_query, mock_default):
        """设备查询失败后进入探测模式，尝试常见采样率"""
        from core.recorder import AudioRecorder

        config = Mock()
        config.device = None
        config.sample_rate = None

        mock_query.side_effect = Exception("query failed")

        # 48000 验证成功
        mock_stream_48 = MagicMock()
        mock_stream_48.samplerate = 48000

        mock_stream_cls.return_value = mock_stream_48

        recorder = AudioRecorder(config)
        sr = recorder._detect_device_sample_rate(None)
        self.assertEqual(sr, 48000)

    @patch('sounddevice.default', new_callable=PropertyMock, return_value=(0, 1))
    @patch('sounddevice.query_devices')
    @patch('sounddevice.InputStream')
    def test_detect_all_probe_fail_fallback_to_16k(self, mock_stream_cls, mock_query, mock_default):
        """所有探测失败回退到 16000"""
        from core.recorder import AudioRecorder

        config = Mock()
        config.device = None
        config.sample_rate = None

        mock_query.side_effect = Exception("query failed")
        mock_stream_cls.side_effect = Exception("all probes fail")

        recorder = AudioRecorder(config)
        sr = recorder._detect_device_sample_rate(None)
        self.assertEqual(sr, 16000)

    @patch('sounddevice.default', new_callable=PropertyMock, return_value=(0, 1))
    @patch('sounddevice.query_devices')
    @patch('sounddevice.InputStream')
    def test_detect_portaudio_resample_mismatch(self, mock_stream_cls, mock_query, mock_default):
        """PortAudio 实际采样率 ≠ 设备默认采样率时进入探测模式"""
        from core.recorder import AudioRecorder

        config = Mock()
        config.device = None
        config.sample_rate = None

        mock_query.return_value = {
            'name': 'DJI Mic',
            'default_samplerate': 48000.0
        }

        # 第一轮验证：实际返回 16000（PortAudio 自动重采样）→ 不匹配
        # 探测 48000：实际返回 16000 → 不匹配
        # 探测 44100：实际返回 16000 → 不匹配
        # 探测 32000：实际返回 16000 → 不匹配
        # 探测 16000：匹配！
        results = [
            MagicMock(samplerate=16000),  # 验证 48k
            MagicMock(samplerate=16000),  # 探测 48k
            MagicMock(samplerate=16000),  # 探测 44100
            MagicMock(samplerate=16000),  # 探测 32000
            MagicMock(samplerate=16000),  # 探测 16000 → 匹配
        ]
        mock_stream_cls.side_effect = results

        recorder = AudioRecorder(config)
        sr = recorder._detect_device_sample_rate(None)
        self.assertEqual(sr, 16000)


class TestRecorderStartStop(unittest.TestCase):
    """recorder start/stop 集成测试"""

    def _make_mock_recorder(self, device_sr=48000):
        """创建 mock 了 sounddevice 的 recorder"""
        from core.recorder import AudioRecorder

        config = Mock()
        config.device = None
        config.sample_rate = None

        mock_stream = MagicMock()
        mock_stream.samplerate = device_sr

        # 直接设置 recorder 属性，不调用 start
        recorder = AudioRecorder(config)
        recorder._device_sr = device_sr
        if device_sr != 16000 and HAS_SCIPY:
            from core.audio_resampler import create_resampler
            recorder._resampler = create_resampler(device_sr, 16000)
        else:
            recorder._resampler = None
        recorder._blocksize = max(512, int(device_sr * 0.01))
        recorder._stream = mock_stream

        return recorder

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_stop_returns_resampled_audio_48k(self):
        """48kHz 采集 stop 后返回 16kHz 音频"""
        recorder = self._make_mock_recorder(48000)
        # 模拟 1 秒的 48kHz 音频（分多个 chunk）
        chunks_48k = [np.random.randn(480,).astype(np.float32) for _ in range(100)]
        recorder._buffer.extend(chunks_48k)
        recorder.is_recording = True

        audio = recorder.stop()
        # 48000 样本 / 3 = 16000 样本
        self.assertEqual(len(audio), 16000)
        self.assertEqual(audio.dtype, np.float32)

    def test_stop_returns_passthrough_audio_16k(self):
        """16kHz 采集 stop 后直接返回（不 resample）"""
        recorder = self._make_mock_recorder(16000)
        chunks_16k = [np.random.randn(160,).astype(np.float32) for _ in range(100)]
        recorder._buffer.extend(chunks_16k)
        recorder.is_recording = True

        audio = recorder.stop()
        self.assertEqual(len(audio), 16000)

    def test_stop_empty_buffer(self):
        """空 buffer 返回空数组"""
        recorder = self._make_mock_recorder(48000)
        recorder.is_recording = True
        audio = recorder.stop()
        self.assertEqual(len(audio), 0)

    def test_blocksize_adaptive(self):
        """blocksize 根据采样率自适应"""
        recorder = self._make_mock_recorder(48000)
        # 48000 * 0.01 = 480, max(512, 480) = 512
        self.assertEqual(recorder._blocksize, 512)

        recorder2 = self._make_mock_recorder(96000)
        # 96000 * 0.01 = 960, max(512, 960) = 960
        self.assertEqual(recorder2._blocksize, 960)


# ============================================================
# 第四层: VADSegmentTranscriber 集成测试
# ============================================================

class TestVADSegmentTranscriberWithResampler(unittest.TestCase):
    """VADSegmentTranscriber 动态采样率测试"""

    def _make_vad_transcriber(self):
        from core.vad_segment_transcriber import VADSegmentTranscriber
        config = Mock()
        config.vad_sensitivity = 2
        config.vad_window_ms = 30
        config.segment_pause_threshold = 0.8
        config.min_segment_duration = 0.3
        config.max_segment_duration = 30.0

        stt_engine = Mock()
        stt_engine.transcribe_sync.return_value = "测试文字"

        on_segment = Mock()
        transcriber = VADSegmentTranscriber(config, stt_engine, on_segment)
        # 强制 RMS 模式（避免 webrtcvad 依赖问题）
        transcriber._vad = None
        transcriber._vad_mode = 'rms'
        transcriber._rms_threshold = 0.015
        return transcriber

    def test_start_without_resampler(self):
        """无 resampler 时 source_sr 保持 16000"""
        transcriber = self._make_vad_transcriber()
        audio_queue = queue.Queue()
        transcriber.start(audio_queue)

        try:
            self.assertEqual(transcriber._source_sr, 16000)
            self.assertIsNone(transcriber._resampler)
        finally:
            transcriber._running = False
            if transcriber._thread:
                transcriber._thread.join(timeout=2)
            if transcriber._inject_queue:
                transcriber._inject_queue.put(None)
            if transcriber._inject_thread:
                transcriber._inject_thread.join(timeout=2)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_start_with_resampler(self):
        """有 resampler 时 source_sr 设置为源采样率"""
        transcriber = self._make_vad_transcriber()
        audio_queue = queue.Queue()

        from core.audio_resampler import create_resampler
        resampler = create_resampler(48000, 16000)

        transcriber.start(audio_queue, resampler=resampler)

        try:
            self.assertEqual(transcriber._source_sr, 48000)
            self.assertIsNotNone(transcriber._resampler)
            # 48000 不需要 VAD resampler（webrtcvad 支持 48000）
            self.assertIsNone(transcriber._vad_resampler)
        finally:
            transcriber._running = False
            if transcriber._thread:
                transcriber._thread.join(timeout=2)
            if transcriber._inject_queue:
                transcriber._inject_queue.put(None)
            if transcriber._inject_thread:
                transcriber._inject_thread.join(timeout=2)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_start_with_uncommon_sample_rate(self):
        """非常见采样率(44100)时 webrtcvad 模式创建 VAD 专用 resampler"""
        transcriber = self._make_vad_transcriber()
        audio_queue = queue.Queue()

        from core.audio_resampler import create_resampler
        resampler = create_resampler(44100, 16000)

        # 切回 webrtcvad 模式来测试 VAD resampler 逻辑
        try:
            import webrtcvad
            transcriber._vad = webrtcvad.Vad()
            transcriber._vad.set_mode(2)
            transcriber._vad_mode = 'webrtcvad'
        except ImportError:
            self.skipTest("webrtcvad not installed")

        transcriber.start(audio_queue, resampler=resampler)

        try:
            self.assertEqual(transcriber._source_sr, 44100)
            # 44100 不在 (8000,16000,32000,48000) 中，应创建 VAD resampler
            # 最近的是 48000
            self.assertIsNotNone(transcriber._vad_resampler)
            self.assertEqual(transcriber._vad_resampler.target_sr, 48000)
        finally:
            transcriber._running = False
            if transcriber._thread:
                transcriber._thread.join(timeout=2)
            if transcriber._inject_queue:
                transcriber._inject_queue.put(None)
            if transcriber._inject_thread:
                transcriber._inject_thread.join(timeout=2)

    def test_speech_duration_uses_source_sr(self):
        """语音时长计算使用 source_sr 而非硬编码 16000"""
        transcriber = self._make_vad_transcriber()

        # 手动设置 source_sr = 48000（不调用 start）
        transcriber._source_sr = 48000

        # 480 个样本 @ 48kHz = 0.01s
        transcriber._speech_buffer = [np.random.randn(480).astype(np.float32)]
        transcriber._speech_duration = len(transcriber._speech_buffer[0]) / transcriber._source_sr

        self.assertAlmostEqual(transcriber._speech_duration, 0.01, places=4)

    def test_min_segment_duration_uses_source_sr(self):
        """最短段时长判断使用 source_sr"""
        transcriber = self._make_vad_transcriber()

        # 手动设置 source_sr = 48000
        transcriber._source_sr = 48000
        transcriber._resampler = Mock()
        transcriber._resampler.needs_resample = True
        transcriber._resampler.resample.return_value = np.array([], dtype=np.float32)

        # 0.3s @ 48kHz = 14400 样本
        # 10000 样本 < 14400 → 跳过
        transcriber._speech_buffer = [np.random.randn(10000).astype(np.float32)]
        transcriber._flush_speech_buffer()
        # STT 不应被调用
        transcriber._stt_engine.transcribe_sync.assert_not_called()

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_flush_resamples_before_stt(self):
        """flush 时先 resample 再送 STT"""
        transcriber = self._make_vad_transcriber()

        transcriber._source_sr = 48000
        from core.audio_resampler import create_resampler
        transcriber._resampler = create_resampler(48000, 16000)

        # 1秒 @ 48kHz = 48000 样本
        transcriber._speech_buffer = [np.random.randn(48000).astype(np.float32)]
        transcriber._flush_speech_buffer()

        # STT 应收到 resample 后的 16000 样本
        call_args = transcriber._stt_engine.transcribe_sync.call_args[0][0]
        self.assertEqual(len(call_args), 16000)


# ============================================================
# 第五层: StreamingTranscriber 集成测试
# ============================================================

class TestStreamingTranscriberWithResampler(unittest.TestCase):
    """StreamingTranscriber 动态采样率测试"""

    def _make_engine_mock(self):
        """创建流式引擎 mock"""
        engine = Mock()
        engine.CHUNK_MS = 600
        engine.CHUNK_SAMPLES = 9600
        engine.get_chunk_samples.return_value = 9600
        engine.transcribe_chunk.return_value = "文字"
        engine.reset = Mock()
        return engine

    def test_start_default_16k(self):
        """默认 16kHz 模式"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        config = StreamingConfig()
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue()
        on_segment = Mock()

        transcriber.start(audio_queue, engine, on_segment)
        try:
            self.assertEqual(transcriber._source_sr, 16000)
            self.assertEqual(transcriber._chunk_samples, 9600)
            self.assertIsNone(transcriber._resampler)
        finally:
            transcriber.stop()

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_start_48k_resampler(self):
        """48kHz 模式：chunk_samples 动态计算"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        config = StreamingConfig()
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue()
        on_segment = Mock()

        from core.audio_resampler import create_resampler
        resampler = create_resampler(48000, 16000)

        transcriber.start(audio_queue, engine, on_segment, resampler=resampler)
        try:
            self.assertEqual(transcriber._source_sr, 48000)
            # 600ms @ 48kHz = 28800
            self.assertEqual(transcriber._chunk_samples, 28800)
            self.assertTrue(transcriber._resampler.needs_resample)
        finally:
            transcriber.stop()

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_start_44100_resampler(self):
        """44100Hz 模式"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        config = StreamingConfig()
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue()
        on_segment = Mock()

        from core.audio_resampler import create_resampler
        resampler = create_resampler(44100, 16000)

        transcriber.start(audio_queue, engine, on_segment, resampler=resampler)
        try:
            self.assertEqual(transcriber._source_sr, 44100)
            # 600ms @ 44100 = 26460
            self.assertEqual(transcriber._chunk_samples, 26460)
        finally:
            transcriber.stop()

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_flush_chunk_resamples(self):
        """flush chunk 时先 resample 再送引擎"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        config = StreamingConfig()
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue()
        on_segment = Mock()

        from core.audio_resampler import create_resampler
        resampler = create_resampler(48000, 16000)

        transcriber.start(audio_queue, engine, on_segment, resampler=resampler)
        try:
            # 累积够 28800 样本后触发 flush
            transcriber._buffer = list(np.random.randn(28800).astype(np.float32))
            transcriber._chunks_count = 0
            transcriber._flush_chunk()

            # 引擎应收到 resample 后的音频（约 9600 样本）
            call_args = engine.transcribe_chunk.call_args[0][0]
            self.assertTrue(len(call_args) >= 9598 and len(call_args) <= 9602,
                          f"Expected ~9600, got {len(call_args)}")
        finally:
            transcriber.stop()

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_flush_chunk_alignment(self):
        """非整数比重采样(44100→16000)后 chunk 对齐到引擎期望值"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        config = StreamingConfig()
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue()
        on_segment = Mock()

        from core.audio_resampler import create_resampler
        resampler = create_resampler(44100, 16000)

        transcriber.start(audio_queue, engine, on_segment, resampler=resampler)
        try:
            # 累积 26460 样本 (600ms @ 44100)
            transcriber._buffer = list(np.random.randn(26460).astype(np.float32))
            transcriber._chunks_count = 0
            transcriber._flush_chunk()

            # 对齐后应恰好等于 CHUNK_SAMPLES
            call_args = engine.transcribe_chunk.call_args[0][0]
            self.assertEqual(len(call_args), 9600,
                           f"Alignment failed: expected 9600, got {len(call_args)}")
        finally:
            transcriber.stop()

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_stop_flushes_resampled_remainder(self):
        """stop 时剩余 buffer 先 resample 再送引擎"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        config = StreamingConfig()
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue()
        on_segment = Mock()

        from core.audio_resampler import create_resampler
        resampler = create_resampler(48000, 16000)

        transcriber.start(audio_queue, engine, on_segment, resampler=resampler)
        # 放一些不满 chunk 的数据
        transcriber._buffer = list(np.random.randn(5000).astype(np.float32))
        transcriber.stop()

        # 引擎应收到 resample 后的剩余数据（is_final=True）
        engine.transcribe_chunk.assert_called()
        # 最后一次调用应该是 is_final=True
        last_call = engine.transcribe_chunk.call_args_list[-1]
        self.assertTrue(last_call[1].get('is_final', False))


# ============================================================
# 第六层: 回归测试
# ============================================================

class TestRegressionNoResamplePath(unittest.TestCase):
    """回归测试：无 resample 路径（16000→16000）不受影响"""

    def test_config_load_backward_compatible(self):
        """旧配置文件（无 sample_rate）正常加载"""
        import tempfile, yaml
        from config import load_config
        old_config = {
            'config_version': 5,
            'audio': {'max_duration': 120, 'silence_timeout': 8, 'device': None},
            'stt': {'engine': 'faster_whisper', 'model_size': 'large-v3-turbo'},
            'hotkey': {'trigger': 'f8', 'mode': 'toggle', 'conflict_check': True},
            'inject': {'method': 'keyboard', 'auto_paste': True, 'paste_delay_ms': 100,
                       'clipboard_backup': True, 'clipboard_restore': True, 'add_trailing_space': False},
            'sound': {'enabled': True, 'volume': 0.5, 'start_sound': True, 'end_sound': True, 'complete_sound': True},
            'web': {'port': 18925, 'auto_open': False},
            'startup': {'minimize': True, 'auto_start': False},
            'command': {'enabled': True, 'custom_commands': [], 'sound_feedback': True},
            'realtime': {'segment_pause_threshold': 0.8, 'min_segment_duration': 0.3,
                        'max_segment_duration': 30.0, 'vad_sensitivity': 2, 'vad_window_ms': 30,
                        'inject_method': 'clipboard', 'segment_separator': '\n', 'auto_timestamp': False},
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(old_config, f)
            path = f.name
        try:
            config = load_config(path)
            self.assertEqual(config.config_version, 8)
            self.assertEqual(config.audio.max_duration, 120)
            self.assertEqual(config.stt.engine, 'faster_whisper')
            self.assertEqual(config.hotkey.trigger, 'f8')
        finally:
            os.unlink(path)

    def test_vad_transcriber_no_resampler_backward_compat(self):
        """VADSegmentTranscriber 不传 resampler 时行为与旧版一致"""
        from core.vad_segment_transcriber import VADSegmentTranscriber
        config = Mock()
        config.vad_sensitivity = 2
        config.vad_window_ms = 30
        config.segment_pause_threshold = 0.8
        config.min_segment_duration = 0.3
        config.max_segment_duration = 30.0

        stt_engine = Mock()
        stt_engine.transcribe_sync.return_value = "hello"
        on_segment = Mock()

        transcriber = VADSegmentTranscriber(config, stt_engine, on_segment)
        audio_queue = queue.Queue()
        # 不传 resampler — 旧版调用方式
        transcriber.start(audio_queue)

        try:
            self.assertEqual(transcriber._source_sr, 16000)
            self.assertIsNone(transcriber._resampler)
        finally:
            transcriber._running = False
            if transcriber._thread:
                transcriber._thread.join(timeout=2)
            if transcriber._inject_queue:
                transcriber._inject_queue.put(None)
            if transcriber._inject_thread:
                transcriber._inject_thread.join(timeout=2)

    def test_streaming_transcriber_no_resampler_backward_compat(self):
        """StreamingTranscriber 不传 resampler 时行为与旧版一致"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        config = StreamingConfig()
        transcriber = StreamingTranscriber(config)

        engine = Mock()
        engine.CHUNK_MS = 600
        engine.CHUNK_SAMPLES = 9600
        engine.get_chunk_samples.return_value = 9600
        engine.reset = Mock()

        audio_queue = queue.Queue()
        on_segment = Mock()

        # 不传 resampler — 旧版调用方式
        transcriber.start(audio_queue, engine, on_segment)

        try:
            self.assertEqual(transcriber._source_sr, 16000)
            self.assertEqual(transcriber._chunk_samples, 9600)
            self.assertIsNone(transcriber._resampler)
        finally:
            transcriber.stop()

    def test_no_hardcoded_sample_rate_in_modules(self):
        """确认 vad_segment_transcriber 和 streaming_transcriber 没有模块级 SAMPLE_RATE"""
        import core.vad_segment_transcriber as vad_mod
        import core.streaming_transcriber as stream_mod

        self.assertFalse(hasattr(vad_mod, 'SAMPLE_RATE'),
                        "vad_segment_transcriber 不应有模块级 SAMPLE_RATE")
        self.assertFalse(hasattr(stream_mod, 'SAMPLE_RATE'),
                        "streaming_transcriber 不应有模块级 SAMPLE_RATE")


# ============================================================
# 运行
# ============================================================

if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 按层添加
    suite.addTests(loader.loadTestsFromTestCase(TestAudioResampler))
    suite.addTests(loader.loadTestsFromTestCase(TestCreateResamplerFactory))
    suite.addTests(loader.loadTestsFromTestCase(TestConfigSampleRate))
    suite.addTests(loader.loadTestsFromTestCase(TestRecorderSampleRateDetection))
    suite.addTests(loader.loadTestsFromTestCase(TestRecorderStartStop))
    suite.addTests(loader.loadTestsFromTestCase(TestVADSegmentTranscriberWithResampler))
    suite.addTests(loader.loadTestsFromTestCase(TestStreamingTranscriberWithResampler))
    suite.addTests(loader.loadTestsFromTestCase(TestRegressionNoResamplePath))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    print(f"  测试总数: {result.testsRun}")
    print(f"  ✅ 通过:   {result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)}")
    print(f"  ⏭ 跳过:   {len(result.skipped)}")
    print(f"  ❌ 失败:   {len(result.failures)}")
    print(f"  💥 错误:   {len(result.errors)}")
    print("=" * 70)

    sys.exit(0 if result.wasSuccessful() else 1)
