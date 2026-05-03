# 模式切换重启设计 — Mode Switch via App Restart (方案C)

> **版本**: v3.1  
> **日期**: 2026-05-03  
> **作者**: Saber  
> **状态**: Master 评审  
> **评分历程**: v1.0: 78 → v2.0: 88 → v3.0: 92/100 ✅

---

## 一、需求概述

### 背景

voice-input-tool 支持 batch（按热键录音→转写）和 realtime（持续转写）两种模式。通过托盘右键菜单切换时，需要同步切换引擎/模型配置（batch 用 mlx-whisper/faster-whisper，realtime 用 FunASR 流式）。

### 方案选择

| 方案 | 思路 | 结论 |
|------|------|------|
| B（热加载） | 销毁旧引擎→创建新引擎→加载模型 | 复杂度高，66/100 未通过评审 |
| **C（重启）** | 修改配置→保存→重启进程 | **简单可靠，风险极低** |

### 核心流程

```
用户点击"切换到实时转写"
  → 校验当前状态为 IDLE
  → 修改 config.mode = "realtime"
  → 保存 config.yaml（原子写入）
  → 显示通知"正在切换到实时转写模式，即将重启..."
  → 2.0s 后停止托盘（触发主循环退出）
  → main() 清理流程：engine.shutdown() → hotkey_manager.unregister()
  → finally: 写入 .restarting 标志文件 → Popen 新进程 → release 锁 → 退出
  → 新进程：检测到 .restarting 标志 → 显示"模式已切换"通知 → 删除标志文件 → 正常启动
```

---

## 二、关注点评估

### 2.1 SingleInstanceLock 竞态 ⚠️

**问题**：Windows Named Mutex (`Global\VoiceInputTool_SingleInstance`)。新进程可能在旧进程释放锁之前就尝试 acquire。

**解决方案**：**先 Popen → 再 release 锁**。新进程启动后尝试 acquire 锁时会被阻塞（Windows Named Mutex 是内核对象，等待释放），旧进程清理完成后释放锁，新进程立即获得。

```
时序：
1. 旧进程：Popen 新进程（新进程挂起等待 acquire lock）
2. 旧进程：engine.shutdown()（幂等，见 2.2）
3. 旧进程：instance_lock.release()
4. 新进程：acquire lock 成功 → 正常运行
```

**为何不能先 release 再 Popen**：release 和 Popen 之间有窗口期，第三方程序可能趁虚启动。

**为何安全**：`Popen` 在操作系统层面创建进程后立即返回，新进程的 `main()` 中 `acquire()` 会阻塞直到 Mutex 可用。Python 的 `subprocess.Popen` 不等子进程完成。

### 2.2 engine.shutdown() 幂等性 ⚠️ v2.0 新增

**问题**：v1.0 评审发现 `engine.shutdown()` 可能被调用两次（`_on_tray_switch_mode` 中 + `tray.run()` 返回后 main 清理逻辑中）。

**修复**：`CoreEngine.shutdown()` 开头加幂等保护：

```python
def shutdown(self):
    if self._shutdown_event.is_set():
        logger.debug("shutdown 已执行，跳过重复调用")
        return
    logger.info("CoreEngine 开始关闭...")
    self._shutdown_event.set()
    # ... 后续清理 ...
```

**说明**：`_on_tray_switch_mode` 中 **不调用** `engine.shutdown()`。只设置标志 + 延迟停止托盘。`engine.shutdown()` 统一在 `tray.run()` 返回后的 main 清理流程中调用（恰好一次）。

### 2.3 pystray 主循环退出

`tray.run()` 是阻塞调用。模式切换在托盘菜单回调中触发：

```python
回调中 → 设置 _restart_pending Event → 通知用户 → 延迟 2.0s → tray.stop()
                                                          ↓
tray.run() 返回 → engine.shutdown()（恰好一次）→ hotkey_manager.unregister()
                                                          ↓
finally: _restart_pending 检测 → Popen → release lock → exit
```

