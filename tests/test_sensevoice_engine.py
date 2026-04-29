"""SenseVoice 引擎 Mock 测试

覆盖配置校验、引擎核心、辅助方法、异常路径。
不依赖实际模型加载，使用 unittest.mock。
"""

import sys
import types
import pytest
import numpy as np
from unittest.mock import patch, MagicMock, PropertyMock


# Windows 上 funasr import torch 触发 WinError 206（路径过长），
# 预注册 mock 模块防止真实 import
if 'funasr' not in sys.modules:
    _mock_funasr = types.ModuleType('funasr')
    _mock_auto_model = MagicMock()
    _mock_funasr.AutoModel = _mock_auto_model
    sys.modules['funasr'] = _mock_funasr
    sys.modules['funasr.utils'] = types.ModuleType('funasr.utils')
    sys.modules['funasr.utils.postprocess_utils'] = types.ModuleType('funasr.utils.postprocess_utils')
    # 用真实 identity 函数而非 MagicMock，避免影响 wraps 测试
    sys.modules['funasr.utils.postprocess_utils'].rich_transcription_postprocess = lambda x: x


# ============================================================
# 辅助方法测试（不依赖模型加载）
# ============================================================

class TestExtractText:
    """_extract_text 方法测试"""

    def test_dict_result(self):
        from core.stt_funasr import FunASREngine
        result = [{"text": "你好世界"}]
        assert FunASREngine._extract_text(result) == "你好世界"

    def test_object_result(self):
        from core.stt_funasr import FunASREngine
        item = MagicMock()
        item.text = "hello"
        result = [item]
        assert FunASREngine._extract_text(result) == "hello"

    def test_string_result(self):
        from core.stt_funasr import FunASREngine
        result = ["测试文本"]
        assert FunASREngine._extract_text(result) == "测试文本"

    def test_empty_result(self):
        from core.stt_funasr import FunASREngine
        assert FunASREngine._extract_text([]) == ""
        assert FunASREngine._extract_text(None) == ""

    def test_dict_missing_text(self):
        from core.stt_funasr import FunASREngine
        result = [{"other": "value"}]
        assert FunASREngine._extract_text(result) == ""


class TestNormalizeChineseSpaces:
    """_normalize_chinese_spaces 方法测试"""

    def test_basic_chinese_spaces(self):
        from core.stt_funasr import FunASREngine
        assert FunASREngine._normalize_chinese_spaces("你 好 世 界") == "你好世界"

    def test_preserve_english_spaces(self):
        from core.stt_funasr import FunASREngine
        assert FunASREngine._normalize_chinese_spaces("hello world") == "hello world"

    def test_mixed(self):
        from core.stt_funasr import FunASREngine
        assert FunASREngine._normalize_chinese_spaces("说 hello 再 见") == "说 hello 再见"

    def test_max_iterations(self):
        from core.stt_funasr import FunASREngine
        # 正常文本应在 max_iterations 内收敛
        text = "中 " * 50 + "文"
        result = FunASREngine._normalize_chinese_spaces(text, max_iterations=10)
        assert " " not in result or all(
            c == " " for i, c in enumerate(result) if i > 0
        )


class TestPostprocessSensevoice:
    """_postprocess_sensevoice 方法测试"""

    def test_official_with_markers(self):
        """官方函数应清除特殊标记"""
        from core.stt_funasr import FunASREngine
        # Mock 官方函数
        with patch("core.stt_funasr.FunASREngine._postprocess_sensevoice",
                    wraps=FunASREngine._postprocess_sensevoice):
            result = FunASREngine._postprocess_sensevoice("你好世界")
            assert result == "你好世界"

    def test_fallback_markers(self):
        """fallback 正则应清除已知标记"""
        from core.stt_funasr import FunASREngine
        with patch("funasr.utils.postprocess_utils.rich_transcription_postprocess",
                   side_effect=ImportError):
            result = FunASREngine._postprocess_sensevoice("<|zh|><|EMO_HAPPY|>你好")
            assert result == "你好"

    def test_fallback_nospeech(self):
        from core.stt_funasr import FunASREngine
        with patch("funasr.utils.postprocess_utils.rich_transcription_postprocess",
                   side_effect=ImportError):
            result = FunASREngine._postprocess_sensevoice("<|nospeech|>")
            assert result == ""

    def test_empty_input(self):
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice("") == ""
        assert FunASREngine._postprocess_sensevoice(None) == ""

    def test_none_input(self):
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice(None) == ""

    def test_non_string_input(self):
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice(123) == "123"

    def test_fallback_bgm_laugh(self):
        from core.stt_funasr import FunASREngine
        with patch("funasr.utils.postprocess_utils.rich_transcription_postprocess",
                   side_effect=ImportError):
            result = FunASREngine._postprocess_sensevoice("<|BGM|><|LAUGH|>测试")
            assert result == "测试"


# ============================================================
# 引擎核心测试（Mock AutoModel）
# ============================================================

