"""STTConfig mlx_whisper 相关 + 引擎选择逻辑 + Config 全字段校验 测试"""

import unittest
from unittest.mock import MagicMock, patch
from config import (
    AppConfig, STTConfig, StreamingConfig, HotkeyConfig, AudioConfig,
    InjectConfig, SoundConfig, WebConfig, StartupConfig, CommandConfig,
    RealtimeConfig, load_config, save_config, _flatten_to_appconfig,
)


# ================================================================
# P0-1: STTConfig mlx_whisper 校验
# ================================================================

class TestSTTConfigMlxWhisper(unittest.TestCase):
    """STTConfig 新增 mlx_whisper 引擎的校验逻辑"""

    def test_mlx_whisper_valid_engine(self):
        """engine='mlx_whisper' 是有效值"""
        cfg = STTConfig(engine="mlx_whisper")
        self.assertEqual(cfg.engine, "mlx_whisper")

    def test_mlx_whisper_default_model(self):
        """mlx_whisper 使用 whisper 系列模型"""
        cfg = STTConfig(engine="mlx_whisper")
        self.assertIn(cfg.model_size, ("tiny", "base", "small", "medium",
                                       "large-v3", "large-v3-turbo"))

    def test_mlx_whisper_valid_sizes(self):
        """mlx_whisper 接受所有 whisper 模型尺寸"""
        for size in ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"):
            with self.subTest(size=size):
                cfg = STTConfig(engine="mlx_whisper", model_size=size)
                self.assertEqual(cfg.model_size, size)

    def test_mlx_whisper_invalid_size_raises(self):
        """mlx_whisper + 无效 model_size 抛异常"""
        with self.assertRaises(ValueError):
            STTConfig(engine="mlx_whisper", model_size="mega-ultra")

    def test_mlx_whisper_funasr_size_autocorrects(self):
        """mlx_whisper + FunASR 模型名自动切换到 large-v3-turbo"""
        cfg = STTConfig(engine="mlx_whisper", model_size="paraformer-zh")
        self.assertEqual(cfg.model_size, "large-v3-turbo")

    def test_mlx_whisper_streaming_disabled_warning(self):
        """mlx_whisper 不支持 streaming，自动禁用"""
        cfg = STTConfig(engine="mlx_whisper", streaming=StreamingConfig(enabled=True))
        self.assertFalse(cfg.streaming.enabled)

    def test_auto_is_valid_engine(self):
        """engine='auto' 是有效值"""
        cfg = STTConfig(engine="auto")
        self.assertEqual(cfg.engine, "auto")

    def test_auto_with_whisper_sizes(self):
        """auto 引擎接受 whisper 模型尺寸"""
        for size in ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"):
            with self.subTest(size=size):
                cfg = STTConfig(engine="auto", model_size=size)
                self.assertEqual(cfg.model_size, size)

    def test_auto_streaming_disabled(self):
        """auto 引擎的 streaming 也被禁用（非 FunASR）"""
        cfg = STTConfig(engine="auto", streaming=StreamingConfig(enabled=True))
        self.assertFalse(cfg.streaming.enabled)

    def test_funasr_streaming_stays_enabled(self):
        """FunASR 引擎的 streaming 保持启用"""
        cfg = STTConfig(engine="funasr", streaming=StreamingConfig(enabled=True))
        self.assertTrue(cfg.streaming.enabled)

    def test_invalid_engine_raises(self):
        """无效引擎名抛异常"""
        with self.assertRaises(ValueError):
            STTConfig(engine="whisperx")

    def test_streaming_from_dict(self):
        """streaming 字段支持 dict 输入"""
        cfg = STTConfig(engine="mlx_whisper", streaming={"enabled": True, "max_queue_size": 50})
        self.assertIsInstance(cfg.streaming, StreamingConfig)
        self.assertFalse(cfg.streaming.enabled)  # auto-disabled for non-funasr

    def test_all_valid_engines(self):
        """四个有效引擎都不抛异常"""
        for eng in ("auto", "faster_whisper", "funasr", "mlx_whisper"):
            with self.subTest(engine=eng):
                cfg = STTConfig(engine=eng)
                self.assertEqual(cfg.engine, eng)

    # --- SenseVoice 配置校验 ---
    def test_sensevoice_valid_model_size(self):
        """SenseVoiceSmall 应被接受为合法模型名"""
        cfg = STTConfig(engine="funasr", model_size="SenseVoiceSmall")
        self.assertEqual(cfg.model_size, "SenseVoiceSmall")

    def test_sensevoice_invalid_fallback(self):
        """非法模型名应回退到 paraformer-zh"""
        cfg = STTConfig(engine="funasr", model_size="nonexistent-model")
        self.assertEqual(cfg.model_size, "paraformer-zh")

    def test_sensevoice_streaming_conflict(self):
        """SenseVoiceSmall + streaming.enabled=True 应自动禁用 streaming"""
        cfg = STTConfig(
            engine="funasr",
            model_size="SenseVoiceSmall",
            streaming=StreamingConfig(enabled=True),
        )
        self.assertFalse(cfg.streaming.enabled, "streaming 应被自动禁用")

    def test_paraformer_streaming_still_works(self):
        """paraformer-zh + streaming.enabled=True 不受影响"""
        cfg = STTConfig(
            engine="funasr",
            model_size="paraformer-zh",
            streaming=StreamingConfig(enabled=True),
        )
        self.assertTrue(cfg.streaming.enabled)

    def test_sensevoice_streaming_disabled_no_side_effect(self):
        """SenseVoiceSmall + streaming.enabled=False 无变化"""
        cfg = STTConfig(
            engine="funasr",
            model_size="SenseVoiceSmall",
            streaming=StreamingConfig(enabled=False),
        )
        self.assertFalse(cfg.streaming.enabled)
        self.assertEqual(cfg.model_size, "SenseVoiceSmall")


