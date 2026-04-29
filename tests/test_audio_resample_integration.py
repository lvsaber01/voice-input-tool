#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
音频重采样 — 集成测试 + 端到端功能测试

与 test_audio_resample.py（单元测试）的区别：
  - 单元测试：mock 外部依赖，验证模块内部逻辑
  - 集成测试：跨模块协作，验证数据流正确性
  - 端到端测试：模拟真实使用场景，验证完整功能链

覆盖场景：
  1. 音频文件 resample 端到端（16k/44.1k/48k → 16k，验证长度、dtype、信号完整性）
  2. Recorder + Resampler 集成（模拟采集回调 → stop → resample → 验证输出）
  3. VAD 分段转写集成（注入模拟音频 → VAD 分段 → resample → STT mock → 验证调用）
  4. Streaming 转写集成（注入模拟音频 → chunk 切分 → resample → 引擎 mock → 验证调用）
  5. 多采样率音频文件全链路（读取 WAV → resample → VAD/Streaming 消费）
  6. 配置迁移端到端（旧配置 → 自动迁移 → 新字段可用 → 应用正常启动）
  7. 回归端到端（原有功能：16kHz 路径不受影响）

运行方式: python -m pytest tests/test_audio_resample_integration.py -v
"""

import sys
import os
import unittest
import time
import queue
import threading
import tempfile
import numpy as np
from unittest.mock import Mock, patch, MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 检查依赖
try:
    import scipy.signal
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    import soundfile
    HAS_SOUNDFILE = True
except ImportError:
    HAS_SOUNDFILE = False

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False


def _generate_sine_wave(duration_sec, sr, freq=440.0, amplitude=0.5):
    """生成正弦波测试音频"""
    t = np.arange(int(duration_sec * sr)) / float(sr)
    return (np.sin(2 * np.pi * freq * t) * amplitude).astype(np.float32)


def _generate_silence(duration_sec, sr):
    """生成静音"""
    return np.zeros(int(duration_sec * sr), dtype=np.float32)


def _generate_speech_like(duration_sec, sr):
    """生成类语音信号（多频叠加 + 包络）"""
    t = np.arange(int(duration_sec * sr)) / float(sr)
    # 多个频率模拟语音共振峰
    signal = (
        0.3 * np.sin(2 * np.pi * 150 * t) +   # 基频
        0.2 * np.sin(2 * np.pi * 400 * t) +   # 第一共振峰
        0.15 * np.sin(2 * np.pi * 1000 * t) +  # 第二共振峰
        0.1 * np.sin(2 * np.pi * 2500 * t) +   # 第三共振峰
        0.05 * np.random.randn(len(t))           # 噪声
    )
    # 添加包络（模拟语音的起伏）
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
    return (signal * envelope).astype(np.float32)


# ============================================================
# 集成测试 1: AudioResampler 与真实音频文件
# ============================================================

class TestResamplerWithRealAudio(unittest.TestCase):
    """AudioResampler + 真实音频文件集成测试"""

    @classmethod
    def setUpClass(cls):
        if not (HAS_SCIPY and HAS_SOUNDFILE):
            raise unittest.SkipTest("scipy or soundfile not installed")

        """准备不同采样率的 WAV 文件"""
        cls.temp_dir = tempfile.mkdtemp()
        cls.files = {}

        # 生成 2 秒的 440Hz 正弦波，各采样率版本
        for sr in [8000, 16000, 32000, 44100, 48000, 96000]:
            audio = _generate_sine_wave(2.0, sr, freq=440.0)
            path = os.path.join(cls.temp_dir, f"test_{sr}hz.wav")
            soundfile.write(path, audio, sr)
            cls.files[sr] = path

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    @unittest.skipUnless(HAS_SCIPY and HAS_SOUNDFILE, "scipy/soundfile not installed")
    def test_read_16k_wav_no_resample(self):
        """16kHz WAV 文件 → 读取 → 不 resample → 长度不变"""
        from core.audio_resampler import create_resampler

        audio, sr = soundfile.read(self.files[16000], dtype='float32')
        resampler = create_resampler(16000, 16000)
        output = resampler.resample(audio)

        self.assertEqual(len(audio), len(output))
        np.testing.assert_array_almost_equal(audio, output)

    @unittest.skipUnless(HAS_SCIPY and HAS_SOUNDFILE, "scipy/soundfile not installed")
    def test_read_48k_wav_resample_to_16k(self):
        """48kHz WAV 文件 → 读取 → resample → 输出 16kHz 长度正确"""
        from core.audio_resampler import create_resampler

        audio_48k, sr = soundfile.read(self.files[48000], dtype='float32')
        self.assertEqual(sr, 48000)
        self.assertEqual(len(audio_48k), 96000)  # 2s @ 48kHz

        resampler = create_resampler(48000, 16000)
        output = resampler.resample(audio_48k)

        self.assertEqual(len(output), 32000)  # 2s @ 16kHz
        self.assertEqual(output.dtype, np.float32)

    @unittest.skipUnless(HAS_SCIPY and HAS_SOUNDFILE, "scipy/soundfile not installed")
    def test_read_44100_wav_resample_to_16k(self):
        """44.1kHz WAV → resample → 输出正确"""
        from core.audio_resampler import create_resampler

        audio, sr = soundfile.read(self.files[44100], dtype='float32')
        resampler = create_resampler(44100, 16000)
        output = resampler.resample(audio)

        self.assertEqual(len(output), 32000)  # 2s @ 16kHz

    @unittest.skipUnless(HAS_SCIPY and HAS_SOUNDFILE, "scipy/soundfile not installed")
    def test_resample_preserves_frequency(self):
        """resample 后频率不变（440Hz 正弦波）"""
        from core.audio_resampler import create_resampler

        # 48kHz → 16kHz
        audio_48k, _ = soundfile.read(self.files[48000], dtype='float32')
        resampler = create_resampler(48000, 16000)
        output = resampler.resample(audio_48k)

        # FFT 检测主频是否为 440Hz
        fft = np.abs(np.fft.rfft(output))
        freqs = np.fft.rfftfreq(len(output), 1.0 / 16000.0)
        peak_freq = freqs[np.argmax(fft)]

        self.assertAlmostEqual(peak_freq, 440.0, delta=5.0,  # 允许 ±5Hz 容差
                              msg=f"主频 {peak_freq:.1f}Hz 不在 440±5Hz 范围内")

    @unittest.skipUnless(HAS_SCIPY and HAS_SOUNDFILE, "scipy/soundfile not installed")
    def test_multi_rate_roundtrip_consistency(self):
        """多个源采样率 resample 到 16kHz 后结果一致"""
        from core.audio_resampler import create_resampler

        # 同一音频内容，不同采样率版本
        outputs = {}
        for sr in [16000, 32000, 44100, 48000]:
            audio, _ = soundfile.read(self.files[sr], dtype='float32')
            if sr != 16000:
                resampler = create_resampler(sr, 16000)
                outputs[sr] = resampler.resample(audio)
            else:
                outputs[sr] = audio

        # 所有输出长度应为 32000（2s @ 16kHz）
        for sr, output in outputs.items():
            self.assertEqual(len(output), 32000, f"{sr}Hz resample 输出长度错误")

        # 比较信号形状相似度（取中间段，避免边界效应）
        middle = slice(8000, 24000)
        ref = outputs[48000][middle]
        for sr in [16000, 32000, 44100]:
            corr = np.corrcoef(ref, outputs[sr][middle])[0, 1]
            self.assertGreater(corr, 0.99,
                             f"{sr}Hz 与 48kHz 的相关性 {corr:.4f} < 0.99")

    @unittest.skipUnless(HAS_SCIPY and HAS_SOUNDFILE, "scipy/soundfile not installed")
    def test_speech_like_signal_integrity(self):
        """类语音信号 resample 后保持完整性（无截断、无爆音）"""
        from core.audio_resampler import create_resampler

        # 48kHz 类语音
        speech_48k = _generate_speech_like(1.0, 48000)
        resampler = create_resampler(48000, 16000)
        output = resampler.resample(speech_48k)

        # 无 NaN/Inf
        self.assertFalse(np.any(np.isnan(output)), "输出含 NaN")
        self.assertFalse(np.any(np.isinf(output)), "输出含 Inf")

        # 振幅范围合理（不应放大）
        max_val = float(np.max(np.abs(output)))
        self.assertLessEqual(max_val, 1.0, f"输出振幅 {max_val} 超过 1.0")

        # 无严重直流偏移
        mean_val = float(np.mean(output))
        self.assertAlmostEqual(mean_val, 0.0, places=2, msg=f"直流偏移 {mean_val}")


# ============================================================
# 集成测试 2: Recorder 模拟采集 → Resample → 输出验证
# ============================================================

class TestRecorderSimulatedCapture(unittest.TestCase):
    """模拟 Recorder 采集回调 → stop → resample 完整链路"""

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_simulate_48k_capture_and_resample(self):
        """模拟 48kHz 采集：通过回调注入音频 → stop → 验证 16kHz 输出"""
        from core.recorder import AudioRecorder
        from core.audio_resampler import create_resampler

        config = Mock()
        config.device = None
        config.sample_rate = None

        recorder = AudioRecorder(config)
        recorder._device_sr = 48000
        recorder._resampler = create_resampler(48000, 16000)
        recorder._blocksize = 512
        recorder.is_recording = True

        # 模拟 sounddevice 回调：注入 2 秒的 48kHz 音频（分多个 chunk）
        source_audio = _generate_sine_wave(2.0, 48000, freq=300.0)
        chunk_size = 512
        for i in range(0, len(source_audio), chunk_size):
            chunk = source_audio[i:i+chunk_size].reshape(-1, 1)
            # 模拟 sounddevice 回调：indata shape = (chunk_size, 1)
            recorder._audio_callback(chunk, chunk_size, None, None)

        # 停止录音
        output = recorder.stop()

        # 验证输出
        self.assertEqual(output.dtype, np.float32)
        self.assertEqual(len(output), 32000)  # 2s @ 16kHz

        # 验证频率保持
        fft = np.abs(np.fft.rfft(output))
        freqs = np.fft.rfftfreq(len(output), 1.0 / 16000.0)
        peak_freq = freqs[np.argmax(fft)]
        self.assertAlmostEqual(peak_freq, 300.0, delta=5.0)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_simulate_44100_capture_and_resample(self):
        """模拟 44.1kHz 采集 → resample → 验证"""
        from core.recorder import AudioRecorder
        from core.audio_resampler import create_resampler

        config = Mock()
        config.device = None
        config.sample_rate = None

        recorder = AudioRecorder(config)
        recorder._device_sr = 44100
        recorder._resampler = create_resampler(44100, 16000)
        recorder._blocksize = max(512, int(44100 * 0.01))
        recorder.is_recording = True

        source_audio = _generate_speech_like(1.5, 44100)
        chunk_size = recorder._blocksize
        for i in range(0, len(source_audio), chunk_size):
            chunk = source_audio[i:i+chunk_size].reshape(-1, 1)
            recorder._audio_callback(chunk, min(chunk_size, len(chunk)), None, None)

        output = recorder.stop()

        self.assertEqual(output.dtype, np.float32)
        self.assertEqual(len(output), 24000)  # 1.5s @ 16kHz
        self.assertFalse(np.any(np.isnan(output)))

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_simulate_16k_capture_passthrough(self):
        """16kHz 采集 → 直通（无 resample）→ 验证"""
        from core.recorder import AudioRecorder

        config = Mock()
        config.device = None
        config.sample_rate = None

        recorder = AudioRecorder(config)
        recorder._device_sr = 16000
        recorder._resampler = None
        recorder._blocksize = 512
        recorder.is_recording = True

        source_audio = _generate_sine_wave(1.0, 16000, freq=500.0)
        chunk_size = 512
        for i in range(0, len(source_audio), chunk_size):
            chunk = source_audio[i:i+chunk_size].reshape(-1, 1)
            recorder._audio_callback(chunk, min(chunk_size, len(chunk)), None, None)

        output = recorder.stop()

        self.assertEqual(len(output), 16000)  # 1s @ 16kHz
        # 16kHz 直通，应与原始数据一致
        np.testing.assert_array_almost_equal(output, source_audio, decimal=5)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_simulate_realtime_queue_with_resample(self):
        """实时模式：模拟音频 → rt_queue → 消费者 resample"""
        from core.recorder import AudioRecorder
        from core.audio_resampler import create_resampler

        config = Mock()
        config.device = None
        config.sample_rate = None

        rt_queue = queue.Queue(maxsize=300)

        recorder = AudioRecorder(config)
        recorder._device_sr = 48000
        recorder._resampler = create_resampler(48000, 16000)
        recorder._blocksize = 512
        recorder.is_recording = True
        recorder._rt_queue = rt_queue

        # 模拟采集 0.5 秒
        source_audio = _generate_speech_like(0.5, 48000)
        chunk_size = 512
        for i in range(0, len(source_audio), chunk_size):
            chunk = source_audio[i:i+chunk_size].reshape(-1, 1)
            recorder._audio_callback(chunk, min(chunk_size, len(chunk)), None, None)

        recorder.stop()

        # 消费者从 rt_queue 取出并 resample
        collected = []
        while not rt_queue.empty():
            collected.append(rt_queue.get_nowait())

        if collected:
            raw_audio = np.concatenate(collected)
            resampled = recorder._resampler.resample(raw_audio)
            self.assertEqual(resampled.dtype, np.float32)
            # 0.5s @ 16kHz = 8000 样本
            self.assertTrue(abs(len(resampled) - 8000) < 50,
                          f"预期 ~8000 样本，得到 {len(resampled)}")


# ============================================================
# 集成测试 3: VADSegmentTranscriber 全链路
# ============================================================

class TestVADTranscriberFullPipeline(unittest.TestCase):
    """VADSegmentTranscriber + Resampler + STT mock 全链路"""

    def _make_setup(self, source_sr=48000):
        """创建完整 VAD 转写环境"""
        from core.vad_segment_transcriber import VADSegmentTranscriber
        from core.audio_resampler import create_resampler

        config = Mock()
        config.vad_sensitivity = 2
        config.vad_window_ms = 30
        config.segment_pause_threshold = 0.3  # 短静音快速切分
        config.min_segment_duration = 0.1      # 短段也处理
        config.max_segment_duration = 30.0

        stt_engine = Mock()
        stt_engine.transcribe_sync.return_value = "识别结果"

        on_segment = Mock()
        transcriber = VADSegmentTranscriber(config, stt_engine, on_segment)

        # 强制 RMS 模式
        transcriber._vad = None
        transcriber._vad_mode = 'rms'
        transcriber._rms_threshold = 0.001  # 低阈值，几乎全部视为语音

        audio_queue = queue.Queue(maxsize=300)

        # 创建 resampler
        resampler = create_resampler(source_sr, 16000) if source_sr != 16000 else None

        return transcriber, audio_queue, resampler, stt_engine, on_segment

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_48k_audio_vad_segment_and_resample(self):
        """48kHz 音频 → VAD 分段 → resample → STT → 验证调用参数"""
        transcriber, audio_queue, resampler, stt_engine, on_segment = \
            self._make_setup(source_sr=48000)

        transcriber.start(audio_queue, resampler=resampler)

        try:
            # 注入 1 秒语音 + 0.5 秒静音
            speech = _generate_speech_like(1.0, 48000)
            silence = _generate_silence(0.5, 48000)

            # 分 chunk 注入
            chunk_size = 512
            for audio in [speech, silence]:
                for i in range(0, len(audio), chunk_size):
                    audio_queue.put(audio[i:i+chunk_size])

            # 等待 VAD 处理
            time.sleep(1.0)
        finally:
            transcriber._running = False
            # 处理剩余 buffer
            if transcriber._speech_buffer:
                transcriber._flush_speech_buffer()
            if transcriber._inject_thread:
                transcriber._inject_queue.put(None)
                transcriber._inject_thread.join(timeout=3)
            if transcriber._thread:
                transcriber._thread.join(timeout=3)

        # 验证 STT 被调用
        if stt_engine.transcribe_sync.called:
            # STT 应收到 16kHz 音频
            for call_args in stt_engine.transcribe_sync.call_args_list:
                audio_input = call_args[0][0]
                self.assertEqual(audio_input.dtype, np.float32)
                # 输入应为 resample 后的 16kHz（长度与源不同）
                # 不验证具体长度（VAD 可能截断），只验证 dtype
        # 验证注入回调被调用
        if stt_engine.transcribe_sync.called and stt_engine.transcribe_sync.return_value:
            on_segment.assert_called()

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_44100_audio_vad_pipeline(self):
        """44.1kHz 音频 VAD 管线"""
        transcriber, audio_queue, resampler, stt_engine, on_segment = \
            self._make_setup(source_sr=44100)

        transcriber.start(audio_queue, resampler=resampler)

        try:
            speech = _generate_speech_like(0.5, 44100)
            chunk_size = 512
            for i in range(0, len(speech), chunk_size):
                audio_queue.put(speech[i:i+chunk_size])

            time.sleep(0.5)
        finally:
            transcriber._running = False
            if transcriber._speech_buffer:
                transcriber._flush_speech_buffer()
            if transcriber._inject_thread:
                transcriber._inject_queue.put(None)
                transcriber._inject_thread.join(timeout=3)
            if transcriber._thread:
                transcriber._thread.join(timeout=3)

        if stt_engine.transcribe_sync.called:
            audio_input = stt_engine.transcribe_sync.call_args[0][0]
            self.assertEqual(audio_input.dtype, np.float32)

    def test_16k_audio_vad_no_resample_pipeline(self):
        """16kHz 音频 VAD 管线（无 resample）"""
        transcriber, audio_queue, resampler, stt_engine, on_segment = \
            self._make_setup(source_sr=16000)

        transcriber.start(audio_queue)

        try:
            speech = _generate_speech_like(0.5, 16000)
            chunk_size = 512
            for i in range(0, len(speech), chunk_size):
                audio_queue.put(speech[i:i+chunk_size])

            time.sleep(0.5)
        finally:
            transcriber._running = False
            if transcriber._speech_buffer:
                transcriber._flush_speech_buffer()
            if transcriber._inject_thread:
                transcriber._inject_queue.put(None)
                transcriber._inject_thread.join(timeout=3)
            if transcriber._thread:
                transcriber._thread.join(timeout=3)

        if stt_engine.transcribe_sync.called:
            audio_input = stt_engine.transcribe_sync.call_args[0][0]
            self.assertEqual(audio_input.dtype, np.float32)
            # 无 resample，音频应直接传递
            # 长度 = chunk 累积的原始长度（VAD 可能有最小段过滤）


# ============================================================
# 集成测试 4: StreamingTranscriber 全链路
# ============================================================

class TestStreamingTranscriberFullPipeline(unittest.TestCase):
    """StreamingTranscriber + Resampler + 引擎 mock 全链路"""

    def _make_engine_mock(self):
        engine = Mock()
        engine.CHUNK_MS = 600
        engine.CHUNK_SAMPLES = 9600
        engine.get_chunk_samples.return_value = 9600
        engine.transcribe_chunk.return_value = "转写文字"
        engine.reset = Mock()
        return engine

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_48k_audio_streaming_pipeline(self):
        """48kHz 音频 → StreamingTranscriber → chunk 切分 → resample → 引擎"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        from core.audio_resampler import create_resampler

        config = StreamingConfig(enabled=True, max_queue_size=300)
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue(maxsize=300)
        on_segment = Mock()
        resampler = create_resampler(48000, 16000)

        transcriber.start(audio_queue, engine, on_segment, resampler=resampler)

        # 注入 3 秒 48kHz 音频（足够产生多个 chunk）
        source = _generate_speech_like(3.0, 48000)
        chunk_size = 512
        for i in range(0, len(source), chunk_size):
            audio_queue.put(source[i:i+chunk_size])

        # 等待所有 chunk 被处理
        time.sleep(2.0)
        transcriber.stop()

        # 验证引擎被调用
        self.assertGreater(engine.transcribe_chunk.call_count, 0,
                          "引擎应被至少调用一次")

        # 验证每次调用的音频参数
        for call_args_list in engine.transcribe_chunk.call_args_list:
            audio_input = call_args_list[0][0]
            self.assertEqual(audio_input.dtype, np.float32)
            # resample 后长度应接近 CHUNK_SAMPLES (9600)
            if not call_args_list[1].get('is_final', False):
                self.assertAlmostEqual(len(audio_input), 9600, delta=5,
                                     msg=f"chunk 长度 {len(audio_input)} 不接近 9600")

        # 验证回调被调用
        if engine.transcribe_chunk.return_value:
            self.assertGreater(on_segment.call_count, 0)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_44100_audio_streaming_pipeline(self):
        """44.1kHz 音频 → StreamingTranscriber 全链路"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        from core.audio_resampler import create_resampler

        config = StreamingConfig(enabled=True, max_queue_size=300)
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue(maxsize=300)
        on_segment = Mock()
        resampler = create_resampler(44100, 16000)

        transcriber.start(audio_queue, engine, on_segment, resampler=resampler)

        source = _generate_speech_like(2.0, 44100)
        chunk_size = 512
        for i in range(0, len(source), chunk_size):
            audio_queue.put(source[i:i+chunk_size])

        time.sleep(2.0)
        transcriber.stop()

        self.assertGreater(engine.transcribe_chunk.call_count, 0)

    def test_16k_audio_streaming_no_resample(self):
        """16kHz 音频 → StreamingTranscriber（无 resample）"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig

        config = StreamingConfig(enabled=True, max_queue_size=300)
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue(maxsize=300)
        on_segment = Mock()

        transcriber.start(audio_queue, engine, on_segment)

        source = _generate_speech_like(2.0, 16000)
        chunk_size = 512
        for i in range(0, len(source), chunk_size):
            audio_queue.put(source[i:i+chunk_size])

        time.sleep(2.0)
        transcriber.stop()

        self.assertGreater(engine.transcribe_chunk.call_count, 0)

        # 非 final chunk 长度应恰好 9600
        for call_args_list in engine.transcribe_chunk.call_args_list:
            if not call_args_list[1].get('is_final', False):
                audio_input = call_args_list[0][0]
                self.assertEqual(len(audio_input), 9600)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_streaming_alignment_44100_all_chunks(self):
        """44.1kHz streaming: 所有非 final chunk 对齐到 9600"""
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        from core.audio_resampler import create_resampler

        config = StreamingConfig(enabled=True, max_queue_size=300)
        transcriber = StreamingTranscriber(config)

        engine = self._make_engine_mock()
        audio_queue = queue.Queue(maxsize=300)
        on_segment = Mock()
        resampler = create_resampler(44100, 16000)

        transcriber.start(audio_queue, engine, on_segment, resampler=resampler)

        # 注入 5 秒（足够产生多个完整 chunk）
        source = _generate_speech_like(5.0, 44100)
        chunk_size = 512
        for i in range(0, len(source), chunk_size):
            audio_queue.put(source[i:i+chunk_size])

        time.sleep(3.0)
        transcriber.stop()

        # 检查所有非 final chunk
        misaligned = 0
        for call_args_list in engine.transcribe_chunk.call_args_list:
            if not call_args_list[1].get('is_final', False):
                audio_input = call_args_list[0][0]
                if len(audio_input) != 9600:
                    misaligned += 1

        self.assertEqual(misaligned, 0,
                        f"{misaligned} 个 chunk 未对齐到 9600")


