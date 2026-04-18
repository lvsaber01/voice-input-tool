"""模型下载脚本

使用 huggingface_hub 下载 faster-whisper 的 CTranslate2 格式模型。
支持命令行参数指定模型大小，默认下载 small 模型。

用法:
    python download_model.py              # 下载 small 模型
    python download_model.py --size base  # 下载 base 模型
    python download_model.py --size medium
    python download_model.py --list       # 列出可用模型
"""

import argparse
import os
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# faster-whisper 在 HuggingFace 上的 CTranslate2 模型仓库
# 格式: {model_size: (repo_id, 子目录)}
MODELS = {
    "tiny": ("Systran/faster-whisper-tiny", None),
    "base": ("Systran/faster-whisper-base", None),
    "small": ("Systran/faster-whisper-small", None),
    "medium": ("Systran/faster-whisper-medium", None),
    "large-v3": ("Systran/faster-whisper-large-v3", None),
}

# 模型大小参考信息
MODEL_INFO = {
    "tiny": "~75MB, 最快, 中文质量一般",
    "base": "~150MB, 快, 中文可用",
    "small": "~500MB, 中等速度, 中文好（推荐）",
    "medium": "~1.5GB, 慢, 中文很好",
    "large-v3": "~3GB, 最慢, 中文最佳",
}


def download_model(model_size: str, output_dir: Path):
    """下载指定大小的模型到 output_dir/{model_size}/ 目录"""
    if model_size not in MODELS:
        print(f"错误: 不支持的模型大小 '{model_size}'")
        print(f"可选: {', '.join(MODELS.keys())}")
        sys.exit(1)

    repo_id, subfolder = MODELS[model_size]
    target_dir = output_dir / model_size
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"下载模型: {model_size}")
    print(f"  仓库: {repo_id}")
    print(f"  目标: {target_dir}")
    print(f"  大小: {MODEL_INFO[model_size]}")
    print()

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("错误: huggingface_hub 未安装")
        print("请运行: pip install huggingface_hub")
        sys.exit(1)

    try:
        downloaded_path = snapshot_download(
            repo_id=repo_id,
            local_dir=str(target_dir),
            local_dir_use_symlinks=False,
        )
        print(f"\n✓ 模型下载完成: {downloaded_path}")
        return True
    except Exception as e:
        print(f"\n✗ 模型下载失败: {e}")
        print("请检查网络连接，或手动从以下地址下载:")
        print(f"  https://huggingface.co/{repo_id}")
        return False


def list_models():
    """列出可用模型"""
    print("可用模型:")
    print("-" * 60)
    for name, info in MODEL_INFO.items():
        marker = " (推荐)" if name == "small" else ""
        print(f"  {name:10s}  {info}{marker}")
    print()


def main():
    parser = argparse.ArgumentParser(description="下载 faster-whisper 模型")
    parser.add_argument(
        "--size", "-s",
        default="small",
        choices=list(MODELS.keys()),
        help="模型大小 (默认: small)",
    )
    parser.add_argument(
        "--output", "-o",
        default=str(PROJECT_ROOT / "models"),
        help="输出目录 (默认: ./models/)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="列出可用模型",
    )
    args = parser.parse_args()

    if args.list:
        list_models()
        return

    output_dir = Path(args.output)
    success = download_model(args.size, output_dir)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
