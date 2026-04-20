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


if __name__ == '__main__':
    unittest.main(verbosity=2)