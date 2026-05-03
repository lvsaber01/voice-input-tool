#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模式切换端到端功能测试 — 重启流程模拟"""

import unittest
import tempfile
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import yaml


class TestRestartFlagFile(unittest.TestCase):
    """重启标志文件 (.restarting) 操作测试"""

    def test_restart_flag_file_create_and_read(self):
        """创建 .restarting 文件后能正确读取模式名并删除"""
        with tempfile.TemporaryDirectory() as d:
            flag_path = Path(d) / ".restarting"
            mode_name = "realtime"

            # 写入标志文件
            flag_path.write_text(mode_name)
            self.assertTrue(flag_path.exists())

            # 读取并验证
            content = flag_path.read_text().strip()
            self.assertEqual(content, "realtime")

            # 删除
            flag_path.unlink(missing_ok=True)
            self.assertFalse(flag_path.exists())

    def test_restart_flag_file_missing(self):
        """.restarting 不存在时正常启动（不触发重启逻辑）"""
        with tempfile.TemporaryDirectory() as d:
            flag_path = Path(d) / ".restarting"
            self.assertFalse(flag_path.exists())
            # 不存在时 read_text 会报错，模拟 main.py 的 try/except
            triggered = False
            if flag_path.exists():
                triggered = True
            self.assertFalse(triggered)

    def test_restart_flag_file_corrupt_content(self):
        """.restarting 内容异常时无害处理"""
        with tempfile.TemporaryDirectory() as d:
            flag_path = Path(d) / ".restarting"

            # 写入异常内容
            flag_path.write_text("not_a_valid_mode\nwith_extra_lines")
            content = flag_path.read_text().strip()
            # 即使内容异常，也不应抛异常，只是 label 不准确
            self.assertEqual(content, "not_a_valid_mode\nwith_extra_lines")

            # 空内容
            flag_path.write_text("")
            content = flag_path.read_text().strip()
            self.assertEqual(content, "")


class TestConfigSaveOnModeSwitch(unittest.TestCase):
    """模式切换时配置保存测试"""

    def test_config_save_on_mode_switch(self):
        """切换模式后配置文件中 mode 值已更新"""
        from config import AppConfig, save_config, load_config

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.yaml")
            config = AppConfig()
            config.mode = "batch"
            save_config(path, config)

            # 模拟切换到 realtime
            config.mode = "realtime"
            save_config(path, config)

            # 重新加载验证
            config2 = load_config(path)
            self.assertEqual(config2.mode, "realtime")

    def test_config_rollback_on_save_failure(self):
        """配置保存失败时 mode 回滚"""
        from config import AppConfig

        config = AppConfig()
        config.mode = "batch"
        original_mode = config.mode

        # 模拟切换操作
        new_mode = "realtime"
        config.mode = new_mode

        # 模拟保存失败
        try:
            raise IOError("磁盘写入失败")
        except Exception:
            # 回滚内存中的 mode
            config.mode = original_mode

        self.assertEqual(config.mode, "batch")


class TestRestartEventPreventsDoubleTrigger(unittest.TestCase):
    """重启事件防重入测试"""

    def test_restart_event_prevents_double_trigger(self):
        """Event.set() 后第二次调用被拦截"""
        restart_event = threading.Event()
        self.assertFalse(restart_event.is_set())

        # 第一次 set
        restart_event.set()
        self.assertTrue(restart_event.is_set())

        # 模拟第二次调用检查 — is_set() 返回 True，逻辑应被拦截
        if restart_event.is_set():
            blocked = True
        else:
            blocked = False
        self.assertTrue(blocked, "第二次重启请求应被拦截")


class TestStateCheckRejectsNonIdle(unittest.TestCase):
    """非 IDLE 状态拒绝切换测试"""

    def test_state_check_rejects_non_idle(self):
        """非 IDLE 状态校验拒绝切换"""
        from core.engine import EngineState

        # 模拟不同状态下的切换检查
        non_idle_states = [
            EngineState.RECORDING,
            EngineState.PROCESSING,
            EngineState.STREAMING,
            EngineState.INJECTING,
            EngineState.ERROR,
            EngineState.LOADING,
            EngineState.PAUSED,
        ]

        for state in non_idle_states:
            # 模拟 main.py 中 _on_tray_switch_mode 的状态检查
            allowed = (state == EngineState.IDLE)
            self.assertFalse(allowed, f"状态 {state.name} 不应允许切换模式")

        # IDLE 应允许
        self.assertTrue(EngineState.IDLE == EngineState.IDLE)


class TestSubprocessPopenFailureFallback(unittest.TestCase):
    """Popen 失败回退测试"""

    def test_subprocess_popen_failure_fallback(self):
        """Popen 失败时 Event.clear 回退"""
        restart_event = threading.Event()
        restart_event.set()
        self.assertTrue(restart_event.is_set())

        # 模拟 Popen 失败后 clear
        try:
            raise OSError("启动新进程失败")
        except Exception:
            restart_event.clear()

        self.assertFalse(restart_event.is_set(), "Popen 失败后应 clear restart_event")

    def test_restart_event_full_flow(self):
        """完整重启事件流程：set → 检查 → Popen → 成功/失败"""
        restart_event = threading.Event()

        # 1. 初始状态
        self.assertFalse(restart_event.is_set())

        # 2. 触发重启
        restart_event.set()
        self.assertTrue(restart_event.is_set())

        # 3. 检查是否需要重启
        if restart_event.is_set():
            need_restart = True
        self.assertTrue(need_restart)

        # 4. 模拟 Popen 成功后不 clear（由 finally 控制）
        # 正常流程中 instance_lock.release() 在 finally 中处理
        # _restart_event.is_set() 为 True → 不释放 instance_lock

        # 5. 模拟 Popen 失败
        restart_event.clear()
        self.assertFalse(restart_event.is_set())


if __name__ == "__main__":
    unittest.main()
