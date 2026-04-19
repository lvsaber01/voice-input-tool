"""全面集成测试 — 验证各模块真实协作的正确性。

不依赖真实麦克风、显示器、STT 模型。
"""

import io
import os
import re
import sys
import json
import time
import queue
import struct
import unittest
import threading
import wave
import tempfile
import shutil
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from dataclasses import asdict

import numpy as np

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    AppConfig, AudioConfig, STTConfig, SoundConfig, InjectConfig,
    HotkeyConfig, WebConfig, StartupConfig, CommandConfig, RealtimeConfig,
    load_config, save_config, _migrate_v1_to_v2, _migrate_v2_to_v3,
    _flatten_to_appconfig, _dataclass_to_dict, CURRENT_CONFIG_VERSION,
)
from core.events import EventBus, EngineEvent
from core.silence_detector import SilenceDetector
from core.command import CommandMatcher, CommandExecutor, DEFAULT_COMMANDS
from core.stats import UsageStats
from core.sound_player import SoundPlayer


# ================================================================
# 辅助工具
# ================================================================

def generate_sine_wave(duration_s: float, freq: float = 440.0,
                       sample_rate: int = 16000, amplitude: float = 0.5) -> np.ndarray:
    """生成正弦波音频（模拟语音）"""
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def generate_silence(duration_s: float, sample_rate: int = 16000) -> np.ndarray:
    """生成静音数据"""
    return np.zeros(int(sample_rate * duration_s), dtype=np.float32)


def create_wav_file(path, data: np.ndarray, sample_rate: int = 16000, sampwidth: int = 2):
    """用标准库生成 WAV 文件"""
    if sampwidth == 2:
        raw = (data * 32768).astype(np.int16)
    elif sampwidth == 4:
        raw = (data * 2147483648).astype(np.int32)
    else:
        raise ValueError(f"Unsupported sampwidth: {sampwidth}")

    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(raw.tobytes())


# ================================================================
# 1. 模块真实初始化测试
# ================================================================

class TestModuleInit(unittest.TestCase):
    """验证所有模块在 macOS 环境下能正常 import 和实例化。"""

    def test_eventbus_init_and_shutdown(self):
        bus = EventBus()
        bus.shutdown()

    def test_usagestats_init(self):
        with tempfile.TemporaryDirectory() as d:
            stats = UsageStats(stats_dir=d)
            self.assertEqual(stats._data["transcribe_count"], 0)

    def test_commandmatcher_init_default(self):
        cm = CommandMatcher()
        self.assertGreater(len(cm._commands), 0)

    def test_commandmatcher_init_custom(self):
        from core.command import VoiceCommand, CommandType
        cmds = [VoiceCommand("test", [r"测试"], CommandType.TEXT_REPLACE, "X")]
        cm = CommandMatcher(commands=cmds)
        self.assertEqual(len(cm._commands), 1)

    def test_soundplayer_init(self):
        sp = SoundPlayer(SoundConfig())
        self.assertEqual(sp._sounds, {})

    def test_appconfig_defaults(self):
        cfg = AppConfig()
        self.assertEqual(cfg.mode, "batch")
        self.assertEqual(cfg.config_version, 2)
        self.assertIsInstance(cfg.audio, AudioConfig)
        self.assertIsInstance(cfg.stt, STTConfig)

    def test_audioconfig_validation(self):
        with self.assertRaises(ValueError):
            AudioConfig(max_duration=0)
        with self.assertRaises(ValueError):
            AudioConfig(silence_threshold=0)

    def test_sttconfig_validation(self):
        with self.assertRaises(ValueError):
            STTConfig(model_size="invalid")
        with self.assertRaises(ValueError):
            STTConfig(beam_size=0)

    def test_hotkeyconfig_validation(self):
        with self.assertRaises(ValueError):
            HotkeyConfig(mode="invalid")

    def test_soundconfig_validation(self):
        with self.assertRaises(ValueError):
            SoundConfig(volume=1.5)

    def test_recorder_init(self):
        from core.recorder import AudioRecorder
        rec = AudioRecorder(AudioConfig())
        self.assertFalse(rec.is_recording)
        self.assertIsNotNone(rec.get_buffer_queue())

    def test_silence_detector_init(self):
        sd = SilenceDetector(AudioConfig(), lambda: None)
        self.assertFalse(sd._detecting)


