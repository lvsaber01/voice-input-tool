# 语音输入工具 — 详细设计文档

> **版本**: v3.0  
> **日期**: 2026-04-18  
> **状态**: 待评审  
> **目标平台**: Windows 10/11（需管理员权限）  
> **项目位置**: `~/PROJECT/voice-input-tool/`

---

## 一、项目概述

### 1.1 目标

构建一个 Windows 平台的语音输入工具，用户通过全局快捷键触发录音，语音经本地 STT 引擎识别后，文字自动注入到当前光标位置。面向 vibe-coding 场景，支持中英文混合自动检测。

### 1.2 核心特性

- **全局热键触发**：支持 PTT（按住说话）和 Toggle（开关切换）两种模式
- **本地离线识别**：faster-whisper + CTranslate2，无需联网
- **中英混合自动检测**：language=auto，自动识别语言
- **解压即用**：ZIP 打包，含 embeddable Python + 依赖 + 模型
- **浏览器配置界面**：本地 HTTP 服务 + HTML 配置页
- **系统托盘运行**：最小化到托盘，不占任务栏

### 1.3 非目标

- 不支持 macOS / Linux（keyboard 库 Windows 专属）
- 不支持实时流式识别（整段录音后一次性识别）
- 不支持自定义语音模型训练

### 1.4 系统要求

| 要求 | 说明 |
|------|------|
| 操作系统 | Windows 10 1903+ / Windows 11 |
| **权限** | **管理员权限**（keyboard 库全局钩子需要 LowLevelKeyboardHook） |
| 内存 | ≥2GB 可用（small 模型运行需要） |
| 麦克风 | 系统已识别的录音设备 |
| 运行时 | VC++ 2019+ Runtime（CTranslate2 依赖） |

---

## 二、技术栈

| 组件 | 技术选型 | 版本 | 说明 |
|------|---------|------|------|
| Python 运行时 | CPython Embeddable | 3.11.x | Windows 免安装版，打包进 ZIP |
| 全局热键 | `keyboard` | >=0.13.12 | Windows 原生全局钩子，**需管理员权限** |
| 音频采集+播放 | `sounddevice` + `numpy` | - | WASAPI 后端，录音+提示音播放统一方案 |
| STT 引擎 | `faster-whisper` | >=1.0 | CTranslate2 后端，比原版快 4x |
| STT 模型 | Whisper `small` | - | ~500MB，中英混合效果好 |
| 系统托盘 | `pystray` + `Pillow` | - | 托盘图标 + 右键菜单 |
| 配置界面 | `http.server` + HTML/JS | - | 轻量，无 Flask 依赖 |
| 剪贴板操作 | `ctypes` (Win32 API) | - | 直接调用 OpenClipboard/SetClipboardData，消除竞态 |
| 按键模拟 | `ctypes` (Win32 SendInput) | - | 模拟 Ctrl+V |
| 配置管理 | YAML (`PyYAML`) + dataclass | - | 类型安全 + 人类可读 |
| 日志系统 | Python `logging` | - | 内置模块，按天轮转 |

---

## 三、系统架构

### 3.1 整体架构

```
┌─────────────────────────────────────────┐
│          System Tray (pystray)           │
│     图标状态切换 + 右键菜单控制           │
└──────────────┬──────────────────────────┘
               │ 通知状态变更
┌──────────────▼──────────────────────────┐
│         CoreEngine (状态机)              │
│                                         │
│  ┌────────────┐  ┌───────────────────┐ │
│  │ HotkeyMgr  │  │   AudioRecorder   │ │
│  │ (keyboard) │  │  (sounddevice)    │ │
│  └─────┬──────┘  └──────┬────────────┘ │
│        │                │              │
│        │  ┌─────────────▼──────────┐   │
│        │  │  SilenceDetector       │   │
│        │  │  (独立消费者线程)       │   │
│        │  └─────────────┬──────────┘   │
│        │                │              │
│        │  ┌─────────────▼──────────┐   │
│        │  │  STT Engine            │   │
│        │  │  (faster-whisper       │   │
│        │  │   线程池异步调用)      │   │
│        │  └─────────────┬──────────┘   │
│        │                │              │
│        │  ┌─────────────▼──────────┐   │
│        │  │  TextInjector          │   │
│        │  │  (Win32 Clipboard API) │   │
│        │  └────────────────────────┘   │
└─────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│     Config Web UI (http.server)          │
│      http://localhost:{port}             │
│      (带 token 校验)                     │
└─────────────────────────────────────────┘
```

### 3.2 线程模型

这是多线程桌面应用，线程模型如下：

```
┌──────────────────────────────────────────────────────────┐
│                     进程空间                              │
│                                                          │
│  ┌─────────────┐  主线程（pystray 事件循环，阻塞）        │
│  │   Main      │  - 托盘图标渲染                         │
│  │   Thread    │  - 右键菜单响应                         │
│  │             │  - 图标状态更新                         │
│  └─────────────┘                                         │
│                                                          │
│  ┌─────────────┐  热键线程（keyboard hook 回调线程）      │
│  │  Hotkey     │  - 监听全局按键事件                     │
│  │  Thread     │  - 触发 CoreEngine 事件（线程安全）     │
│  └─────────────┘                                         │
│                                                          │
│  ┌─────────────┐  音频回调线程（sounddevice 实时线程）    │
│  │  Audio CB   │  - 仅做 buffer.append(indata.copy())   │
│  │  Thread     │  - 不做任何计算！                       │
│  └──────┬──────┘                                         │
│         │ buffer 队列                                     │
│  ┌──────▼──────┐  静音检测线程（消费者）                  │
│  │  Silence    │  - 从 buffer 取数据计算 RMS             │
│  │  Detector   │  - 检测静音超时                         │
│  └─────────────┘                                         │
│                                                          │
│  ┌─────────────┐  STT 线程池（ThreadPoolExecutor）       │
│  │  STT Pool   │  - max_workers=1（防并发）              │
│  │             │  - 异步提交识别任务                      │
│  └─────────────┘                                         │
│                                                          │
│  ┌─────────────┐  Web 服务线程（http.server）             │
│  │  Web Server │  - 配置页面 + REST API                  │
│  │  Thread     │  - daemon=True，随主线程退出            │
│  └─────────────┘                                         │
│                                                          │
│  ┌─────────────┐  提示音线程（sounddevice 播放）          │
│  │  Sound      │  - 非阻塞播放提示音                     │
│  │  Player     │  - daemon=True                          │
│  └─────────────┘                                         │
└──────────────────────────────────────────────────────────┘
```

