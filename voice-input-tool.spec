# voice-input-tool.spec
# PyInstaller 配置文件 - Windows 版本
# 版本: v1.1

import sys
import os
import subprocess

block_cipher = None

# ========== hiddenimports（完整列表） ==========

# 核心依赖
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
    '_sounddevice',
    'soundfile',
    '_soundfile',
    'numpy',
    
    # 配置
    'yaml',
    'yaml.loader',
    'yaml.dumper',
    'yaml.representer',
    
    # 系统托盘
    'pystray',
    'pystray._win32',
    'pystray._darwin',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    
    # OpenCC 繁简转换
    'opencc',
    'opencc_clib',
    
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
    '_webrtcvad',
    
    # HuggingFace 模型下载
    'huggingface_hub',
    'huggingface_hub.file_download',
    'huggingface_hub.repository',
    
    # platform_adapter 动态导入
    'platform_adapter',
    'platform_adapter.hotkey_windows',
    'platform_adapter.hotkey_macos',
    'platform_adapter.clipboard_windows',
    'platform_adapter.clipboard_macos',
    'platform_adapter.key_simulator',
]

# FunASR 引擎（可选）
funasr_imports = [
    'funasr',
    'funasr.auto',
    'funasr.models',
    'modelscope',
]

# 检查 FunASR 是否可用
try:
    import funasr
    hiddenimports.extend(funasr_imports)
    print("[INFO] FunASR detected, adding to hiddenimports")
except ImportError:
    print("[INFO] FunASR not available, skipping")

# Windows 特定依赖
if sys.platform == 'win32':
    hiddenimports.extend([
        'keyboard',
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

# ========== datas（数据文件） ==========

datas = [
    ('config.example.yaml', '.'),  # 模板配置
    ('gui/templates', 'gui/templates'),
]

# 模型目录（如果存在）
models_dir = os.path.join(os.getcwd(), 'models')
if os.path.exists(models_dir):
    datas.append(('models', 'models'))
    print(f"[INFO] Including models directory: {models_dir}")
else:
    print("[INFO] Models directory not found, will download on first run")

# ========== UPX 检测 ==========

def check_upx():
    """检测 UPX 是否可用"""
    try:
        result = subprocess.run(['upx', '--version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False

use_upx = check_upx()
print(f"[INFO] UPX compression: {use_upx}")

# ========== Analysis ==========

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['build/hooks'],
    runtime_hooks=['build/hooks/runtime_hook.py'],
    excludes=[],  # 排除不需要的包
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
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
    upx=use_upx,
    console=False,  # 无控制台窗口
    icon='build/icon.ico',  # 图标（占位）
    uac_admin=False,  # 不强制管理员权限
)

# ========== COLLECT ==========

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=use_upx,
    upx_exclude=[],  # UPX 排除列表
    name='VoiceInputTool',
)

# ========== macOS BUNDLE（仅 macOS） ==========

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