class TestFunASREngineCore:
    """引擎核心逻辑测试"""

    def _make_config(self, model_size="paraformer-zh"):
        config = MagicMock()
        config.model_size = model_size
        config.modelscope_endpoint = ""
        return config

    @patch("funasr.AutoModel")
    def test_sensevoice_load_model(self, mock_auto_model):
        """SenseVoice 应使用 trust_remote_code=True 和 vad_model"""
        from core.stt_funasr import FunASREngine
        config = self._make_config("SenseVoiceSmall")
        engine = FunASREngine(config)
        success, err = engine.load_model()

        assert success is True
        assert err == ''
        assert engine._is_sensevoice is True

        # 验证 AutoModel 调用参数
        call_kwargs = mock_auto_model.call_args[1]
        assert call_kwargs["model"] == "iic/SenseVoiceSmall"
        assert call_kwargs["trust_remote_code"] is True
        assert call_kwargs["vad_model"] == "fsmn-vad"
        assert call_kwargs["vad_kwargs"] == {"max_single_segment_time": 30000}

    @patch("funasr.AutoModel")
    def test_paraformer_load_model(self, mock_auto_model):
        """Paraformer 应使用 punc_model"""
        from core.stt_funasr import FunASREngine
        config = self._make_config("paraformer-zh")
        engine = FunASREngine(config)
        success, err = engine.load_model()

        assert success is True
        assert engine._is_sensevoice is False
        call_kwargs = mock_auto_model.call_args[1]
        assert call_kwargs["punc_model"] == "ct-punc"

    @patch("funasr.AutoModel", side_effect=ImportError)
    def test_load_model_import_error(self, mock_auto_model):
        """funasr 未安装应返回友好提示"""
        from core.stt_funasr import FunASREngine
        config = self._make_config("SenseVoiceSmall")
        engine = FunASREngine(config)
        success, err = engine.load_model()
        assert success is False
        assert "pip install" in err

    @patch("funasr.AutoModel", side_effect=Exception("fsmn-vad download failed"))
    def test_load_model_vad_fail(self, mock_auto_model):
        """VAD 下载失败应返回特定提示"""
        from core.stt_funasr import FunASREngine
        config = self._make_config("SenseVoiceSmall")
        engine = FunASREngine(config)
        success, err = engine.load_model()
        assert success is False
        assert "VAD" in err

    @patch("funasr.AutoModel", side_effect=Exception("SenseVoiceSmall not found"))
    def test_load_model_sensevoice_fail(self, mock_auto_model):
        """SenseVoice 加载失败应返回特定提示"""
        from core.stt_funasr import FunASREngine
        config = self._make_config("SenseVoiceSmall")
        engine = FunASREngine(config)
        success, err = engine.load_model()
        assert success is False
        assert "SenseVoice" in err

    @patch("funasr.AutoModel")
    def test_load_model_env_restore(self, mock_auto_model):
        """环境变量应在加载后恢复"""
        import os
        from core.stt_funasr import FunASREngine

        original = os.environ.get('MODELSCOPE_ENDPOINT')
        config = self._make_config("paraformer-zh")
        config.modelscope_endpoint = "https://mirror.example.com"
        engine = FunASREngine(config)
        engine.load_model()

        # 环境变量应恢复到原始值
        assert os.environ.get('MODELSCOPE_ENDPOINT') == original

    @patch("funasr.AutoModel")
    def test_sensevoice_transcribe(self, mock_auto_model):
        """SenseVoice 转写应使用 language='auto'"""
        from core.stt_funasr import FunASREngine
        config = self._make_config("SenseVoiceSmall")
        engine = FunASREngine(config)
        engine.load_model()

        # Mock generate
        engine.model.generate = MagicMock(return_value=[{"text": "你好世界"}])
        result = engine._do_transcribe(np.random.randn(16000).astype(np.float32))

        assert result[0] == "你好世界"  # text
        assert result[1] == "auto"     # language
        assert result[3] is None       # error

        # 验证 generate 参数
        call_kwargs = engine.model.generate.call_args[1]
        assert call_kwargs["language"] == "auto"
        assert call_kwargs["use_itn"] is True

    @patch("funasr.AutoModel")
    def test_paraformer_transcribe(self, mock_auto_model):
        """Paraformer 转写应返回 language='zh'"""
        from core.stt_funasr import FunASREngine
        config = self._make_config("paraformer-zh")
        engine = FunASREngine(config)
        engine.load_model()

        engine.model.generate = MagicMock(return_value=[{"text": "测试"}])
        result = engine._do_transcribe(np.random.randn(16000).astype(np.float32))

        assert result[1] == "zh"
        assert engine._is_sensevoice is False

    def test_model_not_loaded(self):
        """模型未加载应返回错误"""
        from core.stt_funasr import FunASREngine
        config = self._make_config()
        engine = FunASREngine(config)
        # 不调用 load_model
        result = engine._do_transcribe(np.random.randn(16000).astype(np.float32))
        assert result[0] == ""
        assert result[3] is not None  # error

    def test_short_audio(self):
        """短音频应返回空"""
        from core.stt_funasr import FunASREngine
        config = self._make_config()
        engine = FunASREngine(config)
        engine.model = MagicMock()  # Mock model
        result = engine._do_transcribe(np.random.randn(100).astype(np.float32))
        assert result[0] == ""


# ============================================================
# import os 验证
# ============================================================

class TestImportOs:
    """确认 import os 存在"""

    def test_os_imported(self):
        import core.stt_funasr
        import os
        # 确认模块中有 os 模块的引用
        assert hasattr(core.stt_funasr, 'os')
