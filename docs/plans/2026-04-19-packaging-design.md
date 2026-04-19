# 语音输入工具打包方案设计

> **版本**: v1.1 (修复P0问题)
> **日期**: 2026-04-19
> **设计者**: Saber
> **状态**: 待评审

---

## 一、设计目标

### 1.1 核心目标

将语音输入工具打包为可执行文件，用户无需安装 Python 和依赖，直接运行即可。

### 1.2 约束条件

| 约束 | 说明 |
|------|------|
| **目标平台** | Windows + macOS（Linux 不考虑） |
| **模型处理** | 首次启动下载，默认 small 模型（~150MB） |
| **打包工具** | PyInstaller |
| **打包模式** | 目录模式（启动快，配置可替换） |
| **exe 大小** | 约 50MB（不含模型） |

---

## 二、整体架构

### 2.1 打包流程

```
voice-input-tool/
├── src/                    # 源代码（待调整）
├── config.yaml             # 默认配置
├── build/                  # 构建脚本
│   ├── build_windows.bat   # Windows 打包脚本
│   ├── build_macos.sh      # macOS 打包脚本
│   └── voice-input-tool.spec  # PyInstaller 配置
├── dist/                   # 输出目录
│   ├── windows/
│   │   └── VoiceInputTool/   # Windows 发布包
│   │       ├── VoiceInputTool.exe
│   │       ├── config.yaml
│   │       └── _internal/    # 依赖库
│   └── macos/
│       └── VoiceInputTool.app  # macOS app bundle
```

### 2.2 发布包结构

用户下载后得到：
- **Windows**: `VoiceInputTool-Windows.zip` (~50MB)
- **macOS**: `VoiceInputTool-macOS.zip` (~60MB)

解压后直接运行，无需安装。

---

## 三、关键技术方案

### 3.1 PyInstaller 配置

#### 统一 spec 文件（修复后）

```python
# voice-input-tool.spec
import sys
import os

block_cipher = None

# ========== 核心依赖（必须） ==========
hiddenimports = [
    # faster-whisper 全链路
    'faster_whisper',
    'faster_whisper.transcribe',
    'faster_whisper.audio',
    'faster_whisper.feature_extractor',
    'faster_whisper.tokenizer',
    
    # ctranslate2（faster-whisper 后端）
    'ctranslate2',
    'ctranslate2.translator',
    'ctranslate2.models',
    
    # 音频处理
    'sounddevice',
    '_sounddevice',      # C 扩展，必须显式声明
    'soundfile',
    '_soundfile',        # C 扩展
    'numpy',
    
    # 配置
    'yaml',
    'yaml.loader',
    'yaml.dumper',
    'yaml.representer',
    
    # 系统托盘
    'pystray',
    'pystray._win32',    # Windows 后端
    'pystray._darwin',   # macOS 后端
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    
    # OpenCC 繁简转换
    'opencc',
    'opencc_clib',       # C 扩展
    
    # Web 配置页
    'webbrowser',
    'http.server',
    'socketserver',
    'threading',
    'queue',
    
    # 事件系统
    'concurrent.futures',
    'concurrent.futures.thread',
    
    # VAD
    'webrtcvad',
    '_webrtcvad',        # C 扩展
    
    # HuggingFace 模型下载
    'huggingface_hub',
    'huggingface_hub.file_download',
    'huggingface_hub.repository',
    
    # ========== 动态导入（platform_adapter） ==========
    'platform_adapter',
    'platform_adapter.hotkey_windows',
    'platform_adapter.hotkey_macos',
    'platform_adapter.clipboard_windows',
    'platform_adapter.clipboard_macos',
    'platform_adapter.key_simulator',
]

# ========== 平台特定依赖 ==========
if sys.platform == 'win32':
    hiddenimports.extend([
        'keyboard',           # 热键库（必须！）
        'keyboard._keyboard_event',
        'win32api',
        'win32con',
        'pywintypes',
        'ctypes',
        'ctypes.wintypes',
    ])
else:  # macOS
    hiddenimports.extend([
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._darwin',
        'pynput.mouse',
        'pynput.mouse._darwin',
        'objc',
        'Foundation',
        'AppKit',
    ])

# datas（避免通配符，整目录复制）
datas = [
    ('config.yaml', '.'),
    ('gui/templates', 'gui/templates'),
    ('gui/static', 'gui/static'),
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['build/hooks'],
    runtime_hooks=['build/hooks/runtime_hook.py'],
)

pyz = PYZ(a.pure, cipher=block_cipher)

# UPX 可用性检测
use_upx = os.system('upx --version') == 0 if os.name != 'posix' else False

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoiceInputTool',
    debug=False,
    strip=False,
    upx=use_upx,
    console=False,
    icon='build/icon.ico' if sys.platform == 'win32' else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=use_upx,
    name='VoiceInputTool',
)

# macOS BUNDLE（添加权限声明）
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='VoiceInputTool.app',
        icon='build/icon.icns',
        bundle_identifier='com.voiceinputtool.app',
        version='1.0.0',
        info_plist={
            'NSMicrophoneUsageDescription': '语音输入工具需要麦克风权限进行语音识别',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '10.13.0',
        },
    )
```

