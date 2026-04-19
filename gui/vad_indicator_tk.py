"""VAD 指示器 tkinter 实现（跨平台）

使用 tkinter 实现无边框置顶圆形浮窗，适用于 macOS / Linux。
独立线程运行 mainloop，通过 after() 调度保证线程安全。
"""

import threading
import logging
import time
from typing import Optional

from gui.vad_indicator import VADIndicator

logger = logging.getLogger(__name__)

UPDATE_INTERVAL_MS = 50  # 20fps


class TkVADIndicator(VADIndicator):
    """tkinter VAD 指示器窗口。"""

    def __init__(self, size: int = 32):
        self._size = size
        self._root = None
        self._canvas = None
        self._oval = None
        self._thread: Optional[threading.Thread] = None
        self._level = 0.0
        self._is_speech = False
        self._visible = False
        self._lock = threading.Lock()
        self._destroyed = False

    # ── public interface ──

    def show(self):
        with self._lock:
            if self._visible and self._root is not None:
                return
            # 等待旧线程结束（防止快速 show/hide/show）
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            self._visible = True
            self._destroyed = False
            self._thread = threading.Thread(
                target=self._tk_loop, name="vad-tk", daemon=True
            )
            self._thread.start()

    def hide(self):
        with self._lock:
            self._visible = False

        root = self._root
        if root is not None:
            try:
                root.after(0, self._safe_destroy)
            except Exception:
                pass

    def update(self, level: float, is_speech: bool):
        with self._lock:
            self._level = max(0.0, min(1.0, level))
            self._is_speech = is_speech

    # ── tkinter loop ──

    def _tk_loop(self):
        try:
            import tkinter as tk
        except ImportError:
            logger.warning("tkinter 不可用，VAD 指示器无法显示")
            return

        try:
            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            # 透明背景（macOS）
            try:
                root.attributes("-transparentcolor", "white")
            except tk.TclError:
                pass

            # 位置：屏幕右下角
            root.update_idletasks()
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            margin = 20
            x = sw - self._size - margin
            y = sh - self._size - margin - 40  # 留出 dock 栏
            root.geometry(f"{self._size}x{self._size}+{x}+{y}")

            canvas = tk.Canvas(
                root,
                width=self._size,
                height=self._size,
                bg="white",
                highlightthickness=0,
            )
            canvas.pack()
            oval = canvas.create_oval(
                2, 2, self._size - 2, self._size - 2,
                fill="#808080", outline=""
            )

            self._root = root
            self._canvas = canvas
            self._oval = oval

            # 启动定时刷新
            self._schedule_refresh()
            root.mainloop()
        except Exception as e:
            logger.error("VAD tkinter 窗口异常: %s", e)
        finally:
            self._root = None
            self._canvas = None
            self._destroyed = True

    def _schedule_refresh(self):
        root = self._root
        if root is None:
            return
        try:
            self._refresh()
            root.after(UPDATE_INTERVAL_MS, self._schedule_refresh)
        except tk.TclError:
            pass

    def _refresh(self):
        canvas = self._canvas
        oval = self._oval
        if canvas is None or oval is None:
            return

        with self._lock:
            level = self._level
            is_speech = self._is_speech
            visible = self._visible

        if not visible:
            return

        # 颜色计算
        if is_speech:
            g_val = int(128 + 127 * level)
            color = f"#00{g_val:02x}00"
        elif level > 0.01:
            color = "#00FFFF"
        else:
            color = "#808080"

        try:
            canvas.itemconfig(oval, fill=color)
        except Exception:
            pass

    def _safe_destroy(self):
        root = self._root
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
