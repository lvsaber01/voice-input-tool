# 语音输入工具 V2 增强设计文档

> **版本**: v3.1  
> **日期**: 2026-04-19  
> **基础版本**: v3.0 (已实现核心功能)  
> **状态**: 待评审  
> **项目位置**: `~/PROJECT/voice-input-tool/`  
> **评审历史**: v1.0: 72分 → v2.0: 79分 → v3.0: 85分 → v3.1: 待评审

---

## 一、变更总览

| # | 类型 | 变更名称 | 优先级 | 影响范围 |
|---|------|---------|--------|---------|
| 0 | 架构 | 事件总线解耦 | 架构 | 新增 events.py, 改 engine.py |
| 1 | 优化 | 录音最大时长保护 | P0 | engine.py |
| 2 | 优化 | 剪贴板备份兜底机制 | P0 | clipboard_base.py |
| 3 | 优化 | 提示音依赖修复（wave 标准库） | P0 | sound_player.py |
| 4 | 优化 | 使用统计模块 | P1 | 新增 stats.py, web_server.py |
| 5 | 优化 | STT 语言检测结果展示 | P1 | stt_engine.py, engine.py |
| 6 | 优化 | 声卡设备选择 | P1 | recorder.py, config.py, web_server.py |
| 7 | 优化 | 热键冲突检测实现 | P2 | hotkey.py |
| 8 | 扩展 | 语音命令模式 | 扩展 | 新增 command.py |
| 9 | 扩展 | 语音活动可视化（VAD 指示器） | 扩展 | 新增 vad_indicator.py |

---

## 二、架构改进：事件总线

### 问题

CoreEngine 承担过多职责（7/9 个变更要改它），且 `tray.set_state` 在持锁状态下同步调用，存在 ABBA 死锁风险。

### 方案

引入 `core/events.py` 轻量级事件总线。engine 只负责发布事件，各子系统通过订阅事件工作。

**`core/events.py`**：

```python
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
    ENGINE_SHUTDOWN = auto()        # ()


class EventBus:
    """进程内事件总线

    - handler 通过 ThreadPoolExecutor(max_workers=2) 异步执行
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
        """优雅关闭，等待已提交事件完成（最多 3 秒）。

        cancel_futures=True 取消尚未开始的任务（Python 3.9+）。
        超时后强制关闭，不无限挂起。
        """
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            # Python 3.8 不支持 cancel_futures
            self._executor.shutdown(wait=False)
```

### 集成方式

**engine.py**：
- `__init__` 创建 `EventBus`
- 状态转换后 `self._events.publish(EngineEvent.STATE_CHANGED, old, new)`
- 转写完成后 `self._events.publish(EngineEvent.TRANSCRIBE_COMPLETE, text, lang, ms)`
- 删除 engine 中对 `self._stats.*` / `self._vad_indicator.*` / `self._tray.show_notification` 的直接调用

**各子系统在 main.py 中订阅**：
```python
# Stats 订阅
events.subscribe(EngineEvent.TRANSCRIBE_COMPLETE, stats.record_transcribe)
events.subscribe(EngineEvent.TRANSCRIBE_ERROR, lambda e: stats.record_error())
events.subscribe(EngineEvent.MAX_DURATION_TRIGGERED, stats.record_max_duration)

# VAD 指示器订阅
events.subscribe(EngineEvent.RMS_UPDATE, vad_indicator.update)
events.subscribe(EngineEvent.RECORDING_STARTED, vad_indicator.show)
events.subscribe(EngineEvent.RECORDING_STOPPED, vad_indicator.hide)

# Tray 订阅（异步执行，解决死锁）
events.subscribe(EngineEvent.STATE_CHANGED, tray.on_state_changed)
```

### STT 回调签名同步

`_on_stt_complete` 签名变更为 `(text, language, duration_ms, error)`，**破坏性变更**，需同步修改：

