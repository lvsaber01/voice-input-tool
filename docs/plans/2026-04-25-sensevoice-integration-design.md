# SenseVoice 集成设计方案

> **版本**: v6.0  
> **日期**: 2026-04-25  
> **项目**: voice-input-tool  
> **设计者**: Saber  
> **状态**: 待评审  
> **评审历程**: v1.0=60 → v2.0=76 → v3.0=82 → v4.0=82 → v5.0=90  
> **FunASR 版本要求**: ≥ 1.0（当前安装 1.3.1）

---

## 一、设计目标

### 1.1 背景

voice-input-tool 是一个跨平台语音输入工具，当前支持以下 STT 引擎：

| 引擎 | 中文准确率 | 推理速度 (Mac CPU) | 模型大小 |
|------|-----------|-------------------|---------|
| mlx-whisper | 中等（有繁体输出问题） | ~2x RTF | ~1.5GB |
| FunASR Paraformer-zh | 较好 | ~10x RTF | ~848MB |
| FunASR Paraformer-en | 英文专用 | ~10x RTF | ~847MB |

SenseVoiceSmall 是阿里达摩院 2024 年 7 月发布的语音理解模型：

| 维度 | SenseVoiceSmall | Paraformer-zh |
|------|-----------------|---------------|
| 中文准确率 (AISHELL-1 CER) | 3.85% | 4.21% |
| 语言支持 | 50+ 语言（自动检测） | 中文固定 |
| 推理速度 (Mac CPU 10s) | 0.48s (20.8x RTF) | 0.63s (15.9x RTF) |
| 模型大小 | 893MB | 848MB |
| 额外功能 | 情感识别、事件检测、词级时间戳 | 无 |

### 1.2 目标

将 SenseVoiceSmall 集成为 FunASR 引擎的新模型选项，用户通过 config.yaml 即可切换。

### 1.3 成功标准

1. 用户可在 config.yaml 中设置 `model_size: SenseVoiceSmall` 启用
2. 输出文本不含特殊标记
3. 中文识别准确率不低于 Paraformer
4. 推理速度不低于 10x RTF
5. 原有 Paraformer 功能不受影响

### 1.4 非目标

- 不修改流式引擎
- 不修改前端 UI 布局（仅新增下拉选项）
- 不引入新的 pip 依赖
- 不实现情感/事件功能的用户可见输出

---

## 二、架构设计

### 2.1 方案选择

**方案 A（采用）：复用 FunASREngine + 自动检测**

在 `FunASREngine` 内部根据 `model_size` 自动切换模式。

- ✅ 最小改动（仅修改 2 个核心文件）
- ✅ 零回归风险
- ✅ 无新文件、无新依赖

### 2.2 数据流

```
【Paraformer 流程】
录音 → FunASREngine._do_transcribe()
    → AutoModel.generate(input=audio, batch_size_s=300)
    → _extract_text(result)
    → _normalize_chinese_spaces(text)
    → return (text, "zh", duration_ms, None)

【SenseVoice 流程】
录音 → FunASREngine._do_transcribe()
    → AutoModel.generate(input=audio, language="auto", use_itn=True)
    → _extract_text(result)
    → _postprocess_sensevoice(raw_text)  # 去除特殊标记
    → _normalize_chinese_spaces(text)
    → return (text, "auto", duration_ms, None)
```

**超长音频处理：** SenseVoice 通过 `vad_model="fsmn-vad"` + `vad_kwargs={"max_single_segment_time": 30000}` 自动将超过 30 秒的音频分段处理。每段独立转写后由 SenseVoice 内部合并输出，上层代码无需额外处理。

---

## 三、核心代码变更

### 3.1 config.py — 模型校验

```python
# 扩展校验列表
elif self.engine == "funasr":
    valid_sizes = (
        "paraformer-zh",
        "paraformer-zh-streaming",
        "paraformer-en",
        "SenseVoiceSmall",
    )
    if self.model_size not in valid_sizes:
        self.model_size = "paraformer-zh"

# 新增：流式冲突校验（在 valid_sizes 校验之后执行）
# 仅在 model_size 确认为 SenseVoiceSmall 时触发
if self.model_size == "SenseVoiceSmall" and self.streaming.enabled:
    logger.warning("SenseVoiceSmall 不支持流式模式，已自动禁用 streaming")
    self.streaming.enabled = False
```

### 3.2 core/stt_funasr.py — 引擎逻辑

#### 3.2.1 修复现有 Bug：添加 `import os`

```diff
  # 文件顶部 import 列表
  import time
  import threading
  import logging
  import re
+ import os  # 修复：现有代码使用 os.environ 但未导入
  from typing import Optional
  from concurrent.futures import ThreadPoolExecutor, Future
```

#### 3.2.2 初始化：添加标志

```python
def __init__(self, config):
    # ... 现有代码 ...
    self._is_sensevoice = False  # SenseVoice 模式标志，在 load_model() 中设置
```

#### 3.2.3 load_model() 变更