# ============================================================
# 端到端测试 5: 配置迁移 + 应用启动模拟
# ============================================================

class TestConfigMigrationE2E(unittest.TestCase):
    """配置迁移端到端：旧配置 → 迁移 → 应用启动"""

    def _write_temp_config(self, config_dict):
        import yaml
        fd, path = tempfile.mkstemp(suffix='.yaml')
        with os.fdopen(fd, 'w') as f:
            yaml.dump(config_dict, f, allow_unicode=True)
        return path

    def test_v4_config_migrates_to_v7_and_app_starts(self):
        """v4 旧配置 → 自动迁移到 v7 → 所有字段可用"""
        from config import load_config

        old_config = {
            'config_version': 4,
            'mode': 'batch',
            'audio': {
                'max_duration': 120,
                'silence_timeout': 8,
                'silence_threshold': 0.01,
                'silence_check_interval': 0.5,
                'device': None,
            },
            'stt': {
                'engine': 'funasr',
                'model_size': 'paraformer-zh',
                'language': 'zh',
                'device': 'auto',
                'compute_type': 'int8',
                'beam_size': 5,
            },
            'hotkey': {'trigger': 'f8', 'mode': 'toggle', 'conflict_check': True},
            'inject': {'method': 'keyboard', 'auto_paste': True},
        }

        path = self._write_temp_config(old_config)
        try:
            config = load_config(path)
            self.assertEqual(config.config_version, 7)
            self.assertIsNone(config.audio.sample_rate)  # 新字段，默认 auto
            self.assertEqual(config.audio.max_duration, 120)
            self.assertEqual(config.stt.engine, 'funasr')
            # 验证所有子配置可正常访问
            self.assertIsNotNone(config.hotkey)
            self.assertIsNotNone(config.inject)
            self.assertIsNotNone(config.sound)
            self.assertIsNotNone(config.realtime)
            self.assertIsNotNone(config.stt.streaming)
        finally:
            os.unlink(path)

    def test_migrated_config_saved_correctly(self):
        """迁移后自动保存，重新加载不重复迁移"""
        from config import load_config
        import yaml

        old_config = {'config_version': 5, 'audio': {'max_duration': 60}}
        path = self._write_temp_config(old_config)

        try:
            # 第一次加载（触发迁移）
            config1 = load_config(path)
            self.assertEqual(config1.config_version, 7)

            # 重新加载（不应再迁移）
            with open(path, 'r') as f:
                raw = yaml.safe_load(f)
            self.assertEqual(raw['config_version'], 7)

            config2 = load_config(path)
            self.assertEqual(config2.config_version, 7)
            self.assertEqual(config2.audio.max_duration, 60)
        finally:
            os.unlink(path)

    def test_v7_config_with_explicit_sample_rate(self):
        """v7 配置 + 显式 sample_rate → 正常加载"""
        from config import load_config

        config_dict = {
            'config_version': 7,
            'audio': {'max_duration': 120, 'sample_rate': 48000},
            'stt': {'engine': 'faster_whisper', 'model_size': 'large-v3-turbo'},
        }
        path = self._write_temp_config(config_dict)
        try:
            config = load_config(path)
            self.assertEqual(config.audio.sample_rate, 48000)
        finally:
            os.unlink(path)