**stt_engine.py**：
```python
def _do_transcribe(self, audio: np.ndarray) -> tuple:
    try:
        if audio.size == 0 or len(audio) < 3200:
            return ("", None, 0, None)
        import time
        t0 = time.monotonic()
        segments, info = self.model.transcribe(
            audio, language=self.config.language,
            beam_size=self.config.beam_size, vad_filter=True,
        )
        text = " ".join(seg.text for seg in segments).strip()
        duration_ms = int((time.monotonic() - t0) * 1000)
        language = getattr(info, 'language', None)
        return (text if text else "", language, duration_ms, None)
    except Exception as e:
        return ("", None, 0, e)

def _unpack_result(self, future) -> tuple:
    try:
        return future.result()
    except Exception as e:
        return ("", None, 0, e)

def transcribe_async(self, audio, callback):
    with self._lock:
        if self._current_future and not self._current_future.done():
            callback("", None, 0, RuntimeError("STT 正忙"))
            return
        self._current_future = self._executor.submit(self._do_transcribe, audio)
        self._current_future.add_done_callback(
            lambda f: callback(*self._unpack_result(f))
        )
```

**engine.py** 的 `_on_stt_complete`：
```python
def _on_stt_complete(self, text: str, language: Optional[str],
                     duration_ms: int, error: Optional[Exception]):
```

---

## 三、详细设计

### 3.1 录音最大时长保护

**问题**：Toggle 模式忘记停止会无限录音。

**方案**：`_start_recording()` 中启动 `threading.Timer`，超时自动提交。

```python
# CoreEngine.__init__ 新增
self._max_duration_timer: Optional[threading.Timer] = None

def _start_recording(self):
    if self.transition(EngineState.RECORDING):
        # ... 现有启动逻辑 ...
        max_dur = self._config.audio.max_duration
        if max_dur > 0:
            self._max_duration_timer = threading.Timer(max_dur, self._on_max_duration_timeout)
            self._max_duration_timer.daemon = True
            self._max_duration_timer.start()

def _on_max_duration_timeout(self):
    """Timer 线程中执行。
    _stop_recording_and_transcribe 内部先调 transition()，
    状态锁保证去重：只有 RECORDING→PROCESSING 成功的那个线程才执行后续清理。
    事件在 transition 成功后发布，保证状态一致性。
    """
    self._stop_recording_and_transcribe()

def _stop_recording_and_transcribe(self):
    """停止录音并提交 STT（去重安全）。

    transition(RECORDING → PROCESSING) 持有 _state_lock，
    同一时刻只有一个线程能成功。失败方直接 return，
    recorder.stop() 只会被执行一次。
    """
    if not self.transition(EngineState.PROCESSING):
        logger.debug("_stop_recording: 状态已变，跳过（去重）")
        return
    logger.info("停止录音，开始识别")
    if self._max_duration_timer:
        self._max_duration_timer.cancel()
        self._max_duration_timer = None
    # ... 现有停止逻辑（recorder.stop / silence_detector.end / sound_player） ...
    # 事件在 transition 成功后发布，保证状态一致
    self._events.publish(EngineEvent.MAX_DURATION_TRIGGERED)
```

streaming 模式同样加入，复用同一个 `_max_duration_timer`。

**注意：watchdog 去重**：现有 engine.py 的 `_start_timeout_watchdog._check` 中在 `_state_lock` 内直接调用 `tray.show_notification`。重构为事件总线后，改为锁内记录状态、锁外 publish 事件，消除潜在 ABBA 死锁。

---

### 3.2 剪贴板备份兜底机制

**问题**：崩溃时剪贴板备份丢失。

**方案**：备份时同步写入临时文件，base64 编码 + 文件权限限制。