# ================================================================
# 2. 合成音频测试
# ================================================================

class TestSyntheticAudio(unittest.TestCase):
    """用 numpy 合成音频测试 SilenceDetector 和 RMS 计算。"""

    def test_rms_calculation_sine(self):
        """正弦波 RMS 应接近 amplitude / sqrt(2)"""
        data = generate_sine_wave(1.0, amplitude=0.5)
        rms = SilenceDetector._calculate_rms(data)
        expected = 0.5 / np.sqrt(2)
        self.assertAlmostEqual(rms, expected, places=3)

    def test_rms_calculation_silence(self):
        """静音 RMS 应为 0"""
        data = generate_silence(1.0)
        rms = SilenceDetector._calculate_rms(data)
        self.assertAlmostEqual(rms, 0.0, places=5)

    def test_rms_calculation_noise(self):
        """白噪声 RMS 应在合理范围"""
        rng = np.random.default_rng(42)
        noise = rng.standard_normal(16000).astype(np.float32) * 0.1
        rms = SilenceDetector._calculate_rms(noise)
        self.assertGreater(rms, 0.05)
        self.assertLess(rms, 0.15)

    def test_silence_detector_triggers_timeout(self):
        """注入足够静音后应触发超时回调。

        SilenceDetector 基于挂钟时间检测超时，所以需要实际等待。
        """
        triggered = threading.Event()
        config = AudioConfig(silence_timeout=0.5, silence_threshold=0.01)
        buf_queue = queue.Queue()

        sd = SilenceDetector(config, lambda: triggered.set())
        sd.start(buf_queue)
        sd.begin_detection()

        # Feed silence chunks with real-time gaps so wall-clock time passes
        for _ in range(15):
            buf_queue.put(generate_silence(0.1))
            time.sleep(0.08)  # ~0.1s wall-clock per chunk

        self.assertTrue(triggered.wait(timeout=5), "静音超时未触发")
        sd.shutdown()

    def test_silence_detector_no_trigger_with_sound(self):
        """持续注入语音不应触发超时"""
        triggered = threading.Event()
        config = AudioConfig(silence_timeout=2.0, silence_threshold=0.01)
        buf_queue = queue.Queue()

        sd = SilenceDetector(config, lambda: triggered.set())
        sd.start(buf_queue)
        sd.begin_detection()

        # 持续注入语音
        for _ in range(10):
            buf_queue.put(generate_sine_wave(0.1, amplitude=0.5))

        self.assertFalse(triggered.wait(timeout=1.0), "不应触发静音超时")
        sd.shutdown()

    def test_silence_detector_rms_callback(self):
        """RMS 回调应正确区分语音/静音"""
        results = []

        config = AudioConfig(silence_timeout=0, silence_threshold=0.01)
        buf_queue = queue.Queue()
        done = threading.Event()

        def on_rms(rms, is_speech):
            results.append((rms, is_speech))
            if len(results) >= 4:
                done.set()

        sd = SilenceDetector(config, lambda: None, on_rms_update=on_rms)
        sd.start(buf_queue)
        sd.begin_detection()

        # Use larger chunks so throttling (100ms) doesn't skip them all
        buf_queue.put(generate_sine_wave(0.2, amplitude=0.5))
        time.sleep(0.15)
        buf_queue.put(generate_silence(0.2))
        time.sleep(0.15)
        buf_queue.put(generate_sine_wave(0.2, amplitude=0.3))
        time.sleep(0.15)
        buf_queue.put(generate_silence(0.2))

        self.assertTrue(done.wait(timeout=5))
        sd.shutdown()

        # 至少有语音和非语音
        speech_rms = [r for r, s in results if s]
        silence_rms = [r for r, s in results if not s]
        self.assertGreater(len(speech_rms), 0)
        self.assertGreater(len(silence_rms), 0)
        self.assertGreater(min(speech_rms), max(silence_rms))


# ================================================================
# 3. 完整引擎管线测试
# ================================================================

