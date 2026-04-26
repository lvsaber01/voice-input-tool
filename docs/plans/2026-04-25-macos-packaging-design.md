# macOS .app 打包方案设计

> **版本**: v1.3 (第3轮评审修订)
> **日期**: 2026-04-25
> **设计者**: Saber
> **状态**: 待评审
> **前置文档**: `docs/plans/2026-04-19-packaging-design.md`
> **评审历程**: v1.0=62 → v1.1=82 → v1.2=88 → v1.3=待评

---

## 一、设计目标

| 项目 | 说明 |
|------|------|
| **目标** | 将 voice-input-tool 打包为 macOS .app，新机免安装直接运行 |
| **分发方式** | 内部使用，unsigned .app + DMG 分发 |
| **功能范围** | 完整功能：热键录音、STT、键盘注入、系统托盘、Web 配置页 |
| **STT 引擎** | faster-whisper（内置）+ FunASR（运行时可选安装） |
| **打包工具** | PyInstaller（复用现有 spec 架构） |
| **目标芯片** | Apple Silicon (arm64) only |
| **最低 macOS** | 12.0 (Monterey) |

---

## 二、整体架构

### 2.1 .app 目录结构（PyInstaller BUNDLE 实际结构）

```
VoiceInputTool.app/
└── Contents/
    ├── Info.plist                  # App 元信息 + 权限声明
    ├── PkgInfo                     # "APPL????"（PyInstaller 自动生成）
    ├── MacOS/
    │   ├── VoiceInputTool          # 主可执行文件
    │   └── python3.11/             # sys._MEIPASS 指向此处
    │       ├── base_library.zip
    │       ├── collections/
    │       ├── ctypes/
    │       ├── ...                 # 所有 Python 依赖
    │       ├── config.yaml         # 默认配置
    │       ├── assets/             # 提示音等资源
    │       └── gui/templates/      # Web 配置页模板
    └── Resources/
        └── icon.icns               # App 图标
```

> ⚠️ **重要**: PyInstaller BUNDLE 模式下，所有依赖通过 `sys._MEIPASS` 访问，路径为 `Contents/MacOS/python3.11/`。`Contents/Resources/` 仅放图标等非代码资源。日志等用户数据**禁止写入 .app 内部**（macOS 将 .app 视为只读）。

### 2.2 用户数据目录

```
~/Library/Application Support/VoiceInputTool/
├── config.yaml          # 用户配置（覆盖默认）
├── models/              # STT 模型（首次下载）
└── funasr_env/          # FunASR 运行时环境（可选安装）

~/Library/Logs/VoiceInputTool/
└── voice_input_tool.log  # 运行日志
```

### 2.3 STT 引擎架构

```
┌─────────────────────────────────────────┐
│              VoiceInputTool.app          │
│                                          │
│  ┌──────────────┐  ┌──────────────────┐  │
│  │ faster-whisper│  │    FunASR        │  │
│  │   (内置)     │  │ (运行时可选安装)  │  │
│  │              │  │                  │  │
│  │ 模型:首次下载 │  │ pip install 到   │  │
│  │ ~150-500MB   │  │ funasr_env/      │  │
│  └──────────────┘  └──────────────────┘  │
│         │                  │             │
│         └──────┬───────────┘             │
│                ▼                         │
│         CoreEngine STT 调度              │
└─────────────────────────────────────────┘
```

**FunASR 运行时安装流程**:
1. 用户在 Web 配置页选择 FunASR 引擎
2. 检测 `~/Library/Application Support/VoiceInputTool/funasr_env/` 是否存在
3. 不存在 → 执行 `python3 -m venv funasr_env && funasr_env/bin/pip install funasr modelscope torch`
4. 下载 FunASR 模型到 models/
5. 子进程调用 `funasr_env/bin/python` 加载模型
6. 通过 stdin/stdout/pipe 与主进程通信

> **为什么不打包 FunASR**: torch CPU-only arm64 ~200MB，FunASR + modelscope 依赖链 ~150MB，合计增加 ~350MB 且 PyInstaller 对 torch 的 hiddenimports 极难穷举。运行时安装体积可控、依赖完整。

