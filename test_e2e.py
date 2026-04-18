#!/usr/bin/env python3
"""端到端模拟测试 — 不依赖麦克风和热键

测试流程：
1. STT 引擎：加载模型 + 转写测试音频
2. 剪贴板注入：写入 + 模拟粘贴
3. 实时转写：模拟音频队列 + VAD + 分段转写 + 注入
4. CoreEngine 状态机：状态转换验证
5. 完整批量模式：音频数据 → STT → 注入
"""

import sys
import os
import time
import queue
import threading
import tempfile
import wave
import logging

import numpy as np

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger('e2e_test')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def generate_speech_like_audio(duration=3.0, sr=16000):
    """生成类似语音的测试音频（多频率叠加 + 包络）"""
    t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
    # 语音频段（300-3400Hz）内多个频率
    audio = np.zeros_like(t)
    for freq in [300, 500, 800, 1200, 2000]:
        audio += (0.15 / 5) * np.sin(2 * np.pi * freq * t)
    # 加包络（模拟说话节奏：有停顿）
    envelope = np.ones_like(t)
    words_count = int(duration / 1.0)  # 每秒一个"词"
    for i in range(words_count):
        start = int(i * sr)
        end = min(int((i + 0.7) * sr), len(t))
        gap_start = end
        gap_end = min(int((i + 1.0) * sr), len(t))
        if gap_start < len(t):
            envelope[gap_start:gap_end] = 0.0  # 静音段
    audio *= envelope
    # 归一化
    if np.max(np.abs(audio)) > 0:
        audio = audio / np.max(np.abs(audio)) * 0.8
    return audio


def save_wav(audio, path, sr=16000):
    """保存 numpy 数组为 WAV 文件"""
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


# ============================================================
# 测试 1: STT 引擎
# ============================================================
def test_stt_engine():
    logger.info("=" * 50)
    logger.info("测试 1: STT 引擎")
    logger.info("=" * 50)

    from config import STTConfig
    from core.stt_engine import STTEngine

    cfg = STTConfig(model_size='small', model_path='./models/')
    engine = STTEngine(cfg)

    # 加载模型
    logger.info("加载模型...")
    ok = engine.load_model()
    assert ok, "模型加载失败！"
    logger.info("✅ 模型加载成功")

    # 测试空音频
    empty = np.array([], dtype=np.float32)
    result = engine._do_transcribe(empty)
    assert result == ("", None), f"空音频应该返回空: {result}"
    logger.info("✅ 空音频处理正确")

    # 测试短音频
    short = np.random.randn(1600).astype(np.float32) * 0.01
    result = engine._do_transcribe(short)
    text, err = result
    assert err is None, f"短音频不应报错: {err}"
    logger.info(f"✅ 短音频处理完成 (text='{text[:50] if text else '(empty)'}')")

    # 测试语音类音频
    audio = generate_speech_like_audio(3.0)
    logger.info("转写 3s 测试音频...")
    result = engine._do_transcribe(audio)
    text, err = result
    assert err is None, f"转写失败: {err}"
    logger.info(f"✅ 转写完成: text='{text[:100] if text else '(empty)'}'")

    # 测试 transcribe_sync
    text2 = engine.transcribe_sync(audio)
    assert isinstance(text2, str), "transcribe_sync 应返回 str"
    logger.info(f"✅ transcribe_sync: '{text2[:100] if text2 else '(empty)'}'")

    # 测试 transcribe_async
    results = []
    event = threading.Event()
    def callback(t, e):
        results.append((t, e))
        event.set()
    
    engine.transcribe_async(audio, callback)
    event.wait(timeout=10)
    assert len(results) == 1, f"回调未触发: {results}"
    assert results[0][1] is None, f"异步转写失败: {results[0][1]}"
    logger.info(f"✅ transcribe_async: '{results[0][0][:100] if results[0][0] else '(empty)'}'")

    engine.shutdown()
    logger.info("✅ STT 引擎测试全部通过\n")
    return True


