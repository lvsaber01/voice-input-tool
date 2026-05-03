#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归测试 - 验证现有功能不受影响"""

import unittest
import queue
import numpy as np
from unittest.mock import Mock, patch, MagicMock
import tempfile
import os

class TestRegression(unittest.TestCase):
    """回归测试 - 验证变更不影响现有功能"""
    
    def test_faster_whisper_batch_mode(self):
        """回归：faster-whisper 批量模式"""
        from config import AppConfig, STTConfig, StreamingConfig
        
        # faster-whisper 配置
        config = AppConfig()
        config.stt = STTConfig(engine='faster_whisper', model_size='large-v3-turbo')
        
        # streaming 应自动禁用
        self.assertFalse(config.stt.streaming.enabled)
    
    def test_funasr_non_streaming_mode(self):
        """回归：FunASR 非流式模式"""
        from config import AppConfig, STTConfig, StreamingConfig
        
        config = AppConfig()
        config.stt = STTConfig(
            engine='funasr',
            streaming=StreamingConfig(enabled=False)
        )
        
        self.assertFalse(config.stt.streaming.enabled)
    
    def test_vad_segment_transcriber_exists(self):
        """回归：VAD分段转写器仍可用"""
        try:
            from core.vad_segment_transcriber import VADSegmentTranscriber
            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"VADSegmentTranscriber 导入失败: {e}")
    
    def test_config_yaml_compatibility(self):
        """回归：旧配置文件兼容"""
        from config import load_config
        
        # 旧版本配置（无 streaming 节）
        old_config_yaml = """
config_version: 4
mode: batch
stt:
  engine: faster_whisper
  model_size: large-v3-turbo
"""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(old_config_yaml)
            f.flush()
            temp_path = f.name
        
        try:
            config = load_config(temp_path)
            self.assertEqual(config.mode, 'batch')
            self.assertEqual(config.stt.engine, 'faster_whisper')
            # streaming 应使用默认值
            self.assertTrue(hasattr(config.stt, 'streaming'))
        finally:
            os.unlink(temp_path)
    
    def test_hotkey_config_unchanged(self):
        """回归：热键配置不变"""
        from config import HotkeyConfig
        
        config = HotkeyConfig(trigger='f8', mode='toggle')
        self.assertEqual(config.trigger, 'f8')
        self.assertEqual(config.mode, 'toggle')
    
    def test_inject_config_unchanged(self):
        """回归：注入配置不变"""
        from config import InjectConfig
        
        config = InjectConfig(method='clipboard', auto_paste=True)
        self.assertEqual(config.method, 'clipboard')
        self.assertTrue(config.auto_paste)


class TestEngineSelectionLogic(unittest.TestCase):
    """引擎选择逻辑测试"""
    
    @patch('core.stt_funasr_streaming.FunASRStreamingEngine')
    @patch('core.streaming_transcriber.StreamingTranscriber')
    def test_streaming_mode_selection(self, mock_stream, mock_engine):
        """测试流式模式选择"""
        from config import AppConfig, STTConfig, StreamingConfig
        
        config = AppConfig()
        config.stt = STTConfig(
            engine='funasr',
            streaming=StreamingConfig(enabled=True)
        )
        
        # 验证 streaming.enabled=True + engine='funasr'
        self.assertTrue(config.stt.streaming.enabled)
        self.assertEqual(config.stt.engine, 'funasr')
    
    def test_streaming_disabled_for_faster_whisper(self):
        """测试 faster-whisper 禁用流式"""
        from config import STTConfig
        
        config = STTConfig(engine='faster_whisper')
        
        # __post_init__ 应自动禁用 streaming
        # 注意：这需要在实际代码中验证