```python
class ClipboardInjectorBase(ABC):
    _BACKUP_FILE = os.path.join(tempfile.gettempdir(), f"voice_input_tool_cb_{os.getpid()}.bak")

    def inject(self, text: str) -> bool:
        if not text or not text.strip():
            return True
        try:
            self._backup = self.read_clipboard()
            self._backup_to_file(self._backup)
        except Exception:
            self._backup = None
        # ... 现有注入逻辑 ...

    def _backup_to_file(self, content: Optional[str]):
        try:
            if content is not None:
                import base64
                encoded = base64.b64encode(content.encode('utf-8')).decode('ascii')
                with open(self._BACKUP_FILE, 'w', encoding='utf-8') as f:
                    f.write(encoded)
                self._set_file_private(self._BACKUP_FILE)
            elif os.path.exists(self._BACKUP_FILE):
                os.remove(self._BACKUP_FILE)
        except Exception:
            pass

    def _restore_from_file(self) -> Optional[str]:
        try:
            if os.path.exists(self._BACKUP_FILE):
                import base64
                with open(self._BACKUP_FILE, 'r', encoding='utf-8') as f:
                    return base64.b64decode(f.read()).decode('utf-8')
        except Exception:
            pass
        return None

    @staticmethod
    def _set_file_private(filepath: str):
        import platform, stat
        if platform.system() == 'Windows':
            try:
                import subprocess
                username = os.environ.get('USERNAME', '')
                if username:
                    subprocess.run(
                        ['icacls', filepath, '/inheritance:r',
                         f'/grant:r', f'{username}:R'],
                        capture_output=True, timeout=2
                    )
            except Exception:
                pass
        else:
            try:
                os.chmod(filepath, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass
```

**main.py** 启动时：检测残留备份文件则记录日志并清理。

---

### 3.3 提示音依赖修复

**问题**：依赖 scipy/soundfile 但不在 requirements 中。

**方案**：用标准库 `wave` + `struct` 替代。

```python
@staticmethod
def _load_wav_stdlib(path) -> tuple:
    import wave, struct
    import numpy as np
    with wave.open(str(path), 'rb') as wf:
        samplerate = wf.getframerate()
        n_frames = wf.getnframes()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(n_frames)

    if sampwidth == 2:
        fmt = f'<{n_frames * n_channels}h'
        samples = struct.unpack(fmt, raw)
        data = np.array(samples, dtype=np.float32) / 32768.0
    elif sampwidth == 4:
        fmt = f'<{n_frames * n_channels}i'
        samples = struct.unpack(fmt, raw)
        data = np.array(samples, dtype=np.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的采样宽度: {sampwidth}")

    if n_channels > 1:
        data = data[::n_channels]
    return (data, samplerate)
```

`_load_sound` 优先调用 `_load_wav_stdlib`，零外部依赖。

---

### 3.4 使用统计模块

**`core/stats.py`**：线程安全的轻量统计收集器。

关键设计点：
- `_lock` 保护所有 `_data` 读写
- `get_summary` 在锁内拷贝当天数据，锁外读历史文件
- 数据持久化到 `./stats/stats_YYYY-MM-DD.json`
- 跨天自动切换

```python
class UsageStats:
    def __init__(self, stats_dir: str = "./stats"):
        self._dir = Path(stats_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._today = date.today()
        self._data = self._load_today()

    def get_summary(self, days: int = 7) -> dict:
        with self._lock:
            self._check_date_rollover()
            today_data = dict(self._data)  # 锁内拷贝，避免不一致
        # 锁外读历史文件
        summaries = []
        for i in range(days):
            target = date.today() - timedelta(days=i)
            path = self._stats_file(target)
            if path.exists():
                try:
                    summaries.append(json.loads(path.read_text(encoding='utf-8')))
                except Exception:
                    pass
        summaries = [s for s in summaries if s.get("date") != self._today.isoformat()]
        summaries.append(today_data)
        # ... 汇总计算 ...
```

通过事件总线订阅，engine 无直接依赖。

#### EventBus handler 规范