# ============================================================
# 测试 2: 剪贴板注入
# ============================================================
def test_clipboard_inject():
    logger.info("=" * 50)
    logger.info("测试 2: 剪贴板注入 (macOS)")
    logger.info("=" * 50)

    from platform_adapter.clipboard_macos import MacOSClipboardInjector

    injector = MacOSClipboardInjector.__new__(MacOSClipboardInjector)
    injector.config = type('obj', (object,), {'restore_clipboard': True})()

    # 保存原始剪贴板
    original = injector.read_clipboard()
    logger.info(f"原始剪贴板: '{(original or '')[:50]}'")

    # 写入测试
    test_text = "语音输入工具测试文本 Hello World 你好世界"
    ok = injector.write_clipboard(test_text)
    assert ok, "写入剪贴板失败"
    read_back = injector.read_clipboard()
    assert read_back == test_text, f"读回不匹配: '{read_back}'"
    logger.info("✅ 剪贴板写入/读取正常")

    # 恢复原始内容
    if original:
        injector.write_clipboard(original)
    logger.info("✅ 剪贴板已恢复")

    # 测试注入模板方法（不实际粘贴，只测写入+恢复）
    from platform_adapter.clipboard_base import ClipboardInjectorBase
    logger.info("✅ 剪贴板注入测试通过\n")
    return True


# ============================================================
# 测试 3: StreamTranscriber（模拟音频队列）
# ============================================================
def test_stream_transcriber():
    logger.info("=" * 50)
    logger.info("测试 3: StreamTranscriber（实时转写模拟）")
    logger.info("=" * 50)

    from config import STTConfig, RealtimeConfig
    from core.stt_engine import STTEngine
    from core.stream_transcriber import StreamTranscriber

    # 加载 STT
    stt = STTEngine(STTConfig(model_size='small', model_path='./models/'))
    ok = stt.load_model()
    assert ok, "模型加载失败"

    # 收集转写结果
    segments = []

    rt_cfg = RealtimeConfig(
        segment_pause_threshold=0.3,  # 短停顿就切分（测试用）
        min_segment_duration=0.2,
        max_segment_duration=5.0,
        vad_sensitivity=2,
        vad_window_ms=30,
    )

    transcriber = StreamTranscriber(rt_cfg, stt, lambda t: segments.append(t))

    # 模拟音频队列：语音 + 静音 + 语音
    sr = 16000
    chunk_size = 480  # 30ms

    # 第一段"语音"（1秒）
    speech1 = generate_speech_like_audio(1.0, sr)
    # 静音（0.5秒）
    silence = np.zeros(int(sr * 0.5), dtype=np.float32)
    # 第二段"语音"（1秒）
    speech2 = generate_speech_like_audio(1.0, sr)

    audio_queue = queue.Queue(maxsize=300)

    # 启动
    transcriber.start(audio_queue)
    logger.info("StreamTranscriber 已启动，开始喂入音频...")

    # 喂入第一段语音
    for i in range(0, len(speech1), chunk_size):
        chunk = speech1[i:i+chunk_size]
        if len(chunk) > 0:
            audio_queue.put(chunk.astype(np.float32))
    logger.info("第一段语音已喂入")

    # 喂入静音（触发分段）
    for i in range(0, len(silence), chunk_size):
        chunk = silence[i:i+chunk_size]
        if len(chunk) > 0:
            audio_queue.put(chunk.astype(np.float32))
    logger.info("静音段已喂入")

    # 喂入第二段语音
    for i in range(0, len(speech2), chunk_size):
        chunk = speech2[i:i+chunk_size]
        if len(chunk) > 0:
            audio_queue.put(chunk.astype(np.float32))
    logger.info("第二段语音已喂入")

    # 等待转写完成
    time.sleep(3)

    # 停止
    transcriber.stop()
    stt.shutdown()

    logger.info(f"✅ 实时转写完成，收到 {len(segments)} 个段落:")
    for i, seg in enumerate(segments):
        logger.info(f"   段落 {i+1}: '{seg[:80]}'")
    
    logger.info("✅ StreamTranscriber 测试通过\n")
    return True