### 2.4 用户首次使用流程

```
1. 打开 DMG → 拷贝 VoiceInputTool.app 到 /Applications/
2. 首次双击 → macOS "无法验证开发者"
   → 解决：在终端执行 xattr -cr /Applications/VoiceInputTool.app
   （DMG 内附 README 说明）
3. 首次启动 → 弹出麦克风权限请求 → 允许
4. 系统设置 → 隐私与安全性 → 辅助功能 → 添加 VoiceInputTool
5. 系统设置 → 隐私与安全性 → 输入监控 → 添加 VoiceInputTool（macOS 14+）
6. 托盘出现图标 → 首次使用 faster-whisper 会自动下载模型
7. 如需 FunASR → 在 Web 配置页一键安装（需联网，约 5-10 分钟）
8. 按 F8 开始使用
```

### 2.5 文件大小预估

| 组件 | 预估大小 | 说明 |
|------|----------|------|
| Python 运行时 + 基础依赖 | ~80MB | PyInstaller base |
| faster-whisper + ctranslate2 | ~30MB | 核心推理引擎 |
| numpy / audio / tray / web | ~30MB | sounddevice, pystray, etc. |
| pynput + pyobjc | ~15MB | 热键 + macOS API |
| opencc | ~5MB | 繁简转换 |
| **.app 合计（不含模型）** | **~160MB** | |
| STT 模型（首次下载） | ~150-500MB | 用户自选 |
| FunASR 运行时（可选） | ~350MB | 用户主动安装 |

---

## 三、关键挑战与解决方案

### 3.1 macOS 14+ 权限体系

macOS 14 (Sonoma) 及以上需要**三个独立权限**：

| 权限 | Info.plist Key | 用户操作 | 代码检测方式 |
|------|----------------|----------|-------------|
| 麦克风 | `NSMicrophoneUsageDescription` | 弹窗授权 | `AVFoundation` 请求 |
| 辅助功能 | 无（系统级） | 手动添加 | `AXIsProcessTrusted()` |
| 输入监控 | 无（系统级） | 手动添加 | `CGEventTapCreate()` 返回 NULL |
| Apple Events | `NSAppleEventsUsageDescription` | 弹窗授权 | osascript 调用时触发 |

**启动时权限检查流程**:
```python
def check_permissions():
    # 1. 麦克风 — 首次自动弹窗，后续静默
    request_microphone()
    
    # 2. 辅助功能 — 检查并引导用户
    if not AXIsProcessTrusted():
        show_dialog("请在 系统设置 → 隐私 → 辅助功能 中添加本应用")
    
    # 3. 输入监控 (macOS 14+) — pynput 热键需要
    if not check_input_monitoring():
        show_dialog("请在 系统设置 → 隐私 → 输入监控 中添加本应用")
    
    # 4. Apple Events — osascript 键盘注入需要
    # Info.plist 声明后首次调用自动弹窗
```

### 3.2 macOS 键盘注入方案

**架构决策**: macOS 上不实现 KeySimulator 的逐字符模拟，统一使用 clipboard 方案：

```
用户录音 → STT 识别 → 文本写入剪贴板 → Cmd+V 模拟粘贴
```

**原因**:
- KeySimulator 的逐字符模拟需要逐字符调用 `CGEventPost`，中文输入法下不可靠
- clipboard + paste 方案在 macOS 上表现稳定（`pbcopy` + `CGEventPost(kCGEventKeyDown, Cmd+V)`）
- macOS SIP 不影响此方案（不涉及内核级注入）
- Web 配置页不需要修改，`inject_method` 字段统一为 `clipboard`

**实现**: `platform_adapter/key_simulator.py` 中 `create_key_simulator()` 在 macOS 上返回 `MacClipboardInjector`，复用 `clipboard_macos.py` 的 pbcopy + CGEvent 粘贴逻辑。

### 3.3 pynput 打包

