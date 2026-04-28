"""新增引擎配置测试

测试 Qwen3-ASR 和 Fun-ASR-Nano 的配置验证。
"""

import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# Qwen3-ASR 配置测试
# ============================================================

class TestQwen3ASRConfig:
    """Qwen3-ASR 引擎配置测试"""

    def test_qwen3_asr_06b_config_passes(self):
        """测试项1: qwen3_asr + Qwen3-ASR-0.6B 配置通过"""
        from config import STTConfig
        
        config = STTConfig(engine='qwen3_asr', model_size='Qwen3-ASR-0.6B')
        assert config.engine == 'qwen3_asr'
        assert config.model_size == 'Qwen3-ASR-0.6B'

    def test_qwen3_asr_17b_config_passes(self):
        """测试项2: qwen3_asr + Qwen3-ASR-1.7B 配置通过"""
        from config import STTConfig
        
        config = STTConfig(engine='qwen3_asr', model_size='Qwen3-ASR-1.7B')
        assert config.engine == 'qwen3_asr'
        assert config.model_size == 'Qwen3-ASR-1.7B'

    def test_qwen3_asr_invalid_auto_switch(self):
        """测试项3: qwen3_asr + 无效 model_size 自动切换到 Qwen3-ASR-0.6B"""
        from config import STTConfig
        
        config = STTConfig(engine='qwen3_asr', model_size='Invalid-Model')
        assert config.model_size == 'Qwen3-ASR-0.6B'


# ============================================================
# Fun-ASR-Nano 配置测试
# ============================================================

class TestFunASRNanoConfig:
    """Fun-ASR-Nano 配置测试"""

    def test_funasr_nano_config_passes(self):
        """测试项4: funasr + Fun-ASR-Nano 配置通过"""
        from config import STTConfig
        
        config = STTConfig(engine='funasr', model_size='Fun-ASR-Nano')
        assert config.engine == 'funasr'
        assert config.model_size == 'Fun-ASR-Nano'


# ============================================================
# 向后兼容测试
# ============================================================

class TestBackwardCompatibility:
    """向后兼容测试"""

    def test_faster_whisper_qwen3_auto_switch(self):
        """测试项5: faster_whisper + Qwen3 model 自动切换"""
        from config import STTConfig
        
        config = STTConfig(engine='faster_whisper', model_size='Qwen3-ASR-0.6B')
        # Should auto-switch to large-v3-turbo
        assert config.model_size == 'large-v3-turbo'

    def test_auto_mode_whisper_backward_compatible(self):
        """测试项11: auto 模式向后兼容（whisper model_size 保持 faster_whisper）"""
        from config import STTConfig
        
        # When engine is auto and model_size is a whisper size, should keep faster_whisper compatible
        config = STTConfig(engine='auto', model_size='large-v3-turbo')
        assert config.engine == 'auto'
        assert config.model_size == 'large-v3-turbo'


# ============================================================
# max_new_tokens 测试
# ============================================================

class TestMaxNewTokens:
    """max_new_tokens 配置测试"""

    def test_default_value_256(self):
        """测试项6: max_new_tokens 默认值 256"""
        from config import STTConfig
        
        config = STTConfig()
        assert config.max_new_tokens == 256

    def test_custom_value(self):
        """测试项7: max_new_tokens 自定义值"""
        from config import STTConfig
        
        config = STTConfig(max_new_tokens=512)
        assert config.max_new_tokens == 512


# ============================================================
# v5→v6 迁移测试
# ============================================================

class TestV5ToV6Migration:
    """v5→v6 配置迁移测试"""

    def test_migration_adds_max_new_tokens(self):
        """测试项8: v5→v6 迁移函数新增 max_new_tokens"""
        from config import _migrate_v5_to_v6
        
        v5_raw = {
            "config_version": 5,
            "stt": {
                "engine": "auto",
                "model_size": "large-v3-turbo"
            }
        }
        result = _migrate_v5_to_v6(v5_raw)
        
        assert result["config_version"] == 6
        assert "max_new_tokens" in result["stt"]
        assert result["stt"]["max_new_tokens"] == 256

    def test_migration_preserves_existing_max_new_tokens(self):
        """测试项9: v5→v6 迁移不覆盖已有 max_new_tokens"""
        from config import _migrate_v5_to_v6
        
        v5_raw = {
            "config_version": 5,
            "stt": {
                "engine": "auto",
                "model_size": "large-v3-turbo",
                "max_new_tokens": 512
            }
        }
        result = _migrate_v5_to_v6(v5_raw)
        
        assert result["stt"]["max_new_tokens"] == 512


# ============================================================
# 无效引擎类型测试
# ============================================================

class TestInvalidEngine:
    """无效引擎类型测试"""

    def test_invalid_engine_raises_valueerror(self):
        """测试项10: 无效 engine 类型抛出 ValueError"""
        from config import STTConfig
        
        with pytest.raises(ValueError) as exc_info:
            STTConfig(engine='invalid_engine')
        
        assert 'invalid_engine' in str(exc_info.value).lower() or '无效' in str(exc_info.value)