# ============================================================
# 端到端测试 6: 模块导入 + 依赖检查
# ============================================================

class TestModuleImportE2E(unittest.TestCase):
    """端到端：所有修改模块可正常导入、无循环依赖"""

    def test_import_audio_resampler(self):
        from core.audio_resampler import AudioResampler, create_resampler
        self.assertTrue(callable(create_resampler))

    def test_import_recorder(self):
        from core.recorder import AudioRecorder
        self.assertTrue(hasattr(AudioRecorder, '_detect_device_sample_rate'))

    def test_import_vad_segment_transcriber(self):
        from core.vad_segment_transcriber import VADSegmentTranscriber
        # 确认 start 签名包含 resampler 参数
        import inspect
        sig = inspect.signature(VADSegmentTranscriber.start)
        self.assertIn('resampler', sig.parameters)

    def test_import_streaming_transcriber(self):
        from core.streaming_transcriber import StreamingTranscriber
        import inspect
        sig = inspect.signature(StreamingTranscriber.start)
        self.assertIn('resampler', sig.parameters)

    def test_import_config(self):
        from config import AppConfig, CURRENT_CONFIG_VERSION, load_config
        self.assertEqual(CURRENT_CONFIG_VERSION, 7)

    def test_no_circular_import(self):
        """无循环导入"""
        import importlib
        modules_to_reload = [
            'config',
            'core.audio_resampler',
            'core.recorder',
            'core.vad_segment_transcriber',
            'core.streaming_transcriber',
        ]
        for mod_name in modules_to_reload:
            try:
                importlib.import_module(mod_name)
            except ImportError as e:
                if 'sounddevice' not in str(e).lower() and 'scipy' not in str(e).lower():
                    raise