#### 线程间通信

| 通信方式 | 用途 | 线程安全 |
|---------|------|---------|
| `threading.Event` | 热键→CoreEngine 的启停信号 | ✅ |
| `queue.Queue` | 音频回调→静音检测的数据传递 | ✅ |
| `concurrent.futures.Future` | STT 线程池任务提交和结果获取 | ✅ |
| `threading.Lock` | 配置文件读写保护 | ✅ |
| `CoreEngine._state_lock` | 状态机转换的互斥锁 | ✅ |

#### 线程安全关闭

```
关闭信号链:
1. 用户点击"退出" → pystray 回调
2. CoreEngine.shutdown() 设置 shutdown_event
3. 各线程检测 shutdown_event → 退出循环
4. 等待所有非 daemon 线程 join(timeout=5s)
5. 强制终止超时线程
6. 退出进程
```

### 3.3 CoreEngine 状态机

```python
from enum import Enum, auto

class EngineState(Enum):
    IDLE = auto()           # 就绪，等待热键触发
    RECORDING = auto()      # 录音中
    PROCESSING = auto()     # STT 识别中
    INJECTING = auto()      # 文字注入中
    ERROR = auto()          # 错误状态（可恢复）
    LOADING = auto()        # 模型加载中（启动阶段）
```

#### 状态转移矩阵

| 当前状态 | 事件 | 目标状态 | 动作 |
|---------|------|---------|------|
| LOADING | 模型加载完成 | IDLE | 图标变绿 |
| LOADING | 模型加载失败 | ERROR | 图标变红，通知用户 |
| IDLE | 热键按下 / 开始录音 | RECORDING | 启动录音，图标变红 |
| RECORDING | 热键释放(PTT) / 再按(Toggle) | PROCESSING | 停止录音，启动STT |
| RECORDING | 静音超时 | PROCESSING | 停止录音，启动STT |
| RECORDING | 最大时长 | PROCESSING | 停止录音，启动STT |
| PROCESSING | STT 完成 | INJECTING | 注入文字 |
| PROCESSING | STT 失败 | IDLE | 通知用户，图标恢复 |
| PROCESSING | STT 返回空 | IDLE | 通知"未检测到语音" |
| INJECTING | 注入完成 | IDLE | 播放完成音，图标恢复 |
| INJECTING | 注入失败 | IDLE | 文字保留剪贴板，通知用户 |
| ERROR | 用户点击"重试加载模型" | LOADING | 重新加载模型 |
| ERROR | 模型重新加载成功 | IDLE | 图标变绿 |
| * | shutdown 事件 | * | 立即停止，进入关闭流程 |

#### 状态机约束

- **互斥锁保护**：所有状态转换通过 `CoreEngine._state_lock` (threading.Lock) 保护
- **非法转换忽略**：如 RECORDING 状态下再次收到"开始录音"事件，直接忽略
- **PROCESSING 期间拒绝热键**：识别进行中按热键不排队、不中断
- **全局超时兜底**：每个状态有最大停留时间（PROCESSING 30s，INJECTING 5s），超时强制回 IDLE
- **shutdown 优先检查**：状态转换函数首先检查 `shutdown_event`，确保关闭信号不被阻塞

#### CoreEngine 类接口

```python
class CoreEngine:
    """核心调度引擎，状态机驱动"""
    
    def __init__(self, config: AppConfig, tray: TrayIcon, on_shutdown_complete):
        self._config = config
        self._tray = tray
        self._state = EngineState.LOADING
        self._state_lock = threading.Lock()
        self._shutdown_event = threading.Event()
        
        # 子模块
        self._recorder = AudioRecorder(config.audio)
        self._silence_detector = SilenceDetector(config.audio, self._on_silence_timeout)
        self._stt_engine = STTEngine(config.stt)
        self._injector = TextInjector(config.inject)
        self._sound_player = SoundPlayer(config.sound)
    
    # --- 公开方法（由热键/托盘菜单调用） ---
    
    def on_hotkey_start(self):
        """热键按下（PTT模式）/ 首次按键（Toggle模式）"""
    
    def on_hotkey_stop(self):
        """热键释放（PTT模式）/ 二次按键（Toggle模式）"""
    
    def on_hotkey_toggle(self):
        """Toggle模式热键回调，根据当前状态决定start/stop"""
    
    def on_tray_start_stop(self):
        """托盘菜单"开始/停止录音""""
    
    def on_tray_retry_model(self):
        """托盘菜单"重试加载模型"（ERROR状态恢复）"""
    
    def shutdown(self):
        """优雅关闭"""
    
    # --- 内部方法 ---
    
    def transition(self, target: EngineState) -> bool:
        """公开状态转换（自动加锁）"""
    
    def _transition_locked(self, target: EngineState) -> bool:
        """内部状态转换（调用方已持锁）"""
    
    def _start_recording(self):
        """启动录音流程"""
    
    def _stop_recording_and_transcribe(self):
        """停止录音并提交STT"""
    
    def _on_silence_timeout(self):
        """静音超时回调（在detector线程中执行）"""
    
    def _on_stt_complete(self, text: str, error: Optional[Exception]):
        """STT完成回调（在线程池工作线程中执行）"""
    
    def _inject_text(self, text: str):
        """注入文字"""
    
    def _start_timeout_watchdog(self, state: EngineState, timeout_s: float):
        """启动状态超时看门狗"""
    
    # --- 全局超时实现 ---
    # 使用 threading.Timer 实现:
    #   _start_timeout_watchdog(PROCESSING, 30) → 30秒后若仍在PROCESSING则强制回IDLE
    #   Timer 在新线程中执行，通过 _state_lock 安全检查当前状态
```

#### 全局超时实现方案