- handler 中**禁止同步调用** engine 的任何方法（避免重入和死锁）
- handler 应为无状态的纯消费操作（写文件、更新 UI、记日志）
- 如需 engine 执行动作，应通过 `publish` 另一个事件间接触发

#### RMS 更新节流

silence_detector 的 RMS 回调频率由音频 chunk 决定（~30ms/次），
但 VAD 指示器刷新率仅需 20fps（50ms）。
在 silence_detector 中加节流：
```python
class SilenceDetector:
    def __init__(self, ...):
        self._last_rms_publish = 0.0
        self._rms_throttle_interval = 0.1  # 100ms 节流

    def _run(self):
        # ...
        rms = self._calculate_rms(chunk)
        if self._detecting and self._on_rms_update:
            now = time.monotonic()
            if now - self._last_rms_publish >= self._rms_throttle_interval:
                self._on_rms_update(rms, rms >= self.config.silence_threshold)
                self._last_rms_publish = now
```

---

### 3.5 STT 语言检测结果展示

**stt_engine.py** 返回值扩展为 `(text, language, duration_ms, error)`。

engine 的 `_on_stt_complete` 通过事件总线发布：
```python
self._events.publish(EngineEvent.TRANSCRIBE_COMPLETE, text, language, duration_ms)
```

tray 订阅后显示通知：
```python
def on_transcribe_complete(self, text, language, duration_ms):
    lang_display = f"[{language}]" if language else ""
    self.show_notification("已注入", f"{lang_display} {text[:30]}... {duration_ms}ms")
```

---

### 3.6 声卡设备选择

**config.py** — AudioConfig 新增：
```python
device: Optional[str] = None   # None=系统默认，或设备名/索引
```

**recorder.py**：
```python
def start(self, rt_queue=None):
    device = self.config.device
    if device is not None:
        try:
            device = int(device)
        except ValueError:
            pass
    self._stream = sd.InputStream(..., device=device)
```

**web_server.py** 新增 `GET /api/audio/devices` 返回输入设备列表。

设备热插拔：`_audio_callback` 中捕获 `sd.CallbackAbort`，发布错误事件。

---

### 3.7 热键冲突检测

```python
def register(self):
    import keyboard
    key = self.config.trigger
    try:
        if self.config.mode == "push_to_talk":
            keyboard.on_press_key(key, self._on_press)
            keyboard.on_release_key(key, self._on_release)
        else:
            keyboard.on_press_key(key, self._on_toggle)
    except keyboard.InvalidKeyError:
        return False, f"无效的热键: {key}"
    except Exception as e:
        return False, f"热键 {key} 注册失败（可能被占用）: {e}"
    return True, None
```

main.py 中注册失败时通过事件通知 tray 显示警告。旧热键通过 `keyboard.unhook_all()` 清理后再注册新键。

---

### 3.8 语音命令模式

**`core/command.py`**

#### 命令表设计

**核心原则**：
- 所有 pattern 使用完整字符串枚举（`|` 分隔）
- **禁止 `?` 量词**，杜绝空串/短词匹配
- `fullmatch` 全文精确匹配
- 最小 2 字符长度保护