**hiddenimports（完整）**:
```python
'pynput', 'pynput.keyboard', 'pynput.keyboard._darwin',
'pynput.mouse', 'pynput.mouse._darwin',
'pynput._util', 'pynput._util.darwin',
'pyobjc', 'objc', 'Foundation', 'AppKit', 'Quartz',
'Quartz.CoreGraphics', 'Quartz.QuartzCore',
'CoreFoundation', 'ApplicationServices',
```

### 3.4 Gatekeeper 处理

**DMG 分发方案**:
```bash
# 制作 DMG
hdiutil create -volname "VoiceInputTool" \
  -srcfolder "dist/VoiceInputTool.app" \
  -ov -format UDZO \
  "dist/VoiceInputTool-macOS.dmg"
```

**DMG 内包含**:
- `VoiceInputTool.app`
- `README.txt`（权限设置指南 + 首次使用说明）

**README.txt 内容**:
```
Voice Input Tool — macOS 版

首次使用步骤：
1. 拷贝 VoiceInputTool.app 到应用程序文件夹
2. 在终端执行: xattr -cr /Applications/VoiceInputTool.app
3. 双击启动，按提示授权麦克风
4. 系统设置 → 隐私 → 辅助功能 → 添加 VoiceInputTool
5. 系统设置 → 隐私 → 输入监控 → 添加 VoiceInputTool (macOS 14+)
```

---

## 四、构建流程

### 4.1 构建脚本: `build/build_macos.sh`

```bash
#!/bin/bash
set -e

echo "=== VoiceInputTool macOS Build ==="
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# 1. 环境检查
echo "[1/6] Checking build environment..."
python3 --version
xcode-select -p || { echo "ERROR: Xcode CLI Tools required. Run: xcode-select --install"; exit 1; }

# 2. 创建虚拟环境
echo "[2/6] Setting up venv..."
rm -rf .venv_build
python3 -m venv .venv_build
source .venv_build/bin/activate
pip install --upgrade pip setuptools wheel

# 3. 安装依赖（不含 FunASR，FunASR 运行时安装）
echo "[3/6] Installing dependencies..."
pip install -r requirements.txt
pip install -r requirements_macos.txt
pip install pyinstaller

# 4. PyInstaller 打包
echo "[4/6] Building .app with PyInstaller..."
pyinstaller voice-input-tool-mac.spec --clean --noconfirm

# 5. 清理 Gatekeeper 隔离标记
echo "[5/6] Clearing quarantine..."
xattr -cr dist/VoiceInputTool.app 2>/dev/null || true

# 6. 制作 DMG
echo "[6/6] Creating DMG..."
hdiutil create -volname "VoiceInputTool" \
  -srcfolder "dist/VoiceInputTool.app" \
  -ov -format UDZO \
  "dist/VoiceInputTool-macOS-arm64.dmg"

echo ""
echo "=== Build complete ==="
echo "App:  dist/VoiceInputTool.app ($(du -sh dist/VoiceInputTool.app | cut -f1))"
echo "DMG:  dist/VoiceInputTool-macOS-arm64.dmg ($(du -sh dist/VoiceInputTool-macOS-arm64.dmg | cut -f1))"
```

### 4.2 macOS 专用 spec 文件: `voice-input-tool-mac.spec`

