# 语音输入工具 — 跨平台适配设计文档

> **版本**: v4.0  
> **日期**: 2026-04-19  
> **状态**: 待评审  
> **关联**: 基于 voice-input-tool v3.0 设计文档  
> **目标**: 同时支持 Windows 10/11 和 macOS 12+

---

## 一、设计目标

### 1.1 核心原则

- **一套代码，双平台运行**：通过平台抽象层（adapter pattern）隔离平台差异
- **平台检测自动切换**：运行时自动选择对应平台的实现
- **无功能阉割**：两个平台的功能完全对等
- **独立打包**：Windows 和 macOS 各自打包，不互相包含对方依赖

### 1.2 非目标

- 不支持 Linux（后续可扩展）
- 不做跨平台 GUI 框架迁移（继续用 pystray）
- 不做通用安装包（pip install），保持 ZIP 解压即用

---

## 二、平台差异分析

### 2.1 完整差异矩阵

| 模块 | Windows 实现 | macOS 实现 | 差异级别 |
|------|-------------|-----------|---------|
| 全局热键 | `keyboard` (LowLevelKeyboardHook) | `pynput` (Accessibility API) | 🔴 高 |
| 剪贴板写入 | Win32 API (OpenClipboard/SetClipboardData) | `pbcopy` (subprocess) | 🟡 中 |
| 按键模拟 | Win32 SendInput (Ctrl+V) | `osascript` 模拟 Cmd+V | 🟡 中 |
| 单实例锁 | Win32 Named Mutex | `fcntl.flock` 文件锁 | 🟢 低 |
| 权限要求 | 管理员权限（keyboard 库） | 辅助功能权限（系统偏好设置） | 🟡 中 |
| 系统托盘 | pystray (Windows 托盘) | pystray (macOS 菜单栏) | 🟢 低* |
| 音频录制 | sounddevice (WASAPI) | sounddevice (CoreAudio) | ✅ 无 |
| 音频播放 | sounddevice | sounddevice | ✅ 无 |
| STT 引擎 | faster-whisper (CTranslate2) | faster-whisper (CTranslate2) | ✅ 无 |
| Web 配置 | http.server | http.server | ✅ 无 |
| 配置管理 | PyYAML + dataclass | PyYAML + dataclass | ✅ 无 |
| 状态机 | CoreEngine | CoreEngine | ✅ 无 |
| 提示音 | WAV + sounddevice | WAV + sounddevice | ✅ 无 |
| 日志系统 | logging + %APPDATA% | logging + ~/Library/Application Support/ | 🟢 低 |
| 打包 | Embeddable Python + zip | python-build-standalone + zip | 🟡 中 |

*pystray 在 macOS 上使用 AppKit（通过 pyobjc），图标显示在菜单栏而非系统托盘。行为略有差异但 API 一致，无需特殊处理。测试阶段需验证图标显示效果。

---

## 三、架构设计

### 3.1 平台抽象层

```
voice-input-tool/
├── core/
│   ├── engine.py              # 无改动
│   ├── recorder.py            # 无改动
│   ├── silence_detector.py    # 无改动
│   ├── stt_engine.py          # 无改动
│   ├── sound_player.py        # 无改动
│   ├── hotkey.py              # 改造：委托给 platform adapter
│   └── injector.py            # 改造：委托给 platform adapter
├── platform_adapter/              # 平台抽象层
│   ├── __init__.py            # 平台检测 + 注册表式工厂
│   ├── hotkey_base.py         # 热键抽象基类
│   ├── hotkey_windows.py      # Windows 实现 (keyboard 库)
│   ├── hotkey_macos.py        # macOS 实现 (pynput)
│   ├── clipboard_base.py      # 剪贴板抽象基类（含模板方法）
│   ├── clipboard_windows.py   # Windows 实现 (Win32 API)
│   └── clipboard_macos.py     # macOS 实现 (pbcopy + osascript)
```

### 3.2 平台检测与注册表式工厂

```python
# platform_adapter/__init__.py
import sys

# 模块加载时确定一次平台
CURRENT_PLATFORM = 'windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else None

if CURRENT_PLATFORM is None:
    raise RuntimeError(f"不支持的平台: {sys.platform}")

# 注册表：平台 → (模块名, 类名)
_HOTKEY_REGISTRY = {
    'windows': ('platform_adapter.hotkey_windows', 'WindowsHotkeyManager'),
    'macos': ('platform_adapter.hotkey_macos', 'MacOSHotkeyManager'),
}

_CLIPBOARD_REGISTRY = {
    'windows': ('platform_adapter.clipboard_windows', 'WindowsClipboardInjector'),
    'macos': ('platform_adapter.clipboard_macos', 'MacOSClipboardInjector'),
}

def _create_from_registry(registry: dict, config, *args, **kwargs):
    """通用工厂：从注册表动态导入并实例化"""
    module_name, class_name = registry[CURRENT_PLATFORM]
    import importlib
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls(config, *args, **kwargs)

def create_hotkey_manager(config, on_start, on_stop, on_toggle=None):
    """工厂函数：创建平台对应的热键管理器"""
    return _create_from_registry(
        _HOTKEY_REGISTRY, config, on_start, on_stop, on_toggle
    )

def create_clipboard_injector(config):
    """工厂函数：创建平台对应的剪贴板注入器"""
    return _create_from_registry(_CLIPBOARD_REGISTRY, config)

def get_log_directory() -> str:
    """返回平台对应的日志目录"""
    import os
    if CURRENT_PLATFORM == 'windows':
        base = os.getenv('APPDATA', os.path.expanduser('~'))
    else:
        base = os.path.expanduser('~/Library/Application Support')
    return os.path.join(base, 'voice-input-tool', 'logs')

def get_config_directory() -> str:
    """返回平台对应的配置目录"""
    import os
    if CURRENT_PLATFORM == 'windows':
        base = os.getenv('APPDATA', os.path.expanduser('~'))
    else:
        base = os.path.expanduser('~/Library/Application Support')
    return os.path.join(base, 'voice-input-tool')
```