```python
DEFAULT_COMMANDS = [
    # 编辑操作
    VoiceCommand("撤销", [r"撤销|撤回|删除上一句"],
                 CommandType.KEY_SEQUENCE, "ctrl+z", "撤销上一次输入"),
    VoiceCommand("换行", [r"换行|回车|下一行"],
                 CommandType.KEY_SEQUENCE, "enter", "插入换行"),
    VoiceCommand("退格", [r"退格|删掉"],
                 CommandType.KEY_SEQUENCE, "backspace", "删除一个字符"),
    VoiceCommand("全选", [r"全选"],
                 CommandType.KEY_SEQUENCE, "ctrl+a", "全选文本"),
    VoiceCommand("复制", [r"复制"],
                 CommandType.KEY_SEQUENCE, "ctrl+c", "复制选中文本"),
    VoiceCommand("粘贴", [r"粘贴"],
                 CommandType.KEY_SEQUENCE, "ctrl+v", "粘贴剪贴板"),
    # 引擎控制
    VoiceCommand("停止录音", [r"停止录音|停止转写"],
                 CommandType.ENGINE_ACTION, "stop", "停止当前录音"),
    # 标点符号
    VoiceCommand("句号", [r"句号"],
                 CommandType.TEXT_REPLACE, "。", "输入句号"),
    VoiceCommand("逗号", [r"逗号"],
                 CommandType.TEXT_REPLACE, "，", "输入逗号"),
    VoiceCommand("问号", [r"问号"],
                 CommandType.TEXT_REPLACE, "？", "输入问号"),
    VoiceCommand("感叹号", [r"感叹号|叹号"],
                 CommandType.TEXT_REPLACE, "！", "输入感叹号"),
    VoiceCommand("冒号", [r"冒号"],
                 CommandType.TEXT_REPLACE, "：", "输入冒号"),
]
```

#### 匹配逻辑

```python
def match(self, text: str) -> Optional[tuple]:
    cleaned = text.strip().strip("。，？！、：；""''")
    # 最小长度保护：单字/空串不匹配命令
    if len(cleaned) < 2:
        return None
    for i, cmd in enumerate(self._commands):
        for pattern in self._compiled[i]:
            m = pattern.fullmatch(cleaned)
            if m:
                return (cmd, m)
    return None
```

#### 命令执行线程安全

按键模拟通过 platform_adapter 层的 `create_key_simulator()` 执行，懒初始化避免重复创建：
```python
class CommandExecutor:
    def __init__(self, engine=None, injector=None):
        self._engine = engine
        self._injector = injector
        self._key_sim = None  # 懒初始化

    @property
    def key_simulator(self):
        if self._key_sim is None:
            from platform_adapter import create_key_simulator
            self._key_sim = create_key_simulator()
        return self._key_sim

def _execute_key_sequence(self, sequence: str) -> bool:
    self.key_simulator.send(sequence)
    return True
```

`platform_adapter/key_simulator.py` 封装 `keyboard.send()`，确保平台兼容。

#### 配置

```yaml
command:
  enabled: true
  custom_commands: []
  sound_feedback: true
```

engine 中命令匹配在 `_on_stt_complete` 的注入之前执行，通过事件总线发布 `COMMAND_EXECUTED`。

---

### 3.9 语音活动可视化（VAD 指示器）

**`gui/vad_indicator.py`** — 跨平台抽象

**`gui/vad_indicator_win32.py`** — Windows 实现

#### Win32 实现关键修复

1. **消息循环**：使用 `SetTimer` + `WM_TIMER` 驱动定期重绘，不依赖 `GetMessage` 阻塞间隙
2. **线程安全关闭**：`hide()` 通过 `PostMessage(WM_USER_CLOSE)` 通知创建线程销毁窗口
3. **GDI 资源管理**：`SelectObject` → 绘制 → 恢复旧 brush → `DeleteObject`
4. **刷新率**：50ms 定时器（20fps），避免高频 CPU 消耗
5. **WNDPROC 防 GC**：保存为实例属性 `self._wnd_proc_func`