使用 `threading.Timer` 实现状态超时看门狗：

```python
def _start_timeout_watchdog(self, state: EngineState, timeout_s: float):
    def _check():
        with self._state_lock:
            if self._state == state:
                logger.warning(f"状态 {state.name} 超时，强制回 IDLE")
                self._transition_locked(EngineState.IDLE)
                self._tray.show_notification("超时", f"操作超时，已重置")
    timer = threading.Timer(timeout_s, _check)
    timer.daemon = True
    timer.start()
```

---

## 四、模块详细设计

### 4.1 入口模块 — `main.py`

**职责**：初始化各模块，启动主循环

```
启动流程:
1. 全局异常兜底: sys.excepthook = global_exception_handler
2. 单实例检测: Win32 Named Mutex "Global\VoiceInputTool_SingleInstance"
   - 已有实例运行 → 弹窗提示并退出
3. 初始化日志系统 (logging)
4. 加载配置 (config.py)，执行版本迁移
5. 异步初始化 STT 引擎:
   - 启动后台线程加载模型
   - CoreEngine 状态: LOADING
   - 托盘图标显示"加载中"
6. 初始化 CoreEngine（状态机）
7. 初始化热键管理器
8. 初始化系统托盘图标
9. 启动配置 Web 服务（后台线程）
10. 进入 pystray 主循环（阻塞）

退出流程:
1. CoreEngine.shutdown()
2. 停止录音（如果正在进行）
3. 等待 STT 线程池完成当前任务
4. 注销全局热键
5. 停止 Web 服务
6. 停止日志系统
7. 释放单实例锁
8. 退出托盘
```

#### 全局异常处理

```python
def global_exception_handler(exc_type, exc_value, exc_traceback):
    """未捕获异常的兜底处理"""
    logger.critical("未捕获的异常", exc_info=(exc_type, exc_value, exc_traceback))
    # 写入崩溃日志: %APPDATA%/voice-input-tool/crash.log
    # 托盘通知: "程序发生错误，请查看日志"
```

### 4.2 配置管理 — `config.py`

**职责**：读取/写入/校验配置文件 `config.yaml`，支持版本迁移

#### 配置数据类

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class HotkeyConfig:
    trigger: str = "f8"
    mode: str = "toggle"          # push_to_talk | toggle
    conflict_check: bool = True

@dataclass
class STTConfig:
    model_size: str = "small"
    model_path: str = "./models/"
    language: Optional[str] = None  # None=auto
    device: str = "auto"            # auto | cpu | cuda
    compute_type: str = "int8"
    beam_size: int = 5

@dataclass
class AudioConfig:
    max_duration: int = 120
    silence_timeout: int = 8        # 0=关闭
    silence_threshold: float = 0.01
    silence_check_interval: float = 0.5
    # sample_rate 不暴露给用户配置，写死在代码中
    # Whisper 要求 16000Hz，sounddevice 会按此采样率采集

@dataclass
class InjectConfig:
    method: str = "clipboard"
    auto_paste: bool = True
    paste_delay_ms: int = 100
    clipboard_backup: bool = True
    clipboard_restore: bool = True  # 默认恢复原内容
    add_trailing_space: bool = False

@dataclass
class SoundConfig:
    enabled: bool = True
    volume: float = 0.5
    start_sound: bool = True
    end_sound: bool = True
    complete_sound: bool = True

@dataclass
class WebConfig:
    port: int = 18921
    auto_open: bool = False

@dataclass
class StartupConfig:
    minimize: bool = True
    auto_start: bool = False

@dataclass
class AppConfig:
    config_version: int = 2         # 配置版本号
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
    sound: SoundConfig = field(default_factory=SoundConfig)
    web: WebConfig = field(default_factory=WebConfig)
    startup: StartupConfig = field(default_factory=StartupConfig)
```

#### 配置版本迁移

```python
CONFIG_MIGRATIONS = {
    # version → migration_function
    1: migrate_v1_to_v2,  # v1→v2: 新增 clipboard_restore, config_version 字段
}

def load_config(path: str) -> AppConfig:
    """加载配置，执行版本迁移"""
    if not os.path.exists(path):
        config = AppConfig()
        save_config(path, config)
        return config
    
    raw = yaml.safe_load(open(path))
    version = raw.get("config_version", 1)
    
    while version < CURRENT_CONFIG_VERSION:
        migrator = CONFIG_MIGRATIONS.get(version)
        if migrator:
            raw = migrator(raw)
            version = raw.get("config_version", version + 1)
    
    return AppConfig(**flatten_to_dataclass(raw))
```

#### 配置校验规则

- `hotkey.trigger`：必须为 keyboard 库支持的键名
- `stt.model_size`：枚举值校验 (tiny/base/small/medium/large-v3)
- `stt.model_path`：路径存在性检查，不存在则创建
- `audio.silence_timeout`：0 或正整数
- `web.port`：1-65535，启动时检测端口占用
- 所有数值范围校验在 dataclass `__post_init__` 中执行

#### 配置文件并发保护

```python
_config_lock = threading.Lock()

def save_config(path: str, config: AppConfig):
    """线程安全的配置保存"""
    with _config_lock:
        # 原子写入: 先写临时文件，再 rename
        tmp_path = path + ".tmp"
        yaml.dump(dataclass_to_dict(config), open(tmp_path, 'w'))
        os.replace(tmp_path, path)
