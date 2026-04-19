# PyInstaller runtime hook
# 处理打包后的路径和工作目录

import sys
import os

def get_user_data_dir():
    """获取用户数据目录（跨平台）"""
    if sys.platform == 'win32':
        # Windows: %APPDATA%/VoiceInputTool
        return os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'VoiceInputTool')
    elif sys.platform == 'darwin':
        # macOS: ~/Library/Application Support/VoiceInputTool
        return os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'VoiceInputTool')
    else:
        # Linux: ~/.local/share/VoiceInputTool
        return os.path.join(os.path.expanduser('~'), '.local', 'share', 'VoiceInputTool')

def setup_paths():
    """设置打包后的路径"""
    if getattr(sys, 'frozen', False):
        # 打包后运行
        # exe 所在目录
        exe_dir = os.path.dirname(sys.executable)
        
        # 设置工作目录为 exe 目录（便于读取 config.yaml）
        os.chdir(exe_dir)
        
        # 用户数据目录
        user_data_dir = get_user_data_dir()
        os.makedirs(user_data_dir, exist_ok=True)
        
        # 导出环境变量供其他模块使用
        os.environ['VOICE_INPUT_TOOL_EXE_DIR'] = exe_dir
        os.environ['VOICE_INPUT_TOOL_USER_DATA'] = user_data_dir
        
        # 模型目录：优先用户数据目录，其次 exe 目录
        user_models_dir = os.path.join(user_data_dir, 'models')
        exe_models_dir = os.path.join(exe_dir, 'models')
        
        os.makedirs(user_models_dir, exist_ok=True)
        os.environ['VOICE_INPUT_TOOL_MODELS'] = user_models_dir
        
        # 日志目录：用户数据目录
        logs_dir = os.path.join(user_data_dir, 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        os.environ['VOICE_INPUT_TOOL_LOGS'] = logs_dir

# 执行路径设置
setup_paths()