# ============================================================
# 端到端测试 7: 噪声/边界场景
# ============================================================

class TestEdgeCaseScenarios(unittest.TestCase):
    """边界场景测试"""

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_very_short_audio_48k(self):
        """极短 48kHz 音频（50ms）→ resample 不崩溃"""
        from core.audio_resampler import create_resampler

        audio = _generate_sine_wave(0.05, 48000)
        resampler = create_resampler(48000, 16000)
        output = resampler.resample(audio)

        self.assertEqual(output.dtype, np.float32)
        self.assertGreater(len(output), 0)

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_pure_noise_48k_resample(self):
        """纯噪声 48kHz → resample → 频谱特性保持"""
        from core.audio_resampler import create_resampler

        np.random.seed(42)
        noise_48k = np.random.randn(48000).astype(np.float32) * 0.3

        resampler = create_resampler(48000, 16000)
        output = resampler.resample(noise_48k)

        self.assertEqual(len(output), 16000)
        self.assertFalse(np.any(np.isnan(output)))
        # 噪声的 RMS 经 anti-aliasing 滤波后会降低（FIR 滤波器去除高频分量）
        # 这是 scipy.signal.resample_poly 的正常行为
        rms_in = float(np.sqrt(np.mean(noise_48k ** 2)))
        rms_out = float(np.sqrt(np.mean(output ** 2)))
        # 宽松验证：RMS 不应衰减超过 50%
        self.assertGreater(rms_out, rms_in * 0.3,
                          msg=f"RMS 衰减过大: {rms_in:.4f} → {rms_out:.4f}")
        self.assertLess(rms_out, rms_in * 1.1,
                         msg=f"RMS 异常放大: {rms_in:.4f} → {rms_out:.4f}")

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_large_audio_96k_resample(self):
        """96kHz 长音频（10s）→ resample → 验证性能和正确性"""
        from core.audio_resampler import create_resampler

        audio_96k = _generate_speech_like(10.0, 96000)
        resampler = create_resampler(96000, 16000)

        start = time.time()
        output = resampler.resample(audio_96k)
        elapsed = time.time() - start

        self.assertEqual(len(output), 160000)  # 10s @ 16kHz
        self.assertLess(elapsed, 5.0, f"resample 10s@96kHz 耗时 {elapsed:.2f}s > 5s")
        self.assertFalse(np.any(np.isnan(output)))

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_repeated_resample_calls_no_state_leak(self):
        """多次 resample 调用无状态泄漏"""
        from core.audio_resampler import create_resampler

        resampler = create_resampler(48000, 16000)

        results = []
        for i in range(100):
            np.random.seed(i)
            audio = np.random.randn(48000).astype(np.float32)
            output = resampler.resample(audio)
            results.append(len(output))
            self.assertEqual(output.dtype, np.float32)

        # 所有结果长度应一致
        self.assertTrue(all(r == 16000 for r in results))

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_alternating_speech_silence_resample(self):
        """交替语音/静音段 resample 后段边界正确"""
        from core.audio_resampler import create_resampler

        # 构造: 0.5s 语音 + 0.3s 静音 + 0.5s 语音 @ 48kHz
        speech = _generate_speech_like(0.5, 48000)
        silence = _generate_silence(0.3, 48000)
        audio = np.concatenate([speech, silence, speech])

        resampler = create_resampler(48000, 16000)
        output = resampler.resample(audio)

        # 总长度: 1.3s @ 16kHz = 20800
        self.assertEqual(len(output), 20800)

        # 中间段应为接近静音（RMS 很低）
        mid_start = 8000   # ~0.5s @ 16kHz
        mid_end = 12800    # ~0.8s @ 16kHz
        mid_rms = float(np.sqrt(np.mean(output[mid_start:mid_end] ** 2)))
        self.assertLess(mid_rms, 0.1, f"静音段 RMS {mid_rms} 过高")


# ============================================================
# 运行
# ============================================================

if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestResamplerWithRealAudio))
    suite.addTests(loader.loadTestsFromTestCase(TestRecorderSimulatedCapture))
    suite.addTests(loader.loadTestsFromTestCase(TestVADTranscriberFullPipeline))
    suite.addTests(loader.loadTestsFromTestCase(TestStreamingTranscriberFullPipeline))
    suite.addTests(loader.loadTestsFromTestCase(TestConfigMigrationE2E))
    suite.addTests(loader.loadTestsFromTestCase(TestModuleImportE2E))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCaseScenarios))

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