---

## 四、模块详细设计

### 4.1 热键抽象接口 — `platform_adapter/hotkey_base.py`

```python
from abc import ABC, abstractmethod

class HotkeyManagerBase(ABC):
    """热键管理器抽象基类
    
    所有平台实现必须支持：
    - PTT 模式：按住触发键录音，松开停止
    - Toggle 模式：按一下开始，再按一下停止
    - 运行时热更换快捷键
    - 冲突检测
    """
    
    @abstractmethod
    def register(self):
        """注册全局热键"""
        pass
    
    @abstractmethod
    def unregister(self):
        """注销全局热键"""
        pass
    
    @abstractmethod
    def rebind(self, new_key: str):
        """运行时热更换快捷键"""
        pass
    
    @abstractmethod
    def check_conflicts(self) -> list[str]:
        """检测快捷键冲突，返回冲突描述列表"""
        pass
```

### 4.2 热键配置统一格式

**核心设计**：配置文件中的热键字符串使用统一格式，各平台实现自行解析映射。

```
统一格式（config.yaml 中）:
  "f8"           → 单键
  "ctrl+alt+v"   → 组合键（Ctrl/Cmd 自动适配平台）
  "f9"           → 功能键

平台映射规则:
  Windows: ctrl → Ctrl, alt → Alt
  macOS:   ctrl → Cmd (command), alt → Option (alt)
  
注意：macOS 上 Ctrl+xxx 通常被系统或终端占用，所以 "ctrl" 在 macOS 上映射为 Cmd 键
```

```python
# 各平台实现中提供的解析方法
def _parse_hotkey(self, key_str: str):
    """将统一格式的热键字符串解析为平台特定的键对象
    
    统一格式: 'f8', 'ctrl+alt+v', 'shift+f9'
    Windows → keyboard 库格式（不变）
    macOS → pynput Key/KeyCode 对象集合
    """
    pass
```

### 4.3 macOS 热键实现 — `platform_adapter/hotkey_macos.py`

