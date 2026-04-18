# 🎙️ 语音输入工具

Windows 平台离线语音输入工具。按住快捷键说话，松开后文字自动输入到当前光标位置。支持中英文混合自动检测。

## 系统要求

| 要求 | 说明 |
|------|------|
| 操作系统 | Windows 10 1903+ / Windows 11 |
| 权限 | **管理员权限**（全局热键需要） |
| 运行时 | VC++ 2015-2022 Runtime（[下载](https://aka.ms/vs/17/release/vc_redist.x64.exe)） |
| 内存 | ≥2GB 可用（small 模型运行需要） |
| 麦克风 | 系统已识别的录音设备 |

## 快速开始

### 1. 解压

将 ZIP 包解压到任意目录。

### 2. 环境准备

右键 `setup.bat` → **以管理员身份运行**。脚本会自动：
- 检查 VC++ Runtime
- 配置 Python 环境
- 安装依赖
- 下载 STT 模型（约 500MB，首次需要几分钟）

### 3. 启动

右键 `run.bat` → **以管理员身份运行**。程序会最小化到系统托盘。

## 配置说明

配置文件：`config.yaml`（首次运行自动生成）

### 快捷键配置 (hotkey)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `trigger` | `f8` | 触发键，支持 keyboard 库的所有键名 |
| `mode` | `toggle` | 模式：`toggle`（开关切换）或 `push_to_talk`（按住说话） |
| `conflict_check` | `true` | 启动时检测快捷键冲突 |

### 语音识别配置 (stt)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `model_size` | `small` | 模型大小：tiny/base/small/medium/large-v3 |
| `model_path` | `./models/` | 模型目录路径 |
| `language` | `null` | 语言（null=自动检测，zh=中文，en=英文） |
| `device` | `auto` | 计算设备：auto/cpu/cuda |
| `compute_type` | `int8` | 计算精度：int8/float16 |
| `beam_size` | `5` | 搜索宽度 |

> ⚠️ 修改 model_size、device 需要重启程序

### 音频配置 (audio)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `max_duration` | `120` | 最大录音时长（秒） |
| `silence_timeout` | `8` | 静音自动停止（秒），0=关闭 |
| `silence_threshold` | `0.01` | 静音检测阈值 |
| `silence_check_interval` | `0.5` | 静音检测间隔（秒） |

### 注入配置 (inject)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `auto_paste` | `true` | 自动模拟 Ctrl+V 粘贴 |
| `clipboard_backup` | `true` | 注入前备份剪贴板 |
| `clipboard_restore` | `true` | 注入后恢复原剪贴板内容 |
| `add_trailing_space` | `false` | 注入文字末尾追加空格 |
| `paste_delay_ms` | `100` | 写入剪贴板到模拟粘贴的延迟（毫秒） |

### 提示音配置 (sound)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 启用提示音 |
| `volume` | `0.5` | 音量 (0~1) |
| `start_sound` | `true` | 录音开始提示音 |
| `end_sound` | `true` | 录音结束提示音 |
| `complete_sound` | `true` | 识别完成提示音 |

### Web 配置 (web)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `port` | `18921` | Web 配置页端口 |

## 快捷键使用

### Toggle 模式（默认）

1. 按 **F8** 开始录音（图标变红 + 提示音）
2. 对着麦克风说话
3. 再按 **F8** 停止录音，自动识别并输入文字
4. 也可以等静音超时自动停止（默认 8 秒无声音）

### PTT（按住说话）模式

1. **按住** F8 开始录音
2. 说话...
3. **松开** F8 停止录音，自动识别并输入文字

## Web 配置页面

程序运行后，右键托盘图标 → **打开设置**，会打开浏览器配置页面。

也可手动访问：`http://localhost:18921`（需要 token，通过托盘菜单获取）

配置页面支持：
- 快捷键设置和冲突检测
- 模型选择（需重启）
- 音频参数调整
- 提示音开关和试听
- 注入选项配置

## 模型替换

如需更换模型：

1. 下载 CTranslate2 格式模型：
   ```bash
   python\python.exe scripts\download_model.py --size base
   ```
   可选大小：tiny / base / small / medium / large-v3

2. 修改 `config.yaml` 中 `stt.model_size` 为对应名称

3. 重启程序

| 模型 | 大小 | 内存 | 速度 | 中文质量 |
|------|------|------|------|---------|
| tiny | ~75MB | ~150MB | 最快 | 一般 |
| base | ~150MB | ~250MB | 快 | 可用 |
| **small** | **~500MB** | **~500MB** | **中等** | **好（推荐）** |
| medium | ~1.5GB | ~1.5GB | 慢 | 很好 |
| large-v3 | ~3GB | ~3GB | 最慢 | 最佳 |

## 常见问题

### 管理员权限

**问题**：程序提示"需要管理员权限"

**解决**：右键 `run.bat` → "以管理员身份运行"。keyboard 库的全局热键功能需要管理员权限。

### 麦克风权限

**问题**：录音失败或"麦克风不可用"

**解决**：
1. 确认麦克风已连接并在系统中被识别
2. Windows 设置 → 隐私 → 麦克风 → 允许应用访问麦克风

### 端口冲突

**问题**：Web 配置页面无法访问

**解决**：默认端口 18921 可能被占用。修改 `config.yaml` 中 `web.port` 为其他端口，重启程序。

### 模型加载失败

**问题**：托盘图标显示错误，提示模型加载失败

**解决**：
1. 运行 `python\python.exe scripts\download_model.py` 下载模型
2. 确认 `models/small/` 目录中有模型文件
3. 通过托盘菜单"重试加载模型"

### VC++ Runtime 缺失

**问题**：程序启动后立即崩溃

**解决**：下载安装 [VC++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)

## 已知限制

- **窗口切换**：如果录音过程中切换了窗口，文字会注入到当前焦点窗口而非录音时的窗口
- **UAC 窗口**：在系统级弹窗（如 UAC 提示）上方，自动粘贴可能失效，文字会保留在剪贴板供手动粘贴
- **仅支持 Windows**：macOS/Linux 下可运行但热键功能不可用

## 项目结构

```
voice-input-tool/
├── main.py              # 程序入口
├── config.py            # 配置管理
├── config.yaml          # 用户配置文件
├── core/                # 核心模块
│   ├── engine.py        # 状态机引擎
│   ├── hotkey.py        # 全局热键
│   ├── recorder.py      # 音频录制
│   ├── silence_detector.py  # 静音检测
│   ├── stt_engine.py    # 语音识别
│   ├── injector.py      # 文字注入
│   └── sound_player.py  # 提示音
├── gui/                 # 界面模块
│   ├── tray.py          # 系统托盘
│   ├── web_server.py    # Web 配置服务
│   └── templates/       # HTML 模板
├── scripts/             # 工具脚本
│   ├── run.bat          # 一键启动
│   ├── setup.bat        # 环境准备
│   └── download_model.py # 模型下载
├── assets/              # 资源文件
├── models/              # STT 模型
├── python/              # Embeddable Python
└── requirements.txt     # Python 依赖
```

## 技术栈

- **STT**: faster-whisper + CTranslate2（本地离线，无需联网）
- **热键**: keyboard 库（Windows 全局钩子）
- **音频**: sounddevice + numpy
- **托盘**: pystray + Pillow
- **注入**: Win32 Clipboard API + SendInput（ctypes，无第三方依赖）
- **配置**: YAML + dataclass

---

*语音输入工具 v1.0 — 为 vibe-coding 场景设计*