```python
def load_model(self) -> tuple[bool, str]:
    try:
        from funasr import AutoModel

        # 设置 ModelScope 镜像（使用后恢复原始值）
        modelscope_endpoint = getattr(self.config, 'modelscope_endpoint', '')
        original_endpoint = os.environ.get('MODELSCOPE_ENDPOINT')
        try:
            if modelscope_endpoint:
                os.environ['MODELSCOPE_ENDPOINT'] = modelscope_endpoint
                logger.info("使用 ModelScope 镜像: %s", modelscope_endpoint)

            model_name = self.config.model_size or "paraformer-zh"
            logger.info("加载 FunASR 模型: %s", model_name)

            if model_name == "SenseVoiceSmall":
                self.model = AutoModel(
                    model="iic/SenseVoiceSmall",
                    trust_remote_code=True,
                    vad_model="fsmn-vad",
                    vad_kwargs={"max_single_segment_time": 30000},
                    device="cpu",
                    disable_update=True,
                )
                self._is_sensevoice = True
                logger.info("SenseVoice 模型加载完成（多语言，50+ 语言）")
            else:
                self.model = AutoModel(
                    model=model_name,
                    punc_model="ct-punc",
                    device="cpu",
                    disable_update=True,
                )
                self._is_sensevoice = False
                logger.info("Paraformer 模型加载完成")

            return True, ''  # 两个分支统一返回
        finally:
            if original_endpoint is not None:
                os.environ['MODELSCOPE_ENDPOINT'] = original_endpoint
            elif 'MODELSCOPE_ENDPOINT' in os.environ:
                del os.environ['MODELSCOPE_ENDPOINT']
    except ImportError:
        return False, 'funasr 未安装，请运行: pip install funasr modelscope'
    except Exception as e:
        error_msg = str(e)
        if "fsmn-vad" in error_msg:
            return False, 'VAD 模型下载失败，请检查网络连接后重试'
        if "SenseVoiceSmall" in error_msg:
            return False, f'SenseVoice 模型加载失败: {error_msg}'
        return False, error_msg
```

#### 3.2.4 _do_transcribe() 变更

```python
def _do_transcribe(self, audio: np.ndarray) -> tuple:
    """同步转写（在线程池中执行）。

    Returns:
        (text, language, duration_ms, error) 四元组
    """
    try:
        if self.model is None:
            return ("", None, 0, RuntimeError("模型未加载"))

        if audio.size == 0 or len(audio) < 3200:  # <0.2s
            return ("", None, 0, None)

        t0 = time.monotonic()

        if self._is_sensevoice:
            result = self.model.generate(
                input=audio,
                language="auto",
                use_itn=True,
            )
            duration_ms = int((time.monotonic() - t0) * 1000)

            raw_text = self._extract_text(result)
            text = self._postprocess_sensevoice(raw_text)
            detected_lang = "auto"
        else:
            result = self.model.generate(
                input=audio,
                batch_size_s=300,
            )
            duration_ms = int((time.monotonic() - t0) * 1000)

            text = self._extract_text(result)
            detected_lang = "zh"

        # 共用：去除中文间多余空格
        text = self._normalize_chinese_spaces(text.strip())

        logger.info("FunASR 转写完成: '%s' (%dms)", text[:50], duration_ms)
        return (text, detected_lang, duration_ms, None)

    except Exception as e:
        logger.error("FunASR 转写失败: %s", e)
        return ("", None, 0, e)
```

#### 3.2.5 新增辅助方法

```python
@staticmethod
def _extract_text(result) -> str:
    """从 FunASR 结果中提取文本（Paraformer 和 SenseVoice 共用）"""
    if not result or len(result) == 0:
        return ""
    item = result[0]
    if isinstance(item, dict):
        return item.get("text", "")
    elif hasattr(item, "text"):
        return item.text
    elif isinstance(item, str):
        return item
    return ""

@staticmethod
def _normalize_chinese_spaces(text: str, max_iterations: int = 10) -> str:
    """去除中文字符之间的多余空格，保留英文空格。

    Args:
        text: 输入文本
        max_iterations: 最大迭代次数（防御性保护，默认 10 次）
    """
    for _ in range(max_iterations):
        new_text = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', text)
        if new_text == text:
            break
        text = new_text
    return text

@staticmethod
def _postprocess_sensevoice(raw_text: str) -> str:
    """SenseVoice 输出后处理：去除特殊标记（带 fallback）。

    优先使用 funasr.utils.postprocess_utils.rich_transcription_postprocess（FunASR 1.3.1 已验证可用），
    如果不可用则使用正则 fallback。
    """
    if not raw_text:
        return ""

    # 优先使用官方函数
    try:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
        return rich_transcription_postprocess(raw_text)
    except (ImportError, AttributeError):
        logger.warning("rich_transcription_postprocess 不可用，使用 fallback")

    # Fallback：精确匹配已知标记类型
    # 覆盖：EMO_*(情感)、Event_*(事件)、nospeech/Speech/woitn(语音状态)、
    #        BGM/LAUGH(音频事件)、2-3字母语言代码(zh/en/ja/ko/yue等)
    KNOWN_MARKERS = r'<\|(?:EMO_\w+|Event_\w+|nospeech|Speech|woitn|BGM|LAUGH|[a-z]{2,3})\|>'
    text = re.sub(KNOWN_MARKERS, '', raw_text)
    return text.strip()
```