```python
import threading
import time
import logging
from pynput import keyboard as pynput_keyboard
from platform_adapter.hotkey_base import HotkeyManagerBase

logger = logging.getLogger(__name__)

# 统一键名 → pynput Key 映射表
_KEY_MAP = {
    'f1': pynput_keyboard.Key.f1, 'f2': pynput_keyboard.Key.f2,
    # ... f3-f20
    'ctrl': pynput_keyboard.Key.cmd,      # macOS: ctrl 映射为 Cmd
    'alt': pynput_keyboard.Key.alt,
    'shift': pynput_keyboard.Key.shift,
    'cmd': pynput_keyboard.Key.cmd,
    'space': pynput_keyboard.Key.space,
    'tab': pynput_keyboard.Key.tab,
    'enter': pynput_keyboard.Key.enter,
    'esc': pynput_keyboard.Key.esc,
    # 字母键直接用 KeyCode.from_char()
}

class MacOSHotkeyManager(HotkeyManagerBase):
    """macOS 实现：使用 pynput 库
    
    macOS 特殊要求：
    - 需要在"系统偏好设置 → 安全性与隐私 → 隐私 → 辅助功能"中授权
    - 使用 AXIsProcessTrusted() 检测权限（比 osascript 更可靠）
    - 不需要管理员/root 权限
    """
    
    def __init__(self, config, on_start, on_stop, on_toggle=None):
        self._config = config
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_toggle = on_toggle
        self._lock = threading.Lock()  # 操作锁。获取顺序: _lock → _keys_lock，不可反向
        self._listener = None
        self._last_trigger_time = 0
        
        # 按键状态追踪（独立于防抖逻辑）
        self._keys_lock = threading.Lock()  # 保护 _current_keys
        self._current_keys = set()          # 当前按下的键（normalized 字符串）
        
        # 目标热键（解析后的键名集合）
        self._target_keys = set()
        
        # PTT 状态
        self._ptt_active = False       # PTT 模式专用
        self._recording_active = False   # Toggle 模式备用
        
        # Listener 健康监控
        self._watchdog_thread = None
        self._running = False
    
    def register(self):
        """注册全局热键监听"""
        self._check_accessibility()
        self._target_keys = self._parse_hotkey(self._config.trigger)
        
        self._running = True
        self._listener = pynput_keyboard.Listener(
            on_press=self._handle_press,
            on_release=self._handle_release,
            daemon=True
        )
        self._listener.start()
        
        # 启动 Listener 健康监控
        self._watchdog_thread = threading.Thread(
            target=self._listener_watchdog, daemon=True
        )
        self._watchdog_thread.start()
        
        logger.info(f"macOS 热键注册: {self._config.trigger}, 模式: {self._config.mode}")
    
    @staticmethod
    def _check_accessibility():
        """使用 AXIsProcessTrusted() 检测辅助功能权限"""
        try:
            import ctypes
            # 尝试通过 ctypes 调用 ApplicationServices 框架
            app_services = ctypes.cdll.LoadLibrary(
                '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices'
            )
            app_services.AXIsProcessTrusted.restype = bool
            if not app_services.AXIsProcessTrusted():
                MacOSHotkeyManager._prompt_accessibility()
                raise PermissionError(
                    "需要在 系统偏好设置 → 安全性与隐私 → 隐私 → 辅助功能 中\n"
                    f"授权 Python 或终端应用"
                )
        except PermissionError:
            raise
        except Exception:
            # AXIsProcessTrusted 不可用时，用 osascript 作为 fallback
            logger.warning("AXIsProcessTrusted 不可用，使用 osascript fallback")
            import subprocess
            try:
                result = subprocess.run(
                    ['osascript', '-e',
                     'tell application "System Events" to get every process'],
                    capture_output=True, timeout=5
                )
                if result.returncode != 0:
                    MacOSHotkeyManager._prompt_accessibility()
                    raise PermissionError("需要辅助功能权限")
            except subprocess.TimeoutExpired:
                logger.warning("辅助功能权限检测超时，继续运行")
    
    @staticmethod
    def _prompt_accessibility():
        """打开系统偏好设置的辅助功能面板"""
        import subprocess
        subprocess.Popen([
            'open',
            'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'
        ])
    
    def _parse_hotkey(self, key_str: str) -> set:
        """解析统一格式的热键字符串为键名集合"""
        parts = key_str.lower().replace(' ', '').split('+')
        return set(parts)
    
    def _normalize_key(self, key) -> str:
        """将 pynput Key/KeyCode 统一为字符串"""
        if isinstance(key, pynput_keyboard.Key):
            # f1-f20, space, tab, enter, cmd, alt, shift, ctrl 等
            name = key.name
            # pynput 用 cmd_l/cmd_r，统一为 cmd
            if name in ('cmd_l', 'cmd_r'):
                return 'cmd'
            if name in ('alt_l', 'alt_r'):
                return 'alt'
            if name in ('shift_l', 'shift_r'):
                return 'shift'
            if name in ('ctrl_l', 'ctrl_r'):
                return 'ctrl'
            return name
        elif isinstance(key, pynput_keyboard.KeyCode):
            if key.char:
                return key.char.lower()
            elif key.vk:
                # 功能键可能以 vk 形式出现
                return f'vk_{key.vk}'
        return str(key)
    
    def _handle_press(self, key):
        """按键按下回调
        
        关键设计：
        1. 始终更新 _current_keys（不受防抖影响）
        2. 防抖只影响是否触发回调
        3. Toggle 和 PTT 的状态管理完全分离
        """
        normalized = self._normalize_key(key)
        
        # 始终更新按键状态（防抖不影响状态追踪）
        with self._keys_lock:
            self._current_keys.add(normalized)
        
        # 检查是否匹配目标热键
        with self._keys_lock:
            matched = self._target_keys.issubset(self._current_keys)
        
        if not matched:
            return
        
        # 防抖：只影响回调触发
        now = time.monotonic()
        if now - self._last_trigger_time < 0.2:
            return
        self._last_trigger_time = now
        
        # 根据模式分发（Toggle 和 PTT 逻辑完全独立）
        if self._config.mode == 'toggle':
            # Toggle 模式：每次按下都调用 on_toggle
            if self._on_toggle:
                self._on_toggle()
            else:
                # 没有 on_toggle 时交替调用 start/stop
                # CoreEngine 状态机有非法转换忽略保护
                if not self._recording_active:
                    self._recording_active = True
                    self._on_start()
                else:
                    self._recording_active = False
                    self._on_stop()
        else:
            # PTT 模式：按下开始（仅首次触发）
            if not self._ptt_active:
                self._ptt_active = True
                self._recording_active = True
                self._on_start()
    
    def _handle_release(self, key):
        """按键释放回调"""
        normalized = self._normalize_key(key)
        
        # 始终更新按键状态
        with self._keys_lock:
            self._current_keys.discard(normalized)
        
        # PTT 模式：所有目标键释放时停止
        if self._config.mode == 'push_to_talk' and self._ptt_active:
            with self._keys_lock:
                still_pressed = self._target_keys.intersection(self._current_keys)
            if not still_pressed:
                self._ptt_active = False
                self._recording_active = False
                self._on_stop()
    
    def _listener_watchdog(self):
        """Listener 健康监控线程
        
        pynput Listener 在 macOS 12+ 上可能无故停止。
        此线程定期检查 Listener 存活状态，自动重连。
        
        重连流程:
        1. 显式 stop 旧 Listener（释放资源）
        2. 清空 _current_keys（避免残留按键状态）
        3. 重置 _ptt_active / _recording_active
        4. 创建新 Listener
        """
        while self._running:
            time.sleep(10)  # 每 10 秒检查一次
            if not self._running:
                break
            if self._listener and not self._listener.is_alive():
                logger.warning("pynput Listener 已停止，尝试重连...")
                try:
                    # 步骤 1: 显式停止旧 Listener
                    old_listener = self._listener
                    self._listener = None  # 防止并发访问
                    try:
                        old_listener.stop()
                    except Exception:
                        pass
                    
                    # 步骤 2: 清空按键状态
                    with self._keys_lock:
                        self._current_keys.clear()
                    
                    # 步骤 3: 重置录音状态
                    self._ptt_active = False
                    self._recording_active = False
                    
                    # 步骤 4: 创建新 Listener
                    self._listener = pynput_keyboard.Listener(
                        on_press=self._handle_press,
                        on_release=self._handle_release,
                        daemon=True
                    )
                    self._listener.start()
                    logger.info("pynput Listener 重连成功，状态已重置")
                except Exception as e:
                    logger.error(f"pynput Listener 重连失败: {e}")
    
    def unregister(self):
        self._running = False
        if self._listener:
            self._listener.stop()
            self._listener = None
    
    def rebind(self, new_key: str):
        with self._lock:
            self.unregister()
            self._config.trigger = new_key
            self._target_keys = self._parse_hotkey(new_key)
            self._ptt_active = False
            self._recording_active = False
            with self._keys_lock:
                self._current_keys.clear()
            self.register()
    
    def check_conflicts(self) -> list[str]:
        conflicts = []
        # macOS 系统快捷键
        system_keys = {
            'cmd+space', 'cmd+tab', 'cmd+q', 'ctrl+cmd+q',
            'cmd+shift+3', 'cmd+shift+4', 'cmd+shift+5'
        }
        trigger = self._config.trigger.lower().replace(' ', '')
        # 将统一格式的 ctrl 映射为 macOS 的 cmd
        mac_trigger = trigger.replace('ctrl', 'cmd')
        if mac_trigger in system_keys:
            conflicts.append(f"{self._config.trigger} 与 macOS 系统快捷键冲突")
        return conflicts
```

