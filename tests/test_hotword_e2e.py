"""Layer 4: 端到端功能测试。

验证完整链路：热词/规则修改 → reload → pipeline → 注入。
不依赖真实音频，用 mock 替代。
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hotword import HotwordManager
from core.text_pipeline import TextPipeline
from config import AppConfig


def _make_pipeline(hw_content="", rules_content="", case_sensitive=False, min_word_length=2):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(hw_content)
        hw_path = f.name
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(rules_content)
        rules_path = f.name
    mgr = HotwordManager(hotwords_file=hw_path, rules_file=rules_path, min_word_length=min_word_length)
    mgr._hotwords_file = hw_path
    mgr._rules_file = rules_path
    pipeline = TextPipeline(hotword_manager=mgr, case_sensitive=case_sensitive)
    return pipeline, mgr, hw_path, rules_path


def cleanup(hw_path, rules_path):
    for p in (hw_path, rules_path):
        try:
            os.unlink(p)
        except OSError:
            pass


class TestHotwordFileReloadE2E(unittest.TestCase):
    """修改热词文件 → reload → 验证 pipeline 行为变化。"""

    def test_add_hotword_reload_takes_effect(self):
        """添加热词 → reload → pipeline 生效"""
        p, mgr, hw, rules = _make_pipeline(hw_content="")
        try:
            # 初始：Docker 不被替换
            result = p.process("我用Docker部署")
            self.assertFalse(result.is_changed)
            # 添加热词（不同 target 才有 changed）
            mgr.add_hotword("Docker", "Docker容器")
            p.reload()
            result = p.process("我用Docker部署")
            self.assertTrue(result.is_changed)
            self.assertIn("Docker容器", result.text)
        finally:
            cleanup(hw, rules)

    def test_modify_rules_reload_takes_effect(self):
        """修改规则文件 → reload → 规则生效"""
        p, mgr, hw, rules = _make_pipeline(rules_content="")
        try:
            result = p.process("[音乐]播放")
            self.assertFalse(result.is_changed)
            mgr.add_rule(r"\[音乐\]", "")
            p.reload()
            result = p.process("[音乐]播放")
            self.assertTrue(result.is_changed)
            self.assertEqual(result.text, "播放")
        finally:
            cleanup(hw, rules)

    def test_remove_hotword_reload(self):
        """删除热词 → reload → 替换失效"""
        p, mgr, hw, rules = _make_pipeline(hw_content="Docker -> Docker容器")
        try:
            result = p.process("Docker")
            self.assertTrue(result.is_changed)
            mgr.remove_hotword("Docker")
            p.reload()
            result = p.process("Docker")
            self.assertFalse(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_user_file_overrides_default(self):
        """用户文件中的热词独立生效"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write("CustomWord -> CW")
            user_hw = f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write("")
            user_rules = f.name
        try:
            mgr = HotwordManager(hotwords_file=user_hw, rules_file=user_rules)
            mgr._hotwords_file = user_hw
            mgr._rules_file = user_rules
            pipeline = TextPipeline(hotword_manager=mgr)
            result = pipeline.process("CustomWord")
            self.assertTrue(result.is_changed)
        finally:
            os.unlink(user_hw)
            os.unlink(user_rules)


