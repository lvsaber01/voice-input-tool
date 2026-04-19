"""UsageStats 单元测试"""

import unittest
import json
import tempfile
import shutil
import os
import time
from unittest.mock import patch
from datetime import date, timedelta

from core.stats import UsageStats


class TestUsageStats(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_transcribe_params(self):
        """record_transcribe 参数顺序正确 (text, language, duration_ms)"""
        stats = UsageStats(stats_dir=self.tmpdir)
        stats.record_transcribe("hello", "en", 500)
        s = stats.get_summary(1)
        self.assertEqual(s["total_transcribe"], 1)
        self.assertEqual(s["total_duration_ms"], 500)
        self.assertIn("en", s["languages"])
        self.assertEqual(s["languages"]["en"], 1)

    def test_empty_text_counts_empty_result(self):
        """空文本计入 empty_result_count"""
        stats = UsageStats(stats_dir=self.tmpdir)
        stats.record_transcribe("", None, 100)
        stats.record_transcribe("   ", None, 50)
        stats.record_transcribe("hello", "en", 200)
        s = stats.get_summary(1)
        self.assertEqual(s["total_empty"], 2)
        self.assertEqual(s["total_transcribe"], 3)

    def test_get_summary_copy_consistency(self):
        """get_summary 返回的是拷贝，不影响内部数据"""
        stats = UsageStats(stats_dir=self.tmpdir)
        stats.record_transcribe("hello", "en", 100)
        s1 = stats.get_summary(1)
        s1["total_transcribe"] = 999
        s2 = stats.get_summary(1)
        self.assertEqual(s2["total_transcribe"], 1)

    def test_persistence_to_json(self):
        """持久化到 JSON 文件"""
        stats = UsageStats(stats_dir=self.tmpdir)
        stats.record_transcribe("hello", "en", 100)
        # flush 已在 record_transcribe 中调用
        path = os.path.join(self.tmpdir, f"stats_{date.today().isoformat()}.json")
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["transcribe_count"], 1)

    def test_cross_day_rollover(self):
        """跨天自动切换"""
        stats = UsageStats(stats_dir=self.tmpdir)
        stats.record_transcribe("day1", "en", 100)

        # 模拟跨天
        tomorrow = date.today() + timedelta(days=1)
        with stats._lock:
            stats._today = date.today() - timedelta(days=1)
            stats._data = stats._new_day_data()

        # 触发 _check_date_rollover（通过 record_transcribe）
        stats.record_transcribe("day2", "en", 200)
        self.assertEqual(stats._today, date.today())
        self.assertEqual(stats._data["transcribe_count"], 1)


if __name__ == "__main__":
    unittest.main()