### 4.4 剪贴板抽象基类 — `platform_adapter/clipboard_base.py`

```python
from abc import ABC, abstractmethod
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class ClipboardInjectorBase(ABC):
    """剪贴板注入器抽象基类
    
    使用模板方法模式：inject() 定义跨平台共享的完整流程，
    子类只需实现 read/write/simulate_paste 三个原子操作。
    """
    
    def __init__(self, config):
        self._config = config
        self._backup = None
    
    @abstractmethod
    def read_clipboard(self) -> Optional[str]:
        """读取剪贴板文本内容，非文本返回 None"""
        pass
    
    @abstractmethod
    def write_clipboard(self, text: str) -> bool:
        """写入文本到剪贴板"""
        pass
    
    @abstractmethod
    def simulate_paste(self) -> bool:
        """模拟粘贴操作（Ctrl+V / Cmd+V）"""
        pass
    
    def inject(self, text: str) -> bool:
        """完整注入流程（模板方法）
        
        流程: 备份 → 写入 → 延时 → 粘贴 → 恢复
        """
        import time
        
        # 空文本短路处理
        if not text or not text.strip():
            logger.debug("空文本，跳过注入")
            return True
        
        try:
            # 1. 备份剪贴板
            if self._config.clipboard_backup:
                self._backup = self.read_clipboard()
            
            # 2. 写入文本
            if not self.write_clipboard(text):
                raise RuntimeError("剪贴板写入失败")
            
            # 3. 等待延时
            time.sleep(self._config.paste_delay_ms / 1000)
            
            # 4. 模拟粘贴
            if not self.simulate_paste():
                # 粘贴失败，文字仍在剪贴板
                logger.warning("模拟粘贴失败，文字已保留在剪贴板")
                return True  # 文字可用，用户可手动粘贴
            
            # 5. 恢复剪贴板
            PASTE_SETTLE_DELAY = 0.2
            if self._config.clipboard_restore and self._backup is not None:
                time.sleep(PASTE_SETTLE_DELAY)
                self.write_clipboard(self._backup)
            
            return True
        except Exception as e:
            logger.error(f"注入失败: {e}")
            # 保证文字至少在剪贴板中
            try:
                self.write_clipboard(text)
            except Exception:
                pass
            return False
```

### 4.5 Windows 剪贴板实现 — `platform_adapter/clipboard_windows.py`

```python
class WindowsClipboardInjector(ClipboardInjectorBase):
    """Windows 实现：Win32 API"""
    # 迁移当前 core/injector.py 中的完整 Win32 实现
    # read_clipboard → OpenClipboard + GetClipboardData(CF_UNICODETEXT)
    # write_clipboard → OpenClipboard + EmptyClipboard + SetClipboardData
    # simulate_paste → SendInput(Ctrl+V)，失败时通知用户手动粘贴
    pass
```

### 4.6 macOS 剪贴板实现 — `platform_adapter/clipboard_macos.py`

```python
import subprocess
import logging
from platform_adapter.clipboard_base import ClipboardInjectorBase
from typing import Optional

logger = logging.getLogger(__name__)

class MacOSClipboardInjector(ClipboardInjectorBase):
    """macOS 实现：pbcopy + osascript"""
    
    def read_clipboard(self) -> Optional[str]:
        """使用 pbpaste 读取剪贴板"""
        try:
            result = subprocess.run(
                ['pbpaste', '-Prefer', 'txt'],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
            return None
        except Exception:
            return None
    
    def write_clipboard(self, text: str) -> bool:
        """使用 pbcopy 写入剪贴板"""
        try:
            process = subprocess.Popen(
                ['pbcopy'],
                stdin=subprocess.PIPE
            )
            process.communicate(text.encode('utf-8'), timeout=2)
            return process.returncode == 0
        except Exception as e:
            logger.error(f"pbcopy 失败: {e}")
            return False
    
    def simulate_paste(self) -> bool:
        """使用 osascript 模拟 Cmd+V
        
        回退链: osascript → pyautogui（如果已安装）→ 返回 False
        """
        # 方案 1: osascript
        try:
            result = subprocess.run(
                ['osascript', '-e',
                 'tell application "System Events" to keystroke "v" using command down'],
                capture_output=True, timeout=3
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
        
        # 方案 2: pyautogui（可选依赖，可能未安装）
        try:
            import pyautogui
            pyautogui.hotkey('command', 'v')
            return True
        except ImportError:
            logger.debug("pyautogui 未安装，跳过回退方案")
        except Exception as e:
            logger.warning(f"pyautogui 粘贴失败: {e}")
        
        # 所有方案都失败
        logger.warning("模拟粘贴失败，请手动 Cmd+V")
        return False
```

### 4.7 现有模块改造

#### core/hotkey.py → 委托模式

```python
import logging
from platform_adapter import create_hotkey_manager

logger = logging.getLogger(__name__)

class HotkeyManager:
    """热键管理器（平台委托薄封装）"""
    
    def __init__(self, config, on_start, on_stop, on_toggle=None):
        self._impl = create_hotkey_manager(config, on_start, on_stop, on_toggle)
    
    def register(self):
        self._impl.register()
    
    def unregister(self):
        self._impl.unregister()
    
    def rebind(self, new_key: str):
        self._impl.rebind(new_key)
    
    def check_conflicts(self) -> list[str]:
        return self._impl.check_conflicts()
```

#### core/injector.py → 委托模式

```python
from platform_adapter import create_clipboard_injector

class TextInjector:
    """文字注入器（平台委托薄封装）"""
    
    def __init__(self, config):
        self._impl = create_clipboard_injector(config)
        self._config = config
    
    def inject(self, text: str) -> bool:
        return self._impl.inject(text)
```

#### main.py — 平台适配改造