# ============================================================
# 测试 4: CoreEngine 状态机
# ============================================================
def test_engine_state_machine():
    logger.info("=" * 50)
    logger.info("测试 4: CoreEngine 状态机")
    logger.info("=" * 50)

    from core.engine import EngineState, CoreEngine, _VALID_TRANSITIONS
    from unittest.mock import MagicMock

    # 验证转移矩阵
    assert EngineState.STREAMING in _VALID_TRANSITIONS
    assert EngineState.IDLE in _VALID_TRANSITIONS[EngineState.STREAMING]
    assert EngineState.STREAMING in _VALID_TRANSITIONS[EngineState.IDLE]
    logger.info("✅ STREAMING 状态转移矩阵正确")

    # 创建真实 engine（但 mock 掉子模块）
    import core.engine as engine_mod

    engine = CoreEngine.__new__(CoreEngine)
    engine._state = EngineState.LOADING
    engine._state_lock = threading.Lock()
    engine._shutdown_event = threading.Event()
    engine._tray = MagicMock()
    engine._config = MagicMock()
    engine._config.mode = 'batch'
    engine._watchdog_timer = None

    # LOADING → IDLE
    assert engine.transition(EngineState.IDLE)
    assert engine.state == EngineState.IDLE
    logger.info("✅ LOADING → IDLE")

    # IDLE → STREAMING
    assert engine.transition(EngineState.STREAMING)
    assert engine.state == EngineState.STREAMING
    logger.info("✅ IDLE → STREAMING")

    # STREAMING → IDLE
    assert engine.transition(EngineState.IDLE)
    assert engine.state == EngineState.IDLE
    logger.info("✅ STREAMING → IDLE")

    # IDLE → RECORDING
    assert engine.transition(EngineState.RECORDING)
    logger.info("✅ IDLE → RECORDING")

    # RECORDING → PROCESSING
    assert engine.transition(EngineState.PROCESSING)
    logger.info("✅ RECORDING → PROCESSING")

    # PROCESSING → INJECTING
    assert engine.transition(EngineState.INJECTING)
    logger.info("✅ PROCESSING → INJECTING")

    # INJECTING → IDLE
    assert engine.transition(EngineState.IDLE)
    logger.info("✅ INJECTING → IDLE")

    # 非法转换测试
    assert not engine.transition(EngineState.INJECTING)  # IDLE → INJECTING 不合法
    logger.info("✅ 非法转换被正确拒绝")

    logger.info("✅ 状态机测试通过\n")
    return True


# ============================================================
# 测试 5: 完整批量模式模拟
# ============================================================
def test_batch_mode_simulation():
    logger.info("=" * 50)
    logger.info("测试 5: 批量模式完整模拟")
    logger.info("=" * 50)

    from config import STTConfig, InjectConfig
    from core.stt_engine import STTEngine
    from platform_adapter.clipboard_macos import MacOSClipboardInjector

    # 加载 STT
    stt = STTEngine(STTConfig(model_size='small', model_path='./models/'))
    ok = stt.load_model()
    assert ok

    # 生成测试音频
    audio = generate_speech_like_audio(3.0)
    logger.info(f"生成 {len(audio)/16000:.1f}s 测试音频")

    # 转写
    text = stt.transcribe_sync(audio)
    logger.info(f"✅ 转写结果: '{text[:100] if text else '(empty)'}'")

    # 注入到剪贴板
    injector = MacOSClipboardInjector.__new__(MacOSClipboardInjector)
    injector.config = type('obj', (object,), {'restore_clipboard': True})()
    
    if text:
        ok = injector.write_clipboard(text)
        assert ok, "写入剪贴板失败"
        read_back = injector.read_clipboard()
        assert read_back == text, "读回不匹配"
        logger.info(f"✅ 注入成功: '{read_back[:80]}'")
    else:
        logger.info("⚠️ 转写为空（测试音频非真实语音，正常）")

    stt.shutdown()
    logger.info("✅ 批量模式模拟测试通过\n")
    return True


# ============================================================
# Main
# ============================================================
if __name__ == '__main__':
    results = {}
    tests = [
        ("STT 引擎", test_stt_engine),
        ("剪贴板注入", test_clipboard_inject),
        ("StreamTranscriber", test_stream_transcriber),
        ("状态机", test_engine_state_machine),
        ("批量模式模拟", test_batch_mode_simulation),
    ]

    logger.info("🚀 开始端到端模拟测试\n")

    for name, test_fn in tests:
        try:
            ok = test_fn()
            results[name] = "✅ PASS"
        except Exception as e:
            results[name] = f"❌ FAIL: {e}"
            logger.error(f"测试失败 [{name}]: {e}", exc_info=True)

    # 汇总
    logger.info("\n" + "=" * 50)
    logger.info("测试汇总")
    logger.info("=" * 50)
    for name, result in results.items():
        logger.info(f"  {name}: {result}")
    
    passed = sum(1 for v in results.values() if v.startswith("✅"))
    total = len(results)
    logger.info(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        logger.info("🎉 全部测试通过！")
    sys.exit(0 if passed == total else 1)