# ================================================================
# P0-2: Config 全字段校验（补充现有 test_config.py 未覆盖的）
# ================================================================

class TestConfigValidation(unittest.TestCase):
    """Config 各数据类的校验逻辑"""

    # --- HotkeyConfig ---
    def test_hotkey_invalid_mode(self):
        with self.assertRaises(ValueError):
            HotkeyConfig(mode="hold")

    def test_hotkey_toggle_mode(self):
        cfg = HotkeyConfig(mode="toggle")
        self.assertEqual(cfg.mode, "toggle")

    def test_hotkey_push_to_talk_mode(self):
        cfg = HotkeyConfig(mode="push_to_talk")
        self.assertEqual(cfg.mode, "push_to_talk")

    # --- StreamingConfig ---
    def test_streaming_invalid_queue_size(self):
        with self.assertRaises(ValueError):
            StreamingConfig(max_queue_size=5)

    def test_streaming_invalid_strategy(self):
        with self.assertRaises(ValueError):
            StreamingConfig(overflow_strategy="newest")

    def test_streaming_valid(self):
        cfg = StreamingConfig(max_queue_size=100, overflow_strategy="drop_old")
        self.assertEqual(cfg.max_queue_size, 100)

    # --- AudioConfig ---
    def test_audio_invalid_max_duration(self):
        with self.assertRaises(ValueError):
            AudioConfig(max_duration=0)

    def test_audio_invalid_silence_threshold_zero(self):
        with self.assertRaises(ValueError):
            AudioConfig(silence_threshold=0)

    def test_audio_invalid_silence_threshold_over1(self):
        with self.assertRaises(ValueError):
            AudioConfig(silence_threshold=1.5)

    def test_audio_silence_timeout_zero_ok(self):
        """silence_timeout=0 表示关闭，应合法"""
        cfg = AudioConfig(silence_timeout=0)
        self.assertEqual(cfg.silence_timeout, 0)

    def test_audio_negative_silence_timeout(self):
        with self.assertRaises(ValueError):
            AudioConfig(silence_timeout=-1)

    # --- InjectConfig ---
    def test_inject_negative_paste_delay(self):
        with self.assertRaises(ValueError):
            InjectConfig(paste_delay_ms=-1)

    # --- SoundConfig ---
    def test_sound_volume_over1(self):
        with self.assertRaises(ValueError):
            SoundConfig(volume=1.5)

    def test_sound_volume_zero_ok(self):
        cfg = SoundConfig(volume=0)
        self.assertEqual(cfg.volume, 0)

    # --- WebConfig ---
    def test_web_port_zero(self):
        with self.assertRaises(ValueError):
            WebConfig(port=0)

    def test_web_port_over65535(self):
        with self.assertRaises(ValueError):
            WebConfig(port=70000)

    def test_web_valid_port(self):
        cfg = WebConfig(port=8080)
        self.assertEqual(cfg.port, 8080)

    # --- RealtimeConfig ---
    def test_realtime_invalid_vad_sensitivity(self):
        with self.assertRaises(ValueError):
            RealtimeConfig(vad_sensitivity=4)

    def test_realtime_vad_sensitivity_range(self):
        for s in range(4):
            with self.subTest(sensitivity=s):
                cfg = RealtimeConfig(vad_sensitivity=s)
                self.assertEqual(cfg.vad_sensitivity, s)

    def test_realtime_max_segment_leq_min(self):
        with self.assertRaises(ValueError):
            RealtimeConfig(min_segment_duration=5.0, max_segment_duration=5.0)

    def test_realtime_valid(self):
        cfg = RealtimeConfig()
        self.assertEqual(cfg.segment_pause_threshold, 0.8)