class TestEnginePipeline(unittest.TestCase):
    """mock 掉 platform-specific，验证完整引擎管线。"""

    def _make_engine(self, mode="batch"):
        """创建测试用 CoreEngine（mock 掉 tray/injector/stream_transcriber）"""
        config = AppConfig(mode=mode)
        config.audio.silence_timeout = 0  # 关闭静音检测超时，手动控制

        tray_mock = MagicMock()
        tray_mock.show_notification = MagicMock()
        tray_mock.set_state = MagicMock()

        # Patch 掉 platform-specific imports
        with patch('core.injector.create_clipboard_injector') as mock_cci:
            mock_impl = MagicMock()
            mock_impl.inject.return_value = True
            mock_cci.return_value = mock_impl

            # StreamTranscriber is imported inside engine.py: from core.stream_transcriber import ...
            with patch('core.stream_transcriber.StreamTranscriber') as mock_st_class:
                mock_st = MagicMock()
                mock_st.start = MagicMock()
                mock_st.stop = MagicMock()
                mock_st_class.return_value = mock_st

                from core.engine import CoreEngine
                engine = CoreEngine(config=config, tray=tray_mock)

        return engine

    def test_initial_state_is_loading(self):
        engine = self._make_engine()
        from core.engine import EngineState
        self.assertEqual(engine.state, EngineState.LOADING)
        engine.shutdown()

    def test_transition_loading_to_idle(self):
        engine = self._make_engine()
        from core.engine import EngineState
        self.assertTrue(engine.transition(EngineState.IDLE))
        self.assertEqual(engine.state, EngineState.IDLE)
        engine.shutdown()

    def test_invalid_transition(self):
        engine = self._make_engine()
        from core.engine import EngineState
        self.assertFalse(engine.transition(EngineState.RECORDING))
        self.assertEqual(engine.state, EngineState.LOADING)
        engine.shutdown()

    def test_state_changed_event(self):
        engine = self._make_engine()
        from core.engine import EngineState
        states = []
        engine.events.subscribe(EngineEvent.STATE_CHANGED,
                                lambda old, new: states.append((old, new)))
        engine.transition(EngineState.IDLE)
        time.sleep(0.3)  # async handler
        self.assertTrue(any(s[1] == EngineState.IDLE for s in states))
        engine.shutdown()

    def test_full_batch_pipeline(self):
        """完整批量管线：IDLE → RECORDING → PROCESSING → INJECTING → IDLE"""
        engine = self._make_engine()
        from core.engine import EngineState

        # Go to IDLE first
        engine.transition(EngineState.IDLE)

        # Track events
        events_received = []
        for evt in [EngineEvent.RECORDING_STARTED, EngineEvent.RECORDING_STOPPED,
                     EngineEvent.TRANSCRIBE_COMPLETE, EngineEvent.TEXT_INJECTED]:
            engine.events.subscribe(evt, lambda *a, e=evt: events_received.append(e))

        # Start recording
        engine.on_hotkey_toggle()
        self.assertEqual(engine.state, EngineState.RECORDING)

        # Inject audio into recorder buffer directly
        sine = generate_sine_wave(1.0, amplitude=0.3)
        recorder_q = engine._recorder.get_buffer_queue()
        engine._recorder._buffer.append(sine.reshape(1, -1))

        # Mock STT to return text immediately
        original_do_transcribe = engine._stt_engine._do_transcribe
        def mock_transcribe(audio):
            return ("测试文本", "zh", 100, None)
        engine._stt_engine._do_transcribe = mock_transcribe

        # Stop recording -> triggers STT
        engine.on_hotkey_toggle()

        time.sleep(1.0)  # wait for async pipeline

        self.assertEqual(engine.state, EngineState.IDLE)
        engine.shutdown()

    def test_hotkey_toggle_cycle(self):
        engine = self._make_engine()
        from core.engine import EngineState
        engine.transition(EngineState.IDLE)

        # Start
        engine.on_hotkey_toggle()
        self.assertEqual(engine.state, EngineState.RECORDING)

        # Inject mock audio so recorder.stop() returns something
        engine._recorder._buffer.append(generate_sine_wave(0.5).reshape(1, -1))
        engine._stt_engine._do_transcribe = lambda a: ("", None, 0, None)

        # Stop
        engine.on_hotkey_toggle()
        time.sleep(0.5)
        self.assertEqual(engine.state, EngineState.IDLE)
        engine.shutdown()

    def test_shutdown_prevents_actions(self):
        engine = self._make_engine()
        from core.engine import EngineState
        engine.transition(EngineState.IDLE)
        engine.shutdown()
        # After shutdown, toggle should be no-op
        engine.on_hotkey_toggle()
        self.assertTrue(engine.is_shutdown)