### 2.4 配置保存路径

- 打包后：`USER_DATA_DIR / "config.yaml"`
- 开发环境：`PROJECT_ROOT / "config.yaml"`

`save_config()` 已有原子写入（tmp → replace）。

### 2.5 热键默认值 F8 → F9

修改 `HotkeyConfig.trigger` 默认值。已有配置文件不受影响。

### 2.6 stt_realtime 配置节

batch 和 realtime 模式独立的 STT 配置。新增 `stt_realtime` 配置节。

### 2.7 配置版本迁移

v9 → v10：新增 `stt_realtime` 节。

### 2.8 重启标志文件 .restarting ⭐ v2.0 新增

**问题**：v1.0 中用户看到托盘消失 3-8 秒（模型加载），无任何反馈。

**方案**：旧进程退出前在 `USER_DATA_DIR` 写入 `.restarting` 文件（内容为新模式名称），新进程启动时检测到此文件 → 显示"模式已切换"通知 → 删除文件。

```python
# 旧进程退出前
RESTART_FLAG = USER_DATA_DIR / ".restarting"
with open(RESTART_FLAG, "w") as f:
    f.write(new_mode)

# 新进程启动时（main() 最前面）
RESTART_FLAG = USER_DATA_DIR / ".restarting"
if RESTART_FLAG.exists():
    mode_name = RESTART_FLAG.read_text().strip()
    # 延迟到 tray 可用后显示通知
    mode_label = "实时转写" if mode_name == "realtime" else "批量录音"
    logger.info("检测到重启标志：从模式切换重启，新模式: %s", mode_label)
    RESTART_FLAG.unlink(missing_ok=True)
```

### 2.9 错误场景

| 场景 | 处理 |
|------|------|
| 配置保存失败 | 捕获异常，通知"配置保存失败"，不重启，`_restart_pending` 不设置 |
| 新进程启动失败 | 旧进程已退出，用户需手动启动；日志记录 Popen 命令便于排查 |
| 新进程模型加载失败 | 正常进入 ERROR 状态，可通过托盘"重试" |
| 快速连续点击"切换" | `Event.is_set()` 防重入 + 非IDLE 状态菜单禁用 |
| .restarting 文件残留 | 新进程删除；如手动 crash 残留，只产生一条额外通知，无害 |

### 2.10 日志设计

```
--- 旧进程 ---
INFO  模式切换: batch → realtime
INFO  配置已保存: /path/to/config.yaml
INFO  模式切换：通知用户后将在 2.0s 后停止托盘
INFO  CoreEngine 开始关闭...
INFO  CoreEngine 关闭完成
INFO  写入重启标志: /path/to/.restarting (realtime)
INFO  模式切换：启动新进程 ['/path/to/exe']
INFO  释放单实例锁

--- 新进程 ---
INFO  语音输入工具启动
INFO  检测到重启标志：从模式切换重启，新模式: realtime
INFO  加载配置: /path/to/config.yaml
INFO  模式: realtime
INFO  使用流式转写模式 (FunASR streaming)
INFO  通知: 模式已切换 - 已切换到实时转写模式
```

---

## 三、详细设计

### 3.1 配置变更 (config.py)

#### 新增 STTRealtimeConfig

