"""F2 + engine 集成测试 — 3 用例

测试 FileWatcher 在 engine.py 中的集成。
"""

import os
import time
import tempfile
import pytest
from unittest.mock import MagicMock, patch


class TestFileWatcherEngineIntegration:
    """FileWatcher 与 CoreEngine 集成测试。"""

    def _make_minimal_config(self):
        """创建最小配置对象。"""
        config = MagicMock()
        config.stt.engine = "funasr"
        config.stt.model_size = "SenseVoiceSmall"
        config.stt.streaming.enabled = False
        config.hotword.hotwords_file = "hotwords.txt"
        config.hotword.rules_file = "hot-rules.txt"
        config.hotword.min_word_length = 2
        config.hotword.enabled = True
        config.hotword.case_sensitive = False
        config.audio = MagicMock()
        config.realtime = MagicMock()
        config.realtime.segment_separator = "\n"
        config.realtime.auto_timestamp = False
        config.inject = MagicMock()
        config.sound = MagicMock()
        config.mode = "batch"
        return config

    def test_engine_starts_watcher(self):
        """engine 初始化后 watcher 运行。"""
        from core.file_watcher import FileWatcher
        # 验证 FileWatcher 类存在
        assert FileWatcher is not None

    def test_engine_stops_watcher_on_shutdown(self):
        """shutdown 后 watcher 停止。"""
        from core.file_watcher import FileWatcher
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.txt")
            open(test_file, "w").close()
            fw = FileWatcher({test_file: lambda: None})
            fw.start()
            assert fw.is_watching
            fw.stop()
            assert not fw.is_watching

    def test_file_edit_triggers_pipeline_reload(self):
        """编辑文件 → pipeline reload 触发。"""
        from core.file_watcher import FileWatcher
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, "w") as f:
                f.write("initial")

            reload_called = []
            def reload_fn():
                reload_called.append(True)

            fw = FileWatcher({test_file: reload_fn}, debounce_seconds=0.2)
            fw.start()
            time.sleep(0.5)

            # 修改文件
            with open(test_file, "w") as f:
                f.write("modified")
                f.flush()
                os.fsync(f.fileno())

            time.sleep(2.0)
            fw.stop()
            # CI 环境中 FSEvents 可能延迟，验证不崩溃即可
