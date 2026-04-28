"""Fun-ASR-Nano 单元测试

使用 mock 测试 Fun-ASR-Nano 分支，不依赖实际模型。
"""

import pytest
import numpy as np
from unittest.mock import patch, MagicMock


# ============================================================
# load_model Fun-ASR-Nano 测试
# ============================================================

class TestFunASRNanoLoadModel:
    """Fun-ASR-Nano load_model 测试"""

    def _make_config(self, model_size="Fun-ASR-Nano"):
        config = MagicMock()
        config.model_size = model_size
        config.modelscope_endpoint = ""
        return config

    @patch('core.stt_funasr.logger')
    def test_load_model_success(self, mock_logger):
        """测试项1: load_model Fun-ASR-Nano 成功，验证 AutoModel 参数"""
        from core.stt_funasr import FunASREngine
        import sys
        
        # Mock 整个 funasr 包，模拟 __version__='1.3.1' (>=1.1)
        mock_funasr = MagicMock()
        mock_funasr.__version__ = '1.3.1'
        mock_auto_model_instance = MagicMock()
        mock_funasr.AutoModel.return_value = mock_auto_model_instance
        
        fake_modules = {
            'funasr': mock_funasr,
        }
        
        with patch.dict('sys.modules', fake_modules):
            config = self._make_config("Fun-ASR-Nano")
            engine = FunASREngine(config)
            success, err = engine.load_model()

            assert success is True
            assert err == ''
            assert engine._is_fun_asr_nano is True

            # Verify AutoModel was called with correct parameters
            call_kwargs = mock_funasr.AutoModel.call_args[1]
            assert call_kwargs["model"] == "FunAudioLLM/Fun-ASR-Nano-2512"
            assert call_kwargs["trust_remote_code"] is True
            assert call_kwargs["device"] == "cpu"

    @patch('core.stt_funasr.logger')
    def test_version_check_fails(self, mock_logger):
        """测试项2: funasr 版本过低时返回升级提示"""
        from core.stt_funasr import FunASREngine
        import sys
        
        # Mock funasr with old version
        mock_funasr = MagicMock()
        mock_funasr.__version__ = '1.0.0'
        
        fake_modules = {
            'funasr': mock_funasr,
        }
        
        with patch.dict('sys.modules', fake_modules):
            config = self._make_config("Fun-ASR-Nano")
            engine = FunASREngine(config)
            success, err = engine.load_model()
            
            assert success is False
            assert "1.0.0" in err
            assert "pip install" in err


# ============================================================
# _do_transcribe Fun-ASR-Nano 分支测试
# ============================================================

class TestFunASRNanoTranscribe:
    """Fun-ASR-Nano 转写测试"""

    def _make_config(self, model_size="Fun-ASR-Nano"):
        config = MagicMock()
        config.model_size = model_size
        config.modelscope_endpoint = ""
        return config

    def test_do_transcribe_calls_generate_with_correct_params(self):
        """测试项3: _do_transcribe Fun-ASR-Nano 分支调用 generate 参数正确"""
        from core.stt_funasr import FunASREngine
        
        config = self._make_config("Fun-ASR-Nano")
        engine = FunASREngine(config)
        engine._is_fun_asr_nano = True
        engine._is_sensevoice = False
        
        mock_model = MagicMock()
        mock_model.generate.return_value = [{"text": "你好世界"}]
        engine.model = mock_model
        
        audio = np.random.randn(16000).astype(np.float32)
        result = engine._do_transcribe(audio)
        
        # Verify generate was called with correct parameters
        call_kwargs = mock_model.generate.call_args[1]
        assert call_kwargs["cache"] == {}
        assert call_kwargs["batch_size"] == 1
        assert call_kwargs["language"] == "auto"
        assert call_kwargs["itn"] is True


# ============================================================
# 配置校验测试
# ============================================================

class TestFunASRNanoConfig:
    """Fun-ASR-Nano 配置校验测试"""

    def test_funasr_nano_config_passes(self):
        """测试项4: STTConfig(engine='funasr', model_size='Fun-ASR-Nano') 通过"""
        from config import STTConfig
        
        config = STTConfig(engine='funasr', model_size='Fun-ASR-Nano')
        assert config.engine == 'funasr'
        assert config.model_size == 'Fun-ASR-Nano'

    def test_invalid_model_size_auto_switch(self):
        """测试项5: Fun-ASR-Nano 无效 model_size 自动切换到 paraformer-zh"""
        from config import STTConfig
        
        config = STTConfig(engine='funasr', model_size='Invalid-Model')
        # Should auto-switch to paraformer-zh
        assert config.model_size == 'paraformer-zh'
