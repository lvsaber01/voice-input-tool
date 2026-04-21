# Models Directory

Place downloaded models here for offline use.

## Directory Structure

```
models/
  faster-whisper-large-v3-turbo/   # faster-whisper model
    model.bin
    config.json
    tokenizer.json
    preprocessor_config.json
    vocabulary.json
```

## 1. faster-whisper Models

### Supported Models

| model_size | Description | Size |
|-----------|-------------|------|
| `large-v3-turbo` | Best quality, fast (recommended) | ~1.5GB |
| `large-v3` | Best quality | ~3GB |
| `medium` | Balanced | ~1.5GB |
| `small` | Fast | ~500MB |
| `base` | Lightweight | ~150MB |
| `tiny` | Fastest, lowest quality | ~80MB |

### Download (China)

HuggingFace mirror: https://hf-mirror.com

1. Visit `https://hf-mirror.com/mobiuslabsgmbh/faster-whisper-large-v3-turbo`
2. Click "Files and versions" tab
3. Download all files into `models/faster-whisper-large-v3-turbo/`

Required files:
- `model.bin` (main model)
- `config.json`
- `tokenizer.json`
- `preprocessor_config.json`
- `vocabulary.json` (or `vocabulary.txt`)

### Folder Naming Rule

Folder name MUST be `faster-whisper-{model_size}`, matching `stt.model_size` in config.yaml.

Examples:
- `models/faster-whisper-large-v3-turbo/` for `model_size: large-v3-turbo`
- `models/faster-whisper-medium/` for `model_size: medium`

### Config

```yaml
stt:
  engine: faster_whisper
  model_size: large-v3-turbo
  model_path: ./models/
  hf_endpoint: https://hf-mirror.com  # China mirror
```

## 2. FunASR Models (Paraformer)

### Supported Models

| model_size | Description | Source |
|-----------|-------------|--------|
| `paraformer-zh` | Chinese speech recognition | ModelScope |

### Download (China)

1. Visit https://modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch
2. Click "Files" tab, download all files

### Placement

FunASR loads models from ModelScope cache directory. Place files at:

```
C:\Users\<YourUsername>\.cache\modelscope\hub\models\iic\
  speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch\
    configuration.json
    model.pt
    am.mvn
    ...
```

This matches the default ModelScope download location. FunASR will auto-detect it.

### Config

```yaml
stt:
  engine: funasr
  model_size: paraformer-zh
```

## Quick Reference

| Model | Source (China) | Local Path |
|-------|---------------|------------|
| faster-whisper | hf-mirror.com | `./models/faster-whisper-{size}/` |
| FunASR Paraformer | modelscope.cn | `~/.cache/modelscope/hub/models/iic/...` |

## First Run

On first launch with network access, models download automatically:
- faster-whisper: from HuggingFace (uses `hf_endpoint` mirror if configured)
- FunASR: from ModelScope

If download fails due to network, follow the manual download steps above.
