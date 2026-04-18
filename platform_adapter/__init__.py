"""平台适配层

通过 Adapter Pattern 隔离平台差异，core 层零感知具体平台实现。
运行时根据 sys.platform 自动选择对应实现。
"""

import sys
import logging
import importlib

logger = logging.getLogger(__name__)

# ============================================================
# 平台检测
# ============================================================

if sys.platform == "win32":
    CURRENT_PLATFORM = "windows"
elif sys.platform == "darwin":
    CURRENT_PLATFORM = "macos"
else:
    CURRENT_PLATFORM = None

# ============================================================
# 注册表（延迟导入）
# ============================================================

_HOTKEY_REGISTRY = {
    "windows": ("platform_adapter.hotkey_windows", "WindowsHotkeyManager"),
    "macos": ("platform_adapter.hotkey_macos", "MacOSHotkeyManager"),
}

_CLIPBOARD_REGISTRY = {
    "windows": ("platform_adapter.clipboard_windows", "WindowsClipboardInjector"),
    "macos": ("platform_adapter.clipboard_macos", "MacOSClipboardInjector"),
}


def _create_from_registry(registry: dict, platform: str, *args, **kwargs):
    """从注册表延迟导入并实例化"""
    if platform not in registry:
        raise RuntimeError(f"不支持的平台: {sys.platform}")

    module_name, class_name = registry[platform]
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls(*args, **kwargs)


def create_hotkey_manager(config, on_start, on_stop, on_toggle=None):
    """创建当前平台的 HotkeyManager 实例"""
    return _create_from_registry(
        _HOTKEY_REGISTRY, CURRENT_PLATFORM,
        config, on_start, on_stop, on_toggle
    )


def create_clipboard_injector(config):
    """创建当前平台的 ClipboardInjector 实例"""
    return _create_from_registry(
        _CLIPBOARD_REGISTRY, CURRENT_PLATFORM,
        config
    )
