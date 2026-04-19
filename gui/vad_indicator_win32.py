"""VAD 指示器 Win32 实现

纯 Win32 API (ctypes) 实现，无额外依赖。
使用 SetTimer + WM_TIMER 驱动重绘，PostMessage 异步关闭。
GDI 资源管理：SelectObject → 绘制 → 恢复旧 brush → DeleteObject。
WNDPROC 保存为实例属性防 GC。
"""

import ctypes
import ctypes.wintypes
import threading
import logging
import time
from typing import Optional

from gui.vad_indicator import VADIndicator

logger = logging.getLogger(__name__)

# Win32 常量
WM_TIMER = 0x0113
WM_PAINT = 0x000F
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_USER_CLOSE = 0x0401

TIMER_ID_REFRESH = 1
TIMER_INTERVAL_MS = 50  # 20fps

# 窗口类名
_WND_CLASS_NAME = "VoiceInputToolVAD"


class Win32VADWindow(VADIndicator):
    """Win32 VAD 指示器窗口。

    创建一个小的无边框置顶圆形窗口，显示语音活动状态。
    颜色：语音=绿色渐变，有声量非语音=青色，无声=灰色。
    """

    def __init__(self, size: int = 32):
        self._size = size
        self._hwnd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._level = 0.0
        self._is_speech = False
        self._visible = False
        self._lock = threading.Lock()
        # WNDPROC 保存为实例属性防 GC
        self._wnd_proc_func = None
        self._running = False
        self._class_atom = None

    def show(self):
        """显示 VAD 指示器（在独立线程中创建窗口）"""
        with self._lock:
            if self._visible and self._hwnd:
                return
            self._visible = True

        if self._thread is None or not self._thread.is_alive():
            self._running = True
            self._thread = threading.Thread(target=self._window_loop, name="vad-indicator", daemon=True)
            self._thread.start()

    def hide(self):
        """异步关闭（线程安全）"""
        with self._lock:
            self._visible = False

        if self._hwnd:
            user32 = ctypes.windll.user32
            user32.PostMessageW(self._hwnd, WM_USER_CLOSE, 0, 0)

    def update(self, level: float, is_speech: bool):
        """更新 RMS 级别和语音状态"""
        with self._lock:
            self._level = max(0.0, min(1.0, level))
            self._is_speech = is_speech

    def _window_loop(self):
        """窗口消息循环（在独立线程中运行）"""
        try:
            self._register_class()
            self._create_window()
            # 消息循环
            msg = ctypes.wintypes.MSG()
            user32 = ctypes.windll.user32
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            logger.error("VAD 窗口循环异常: %s", e)
        finally:
            self._hwnd = None
            self._running = False

    def _register_class(self):
        """注册窗口类"""
        user32 = ctypes.windll.user32
        hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)

        # WNDPROC 保存为实例属性防 GC
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.wintypes.HWND, ctypes.c_uint,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
        )
        self._wnd_proc_func = WNDPROC(self._wnd_proc)

        wc = ctypes.Structure  # WNDCLASSEXW 用 bytes 拼
        # 使用 ctypes 结构体
        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.wintypes.HINSTANCE),
                ("hIcon", ctypes.wintypes.HICON),
                ("hCursor", ctypes.wintypes.HANDLE),
                ("hbrBackground", ctypes.wintypes.HBRUSH),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", ctypes.wintypes.HICON),
            ]

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._wnd_proc_func
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinstance
        wc.hIcon = None
        wc.hCursor = user32.LoadCursorW(None, ctypes.cast(0x7F00, ctypes.wintypes.HANDLE))
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = _WND_CLASS_NAME
        wc.hIconSm = None

        self._class_atom = user32.RegisterClassExW(ctypes.byref(wc))

    def _create_window(self):
        """创建窗口"""
        user32 = ctypes.windll.user32
        hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)

        # 窗口位置：右上角
        screen_w = user32.GetSystemMetrics(0)
        margin = 20
        x = screen_w - self._size - margin
        y = margin

        # WS_EX_TOPMOST | WS_EX_TOOLWINDOW (不在任务栏显示)
        ex_style = 0x00000008 | 0x00000080
        # WS_POPUP | WS_VISIBLE
        style = 0x80000000 | 0x10000000

        self._hwnd = user32.CreateWindowExW(
            ex_style, _WND_CLASS_NAME, "VAD",
            style,
            x, y, self._size, self._size,
            None, None, hinstance, None
        )

        # 启动定时器
        user32.SetTimer(self._hwnd, TIMER_ID_REFRESH, TIMER_INTERVAL_MS, None)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        """窗口过程"""
        user32 = ctypes.windll.user32

        if msg == WM_TIMER:
            self._process_updates()
            user32.InvalidateRect(hwnd, None, True)
            return 0
        elif msg == WM_USER_CLOSE:
            user32.KillTimer(hwnd, TIMER_ID_REFRESH)
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == WM_DESTROY:
            user32.KillTimer(hwnd, TIMER_ID_REFRESH)
            user32.PostQuitMessage(0)
            return 0
        elif msg == WM_PAINT:
            self._on_paint(hwnd)
            return 0
        elif msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _process_updates(self):
        """处理更新队列（在 WM_TIMER 中调用）"""
        pass  # _level 和 _is_speech 已在 update() 中直接更新

    def _on_paint(self, hwnd):
        """WM_PAINT 处理：绘制圆形指示器"""
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        ps = ctypes.wintypes.PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

        with self._lock:
            level = self._level
            is_speech = self._is_speech

        # BGR 颜色计算
        if is_speech:
            # 绿色渐变
            g_val = int(128 + 127 * level)
            brush_color = g_val << 8  # 0x00GG00
        elif level > 0.01:
            # 青色（有音量但非语音）
            brush_color = 0xFFFF00  # BGR: 青
        else:
            # 灰色（无声）
            brush_color = 0x808080

        new_brush = gdi32.CreateSolidBrush(brush_color)
        old_brush = gdi32.SelectObject(hdc, new_brush)

        # 绘制圆形
        gdi32.Ellipse(hdc, 0, 0, self._size, self._size)

        # 恢复旧 brush 并删除新 brush
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(new_brush)

        user32.EndPaint(hwnd, ctypes.byref(ps))