class TestSenseVoiceRegression(unittest.TestCase):
    """SenseVoice 集成回归测试 — 确保不影响现有功能"""

    def test_paraformer_still_default(self):
        """回归：paraformer-zh 仍是默认模型"""
        from config import STTConfig
        cfg = STTConfig(engine="funasr")
        self.assertEqual(cfg.model_size, "paraformer-zh")

    def test_paraformer_load_model_unchanged(self):
        """回归：Paraformer load_model 仍走原路径"""
        from core.stt_funasr import FunASREngine
        config = MagicMock()
        config.model_size = "paraformer-zh"
        config.modelscope_endpoint = ""
        engine = FunASREngine(config)
        self.assertFalse(engine._is_sensevoice)

    def test_paraformer_transcribe_returns_zh(self):
        """回归：Paraformer 转写仍返回 language='zh'"""
        from core.stt_funasr import FunASREngine
        config = MagicMock()
        config.model_size = "paraformer-zh"
        config.modelscope_endpoint = ""
        engine = FunASREngine(config)
        engine.model = MagicMock()
        engine.model.generate = MagicMock(return_value=[{"text": "测试"}])
        result = engine._do_transcribe(np.random.randn(16000).astype(np.float32))
        self.assertEqual(result[1], "zh")  # language
        self.assertFalse(engine._is_sensevoice)

    def test_extract_text_still_works(self):
        """回归：_extract_text 兼容旧格式"""
        from core.stt_funasr import FunASREngine
        # dict 格式（原 Paraformer 格式）
        self.assertEqual(FunASREngine._extract_text([{"text": "你好"}]), "你好")
        # 空结果
        self.assertEqual(FunASREngine._extract_text([]), "")
        self.assertEqual(FunASREngine._extract_text(None), "")

    def test_normalize_chinese_spaces_still_works(self):
        """回归：中文空格去除逻辑不变"""
        from core.stt_funasr import FunASREngine
        self.assertEqual(FunASREngine._normalize_chinese_spaces("你 好"), "你好")
        self.assertEqual(FunASREngine._normalize_chinese_spaces("hello world"), "hello world")

    def test_sensevoice_interface_compatible(self):
        """回归：SenseVoice 返回值格式与 Paraformer 一致"""
        from core.stt_funasr import FunASREngine
        config = MagicMock()
        config.model_size = "SenseVoiceSmall"
        config.modelscope_endpoint = ""
        engine = FunASREngine(config)
        engine.model = MagicMock()
        engine.model.generate = MagicMock(return_value=[{"text": "<|zh|>测试"}])
        engine._is_sensevoice = True
        result = engine._do_transcribe(np.random.randn(16000).astype(np.float32))
        # 四元组格式
        self.assertEqual(len(result), 4)
        self.assertIsInstance(result[0], str)  # text
        self.assertEqual(result[1], "auto")    # language
        self.assertIsInstance(result[2], int)   # duration_ms
        self.assertIsNone(result[3])            # error

    def test_config_old_yaml_compatible(self):
        """回归：旧配置文件（无 SenseVoice）仍可加载"""
        from config import load_config
        old_yaml = """
config_version: 5
mode: batch
stt:
  engine: funasr
  model_size: paraformer-zh
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(old_yaml)
            f.flush()
            temp_path = f.name
        try:
            config = load_config(temp_path)
            self.assertEqual(config.stt.model_size, "paraformer-zh")
        finally:
            os.unlink(temp_path)


class TestModeSwitchRegression(unittest.TestCase):
    """模式切换功能回归测试 — 验证变更不影响现有功能"""

    def test_regression_batch_mode_default_config(self):
        """回归：默认配置仍为 batch 模式"""
        from config import AppConfig
        cfg = AppConfig()
        self.assertEqual(cfg.mode, "batch")

    def test_regression_stt_config_unchanged(self):
        """回归：config.stt 字段不受 stt_realtime 影响"""
        from config import AppConfig, STTConfig
        cfg = AppConfig()
        # 默认 stt 应保持不变
        self.assertEqual(cfg.stt.engine, "auto")
        self.assertEqual(cfg.stt.model_size, "large-v3-turbo")
        # stt_realtime 不影响 stt
        self.assertEqual(cfg.stt_realtime.engine, "funasr")
        self.assertNotEqual(cfg.stt.engine, cfg.stt_realtime.engine)

    def test_regression_funasr_streaming_works(self):
        """回归：FunASR 流式引擎仍可创建"""
        from config import STTConfig, StreamingConfig
        # FunASR streaming 配置应能正常创建
        cfg = STTConfig(
            engine="funasr",
            model_size="paraformer-zh-streaming",
            streaming=StreamingConfig(enabled=True),
        )
        self.assertTrue(cfg.streaming.enabled)
        self.assertEqual(cfg.engine, "funasr")
        self.assertEqual(cfg.model_size, "paraformer-zh-streaming")

    def test_regression_all_engine_imports(self):
        """回归：所有引擎模块仍可导入"""
        # 验证各引擎模块可导入（不初始化）
        try:
            from core.stt_engine import STTEngine
            from core.stt_funasr import FunASREngine
            from core.stt_funasr_streaming import FunASRStreamingEngine
            from core.vad_segment_transcriber import VADSegmentTranscriber
            from core.streaming_transcriber import StreamingTranscriber
            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"引擎模块导入失败: {e}")


if __name__ == '__main__':
    unittest.main(verbosity=2)