### 3.2 模型下载机制

#### 首次启动流程

```
1. 检查 models/ 目录是否存在模型
2. 不存在 → 弹窗提示"正在下载模型..."
3. 使用 faster-whisper 内置下载逻辑
4. 下载完成 → 加载模型
5. 更新 config.yaml 记录模型路径
```

#### 模型切换流程

```
1. 用户在 Web 配置页选择新模型
2. 重启程序
3. 检查新模型是否存在
4. 不存在 → 下载
5. 加载新模型
```

### 3.3 配置文件处理

打包后的配置文件路径：
- **Windows**: `%APPDATA%/VoiceInputTool/config.yaml`
- **macOS**: `~/Library/Application Support/VoiceInputTool/config.yaml`

首次启动时，将 `config.yaml` 从 exe 目录复制到用户目录（保留默认配置）。

### 3.4 日志文件路径

- **Windows**: `%APPDATA%/VoiceInputTool/logs/`
- **macOS**: `~/Library/Application Support/VoiceInputTool/logs/`

---

## 四、构建流程

### 4.1 Windows 构建

```batch
@echo off
:: build/build_windows.bat

echo Installing PyInstaller...
pip install pyinstaller

echo Building Windows package...
pyinstaller voice-input-tool.spec --noconfirm

echo Creating release package...
cd dist\windows
7z a VoiceInputTool-Windows.zip VoiceInputTool

echo Done!
```

### 4.2 macOS 构建

```bash
#!/bin/bash
# build/build_macos.sh

echo "Installing PyInstaller..."
pip install pyinstaller

echo "Building macOS package..."
pyinstaller voice-input-tool-macos.spec --noconfirm

echo "Creating release package..."
cd dist/macos
zip -r VoiceInputTool-macOS.zip VoiceInputTool.app

echo "Done!"
```

### 4.3 GitHub Actions 自动构建

```yaml
# .github/workflows/release.yml
name: Build and Release

on:
  push:
    tags:
      - 'v*'

jobs:
  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.13'
      - run: pip install -r requirements.txt
      - run: pip install pyinstaller
      - run: pyinstaller voice-input-tool.spec --noconfirm
      - uses: actions/upload-artifact@v3
        with:
          name: VoiceInputTool-Windows
          path: dist/windows/VoiceInputTool/

  build-macos:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.13'
      - run: pip install -r requirements.txt
      - run: pip install pyinstaller
      - run: pyinstaller voice-input-tool-macos.spec --noconfirm
      - uses: actions/upload-artifact@v3
        with:
          name: VoiceInputTool-macOS
          path: dist/macos/VoiceInputTool.app

  release:
    needs: [build-windows, build-macos]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v3
      - uses: softprops/action-gh-release@v1
        with:
          files: |
            VoiceInputTool-Windows/*
            VoiceInputTool-macOS/*
```

---

## 五、特殊处理

### 5.1 依赖打包问题

#### 问题：faster-whisper 模型文件

faster-whisper 模型不打包进 exe，首次启动时下载。

**处理方式**：
- 配置 `download_root` 为用户目录
- 首次启动检测模型是否存在
- 提供下载进度 UI（Web 配置页或托盘通知）

#### 问题：PyInstaller hiddenimports

某些包 PyInstaller 无法自动检测，需要手动添加：

```python
hiddenimports = [
    'faster_whisper',
    'faster_whisper.transcribe',
    'pynput.keyboard',
    'pynput.mouse',
    'sounddevice',
    'soundfile',
    'pyyaml',
    'webbrowser',
    'threading',
    'queue',
    # Windows
    'win32api',
    'win32con',
    'pywintypes',
    # macOS
    'objc',
    'Foundation',
]
```

### 5.2 平台特定代码

#### Windows 热键

使用 `pynput` + `win32api`，需确保打包后正常工作。

#### macOS 权限

macOS 需要申请：
- 输入监控权限（Accessibility）
- 麦克风权限

打包后首次运行会弹出系统权限请求。

### 5.3 运行时钩子

```python
# build/hooks/runtime_hook.py
import sys
import os

# 设置工作目录为 exe 所在目录
if getattr(sys, 'frozen', False):
    # 打包后运行
    exe_dir = os.path.dirname(sys.executable)
    os.chdir(exe_dir)
    
    # 设置用户数据目录
    if sys.platform == 'win32':
        data_dir = os.path.join(os.environ['APPDATA'], 'VoiceInputTool')
    else:
        data_dir = os.path.expanduser('~/.local/share/VoiceInputTool')
    
    os.makedirs(data_dir, exist_ok=True)
```