```python
# 改动点：
# 1. 单实例锁：跨平台实现
# 2. 日志路径：使用 platform.get_log_directory()
# 3. 权限提示：按平台区分

import platform_adapter

def _acquire_single_instance_lock():
    """跨平台单实例锁"""
    import sys
    if sys.platform == 'win32':
        # Windows: Named Mutex（保持现有逻辑不变）
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, "Global\\VoiceInputTool_SingleInstance")
        if ctypes.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            logger.error("已有实例运行")
            sys.exit(1)
        return mutex
    else:
        # macOS: fcntl.flock 文件锁
        import fcntl
        config_dir = platform_adapter.get_config_directory()
        os.makedirs(config_dir, exist_ok=True)
        lock_path = os.path.join(config_dir, '.lock')
        global _lock_fd
        _lock_fd = open(lock_path, 'w')
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return _lock_fd
        except IOError:
            logger.error("已有实例运行")
            sys.exit(1)
```

---

## 五、依赖差异

### 5.1 共通依赖 — `requirements_base.txt`

```
sounddevice>=0.4.6
numpy>=1.24.0
faster-whisper>=1.0.0
pystray>=0.19.0
Pillow>=10.0.0
PyYAML>=6.0
```

### 5.2 Windows 专用 — `requirements_windows.txt`

```
-r requirements_base.txt
keyboard>=0.13.5
```

### 5.3 macOS 专用 — `requirements_macos.txt`

```
-r requirements_base.txt
pynput>=1.7.6
pyobjc-core>=10.0        # pystray macOS 后端依赖
pyobjc-framework-Quartz>=10.0  # pystray macOS 后端依赖
pyautogui>=0.9.54        # 可选：粘贴模拟回退方案
```

### 5.4 统一 requirements.txt（开发用）

```python
# 由 setup 脚本自动生成或手动维护
# = base + 当前平台
import sys
system = 'windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else 'linux'
# Windows: base + keyboard
# macOS: base + pynput + pyobjc + pyautogui
```

### 5.5 Apple Silicon 兼容性

faster-whisper + CTranslate2 在 Apple Silicon (M1-M4) 上：
- ✅ ARM64 原生支持（通过 Universal2 wheel 或 ARM64-only wheel）
- ✅ `compute_type=int8` 在 ARM 上可用（CTranslate2 4.x 支持）
- 性能：M4 芯片上 small 模型识别速度约 6-10x 实时，优于大多数 x86 CPU

---

## 六、打包方案

### 6.1 策略：独立打包，各自精简

```
voice-input-tool-v1.0-windows.zip    (~600MB)
└── python/        → Embeddable Python 3.11 (Windows, 官方提供)
    + keyboard     → Windows 专用
    + run.bat      → UAC 提权启动

voice-input-tool-v1.0-macos.zip      (~550MB)
└── python/        → python-build-standalone (第三方构建)
    + pynput       → macOS 专用
    + run.command  → chmod +x 启动
```

### 6.2 macOS Embeddable Python 方案

macOS 没有官方 Embeddable Python，推荐使用 **python-build-standalone** 项目：

```bash
# 从 python-build-standalone 发布页下载
# https://github.com/indygreg/python-build-standalone/releases
# 选择: cpython-3.11.*-macos-universal2-install_only.tar.gz

# 解压后得到完整的 Python 安装（无需系统 Python）
# 包含 pip，可直接安装依赖
# Universal2 二进制同时支持 Intel 和 Apple Silicon
```

目录结构：
```
python/
├── bin/python3          → Python 解释器
├── lib/python3.11/      → 标准库
└── lib/python3.11/site-packages/  → 依赖
```

### 6.3 macOS 启动脚本 — `scripts/run.command`

```bash
#!/bin/bash
cd "$(dirname "$0")"

# 检测辅助功能权限
./python/bin/python3 -c "
from platform_adapter.hotkey_macos import MacOSHotkeyManager
try:
    MacOSHotkeyManager._check_accessibility()
except PermissionError as e:
    print(f'⚠️  {e}')
    exit(1)
" || { echo "按回车退出..."; read; exit 1; }

exec ./python/bin/python3 main.py
```

### 6.4 macOS 环境准备 — `scripts/setup_macos.sh`

```bash
#!/bin/bash
set -e
echo "=== 语音输入工具 - macOS 环境准备 ==="

PYTHON="./python/bin/python3"

# 1. 检测 Python
if [ ! -f "$PYTHON" ]; then
    echo "❌ 未找到内置 Python，请确认 python/ 目录存在"
    exit 1
fi
$PYTHON --version

# 2. 安装依赖
$PYTHON -m pip install --upgrade pip
$PYTHON -m pip install -r requirements_macos.txt

# 3. 检查模型
if [ ! -d "models/small" ] || [ -z "$(ls models/small/)" ]; then
    echo "模型文件不存在，运行下载脚本..."
    $PYTHON scripts/download_model.py
fi

# 4. 辅助功能权限提示
echo ""
echo "⚠️  首次使用请授权辅助功能权限:"
echo "   系统偏好设置 → 安全性与隐私 → 隐私 → 辅助功能"
echo "   添加终端或 Python 到允许列表"
echo ""
echo "=== 环境准备完成 ==="
echo "运行 ./run.command 或 ./python/bin/python3 main.py 启动工具"
```

---

## 七、线程模型（macOS 差异）

与 Windows 版完全一致，唯一区别：

| 线程 | Windows | macOS |
|------|---------|-------|
| 热键线程 | keyboard hook 线程 | pynput Listener.daemon 线程 |
| **新增** | — | Listener 健康监控线程（每10s检查+自动重连） |

Listener 监控线程是 macOS 专属的新增线程，解决 pynput Listener 在 macOS 12+ 上的稳定性问题。

---

## 八、项目目录结构（跨平台版）