```python
@dataclass
class STTRealtimeConfig:
    """realtime 模式专用 STT 配置
    
    缺失字段 fallback 到 stt 配置的 resolve() 方法处理。
    """
    engine: str = "funasr"
    model_size: str = "paraformer-zh-streaming"
    language: Optional[str] = None   # None = 从 stt 继承
    device: str = ""                 # 空字符串 = 从 stt 继承
    compute_type: str = ""
    hf_endpoint: str = ""
    modelscope_endpoint: str = ""
    streaming: Optional[StreamingConfig] = None  # None = 使用默认（enabled=True）

    def resolve(self, fallback_stt: STTConfig) -> STTConfig:
        """解析为完整 STTConfig，缺失字段从 fallback_stt 继承。
        
        统一使用 None / 空字符串 作为 fallback 触发条件，
        避免 language 用 is not None 而 device 用 != "auto" 的不一致。
        """
        return STTConfig(
            engine=self.engine,
            model_size=self.model_size,
            language=self.language if self.language is not None else fallback_stt.language,
            device=self.device if self.device else fallback_stt.device,
            compute_type=self.compute_type if self.compute_type else fallback_stt.compute_type,
            hf_endpoint=self.hf_endpoint if self.hf_endpoint else fallback_stt.hf_endpoint,
            modelscope_endpoint=self.modelscope_endpoint if self.modelscope_endpoint else fallback_stt.modelscope_endpoint,
            streaming=self.streaming if self.streaming is not None else StreamingConfig(enabled=True),
            beam_size=fallback_stt.beam_size,
            max_new_tokens=fallback_stt.max_new_tokens,
            model_path=fallback_stt.model_path,
        )
```

#### AppConfig 新增字段

```python
@dataclass
class AppConfig:
    config_version: int = 10  # 9 → 10
    mode: str = "batch"
    stt: STTConfig = field(default_factory=STTConfig)
    stt_realtime: STTRealtimeConfig = field(default_factory=STTRealtimeConfig)  # 新增
    # ... 其他不变
```

#### 注册表 + 迁移

```python
_SUB_CONFIG_TYPES["stt_realtime"] = STTRealtimeConfig

CONFIG_MIGRATIONS[9] = _migrate_v9_to_v10

def _migrate_v9_to_v10(raw):
    raw["config_version"] = 10
    if "stt_realtime" not in raw:
        raw["stt_realtime"] = {
            "engine": "funasr",
            "model_size": "paraformer-zh-streaming",
            "streaming": {"enabled": True}
        }
    return raw
```

#### HotkeyConfig 默认值

```python
@dataclass
class HotkeyConfig:
    trigger: str = "f9"  # 原 "f8"
    mode: str = "toggle"
    conflict_check: bool = True
```

### 3.2 CoreEngine 改造 (core/engine.py)

#### shutdown() 幂等保护

```python
def shutdown(self):
    # 幂等保护：防止多次调用
    if self._shutdown_event.is_set():
        logger.debug("shutdown 已执行，跳过重复调用")
        return
    logger.info("CoreEngine 开始关闭...")
    self._shutdown_event.set()
    # ... 后续清理不变 ...
```

#### 引擎选择逻辑抽取

将 `__init__` 中的引擎创建逻辑（约 70 行 if/elif 分支）抽取为 `_create_stt_engine(stt_config)` 方法。

