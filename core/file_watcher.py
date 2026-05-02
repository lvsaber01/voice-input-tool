"""文件监控模块 — 基于 watchdog 的热词文件变更检测。

封装 watchdog 库的文件监控逻辑，带防抖。
由 engine.py 初始化和生命周期管理。

设计文档：2026-05-02-postprocess-enhancement-design.md 第四章
"""

import os
import logging
import threading
from typing import Dict, Callable, Optional

try:
    from watchdog.observers import Observer
    from watchdog.events import (
        FileSystemEventHandler,
        FileModifiedEvent,
        FileCreatedEvent,
        FileDeletedEvent,
    )
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False
    Observer = None
    FileSystemEventHandler = None

logger = logging.getLogger(__name__)

_FORBIDDEN_DIRS = {"/", "\\", os.sep, os.path.expanduser("~")}


class _FileChangeHandler(FileSystemEventHandler if FileSystemEventHandler else object):
    """watchdog 事件处理器。"""

    def __init__(self, on_event: Callable):
        self._on_event = on_event

    def on_modified(self, event):
        self._on_event(event)

    def on_created(self, event):
        self._on_event(event)

    def on_deleted(self, event):
        self._on_event(event)


class FileWatcher:
    """基于 watchdog 的文件变更监控，带防抖。

    生命周期: start() → 运行 → stop()
    线程: daemon thread（不阻塞主线程退出）

    内部使用 watchdog.observers.Observer + FileSystemEventHandler。
    防抖通过 threading.Timer 实现（Timer 内部有锁，cancel/start 线程安全）。

    统一采用「监控父目录 + basename 过滤」模式，无论文件是否存在。
    """

    def __init__(
        self,
        paths: Dict[str, Callable[[], None]],
        debounce_seconds: float = 1.0,
    ):
        """初始化文件监控。

        Args:
            paths: {文件绝对路径: 变更回调} 映射。
                   文件不存在时仍然监控其父目录，等待创建。
            debounce_seconds: 防抖间隔（秒），同一文件多次变更合并为一次。
        """
        self._paths: Dict[str, Callable[[], None]] = dict(paths)
        self._debounce_seconds = debounce_seconds
        self._timers: Dict[str, threading.Timer] = {}
        self._observer = None
        self._started = False
        # {目录路径: {文件 basename: 回调}}
        self._dir_watches: Dict[str, Dict[str, Callable[[], None]]] = {}

    def start(self) -> None:
        """启动文件监控（daemon thread）。

        对每个路径，统一监控其父目录，通过 basename 过滤事件。
        同一目录只 schedule 一次（避免重复）。
        空路径时不启动。
        """
        if not self._paths:
            logger.info("FileWatcher: 无监控路径，不启动")
            return

        self._observer = Observer()
        handler = _FileChangeHandler(self._on_file_event)

        dirs_to_watch: set = set()

        for file_path, callback in self._paths.items():
            abs_path = os.path.abspath(file_path)
            parent = os.path.dirname(abs_path)

            # 跳过根目录（Windows: C:\, Unix: /, 以及空路径）
            is_root = (not parent or parent in _FORBIDDEN_DIRS
                       or (len(parent) <= 3 and parent.endswith(("\\", "/"))))
            if is_root:
                logger.warning("FileWatcher: 路径 %s 的父目录为根目录，跳过", file_path)
                continue

            basename = os.path.basename(abs_path)
            self._dir_watches.setdefault(parent, {})[basename] = callback
            dirs_to_watch.add(parent)

            if not os.path.isfile(abs_path):
                logger.info("FileWatcher: %s 不存在，监控目录 %s 等待创建", basename, parent)

        # 按目录去重，只 schedule 一次
        for dir_path in sorted(dirs_to_watch):
            self._observer.schedule(handler, dir_path, recursive=False)

        if self._dir_watches:
            self._observer.daemon = True
            self._observer.start()
            self._started = True
            logger.info("FileWatcher: 已启动，监控 %d 个目录", len(self._dir_watches))
        else:
            logger.warning("FileWatcher: 无有效监控路径")

    def stop(self) -> None:
        """停止文件监控，等待线程退出（超时 5s）。"""
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()

        if self._observer:
            self._observer.stop()
            if self._started:
                self._observer.join(timeout=5.0)
            self._observer = None

        self._started = False
        logger.info("FileWatcher: 已停止")

    def _on_file_event(self, event) -> None:
        """处理文件变更事件（由 watchdog handler 调用）。"""
        if event.is_directory:
            return

        basename = os.path.basename(event.src_path)
        parent = os.path.dirname(os.path.abspath(event.src_path))

        watches = self._dir_watches.get(parent, {})
        callback = watches.get(basename)
        if callback is None:
            return

        # 文件删除：不触发 reload，记录日志，继续监控目录等待重建
        if isinstance(event, FileDeletedEvent):
            logger.info("FileWatcher: %s 已删除，继续监控等待重建", basename)
            return

        # 防抖：FileModifiedEvent / FileCreatedEvent
        key = f"{parent}:{basename}"
        if key in self._timers:
            self._timers[key].cancel()

        self._timers[key] = threading.Timer(
            self._debounce_seconds, self._safe_call, args=(callback,)
        )
        self._timers[key].daemon = True
        self._timers[key].start()

    @staticmethod
    def _safe_call(callback: Callable[[], None]) -> None:
        """安全执行回调，异常不影响监控。"""
        try:
            callback()
        except Exception as e:
            logger.error("FileWatcher 回调异常: %s", e)

    @property
    def is_watching(self) -> bool:
        return (self._started and self._observer is not None
                and self._observer.is_alive())