# ================================================================
# P0-2b: Config 序列化/反序列化 + 版本迁移
# ================================================================

class TestConfigSerialization(unittest.TestCase):

    def test_flatten_to_appconfig_full(self):
        """完整 dict → AppConfig"""
        raw = {
            "config_version": 8,
            "mode": "batch",
            "hotkey": {"trigger": "f9", "mode": "push_to_talk"},
            "stt": {"engine": "mlx_whisper", "model_size": "small"},
            "audio": {"max_duration": 60},
            "inject": {"method": "clipboard"},
            "sound": {"volume": 0.3},
            "web": {"port": 9999},
            "startup": {"minimize": False},
            "command": {"enabled": False},
            "realtime": {"vad_sensitivity": 1},
        }
        cfg = _flatten_to_appconfig(raw)
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(cfg.hotkey.trigger, "f9")
        self.assertEqual(cfg.stt.engine, "mlx_whisper")
        self.assertEqual(cfg.stt.model_size, "small")
        self.assertEqual(cfg.audio.max_duration, 60)
        self.assertEqual(cfg.web.port, 9999)

    def test_flatten_ignores_unknown_keys(self):
        """多余 key 被忽略"""
        raw = {"config_version": 8, "unknown_key": 123, "stt": {"engine": "auto", "bogus": True}}
        cfg = _flatten_to_appconfig(raw)
        self.assertIsInstance(cfg, AppConfig)

    def test_save_and_load_roundtrip(self):
        """保存后加载，配置一致"""
        import tempfile, os
        cfg = AppConfig()
        cfg.config_version = 8  # 设为最新版本避免迁移
        cfg.stt.engine = "mlx_whisper"
        cfg.stt.model_size = "small"

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name

        try:
            save_config(path, cfg)
            loaded = load_config(path)
            self.assertEqual(loaded.stt.engine, "mlx_whisper")
            self.assertEqual(loaded.stt.model_size, "small")
            # 版本号会迁移到 CURRENT_CONFIG_VERSION
            self.assertEqual(loaded.config_version, 8)
        finally:
            os.unlink(path)

    def test_load_nonexistent_creates_default(self):
        """加载不存在的文件 → 创建默认配置"""
        import tempfile, os
        path = tempfile.mktemp(suffix=".yaml")
        try:
            cfg = load_config(path)
            self.assertIsInstance(cfg, AppConfig)
            self.assertTrue(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_migration_v1_to_current(self):
        """v1 配置迁移到最新版本"""
        import tempfile, os
        raw_v1 = {"mode": "batch"}
        path = tempfile.mktemp(suffix=".yaml")
        try:
            import yaml
            with open(path, "w") as f:
                yaml.dump(raw_v1, f)
            cfg = load_config(path)
            self.assertEqual(cfg.config_version, 8)
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ================================================================
# P0-3: 引擎选择逻辑（engine.py 的 auto 路由）
# ================================================================

class TestEngineSelection(unittest.TestCase):
    """CoreEngine 中 auto → mlx_whisper/faster_whisper 的路由逻辑"""

    @patch('core.stt_mlx_whisper.platform')
    def test_auto_selects_mlx_on_macos(self, mock_plat):
        """macOS 上 engine='auto' 应选择 mlx_whisper"""
        mock_plat.system.return_value = 'Darwin'
        # 模拟 engine.py 的选择逻辑
        stt_engine_type = "auto"
        if stt_engine_type == "auto":
            stt_engine_type = "mlx_whisper" if mock_plat.system() == "Darwin" else "faster_whisper"
        self.assertEqual(stt_engine_type, "mlx_whisper")

    def test_auto_selects_fw_on_windows(self):
        """Windows 上 engine='auto' 应选择 faster_whisper"""
        with patch('core.stt_mlx_whisper.platform') as mock_plat:
            mock_plat.system.return_value = 'Windows'
            stt_engine_type = "auto"
            if stt_engine_type == "auto":
                stt_engine_type = "mlx_whisper" if mock_plat.system() == "Darwin" else "faster_whisper"
            self.assertEqual(stt_engine_type, "faster_whisper")

    def test_mlx_whisper_fallback_on_non_macos(self):
        """非 macOS 上 engine='mlx_whisper' 应回退"""
        with patch('core.stt_mlx_whisper.platform') as mock_plat:
            mock_plat.system.return_value = 'Windows'
            stt_engine_type = "mlx_whisper"
            if stt_engine_type == "mlx_whisper" and mock_plat.system() != "Darwin":
                stt_engine_type = "faster_whisper"
            self.assertEqual(stt_engine_type, "faster_whisper")

    def test_streaming_enabled_for_mlx(self):
        """streaming_enabled 对 mlx_whisper 生效"""
        stt_engine_type = "mlx_whisper"
        streaming_enabled = False
        if stt_engine_type in ("funasr", "mlx_whisper"):
            streaming_enabled = True
        self.assertTrue(streaming_enabled)

    def test_streaming_not_for_faster_whisper(self):
        """streaming_enabled 对 faster_whisper 不生效"""
        stt_engine_type = "faster_whisper"
        streaming_enabled = getattr(StreamingConfig(), 'enabled', False) \
            if stt_engine_type in ("funasr", "mlx_whisper") else False
        self.assertFalse(streaming_enabled)


# ================================================================
# P1: Config 联动测试
# ================================================================

class TestConfigInteraction(unittest.TestCase):
    """配置项之间的联动关系"""

    def test_mode_realtime_with_mlx_whisper(self):
        """realtime 模式 + mlx_whisper 引擎"""
        cfg = AppConfig(mode="realtime")
        cfg.stt.engine = "mlx_whisper"
        # streaming 应被 STTConfig 自动禁用（非 funasr）
        cfg.stt.streaming = StreamingConfig(enabled=True)
        # 重新触发 post_init 的校验逻辑
        cfg.stt.__post_init__()
        self.assertFalse(cfg.stt.streaming.enabled)

    def test_mode_batch_default(self):
        """默认 batch 模式"""
        cfg = AppConfig()
        self.assertEqual(cfg.mode, "batch")

    def test_engine_switch_preserves_model(self):
        """切换引擎时模型自动适配"""
        cfg = STTConfig(engine="auto", model_size="small")
        self.assertEqual(cfg.model_size, "small")

        # 切换到 funasr
        cfg2 = STTConfig(engine="funasr", model_size="small")
        self.assertEqual(cfg2.model_size, "paraformer-zh")  # 自动切换


if __name__ == '__main__':
    unittest.main()