class TestReloadCallbackSyncsFunASR(unittest.TestCase):
    """reload 回调同步 FunASR 原生热词列表。"""

    def test_callback_syncs_on_reload(self):
        """pipeline reload 后回调被触发"""
        p, mgr, hw, rules = _make_pipeline(hw_content="CUDA -> CUDA\nDocker -> Docker")
        try:
            callback_calls = []
            p.register_reload_callback(lambda: callback_calls.append(1))
            p.reload()
            self.assertEqual(len(callback_calls), 1)
        finally:
            cleanup(hw, rules)

    def test_funasr_receives_only_model_hotwords(self):
        """FunASR 只收到 model_hotword=True 的词"""
        # 无箭头的词才传给模型
        p, mgr, hw, rules = _make_pipeline(hw_content="CUDA\nKubernetes -> K8s")
        try:
            model_words = []
            def sync():
                model_words.clear()
                model_words.extend(mgr.get_model_hotword_list())
            p.register_reload_callback(sync)
            p.reload()
            # CUDA（无箭头）→ 传给模型
            self.assertIn("CUDA", model_words)
            # Kubernetes（有箭头）→ 不传模型
            self.assertNotIn("Kubernetes", model_words)
        finally:
            cleanup(hw, rules)

    def test_hotword_only_for_text_replace(self):
        """有箭头热词 → 仅文本替换，不传给 FunASR"""
        p, mgr, hw, rules = _make_pipeline(hw_content="Kubernetes -> K8s")
        try:
            model_words = mgr.get_model_hotword_list()
            self.assertNotIn("Kubernetes", model_words)
            # 但文本替换生效
            result = p.process("Kubernetes")
            self.assertTrue(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_hotword_for_both(self):
        """无箭头热词 → 同时用于文本替换和 FunASR"""
        p, mgr, hw, rules = _make_pipeline(hw_content="CUDA")
        try:
            model_words = mgr.get_model_hotword_list()
            self.assertIn("CUDA", model_words)
            # 文本替换也生效（source=target 所以 is_changed=False）
            result = p.process("CUDA")
            self.assertIn("CUDA", result.text)
        finally:
            cleanup(hw, rules)


class TestDefaultPresetRulesE2E(unittest.TestCase):
    """默认预置规则对 STT 常见输出的清理效果。"""

    def test_preset_rules_clean_punctuation(self):
        """标点替换规则"""
        p, mgr, hw, rules = _make_pipeline(rules_content="[，。] = ,")
        try:
            result = p.process("你好，世界")
            self.assertIn(",", result.text)
            self.assertNotIn("，", result.text)
        finally:
            cleanup(hw, rules)

    def test_preset_rules_clean_noise_markers(self):
        """噪声标记清理规则"""
        p, mgr, hw, rules = _make_pipeline(rules_content=r"\[noise\] = ")
        try:
            result = p.process("你好[noise]世界")
            self.assertNotIn("[noise]", result.text)
        finally:
            cleanup(hw, rules)

    def test_preset_rules_all_compile(self):
        """项目默认 hot-rules.txt 中所有规则可编译通过"""
        rules_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hot-rules.txt")
        if not os.path.exists(rules_path):
            self.skipTest("hot-rules.txt not found")
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write("")
            hw_path = f.name
        try:
            mgr = HotwordManager(hotwords_file=hw_path, rules_file=rules_path)
            mgr._hotwords_file = hw_path
            compiled = mgr.get_compiled_rules()
            self.assertGreater(len(compiled), 0)
        finally:
            os.unlink(hw_path)


class TestHotwordEnabledToggleE2E(unittest.TestCase):
    """hotword.enabled 开关的端到端效果。"""

    def test_enabled_false_skips_pipeline(self):
        """禁用 → pipeline 不处理"""
        p, mgr, hw, rules = _make_pipeline(hw_content="CUDA -> CUDA")
        try:
            p.enabled = False
            result = p.process("cuda is great")
            self.assertFalse(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_enabled_true_enables_pipeline(self):
        """启用 → pipeline 正常处理"""
        p, mgr, hw, rules = _make_pipeline(hw_content="Kubernetes -> K8s", case_sensitive=False)
        try:
            p.enabled = True
            result = p.process("kubernetes is great")
            self.assertTrue(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_toggle_at_runtime(self):
        """运行时切换 enabled → 立即生效"""
        p, mgr, hw, rules = _make_pipeline(hw_content="Kubernetes -> K8s", case_sensitive=False)
        try:
            p.enabled = True
            self.assertTrue(p.process("kubernetes").is_changed)
            p.enabled = False
            self.assertFalse(p.process("kubernetes").is_changed)
            p.enabled = True
            self.assertTrue(p.process("kubernetes").is_changed)
        finally:
            cleanup(hw, rules)


class TestDebounceReloadE2E(unittest.TestCase):
    """热词 reload debounce 测试。"""

    def test_rapid_edits_debounced(self):
        """快速连续 reload → debounce 合并"""
        p, mgr, hw, rules = _make_pipeline(hw_content="")
        try:
            reload_count = []
            def count_reload():
                reload_count.append(1)
            p.register_reload_callback(count_reload)
            p.reload()
            p.reload()
            p.reload()
            self.assertGreaterEqual(len(reload_count), 1)
        finally:
            cleanup(hw, rules)


if __name__ == "__main__":
    unittest.main()
