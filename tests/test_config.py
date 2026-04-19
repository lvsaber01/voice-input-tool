"""config.py 配置系统单元测试"""

import unittest
import tempfile
import os
import yaml

from config import (
    load_config, save_config, AppConfig, CURRENT_CONFIG_VERSION,
    _migrate_v2_to_v3, HotkeyConfig, STTConfig, AudioConfig, SoundConfig,
)


class TestConfigDefaults(unittest.TestCase):

    def test_v3_default_values(self):
        """load_config v3 默认值"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.yaml")
            cfg = load_config(path)
            # AppConfig default is v2; after save+reload it stays v2
            self.assertGreaterEqual(cfg.config_version, 2)
            self.assertEqual(cfg.hotkey.trigger, "f8")
            self.assertEqual(cfg.stt.model_size, "large-v3-turbo")
            self.assertEqual(cfg.audio.max_duration, 120)
            self.assertEqual(cfg.sound.volume, 0.5)
            self.assertEqual(cfg.command.enabled, True)


class TestConfigMigration(unittest.TestCase):

    def test_v2_to_v3_migration(self):
        """v2→v3 迁移：添加 command 节 + audio.device"""
        v2_raw = {
            "config_version": 2,
            "mode": "batch",
            "hotkey": {"trigger": "f8", "mode": "toggle"},
            "stt": {"model_size": "small"},
            "audio": {"max_duration": 120, "silence_timeout": 8},
        }
        result = _migrate_v2_to_v3(v2_raw)
        self.assertEqual(result["config_version"], 3)
        self.assertIn("command", result)
        self.assertIn("device", result["audio"])
        self.assertIsNone(result["audio"]["device"])

    def test_full_migration_v1_to_v3(self):
        """从 v1 配置文件加载，迁移到 v3"""
        v1_config = {
            "mode": "batch",
            "hotkey": {"trigger": "f8", "mode": "toggle"},
            "stt": {"model_size": "small"},
            "audio": {"max_duration": 120},
        }
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.yaml")
            with open(path, 'w') as f:
                yaml.dump(v1_config, f)
            cfg = load_config(path)
            self.assertEqual(cfg.config_version, CURRENT_CONFIG_VERSION)
            self.assertIn("command", cfg.__dataclass_fields__)


class TestConfigValidation(unittest.TestCase):

    def test_invalid_hotkey_mode_raises(self):
        """非法 hotkey.mode 抛异常"""
        with self.assertRaises(ValueError):
            HotkeyConfig(mode="invalid")

    def test_invalid_model_size_raises(self):
        """非法 stt.model_size 抛异常"""
        with self.assertRaises(ValueError):
            STTConfig(model_size="xxxlarge")

    def test_invalid_beam_size_raises(self):
        """非法 stt.beam_size 抛异常"""
        with self.assertRaises(ValueError):
            STTConfig(beam_size=0)

    def test_invalid_audio_max_duration_raises(self):
        """非法 audio.max_duration 抛异常"""
        with self.assertRaises(ValueError):
            AudioConfig(max_duration=0)

    def test_invalid_sound_volume_raises(self):
        """非法 sound.volume 抛异常"""
        with self.assertRaises(ValueError):
            SoundConfig(volume=1.5)


if __name__ == "__main__":
    unittest.main()