```python
class Win32VADWindow:
    WM_USER_CLOSE = 0x0401
    TIMER_ID_REFRESH = 1
    TIMER_INTERVAL_MS = 50  # 20fps

    def hide(self):
        """异步关闭（线程安全）"""
        if self._hwnd:
            user32 = ctypes.windll.user32
            user32.PostMessageW(self._hwnd, self.WM_USER_CLOSE, 0, 0)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == 0x0113:  # WM_TIMER
            self._process_updates()
            user32.InvalidateRect(hwnd, None, True)
            return 0
        elif msg == self.WM_USER_CLOSE:
            user32.KillTimer(hwnd, self.TIMER_ID_REFRESH)
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == 0x0002:  # WM_DESTROY
            user32.KillTimer(hwnd, self.TIMER_ID_REFRESH)
            user32.PostQuitMessage(0)
            return 0
        elif msg == 0x000F:  # WM_PAINT
            self._on_paint(hwnd)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_paint(self, hwnd):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        ps = ctypes.wintypes.PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

        # BGR 颜色计算
        if self._is_speech:
            g_val = int(128 + 127 * self._level)
            brush_color = g_val << 8  # 绿色渐变
        elif self._level > 0.01:
            brush_color = 0xFFFF00  # 青色
        else:
            brush_color = 0x808080  # 灰色

        new_brush = gdi32.CreateSolidBrush(brush_color)
        old_brush = gdi32.SelectObject(hdc, new_brush)
        gdi32.Ellipse(hdc, 0, 0, self._size, self._size)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(new_brush)
        user32.EndPaint(hwnd, ctypes.byref(ps))
```

通过事件总线订阅 `RMS_UPDATE` / `RECORDING_STARTED` / `RECORDING_STOPPED`。

macOS 降级：`VADIndicator.show()` 记录 debug 日志后跳过。

---

## 四、配置版本迁移

v2 → v3：

```python
def _migrate_v2_to_v3(raw):
    raw["config_version"] = 3
    audio = raw.get("audio", {})
    if "device" not in audio:
        audio["device"] = None
    raw["audio"] = audio
    if "command" not in raw:
        raw["command"] = {"enabled": True, "custom_commands": [], "sound_feedback": True}
    return raw
```

`CURRENT_CONFIG_VERSION` → 3。

降级保护：保存配置前先备份 `config.yaml.bak`。

---

## 五、影响范围汇总

| 文件 | 改动类型 | 涉及变更 |
|------|---------|---------|
| `core/events.py` | **新增** | 事件总线 |
| `core/engine.py` | 修改 | 事件发布、Timer、回调签名 |
| `core/stt_engine.py` | 修改 | 返回值扩展 |
| `core/recorder.py` | 修改 | 设备选择 |
| `core/sound_player.py` | 修改 | wave 标准库 |
| `core/silence_detector.py` | 修改 | RMS 回调 |
| `core/stats.py` | **新增** | 使用统计 |
| `core/command.py` | **新增** | 语音命令 |
| `platform_adapter/clipboard_base.py` | 修改 | 加密文件备份 |
| `platform_adapter/key_simulator.py` | **新增** | 按键模拟封装 |
| `gui/tray.py` | 修改 | 事件驱动 |
| `gui/web_server.py` | 修改 | 新增 API |
| `gui/vad_indicator.py` | **新增** | VAD 抽象 |
| `gui/vad_indicator_win32.py` | **新增** | Win32 实现 |
| `main.py` | 修改 | 事件订阅、启动清理 |
| `config.py` | 修改 | v3 迁移、新增字段 |
| `gui/templates/config.html` | 修改 | 统计面板、设备选择 |

---

## 六、实施顺序

**第 1 批（基础设施，串行）**：
- 事件总线 events.py + engine 事件化改造
- 回调签名同步（stt_engine → engine）

**第 2 批（独立改动，可并行）**：
- 提示音修复（sound_player.py）
- 剪贴板备份（clipboard_base.py）
- 录音最大时长（engine.py Timer）
- 声卡设备（config + recorder + web_server）
- 热键冲突（hotkey）

**第 3 批（依赖事件总线，可并行）**：
- 使用统计（stats.py + web_server）
- STT 语言展示（stt_engine + tray）
- 命令模式（command.py + key_simulator）
- VAD 指示器（vad_indicator + silence_detector）

---

*文档版本: v3.1 | 评审历史: v1.0=72分, v2.0=79分, v3.0=85分 | 状态: 待评审*