---

## 六、测试计划

### 6.1 打包后测试清单

| # | 测试项 | Windows | macOS |
|---|--------|---------|-------|
| 1 | 程序启动 | ✅ | ✅ |
| 2 | 热键触发 | ✅ | ✅ |
| 3 | 录音功能 | ✅ | ✅ |
| 4 | STT 转文字 | ✅ | ✅ |
| 5 | 文字注入 | ✅ | ✅ |
| 6 | Web 配置页 | ✅ | ✅ |
| 7 | 模型切换 | ✅ | ✅ |
| 8 | 首次启动下载模型 | ✅ | ✅ |
| 9 | 配置持久化 | ✅ | ✅ |
| 10 | 日志记录 | ✅ | ✅ |

### 6.2 兼容性测试

- Windows 10 / Windows 11
- macOS 12+ (Intel + Apple Silicon)

---

## 七、待确认问题

### 7.1 源代码结构调整

当前代码在项目根目录，建议调整为：

```
voice-input-tool/
├── src/
│   ├── core/           # 核心模块
│   ├── gui/            # GUI 模块
│   ├── platform_adapter/  # 平台适配
│   └── main.py         # 入口
├── config.yaml
├── requirements.txt
└── build/
```

是否需要调整？

### 7.2 FunASR 引擎打包

**必须打包 FunASR**（Master 明确要求）。

#### 依赖问题解决方案

FunASR 依赖 `editdistance` 需要编译，Windows 无预编译 wheel。

**方案**：利用 GitHub Actions Windows runner（自带 VC++ Build Tools）编译 wheel。

```yaml
# 在 build_windows job 中添加
- name: Build editdistance wheel
  run: |
    pip wheel editdistance --wheel-dir wheels --no-deps
    pip install wheels/editdistance*.whl
```

编译后的 wheel 会打包进 exe。

#### FunASR hiddenimports 补充

```python
# 在 hiddenimports 中添加
'funasr',
'funasr.auto',
'funasr.models',
'modelscope',
'torch',
'onnx',
'onnxruntime',
```

**注意**：FunASR + torch 会使 exe 增大约 200MB。

### 7.4 keyboard 库依赖

**确认**：项目实际使用 `keyboard` 库（用于热键验证）。

已在 `requirements_windows.txt` 中添加：
```
keyboard>=0.13.5
```

注意：Windows 上需要管理员权限才能使用 keyboard 库。

### 7.3 图标和品牌

需要设计：
- Windows icon (.ico)
- macOS icon (.icns)
- 应用名称

由 Master 决定。

---

## 八、风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| PyInstaller 兼容性 | 打包失败 | 提前测试，添加 hiddenimports |
| macOS 权限问题 | 用户无法使用 | 文档说明，首次启动提示 |
| 模型下载失败 | 无法识别 | 提供手动下载指南，重试机制 |
| exe 过大 | 下载慢 | 优化依赖，移除不必要的包 |
| 杀毒软件误报 | 用户不敢用 | 申请白名单，文档说明 |

---

## 九、实施计划

### Phase 1：基础打包（Windows）

1. 创建 build 目录和 spec 文件
2. 配置 PyInstaller
3. 测试打包
4. 修复打包问题

### Phase 2：macOS 打包

1. 创建 macOS spec 文件
2. 处理权限问题
3. 测试打包

### Phase 3：自动化构建

1. 配置 GitHub Actions
2. 测试自动发布

### Phase 4：文档完善

1. 用户手册
2. 安装指南
3. 常见问题

---

## 十、评审记录

### 第1轮评审（2026-04-19）

- **评审角色**：资深 Python 打包工程师
- **评审方式**：子代理评审
- **综合评分**：85/100
- **主要问题**：
  - P0-1: hiddenimports 大量遗漏（已修复）
  - P0-2: macOS 缺少 Info.plist 权限声明（已修复）
  - P0-3: keyboard 库依赖缺失（已确认并补充）
- **修复状态**：已修复 P0 问题，待第2轮评审确认

### 第2轮评审（2026-04-19）

- **评审角色**：资深 Python 打包工程师
- **评审方式**：子代理评审
- **综合评分**：90/100 ✅ 通过
- **P0问题核查**：
  - P0-1: hiddenimports 遗漏 → ✅ 已修复
  - P0-2: macOS 权限声明 → ✅ 已修复
  - P0-3: keyboard 库依赖 → ✅ 已确认
- **新发现问题**（P1/P2，不阻塞实施）：
  - P1: spec 文件描述不一致（文档与代码）
  - P2: UPX 检测逻辑优化
  - P2: 用户数据目录路径不一致
  - P2: macOS 缺少 entitlements 文件
- **评审结论**：通过，可进入人工评审阶段

---

*设计完成，等待第2轮评审*