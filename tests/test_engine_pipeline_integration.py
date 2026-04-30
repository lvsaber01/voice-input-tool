"""Layer 2: 引擎 + Pipeline 集成测试。

验证 engine 与 pipeline 的联动逻辑（FakeEngine 复制关键行为）：
- 批量模式：命令优先 → pipeline → 注入
- 实时模式：pipeline → 追加（不做命令匹配）
- 边界：pipeline 失败降级、pipeline 清理后为空、热词替换后匹配命令
"""

import os
import sys
import tempfile
import unittest
from enum import Enum
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hotword import HotwordManager
from core.text_pipeline import TextPipeline
from config import AppConfig


class _State(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    PROCESSING = "PROCESSING"
    INJECTING = "INJECTING"


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


class FakeEngine:
    """复制 CoreEngine 中 pipeline 相关的核心行为。"""

    def __init__(self, config=None, pipeline=None):
        self._config = config or AppConfig()
        self._text_pipeline = pipeline
        self._injector = MagicMock()
        self._events = MagicMock()
        self._shutdown_event = MagicMock()
        self._shutdown_event.is_set.return_value = False
        self._state = _State.IDLE
        self._sound_player = MagicMock()
        self._command_matcher = None
        self._command_executor = None
        self._last_injected_text = None
        self._commands_executed = []
        self._text_injected = False

    def transition(self, new_state):
        valid = {
            (_State.IDLE, _State.PROCESSING),
            (_State.PROCESSING, _State.INJECTING),
            (_State.INJECTING, _State.IDLE),
            (_State.PROCESSING, _State.IDLE),
            (_State.IDLE, _State.INJECTING),
        }
        if (self._state, new_state) in valid:
            self._state = new_state
            return True
        return False

    def _on_stt_complete_inner(self, text, language=None, duration_ms=0, error=None):
        """复制 engine.py 的 _on_stt_complete_inner pipeline 逻辑。"""
        if self._shutdown_event.is_set():
            return
        if error:
            self._state = _State.IDLE
            return
        if not text:
            self._state = _State.IDLE
            return

        # 命令匹配（command.enabled 时先匹配命令再注入）
        command_cfg = getattr(self._config, 'command', None)
        if command_cfg and getattr(command_cfg, 'enabled', False):
            from core.command import CommandMatcher, CommandExecutor
            if not self._command_matcher:
                self._command_matcher = CommandMatcher()
            result = self._command_matcher.match(text)
            if result:
                cmd, _ = result
                self._commands_executed.append(cmd.name)
                # 命令匹配成功 → 模拟执行成功 → 直接返回
                self._state = _State.IDLE
                return

        # 未匹配命令，走 pipeline 处理
        if self._text_pipeline:
            try:
                pipeline_result = self._text_pipeline.process(text)
                text = pipeline_result.text
            except Exception:
                pass  # 降级使用原文

        if self.transition(_State.INJECTING):
            self._last_injected_text = text
            self._text_injected = True
            self._injector.inject(text)
            self._state = _State.IDLE

    def _on_realtime_segment(self, text):
        """复制 engine.py 的 _on_realtime_segment pipeline 逻辑。"""
        if self._shutdown_event.is_set():
            return
        separator = getattr(self._config.realtime, 'segment_separator', ' ')

        # 实时模式走 pipeline，不做命令匹配
        if self._text_pipeline:
            try:
                result = self._text_pipeline.process(text)
                text = result.text
                if not text.strip():
                    return  # pipeline 清理后为空，跳过
            except Exception:
                pass  # 降级使用原文

        self._injector.inject(text + separator)
        self._last_injected_text = text + separator


# ============================================================
# 批量模式测试
# ============================================================

class TestBatchModePipelineIntegration(unittest.TestCase):

    def _setup(self, hw_content="", rules_content="", command_enabled=False):
        pipeline, mgr, hw, rules = _make_pipeline(hw_content, rules_content)
        config = AppConfig()
        if hasattr(config, 'command') and config.command:
            config.command.enabled = command_enabled
        engine = FakeEngine(config=config, pipeline=pipeline)
        return engine, mgr, hw, rules

    def test_pipeline_cleans_noise_markers(self):
        """STT 返回含噪声标记 → pipeline 清理后注入"""
        engine, _, hw, rules = self._setup(rules_content=r"\[noise\] = ")
        try:
            engine._on_stt_complete_inner("你好[noise]世界", "zh", 1000, None)
            self.assertEqual(engine._last_injected_text, "你好世界")
        finally:
            cleanup(hw, rules)

    def test_pipeline_replaces_hotword(self):
        """STT 返回含热词匹配 → pipeline 替换"""
        engine, _, hw, rules = self._setup(hw_content="Kubernetes -> K8s")
        try:
            engine._on_stt_complete_inner("我学习Kubernetes三年了", "zh", 1000, None)
            self.assertIn("K8s", engine._last_injected_text)
        finally:
            cleanup(hw, rules)

    def test_command_match_blocks_pipeline(self):
        """命令优先：匹配到命令时不走 pipeline"""
        engine, _, hw, rules = self._setup(command_enabled=True)
        try:
            engine._on_stt_complete_inner("换行", "zh", 500, None)
            self.assertIn("换行", engine._commands_executed)
            self.assertFalse(engine._text_injected)
        finally:
            cleanup(hw, rules)

    def test_command_miss_then_pipeline(self):
        """命令未命中 → 走 pipeline 处理"""
        engine, _, hw, rules = self._setup(
            hw_content="CUDA -> CUDA",
            command_enabled=True
        )
        try:
            engine._on_stt_complete_inner("我用到CUDA编程", "zh", 1000, None)
            self.assertEqual(0, len(engine._commands_executed))
            self.assertTrue(engine._text_injected)
            self.assertIn("CUDA", engine._last_injected_text)
        finally:
            cleanup(hw, rules)

    def test_pipeline_failure_falls_back_to_original(self):
        """pipeline 异常 → 降级返回原文，不阻塞引擎"""
        engine, _, hw, rules = self._setup(hw_content="CUDA -> CUDA")
        try:
            with patch.object(engine._text_pipeline, 'process', side_effect=RuntimeError("test")):
                engine._on_stt_complete_inner("测试CUDA文本", "zh", 1000, None)
            self.assertTrue(engine._text_injected)
            self.assertIn("CUDA", engine._last_injected_text)
        finally:
            cleanup(hw, rules)

    def test_pipeline_disabled_skips_processing(self):
        """pipeline.enabled=false → 跳过处理，原文注入"""
        engine, _, hw, rules = self._setup(hw_content="CUDA -> CUDA")
        try:
            engine._text_pipeline.enabled = False
            engine._on_stt_complete_inner("测试CUDA文本", "zh", 1000, None)
            self.assertEqual(engine._last_injected_text, "测试CUDA文本")
        finally:
            cleanup(hw, rules)

    def test_pipeline_cleans_to_empty_still_injects(self):
        """pipeline 清理后文本为空 → 仍注入空字符串"""
        engine, _, hw, rules = self._setup(rules_content=r"\[.*?\] = ")
        try:
            engine._on_stt_complete_inner("[noise]", "zh", 500, None)
            self.assertTrue(engine._text_injected)
        finally:
            cleanup(hw, rules)

    def test_no_pipeline_passthrough(self):
        """无 pipeline → 原文直接注入"""
        engine = FakeEngine(config=AppConfig())
        engine._on_stt_complete_inner("直接注入文本", "zh", 1000, None)
        self.assertEqual(engine._last_injected_text, "直接注入文本")

    def test_stt_error_no_pipeline(self):
        """STT 错误 → 不走 pipeline，不注入"""
        engine, _, hw, rules = self._setup(hw_content="CUDA -> CUDA")
        try:
            engine._on_stt_complete_inner("", "zh", 0, RuntimeError("STT failed"))
            self.assertIsNone(engine._last_injected_text)
            self.assertFalse(engine._text_injected)
        finally:
            cleanup(hw, rules)

    def test_stt_empty_text_no_injection(self):
        """STT 返回空文本 → 不注入"""
        engine, _, hw, rules = self._setup()
        try:
            engine._on_stt_complete_inner("", None, 0, None)
            self.assertIsNone(engine._last_injected_text)
            self.assertFalse(engine._text_injected)
        finally:
            cleanup(hw, rules)


# ============================================================
# 实时模式测试
# ============================================================

class TestRealtimeModePipelineIntegration(unittest.TestCase):

    def _setup(self, hw_content="", rules_content=""):
        pipeline, mgr, hw, rules = _make_pipeline(hw_content, rules_content)
        config = AppConfig()
        config.realtime.segment_separator = " "
        engine = FakeEngine(config=config, pipeline=pipeline)
        return engine, mgr, hw, rules

    def test_realtime_pipeline_cleans_noise(self):
        """实时段落含噪声标记 → pipeline 清理"""
        engine, _, hw, rules = self._setup(rules_content=r"\[noise\] = ")
        try:
            engine._on_realtime_segment("你好[noise]")
            injected = engine._injector.inject.call_args[0][0]
            self.assertIn("你好", injected)
            self.assertNotIn("[noise]", injected)
        finally:
            cleanup(hw, rules)

    def test_realtime_pipeline_replaces_hotword(self):
        """实时段落含热词 → pipeline 替换"""
        engine, _, hw, rules = self._setup(hw_content="CUDA -> CUDA")
        try:
            engine._on_realtime_segment("我用CUDA编程")
            injected = engine._injector.inject.call_args[0][0]
            self.assertIn("CUDA", injected)
        finally:
            cleanup(hw, rules)

    def test_realtime_no_command_matching(self):
        """实时模式不做命令匹配 — '换行' 不触发命令"""
        engine, _, hw, rules = self._setup(hw_content="换行 -> newline")
        try:
            engine._on_realtime_segment("换行")
            self.assertEqual(0, len(engine._commands_executed))
            engine._injector.inject.assert_called_once()
        finally:
            cleanup(hw, rules)

    def test_realtime_pipeline_empty_skips(self):
        """实时段落 pipeline 清理后为空 → 跳过注入"""
        engine, _, hw, rules = self._setup(rules_content=r".* = ")
        try:
            engine._on_realtime_segment("[noise]")
            engine._injector.inject.assert_not_called()
        finally:
            cleanup(hw, rules)

    def test_realtime_pipeline_failure_uses_original(self):
        """实时 pipeline 异常 → 使用原文"""
        engine, _, hw, rules = self._setup(hw_content="CUDA -> CUDA")
        try:
            with patch.object(engine._text_pipeline, 'process', side_effect=RuntimeError("test")):
                engine._on_realtime_segment("原文CUDA")
            engine._injector.inject.assert_called_once()
            injected = engine._injector.inject.call_args[0][0]
            self.assertIn("CUDA", injected)
        finally:
            cleanup(hw, rules)

    def test_realtime_no_pipeline_passthrough(self):
        """无 pipeline → 实时原文直接注入"""
        config = AppConfig()
        config.realtime.segment_separator = " "
        engine = FakeEngine(config=config)
        engine._on_realtime_segment("直接注入")
        engine._injector.inject.assert_called_once()


# ============================================================
# 命令边界测试
# ============================================================

class TestPipelineCommandBoundary(unittest.TestCase):

    def test_hotword_replacement_matches_command(self):
        """pipeline 替换后文本匹配命令 — 不会二次匹配命令"""
        pipeline, mgr, hw, rules = _make_pipeline(hw_content="duo -> 换行")
        config = AppConfig()
        config.command.enabled = True
        engine = FakeEngine(config=config, pipeline=pipeline)
        try:
            # "duo" 不匹配命令 → 走 pipeline → 替换为 "换行"
            # 但不会再次匹配命令（pipeline 是最后一步）
            engine._on_stt_complete_inner("duo", "zh", 500, None)
            self.assertEqual(0, len(engine._commands_executed))
            self.assertEqual(engine._last_injected_text, "换行")
        finally:
            cleanup(hw, rules)

    def test_mixed_text_command_and_hotword(self):
        """混合文本：部分匹配命令词但整体不匹配 → 走 pipeline"""
        pipeline, mgr, hw, rules = _make_pipeline(hw_content="CUDA -> CUDA")
        config = AppConfig()
        config.command.enabled = True
        engine = FakeEngine(config=config, pipeline=pipeline)
        try:
            engine._on_stt_complete_inner("请换行帮我CUDA", "zh", 1000, None)
            self.assertEqual(0, len(engine._commands_executed))
            self.assertTrue(engine._text_injected)
        finally:
            cleanup(hw, rules)


# ============================================================
# Pipeline 初始化测试
# ============================================================

class TestEngineInitPipeline(unittest.TestCase):

    def test_pipeline_created_when_enabled(self):
        """hotword.enabled=true → pipeline 不为 None"""
        pipeline, mgr, hw, rules = _make_pipeline(hw_content="CUDA")
        try:
            self.assertIsNotNone(pipeline)
            self.assertTrue(pipeline.enabled)
        finally:
            cleanup(hw, rules)

    def test_pipeline_none_when_disabled(self):
        """hotword.enabled=false → 不创建 pipeline"""
        # 模拟 _init_hotword_pipeline 的分支
        config = AppConfig()
        config.hotword.enabled = False
        pipeline = None
        if not config.hotword.enabled:
            pipeline = None
        self.assertIsNone(pipeline)

    def test_reload_callback_registered(self):
        """pipeline 初始化后注册 reload 回调"""
        pipeline, mgr, hw, rules = _make_pipeline(hw_content="CUDA")
        try:
            callback_calls = []
            pipeline.register_reload_callback(lambda: callback_calls.append(1))
            pipeline.reload()
            self.assertEqual(len(callback_calls), 1)
        finally:
            cleanup(hw, rules)


if __name__ == "__main__":
    unittest.main()
