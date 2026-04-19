# main.py 打包路径适配修改方案

## 目标

修改 main.py 以支持 PyInstaller 打包后的路径：
- 打包后使用用户数据目录（runtime_hook 设置的环境变量）
- 开发环境保持原有路径

## 关键改动

### 1. 项目根目录和日志目录

**当前**：
```python
PROJECT_ROOT = Path(__file__).parent.resolve()
LOG_DIR = PROJECT_ROOT / "logs"
```

**修改为**：
```python
import os
import sys

# 检测是否打包运行
if getattr(sys, 'frozen', False):
    # 打包后：使用 runtime_hook 设置的环境变量
    PROJECT_ROOT = Path(sys.executable).parent
    LOG_DIR = Path(os.environ.get('VOICE_INPUT_TOOL_LOGS', PROJECT_ROOT / "logs"))
else:
    # 开发环境：保持原有逻辑
    PROJECT_ROOT = Path(__file__).parent.resolve()
    LOG_DIR = PROJECT_ROOT / "logs"
```

### 2. 配置文件路径

**当前**：
```python
config_path = str(PROJECT_ROOT / "config.yaml")
```

**修改为**：
```python
if getattr(sys, 'frozen', False):
    # 打包后：exe 目录的 config.yaml（默认配置）
    exe_config = PROJECT_ROOT / "config.yaml"
    
    # 用户数据目录的 config.yaml（用户配置）
    user_data_dir = Path(os.environ.get('VOICE_INPUT_TOOL_USER_DATA', PROJECT_ROOT))
    user_config = user_data_dir / "config.yaml"
    
    # 首次启动：复制默认配置到用户目录
    if not user_config.exists() and exe_config.exists():
        import shutil
        shutil.copy2(exe_config, user_config)
    
    # 优先使用用户配置
    config_path = str(user_config if user_config.exists() else exe_config)
else:
    config_path = str(PROJECT_ROOT / "config.yaml")
```

### 3. 模型路径

**当前**：
```python
model_path = PROJECT_ROOT / model_path
```

**修改为**：
```python
if getattr(sys, 'frozen', False):
    # 打包后：优先用户数据目录
    user_models_dir = Path(os.environ.get('VOICE_INPUT_TOOL_MODELS', PROJECT_ROOT / "models"))
    model_path = user_models_dir / Path(model_path).name
else:
    model_path = PROJECT_ROOT / model_path
```

## 影响范围

1. 日志初始化（第54-87行）
2. 配置加载（第188行）
3. 模型路径处理（第197行）
4. 统计数据目录（第264行）

## 测试要点

1. 开发环境运行：保持原有行为
2. 打包后运行：使用用户数据目录
3. 首次启动：复制默认配置到用户目录
4. 配置修改：持久化到用户目录

## 风险评估

- **风险等级**：中等
- **影响范围**：路径逻辑，不涉及核心功能
- **回退方案**：不修改则打包后路径指向 exe 目录（可接受）

---

*等待 Master 审批后执行修改*