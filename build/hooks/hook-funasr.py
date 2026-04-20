# PyInstaller hook for FunASR
# 收集 FunASR 包的所有数据文件（包括 version.txt）

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

# 收集所有非 Python 文件
datas = collect_data_files('funasr', include_py_files=False)

# 收集包元数据（版本信息等）
datas += copy_metadata('funasr')

# 确保 hiddenimports
hiddenimports = [
    'funasr',
    'funasr.auto',
    'funasr.models',
    'funasr.runtime',
    'modelscope',
    'modelscope.models',
]