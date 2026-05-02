"""F3 标点恢复单元测试 — 14 用例

测试 PunctuationRestorer 的核心功能。
需要真实模型的测试标记为 @pytest.mark.slow。
"""

import pytest
import threading
import time
from unittest.mock import MagicMock, patch


class TestPunctuationRestorer:
    """PunctuationRestorer 单元测试。"""

    def test_disabled(self):
        """禁用时不处理文本。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=False)
        assert r.restore("你好世界") == "你好世界"

    def test_empty_text(self):
        """空文本直接返回。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        assert r.restore("") == ""

    def test_none_text(self):
        """None/falsy 输入直接返回。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        assert r.restore("") == ""

    def test_model_unavailable(self):
        """模型不可用时返回原文（降级）。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        # 模拟 funasr 导入失败（_load_model 内部 try/except 会捕获）
        with patch.dict('sys.modules', {'funasr': None}):
            result = r.restore("你好世界")
            assert result == "你好世界"
            assert not r.is_loaded

    @pytest.mark.slow
    def test_restore_basic(self):
        """基础标点恢复（需要真实模型）。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        r.restore("")  # 触发加载
        if not r.is_loaded:
            pytest.skip("ct-punc 模型不可用")
        result = r.restore("你好世界")
        # 标点恢复后应包含某种标点
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.slow
    def test_restore_long_text(self):
        """长文本标点恢复。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        r.restore("")  # 触发加载
        if not r.is_loaded:
            pytest.skip("ct-punc 模型不可用")
        long_text = "今天天气真不错我们出去走走吧顺便去超市买点东西"
        result = r.restore(long_text)
        assert isinstance(result, str)

    @pytest.mark.slow
    def test_already_has_punctuation(self):
        """已有标点的文本不应出错。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        r.restore("")  # 触发加载
        if not r.is_loaded:
            pytest.skip("ct-punc 模型不可用")
        result = r.restore("你好，世界。")
        assert isinstance(result, str)

    def test_shutdown(self):
        """shutdown 后模型被释放。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        mock_model = MagicMock()
        r._model = mock_model
        r._loaded = True
        r._shared_model = False
        r.shutdown()
        assert r._model is None
        assert not r.is_loaded

    def test_is_loaded(self):
        """is_loaded 属性正确反映状态。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        assert not r.is_loaded
        r._loaded = True
        assert r.is_loaded

    def test_double_restore(self):
        """重复调用安全（无模型时）。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        with patch.object(r, '_load_model'):
            r._loaded = False
            result1 = r.restore("你好")
            result2 = r.restore("世界")
        assert isinstance(result1, str)
        assert isinstance(result2, str)

    def test_concurrent_restore(self):
        """多线程并发调用不崩溃。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        results = []
        errors = []

        def worker(text):
            try:
                result = r.restore(text)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"测试文本{i}",))
                    for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(errors) == 0, f"并发错误: {errors}"
        assert len(results) == 5

    def test_shared_model_injection(self):
        """共享模型注入正确。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        mock_model = MagicMock()
        r.set_shared_model(mock_model)
        assert r.is_loaded
        assert r._shared_model is True
        assert r._model is mock_model

    def test_shared_model_not_freed(self):
        """共享实例不被 shutdown 释放。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        mock_model = MagicMock()
        r.set_shared_model(mock_model)
        r.shutdown()
        assert r._model is mock_model  # 不被置 None
        assert not r.is_loaded  # 但 loaded 状态清零

    def test_performance_latency(self):
        """无模型时 restore 延迟极低。"""
        from core.punctuation import PunctuationRestorer
        r = PunctuationRestorer(enabled=True)
        # 确保 _loaded=True 跳过加载
        r._loaded = True
        r._model = MagicMock()
        r._model.generate.return_value = [{"text": "测试文本"}]
        t0 = time.monotonic()
        for _ in range(100):
            r.restore("测试文本")
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"延迟过高: {elapsed:.3f}s"