```

### 4.3 热键管理 — `core/hotkey.py`

**职责**：注册/注销全局热键，处理按键事件

> ⚠️ **关键约束**: `keyboard` 库在 Windows 上使用 LowLevelKeyboardHook，**必须以管理员权限运行**。非管理员运行时，`keyboard.add_hotkey()` 会抛出 `ImportError` 或静默失败。

```python
class HotkeyManager:
    def __init__(self, config: HotkeyConfig, on_start, on_stop):
        """
        on_start: callback — 录音开始（由 CoreEngine 提供）
        on_stop: callback — 录音停止（由 CoreEngine 提供）
        """
        self._lock = threading.Lock()
        self._registered = False
    
    def register(self):
        """注册全局热键，检测冲突"""
        # 1. 检测管理员权限
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise PermissionError("需要管理员权限运行本工具")
        
        # 2. 调用 keyboard.add_hotkey()
        #    Toggle: keyboard.add_hotkey(key, self._on_toggle)
        #    PTT: keyboard.on_press_key(key, self._on_press)
        #         keyboard.on_release_key(key, self._on_release)
        
        # 3. 如果 conflict_check=True，检测常用键冲突
        #    冲突列表: 截图键、系统快捷键、常见 IDE 快捷键
        # 4. 冲突时弹窗提示，但不阻止注册
    
    def unregister(self):
        """注销热键"""
    
    def rebind(self, new_key: str):
        """运行时热更换快捷键"""
        with self._lock:
            self.unregister()
            # 更新 key
            self.register()
```

#### PTT 模式实现

```
keyboard.on_press_key → callback → CoreEngine.on_hotkey_start()
  （独立 hook 线程，不阻塞）
keyboard.on_release_key → callback → CoreEngine.on_hotkey_stop()
  （独立 hook 线程，不阻塞）
防抖: 200ms 内忽略重复事件
```

#### Toggle 模式实现

```
keyboard.add_hotkey → callback → CoreEngine.on_hotkey_toggle()
  （独立 hook 线程，不阻塞）
状态由 CoreEngine 状态机管理
```

#### 快捷键冲突处理

启动时检测以下冲突：
- 系统级：PrintScreen、Win+*、Alt+F4 等
- 常见应用：IDE 快捷键（F8 在某些 IDE 是调试键）
- 冲突检测为**警告**级别，不阻止运行
- 检测到冲突时通过托盘气泡通知提醒用户

### 4.4 音频录制 — `core/recorder.py`

**职责**：音频采集、缓冲管理

```python
import queue

from collections import deque

class AudioRecorder:
    SAMPLE_RATE = 16000  # 常量，不暴露给用户配置
    
    def __init__(self, config: AudioConfig):
        self.config = config
        self.is_recording = False
        self._buffer = deque()          # 线程安全 deque
        self._buffer_queue = queue.Queue()  # 线程安全队列，传递给静音检测线程
        self._stream = None
    
    def start(self):
        """开始录音"""
        self._buffer.clear()
        self._stream = sounddevice.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=self._audio_callback
        )
        self._stream.start()
        self.is_recording = True
    
    def stop(self) -> np.ndarray:
        """停止录音，返回完整 PCM 数据"""
        self.is_recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return np.concatenate(list(self._buffer)) if self._buffer else np.array([])
    
    def _audio_callback(self, indata, frames, time_info, status):
        """sounddevice 回调（实时线程）
        ⚠️ 关键约束：此方法在实时音频线程中执行
        只做数据拷贝，不做任何计算！
        """
        chunk = indata.copy()
        self._buffer.append(chunk)           # 主数据缓冲
        self._buffer_queue.put(chunk)         # 传递给静音检测
    
    def get_buffer_queue(self) -> queue.Queue:
        """返回缓冲队列，供静音检测线程消费"""
        return self._buffer_queue
```

### 4.5 静音检测 — `core/silence_detector.py`

**职责**：独立线程消费音频数据，检测静音超时

> **设计原则**：detector 线程是**持续运行的消费者**，不自行退出。通过 `_detecting` flag 控制是否执行静音检测。CoreEngine 只需设置 flag，无需 join 线程，**避免死锁**。

```python
import time

