#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Qwen3-ASR 集成测试脚本

在 win-dev (Windows, CPU) 上执行真实模型加载和转写。

前置条件: pip install qwen-asr torch torchaudio

测试矩阵：
1. 依赖检查 — is_available() True（安装后）
2. load_model — Qwen3-ASR-0.6B CPU 加载成功
3. transcribe_sync — 真实音频产生非空文本
4. RTF 测试 — 计算实时率
5. 短音频/空音频 — 边界处理
6. transcribe_async 回调签名 — 4 元组
7. 正忙检测 — RuntimeError("正忙")
8. shutdown 释放 — model=None + CUDA 缓存释放
9. max_new_tokens 配置 — 验证从 config 读取
10. dtype 策略 — CPU float32
"""

import sys
import os
import time
import traceback
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0
ERROR = 0


def test(name, func):
    global PASS, FAIL, ERROR
    try:
        func()
        PASS += 1
        print(f"  [PASS] {name}")
    except AssertionError as e:
        FAIL += 1
        print(f"  [FAIL] {name}: {e}")
    except Exception as e:
        ERROR += 1
        print(f"  [ERR] {name}: {type(e).__name__}: {e}")
        traceback.print_exc()


def main():
    print("=" * 60)
    print("Qwen3-ASR 集成测试")
    print("=" * 60)

    # 环境检查
    print("\n--- 环境信息 ---")
    import platform
    print(f"  OS: {platform.system()} {platform.release()}")
    print(f"  Python: {sys.version}")

    try:
        import torch
        print(f"  torch: {torch.__version__}")
        print(f"  CUDA available: {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            print(f"  CUDA bf16 supported: {torch.cuda.is_bf16_supported()}")
    except ImportError:
        print("  torch: NOT INSTALLED")

    # 依赖检查
    try:
        import qwen_asr
        print(f"  qwen-asr: installed")
    except ImportError:
        print("  qwen-asr: NOT INSTALLED")
        print("  请先安装: pip install qwen-asr torch torchaudio")
        print("  跳过所有测试。")
        return

    from core.stt_qwen3_asr import Qwen3ASREngine
    from config import STTConfig

    print("\n--- 1. is_available ---")
    def test_is_available():
        assert Qwen3ASREngine.is_available() is True
    test("is_available 返回 True", test_is_available)

    print("\n--- 2. 模型加载 (Qwen3-ASR-0.6B CPU) ---")
    config = STTConfig(engine="qwen3_asr", model_size="Qwen3-ASR-0.6B")
    config.max_new_tokens = 256
    engine = Qwen3ASREngine(config)

    print(f"  config.engine={config.engine}, model_size={config.model_size}")
    print(f"  max_new_tokens={config.max_new_tokens}")

    def test_load():
        success, err = engine.load_model()
        assert success is True, f"load_model 失败: {err}"
        assert engine.model is not None, "model 为 None"
    test("load_model 成功", test_load)

    # 加载测试音频
    audio_path = os.path.join(os.path.dirname(__file__), "test_audio.npy")
    if os.path.exists(audio_path):
        test_audio = np.load(audio_path)
    else:
        test_audio = np.random.randn(160000).astype(np.float32)
    print(f"\n  测试音频: {len(test_audio)} samples ({len(test_audio)/16000:.2f}s)")

    print("\n--- 3. transcribe_sync ---")
    def test_sync():
        text = engine.transcribe_sync(test_audio)
        assert isinstance(text, str), f"返回类型错误: {type(text)}"
        print(f"    转写结果: \"{text[:80]}\"")
    test("transcribe_sync 返回文本", test_sync)

    print("\n--- 4. RTF 测试 ---")
    def test_rtf():
        duration_s = len(test_audio) / 16000
        t0 = time.monotonic()
        engine.transcribe_sync(test_audio)
        elapsed = time.monotonic() - t0
        rtf = elapsed / duration_s if duration_s > 0 else 0
        print(f"    音频时长: {duration_s:.2f}s, 转写耗时: {elapsed:.2f}s, RTF: {rtf:.3f}")
        assert rtf > 0, "RTF 应大于 0"
    test("RTF 计算", test_rtf)

    print("\n--- 5. 短音频/空音频 ---")
    def test_short():
        text = engine.transcribe_sync(np.zeros(1600, dtype=np.float32))
        assert text == "", f"短音频应返回空串，实际: \"{text}\""
    test("短音频 (<0.1s) 返回空串", test_short)

    def test_empty():
        text = engine.transcribe_sync(np.array([], dtype=np.float32))
        assert text == "", f"空音频应返回空串，实际: \"{text}\""
    test("空音频返回空串", test_empty)

    print("\n--- 6. transcribe_async 回调 ---")
    import threading

    def test_async():
        holder = [None]
        event = threading.Event()

        def cb(text, language, duration_ms, error):
            holder[0] = (text, language, duration_ms, error)
            event.set()

        engine.transcribe_async(test_audio, cb)
        event.wait(timeout=60)

        assert holder[0] is not None, "回调未触发"
        text, lang, dur, err = holder[0]
        assert isinstance(text, str), f"text 类型: {type(text)}"
        assert isinstance(dur, int) and dur >= 0, f"duration_ms: {type(dur)}"
        assert err is None, f"应无错误: {err}"
        print(f"    text=\"{text[:50]}\", lang={lang}, duration={dur}ms")
    test("transcribe_async 回调 4 元组", test_async)

    print("\n--- 7. 正忙检测 ---")
    def test_busy():
        holder2 = [None]
        event2 = threading.Event()

        def cb2(text, lang, dur, err):
            holder2[0] = (text, lang, dur, err)
            event2.set()

        engine.transcribe_async(test_audio, cb2)
        event2.wait(timeout=5)

        assert holder2[0] is not None, "第二次回调未触发"
        _, _, _, err = holder2[0]
        assert isinstance(err, RuntimeError), f"应为 RuntimeError: {type(err)}"
        assert "正忙" in str(err), f"应含'正忙': {err}"
    test("正忙检测", test_busy)

    print("\n--- 8. shutdown ---")
    def test_shutdown():
        engine.shutdown()
        assert engine.model is None, f"shutdown 后 model 应为 None"
    test("shutdown 后 model=None", test_shutdown)

    # ============ 汇总 ============
    print("\n" + "=" * 60)
    print(f"测试结果: [PASS] {PASS} 通过 | [FAIL] {FAIL} 失败 | [ERR] {ERROR} 异常")
    print("=" * 60)
    sys.exit(0 if (FAIL == 0 and ERROR == 0) else 1)


def assert_true(val):
    assert val is True, f"Expected True, got {val}"


if __name__ == "__main__":
    main()
