#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自动化单元测试 - FunASR 流式引擎

运行方式：py -3.11 run_tests.py
"""

import sys
import os
import unittest
from unittest.mock import Mock, patch, MagicMock
import threading
import numpy as np

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFunASRStreamingEngine(unittest.TestCase):
    """FunASRStreamingEngine 单元测试"""
    
    def setUp(self):
        from core.stt_funasr_streaming import FunASRStreamingEngine
        self.config = Mock()
        self.config.modelscope_endpoint = ''
        self.engine = FunASRStreamingEngine(self.config)
    
    def test_get_chunk_samples(self):
        """测试 chunk 样本数"""
        samples = self.engine.get_chunk_samples()
        self.assertEqual(samples, 9600)  # 16000 * 0.6
    
    def test_chunk_size_constant(self):
        """测试 chunk_size 常量"""
        self.assertEqual(self.engine.CHUNK_SIZE, [0, 10, 5])
        self.assertEqual(self.engine.CHUNK_MS, 600)
    
    def test_reset_thread_safe(self):
        """测试 reset 线程安全"""
        self.engine._cache = {'key': 'value'}
        
        # 多线程并发 reset
        threads = [threading.Thread(target=self.engine.reset) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        self.assertEqual(self.engine._cache, {})
    
    def test_is_ready_false_before_load(self):
        """测试加载前 is_ready 返回 False"""
        self.assertFalse(self.engine.is_ready())
    
    def test_transcribe_chunk_returns_empty_on_exception(self):
        """测试异常时返回空字符串（不中断）"""
        # 模拟已加载但模型崩溃
        self.engine._model_loaded = True
        self.engine.model = Mock()
        self.engine.model.generate = Mock(side_effect=RuntimeError("模型崩溃"))
        
        audio = np.random.randn(9600).astype(np.float32)
        result = self.engine.transcribe_chunk(audio, is_final=False)
        
        # 应返回空字符串，不抛异常
        self.assertEqual(result, '')


class TestStreamingTranscriber(unittest.TestCase):
    """StreamingTranscriber 单元测试"""
    
    def setUp(self):
        from core.streaming_transcriber import StreamingTranscriber
        from config import StreamingConfig
        self.config = StreamingConfig(enabled=True, max_queue_size=300)
        self.transcriber = StreamingTranscriber(self.config)
    
    def test_chunk_samples_default(self):
        """测试默认 chunk_samples"""
        self.assertEqual(self.transcriber._chunk_samples, 9600)
    
    def test_not_running_before_start(self):
        """测试启动前 is_running 返回 False"""
        self.assertFalse(self.transcriber.is_running())
    
    def test_buffer_is_instance_variable(self):
        """测试 buffer 是实例变量"""
        self.assertIsInstance(self.transcriber._buffer, list)
        self.assertEqual(self.transcriber._buffer, [])


class TestStreamingConfig(unittest.TestCase):
    """StreamingConfig 配置验证测试"""
    
    def test_valid_config(self):
        """测试有效配置"""
        from config import StreamingConfig
        
        config = StreamingConfig(enabled=True, max_queue_size=300)
        self.assertTrue(config.enabled)
        self.assertEqual(config.max_queue_size, 300)
    
    def test_invalid_max_queue_size(self):
        """测试无效队列大小"""
        from config import StreamingConfig
        
        with self.assertRaises(ValueError):
            StreamingConfig(max_queue_size=5)  # < 10
    
    def test_invalid_overflow_strategy(self):
        """测试无效溢出策略"""
        from config import StreamingConfig
        
        with self.assertRaises(ValueError):
            StreamingConfig(overflow_strategy="invalid")
    
    def test_default_values(self):
        """测试默认值"""
        from config import StreamingConfig
        
        config = StreamingConfig()
        self.assertTrue(config.enabled)
        self.assertEqual(config.max_queue_size, 300)
        self.assertEqual(config.overflow_strategy, "drop_old")


class TestSTTConfigStreaming(unittest.TestCase):
    """STTConfig streaming 字段测试"""
    
    def test_streaming_field_type_conversion(self):
        """测试 streaming 字段类型转换"""
        from config import STTConfig
        
        # 从 dict 转换
        config = STTConfig(
            engine='funasr',
            streaming={'enabled': True, 'max_queue_size': 200}
        )
        
        self.assertTrue(config.streaming.enabled)
        self.assertEqual(config.streaming.max_queue_size, 200)
    
    def test_streaming_disabled_for_faster_whisper(self):
        """测试 faster-whisper 自动禁用 streaming"""
        from config import STTConfig
        
        config = STTConfig(
            engine='faster_whisper',
            streaming={'enabled': True}
        )
        
        # __post_init__ 应自动禁用
        self.assertFalse(config.streaming.enabled)


class TestRegression(unittest.TestCase):
    """回归测试 - 验证变更不影响现有功能"""
    
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
    
    def test_audio_config_unchanged(self):
        """回归：音频配置不变"""
        from config import AudioConfig
        
        config = AudioConfig(max_duration=120)
        self.assertEqual(config.max_duration, 120)
    
    def test_vad_segment_transcriber_importable(self):
        """回归：VADSegmentTranscriber 可导入"""
        from core.vad_segment_transcriber import VADSegmentTranscriber
        self.assertTrue(True)  # 导入成功即通过


if __name__ == '__main__':
    # 运行所有测试
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加所有测试类
    suite.addTests(loader.loadTestsFromTestCase(TestFunASRStreamingEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestStreamingTranscriber))
    suite.addTests(loader.loadTestsFromTestCase(TestStreamingConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestSTTConfigStreaming))
    suite.addTests(loader.loadTestsFromTestCase(TestRegression))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 输出总结
    print("\n" + "="*60)
    print(f"测试总数: {result.testsRun}")
    print(f"成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    print("="*60)
    
    # 返回退出码
    sys.exit(0 if result.wasSuccessful() else 1)