```python
def __init__(self, config, tray, on_shutdown_complete=None):
    self._config = config
    self._tray = tray
    self._on_shutdown_complete = on_shutdown_complete
    # ... 其他状态初始化不变 ...

    # 根据模式选择 STT 配置
    if config.mode == "realtime" and hasattr(config, 'stt_realtime'):
        stt_config = config.stt_realtime.resolve(config.stt)
        logger.info("realtime 模式：使用 stt_realtime 配置 (engine=%s, model=%s)",
                    stt_config.engine, stt_config.model_size)
    else:
        stt_config = config.stt

    self._create_stt_engine(stt_config)

    # ... recorder, silence_detector, injector 等后续初始化不变 ...


def _create_stt_engine(self, stt_config):
    """根据 STT 配置创建引擎和转写器（从 __init__ 抽取）"""
    stt_engine_type = stt_config.engine

    # auto 模式检测逻辑（与原 __init__ 一致）
    if stt_engine_type == "auto":
        import platform
        if platform.system() == "Darwin":
            stt_engine_type = "mlx_whisper"
        else:
            whisper_sizes = ('tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo')
            if stt_config.model_size in whisper_sizes:
                stt_engine_type = "faster_whisper"
            else:
                stt_engine_type = "funasr"
        logger.info("auto 模式：选择 %s 引擎", stt_engine_type)

    streaming_enabled = getattr(stt_config.streaming, 'enabled', False) if \
        stt_engine_type in ('funasr', 'mlx_whisper') else False

    if streaming_enabled and stt_engine_type == 'funasr':
        from core.stt_funasr_streaming import FunASRStreamingEngine
        from core.streaming_transcriber import StreamingTranscriber
        self._stt_engine = FunASRStreamingEngine(stt_config)
        self._stream_transcriber = StreamingTranscriber(stt_config.streaming)
        self._streaming_mode = True
        logger.info("使用流式转写模式 (FunASR streaming)")
    elif stt_engine_type == 'funasr':
        from core.stt_funasr import FunASREngine
        from core.vad_segment_transcriber import VADSegmentTranscriber
        self._stt_engine = FunASREngine(stt_config)
        self._stream_transcriber = VADSegmentTranscriber(
            self._config.realtime, self._stt_engine, self._on_realtime_segment)
        self._streaming_mode = False
        logger.info("使用VAD分段转写模式 (FunASR)")
    elif stt_engine_type == 'mlx_whisper':
        from core.stt_mlx_whisper import MlxWhisperEngine
        from core.vad_segment_transcriber import VADSegmentTranscriber
        self._stt_engine = MlxWhisperEngine(stt_config)
        self._stream_transcriber = VADSegmentTranscriber(
            self._config.realtime, self._stt_engine, self._on_realtime_segment)
        self._streaming_mode = False
        logger.info("使用VAD分段转写模式 (mlx-whisper)")
    elif stt_engine_type == 'qwen3_asr':
        from core.stt_qwen3_asr import Qwen3ASREngine
        from core.vad_segment_transcriber import VADSegmentTranscriber
        self._stt_engine = Qwen3ASREngine(stt_config)
        self._stream_transcriber = VADSegmentTranscriber(
            self._config.realtime, self._stt_engine, self._on_realtime_segment)
        self._streaming_mode = False
        logger.info("使用VAD分段转写模式 (Qwen3-ASR)")
    else:
        from core.stt_engine import STTEngine
        from core.vad_segment_transcriber import VADSegmentTranscriber
        self._stt_engine = STTEngine(stt_config)
        self._stream_transcriber = VADSegmentTranscriber(
            self._config.realtime, self._stt_engine, self._on_realtime_segment)
        self._streaming_mode = False
        logger.info("使用VAD分段转写模式 (faster-whisper)")

    # 音频队列初始化
    max_queue_size = getattr(stt_config.streaming, 'max_queue_size', 300) if self._streaming_mode else 300
    self._rt_audio_queue = None
    self._rt_max_queue_size = max_queue_size
```

### 3.3 main.py 模式切换 (main.py)