```
voice-input-tool/
├── main.py                          # 改造：平台适配
├── config.py                        # 无改动
├── config.yaml                      # 无改动
│
├── platform_adapter/                # 平台抽象层
│   ├── __init__.py                  # 注册表式工厂 + 平台工具函数
│   ├── hotkey_base.py               # 热键抽象基类
│   ├── hotkey_windows.py            # Windows: keyboard 库
│   ├── hotkey_macos.py              # macOS: pynput + watchdog
│   ├── clipboard_base.py            # 剪贴板基类（模板方法）
│   ├── clipboard_windows.py         # Windows: Win32 API
│   └── clipboard_macos.py           # macOS: pbcopy + osascript
│
├── core/
│   ├── engine.py                    # 无改动
│   ├── hotkey.py                    # 改造：委托薄封装
│   ├── recorder.py                  # 无改动
│   ├── silence_detector.py          # 无改动
│   ├── stt_engine.py                # 无改动
│   ├── injector.py                  # 改造：委托薄封装
│   └── sound_player.py              # 无改动
│
├── gui/                             # 无改动
├── assets/                          # 无改动
├── models/                          # 无改动
├── scripts/
│   ├── run.bat                      # Windows 启动
│   ├── run.command                  # macOS 启动
│   ├── setup.bat                    # Windows 环境准备
│   ├── setup_macos.sh               # macOS 环境准备
│   └── download_model.py            # 无改动
│
├── requirements_base.txt            # 共通依赖
├── requirements_windows.txt         # Windows 专用
├── requirements_macos.txt           # macOS 专用
├── requirements.txt                 # 兼容（= base + 当前平台）
└── README.md                        # 更新：双平台说明
```

---

## 九、已知风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| macOS 辅助功能权限未授权 | 全局热键无法注册 | AXIsProcessTrusted() 检测 + 启动引导 + README |
| pynput Listener 无故断开 | 热键失效 | Listener watchdog 线程自动重连 |
| osascript 在沙盒应用中失败 | 粘贴失败 | pyautogui 回退 + 保留剪贴板手动粘贴 |
| macOS 休眠后热键失效 | 恢复后无法触发 | watchdog 检测 + 重连 |
| pbcopy 对特殊字符处理 | 写入异常 | UTF-8 编码 + 异常兜底 |
| pystray macOS 菜单栏行为差异 | 图标显示不一致 | 测试阶段验证 |
| Apple Silicon CTranslate2 兼容 | STT 不可用 | python-build-standalone Universal2 包含 ARM 支持 |
| pyautogui 未安装 | 粘贴回退不可用 | ImportError 安全处理，不崩溃 |

---

## 十、实施优先级

### P0 — 平台抽象层 + 核心适配
1. 创建 platform/ 目录和注册表式工厂
2. 实现 clipboard_base.py（模板方法含空文本短路）
3. 迁移 Windows 实现（从 core/ 提取到 platform/）
4. 实现 macOS hotkey (pynput + watchdog + 按键状态追踪)
5. 实现 macOS clipboard (pbcopy + osascript + pyautogui 回退)

### P1 — 模块改造
6. core/hotkey.py → 委托薄封装
7. core/injector.py → 委托薄封装
8. main.py 平台适配（单实例锁、日志路径、权限提示）

### P2 — macOS 本地测试
9. 启动测试
10. 热键 PTT/Toggle 测试
11. 录音→识别→注入端到端测试
12. 托盘和 Web 配置页测试
13. 休眠恢复测试

### P3 — macOS 打包
14. python-build-standalone 集成
15. run.command + setup_macos.sh
16. requirements 分拆
17. README 更新

---

## 十一、实时转写模式（功能扩展）

### 11.1 功能概述

工具支持两种工作模式：

| 模式 | 代号 | 工作方式 | 典型场景 |
|------|------|---------|---------|
| **批量模式** | `batch` | 录音 → 整段转写 → 一次注入 | 语音输入（原有功能） |
| **实时转写模式** | `realtime` | 持续监听 → 分段转写 → 逐段追加 | 会议记录、访谈、课堂笔记 |

两种模式共享同一个全局热键触发机制，通过配置或托盘菜单切换。

### 11.2 实时转写工作流

```
用户按热键开始
       ↓
┌─────────────────────────────────────────────────┐
│  音频持续采集（sounddevice callback，复用 Recorder）  │
│       ↓                                         │
│  滚动缓冲区管理（Ring Buffer，保留最近 N 秒音频）      │
│       ↓                                         │
│  VAD 语音活动检测（webrtcvad，每次判断 < 1ms）       │
│       ↓                                         │
│  检测到"句子边界"（停顿 > segment_pause_threshold）   │
│       ↓                                         │
│  该段音频送入 faster-whisper 转写（~0.5-2s）         │
│       ↓                                         │
│  文字注入到光标位置 + 追加换行                       │
│       ↓                                         │
│  继续监听下一段...（循环直到用户停止）                │
└─────────────────────────────────────────────────┘
用户再按热键停止
```

### 11.3 核心组件设计

#### 11.3.1 新增模块 — `core/stream_transcriber.py`