```python
# voice-input-tool-mac.spec
# PyInstaller 配置文件 - macOS 版本
# 版本: v1.1

import sys
import os

block_cipher = None

# ========== hiddenimports（完整列表） ==========

hiddenimports = [
    # faster-whisper 全链路
    'faster_whisper', 'faster_whisper.transcribe',
    'faster_whisper.audio', 'faster_whisper.feature_extractor',
    'faster_whisper.tokenizer',
    
    # ctranslate2
    'ctranslate2', 'ctranslate2.translator', 'ctranslate2.models',
    
    # 音频处理
    'sounddevice', '_sounddevice', 'soundfile', '_soundfile',
    'numpy',
    
    # 配置
    'yaml', 'yaml.loader', 'yaml.dumper', 'yaml.representer',
    
    # 系统托盘
    'pystray', 'pystray._darwin', 'PIL', 'PIL.Image', 'PIL.ImageDraw',
    
    # macOS 热键 + 权限
    'pynput', 'pynput.keyboard', 'pynput.keyboard._darwin',
    'pynput.mouse', 'pynput.mouse._darwin',
    'pynput._util', 'pynput._util.darwin',
    'pyobjc', 'objc', 'Foundation', 'AppKit', 'Quartz',
    'Quartz.CoreGraphics', 'Quartz.QuartzCore',
    'CoreFoundation', 'ApplicationServices',
    
    # macOS 剪贴板注入
    'pyautogui',
    
    # OpenCC 繁简转换
    'opencc', 'opencc_clib',
    
    # Web 配置页
    'webbrowser', 'http.server', 'socketserver', 'threading', 'queue',
    
    # 事件系统
    'concurrent.futures', 'concurrent.futures.thread',
    
    # VAD
    'webrtcvad', '_webrtcvad',
    
    # HuggingFace 模型下载
    'huggingface_hub', 'huggingface_hub.file_download', 'huggingface_hub.repository',
    
    # platform_adapter（macOS）
    'platform_adapter',
    'platform_adapter.hotkey_macos',
    'platform_adapter.clipboard_macos',
    
    # AVFoundation（麦克风权限检测）
    'AVFoundation', 'CoreAudio',
    
    # subprocess（FunASR 运行时调用）
    'subprocess', 'shutil',
]

# ========== datas ==========

datas = [
    ('config.yaml', '.'),
    ('assets', 'assets'),
    ('gui/templates', 'gui/templates'),
]

# ========== excludes ==========

excludes = [
    # 测试框架
    'pytest', 'unittest', 'doctest',
    # 不需要的库
    'tkinter', 'matplotlib', 'scipy', 'pandas',
    'IPython', 'jupyter', 'notebook',
    # FunASR（运行时安装，不打包）
    'funasr', 'modelscope', 'torch', 'torchaudio',
    'transformers', 'datasets', 'tokenizers',
    # Windows 相关
    'win32api', 'win32con', 'win32gui', 'ctypes.wintypes',
    'pystray._win32', 'platform_adapter.hotkey_windows',
    'platform_adapter.clipboard_windows',
    # 其他
    'setuptools', 'pip', 'wheel',
    'numpy.tests', 'numpy.distutils', 'numpy.doc',
]

# ========== Analysis ==========

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['build/hooks'],
    runtime_hooks=['build/hooks/runtime_hook_mac.py'],
    excludes=excludes,
    noarchive=False,
)

# ========== PYZ ==========

pyz = PYZ(a.pure, cipher=block_cipher)

# ========== EXE ==========

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoiceInputTool',
    debug=False,
    strip=False,
    upx=False,  # macOS arm64 上 UPX 有 crash 风险，默认关闭
    console=False,
)

# ========== COLLECT ==========

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,  # TODO: 评估 strip=True 对 arm64 .so 的安全性
    upx=False,
    name='VoiceInputTool',
)

# ========== BUNDLE (macOS) ==========

app = BUNDLE(
    coll,
    name='VoiceInputTool.app',
    icon='build/icon.icns',
    bundle_identifier='com.voiceinputtool.app',
    version='1.0.0',
    info_plist={
        'CFBundleName': 'VoiceInputTool',
        'CFBundleDisplayName': 'Voice Input',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundlePackageType': 'APPL',
        'NSMicrophoneUsageDescription': '语音输入工具需要麦克风权限进行语音识别',
        'NSAppleEventsUsageDescription': '语音输入工具需要 Apple Events 权限来模拟键盘输入',
        'NSHighResolutionCapable': True,
        'NSSupportsAutomaticGraphicsSwitching': True,
        'LSMinimumSystemVersion': '12.0',
        'LSUIElement': True,  # 后台应用，不显示在 Dock
    },
)
```

### 4.3 macOS Runtime Hook: `build/hooks/runtime_hook_mac.py`