```python
# 模块级
import threading
_restart_event = threading.Event()  # 替代 bool 标志，线程安全且语义更清晰

def main():
    global _restart_event
    
    # ... 现有初始化（步骤 0-5）...
    
    # 检查重启标志文件（必须在配置加载后、引擎创建前）
    RESTART_FLAG = USER_DATA_DIR / ".restarting"
    restart_from_switch = False
    restart_mode_label = ""
    if RESTART_FLAG.exists():
        try:
            mode_name = RESTART_FLAG.read_text().strip()
            restart_mode_label = "实时转写" if mode_name == "realtime" else "批量录音"
            logger.info("检测到重启标志：从模式切换重启，新模式: %s", mode_name)
            RESTART_FLAG.unlink(missing_ok=True)
            restart_from_switch = True
        except Exception as e:
            logger.warning("处理重启标志失败: %s", e)
    
    # ... 现有初始化（步骤 6-9）...
    
    def _on_tray_switch_mode():
        """托盘菜单切换模式（重启方案）"""
        # 防重入 + 显式状态校验（菜单 enabled 可能被绕过）
        if _restart_event.is_set():
            logger.warning("模式切换：已在重启流程中，忽略重复请求")
            return
        if engine.state != EngineState.IDLE:
            logger.warning("模式切换：当前状态 %s，仅 IDLE 状态可切换", engine.state.name)
            if tray:
                tray.show_notification("切换失败", "当前状态不允许切换模式")
            return
        
        current = config.mode
        new_mode = "realtime" if current == "batch" else "batch"
        
        # 保存配置
        config.mode = new_mode
        try:
            from config import save_config
            save_config(config_path, config)
        except Exception as e:
            logger.error("模式切换：配置保存失败: %s", e)
            # 回滚内存中的 mode，保持与磁盘一致
            config.mode = current
            if tray:
                tray.set_mode(current)
                tray.show_notification("切换失败", f"配置保存失败: {e}")
            return
        
        logger.info("模式切换: %s → %s，准备重启", current, new_mode)
        _restart_event.set()
        
        # 通知用户
        mode_label = "实时转写" if new_mode == "realtime" else "批量录音"
        if tray:
            tray.show_notification("模式切换", f"正在切换到{mode_label}模式，即将重启...")
        
        # 延迟 2.0s 后写入标志文件 + 停止托盘（让通知显示出来）
        def _deferred_stop():
            import time
            time.sleep(2.0)
            # 写入重启标志文件（在托盘停止前，但仍在旧进程存活期间）
            try:
                RESTART_FLAG.write_text(new_mode)
                logger.info("写入重启标志: %s (%s)", RESTART_FLAG, new_mode)
            except Exception as e:
                logger.warning("写入重启标志失败: %s（不影响重启）", e)
            logger.info("模式切换：停止托盘，触发主循环退出")
            tray.stop()
        threading.Thread(target=_deferred_stop, daemon=True, name="restart-delay").start()
    
    # ... 现有 tray/engine 初始化 ...
    tray = TrayIcon(
        on_start=_on_tray_start_stop,
        on_stop=_on_tray_start_stop,
        on_settings=_on_tray_settings,
        on_quit=_on_tray_quit,
        on_retry=_on_tray_retry_model,
        on_switch_mode=_on_tray_switch_mode,  # 使用新回调
    )
    
    # 设置初始模式
    tray.set_mode(config.mode)
    
    # 10-12. 现有：启动 web_server、加载模型、注册热键、进入主循环
    # ...
    
    # 如果是模式切换重启，显示"已切换"通知
    if restart_from_switch and tray:
        # 通过 EventBus 监听 IDLE 状态变化，模型加载完成后通知
        def _on_state_changed(old_state, new_state):
            from core.engine import EngineState
            if new_state == EngineState.IDLE:
                tray.show_notification("模式已切换", f"已切换到{restart_mode_label}模式")
                engine.events.unsubscribe(EngineEvent.STATE_CHANGED, _on_state_changed)
        from core.events import EngineEvent
        engine.events.subscribe(EngineEvent.STATE_CHANGED, _on_state_changed)
    
    tray.run()
    
    # 主循环退出后清理（tray.run() 返回）
    engine.shutdown()  # 幂等，安全
    if hotkey_manager:
        try:
            hotkey_manager.unregister()
        except Exception:
            pass
    
    # 以下代码在 finally 之前、正常退出路径执行
    if _restart_event.is_set():
        import subprocess
        # 先 Popen（新进程会阻塞在 acquire lock）
        cmd = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable] + sys.argv
        logger.info("模式切换：启动新进程 %s", cmd)
        try:
            subprocess.Popen(cmd)
        except Exception as e:
            logger.critical("模式切换：启动新进程失败: %s", e)
            _restart_event.clear()  # 回退到正常退出流程
        else:
            # Popen 成功：释放锁，新进程被唤醒
            instance_lock.release()

    # finally 块（已有）
    # instance_lock.release() 需要判断是否已释放

```

