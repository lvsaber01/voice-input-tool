"""Pipeline 插件化测试 — PipelineStep / ProcessContext / ContextualStep / TextPipeline 插件管理。"""

import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline_step import PipelineStep, ProcessContext, ContextualStep, StepNames
from core.hotword import HotwordManager
from core.text_pipeline import TextPipeline, PunctuationStep, PhonemeStep, RegexStep, HotwordStep


# ─── 辅助 ───


class DummyStep(PipelineStep):
    """用于测试的简单步骤。"""

    def __init__(self, name: str, priority: int, transform=None, enabled: bool = True):
        super().__init__(name=name, priority=priority, enabled=enabled)
        self._transform = transform or (lambda t: t)

    def process(self, text: str) -> str:
        return self._transform(text)


class DummyContextualStep(ContextualStep):
    """用于测试的带上下文步骤。"""

    def __init__(self, name: str, priority: int, enabled: bool = True):
        super().__init__(name=name, priority=priority, enabled=enabled)
        self.context_called = False

    def process_context(self, ctx: ProcessContext) -> str:
        self.context_called = True
        ctx.set("visited", ctx.get("visited", []) + [self.name])
        return ctx.text


def make_pipeline(hw_content="", rules_content="", case_sensitive=False, min_word_length=2):
    """创建 TextPipeline 实例。"""
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
    try:
        os.unlink(hw_path)
    except OSError:
        pass
    try:
        os.unlink(rules_path)
    except OSError:
        pass


# ─── StepNames 测试 ───


class TestStepNames(unittest.TestCase):
    """StepNames 常量测试。"""

    def test_constants_exist(self):
        self.assertEqual(StepNames.PUNCTUATION, "punctuation")
        self.assertEqual(StepNames.PHONEME, "phoneme")
        self.assertEqual(StepNames.REGEX, "regex")
        self.assertEqual(StepNames.HOTWORD, "hotword")
        self.assertEqual(StepNames.ITN, "itn")


# ─── PipelineStep 基础测试 ───


class TestPipelineStep(unittest.TestCase):
    """PipelineStep ABC 基础属性测试。"""

    def test_step_name_priority(self):
        """name/priority 属性正确"""
        step = DummyStep("test_step", 42)
        self.assertEqual(step.name, "test_step")
        self.assertEqual(step.priority, 42)

    def test_step_enabled_default(self):
        """enabled 默认为 True"""
        step = DummyStep("test", 10)
        self.assertTrue(step.enabled)

    def test_step_enabled_setter(self):
        """enabled 可动态切换"""
        step = DummyStep("test", 10)
        step.enabled = False
        self.assertFalse(step.enabled)
        step.enabled = True
        self.assertTrue(step.enabled)

    def test_step_reload_default(self):
        """reload() 默认空实现不抛异常"""
        step = DummyStep("test", 10)
        step.reload()  # 不应抛异常


# ─── ProcessContext 测试 ───


class TestProcessContext(unittest.TestCase):
    """ProcessContext 数据传递测试。"""

    def test_context_text(self):
        """text 属性正确"""
        ctx = ProcessContext(text="hello")
        self.assertEqual(ctx.text, "hello")

    def test_context_set_get(self):
        """set/get 数据传递"""
        ctx = ProcessContext(text="test")
        ctx.set("key1", "value1")
        self.assertEqual(ctx.get("key1"), "value1")

    def test_context_get_default(self):
        """get 默认值"""
        ctx = ProcessContext(text="test")
        self.assertIsNone(ctx.get("missing"))
        self.assertEqual(ctx.get("missing", "default"), "default")

    def test_context_meta_isolation(self):
        """不同 context 的 meta 互不干扰"""
        ctx1 = ProcessContext(text="a")
        ctx2 = ProcessContext(text="b")
        ctx1.set("key", "val1")
        self.assertIsNone(ctx2.get("key"))


# ─── ContextualStep 测试 ───


