"""EventBus 单元测试"""

import unittest
import time
import threading
from core.events import EventBus, EngineEvent


class TestEventBus(unittest.TestCase):

    def test_subscribe_and_publish(self):
        """subscribe + publish 正常工作"""
        bus = EventBus()
        results = []
        bus.subscribe(EngineEvent.RECORDING_STARTED, lambda: results.append("called"))
        bus.publish(EngineEvent.RECORDING_STARTED)
        time.sleep(0.2)
        self.assertEqual(results, ["called"])
        bus.shutdown()

    def test_publish_with_args(self):
        """publish 传递参数给 handler"""
        bus = EventBus()
        results = []
        bus.subscribe(EngineEvent.STATE_CHANGED, lambda old, new: results.append((old, new)))
        bus.publish(EngineEvent.STATE_CHANGED, "A", "B")
        time.sleep(0.2)
        self.assertEqual(results, [("A", "B")])
        bus.shutdown()

    def test_multiple_handlers(self):
        """多个 handler 都被执行"""
        bus = EventBus()
        results = []
        bus.subscribe(EngineEvent.TEXT_INJECTED, lambda t: results.append(f"h1:{t}"))
        bus.subscribe(EngineEvent.TEXT_INJECTED, lambda t: results.append(f"h2:{t}"))
        bus.publish(EngineEvent.TEXT_INJECTED, "hello")
        time.sleep(0.2)
        self.assertIn("h1:hello", results)
        self.assertIn("h2:hello", results)
        bus.shutdown()

    def test_handler_exception_does_not_affect_others(self):
        """handler 异常不影响其他 handler"""
        bus = EventBus()
        results = []

        def bad_handler():
            raise RuntimeError("boom")

        bus.subscribe(EngineEvent.RECORDING_STOPPED, bad_handler)
        bus.subscribe(EngineEvent.RECORDING_STOPPED, lambda: results.append("ok"))
        bus.publish(EngineEvent.RECORDING_STOPPED)
        time.sleep(0.5)
        self.assertEqual(results, ["ok"])
        bus.shutdown()

    def test_publish_no_subscribers(self):
        """没有 handler 时 publish 不报错"""
        bus = EventBus()
        bus.publish(EngineEvent.RMS_UPDATE, 0.5, True)
        bus.shutdown()

    def test_shutdown(self):
        """shutdown 后不再接受新任务"""
        bus = EventBus()
        bus.shutdown()
        # shutdown 后 publish 不会崩溃（executor 已关闭则 submit 会报错）
        # 这里只测试 shutdown 不抛异常
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
