"""系统托盘模块 — 完整实现

职责：托盘图标、右键菜单、状态切换。
使用 pystray + Pillow 动态生成图标，不依赖外部 .png 文件。
"""

import threading
import logging
from typing import Optional, Callable

from core.engine import EngineState

logger = logging.getLogger(__name__)

# ============================================================
# 图标颜色映射（Pillow 动态生成纯色圆形图标）
# ============================================================

_STATE_COLORS = {
    EngineState.IDLE: "#4CAF50",       # 绿色
    EngineState.RECORDING: "#F44336",   # 红色
    EngineState.PROCESSING: "#FF9800",  # 橙黄色
    EngineState.STREAMING: "#2196F3",   # 蓝色（实时转写）
    EngineState.LOADING: "#9E9E9E",     # 灰色
    EngineState.ERROR: "#D32F2F",       # 深红色
}

# 状态对应的中文标签
_STATE_LABELS = {
    EngineState.IDLE: "就绪",
    EngineState.RECORDING: "录音中",
    EngineState.PROCESSING: "识别中",
    EngineState.STREAMING: "实时转写中",
    EngineState.LOADING: "加载中",
    EngineState.ERROR: "错误",
}


def _generate_icon(color: str, size: int = 64) -> "PIL.Image.Image":
    """用 Pillow 动态生成纯色圆形图标。

    Args:
        color: 十六进制颜色值，如 "#4CAF50"
        size: 图标尺寸（像素）

    Returns:
        PIL Image 对象
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 画一个带 padding 的圆形
    padding = 4
    draw.ellipse(
        [padding, padding, size - padding, size - padding],
        fill=color,
    )

    # 中间画一个简单的麦克风形状（竖线 + 圆顶 + 底部弧线）
    cx, cy = size // 2, size // 2
    # 简化：只画一个白色圆点在中心
    dot_r = size // 6
    draw.ellipse(
        [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
        fill="white",
    )

    return img


class TrayIcon:
    """系统托盘图标管理。

    Args:
        on_start: 开始录音回调
        on_stop: 停止录音回调
        on_settings: 打开设置回调
        on_quit: 退出回调
    """

    def __init__(
        self,
        on_start: Callable,
        on_stop: Callable,
        on_settings: Callable,
        on_quit: Callable,
        on_retry: Optional[Callable] = None,
        on_switch_mode: Optional[Callable] = None,
    ):
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._on_retry = on_retry
        self._on_switch_mode = on_switch_mode
        self._current_mode = "batch"  # batch | realtime

        self._state: Optional[EngineState] = EngineState.LOADING
        self._icon = None  # pystray.Icon 实例
        self._icons_cache: dict[EngineState, "PIL.Image.Image"] = {}
        self._running = False

    def _get_icon_image(self, state: EngineState):
        """获取对应状态的图标（带缓存）"""
        if state not in self._icons_cache:
            color = _STATE_COLORS.get(state, "#9E9E9E")
            self._icons_cache[state] = _generate_icon(color)
        return self._icons_cache[state]

    def _build_menu(self):
        """根据当前状态构建右键菜单"""
        import pystray

        is_recording = self._state in (EngineState.RECORDING, EngineState.STREAMING)
        is_error = self._state == EngineState.ERROR
        is_loading = self._state == EngineState.LOADING

        items = []

        # 开始/停止
        if self._state == EngineState.STREAMING:
            items.append(pystray.MenuItem("⏹ 停止转写", self._on_stop, default=False))
        elif self._state == EngineState.RECORDING:
            items.append(pystray.MenuItem("⏹ 停止录音", self._on_stop, default=False))
        elif not is_loading and not is_error:
            if self._current_mode == "realtime":
                items.append(pystray.MenuItem("📝 开始转写", self._on_start, default=False))
            else:
                items.append(pystray.MenuItem("🎤 开始录音", self._on_start, default=False))

        # 模式切换
        if not is_recording and not is_loading:
            mode_label = "📝 切换到实时转写" if self._current_mode == "batch" else "🎤 切换到批量录音"
            items.append(pystray.MenuItem(mode_label, self._on_switch_mode_clicked, default=False))

        items.append(pystray.Menu.SEPARATOR)

        # 打开设置
        items.append(
            pystray.MenuItem("⚙ 打开设置", self._on_settings, default=False)
        )

        # 检查模型
        items.append(
            pystray.MenuItem("🔍 检查模型", self._on_check_model, default=False)
        )

        # 重试加载模型（仅 ERROR 状态）
        if is_error:
            items.append(
                pystray.MenuItem("🔄 重试加载模型", self._on_retry_model, default=False)
            )

        items.append(pystray.Menu.SEPARATOR)

        # 退出
        items.append(
            pystray.MenuItem("❌ 退出", self._on_quit_clicked, default=False)
        )

        return pystray.Menu(*items)

    def _on_check_model(self, icon, item):
        """检查模型状态"""
        logger.info("用户点击：检查模型")

    def _on_retry_model(self, icon, item):
        """重试加载模型"""
        logger.info("用户点击：重试加载模型")
        # on_start 实际上是 on_tray_start_stop，这里需要直接调用引擎
        # 通过 on_stop 回调间接触发不太合适，留空让用户通过菜单操作
        if hasattr(self, '_on_retry') and self._on_retry:
            self._on_retry()

    def _on_quit_clicked(self, icon, item):
        """退出按钮回调"""
        logger.info("用户点击：退出")
        self._on_quit()

    def _on_switch_mode_clicked(self, icon, item):
        """模式切换回调"""
        logger.info("用户点击：切换模式")
        if self._on_switch_mode:
            self._on_switch_mode()

    def _on_double_click(self, icon, item):
        """双击托盘图标 → 打开设置"""
        logger.info("双击托盘图标，打开设置")
        self._on_settings()

    def set_state(self, state: EngineState):
        """切换图标和菜单状态（线程安全，可从任意线程调用）"""
        self._state = state
        label = _STATE_LABELS.get(state, str(state))
        logger.debug("托盘图标状态更新: %s", label)

        if self._icon is None:
            return

        try:
            self._icon.icon = self._get_icon_image(state)
            self._icon.title = f"语音输入工具 - {label}"
            self._icon.menu = self._build_menu()

            # macOS: pystray 需要显式更新
            try:
                self._icon.update_menu()
            except Exception:
                pass
        except Exception as e:
            logger.warning("更新托盘图标失败: %s", e)

    def show_notification(self, title: str, message: str):
        """托盘气泡通知"""
        logger.info("通知: %s - %s", title, message)
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception as e:
            logger.warning("托盘通知失败: %s", e)

    def set_mode(self, mode: str):
        """设置当前工作模式"""
        self._current_mode = mode
        logger.info("托盘模式切换: %s", mode)
        if self._icon is not None:
            try:
                self._icon.menu = self._build_menu()
                self._icon.update_menu()
            except Exception:
                pass

    def run(self):
        """启动托盘主循环（阻塞，在主线程调用）"""
        import pystray

        # 生成初始图标
        icon_image = self._get_icon_image(EngineState.LOADING)
        menu = self._build_menu()

        self._icon = pystray.Icon(
            name="语音输入工具",
            icon=icon_image,
            title="语音输入工具 - 加载中",
            menu=menu,
        )

        # 绑定双击事件
        self._icon.on_activate = self._on_double_click

        self._running = True
        logger.info("系统托盘启动")

        try:
            self._icon.run()
        except Exception as e:
            logger.error("系统托盘运行异常: %s", e)

    def stop(self):
        """停止托盘"""
        self._running = False
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as e:
                logger.warning("停止托盘图标失败: %s", e)
        logger.info("系统托盘已停止")