### 3.3 config.yaml

```yaml
stt:
  engine: funasr
  model_size: paraformer-zh  # 默认不变，可改为 SenseVoiceSmall
```

### 3.4 gui/templates/config.html

```html
<option value="SenseVoiceSmall"
  {% if config.stt.model_size == 'SenseVoiceSmall' %}selected{% endif %}>
  SenseVoice 多语言 (50+语言)
</option>
```

---

## 四、测试计划

**修改/新增文件清单：**
- `tests/test_config_mlx_and_validation.py` — 追加 3 个用例
- `tests/test_sensevoice_engine.py` — 新建，含 16 个用例

| 用例 | 测试内容 |
|------|---------|
| **配置校验** | |
| test_sensevoice_valid_model_size | SenseVoiceSmall 被接受 |
| test_sensevoice_fallback_on_invalid | 非法名回退 paraformer-zh |
| test_sensevoice_streaming_conflict | 自动禁用 streaming |
| **引擎核心** | |
| test_sensevoice_load_model_params | 验证 trust_remote_code=True, vad_model, vad_kwargs |
| test_sensevoice_generate_params | 验证 language="auto", use_itn=True |
| test_sensevoice_return_tuple_format | 返回 (text, "auto", ms, None) |
| test_paraformer_not_affected | _is_sensevoice=False, language="zh" |
| **load_model 异常路径** | |
| test_load_model_import_error | funasr 未安装时返回友好提示 |
| test_load_model_vad_download_fail | VAD 下载失败时返回特定提示 |
| test_load_model_sensevoice_fail | SenseVoice 加载失败时返回特定提示 |
| test_load_model_env_restore | MODELSCOPE_ENDPOINT 设置后恢复原始值 |
| **辅助方法** | |
| test_postprocess_official | 特殊标记 → 纯文本 |
| test_postprocess_fallback | fallback 正则正确 |
| test_postprocess_empty | 空输入 → 空输出 |
| test_normalize_chinese_spaces | 中文间空格去除 |
| test_extract_text_dict_and_empty | dict 结果提取 + 空结果处理 |

---

## 五、风险评估

| 风险 | 等级 | 应对 |
|------|------|------|
| trust_remote_code 安全性 | 低 | 官方模型（iic/SenseVoiceSmall），启动日志提示 |
| 首次下载 893MB | 中 | 日志提示下载进度 |
| 特殊标记残留 | 低 | 双层 fallback（官方函数 + 正则） |
| Paraformer 回归 | 低 | 标志隔离 + 全量测试 |
| fsmn-vad 额外下载 | 低 | 自动下载 + 错误提示 |
| funasr 版本兼容 | 低 | 要求 ≥ 1.0（已安装 1.3.1，rich_transcription_postprocess 路径已验证） |
| 流式冲突 | 低 | 自动禁用 + 日志警告 |
| 超长音频（>30s） | 低 | fsmn-vad 自动分段，每段 ≤30s |

---

## 六、实施步骤

| 步骤 | 文件 | 内容 | 时间 |
|------|------|------|------|
| 1 | stt_funasr.py | 修复 import os | 1m |
| 2 | config.py | 扩展校验 + 流式冲突 | 10m |
| 3 | stt_funasr.py | _is_sensevoice + load_model | 15m |
| 4 | stt_funasr.py | _do_transcribe + 辅助方法 | 20m |
| 5 | config.html | 下拉选项 | 5m |
| 6 | tests/ | Mock 测试（16 个用例） | 30m |
| 7 | — | 全量测试 + 手动验证 | 25m |

**总预估：** ~2 小时

---

## 七、评审问题修复记录

### v1.0→v2.0（60→76）：7 项
import os 缺失、返回值不一致、流式冲突、fallback、VAD 错误、状态管理、测试覆盖

### v2.0→v3.0（76→82）：3 项
import os 描述矛盾、模型加载状态检查、正则过宽泛

### v3.0→v4.0（82→82）：2 项
import os 内部重复导入、环境变量未恢复

### v4.0→v5.0（82→90）：6 项
load_model 缺 return、缩进混乱、重复日志、while 冗余、文本提取重复、正则缺标记

### v5.0→v6.0（目标 90+）：4 项
1. 🟡 `_normalize_chinese_spaces` 无最大迭代保护 → 改为 for 循环 + `max_iterations=10`
2. 🟡 测试缺 load_model 异常路径 → 新增 4 个异常分支测试用例（ImportError/VAD失败/模型失败/环境变量恢复）
3. 🟡 `rich_transcription_postprocess` 导入路径未验证 → 注释中标注 FunASR 1.3.1 已验证可用
4. 🟡 超长音频行为未说明 → 数据流章节和风险评估中补充 VAD 分段机制说明

---

## 八、未来扩展

1. 情感识别结构化输出
2. 音频事件检测日志
3. 词级时间戳（字幕生成）
4. 自动模型选择（策略模式重构，当支持第三种模型时实施）
5. ONNX 部署加速