```python
# build/hooks/runtime_hook_mac.py
# macOS .app bundle 运行时路径适配

import sys
import os

if sys.platform == 'darwin' and hasattr(sys, '_MEIPASS'):
    # PyInstaller 打包后的资源路径
    _base = sys._MEIPASS
    
    # 用户数据目录
    app_support = os.path.expanduser('~/Library/Application Support/VoiceInputTool')
    os.makedirs(app_support, exist_ok=True)
    
    log_dir = os.path.expanduser('~/Library/Logs/VoiceInputTool')
    os.makedirs(log_dir, exist_ok=True)
    
    # 环境变量供各模块使用
    os.environ.setdefault('VIT_BASE_PATH', _base)
    os.environ.setdefault('VIT_USER_DIR', app_support)
    os.environ.setdefault('VIT_LOG_DIR', log_dir)
    os.environ.setdefault('VIT_CONFIG_DIR', app_support)
    
    # 确保 FunASR 可选安装目录
    funasr_env = os.path.join(app_support, 'funasr_env')
    os.environ.setdefault('VIT_FUNASR_ENV', funasr_env)
    
    # console=False 时 stdout/stderr 默认丢弃，重定向到日志文件
    stdout_log = os.path.join(log_dir, 'stdout.log')
    stderr_log = os.path.join(log_dir, 'stderr.log')
    _stdout_fh = open(stdout_log, 'a')  # 保持引用防止 GC
    _stderr_fh = open(stderr_log, 'a')
    _stdout_fh.reconfigure(line_buffering=True)  # 行缓冲，即时写入
    _stderr_fh.reconfigure(line_buffering=True)
    sys.stdout = _stdout_fh
    sys.stderr = _stderr_fh
    sys.stdout.write(f"[RuntimeHook] MEIPASS: {_base}\n")
    sys.stdout.write(f"[RuntimeHook] User dir: {app_support}\n")
    sys.stdout.write(f"[RuntimeHook] Log dir: {log_dir}\n")
    sys.stdout.flush()
```

### 4.4 FunASR 运行时安装模块

新增 `core/funasr_installer.py`:

```python
# core/funasr_installer.py
# FunASR 运行时安装与管理

import os
import sys
import subprocess
import shutil

class FunASRInstaller:
    """FunASR 运行时安装器"""
    
    def __init__(self, env_dir=None):
        self.env_dir = env_dir or os.environ.get(
            'VIT_FUNASR_ENV',
            os.path.expanduser('~/Library/Application Support/VoiceInputTool/funasr_env')
        )
        self.pip_path = os.path.join(self.env_dir, 'bin', 'pip')
        self.python_path = os.path.join(self.env_dir, 'bin', 'python')
        # 🔑 关键：打包后 sys.executable 指向 .app 内的非标准 Python
        # 必须使用系统 Python 来创建 venv
        self._system_python = self._find_system_python()
    
    def _find_system_python(self) -> str:
        """查找系统上可用的标准 Python 解释器
        
        打包后 sys.executable 指向 Contents/MacOS/VoiceInputTool，
        不是标准 Python，无法用于创建 venv。
        需要找到系统的 python3（如 /usr/bin/python3 或 PATH 中的）。
        """
        # 优先级：PATH > Homebrew > /usr/bin/python3
        candidates = [
            shutil.which('python3'),
            shutil.which('python3.11'),
            shutil.which('python3.12'),
            '/usr/bin/python3',
            '/opt/homebrew/bin/python3',
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                try:
                    result = subprocess.run(
                        [candidate, '--version'],
                        capture_output=True, timeout=5
                    )
                    if result.returncode == 0:
                        return candidate
                except (subprocess.TimeoutExpired, OSError):
                    continue
        return None
    
    def is_installed(self) -> bool:
        """检查 FunASR 是否已安装"""
        return (os.path.exists(self.pip_path) 
                and os.path.exists(self.python_path))
    
    def install(self, callback=None) -> bool:
        """安装 FunASR 到隔离环境"""
        if not self._system_python:
            msg = "未找到系统 Python。请安装 Python 3.11+ 后重试。"
            if callback: callback(msg)
            return False
        
        try:
            if callback: callback(f"使用系统 Python: {self._system_python}")
            if callback: callback("创建虚拟环境...")
            subprocess.run(
                [self._system_python, '-m', 'venv', self.env_dir],
                check=True, capture_output=True, timeout=60
            )
            
            if callback: callback("安装 FunASR 依赖（可能需要 5-10 分钟）...")
            subprocess.run(
                [self.pip_path, 'install', '-q',
                 'funasr', 'modelscope', 'torch'],
                check=True, capture_output=True, timeout=1800  # 30min timeout
            )
            
            # 写入桥接脚本
            self._write_bridge_script()
            
            if callback: callback("FunASR 安装完成！")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as e:
            if callback: callback(f"安装失败: {e}")
            return False
    
    def _write_bridge_script(self):
        """写入 FunASR 桥接脚本到 venv 目录
        
        通信协议：JSON Lines（每行一个 JSON 对象）
        主进程 ←→ 桥接进程 通过 stdin/stdout 交换 JSON 消息
        
        消息格式：
        - 主进程 → 桥接：{"cmd": "load", "model": "paraformer-zh"}
        - 桥接 → 主进程：{"status": "ok", "msg": "model_loaded"}
        - 主进程 → 桥接：{"cmd": "transcribe", "audio_path": "/tmp/xxx.wav"}
        - 桥接 → 主进程：{"status": "ok", "text": "识别结果", "lang": "zh"}
        - 错误：{"status": "error", "msg": "错误描述"}
        """
        bridge_code = '''
import sys, json, os, signal
from funasr import AutoModel

model = None

def handle_msg(msg):
    global model
    cmd = msg.get("cmd")
    if cmd == "quit":
        return {"status": "ok", "msg": "bye"}
    elif cmd == "load":
        model_name = msg.get("model", "paraformer-zh")
        model_dir = msg.get("model_dir")
        model = AutoModel(model=model_name, model_dir=model_dir)
        return {"status": "ok", "msg": "model_loaded"}
    elif cmd == "transcribe":
        audio_path = msg.get("audio_path")
        res = model.generate(input=audio_path)
        text = res[0].get("text", "") if res else ""
        return {"status": "ok", "text": text}
    else:
        return {"status": "error", "msg": f"unknown cmd: {cmd}"}

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
        result = handle_msg(msg)
        if result.get("msg") == "bye":
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\\n")
            sys.stdout.flush()
            break
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\\n")
        sys.stdout.flush()
    except Exception as e:
        err = {"status": "error", "msg": str(e)}
        sys.stdout.write(json.dumps(err) + "\\n")
        sys.stdout.flush()
'''
        script_path = os.path.join(self.env_dir, 'funasr_bridge.py')
        with open(script_path, 'w') as f:
            f.write(bridge_code)
    
    def get_launcher_script(self) -> str:
        """返回 FunASR 桥接脚本路径"""
        return os.path.join(self.env_dir, 'funasr_bridge.py')
```

---

## 五、Info.plist 完整配置

通过 PyInstaller BUNDLE 的 `info_plist` 参数配置（见 spec 文件），包含以下 key：

| Key | 值 | 用途 |
|-----|-----|------|
| `CFBundleName` | `VoiceInputTool` | 应用名称 |
| `CFBundleDisplayName` | `Voice Input` | 显示名称 |
| `CFBundleIdentifier` | `com.voiceinputtool.app` | Bundle ID |
| `CFBundleVersion` | `1.0.0` | 内部版本号 |
| `CFBundleShortVersionString` | `1.0.0` | 显示版本号 |
| `CFBundlePackageType` | `APPL` | 应用类型 |
| `NSMicrophoneUsageDescription` | 需要麦克风权限... | 麦克风权限弹窗 |
| `NSAppleEventsUsageDescription` | 需要 Apple Events... | osascript 粘贴权限 |
| `NSHighResolutionCapable` | `true` | Retina 支持 |
| `NSSupportsAutomaticGraphicsSwitching` | `true` | GPU 切换支持 |
| `LSMinimumSystemVersion` | `12.0` | 最低系统版本 |
| `LSUIElement` | `true` | 后台代理，不占 Dock |