# ================================================================
# 4. WAV 文件真实测试
# ================================================================

class TestWAVFileHandling(unittest.TestCase):
    """生成真实 WAV 文件并验证 SoundPlayer._load_wav_stdlib。"""

    def test_load_16bit_wav(self):
        with tempfile.TemporaryDirectory() as d:
            data = generate_sine_wave(1.0, amplitude=0.5)
            path = Path(d) / "test.wav"
            create_wav_file(path, data, sampwidth=2)

            loaded, sr = SoundPlayer._load_wav_stdlib(path)
            self.assertEqual(sr, 16000)
            self.assertEqual(len(loaded), len(data))
            np.testing.assert_allclose(loaded, data, atol=0.01)

    def test_load_32bit_wav(self):
        with tempfile.TemporaryDirectory() as d:
            data = generate_sine_wave(0.5, amplitude=0.3)
            path = Path(d) / "test32.wav"
            create_wav_file(path, data, sampwidth=4)

            loaded, sr = SoundPlayer._load_wav_stdlib(path)
            self.assertEqual(sr, 16000)
            self.assertEqual(len(loaded), len(data))
            np.testing.assert_allclose(loaded, data, atol=0.001)

    def test_load_stereo_wav_takes_mono(self):
        """立体声 WAV 应取单声道"""
        with tempfile.TemporaryDirectory() as d:
            t = np.linspace(0, 0.5, 8000, endpoint=False)
            mono = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            stereo = np.column_stack([mono, mono * 0.5]).flatten()
            # Convert to int16
            raw_int = (stereo * 32768).astype(np.int16)
            path = Path(d) / "stereo.wav"
            with wave.open(str(path), 'wb') as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(raw_int.tobytes())

            loaded, sr = SoundPlayer._load_wav_stdlib(path)
            # Should be half length (mono extraction)
            self.assertEqual(len(loaded), 8000)
            np.testing.assert_allclose(loaded, mono, atol=0.01)

    def test_load_nonexistent_wav_raises(self):
        with self.assertRaises(Exception):
            SoundPlayer._load_wav_stdlib(Path("/nonexistent/file.wav"))


# ================================================================
# 5. 配置系统真实测试
# ================================================================

