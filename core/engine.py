"""CoreEngine 状态机 — 核心调度引擎

按照设计文档 3.3 节实现完整的状态转移矩阵和引擎接口。
所有子模块已集成：recorder, silence_detector, stt_engine, injector, sound_player, tray, web_server。
V2 增强：引入 EventBus 事件总线，tray 调用通过事件异步执行，消除死锁风险。
"""

import threading
import logging
from enum import Enum, auto
from typing import Optional

from core.events import EventBus, EngineEvent

logger = logging.getLogger(__name__)


class EngineState(Enum):
    """引擎状态枚举"""
    IDLE = auto()           # 就绪，等待热键触发
    RECORDING = auto()      # 录音中（批量模式）
    PROCESSING = auto()     # STT 识别中（批量模式）
    STREAMING = auto()      # 实时转写监听中（实时模式）
    INJECTING = auto()      # 文字注入中
    ERROR = auto()          # 错误状态（可恢复）
    LOADING = auto()        # 模型加载中（启动阶段）


# ============================================================
# 状态转移矩阵
# ============================================================

_VALID_TRANSITIONS: dict[EngineState, set[EngineState]] = {
    EngineState.LOADING: {EngineState.IDLE, EngineState.ERROR},
    EngineState.IDLE: {EngineState.RECORDING, EngineState.STREAMING, EngineState.LOADING},
    EngineState.RECORDING: {EngineState.PROCESSING},
    EngineState.PROCESSING: {EngineState.INJECTING, EngineState.IDLE},
    EngineState.STREAMING: {EngineState.IDLE},
    EngineState.INJECTING: {EngineState.IDLE},
    EngineState.ERROR: {EngineState.LOADING, EngineState.IDLE},
}

_STATE_TIMEOUTS: dict[EngineState, float] = {
    EngineState.PROCESSING: 30.0,
    EngineState.INJECTING: 5.0,
}