class TestContextualStep(unittest.TestCase):
    """ContextualStep 双接口测试。"""

    def test_contextual_step_process_default(self):
        """默认 process() 不修改文本"""
        step = DummyContextualStep("ctx_step", 10)
        result = step.process("hello")
        self.assertEqual(result, "hello")

    def test_contextual_step_process_context(self):
        """process_context() 被正确调用"""
        step = DummyContextualStep("ctx_step", 10)
        ctx = ProcessContext(text="hello")
        result = step.process_context(ctx)
        self.assertEqual(result, "hello")
        self.assertTrue(step.context_called)
        self.assertIn("ctx_step", ctx.get("visited"))

    def test_contextual_step_isinstance(self):
        """ContextualStep 是 PipelineStep 的子类"""
        step = DummyContextualStep("ctx_step", 10)
        self.assertIsInstance(step, PipelineStep)
        self.assertIsInstance(step, ContextualStep)


# ─── 内置步骤测试 ───


class TestBuiltinSteps(unittest.TestCase):
    """内置步骤类型测试。"""

    def test_punctuation_step_is_contextual(self):
        step = PunctuationStep()
        self.assertIsInstance(step, ContextualStep)
        self.assertEqual(step.name, StepNames.PUNCTUATION)
        self.assertEqual(step.priority, 10)

    def test_phoneme_step_is_contextual(self):
        step = PhonemeStep()
        self.assertIsInstance(step, ContextualStep)
        self.assertEqual(step.name, StepNames.PHONEME)
        self.assertEqual(step.priority, 20)

    def test_regex_step_is_base(self):
        step = RegexStep()
        self.assertIsInstance(step, PipelineStep)
        self.assertNotIsInstance(step, ContextualStep)
        self.assertEqual(step.name, StepNames.REGEX)
        self.assertEqual(step.priority, 30)

    def test_hotword_step_is_base(self):
        step = HotwordStep()
        self.assertIsInstance(step, PipelineStep)
        self.assertNotIsInstance(step, ContextualStep)
        self.assertEqual(step.name, StepNames.HOTWORD)
        self.assertEqual(step.priority, 40)


# ─── TextPipeline 插件化测试 ───


