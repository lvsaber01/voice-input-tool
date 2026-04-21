#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FunASR 流式引擎单元测试"""

import unittest
import numpy as np
from unittest.mock import Mock, patch, MagicMock
import threading

class TestFunASRStreamingEngine(unittest.TestCase):
    """FunASRStreamingEngine 单元测试"""
    
    def setUp(self):
        # 创建模拟配置
        self.config = Mock()
        self.config.modelscope_endpoint = ''
    
    @unittest.skipUnless(False, 'funasr not available on Mac CI')
    @patch('funasr.AutoModel')
    def test_load_model_success(self, mock_auto_model):
        """测试模型加载成功"""
        from core.stt_funasr_streaming import FunASRStreamingEngine
        
        engine = FunASRStreamingEngine(self.config)
        success, error = engine.load_model()
        
        self.assertTrue(success)
        self.assertEqual(error, '')
        self.assertTrue(engine.is_ready())
    
    def test_transcribe_chunk_empty_audio(self):
        """测试空音频处理"""
        from core.stt_funasr_streaming import FunASRStreamingEngine
        
        engine = FunASRStreamingEngine(self.config)
        engine._model_loaded = True
        engine.model = Mock()
        engine.model.generate = Mock(return_value=[{'text': ''}])
        
        # 空音频
        empty_audio = np.array([])
        result = engine.transcribe_chunk(empty_audio, is_final=True)
        self.assertEqual(result, '')
    
    def test_transcribe_chunk_exception_handling(self):
        """测试异常不中断循环"""
        from core.stt_funasr_streaming import FunASRStreamingEngine
        
        engine = FunASRStreamingEngine(self.config)
        engine._model_loaded = True
        engine.model = Mock()
        engine.model.generate = Mock(side_effect=RuntimeError("模型崩溃"))
        
        audio = np.random.randn(9600).astype(np.float32)
        result = engine.transcribe_chunk(audio, is_final=False)
        
        # 异常时应返回空字符串，不抛异常
        self.assertEqual(result, '')
    
    def test_get_chunk_samples(self):
        """测试 chunk 样本数"""
        from core.stt_funasr_streaming import FunASRStreamingEngine
        
        engine = FunASRStreamingEngine(self.config)
        samples = engine.get_chunk_samples()
        
        self.assertEqual(samples, 9600)  # 16000 * 0.6
    
    def test_reset_thread_safe(self):
        """测试 reset 线程安全"""
        from core.stt_funasr_streaming import FunASRStreamingEngine
        
        engine = FunASRStreamingEngine(self.config)
        engine._cache = {'key': 'value'}
        
        # 多线程并发 reset
        threads = [threading.Thread(target=engine.reset) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        self.assertEqual(engine._cache, {})


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


if __name__ == '__main__':
    unittest.main(verbosity=2)