```python
class StreamTranscriber:
    """实时转写引擎
    
    职责：
    - 从 AudioRecorder 的 buffer_queue 持续消费音频
    - 使用 webrtcvad 检测语音段边界
    - 将分段音频送入 STT Engine 转写
    - 将转写结果通过 callback 输出
    
    与批量模式的区别：
    - 批量模式：CoreEngine 手动触发 start/stop，一次性转写
    - 实时模式：StreamTranscriber 自动循环分段，持续输出
    """
    
    def __init__(self, config, stt_engine, on_segment_transcribed):
        """
        config: RealtimeConfig
        stt_engine: STTEngine 实例（共享）
        on_segment_transcribed: callback(text: str)
            每转写完一段文字就调用，由 CoreEngine 负责注入
        """
        self._config = config
        self._stt_engine = stt_engine
        self._on_segment = on_segment_transcribed
        self._running = False
        self._thread = None
        self._inject_thread = None
        self._inject_queue = queue.Queue(maxsize=10)  # 注入解耦队列
        
        # VAD
        self._vad = None  # webrtcvad 实例
        
        # 音频缓冲
        self._speech_buffer = []  # 当前语音段
        self._silence_start = None  # 当前静音开始时间
        self._speech_duration = 0.0  # 当前语音段时长
        
        # 统计
        self._segments_count = 0
    
    def start(self, audio_queue: queue.Queue):
        """启动实时转写
        
        audio_queue: Recorder 的实时模式专用队列
        （IDLE->STREAMING 时创建，与批量模式 buffer_queue 分离，
         SilenceDetector 不消费此队列，避免竞态）
        """
        self._load_vad()
        self._running = True
        self._audio_queue = audio_queue
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # 启动独立注入线程（避免阻塞转写主循环）
        self._inject_thread = threading.Thread(target=self._inject_worker, daemon=True)
        self._inject_thread.start()
    
    def stop(self):
        """停止实时转写"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        # 处理缓冲区中剩余的语音
        if self._speech_buffer:
            self._flush_speech_buffer()
        # 等待注入队列清空
        if self._inject_thread:
            self._inject_queue.put(None)  # sentinel
            self._inject_thread.join(timeout=3)
    
    def _inject_worker(self):
        """独立注入线程，避免阻塞转写主循环"""
        while True:
            text = self._inject_queue.get()
            if text is None:
                break
            try:
                self._on_segment(text)
            except Exception as e:
                logger.error(f"注入失败: {e}")
    
    def _load_vad(self):
        """加载 VAD 模型（webrtcvad，~1MB，不依赖 torch）"""
        import webrtcvad
        self._vad = webrtcvad.Vad()
        self._vad.set_mode(self._config.vad_sensitivity)  # 0-3，越高越严格
        logger.info(f"webrtcvad 加载完成，灵敏度: {self._config.vad_sensitivity}")
    
    def _run(self):
        """实时转写主循环"""
        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            
            # VAD 判断
            is_speech = self._vad_detect(chunk)
            
            if is_speech:
                self._speech_buffer.append(chunk)
                self._speech_duration += len(chunk) / 16000
                self._silence_start = None
                
                # 超长段强制切分
                if self._speech_duration >= self._config.max_segment_duration:
                    logger.debug(f"语音段达 {self._speech_duration:.1f}s，强制切分")
                    self._flush_speech_buffer()
            else:
                if self._speech_buffer:
                    # 语音段中的静音
                    if self._silence_start is None:
                        self._silence_start = time.monotonic()
                    else:
                        silence_duration = time.monotonic() - self._silence_start
                        if silence_duration >= self._config.segment_pause_threshold:
                            # 句子边界，提交转写
                            self._flush_speech_buffer()
    
    def _vad_detect(self, audio_chunk: np.ndarray) -> bool:
        """使用 webrtcvad 判断是否为语音"""
        # webrtcvad 要求 16bit PCM，帧长必须是 10/20/30ms
        # 16kHz * 30ms = 480 samples
        frame_length = int(self._config.vad_window_ms * 16)  # 480
        if len(audio_chunk) < frame_length:
            return False
        # 取最后一帧判断
        frame = (audio_chunk[-frame_length:] * 32767).astype(np.int16).tobytes()
        try:
            return self._vad.is_speech(frame, 16000)
        except Exception:
            return False
    
    def _flush_speech_buffer(self):
        """将当前语音段送入 STT 转写并输出"""
        if not self._speech_buffer:
            return
        
        audio = np.concatenate(list(self._speech_buffer))
        self._speech_buffer = []
        self._speech_duration = 0.0
        self._silence_start = None
        
        # 超短段跳过（<0.3s）
        if len(audio) < 4800:  # 0.3s @ 16kHz
            return
        
        # 同步转写（在当前线程中）
        # 注意：不用 transcribe_async，因为实时模式需要顺序输出
        try:
            # transcribe_sync: 阻塞式转写，保证顺序输出
            # （区别于 transcribe_async 的线程池异步模式）
            text = self._stt_engine.transcribe_sync(audio)
            if text and text.strip():
                self._segments_count += 1
                # 通过独立队列解耦注入，避免阻塞转写线程
                try:
                    self._inject_queue.put_nowait(text)
                except queue.Full:
                    logger.warning("注入队列已满，丢弃段落")
        except Exception as e:
            logger.error(f"实时转写失败: {e}")
```

#### 11.3.2 新增配置 — `RealtimeConfig`

```python
@dataclass
class RealtimeConfig:
    """实时转写模式配置"""
    # 语音段分割
    segment_pause_threshold: float = 0.8   # 静音多久视为句子边界（秒）
    min_segment_duration: float = 0.3     # 最短语音段（秒），低于此跳过
    max_segment_duration: float = 30.0    # 最长语音段（秒），超长强制切分
    
    # VAD 参数
    vad_sensitivity: int = 2               # webrtcvad 灵敏度（0-3，越高越严格）
    vad_window_ms: int = 30                # VAD 窗口（毫秒，webrtcvad 要求 10/20/30）
    
    # 输出格式
    segment_separator: str = "\n"         # 段落分隔符（\n=换行，" "=空格，""=无分隔）
    auto_timestamp: bool = False          # 是否在每段前加时间戳
    timestamp_format: str = "[HH:MM:SS]" # 时间戳格式
```

#### 11.3.3 配置文件新增

```yaml
# === 工作模式 ===
mode:
  active: "batch"                  # batch | realtime
  
# === 实时转写（仅 realtime 模式生效） ===
realtime:
  segment_pause_threshold: 0.8
  min_segment_duration: 0.3
  max_segment_duration: 30.0
  vad_sensitivity: 2
  segment_separator: "\n"
  auto_timestamp: false
```

### 11.4 CoreEngine 状态机扩展

#### 新增状态

```python
class EngineState(Enum):
    IDLE = auto()
    RECORDING = auto()          # 批量模式录音中
    PROCESSING = auto()         # STT 识别中（批量模式）
    STREAMING = auto()          # 🆕 实时转写监听中
    INJECTING = auto()          # 文字注入中
    ERROR = auto()
    LOADING = auto()
```

