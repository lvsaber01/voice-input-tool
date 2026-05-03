"""轻量级事件总线

解决 CoreEngine 上帝类问题和 tray 调用死锁风险。
事件发布后，所有 handler 通过线程池异步执行。
"""

import logging
from typing import Callable, Dict, List
from enum import Enum, auto
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class EngineEvent(Enum):
    STATE_CHANGED = auto()          # (old_state, new_state)
    RECORDING_STARTED = auto()      # ()
    RECORDING_STOPPED = auto()      # ()
    TRANSCRIBE_COMPLETE = auto()    # (text, language, duration_ms)
    TRANSCRIBE_ERROR = auto()       # (error,)
    TEXT_INJECTED = auto()          # (text,)
    RMS_UPDATE = auto()             # (level, is_speech)
    MAX_DURATION_TRIGGERED = auto() # ()
    COMMAND_EXECUTED = auto()       # (command_name,)
    RECORDING_PAUSED = auto()       # ()
    RECORDING_RESUMED = auto()      # ()
    ENGINE_SHUTDOWN = auto()        # ()


class EventBus:
    """进程内事件总线

    - handler 通过 ThreadPoolExecutor(max_workers=4) 异步执行
    - 避免在 engine 状态锁中同步调用外部组件（tray/stats/vad）
    - handler 异常不向上传播，仅记录日志
    """

    def __init__(self):
        self._handlers: Dict[EngineEvent, List[Callable]] = {}
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="evt")

    def subscribe(self, event: EngineEvent, handler: Callable):
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)

    def publish(self, event: EngineEvent, *args):
        handlers = self._handlers.get(event, [])
        for h in handlers:
            self._executor.submit(self._safe_call, h, event, args)

    def _safe_call(self, handler, event, args):
        try:
            handler(*args)
        except Exception as e:
            logger.error("Event handler error [%s]: %s", event.name, e)

    def shutdown(self):
        """优雅关闭，等待已提交事件完成。

        cancel_futures=True 取消尚未开始的任务（Python 3.9+）。
        超时后强制关闭，不无限挂起。
        """
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            # Python 3.8 不支持 cancel_futures
            self._executor.shutdown(wait=False)
