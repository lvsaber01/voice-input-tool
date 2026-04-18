# 🎙️ 语音输入工具（跨平台版）

**离线语音输入工具**。按住快捷键说话，松开后文字自动输入到当前光标位置。支持中英文混合自动检测。

现已支持 **Windows** 和 **macOS** 双平台！

## 功能特性

| 功能 | Windows | macOS |
|------|---------|-------|
| 批量录音模式 | ✅ | ✅ |
| 实时转写模式 | ✅ | ✅ |
| 全局热键（F8） | ✅ keyboard | ✅ pynput |
| 剪贴板注入 | ✅ Win32 API | ✅ pbcopy + osascript |
| 静音检测自动停止 | ✅ | ✅ |
| 系统托盘图标 | ✅ | ✅ |
| Web 配置页面 | ✅ | ✅ |
| 中英文自动检测 | ✅ | ✅ |

### 两种工作模式

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| **批量模式** | 录音 → 整段转写 → 一次注入 | 语音输入（原有功能） |
| **实时转写模式** | 持续监听 → 分段转写 → 逐段追加 | 会议记录、访谈、课堂笔记 |

---

## 系统要求

### Windows

| 要求 | 说明 |
|------|------|
| 操作系统 | Windows 10 1903+ / Windows 11 |
| 权限 | **管理员权限**（全局热键需要） |
| 运行时 | VC++ 2015-2022 Runtime（[下载](https://aka.ms/vs/17/release/vc_redist.x64.exe)） |
| 内存 | ≥2GB 可用（small 模型运行需要） |
| 麦克风 | 系统已识别的录音设备 |

### macOS

| 要求 | 说明 |
|------|------|
| 操作系统 | macOS 12+ (Monterey) |
| 权限 | **辅助功能权限**（System Preferences → Privacy → Accessibility） |
| 运行时 | Python 3.11+（推荐 3.11/3.12） |
| 内存 | ≥2GB 可用（small 模型运行需要） |
| 麦克风 | 系统已识别的录音设备 |

---

## 快速开始

### Windows 安装

#### 1. 解压
将 ZIP 包解压到任意目录。

#### 2. 环境准备
右键 `setup.bat` → **以管理员身份运行**。脚本会自动：
- 检查 VC++ Runtime
- 配置 Python 环境
- 安装依赖
- 下载 STT 模型（约 500MB，首次需要几分钟）

#### 3. 启动
右键 `run.bat` → **以管理员身份运行**。程序会最小化到系统托盘。

### macOS 安装

#### 1. 克隆或下载
```bash
git clone https://github.com/lvsaber01/voice-input-tool.git
cd voice-input-tool
```

#### 2. 安装 Python 3.11（推荐）
```bash
brew install python@3.11
```

#### 3. 创建虚拟环境并安装依赖
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements_macos.txt
```

#### 4. 下载 STT 模型
```bash
python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', download_root='./models')"
```

#### 5. 授予辅助功能权限
1. 系统偏好设置 → 安全性与隐私 → 隐私 → 辅助功能
2. 添加 Terminal（或你使用的终端应用）到允许列表
3. **重要**：首次运行时会有权限提示，必须允许

#### 6. 启动
```bash
python3 main.py
```

---

## 配置说明

配置文件：`config.yaml`（首次运行自动生成）

### 工作模式配置

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `mode` | `batch` | 工作模式：`batch`（批量）或 `realtime`（实时转写） |

### 实时转写配置 (realtime)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `segment_pause_threshold` | `0.8` | 静音多久视为句子边界（秒） |
| `min_segment_duration` | `0.3` | 最短语音段（秒），低于此跳过 |
| `max_segment_duration` | `30.0` | 最长语音段（秒），超长强制切分 |
| `vad_sensitivity` | `2` | VAD 灵敏度（0-3，越高越严格） |
| `segment_separator` | `\n` | 段落分隔符（`\n`=换行，` `=空格） |
| `auto_timestamp` | `false` | 是否在每段前加时间戳 |

### 快捷键配置 (hotkey)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `trigger` | `f8` | 触发键 |
| `mode` | `toggle` | 热键模式：`toggle`（开关）或 `push_to_talk`（按住） |
| `conflict_check` | `true` | 启动时检测快捷键冲突 |

### 语音识别配置 (stt)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `model_size` | `small` | 模型大小：tiny/base/small/medium/large-v3 |
| `model_path` | `./models/` | 模型目录路径 |
| `language` | `null` | 语言（null=自动检测，zh=中文，en=英文） |
| `device` | `auto` | 计算设备：auto/cpu/cuda |
| `compute_type` | `int8` | 计算精度 |
| `beam_size` | `5` | 搜索宽度 |

### 音频配置 (audio)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `max_duration` | `120` | 最大录音时长（秒） |
| `silence_timeout` | `8` | 静音自动停止（秒），0=关闭 |
| `silence_threshold` | `0.01` | 静音检测阈值 |

### 注入配置 (inject)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `auto_paste` | `true` | 自动模拟粘贴 |
| `clipboard_backup` | `true` | 注入前备份剪贴板 |
| `clipboard_restore` | `true` | 注入后恢复原剪贴板 |

---

## 快捷键使用

### Toggle 模式（默认）

1. 按 **F8** 开始录音/转写（图标变色 + 提示音）
2. 对着麦克风说话
3. 再按 **F8** 停止，自动识别并输入文字

### PTT（按住说话）模式

1. **按住** F8 开始录音
2. 说话...
3. **松开** F8 停止录音，自动识别并输入文字

### 模式切换

右键托盘图标 → 选择 **切换到实时转写** 或 **切换到批量录音**

---

## 实时转写模式使用

### 启动实时转写

1. 托盘菜单 → **切换到实时转写**
2. 按 **F8** 开始监听（托盘图标变蓝）
3. 对着麦克风说话，每说完一句话（停顿 > 0.8s）自动转写并注入
4. 按 **F8** 停止监听

### 注意事项

- **延迟**：约 1-3 秒（非真正流式识别，是分段转写）
- **剪贴板占用**：每次注入占用剪贴板约 300ms，避免同时复制操作
- **光标位置**：假设光标在文档末尾，建议在转写期间不要移动光标

---

## Web 配置页面

程序运行后，右键托盘图标 → **打开设置**，浏览器自动打开配置页面。

也可手动访问：`http://localhost:18921`

---

## 常见问题

### Windows 管理员权限

**问题**：程序提示"需要管理员权限"

**解决**：右键 `run.bat` → "以管理员身份运行"。keyboard 库的全局热键功能需要管理员权限。

### macOS 辅助功能权限

**问题**：热键不响应，托盘菜单正常工作但按 F8 无反应

**解决**：
1. 系统偏好设置 → 安全性与隐私 → 隐私 → 辅助功能
2. 添加运行程序的终端（Terminal.app）到允许列表
3. 需要重启程序生效

### macOS Python 版本问题

**问题**：`pyobjc-core` 编译失败或 `pkg_resources` 找不到

**解决**：
- 使用 Python 3.11 或 3.12（不要用系统 Python 3.9）
- 固定 setuptools 版本：`pip install 'setuptools<81'`

### 麦克风权限

**问题**：录音失败或"麦克风不可用"

**解决**：
- Windows：设置 → 隐私 → 麦克风 → 允许应用访问
- macOS：系统偏好设置 → 安全性与隐私 → 隐私 → 麦克风

### 模型加载失败

**问题**：托盘图标显示错误，提示模型加载失败

**解决**：
1. 运行模型下载脚本
2. 确认 `models/` 目录中有模型文件
3. 托盘菜单 → "重试加载模型"

---

## 已知限制

### 跨平台通用

- **窗口切换**：录音过程中切换窗口，文字会注入到当前焦点窗口
- **UAC 权限弹窗**：系统级弹窗上，自动粘贴可能失效，文字保留在剪贴板

### macOS 特定

- **pynput 稳定性**：macOS 12+ 上 Listener 可能无故停止，已通过 watchdog 自动重连机制缓解
- **osascript 限制**：某些全屏应用（游戏、Final Cut Pro）中模拟 Cmd+V 可能失败
- **实时转写剪贴板冲突**：注入期间短暂占用剪贴板（~300ms）

### Windows 特定

- **必须管理员权限**：非管理员运行时热键无法注册
- **keyboard 库独占**：某些应用（如远程桌面）可能拦截全局热键

---

## 项目结构

```
voice-input-tool/
├── main.py              # 程序入口
├── config.py            # 配置管理（含 RealtimeConfig）
├── config.yaml          # 用户配置文件
├── core/                # 核心模块
│   ├── engine.py        # 状态机引擎（含 STREAMING 状态）
│   ├── hotkey.py        # 热键委托层
│   ├── recorder.py      # 音频录制（支持 rt_queue）
│   ├── silence_detector.py  # 静音检测
│   ├── stt_engine.py    # STT 引擎（含 transcribe_sync）
│   ├── injector.py      # 注入委托层
│   ├── stream_transcriber.py  # 实时转写引擎
│   └── sound_player.py  # 提示音
├── platform_adapter/    # 平台抽象层
│   ├── __init__.py      # 工厂 + 注册表
│   ├── hotkey_base.py   # 热键抽象基类
│   ├── hotkey_windows.py # Windows 实现（keyboard）
│   ├── hotkey_macos.py  # macOS 实现（pynput + watchdog）
│   ├── clipboard_base.py # 注入抽象基类
│   ├── clipboard_windows.py # Windows 实现（Win32 API）
│   └── clipboard_macos.py  # macOS 实现（pbcopy + osascript）
├── gui/                 # 界面模块
│   ├── tray.py          # 系统托盘（含模式切换菜单）
│   └── web_server.py    # Web 配置服务
├── scripts/             # 工具脚本
├── models/              # STT 模型
├── requirements.txt     # 跨平台基础依赖
├── requirements_windows.txt # Windows 特定依赖
├── requirements_macos.txt   # macOS 特定依赖
└── test_e2e.py          # 端到端测试脚本
```

---

## 技术栈

### 跨平台通用

- **STT**: faster-whisper + CTranslate2（本地离线）
- **音频**: sounddevice + numpy
- **托盘**: pystray + Pillow
- **配置**: YAML + dataclass

### Windows 特定

- **热键**: keyboard 库（LowLevelKeyboardHook）
- **注入**: Win32 Clipboard API + SendInput（ctypes）

### macOS 特定

- **热键**: pynput（Accessibility API） + watchdog 自动重连
- **注入**: pbcopy + osascript 模拟 Cmd+V + pyautogui 回退
- **VAD**: webrtcvad（实时转写模式）

---

## 开发与测试

### 运行端到端测试

```bash
python3 test_e2e.py
```

测试覆盖：
- STT 引擎（模型加载、转写）
- 剪贴板注入（读写验证）
- StreamTranscriber（VAD + 分段）
- CoreEngine 状态机（STREAMING 转换）

---

## 许可证

MIT License

---

*语音输入工具 v2.0 — 跨平台支持 + 实时转写模式*