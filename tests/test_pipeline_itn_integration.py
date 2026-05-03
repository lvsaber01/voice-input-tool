"""Pipeline + ITN 集成测试。

验证 ITNStep 在 TextPipeline 中正确注册和执行。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.text_pipeline import TextPipeline
from core.pipeline_step import StepNames
from core.itn import ITNStep


class TestPipelineITNIntegration(unittest.TestCase):
    """Pipeline + ITN 集成测试。"""

    def _make_pipeline(self, itn_enabled=True):
        """创建含 ITN 的 pipeline。"""
        pipeline = TextPipeline(MagicMock())
        itn = ITNStep(enabled=itn_enabled)
        pipeline.add_step(itn)
        return pipeline

    def test_pipeline_with_itn(self):
        """ITNStep 注册后 pipeline 正确转换数字。"""
        pipeline = self._make_pipeline()
        result = pipeline.process("三点十五分开会")
        self.assertEqual(result.text, "03:15开会")

    def test_pipeline_itn_after_hotword(self):
        """热词先执行，ITN 后执行（优先级验证）。"""
        pipeline = self._make_pipeline()

        # 获取各步骤优先级
        itn_step = pipeline.get_step(StepNames.ITN)
        hotword_step = pipeline.get_step(StepNames.HOTWORD)
        if hotword_step:
            self.assertLess(hotword_step.priority, itn_step.priority)

    def test_pipeline_itn_disabled(self):
        """ITNStep disabled 时不转换。"""
        pipeline = self._make_pipeline(itn_enabled=False)
        result = pipeline.process("一百二十三")
        # disabled 的步骤被跳过
        self.assertEqual(result.text, "一百二十三")

    def test_pipeline_itn_value_conversion(self):
        """Pipeline 中 ITN 位权转换。"""
        pipeline = self._make_pipeline()
        result = pipeline.process("三百六十五天")
        self.assertEqual(result.text, "365天")

    def test_pipeline_itn_idiom_protection(self):
        """Pipeline 中 ITN 成语保护。"""
        pipeline = self._make_pipeline()
        result = pipeline.process("乱七八糟的事情")
        self.assertEqual(result.text, "乱七八糟的事情")

    def test_pipeline_itn_phone(self):
        """Pipeline 中 ITN 电话转换。"""
        pipeline = self._make_pipeline()
        result = pipeline.process("电话一八五零零一二三四五六")
        self.assertEqual(result.text, "电话18500123456")

    def test_pipeline_itn_date(self):
        """Pipeline 中 ITN 日期转换。"""
        pipeline = self._make_pipeline()
        result = pipeline.process("二零二六年五月三日")
        self.assertEqual(result.text, "2026年5月3日")


if __name__ == "__main__":
    unittest.main()