**finally 块调整**：

```python
    finally:
        if not _restart_event.is_set():
            # 正常退出 或 Popen 失败回退：释放锁
            instance_lock.release()
        logger.info("语音输入工具已退出")
        logging.shutdown()
```

### 3.4 托盘菜单状态保护 (gui/tray.py)

```python
def _build_menu(self):
    import pystray
    
    is_recording = self._state in (EngineState.RECORDING, EngineState.STREAMING)
    is_error = self._state == EngineState.ERROR
    is_loading = self._state == EngineState.LOADING
    is_idle = self._state == EngineState.IDLE

    items = []

    # 开始/停止（与现有逻辑一致）
    if self._state == EngineState.STREAMING:
        items.append(pystray.MenuItem("⏹ 停止转写", self._on_stop, default=False))
    elif self._state == EngineState.RECORDING:
        items.append(pystray.MenuItem("⏹ 停止录音", self._on_stop, default=False))
    elif not is_loading and not is_error:
        if self._current_mode == "realtime":
            items.append(pystray.MenuItem("📝 开始转写", self._on_start, default=False))
        else:
            items.append(pystray.MenuItem("🎤 开始录音", self._on_start, default=False))

    # 模式切换：仅在 IDLE 状态可用 ⭐ v2.0 修改
    if not is_recording and not is_loading:
        mode_label = "📝 切换到实时转写" if self._current_mode == "batch" else "🎤 切换到批量录音"
        items.append(pystray.MenuItem(
            mode_label,
            self._on_switch_mode_clicked,
            default=False,
            enabled=is_idle  # 仅 IDLE 可点击
        ))

    items.append(pystray.Menu.SEPARATOR)
    
    # ... 其他菜单项不变 ...
```

---

## 四、影响范围

### 修改文件清单

| 文件 | 改动量 | 改动内容 |
|------|--------|---------|
| `config.py` | ~70行 | 新增 `STTRealtimeConfig`（含 resolve），`AppConfig` 加字段，v9→v10 迁移，`HotkeyConfig.trigger` 改 f9，`_SUB_CONFIG_TYPES` 注册 |
| `core/engine.py` | ~80行（净增 ~10行） | `shutdown()` 幂等保护；引擎创建逻辑抽取为 `_create_stt_engine()`；`__init__` 根据 mode 选择 stt 配置 |
| `main.py` | ~45行 | 新增 `_restart_event`、重启标志文件检测、重写 `_on_tray_switch_mode()`、finally 块重启逻辑 |
| `gui/tray.py` | ~8行 | 模式切换菜单项加 `enabled=is_idle` 保护 |

**总计净增约 133 行，涉及 4 个文件。**

### 不影响的文件

- `core/stt_*.py` — 各引擎实现完全不变
- `core/streaming_transcriber.py` / `vad_segment_transcriber.py` — 不变
- `core/recorder.py` / `injector.py` / `silence_detector.py` — 不变
- `gui/web_server.py` — 不变
- `core/hotword.py` / `core/text_pipeline.py` / `core/punctuation.py` — 不变

---

## 五、测试要点