#### 扩展状态转移矩阵

| 当前状态 | 事件 | 目标状态 | 动作 |
|---------|------|---------|------|
| IDLE | 热键触发（batch 模式） | RECORDING | 原有批量模式逻辑 |
| IDLE | 热键触发（realtime 模式） | STREAMING | 启动 StreamTranscriber |
| STREAMING | 热键再按 | IDLE | 停止 StreamTranscriber |
| STREAMING | 段落转写完成 | STREAMING | 注入文字（保持 STREAMING） |
| STREAMING | 段落注入完成 | STREAMING | 继续监听 |
| STREAMING | STT 异常 | STREAMING | 日志记录，继续监听 |
| STREAMING | shutdown | IDLE | 停止 StreamTranscriber |

**关键设计**：STREAMING 状态下，段落转写和注入是**内部循环**，不触发状态转换。只有用户主动停止或 shutdown 才退出 STREAMING。

### 11.5 线程模型扩展

```
实时模式新增线程:
┌─────────────────┐  StreamTranscriber 主线程
│  Audio Consumer │  - 从 buffer_queue 消费音频
│  + VAD          │  - webrtcvad 检测语音边界
│  + STT 调用     │  - 同步调用 faster-whisper（在当前线程）
│  + callback     │  - 通知 CoreEngine 注入
└─────────────────┘
```

**注意**：实时模式的 STT 调用是同步的（非线程池），因为需要按顺序输出段落。如果用异步，后转写的段落可能先完成，导致文字顺序错乱。

### 11.6 注入策略差异

| 场景 | 批量模式 | 实时模式 |
|------|---------|---------|
| 注入方式 | 覆盖剪贴板 + Ctrl/Cmd+V | 仅写入新段落 + Ctrl/Cmd+V |
| 分隔符 | 无 | 配置（换行/空格） |
| 剪贴板恢复 | 可选恢复原内容 | 每次注入后恢复原内容 |
| 注入后光标位置 | 停留在文字末尾 | 停留在文字末尾（下段紧接） |

#### 实时模式注入流程（重新设计）

**核心原则：不读取、不修改目标窗口现有内容。仅追加新文字。**

```
流程（每次段落转写完成后）：
1. 备份当前剪贴板内容
2. 将新段落文本写入剪贴板（仅此段落，不追加历史）
3. 模拟 Ctrl/Cmd+V（粘贴到光标位置）
4. 等待粘贴完成（200ms）
5. 恢复原始剪贴板内容
```

**关键设计**：
- 假设光标始终在文档末尾（用户不在转写期间编辑中间内容）
- 每次只粘贴最新段落，不累积历史段落
- 粘贴后立即恢复剪贴板，占用时间约 300ms，用户在此窗口内复制操作会丢失但概率极低

#### 注入方式配置

实时模式提供三种注入方式：

| 方式 | 说明 | 优点 | 缺点 |
|------|------|------|------|
| `clipboard`（默认） | 剪贴板 + 恢复原内容 | 兼容性好，剪贴板占用短 | 300ms 占用窗口 |
| `clipboard_no_restore` | 剪贴板但不恢复 | 最快 | 剪贴板被占用 |
| `direct_type` | 辅助功能 API 直接输入 | 不占剪贴板 | 速度慢，特殊字符处理复杂 |

### 11.7 依赖变更

```
# requirements_base.txt 新增:
webrtcvad>=2.0.10        # 轻量级 VAD（~1MB）
```

**VAD 方案选择**：

| 方案 | 体积 | 精度 | 说明 |
|------|------|------|------|
| **webrtcvad** | ~1MB | 中 | ✅ 粗粒度分段，检测句子边界 |
| **faster-whisper 内置 VAD** | 0（已包含） | 高 | ✅ 细粒度过滤，段内静音去除 |
| webrtcvad（torch） | ~200MB | 高 | ❌ 不采用，体积过大 |

**最终方案（双层 VAD）**：
- **粗切分**：webrtcvad 检测句子边界（静音 > 0.8s）
- **精过滤**：faster-whisper `vad_filter=True` 去除段内静音
- **打包体积仅增加 ~1MB**

### 11.8 UI 扩展

#### 托盘菜单新增
```
├── 🎤 开始录音 / ⏹ 停止录音    （batch 模式）
├── 📝 开始转写 / ⏹ 停止转写    （realtime 模式）🆕
├── 🔀 切换模式: 批量/实时       🆕
├── ───────────
├── ⚙ 打开设置
└── ❌ 退出
```

#### Web 配置页新增
- 模式切换标签
- 实时转写参数配置（段落阈值、VAD 灵敏度、分隔符、时间戳）

### 11.9 已知限制

| 限制 | 说明 |
|------|------|
| 延迟 1-3 秒 | 非真正的流式识别，是分段转写，存在不可避免的延迟 |
| 分段准确性 | VAD 分段可能把一句话拆成两段或合并多句 |
| 剪贴板冲突 | 实时转写期间短暂占用剪贴板（~300ms/次），用户不宜同时复制粘贴 |
| 识别精度 | 短段落（<1s）的识别精度低于长段落 |
| 非字词级同步 | 是句子级同步，不是每个字实时显示 |

### 11.10 配置热更新

| 配置项 | 实时生效 | 说明 |
|--------|---------|------|
| segment_pause_threshold | ✅ | 下次分段生效 |
| vad_sensitivity | ✅ | 下次 VAD 判断生效 |
| segment_separator | ✅ | 下次注入生效 |
| max_segment_duration | ✅ | 下次强制切分生效 |
| min_segment_duration | ✅ | 下次跳过判断生效 |
| vad_window_ms | ✅ | 下次 VAD 判断生效 |
| mode 切换 | ⚠️ | 需停止当前录音/转写后切换 |
| auto_timestamp | ✅ | 下次注入生效 |
---

*文档结束。版本 v4.0 — 实时转写模式 v2（修复剪贴板方案 + VAD 方案）。*
