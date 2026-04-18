# 实施计划 — 跨平台适配 + 实时转写

## 总览

基于现有 Windows-only 代码库（2303行），按 DESIGN_CROSSPLATFORM.md 实施。

## 遗留小问题（编码时处理）

- [ ] `transcribe_sync` 补充接口签名（stt_engine.py）
- [ ] `config.yaml` 示例补充 `vad_window_ms`
- [ ] 超短段硬编码 `4800` → 用 `min_segment_duration` 配置
- [ ] mode 热更新失败行为说明
- [ ] pyautogui 权限依赖说明

## Phase 1: 平台抽象层（基础架构）

### 1.1 创建目录结构
```
platform_adapter/
├── __init__.py          # 工厂 + 注册表
├── hotkey_base.py       # 热键抽象基类
├── hotkey_windows.py    # 从 core/hotkey.py 迁移
├── hotkey_macos.py      # 新建
├── clipboard_base.py    # 注入抽象基类
├── clipboard_windows.py # 从 core/injector.py 迁移
├── clipboard_macos.py   # 新建
```

### 1.2 抽象基类
- `HotkeyManagerBase`: register/unregister/rebind + on_start/on_stop/on_toggle
- `ClipboardInjectorBase`: inject（模板方法）+ write_clipboard/simulate_paste/read_clipboard

### 1.3 Windows 实现（迁移）
- `hotkey_windows.py`: 从 core/hotkey.py 迁移，改名为 `WindowsHotkeyManager`
- `clipboard_windows.py`: 从 core/injector.py 迁移，改名为 `WindowsClipboardInjector`

### 1.4 macOS 实现（新建）
- `hotkey_macos.py`: pynput + watchdog + AXIsProcessTrusted
- `clipboard_macos.py`: pbcopy + osascript + pyautogui fallback

### 1.5 委托薄封装
- `core/hotkey.py` → 委托到 `platform_adapter.create_hotkey_manager()`
- `core/injector.py` → 委托到 `platform_adapter.create_clipboard_injector()`

### 1.6 工厂 + 注册表
- `platform_adapter/__init__.py`: CURRENT_PLATFORM + 注册表 + 延迟导入

## Phase 2: 引擎扩展（实时转写）

### 2.1 新增模块
- `core/stream_transcriber.py`: StreamTranscriber + webrtcvad + inject_queue

### 2.2 引擎修改
- `core/engine.py`: 新增 STREAMING 状态 + 状态转移扩展
- `config.py`: 新增 RealtimeConfig + mode 字段

### 2.3 STTEngine 扩展
- `core/stt_engine.py`: 新增 `transcribe_sync()` 方法

## Phase 3: UI 扩展

### 3.1 托盘菜单
- 新增"模式切换"菜单项
- 新增 STREAMING 状态图标

### 3.2 Web 配置页
- 新增模式切换 UI
- 新增实时转写参数配置

### 3.3 配置文件
- 新增 realtime 节配置项
- requirements 拆分（base/windows/macos）

## Phase 4: 集成测试 + 打包

- CI 更新
- README 更新
- 跨平台打包脚本

## 执行顺序

按 Phase 1 → 2 → 3 → 4 顺序。每个 Phase 内按子任务编号执行。
预计使用 Claude Code 子代理实现，分 3-4 个 session 完成。