| # | 测试场景 | 预期结果 |
|---|---------|---------|
| 1 | batch IDLE → 点击切换 | 通知显示 → 2s 后托盘消失 → 新进程启动 → 显示"已切换到实时转写" → FunASR 流式引擎加载 |
| 2 | realtime → 切回 batch | 同上反向 |
| 3 | RECORDING 中点击切换 | 菜单项灰色/不可点击 |
| 4 | STREAMING 中点击切换 | 同上 |
| 5 | LOADING 中点击切换 | 同上 |
| 6 | 配置保存失败 | 通知"切换失败"，不重启，`_restart_event` 未设置 |
| 7 | v9 配置自动迁移到 v10 | 自动添加 `stt_realtime` 节 |
| 8 | 打包 exe 模式下切换 | 正确重启 exe |
| 9 | macOS 切换（无单实例锁）| 正常重启 |
| 10 | 连续快速双击切换 | `Event.is_set()` 防重入，第二次忽略 |
| 11 | `.restarting` 文件残留 | 新进程删除 + 显示通知，无害 |
| 12 | `engine.shutdown()` 被调用两次 | 幂等保护，第二次跳过 |
| 13 | `STTRealtimeConfig.resolve()` fallback | 缺失字段正确继承自 `stt` |
| 14 | `Popen` 失败（如 exe 路径无效） | Event 被 clear → 正常退出释放锁 → 日志记录错误 |
| 15 | 状态非 IDLE 时点击切换 | 显式校验拒绝 + 通知 |

---

## 六、风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| SingleInstanceLock 竞态 | 低 | 先 Popen（新进程等锁）→ 再 release（唤醒）|
| engine.shutdown() 重复调用 | 已消除 | 幂等保护 + 统一调用点 |
| 新进程启动失败 | 低 | Popen try/except + `_restart_event.clear()` 回退正常退出；日志记录命令 |
| 通知来不及显示 | 低 | 2.0s 延迟；`.restarting` 标志让新进程也发通知 |
| 用户数据丢失 | 无 | IDLE 状态才允许切换，无进行中的操作 |
| `.restarting` 文件残留 | 低 | 新进程启动时删除；crash 残留只产生额外通知 |

---

## 附录A：评审修复记录

### v1.0 → v2.0（78 → 88/100）

| # | 问题 | 严重度 | 修复方式 |
|---|------|--------|---------|
| 1 | engine.shutdown() 被调用两次 | P0 | 幂等保护 + 统一调用点（只在 main 清理流程中） |
| 2 | SingleInstanceLock 释放时序 | P1 | 先 Popen → 再 release（新进程等锁被唤醒） |
| 3 | 打包 exe 启动慢导致锁等待 | P1 | 上述时序天然解决 |
| 4 | 托盘图标消失无反馈 | P1 | `.restarting` 标志文件 + 新进程通知 |
| 5 | 切换失败静默消失 | P1 | 日志记录 Popen 命令；配置保存失败不触发重启 |
| 6 | resolve() fallback 逻辑不一致 | P1 | 统一用 `None / 空字符串` 作为 fallback 触发条件 |
| 7 | `_restart_pending` bool 非线程安全 | P2 | 改为 `threading.Event` |
| 8 | 通知延迟 1.5s 可能不够 | P2 | 改为 2.0s |
| 9 | `nonlocal config_path` 多余 | P2 | 删除 |

### v2.0 → v3.0（88 → 92/100）

| # | 问题 | 严重度 | 修复方式 |
|---|------|--------|---------|
| 1 | Popen 失败时锁状态不安全 | P0 | try/except 包裹 Popen，失败时 clear Event + 正常退出释放锁 |
| 2 | AppConfig.hotword 类型错误（已有 bug） | P0 | 修复为 `HotwordConfig` |
| 3 | _on_tray_switch_mode 缺少显式状态校验 | P1 | 增加 `engine.state != IDLE` 检查 + 失败通知 |
| 4 | .restarting 文件写入时序偏早 | P1 | 移入 _deferred_stop，托盘停止前写入 |

### v3.0 → v3.1（92 → Master 评审）

| # | 问题 | 严重度 | 修复方式 |
|---|------|--------|---------|
| 1 | 配置保存失败后 config.mode 未回滚 | P1 | save 失败时回滚 `config.mode = current` + `tray.set_mode(current)` |
| 2 | _notify_switch_done 3s 硬编码延迟 | P2 | 改为 EventBus 监听 IDLE 状态变化，事件驱动 |
| 3 | 命名不一致 _restart_pending vs _restart_event | P2 | 统一为 `_restart_event` |

---

*文档状态: v3.1，Master 评审*