---

## 六、测试计划

### 6.1 打包验证

| # | 测试项 | 验证方法 | 预期结果 |
|---|--------|----------|----------|
| 1 | .app 结构 | `tree Contents/` | Info.plist + MacOS + Resources 正确 |
| 2 | 依赖完整 | 双击启动 | 无 ImportError |
| 3 | faster-whisper | 配置 faster-whisper | 模型加载 + 识别正常 |
| 4 | FunASR 可选安装 | Web 配置页一键安装 | venv 创建 + pip install 成功 |
| 5 | FunASR 识别 | 安装后切换引擎 | 模型加载 + 识别正常 |
| 6 | 系统托盘 | 启动后检查菜单栏 | 图标出现，菜单可操作 |
| 7 | 热键 F8 | 按下 F8 | 进入录音状态 |
| 8 | 键盘注入 | 录音后自动注入 | 文字通过 clipboard+paste 输出 |
| 9 | Web 配置页 | 访问 127.0.0.1:18921 | 配置页正常显示 |
| 10 | 日志 | `~/Library/Logs/VoiceInputTool/` | 日志文件存在 |
| 11 | 无控制台 | 启动时 | 无终端窗口 |
| 12 | 麦克风权限 | 首次启动 | 系统弹窗请求 |
| 13 | 辅助功能 | 系统设置 | 引导添加 |
| 14 | 输入监控 | macOS 14+ | 引导添加 |
| 15 | 清洁安装 | 删除所有用户数据后启动 | 全新安装流程正常 |
| 16 | DMG 挂载 | 双击 DMG | 挂载成功，README 可读 |
| 17 | Gatekeeper | `xattr -cr` 后启动 | 正常运行 |

### 6.2 多机器验证

- 本机 (M 系列) ✅
- 目标 Mac 1: ____________（待填）
- 目标 Mac 2: ____________（待填）

---

## 七、实施步骤

| Step | 内容 | 预估时间 |
|------|------|----------|
| 1 | 创建 `voice-input-tool-mac.spec` | 15min |
| 2 | 创建 `build/build_macos.sh` | 10min |
| 3 | 创建 `build/hooks/runtime_hook_mac.py` | 10min |
| 4 | 创建 `core/funasr_installer.py` | 30min |
| 5 | 修改 `core/config.py` 支持 FunASR 运行时安装路径 | 20min |
| 6 | 修改 Web 配置页增加 FunASR 安装按钮 | 20min |
| 7 | 修改权限检查增加 Input Monitoring 检测 | 15min |
| 8 | 生成 .icns 图标（或占位） | 5min |
| 9 | 首次构建 + 修复打包问题 | 60min |
| 10 | 功能验证（全部 17 项） | 45min |
| 11 | 制作 DMG + README | 10min |
| **合计** | | **~4h** |

---

## 八、风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| pynput/objc 隐藏模块遗漏 | 运行时 ImportError | 中 | 逐模块导入测试；参考 PyInstaller 钩子 |
| FunASR 运行时安装失败 | 用户无法使用 FunASR | 低 | venv 隔离；详细错误提示；faster-whisper 兜底 |
| macOS 14+ 权限变更 | 热键/注入失效 | 中 | 权限引导 UI；启动时检测 |
| ctranslate2 arm64 兼容 | faster-whisper 不可用 | 低 | 官方 wheel 支持 arm64 |
| PyInstaller BUNDLE 路径问题 | 资源加载失败 | 中 | runtime_hook + `sys._MEIPASS` 标准方案 |
| .app 体积过大 | 分发不便 | 低 | ~160MB 可接受；FunASR 不打包 |

---

*设计完成，待 Master 评审确认后实施。*