class CoreEngine:
    """核心调度引擎，状态机驱动。

    所有状态转换通过 _state_lock 保护，保证线程安全。
    事件总线 EventBus 解耦 tray/stats 等外部组件。
    """

    def __init__(self, config, tray, on_shutdown_complete=None):
        self._config = config
        self._tray = tray
        self._on_shutdown_complete = on_shutdown_complete

        # 事件总线
        self._events = EventBus()

        # 状态
        self._state = EngineState.LOADING
        self._state_lock = threading.Lock()
        self._shutdown_event = threading.Event()

        # 超时看门狗 timer 引用
        self._watchdog_timer: Optional[threading.Timer] = None

        # 录音最大时长 timer
        self._max_duration_timer: Optional[threading.Timer] = None

        # 子模块
        from core.recorder import AudioRecorder
        from core.silence_detector import SilenceDetector
        from core.injector import TextInjector
        from core.sound_player import SoundPlayer
        
        # 根据配置选择 STT 引擎和转写模式
        stt_engine_type = getattr(config.stt, 'engine', 'auto')

        # auto 模式：macOS 选 mlx_whisper，其他选 funasr
        if stt_engine_type == 'auto':
            import platform
            if platform.system() == 'Darwin':
                stt_engine_type = 'mlx_whisper'
            else:
                # 向后兼容：如果用户之前的 model_size 是 whisper 格式，保持 faster_whisper
                whisper_sizes = ('tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo')
                if config.stt.model_size in whisper_sizes:
                    stt_engine_type = 'faster_whisper'
                    logger.info("auto 模式：检测到 whisper model_size，保持 faster_whisper")
                else:
                    stt_engine_type = 'funasr'
                    # auto 模式 Windows/Linux 默认用 SenseVoiceSmall
                    funasr_sizes = ('paraformer-zh', 'paraformer-zh-streaming', 'paraformer-en', 'SenseVoiceSmall', 'Fun-ASR-Nano')
                    if config.stt.model_size not in funasr_sizes:
                        config.stt.model_size = 'SenseVoiceSmall'
            logger.info("auto 模式：选择 %s 引擎", stt_engine_type)

        # streaming_enabled 对 mlx_whisper 和 funasr 都生效
        streaming_enabled = getattr(config.stt.streaming, 'enabled', False) if \
            stt_engine_type in ('funasr', 'mlx_whisper') else False

        if streaming_enabled and stt_engine_type == 'funasr':
            # 流式模式：FunASR 流式引擎 + StreamingTranscriber
            from core.stt_funasr_streaming import FunASRStreamingEngine
            from core.streaming_transcriber import StreamingTranscriber
            self._stt_engine = FunASRStreamingEngine(config.stt)
            self._stream_transcriber = StreamingTranscriber(config.stt.streaming)
            self._streaming_mode = True
            logger.info("使用流式转写模式 (FunASR streaming)")
        elif stt_engine_type == 'funasr':
            # 分段模式：FunASR 非流式引擎 + VAD分段转写器
            from core.stt_funasr import FunASREngine
            from core.vad_segment_transcriber import VADSegmentTranscriber
            self._stt_engine = FunASREngine(config.stt)
            self._stream_transcriber = VADSegmentTranscriber(
                config.realtime, self._stt_engine, self._on_realtime_segment
            )
            self._streaming_mode = False
            logger.info("使用VAD分段转写模式 (FunASR)")
        elif stt_engine_type == 'mlx_whisper':
            # mlx-whisper：分段模式（VADSegmentTranscriber + MlxWhisperEngine）
            from core.stt_mlx_whisper import MlxWhisperEngine
            from core.vad_segment_transcriber import VADSegmentTranscriber
            self._stt_engine = MlxWhisperEngine(config.stt)
            self._stream_transcriber = VADSegmentTranscriber(
                config.realtime, self._stt_engine, self._on_realtime_segment
            )
            self._streaming_mode = False
            logger.info("使用VAD分段转写模式 (mlx-whisper)")
        elif stt_engine_type == 'qwen3_asr':
            # Qwen3-ASR：分段模式
            from core.stt_qwen3_asr import Qwen3ASREngine
            from core.vad_segment_transcriber import VADSegmentTranscriber
            self._stt_engine = Qwen3ASREngine(config.stt)
            self._stream_transcriber = VADSegmentTranscriber(
                config.realtime, self._stt_engine, self._on_realtime_segment
            )
            self._streaming_mode = False
            logger.info("使用VAD分段转写模式 (Qwen3-ASR)")
        else:
            # faster-whisper：只有分段模式
            from core.stt_engine import STTEngine
            from core.vad_segment_transcriber import VADSegmentTranscriber
            self._stt_engine = STTEngine(config.stt)
            self._stream_transcriber = VADSegmentTranscriber(
                config.realtime, self._stt_engine, self._on_realtime_segment
            )
            self._streaming_mode = False
            logger.info("使用VAD分段转写模式 (faster-whisper)")

        self._recorder = AudioRecorder(config.audio)
        self._silence_detector = SilenceDetector(config.audio, self._on_silence_timeout,
                                                   on_rms_update=self._on_rms_update)
        self._injector = TextInjector(config.inject)
        self._sound_player = SoundPlayer(config.sound)
        
        # 流式模式音频队列（由 engine.py 创建，应用 max_queue_size）
        max_queue_size = getattr(config.stt.streaming, 'max_queue_size', 300) if self._streaming_mode else 300
        self._rt_audio_queue = None  # 在 _start_streaming 时创建
        self._rt_max_queue_size = max_queue_size

        # 启动静音检测消费者线程
        self._silence_detector.start(self._recorder.get_buffer_queue())

    # ============================================================
    # 属性
    # ============================================================

    @property
    def state(self) -> EngineState:
        with self._state_lock:
            return self._state

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown_event.is_set()

    @property
    def events(self) -> EventBus:
        """暴露事件总线，供外部（main.py）订阅。"""
        return self._events

    # ============================================================
    # 公开方法
    # ============================================================

    def on_hotkey_start(self):
        if self._shutdown_event.is_set():
            return
        if self.state == EngineState.IDLE:
            self._start_recording()

    def on_hotkey_stop(self):
        if self._shutdown_event.is_set():
            return
        if self.state == EngineState.RECORDING:
            self._stop_recording_and_transcribe()

    def on_hotkey_toggle(self):
        if self._shutdown_event.is_set():
            return
        current = self.state
        if current == EngineState.IDLE:
            if self._config.mode == "realtime":
                self._start_streaming()
            else:
                self._start_recording()
        elif current == EngineState.RECORDING:
            self._stop_recording_and_transcribe()
        elif current == EngineState.STREAMING:
            self._stop_streaming()

    def on_tray_start_stop(self):
        self.on_hotkey_toggle()

    def on_tray_retry_model(self):
        if self._shutdown_event.is_set():
            return
        if self.state == EngineState.ERROR:
            if self.transition(EngineState.LOADING):
                self._load_model_async()

    # ============================================================
    # 状态转换
    # ============================================================

    def transition(self, target: EngineState) -> bool:
        with self._state_lock:
            return self._transition_locked(target)

    def _transition_locked(self, target: EngineState) -> bool:
        if self._shutdown_event.is_set():
            return False

        current = self._state
        valid_targets = _VALID_TRANSITIONS.get(current, set())

        if target not in valid_targets:
            logger.debug("忽略非法状态转换: %s → %s", current.name, target.name)
            return False

        logger.info("状态转换: %s → %s", current.name, target.name)
        self._state = target

        # 取消当前看门狗
        self._cancel_watchdog()

        # 启动新状态的超时看门狗
        timeout = _STATE_TIMEOUTS.get(target, 0)
        if timeout > 0:
            self._start_timeout_watchdog(target, timeout)

        # 通过事件总线发布状态变更（锁内提交到线程池是安全的）
        self._events.publish(EngineEvent.STATE_CHANGED, current, target)

        return True

    # ============================================================
    # 实时转写模式
    # ============================================================

    def _start_streaming(self):
        import queue as queue_mod
        if self.transition(EngineState.STREAMING):
            logger.info("开始实时转写")
            try:
                self._rt_audio_queue = queue_mod.Queue(maxsize=self._rt_max_queue_size)
                self._recorder.start(self._rt_audio_queue)
                
                # 获取 recorder 的 resampler，注入到实时转写器
                resampler = self._recorder.resampler
                if self._streaming_mode:
                    # 流式模式：传入引擎、回调和 resampler
                    self._stream_transcriber.start(self._rt_audio_queue, self._stt_engine, self._on_realtime_segment, resampler=resampler)
                else:
                    # VAD分段模式：传入队列和 resampler
                    self._stream_transcriber.start(self._rt_audio_queue, resampler=resampler)
                
                self._sound_player.play("start")
                self._events.publish(EngineEvent.RECORDING_STARTED)
            except Exception as e:
                logger.error("启动实时转写失败: %s", e)
                self.transition(EngineState.IDLE)

    def _stop_streaming(self):
        if self.transition(EngineState.IDLE):
            logger.info("停止实时转写")
            try:
                self._stream_transcriber.stop()
                self._recorder.stop()
                self._sound_player.play("end")
                self._events.publish(EngineEvent.RECORDING_STOPPED)
                if self._max_duration_timer:
                    self._max_duration_timer.cancel()
                    self._max_duration_timer = None
            except Exception as e:
                logger.error("停止实时转写失败: %s", e)

    def _on_realtime_segment(self, text: str):
        if self._shutdown_event.is_set():
            return

        separator = self._config.realtime.segment_separator
        timestamp_prefix = ""
        if self._config.realtime.auto_timestamp:
            import datetime
            timestamp_prefix = datetime.datetime.now().strftime("[%H:%M:%S] ")

        full_text = timestamp_prefix + text + separator
        logger.info("实时段落: %s", text[:50])

        try:
            self._injector.inject(full_text)
        except Exception as e:
            logger.error("实时段落注入失败: %s", e)

    # ============================================================
    # 内部方法（批量模式）
    # ============================================================

    def _start_recording(self):
        if self.transition(EngineState.RECORDING):
            logger.info("开始录音")
            try:
                self._recorder.start()
                self._silence_detector.begin_detection()
                self._sound_player.play("start")
                self._events.publish(EngineEvent.RECORDING_STARTED)

                # 启动最大录音时长定时器
                max_dur = getattr(self._config.audio, 'max_duration', 0)
                if max_dur and max_dur > 0:
                    self._max_duration_timer = threading.Timer(max_dur, self._on_max_duration_timeout)
                    self._max_duration_timer.daemon = True
                    self._max_duration_timer.start()
                    logger.debug("最大录音时长定时器: %.1fs", max_dur)
            except Exception as e:
                logger.error("启动录音失败: %s", e)
                self.transition(EngineState.IDLE)
                self._events.publish(EngineEvent.TRANSCRIBE_ERROR, e)

    def _stop_recording_and_transcribe(self):
        """停止录音并提交 STT（去重安全）。

        transition(RECORDING → PROCESSING) 持有 _state_lock，
        同一时刻只有一个线程能成功。
        """
        if not self.transition(EngineState.PROCESSING):
            logger.debug("_stop_recording: 状态已变，跳过（去重）")
            return

        logger.info("停止录音，开始识别")

        # 取消最大时长定时器
        if self._max_duration_timer:
            self._max_duration_timer.cancel()
            self._max_duration_timer = None

        try:
            audio = self._recorder.stop()
            self._silence_detector.end_detection()
            self._sound_player.play("end")
            self._events.publish(EngineEvent.RECORDING_STOPPED)
            # 基于秒数的最短时长判断（而非样本数）
            min_duration_sec = 0.2  # 200ms
            if len(audio) < int(min_duration_sec * 16000):
                logger.debug("录音时长不足 %.1fs，忽略", min_duration_sec)
                self.transition(EngineState.IDLE)
                self._events.publish(EngineEvent.TRANSCRIBE_COMPLETE, "", None, 0)
                return
            self._stt_engine.transcribe_async(audio, self._on_stt_complete)
        except Exception as e:
            logger.error("停止录音失败: %s", e)
            self.transition(EngineState.IDLE)
            self._events.publish(EngineEvent.TRANSCRIBE_ERROR, e)

    def _on_max_duration_timeout(self):
        """Timer 线程中执行，到达最大录音时长时自动提交。"""
        logger.info("达到最大录音时长，自动提交")
        # _stop_recording_and_transcribe 内部先 transition，成功才继续
        # 但我们需要在确认是最大时长触发时发布事件
        # 先发事件再停录音（因为 transition 成功后才知道是不是这个 timer 触发的）
        was_recording = self.state == EngineState.RECORDING
        self._stop_recording_and_transcribe()
        if was_recording:
            # 如果之前是 RECORDING 且 transition 成功了，说明是本 timer 触发的
            self._events.publish(EngineEvent.MAX_DURATION_TRIGGERED)

    def _on_silence_timeout(self):
        logger.info("静音超时，停止录音")
        self._stop_recording_and_transcribe()

    def _on_rms_update(self, rms: float, is_speech: bool):
        """RMS 更新回调，发布到事件总线"""
        self._events.publish(EngineEvent.RMS_UPDATE, rms, is_speech)

    def _on_stt_complete(self, text: str, language: Optional[str],
                         duration_ms: int, error: Optional[Exception]):
        """STT 完成回调（在线程池工作线程中执行）。"""
        logger.info("STT 回调触发: text='%s' lang=%s dur=%d err=%s",
                    (text[:30] if text else ''), language, duration_ms, error)
        try:
            self._on_stt_complete_inner(text, language, duration_ms, error)
        except Exception as e:
            logger.error("STT 回调异常: %s", e, exc_info=True)
            try:
                self.transition(EngineState.IDLE)
            except Exception:
                pass

    def _on_stt_complete_inner(self, text: str, language: Optional[str],
                         duration_ms: int, error: Optional[Exception]):
        """STT 完成回调内部逻辑。"""
        if self._shutdown_event.is_set():
            return

        if error:
            logger.error("STT 识别失败: %s", error)
            self.transition(EngineState.IDLE)
            self._events.publish(EngineEvent.TRANSCRIBE_ERROR, error)
            return

        if not text:
            logger.info("STT 返回空文本，未检测到有效语音")
            self.transition(EngineState.IDLE)
            # 空文本不是错误，用 TRANSCRIBE_COMPLETE 标记空结果
            self._events.publish(EngineEvent.TRANSCRIBE_COMPLETE, "", language, duration_ms)
            return

        # 有识别结果，发布转写完成事件
        self._events.publish(EngineEvent.TRANSCRIBE_COMPLETE, text, language, duration_ms)

        # 命令匹配（command.enabled 时先匹配命令再注入）
        command_cfg = getattr(self._config, 'command', None)
        if command_cfg and getattr(command_cfg, 'enabled', False):
            from core.command import CommandMatcher, CommandExecutor
            if not hasattr(self, '_command_matcher'):
                self._command_matcher = CommandMatcher()
                self._command_executor = CommandExecutor(
                    engine=self, injector=self._injector
                )
            result = self._command_matcher.match(text)
            if result:
                cmd, _ = result
                logger.info("匹配到语音命令: %s", cmd.name)
                success = self._command_executor.execute(cmd)
                if success:
                    self._events.publish(EngineEvent.COMMAND_EXECUTED, cmd.name)
                    self.transition(EngineState.IDLE)
                    self._sound_player.play("complete")
                    return
                # 命令执行失败，继续正常注入

        if self.transition(EngineState.INJECTING):
            self._inject_text(text)

    def _inject_text(self, text: str):
        logger.info("注入文本: %s", text[:50] + ("..." if len(text) > 50 else ""))
        try:
            success = self._injector.inject(text)
            if success:
                self._events.publish(EngineEvent.TEXT_INJECTED, text)
            else:
                # 保留 tray 直接引用用于降级提示（第 3 批迁移）
                if self._tray:
                    self._tray.show_notification("提示", "文字已复制到剪贴板，请手动粘贴 Ctrl+V")
        except Exception as e:
            logger.error("注入失败: %s", e)
            if self._tray:
                self._tray.show_notification("注入失败", str(e))

        self.transition(EngineState.IDLE)
        self._sound_player.play("complete")

    def _load_model_async(self):
        def _load():
            if self._shutdown_event.is_set():
                return
            if self._tray:
                self._tray.set_state(EngineState.LOADING)

            with self._state_lock:
                if self._state != EngineState.LOADING:
                    return
                success, error_msg = self._stt_engine.load_model()
                if success:
                    logger.info("模型加载完成")
                    self._transition_locked(EngineState.IDLE)
                    if self._tray:
                        self._tray.set_state(EngineState.IDLE)
                        self._tray.show_notification("就绪", "语音输入工具已启动")
                else:
                    logger.error("模型加载失败: %s", error_msg)
                    self._transition_locked(EngineState.ERROR)
                    if self._tray:
                        self._tray.set_state(EngineState.ERROR)
                        self._tray.show_notification("错误",
                            f"模型加载失败: {error_msg}\n\n日志目录: logs/")

        t = threading.Thread(target=_load, name="model-loader", daemon=True)
        t.start()

    # ============================================================
    # 超时看门狗
    # ============================================================

    def _start_timeout_watchdog(self, state: EngineState, timeout_s: float):
        def _check():
            old_state = None
            need_notify = False
            with self._state_lock:
                if self._state == state and not self._shutdown_event.is_set():
                    logger.warning("状态 %s 超时 (%.1fs)，强制回 IDLE", state.name, timeout_s)
                    old_state = self._state
                    self._transition_locked(EngineState.IDLE)
                    need_notify = True
            # 锁外发布事件，避免 ABBA 死锁
            if need_notify:
                self._events.publish(EngineEvent.TRANSCRIBE_ERROR,
                                     RuntimeError(f"状态 {state.name} 超时"))

        timer = threading.Timer(timeout_s, _check)
        timer.daemon = True
        timer.start()
        self._watchdog_timer = timer

    def _cancel_watchdog(self):
        if self._watchdog_timer is not None:
            self._watchdog_timer.cancel()
            self._watchdog_timer = None

    # ============================================================
    # 关闭
    # ============================================================

    def shutdown(self):
        logger.info("CoreEngine 开始关闭...")
        self._shutdown_event.set()

        # 取消最大时长定时器
        if self._max_duration_timer:
            self._max_duration_timer.cancel()
            self._max_duration_timer = None

        with self._state_lock:
            self._cancel_watchdog()

        # 停止子模块
        if hasattr(self, '_stream_transcriber') and self.state == EngineState.STREAMING:
            try:
                self._stream_transcriber.stop()
            except Exception as e:
                logger.warning("停止实时转写失败: %s", e)
        if self._recorder and self._recorder.is_recording:
            try:
                self._recorder.stop()
            except Exception as e:
                logger.warning("停止录音器失败: %s", e)
        if self._silence_detector:
            self._silence_detector.shutdown()
        if self._stt_engine:
            self._stt_engine.shutdown()

        # 发布关闭事件
        self._events.publish(EngineEvent.ENGINE_SHUTDOWN)

        # 关闭事件总线
        self._events.shutdown()

        logger.info("CoreEngine 关闭完成")

        if self._on_shutdown_complete:
            self._on_shutdown_complete()