class TestConfigSystem(unittest.TestCase):
    """配置加载、保存、迁移测试。"""

    def test_save_and_load_roundtrip(self):
        """save → load 应保持一致"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.yaml")
            cfg = AppConfig()
            cfg.mode = "realtime"
            cfg.audio.silence_timeout = 5
            cfg.stt.model_size = "base"

            save_config(path, cfg)
            loaded = load_config(path)

            self.assertEqual(loaded.mode, "realtime")
            self.assertEqual(loaded.audio.silence_timeout, 5)
            self.assertEqual(loaded.stt.model_size, "base")

    def test_migrate_v1_to_v2(self):
        raw = {"mode": "batch"}
        result = _migrate_v1_to_v2(raw)
        self.assertEqual(result["config_version"], 2)
        self.assertTrue(result["inject"]["clipboard_restore"])

    def test_migrate_v2_to_v3(self):
        raw = {"config_version": 2, "audio": {}, "inject": {}}
        result = _migrate_v2_to_v3(raw)
        self.assertEqual(result["config_version"], 3)
        self.assertIsNone(result["audio"]["device"])
        self.assertTrue(result["command"]["enabled"])

    def test_full_migration_chain_v1_to_v3(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.yaml")
            # Write a v1 config (no config_version)
            import yaml
            v1_data = {"mode": "batch"}
            with open(path, 'w') as f:
                yaml.dump(v1_data, f)

            loaded = load_config(path)
            self.assertEqual(loaded.config_version, CURRENT_CONFIG_VERSION)

    def test_load_nonexistent_creates_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "new_config.yaml")
            cfg = load_config(path)
            self.assertIsInstance(cfg, AppConfig)
            self.assertTrue(os.path.exists(path))

    def test_dataclass_to_dict_roundtrip(self):
        cfg = AppConfig()
        d = _dataclass_to_dict(cfg)
        self.assertIsInstance(d, dict)
        self.assertEqual(d["mode"], "batch")
        self.assertIsInstance(d["audio"], dict)
        self.assertEqual(d["audio"]["silence_timeout"], 8)

    def test_flatten_to_appconfig_ignores_extra_keys(self):
        raw = {"mode": "batch", "unknown_key": 123, "audio": {"silence_timeout": 5, "bogus": True}}
        cfg = _flatten_to_appconfig(raw)
        self.assertEqual(cfg.mode, "batch")
        self.assertEqual(cfg.audio.silence_timeout, 5)


# ================================================================
# 6. Stats 真实文件系统测试
# ================================================================

class TestStatsFilesystem(unittest.TestCase):
    """Stats 写入真实文件系统，验证多天聚合。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.stats = UsageStats(stats_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_and_flush(self):
        self.stats.record_transcribe("hello", "en", 500)
        self.stats.record_transcribe("world", "en", 300)
        self.stats.record_transcribe("", None, 200)  # empty

        summary = self.stats.get_summary(days=1)
        self.assertEqual(summary["total_transcribe"], 3)
        self.assertEqual(summary["total_empty"], 1)
        self.assertEqual(summary["total_duration_ms"], 1000)

    def test_multi_day_aggregation(self):
        today = date.today()
        # Write yesterday's data manually
        yesterday = today - timedelta(days=1)
        yesterday_data = {
            "date": yesterday.isoformat(),
            "transcribe_count": 5,
            "empty_result_count": 1,
            "error_count": 0,
            "total_duration_ms": 2000,
            "max_duration_triggered": 0,
            "command_triggered": 2,
            "languages": {"zh": 5},
            "text_lengths": [10, 20, 30],
        }
        ypath = Path(self.tmpdir) / f"stats_{yesterday.isoformat()}.json"
        ypath.write_text(json.dumps(yesterday_data))

        # Today's data
        self.stats.record_transcribe("test", "zh", 300)

        summary = self.stats.get_summary(days=7)
        self.assertEqual(summary["total_transcribe"], 6)  # 5 + 1
        self.assertEqual(summary["command_triggered"], 2)
        self.assertEqual(summary["languages"]["zh"], 6)
        self.assertAlmostEqual(summary["avg_text_length"], (10+20+30+4) / 4, places=1)

    def test_error_and_command_tracking(self):
        self.stats.record_error()
        self.stats.record_error()
        self.stats.record_command("换行")

        summary = self.stats.get_summary(days=1)
        self.assertEqual(summary["total_error"], 2)
        self.assertEqual(summary["command_triggered"], 1)

    def test_max_duration_tracking(self):
        self.stats.record_max_duration()
        summary = self.stats.get_summary(days=1)
        self.assertEqual(summary["max_duration_triggered"], 1)

    def test_file_persistence(self):
        self.stats.record_transcribe("persist test", "zh", 800)
        self.stats.flush()

        # Create new stats instance pointing to same dir
        stats2 = UsageStats(stats_dir=self.tmpdir)
        summary = stats2.get_summary(days=1)
        self.assertEqual(summary["total_transcribe"], 1)


# ================================================================
# 7. 命令模式压力测试
# ================================================================

class TestCommandStress(unittest.TestCase):
    """遍历所有默认命令、随机文本、边界情况。"""

    def setUp(self):
        self.matcher = CommandMatcher()

    def test_all_default_commands_match(self):
        """每个默认命令至少有一个 pattern 能匹配"""
        for cmd in DEFAULT_COMMANDS:
            # Use the first alternative of the first pattern as test text
            # Extract a simple text from pattern like "撤销|撤回|删除上一句"
            first_alt = cmd.patterns[0].split('|')[0]
            result = self.matcher.match(first_alt)
            self.assertIsNotNone(result, f"Command '{cmd.name}' pattern '{first_alt}' should match")
            self.assertEqual(result[0].name, cmd.name)

    def test_specific_command_matches(self):
        cases = {
            "撤销": "撤销", "撤回": "撤销", "换行": "换行", "回车": "换行",
            "退格": "退格", "全选": "全选", "复制": "复制", "粘贴": "粘贴",
            "停止录音": "停止录音", "句号": "句号", "逗号": "逗号",
            "问号": "问号", "感叹号": "感叹号", "叹号": "感叹号", "冒号": "冒号",
        }
        for text, expected_name in cases.items():
            result = self.matcher.match(text)
            self.assertIsNotNone(result, f"'{text}' should match a command")
            self.assertEqual(result[0].name, expected_name, f"'{text}' should match '{expected_name}'")

    def test_random_text_no_false_positive(self):
        """随机中文文本不应误触发命令"""
        rng = np.random.default_rng(42)
        false_positives = 0
        for _ in range(500):
            length = rng.integers(3, 20)
            chars = [chr(rng.integers(0x4e00, 0x9fff)) for _ in range(length)]
            text = ''.join(chars)
            if self.matcher.match(text) is not None:
                false_positives += 1
        # Allow very few false positives (< 1%)
        self.assertLess(false_positives, 5, f"Too many false positives: {false_positives}")

    def test_short_text_no_match(self):
        """单字不应匹配命令"""
        for ch in ["撤", "换", "退", "全", "复", "粘", "句", "逗"]:
            self.assertIsNone(self.matcher.match(ch), f"单字 '{ch}' 不应匹配")

    def test_punctuation_wrapped_commands(self):
        """标点包裹的命令文本应正确匹配"""
        cases = ["。撤销。", "，换行，", "！退格！", "？全选？"]
        for text in cases:
            result = self.matcher.match(text)
            self.assertIsNotNone(result, f"标点包裹的 '{text}' 应匹配")

    def test_whitespace_padded_commands(self):
        """前后空格的命令文本应正确匹配"""
        result = self.matcher.match("  撤销  ")
        self.assertIsNotNone(result)
        self.assertEqual(result[0].name, "撤销")

    def test_empty_string_no_match(self):
        self.assertIsNone(self.matcher.match(""))
        self.assertIsNone(self.matcher.match("   "))


# ================================================================
# 8. 事件总线真实异步测试
# ================================================================

class TestEventBusAsync(unittest.TestCase):
    """事件总线的并发、顺序、异常隔离测试。"""

    def test_publish_calls_handlers(self):
        bus = EventBus()
        results = []
        bus.subscribe(EngineEvent.STATE_CHANGED, lambda o, n: results.append((o, n)))
        bus.publish(EngineEvent.STATE_CHANGED, "old", "new")
        time.sleep(0.3)
        self.assertEqual(results, [("old", "new")])
        bus.shutdown()

    def test_multiple_handlers(self):
        bus = EventBus()
        r1, r2 = [], []
        bus.subscribe(EngineEvent.RECORDING_STARTED, lambda: r1.append(1))
        bus.subscribe(EngineEvent.RECORDING_STARTED, lambda: r2.append(2))
        bus.publish(EngineEvent.RECORDING_STARTED)
        time.sleep(0.3)
        self.assertEqual(r1, [1])
        self.assertEqual(r2, [2])
        bus.shutdown()

    def test_concurrent_events(self):
        """多事件并发发布，所有 handler 都应收到"""
        bus = EventBus()
        results = []
        lock = threading.Lock()

        def handler(val):
            with lock:
                results.append(val)

        bus.subscribe(EngineEvent.TRANSCRIBE_COMPLETE, handler)
        n = 50
        for i in range(n):
            bus.publish(EngineEvent.TRANSCRIBE_COMPLETE, i)

        time.sleep(1.0)
        self.assertEqual(len(results), n)
        self.assertEqual(set(results), set(range(n)))
        bus.shutdown()

    def test_exception_isolation(self):
        """一个 handler 异常不应影响其他 handler"""
        bus = EventBus()
        results = []

        def bad_handler():
            raise RuntimeError("boom")

        def good_handler():
            results.append("ok")

        bus.subscribe(EngineEvent.ENGINE_SHUTDOWN, bad_handler)
        bus.subscribe(EngineEvent.ENGINE_SHUTDOWN, good_handler)
        bus.publish(EngineEvent.ENGINE_SHUTDOWN)
        time.sleep(0.3)
        self.assertEqual(results, ["ok"])
        bus.shutdown()

    def test_no_handlers_no_error(self):
        bus = EventBus()
        bus.publish(EngineEvent.MAX_DURATION_TRIGGERED)  # should not raise
        bus.shutdown()

    def test_shutdown_waits_for_pending(self):
        bus = EventBus()
        results = []
        bus.subscribe(EngineEvent.STATE_CHANGED, lambda o, n: results.append(1))
        bus.publish(EngineEvent.STATE_CHANGED, None, None)
        bus.shutdown()  # should wait
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