class SilenceDetector:
    def __init__(self, config: AudioConfig, on_silence_timeout):
        """
        on_silence_timeout: callback — 静音超时时调用 CoreEngine
        ⚠️ 此 callback 在 detector 线程中执行，CoreEngine 处理时
           不能调用 detector 的任何会 join 的方法
           CoreEngine 只需调用 end_detection() 设 flag 即可
        """
        self.config = config
        self._queue = None               # 由 AudioRecorder 提供
        self._running = True              # 线程生命周期（仅在程序退出时设 False）
        self._detecting = False           # 是否正在检测静音（每次录音开始设 True）
        self._thread = None
    
    def start(self, buffer_queue: queue.Queue):
        """启动消费者线程（程序启动时调用一次，线程持续运行）"""
        self._queue = buffer_queue
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def begin_detection(self):
        """开始一次静音检测（每次录音开始时调用）"""
        self._last_sound_time = time.monotonic()
        self._detecting = True
    
    def end_detection(self):
        """结束静音检测（录音停止时调用，只设 flag 不 join）"""
        self._detecting = False
    
    def shutdown(self):
        """关闭线程（程序退出时调用）"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
    
    def _run(self):
        """消费者主循环（持续运行，不自行退出）"""
        self._last_sound_time = time.monotonic()
        
        while self._running:
            try:
                chunk = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            
            if not self._detecting:
                continue  # 非录音状态，丢弃数据
            
            rms = self._calculate_rms(chunk)
            if rms >= self.config.silence_threshold:
                self._last_sound_time = time.monotonic()
            else:
                silence_duration = time.monotonic() - self._last_sound_time
                if silence_duration >= self.config.silence_timeout:
                    self._detecting = False  # 先关闭检测，防止重复触发
                    on_silence_timeout()     # 通知 CoreEngine（CoreEngine 不 join 此线程）
    
    def _calculate_rms(self, data: np.ndarray) -> float:
        """计算音频 RMS 能量"""
        return float(np.sqrt(np.mean(data ** 2)))
```

### 4.6 STT 引擎 — `core/stt_engine.py`

**职责**：加载模型，音频转文字（异步执行）

```python
from concurrent.futures import ThreadPoolExecutor, Future

class STTEngine:
    def __init__(self, config: STTConfig):
        self.model = None
        self.config = config
        self._executor = ThreadPoolExecutor(max_workers=1)  # 单 worker 防并发
        self._current_future: Optional[Future] = None
        self._lock = threading.Lock()
    
    # ⚠️ 注意: transcribe_async 的 callback 在线程池工作线程中执行
    # callback 内的 UI 操作（如托盘通知）需确保线程安全
    
    def load_model(self) -> bool:
        """加载 faster-whisper 模型（耗时操作）
        返回 True=成功, False=失败
        """
        try:
            from faster_whisper import WhisperModel
            device = self._detect_device()
            compute_type = self._detect_compute_type(device)
            self.model = WhisperModel(
                model_size_or_path=self.config.model_path,
                device=device,
                compute_type=compute_type,
            )
            return True
        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            return False
    
    def transcribe_async(self, audio: np.ndarray, callback):
        """异步转写：提交到线程池，完成后回调 callback(text, error)"""
        with self._lock:
            if self._current_future and not self._current_future.done():
                # 已有任务在执行，拒绝新任务
                callback("", RuntimeError("STT 正忙"))
                return
            
            self._current_future = self._executor.submit(self._do_transcribe, audio)
            self._current_future.add_done_callback(
                lambda f: callback(*self._unpack_result(f))
            )
    
    def _do_transcribe(self, audio: np.ndarray) -> tuple:
        """实际转写逻辑（线程池中执行）"""
        try:
            if audio.size == 0:
                return ("", None)  # 空音频
            if len(audio) < 3200:  # <0.2s 音频，太短无法识别
                return ("", None)
            segments, info = self.model.transcribe(
                audio,
                language=self.config.language,
                beam_size=self.config.beam_size,
                vad_filter=True,
            )
            text = " ".join(seg.text for seg in segments).strip()
            return (text if text else "", None)
        except Exception as e:
            return ("", e)
    
    def _unpack_result(self, future: Future) -> tuple:
        """解包 Future 结果，处理异常"""
        try:
            return future.result()  # (text, error) tuple
        except Exception as e:
            return ("", e)
    
    def shutdown(self):
        """关闭线程池"""
        self._executor.shutdown(wait=True)
```

#### 模型管理

```
models/
├── small/                    # 默认模型
│   ├── model.bin
│   ├── vocabulary.txt
│   └── ...
└── README.txt               # 模型替换说明
```

- 首次运行检测 `model_path` 下是否有模型文件
- 无模型 → 托盘通知 + 提供下载脚本
- 支持用户自行替换模型（修改 config.yaml 中 model_size）
- 模型文件使用 faster-whisper 的 CTranslate2 格式

#### 设备自动检测

```python
def _detect_device(self) -> str:
    if self.config.device != "auto":
        return self.config.device
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except:
        pass
    return "cpu"

def _detect_compute_type(self, device: str) -> str:
    if device == "cuda":
        return "float16"
    return "int8"
```

### 4.7 文字注入 — `core/injector.py`

**职责**：文字写入剪贴板并模拟粘贴（使用 Win32 API，无第三方依赖）

```python
import ctypes
from ctypes import wintypes

class TextInjector:
    def __init__(self, config: InjectConfig):
        self.config = config
        self._backup = None
    
    def inject(self, text: str) -> bool:
        """注入文字到当前光标位置，返回成功/失败"""
        try:
            # 1. 备份剪贴板
            if self.config.clipboard_backup:
                self._backup = self._read_clipboard()
            
            # 2. 原子写入剪贴板（Win32 API）
            if not self._write_clipboard(text):
                raise RuntimeError("剪贴板写入失败")
            
            # 3. 等待延时
            time.sleep(self.config.paste_delay_ms / 1000)
            
            # 4. 模拟 Ctrl+V
            self._simulate_paste()
            
            # 5. 恢复原剪贴板内容（如果配置了）
            PASTE_SETTLE_DELAY = 0.2  # 等待粘贴完成的延时（秒）
            if self.config.clipboard_restore and self._backup is not None:
                time.sleep(PASTE_SETTLE_DELAY)
                self._write_clipboard(self._backup)
            
            return True
        except Exception as e:
            logger.error(f"注入失败: {e}")
            self._handle_error(text)
            return False
    
    def _read_clipboard(self) -> Optional[str]:
        """使用 Win32 API 读取剪贴板
        如果剪贴板无文本内容（如为图片/文件），返回 None
        """
        # OpenClipboard → GetClipboardData(CF_UNICODETEXT)
        # 如果返回 NULL → CloseClipboard → return None
        pass
    
    def _write_clipboard(self, text: str) -> bool:
        """使用 Win32 API 写入剪贴板（原子操作）"""
        # OpenClipboard(None) → EmptyClipboard() 
        # → SetClipboardData(CF_UNICODETEXT, handle) → CloseClipboard()
        # 关键: OpenClipboard/CloseClipboard 之间自动加锁，消除竞态
        pass
    
    def _simulate_paste(self):
        """使用 Win32 SendInput 模拟 Ctrl+V"""
        # SendInput 按键序列:
        # KeyDown(VK_CONTROL) → sleep(10ms)
        # KeyDown('V') → sleep(10ms)
        # KeyUp('V') → sleep(10ms)
        # KeyUp(VK_CONTROL)
        # 失败时不回退到 keyboard（同样需管理员权限），而是确保文字在剪贴板中
        # 通知用户手动 Ctrl+V
        pass
    
    def _handle_error(self, text: str):
        """注入失败: 文字保留在剪贴板，通知用户"""
        self._write_clipboard(text)  # 至少保证文字在剪贴板中
```

#### 已知限制

- **窗口切换问题**：如果用户在录音过程中切换了窗口，文字会注入到当前焦点窗口而非录音时的窗口。这是全局热键工具的通病，**不做特殊处理**，在 README 中说明。
- **UAC 窗口**：在 UAC 提示框等系统级窗口上方，SendInput 可能无法注入。同样在 README 中说明。

### 4.8 提示音管理 — `core/sound_player.py`

**职责**：使用 sounddevice 播放提示音（复用同一库，不引入额外依赖）

```python
class SoundPlayer:
    def __init__(self, config: SoundConfig):
        self.config = config
        self._sounds = {}  # 缓存加载的 WAV 数据
    
    def play(self, sound_name: str):
        """非阻塞播放提示音"""
        if not self.config.enabled:
            return
        if not getattr(self.config, f"{sound_name}_sound", True):
            return
        threading.Thread(
            target=self._play_sync,
            args=(sound_name,),
            daemon=True
        ).start()
    
    def _play_sync(self, sound_name: str):
        """同步播放（在独立线程中）"""
        data = self._load_sound(sound_name)
        volume = self.config.volume
        # sounddevice.play(data * volume, samplerate=SR)
```

### 4.9 系统托盘 — `gui/tray.py`

**职责**：托盘图标、右键菜单、状态切换

```python
class TrayIcon:
    def __init__(self, on_start, on_stop, on_settings, on_quit):
        self.state = EngineState.IDLE
        self._icon = None
    
    def set_state(self, state: EngineState):
        """切换图标和菜单状态（线程安全，可从任意线程调用）"""
        # pystray.icon.notify() / icon.icon = new_image
    
    def show_notification(self, title, message):
        """托盘气泡通知"""
    
    def run(self):
        """启动托盘主循环（阻塞，在主线程调用）"""
```

#### 右键菜单

```
├── 🎤 开始录音 / ⏹ 停止录音    (根据状态切换)
├── ───────────
├── ⚙ 打开设置
├── 🔍 检查模型
├── 🔄 重试加载模型              (仅 ERROR 状态显示)
├── ───────────
└── ❌ 退出
```

- 双击托盘图标 → 打开设置页面

#### 图标资源

```
assets/
├── icon_idle.png         # 就绪（绿色麦克风）
├── icon_recording.png    # 录音中（红色麦克风）
├── icon_processing.png   # 识别中（黄色麦克风/齿轮）
├── icon_loading.png      # 模型加载中（灰色）
├── icon_error.png        # 错误（红色感叹号）
├── sound_start.wav       # 录音开始提示音
├── sound_end.wav         # 录音结束提示音
└── sound_complete.wav    # 识别完成提示音
```

### 4.10 配置 Web 界面 — `gui/web_server.py`

**职责**：提供 HTTP 配置页面（带简单 token 校验）

```python
class ConfigWebServer:
    def __init__(self, config: AppConfig, on_config_changed):
        self._token = secrets.token_hex(16)  # 启动时生成随机 token
        self._config_url = f"http://127.0.0.1:{config.web.port}?token={self._token}"
        self._on_config_changed = on_config_changed
    
    def start(self):
        """启动 Web 服务（后台 daemon 线程）"""
    
    def stop(self):
        """停止 Web 服务"""
    
    def get_config_url(self) -> str:
        """返回带 token 的配置页 URL"""
```

#### 安全措施

- 绑定 `127.0.0.1`（仅本机访问）
- 所有 PUT/POST 请求校验 `?token=xxx` 参数
- 启动时生成随机 token，通过托盘"打开设置"获取带 token 的 URL
- token 不持久化（每次启动重新生成）

#### API 设计

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 配置页面 HTML |
| `/api/config` | GET | 获取当前配置（JSON） |
| `/api/config` | PUT | 更新配置（JSON），需 token |
| `/api/status` | GET | 获取运行状态（引擎状态、模型信息） |
| `/api/model/info` | GET | 获取模型信息 |
| `/api/hotkey/test` | POST | 测试快捷键是否可用（尝试注册后立即注销） |
| `/api/audio/test` | POST | 录音 2 秒并回放，测试设备 |
| `/api/sound/test` | POST | 播放测试提示音 |

#### 配置变更生效机制

| 配置项 | 生效方式 |
|--------|---------|
| 快捷键 | 即时生效（热更换） |
| 提示音音量 | 即时生效 |
| 提示音开关 | 即时生效 |
| 静音超时 | 即时生效 |
| STT 模型/设备/精度 | **需重启**（配置页提示） |
| Web 端口 | **需重启**（配置页提示） |

#### 前端页面功能

单页应用，纯 HTML/CSS/JS，无框架依赖：

- **快捷键配置**：输入框捕获按键、冲突检测按钮、模式切换、即时生效
- **STT 配置**：模型选择下拉框、语言选择、设备信息显示（需重启项标注）
- **音频配置**：滑块设置参数、录音测试按钮
- **提示音配置**：开关、音量滑块、试听按钮
- **注入配置**：各选项开关
- **保存/重置**：保存到 config.yaml、恢复默认

---

## 五、日志系统设计

### 5.1 日志配置

```python
import logging
from logging.handlers import TimedRotatingFileHandler

def setup_logging():
    log_dir = os.path.join(os.getenv('APPDATA'), 'voice-input-tool', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    handler = TimedRotatingFileHandler(
        os.path.join(log_dir, 'app.log'),
        when='midnight',
        backupCount=7,          # 保留 7 天
        encoding='utf-8'
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(threadName)s - %(name)s: %(message)s'
    ))
    
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    
    # 崩溃日志单独文件
    crash_handler = logging.FileHandler(
        os.path.join(log_dir, 'crash.log'), encoding='utf-8'
    )
    crash_handler.setLevel(logging.CRITICAL)
    root.addHandler(crash_handler)
```

### 5.2 日志规范

| 级别 | 使用场景 |
|------|---------|
| DEBUG | 音频帧数、RMS 值等调试信息 |
| INFO | 状态转换、录音开始/结束、识别完成、配置变更 |
| WARNING | 快捷键冲突、静音超时、剪贴板备份失败 |
| ERROR | STT 失败、模型加载失败、录音设备错误 |
| CRITICAL | 未捕获异常（崩溃） |

---

## 六、核心工作流

### 6.1 Toggle 模式（默认）

```
┌─────────┐     ┌─────────┐     ┌──────────┐     ┌──────────┐     ┌─────────┐
│  IDLE   │────▶│RECORDING│────▶│PROCESSING│────▶│INJECTING │────▶│  IDLE   │
│ (图标绿) │     │ (图标红) │     │ (图标黄) │     │ (图标蓝) │     │ (图标绿) │
└─────────┘     └─────────┘     └──────────┘     └──────────┘     └─────────┘
                  │ ▲
                  │ │ 触发停止条件:
                  │ │ 1. 再按 F8
                  │ │ 2. 静音超时
                  │ │ 3. 最大时长
                  ▼ │
```

详细步骤：

1. 用户按下 F8
2. HotkeyManager 捕获（hook 线程）→ CoreEngine.on_hotkey_toggle()
3. 状态机: IDLE → RECORDING（加锁）
4. AudioRecorder.start() → sounddevice 开始采集
5. SilenceDetector.begin_detection() → 开始静音检测
6. SoundPlayer.play("start") → 提示音（daemon 线程）
7. TrayIcon.set_state(RECORDING) → 图标变红
8. 用户说话...
9. 停止触发（三选一）：
   - 用户再按 F8 → CoreEngine.on_hotkey_toggle()
   - 静音超时 → SilenceDetector callback → CoreEngine.on_silence_timeout()
   - 最大时长 → AudioRecorder 内部定时器 → CoreEngine.on_max_duration()
10. 状态机: RECORDING → PROCESSING（加锁）
11. audio = AudioRecorder.stop() → 返回完整 PCM 数据
12. SilenceDetector.end_detection()
13. SoundPlayer.play("end")
14. TrayIcon.set_state(PROCESSING) → 图标变黄
15. STTEngine.transcribe_async(audio, callback) → 提交到线程池
16. callback(text, error) 被调用：
    - error → 状态机: PROCESSING → IDLE，通知用户
    - text 为空 → 状态机: PROCESSING → IDLE，通知"未检测到语音"
    - text 有效 → 状态机: PROCESSING → INJECTING
17. TextInjector.inject(text)
18. 状态机: INJECTING → IDLE
19. SoundPlayer.play("complete")
20. TrayIcon.set_state(IDLE) → 图标变绿

### 6.2 Push-to-Talk 模式

与 Toggle 相同，区别仅在触发方式：
- 按住 F8 → on_hotkey_start() → IDLE → RECORDING
- 松开 F8 → on_hotkey_stop() → RECORDING → PROCESSING
- 其余流程一致

### 6.3 异常流程

#### 录音失败
```
AudioRecorder.start() 异常
→ CoreEngine 捕获 → 状态机: IDLE 保持不变
→ TrayIcon 通知 "麦克风不可用"
→ logger.error(...) 记录详细错误
```

#### 模型加载失败
```
启动时模型加载失败
→ CoreEngine 状态: LOADING → ERROR
→ TrayIcon 通知 "模型加载失败，请检查模型文件"
→ 热键不注册（防止用户触发后无响应）
→ 配置页面仍可用，可引导用户检查/下载模型
→ 用户通过配置页修复后可触发重新加载
```

#### 识别失败
```
STT 异常 → callback("", exception)
→ CoreEngine → 状态机: PROCESSING → IDLE
→ TrayIcon 通知 "识别失败: {reason}"
→ logger.error(...)
```

#### 识别返回空
```
text="" 且无异常 → 用户说了话但模型没识别出来
→ CoreEngine → 状态机: PROCESSING → IDLE
→ TrayIcon 通知 "未检测到有效语音"
```

#### 剪贴板注入失败
```
TextInjector.inject() 返回 False
→ 文字已保留在剪贴板
→ CoreEngine → 状态机: INJECTING → IDLE
→ TrayIcon 通知 "文字已复制到剪贴板，请手动粘贴 Ctrl+V"
```

#### 全局超时兜底
```
PROCESSING 状态超过 30 秒
→ CoreEngine 定时器触发 → 强制回 IDLE
→ logger.warning("STT 处理超时")

INJECTING 状态超过 5 秒
→ CoreEngine 定时器触发 → 强制回 IDLE
```

---

## 七、项目目录结构

```
voice-input-tool/
│
├── main.py                          # 入口：单实例检测 + 初始化 + 异常兜底
├── config.py                        # 配置管理（dataclass + 版本迁移）
├── config.yaml                      # 用户配置文件（首次运行自动生成）
│
├── core/
│   ├── __init__.py
│   ├── engine.py                    # CoreEngine 状态机（核心调度）
│   ├── hotkey.py                    # 全局热键管理
│   ├── recorder.py                  # 音频录制（仅采集，不做计算）
│   ├── silence_detector.py          # 静音检测（独立消费者线程）
│   ├── stt_engine.py                # STT 引擎封装（线程池异步）
│   ├── injector.py                  # 文字注入（Win32 API）
│   └── sound_player.py              # 提示音播放（sounddevice）
│
├── gui/
│   ├── __init__.py
│   ├── tray.py                      # 系统托盘
│   ├── web_server.py                # 配置 Web 服务（带 token 校验）
│   └── templates/
│       └── config.html              # 配置页面
│
├── assets/
│   ├── icon_idle.png                # 托盘图标 - 就绪
│   ├── icon_recording.png           # 托盘图标 - 录音中
│   ├── icon_processing.png          # 托盘图标 - 识别中
│   ├── icon_loading.png             # 托盘图标 - 加载中
│   ├── icon_error.png               # 托盘图标 - 错误
│   ├── sound_start.wav              # 提示音 - 开始
│   ├── sound_end.wav                # 提示音 - 结束
│   └── sound_complete.wav           # 提示音 - 完成
│
├── models/
│   └── small/                       # 默认 STT 模型（CTranslate2 格式）
│
├── python/                          # Embeddable Python（打包时放入）
│
├── scripts/
│   ├── setup.bat                    # 一键环境准备脚本
│   ├── download_model.py            # 模型下载脚本（备用）
│   └── build.bat                    # 打包脚本（开发用）
│
├── requirements.txt                 # Python 依赖
├── DESIGN.md                        # 本设计文档
└── README.md                        # 使用说明
```

---

## 八、打包与分发

### 8.1 ZIP 包结构

```
voice-input-tool-v1.0.zip
└── voice-input-tool/
    ├── main.py
    ├── config.py
    ├── core/
    ├── gui/
    ├── assets/
    ├── models/                  # 包含 small 模型
    │   └── small/
    ├── python/                  # Embeddable Python 3.11
    │   ├── python.exe
    │   ├── python311.zip
    │   └── Lib/site-packages/  # 预装依赖
    ├── scripts/
    ├── config.yaml
    ├── run.bat                  # 一键启动（含管理员权限请求）
    ├── setup.bat                # 首次环境准备
    └── README.md
```

### 8.2 run.bat — 管理员权限处理

```batch
@echo off
:: 检测管理员权限，如果没有则请求提权
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo 请求管理员权限...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
python\python.exe main.py
```

### 8.3 预估体积

| 组件 | 大小 |
|------|------|
| Embeddable Python 3.11 | ~30MB |
| 依赖库 (faster-whisper, ctranslate2, etc.) | ~200MB |
| Whisper small 模型 (CTranslate2 格式) | ~500MB |
| 其他资源 | ~5MB |
| **总计** | **~735MB** |
| ZIP 压缩后 | **~500-600MB** |

### 8.4 环境准备脚本 — `setup.bat`

```batch
@echo off
echo === 语音输入工具 - 环境准备 ===

REM 1. 检查 VC++ Runtime (CTranslate2 依赖)
REM    检查注册表 HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64
REM    如果缺少，提示下载安装并提供链接

REM 2. 配置 embeddable Python
REM    确保 python311.zip 已解压
REM    确保 pip 可用（get-pip.py）

REM 3. 安装依赖（如果 site-packages 为空）
REM    python\python.exe -m pip install -r requirements.txt

REM 4. 检查模型文件
REM    如果 models/small/ 为空，运行下载脚本

echo === 环境准备完成 ===
echo 请以管理员权限运行 run.bat 启动工具
pause
```

---

## 九、性能考量

### 9.1 内存占用

| 组件 | 预估内存 |
|------|---------|
| Python 解释器 | ~30MB |
| CTranslate2 运行时 | ~100MB |
| Whisper small 模型 | ~500MB |
| 录音缓冲 (120s @ 16kHz float32) | ~7.5MB |
| **总计** | **~640MB** |

- CPU + int8 精度下，small 模型识别速度约 **4-8x 实时**（即 10 秒音频 ~1-2 秒识别完）
- 有 CUDA GPU 时，速度更快，内存压力也转移到 GPU

### 9.2 延迟分析

| 阶段 | 延迟 |
|------|------|
| 热键响应 | <50ms |
| 录音启动 | <100ms |
| 录音→识别（10s 音频） | 1-2s (CPU) / 0.3-0.5s (CUDA) |
| 识别→注入 | <200ms |
| **端到端（10s 语音）** | **~1.5-2.5s (CPU)** |

### 9.3 性能降级策略

如果运行机器性能不足：
- 可切换到 `tiny` 或 `base` 模型（更小更快，精度略降）
- `compute_type=int8` 是默认值，已是最快精度
- 模型加载后常驻内存，不会反复加载

---

## 十、待实现优先级

### P0 — 核心功能（MVP）

1. CoreEngine 状态机（core/engine.py）
2. 音频录制（core/recorder.py）
3. STT 引擎封装（core/stt_engine.py）— 异步线程池
4. 热键管理（core/hotkey.py）— Toggle 模式
5. 文字注入（core/injector.py）— Win32 API
6. 系统托盘（gui/tray.py）— 基础图标 + 退出
7. 配置管理（config.py）— dataclass + YAML 读写
8. 日志系统（main.py 中初始化）

### P1 — 完善体验

9. PTT 模式支持
10. 静音检测（core/silence_detector.py）
11. 提示音播放（core/sound_player.py）
12. 快捷键冲突检测
13. 单实例锁
14. 全局超时兜底

### P2 — 配置界面

15. Web 配置页面 + API
16. 配置热更新机制
17. 录音/提示音测试功能

### P3 — 打包分发

18. Embeddable Python 集成
19. 环境准备脚本
20. 管理员权限处理（run.bat）
21. ZIP 打包脚本
22. 图标资源制作

---

## 十一、已知风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 需要管理员权限 | 部分用户不愿提权 | run.bat 自动请求 UAC 提权；README 说明原因 |
| Ctrl+V 在某些应用中不稳定 | 文字注入失败 | 延时可配置 + 回退方案（保留到剪贴板） |
| 窗口切换导致注入到错误应用 | 文字发错地方 | README 中标注为已知限制 |
| faster-whisper 缺少 VC++ Runtime | 程序无法启动 | setup.bat 检测并引导安装 |
| 全局热键被其他软件抢占 | 无法触发录音 | 启动时冲突检测 + 支持自定义换键 + 热更换 |
| 640MB 内存占用对低配机器偏高 | 系统卡顿 | 支持切换 tiny/base 模型 |
| 剪贴板被其他程序修改 | 粘贴到错误内容 | Win32 Clipboard API 原子操作 |
| 麦克风权限未授予 | 录音失败 | 启动时检测 + 托盘通知引导 |
| UAC 窗口导致 SendInput 失败 | 无法自动粘贴 | README 标注已知限制 |

---

## 附录 A: 依赖清单 (requirements.txt)

```
keyboard>=0.13.12
sounddevice>=0.4.6
numpy>=1.24.0
faster-whisper>=1.0.0
pystray>=0.19.0
Pillow>=10.0.0
PyYAML>=6.0
```

> 注：剪贴板操作和按键模拟使用 ctypes 调用 Win32 API，无需第三方库。提示音播放复用 sounddevice，无需 simpleaudio。

## 附录 B: 模型替换指南

如需更换模型：

1. 下载目标模型的 CTranslate2 格式文件
   - 官方转换: `ct2-transformers-converter --model openai/whisper-base --output_dir models/base`
   - 或从 HuggingFace 下载预转换版本
2. 放置到 `models/{model_name}/` 目录
3. 修改 `config.yaml` 中 `stt.model_size` 为对应名称
4. 重启工具

模型大小参考：

| 模型 | 磁盘空间 | 内存占用 | 相对速度 | 中文质量 |
|------|---------|---------|---------|---------|
| tiny | ~75MB | ~150MB | 最快 | 一般 |
| base | ~150MB | ~250MB | 快 | 可用 |
| **small** | **~500MB** | **~500MB** | **中等** | **好（推荐）** |
| medium | ~1.5GB | ~1.5GB | 慢 | 很好 |
| large-v3 | ~3GB | ~3GB | 最慢 | 最佳 |

---

*文档结束。版本 v3.0 — 经过 3 轮评审迭代。*
