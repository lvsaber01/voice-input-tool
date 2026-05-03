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
    PAUSED = auto()         # 实时转写暂停中
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
    EngineState.STREAMING: {EngineState.IDLE, EngineState.PAUSED},
    EngineState.PAUSED: {EngineState.STREAMING, EngineState.IDLE},
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

        # 实时转写段落追踪（导出用）
        self._segment_counter = 0
        self._segments_lock = threading.Lock()
        self._session_segments: list = []

        # 子模块
        from core.recorder import AudioRecorder
        from core.silence_detector import SilenceDetector
        from core.injector import TextInjector
        from core.sound_player import SoundPlayer
        
        # 根据配置选择 STT 引擎和转写模式
        if config.mode == "realtime" and hasattr(config, 'stt_realtime'):
            stt_config = config.stt_realtime.resolve(config.stt)
            logger.info("realtime 模式：使用 stt_realtime 配置 (engine=%s, model=%s)",
                        stt_config.engine, stt_config.model_size)
        else:
            stt_config = config.stt

        self._create_stt_engine(stt_config)

        self._recorder = AudioRecorder(config.audio)
        self._silence_detector = SilenceDetector(config.audio, self._on_silence_timeout,
                                                   on_rms_update=self._on_rms_update)
        self._injector = TextInjector(config.inject)
        self._sound_player = SoundPlayer(config.sound)

        # 热词系统
        self._init_hotword_pipeline(config)
        
        # 流式模式音频队列（由 engine.py 创建，应用 max_queue_size）
        # 注意：max_queue_size 已在 _create_stt_engine 中设置

        # 启动静音检测消费者线程
        self._silence_detector.start(self._recorder.get_buffer_queue())

    def _create_stt_engine(self, stt_config):
        """根据 STT 配置创建引擎和转写器（从 __init__ 抽取）"""
        stt_engine_type = stt_config.engine

        # auto 模式检测逻辑（与原 __init__ 一致）
        if stt_engine_type == "auto":
            import platform
            if platform.system() == "Darwin":
                stt_engine_type = "mlx_whisper"
            else:
                whisper_sizes = ('tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo')
                if stt_config.model_size in whisper_sizes:
                    stt_engine_type = "faster_whisper"
                    logger.info("auto 模式：检测到 whisper model_size，保持 faster_whisper")
                else:
                    stt_engine_type = "funasr"
                    # auto 模式 Windows/Linux 默认用 SenseVoiceSmall
                    funasr_sizes = ('paraformer-zh', 'paraformer-zh-streaming', 'paraformer-en', 'SenseVoiceSmall', 'Fun-ASR-Nano')
                    if stt_config.model_size not in funasr_sizes:
                        stt_config.model_size = 'SenseVoiceSmall'
            logger.info("auto 模式：选择 %s 引擎", stt_engine_type)

        streaming_enabled = getattr(stt_config.streaming, 'enabled', False) if \
            stt_engine_type in ('funasr', 'mlx_whisper') else False

        if streaming_enabled and stt_engine_type == 'funasr':
            from core.stt_funasr_streaming import FunASRStreamingEngine
            from core.streaming_transcriber import StreamingTranscriber
            self._stt_engine = FunASRStreamingEngine(stt_config)
            self._stream_transcriber = StreamingTranscriber(stt_config.streaming)
            self._streaming_mode = True
            logger.info("使用流式转写模式 (FunASR streaming)")
        elif stt_engine_type == 'funasr':
            from core.stt_funasr import FunASREngine
            from core.vad_segment_transcriber import VADSegmentTranscriber
            self._stt_engine = FunASREngine(stt_config)
            self._stream_transcriber = VADSegmentTranscriber(
                self._config.realtime, self._stt_engine, self._on_realtime_segment)
            self._streaming_mode = False
            logger.info("使用VAD分段转写模式 (FunASR)")
        elif stt_engine_type == 'mlx_whisper':
            from core.stt_mlx_whisper import MlxWhisperEngine
            from core.vad_segment_transcriber import VADSegmentTranscriber
            self._stt_engine = MlxWhisperEngine(stt_config)
            self._stream_transcriber = VADSegmentTranscriber(
                self._config.realtime, self._stt_engine, self._on_realtime_segment)
            self._streaming_mode = False
            logger.info("使用VAD分段转写模式 (mlx-whisper)")
        elif stt_engine_type == 'qwen3_asr':
            from core.stt_qwen3_asr import Qwen3ASREngine
            from core.vad_segment_transcriber import VADSegmentTranscriber
            self._stt_engine = Qwen3ASREngine(stt_config)
            self._stream_transcriber = VADSegmentTranscriber(
                self._config.realtime, self._stt_engine, self._on_realtime_segment)
            self._streaming_mode = False
            logger.info("使用VAD分段转写模式 (Qwen3-ASR)")
        else:
            from core.stt_engine import STTEngine
            from core.vad_segment_transcriber import VADSegmentTranscriber
            self._stt_engine = STTEngine(stt_config)
            self._stream_transcriber = VADSegmentTranscriber(
                self._config.realtime, self._stt_engine, self._on_realtime_segment)
            self._streaming_mode = False
            logger.info("使用VAD分段转写模式 (faster-whisper)")

        # 音频队列初始化
        max_queue_size = getattr(stt_config.streaming, 'max_queue_size', 300) if self._streaming_mode else 300
        self._rt_audio_queue = None  # 在 _start_streaming 时创建
        self._rt_max_queue_size = max_queue_size

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
    # 热词系统
    # ============================================================

    def _init_hotword_pipeline(self, config):
        """初始化热词管理系统（HotwordManager + TextPipeline）。"""
        try:
            from core.hotword import HotwordManager
            from core.text_pipeline import TextPipeline

            hw_config = getattr(config, 'hotword', None)
            if hw_config is None:
                logger.info("热词配置未设置，跳过热词系统初始化")
                self._hotword_manager = None
                self._text_pipeline = None
                self._punctuation_restorer = None
                return

            self._hotword_manager = HotwordManager(
                hotwords_file=hw_config.hotwords_file,
                rules_file=hw_config.rules_file,
                min_word_length=hw_config.min_word_length,
            )
            self._text_pipeline = TextPipeline(
                hotword_manager=self._hotword_manager,
                enabled=hw_config.enabled,
                case_sensitive=hw_config.case_sensitive,
            )
            self._text_pipeline.register_reload_callback(self._on_pipeline_reloaded)

            # ITN 数字转换步骤（根据配置注册）
            itn_config = getattr(config, 'itn', None)
            if itn_config is None or getattr(itn_config, 'enabled', True):
                from core.itn import ITNStep
                self._text_pipeline.add_step(ITNStep(enabled=True))
                logger.info("ITN 数字转换已启用")
            else:
                logger.info("ITN 数字转换已禁用")

            # 同步 FunASR 原生热词
            self._on_pipeline_reloaded()

            # F3: 标点恢复初始化（仅无标点 STT 模型）
            self._punctuation_restorer = None
            stt_engine_type = getattr(config.stt, "engine", "auto")
            no_punc_models = {
                "SenseVoiceSmall",
                "SenseVoiceMedium",
                "SenseVoiceLarge",
                "Fun-ASR-Nano",
            }
            model_size = config.stt.model_size

            if stt_engine_type == "funasr" and model_size in no_punc_models:
                try:
                    from core.punctuation import PunctuationRestorer
                    from core.pipeline_step import StepNames

                    self._punctuation_restorer = PunctuationRestorer(enabled=True)

                    # 尝试复用 FunASR 引擎的 ct-punc 实例
                    shared = self._get_stt_ct_punc_model()
                    if shared is not None:
                        self._punctuation_restorer.set_shared_model(shared)

                    # 通过 get_step 接口设置标点恢复器
                    punct_step = self._text_pipeline.get_step(StepNames.PUNCTUATION)
                    if punct_step is not None:
                        punct_step.set_restorer(self._punctuation_restorer)
                    else:
                        self._text_pipeline.set_punctuation_restorer(self._punctuation_restorer)
                    logger.info("标点恢复已启用（%s 模式）", model_size)
                except ImportError:
                    logger.warning("标点恢复模块不可用")

            # F2: 文件监控初始化
            self._file_watcher = None
            try:
                from core.file_watcher import FileWatcher

                reload_fn = (
                    lambda: self._text_pipeline.schedule_reload()
                    if self._text_pipeline
                    else None
                )
                watch_paths = {}

                if self._hotword_manager:
                    for filename in ["hotwords.txt", "hotwords-phoneme.txt", "rules.txt"]:
                        file_dir = self._hotword_manager.data_dir
                        if file_dir:
                            watch_paths[str(file_dir / filename)] = reload_fn

                if watch_paths:
                    self._file_watcher = FileWatcher(watch_paths, debounce_seconds=1.0)
                    self._file_watcher.start()
                    logger.info("文件监控已启动，监控 %d 个文件", len(watch_paths))
            except ImportError:
                logger.warning("watchdog 未安装，文件监控不可用。pip install watchdog")
            except Exception as e:
                logger.warning("文件监控启动失败: %s", e)

            logger.info("热词系统初始化完成: enabled=%s, case_sensitive=%s",
                       hw_config.enabled, hw_config.case_sensitive)
        except Exception as e:
            logger.error("热词系统初始化失败，降级为禁用: %s", e, exc_info=True)
            self._hotword_manager = None
            self._text_pipeline = None
            self._punctuation_restorer = None

    def _on_pipeline_reloaded(self):
        """pipeline reload 后同步更新 FunASR 原生热词。"""
        if self._hotword_manager and self._stt_engine:
            if hasattr(self._stt_engine, 'load_hotwords'):
                hotword_list = self._hotword_manager.get_model_hotword_list()
                self._stt_engine.load_hotwords(hotword_list)

    def _get_stt_ct_punc_model(self):
        """获取 STT 引擎的 ct-punc 模型实例。

        按优先级尝试：
        1. FunASREngine.get_punctuation_model()（公开方法）
        2. 向后兼容：直接访问内部属性

        注意：如果引擎尚未加载模型，可能返回 None。
        """
        if not self._stt_engine:
            return None
        if hasattr(self._stt_engine, "get_punctuation_model"):
            try:
                model = self._stt_engine.get_punctuation_model()
                if model is not None:
                    return model
            except Exception:
                pass
        # 向后兼容
        if hasattr(self._stt_engine, "model") and hasattr(
            self._stt_engine.model, "punc_model"
        ):
            return self._stt_engine.model.punc_model
        return None

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

            # 清理会话段落数据
            with self._segments_lock:
                self._session_segments.clear()
                self._segment_counter = 0

    def toggle_pause(self):
        """暂停/继续实时转写（互斥锁 + 停止生产者 + 回滚）。

        STREAMING → PAUSED：暂停 transcriber + recorder，播放暂停音效
        PAUSED → STREAMING：恢复 transcriber + recorder，播放恢复音效
        """
        with self._state_lock:  # 防止多线程并发 toggle
            if self._state == EngineState.STREAMING:
                try:
                    self._stream_transcriber.pause()
                    if hasattr(self._recorder, 'pause'):
                        self._recorder.pause()
                except Exception as e:
                    logger.error("暂停失败: %s", e)
                    return
                if self._transition_locked(EngineState.PAUSED):
                    self._sound_player.play("pause")
                    self._events.publish(EngineEvent.RECORDING_PAUSED)
                else:
                    # 状态转移失败，回滚
                    try:
                        self._stream_transcriber.resume()
                        if hasattr(self._recorder, 'resume'):
                            self._recorder.resume()
                    except Exception as e:
                        logger.error("暂停回滚失败: %s", e)
            elif self._state == EngineState.PAUSED:
                try:
                    self._stream_transcriber.resume()
                    if hasattr(self._recorder, 'resume'):
                        self._recorder.resume()
                except Exception as e:
                    logger.error("恢复失败: %s", e)
                    return
                if self._transition_locked(EngineState.STREAMING):
                    self._sound_player.play("resume")
                    self._events.publish(EngineEvent.RECORDING_RESUMED)
                else:
                    # 状态转移失败，回滚
                    try:
                        self._stream_transcriber.pause()
                        if hasattr(self._recorder, 'pause'):
                            self._recorder.pause()
                    except Exception as e:
                        logger.error("恢复回滚失败: %s", e)
            else:
                logger.debug("toggle_pause: 当前状态 %s，忽略", self._state.name)

    def get_session_segments(self) -> list:
        """获取当前实时转写会话的所有段落数据。"""
        with self._segments_lock:
            return list(self._session_segments)

    def export_session(self, filepath: str, format: str = "txt") -> bool:
        """导出当前转写会话到文件。

        Args:
            filepath: 输出文件路径
            format: 导出格式 (txt/markdown)
        Returns:
            True=导出成功
        """
        with self._segments_lock:
            segments = list(self._session_segments)

        if not segments:
            logger.warning("无段落数据可导出")
            return False

        try:
            if format == "markdown":
                lines = ["# 转写记录", ""]
                for i, seg in enumerate(segments, 1):
                    lines.append(f"{i}. {seg}")
                content = "\n".join(lines)
            else:
                content = "\n".join(segments)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("会话已导出: %s (%d 段)", filepath, len(segments))
            return True
        except Exception as e:
            logger.error("导出失败: %s", e)
            return False

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

        # 实时模式走 pipeline（清理噪声标记等），但不做命令匹配
        if self._text_pipeline:
            try:
                result = self._text_pipeline.process(text)
                text = result.text
                if not text.strip():
                    return  # pipeline 清理后为空，跳过
                # 更新 full_text
                full_text = timestamp_prefix + text + separator
            except Exception as e:
                logger.warning("实时 pipeline 处理异常，使用原文: %s", e)

        try:
            self._injector.inject(full_text)
        except Exception as e:
            logger.error("实时段落注入失败: %s", e)

        # 段落追踪
        with self._segments_lock:
            self._segment_counter += 1
            self._session_segments.append(text)

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

        # 未匹配命令，走 pipeline 处理（命令优先策略）
        if self._text_pipeline:
            try:
                pipeline_result = self._text_pipeline.process(text)
                text = pipeline_result.text
                if pipeline_result.is_changed:
                    logger.info("Pipeline 处理: %s", text[:100])
            except Exception as e:
                logger.warning("Pipeline 处理异常，使用原文: %s", e)

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
        # v3.1: 幂等保护，防止多次调用
        if self._shutdown_event.is_set():
            logger.debug("shutdown 已执行，跳过重复调用")
            return
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
        # F2: 文件监控停止
        if hasattr(self, '_file_watcher') and self._file_watcher:
            try:
                self._file_watcher.stop()
            except Exception as e:
                logger.warning("停止文件监控失败: %s", e)
        # F3: 标点恢复关闭（在 FunASR engine shutdown 之前）
        if hasattr(self, '_punctuation_restorer') and self._punctuation_restorer:
            try:
                self._punctuation_restorer.shutdown()
            except Exception as e:
                logger.warning("停止标点恢复失败: %s", e)
        if self._stt_engine:
            self._stt_engine.shutdown()

        # 发布关闭事件
        self._events.publish(EngineEvent.ENGINE_SHUTDOWN)

        # 关闭事件总线
        self._events.shutdown()

        logger.info("CoreEngine 关闭完成")

        if self._on_shutdown_complete:
            self._on_shutdown_complete()