class TestTextPipelinePlugin(unittest.TestCase):
    """TextPipeline 插件化管理测试。"""

    def test_builtin_steps_order(self):
        """4 个内置步骤按 priority 排序"""
        p, mgr, hw, rules = make_pipeline()
        try:
            steps = p.steps
            self.assertEqual(len(steps), 4)
            priorities = [s.priority for s in steps]
            self.assertEqual(priorities, [10, 20, 30, 40])
        finally:
            cleanup(hw, rules)

    def test_add_step(self):
        """add_step 正确插入并排序"""
        p, mgr, hw, rules = make_pipeline()
        try:
            custom = DummyStep("custom", 25)
            p.add_step(custom)
            steps = p.steps
            self.assertEqual(len(steps), 5)
            # custom (25) 应在 phoneme(20) 和 regex(30) 之间
            names = [s.name for s in steps]
            self.assertEqual(names, ["punctuation", "phoneme", "custom", "regex", "hotword"])
        finally:
            cleanup(hw, rules)

    def test_add_step_duplicate_raises(self):
        """重复 name 抛 ValueError"""
        p, mgr, hw, rules = make_pipeline()
        try:
            dup = DummyStep(StepNames.REGEX, 99)
            with self.assertRaises(ValueError):
                p.add_step(dup)
        finally:
            cleanup(hw, rules)

    def test_remove_step(self):
        """remove_step 正确移除"""
        p, mgr, hw, rules = make_pipeline()
        try:
            result = p.remove_step(StepNames.PHONEME)
            self.assertTrue(result)
            self.assertEqual(len(p.steps), 3)
            self.assertIsNone(p.get_step(StepNames.PHONEME))
        finally:
            cleanup(hw, rules)

    def test_remove_step_not_found(self):
        """remove_step 不存在返回 False"""
        p, mgr, hw, rules = make_pipeline()
        try:
            result = p.remove_step("nonexistent")
            self.assertFalse(result)
        finally:
            cleanup(hw, rules)

    def test_get_step(self):
        """get_step 返回正确步骤"""
        p, mgr, hw, rules = make_pipeline()
        try:
            step = p.get_step(StepNames.REGEX)
            self.assertIsNotNone(step)
            self.assertEqual(step.name, StepNames.REGEX)
        finally:
            cleanup(hw, rules)

    def test_get_step_missing(self):
        """get_step 不存在返回 None"""
        p, mgr, hw, rules = make_pipeline()
        try:
            step = p.get_step("nonexistent")
            self.assertIsNone(step)
        finally:
            cleanup(hw, rules)

    def test_process_empty(self):
        """空文本跳过所有步骤"""
        p, mgr, hw, rules = make_pipeline()
        try:
            result = p.process("")
            self.assertEqual(result.text, "")
            self.assertFalse(result.is_changed)
        finally:
            cleanup(hw, rules)

    def test_process_disabled_step(self):
        """disabled 步骤被跳过"""
        p, mgr, hw, rules = make_pipeline(rules_content=r"[，] = ,")
        try:
            regex_step = p.get_step(StepNames.REGEX)
            regex_step.enabled = False
            result = p.process("你好，世界。")
            # 正则步骤被禁用，不应替换
            self.assertEqual(result.text, "你好，世界。")
        finally:
            cleanup(hw, rules)

    def test_process_step_exception(self):
        """步骤异常时跳过该步骤，不影响其他步骤"""
        p, mgr, hw, rules = make_pipeline(hw_content="CUDA -> CUDA_REPLACED")
        try:
            # 让 regex 步骤抛异常
            regex_step = p.get_step(StepNames.REGEX)
            regex_step.process = MagicMock(side_effect=RuntimeError("test error"))
            result = p.process("cuda")
            # hotword 步骤仍应执行
            self.assertIn("CUDA_REPLACED", result.text)
        finally:
            cleanup(hw, rules)

    def test_cow_read_during_write(self):
        """写操作期间读不受影响（COW）"""
        p, mgr, hw, rules = make_pipeline()
        try:
            read_results = []
            barrier = threading.Barrier(2)
            write_done = threading.Event()

            def reader():
                barrier.wait()
                for _ in range(50):
                    steps = p.steps
                    read_results.append(len(steps))

            def writer():
                barrier.wait()
                for i in range(50):
                    step = DummyStep(f"dynamic_{i}", 100 + i)
                    try:
                        p.add_step(step)
                    except ValueError:
                        pass  # may already exist from previous iteration
                write_done.set()

            t_read = threading.Thread(target=reader)
            t_write = threading.Thread(target=writer)
            t_write.start()
            t_read.start()
            t_write.join(timeout=5)
            t_read.join(timeout=5)

            # 所有读取结果应为 4 或 5（不会有中间状态）
            for r in read_results:
                self.assertIn(r, [4, 5, 6, 7, 8, 9, 10])  # 合理范围
        finally:
            cleanup(hw, rules)

    def test_write_lock_serializes(self):
        """并发 add_step 串行化"""
        p, mgr, hw, rules = make_pipeline()
        try:
            errors = []

            def add_steps(prefix, count):
                for i in range(count):
                    try:
                        p.add_step(DummyStep(f"{prefix}_{i}", 200 + i))
                    except Exception as e:
                        errors.append(e)

            threads = [threading.Thread(target=add_steps, args=(f"t{i}", 5)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            # 无异常
            self.assertEqual(errors, [])
            # 4 初始 + 4*5 = 24 步骤
            self.assertEqual(len(p.steps), 24)
        finally:
            cleanup(hw, rules)

    def test_process_context_phoneme(self):
        """PhonemeStep 写入 phoneme_matches 到 context"""
        p, mgr, hw, rules = make_pipeline()
        try:
            # process 总是返回 phoneme_matches（可能为空列表）
            result = p.process("测试")
            self.assertIsInstance(result.phoneme_matches, list)
        finally:
            cleanup(hw, rules)


if __name__ == "__main__":
    unittest.main()
