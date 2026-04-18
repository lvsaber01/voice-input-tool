"""CoreEngine 状态机 — 核心调度引擎

按照设计文档 3.3 节实现完整的状态转移矩阵和引擎接口。
所有子模块已集成：recorder, silence_detector, stt_engine, injector, sound_player, tray, web_server。
"""

import threading
import logging
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


class EngineState(Enum):
    """引擎状态枚举"""
    IDLE = auto()           # 就绪，等待热键触发
    RECORDING = auto()      # 录音中
    PROCESSING = auto()     # STT 识别中
    INJECTING = auto()      # 文字注入中
    ERROR = auto()          # 错误状态（可恢复）
    LOADING = auto()        # 模型加载中（启动阶段）


# ============================================================
# 状态转移矩阵
# ============================================================
# 格式: {当前状态: {目标状态集合}}
# 非法转换（不在矩阵中的）将被忽略

_VALID_TRANSITIONS: dict[EngineState, set[EngineState]] = {
    EngineState.LOADING: {EngineState.IDLE, EngineState.ERROR},
    EngineState.IDLE: {EngineState.RECORDING, EngineState.LOADING},
    EngineState.RECORDING: {EngineState.PROCESSING},
    EngineState.PROCESSING: {EngineState.INJECTING, EngineState.IDLE},
    EngineState.INJECTING: {EngineState.IDLE},
    EngineState.ERROR: {EngineState.LOADING, EngineState.IDLE},
}

# 各状态超时（秒），0 表示无超时
_STATE_TIMEOUTS: dict[EngineState, float] = {
    EngineState.PROCESSING: 30.0,
    EngineState.INJECTING: 5.0,
}


class CoreEngine:
    """核心调度引擎，状态机驱动。

    所有状态转换通过 _state_lock 保护，保证线程安全。
    子模块实例化推迟到 Phase 2。
    """

    def __init__(self, config, tray, on_shutdown_complete=None):
        """
        Args:
            config: AppConfig 实例
            tray: TrayIcon 实例（gui/tray.py）
            on_shutdown_complete: 关闭完成回调
        """
        self._config = config
        self._tray = tray
        self._on_shutdown_complete = on_shutdown_complete

        # 状态
        self._state = EngineState.LOADING
        self._state_lock = threading.Lock()
        self._shutdown_event = threading.Event()

        # 超时看门狗 timer 引用
        self._watchdog_timer: Optional[threading.Timer] = None

        # 子模块
        from core.recorder import AudioRecorder
        from core.silence_detector import SilenceDetector
        from core.stt_engine import STTEngine
        from core.injector import TextInjector
        from core.sound_player import SoundPlayer

        self._recorder = AudioRecorder(config.audio)
        self._silence_detector = SilenceDetector(config.audio, self._on_silence_timeout)
        self._stt_engine = STTEngine(config.stt)
        self._injector = TextInjector(config.inject)
        self._sound_player = SoundPlayer(config.sound)

        # 启动静音检测消费者线程
        self._silence_detector.start(self._recorder.get_buffer_queue())

    # ============================================================
    # 属性
    # ============================================================

    @property
    def state(self) -> EngineState:
        """当前引擎状态（线程安全读取）"""
        with self._state_lock:
            return self._state

    @property
    def is_shutdown(self) -> bool:
        """是否已收到关闭信号"""
        return self._shutdown_event.is_set()

    # ============================================================
    # 公开方法（由热键/托盘菜单调用）
    # ============================================================

    def on_hotkey_start(self):
        """热键按下（PTT 模式）。

        状态转移: IDLE → RECORDING
        """
        if self._shutdown_event.is_set():
            return
        if self.state == EngineState.IDLE:
            self._start_recording()

    def on_hotkey_stop(self):
        """热键释放（PTT 模式）。

        状态转移: RECORDING → PROCESSING
        """
        if self._shutdown_event.is_set():
            return
        if self.state == EngineState.RECORDING:
            self._stop_recording_and_transcribe()

    def on_hotkey_toggle(self):
        """Toggle 模式热键回调，根据当前状态决定 start/stop。"""
        if self._shutdown_event.is_set():
            return
        current = self.state
        if current == EngineState.IDLE:
            self._start_recording()
        elif current == EngineState.RECORDING:
            self._stop_recording_and_transcribe()
        # 其他状态忽略

    def on_tray_start_stop(self):
        """托盘菜单"开始/停止录音"。"""
        self.on_hotkey_toggle()

    def on_tray_retry_model(self):
        """托盘菜单"重试加载模型"（ERROR 状态恢复）。

        状态转移: ERROR → LOADING → IDLE
        """
        if self._shutdown_event.is_set():
            return
        if self.state == EngineState.ERROR:
            if self.transition(EngineState.LOADING):
                self._load_model_async()

    # ============================================================
    # 状态转换
    # ============================================================

    def transition(self, target: EngineState) -> bool:
        """公开状态转换（自动加锁）。

        Args:
            target: 目标状态

        Returns:
            True 表示转换成功，False 表示非法转换被忽略
        """
        with self._state_lock:
            return self._transition_locked(target)

    def _transition_locked(self, target: EngineState) -> bool:
        """内部状态转换（调用方已持锁）。

        检查转移矩阵，执行转换并启动对应超时看门狗。
        """
        # shutdown 优先检查
        if self._shutdown_event.is_set():
            return False

        current = self._state
        valid_targets = _VALID_TRANSITIONS.get(current, set())

        if target not in valid_targets:
            logger.debug(
                "忽略非法状态转换: %s → %s", current.name, target.name
            )
            return False

        logger.info("状态转换: %s → %s", current.name, target.name)
        self._state = target

        # 取消当前看门狗
        self._cancel_watchdog()

        # 启动新状态的超时看门狗
        timeout = _STATE_TIMEOUTS.get(target, 0)
        if timeout > 0:
            self._start_timeout_watchdog(target, timeout)

        return True

    # ============================================================
    # 内部方法（Phase 2 实现具体逻辑）
    # ============================================================

    def _start_recording(self):
        """启动录音流程。

        状态转移: IDLE → RECORDING
        """
        if self.transition(EngineState.RECORDING):
            logger.info("开始录音")
            try:
                self._recorder.start()
                self._silence_detector.begin_detection()
                self._sound_player.play("start")
                if self._tray:
                    self._tray.set_state(EngineState.RECORDING)
            except Exception as e:
                logger.error("启动录音失败: %s", e)
                self.transition(EngineState.IDLE)
                if self._tray:
                    self._tray.show_notification("错误", f"麦克风不可用: {e}")
                    self._tray.set_state(EngineState.IDLE)

    def _stop_recording_and_transcribe(self):
        """停止录音并提交 STT。

        状态转移: RECORDING → PROCESSING
        """
        if self.transition(EngineState.PROCESSING):
            logger.info("停止录音，开始识别")
            try:
                audio = self._recorder.stop()
                self._silence_detector.end_detection()
                self._sound_player.play("end")
                if self._tray:
                    self._tray.set_state(EngineState.PROCESSING)
                self._stt_engine.transcribe_async(audio, self._on_stt_complete)
            except Exception as e:
                logger.error("停止录音失败: %s", e)
                self.transition(EngineState.IDLE)
                if self._tray:
                    self._tray.set_state(EngineState.IDLE)

    def _on_silence_timeout(self):
        """静音超时回调（在 detector 线程中执行）。

        状态转移: RECORDING → PROCESSING
        """
        logger.info("静音超时，停止录音")
        self._stop_recording_and_transcribe()

    def _on_stt_complete(self, text: str, error: Optional[Exception]):
        """STT 完成回调（在线程池工作线程中执行）。

        Args:
            text: 识别文本（空字符串表示无结果）
            error: 异常实例（None 表示成功）
        """
        if self._shutdown_event.is_set():
            return

        if error:
            logger.error("STT 识别失败: %s", error)
            self.transition(EngineState.IDLE)
            if self._tray:
                self._tray.show_notification("识别失败", str(error))
                self._tray.set_state(EngineState.IDLE)
            return

        if not text:
            logger.info("STT 返回空文本，未检测到有效语音")
            self.transition(EngineState.IDLE)
            if self._tray:
                self._tray.show_notification("提示", "未检测到有效语音")
                self._tray.set_state(EngineState.IDLE)
            return

        # 有识别结果，进入注入阶段
        if self.transition(EngineState.INJECTING):
            self._inject_text(text)

    def _inject_text(self, text: str):
        """注入文字到当前光标位置。

        状态转移: INJECTING → IDLE
        """
        logger.info("注入文本: %s", text[:50] + ("..." if len(text) > 50 else ""))
        try:
            success = self._injector.inject(text)
            if not success:
                if self._tray:
                    self._tray.show_notification("提示", "文字已复制到剪贴板，请手动粘贴 Ctrl+V")
        except Exception as e:
            logger.error("注入失败: %s", e)
            if self._tray:
                self._tray.show_notification("注入失败", str(e))

        self.transition(EngineState.IDLE)
        self._sound_player.play("complete")
        if self._tray:
            self._tray.set_state(EngineState.IDLE)

    def _load_model_async(self):
        """异步加载模型（后台线程）。

        状态转移: LOADING → IDLE（成功）或 LOADING → ERROR（失败）
        """
        def _load():
            if self._shutdown_event.is_set():
                return
            if self._tray:
                self._tray.set_state(EngineState.LOADING)

            success = self._stt_engine.load_model()

            with self._state_lock:
                if self._state != EngineState.LOADING:
                    return  # 已经不在 LOADING 状态（可能被 shutdown）
                if success:
                    logger.info("模型加载完成")
                    self._transition_locked(EngineState.IDLE)
                    if self._tray:
                        self._tray.set_state(EngineState.IDLE)
                        self._tray.show_notification("就绪", "语音输入工具已启动")
                else:
                    logger.error("模型加载失败")
                    self._transition_locked(EngineState.ERROR)
                    if self._tray:
                        self._tray.set_state(EngineState.ERROR)
                        self._tray.show_notification("错误", "模型加载失败，请检查模型文件")

        t = threading.Thread(target=_load, name="model-loader", daemon=True)
        t.start()

    # ============================================================
    # 超时看门狗
    # ============================================================

    def _start_timeout_watchdog(self, state: EngineState, timeout_s: float):
        """启动状态超时看门狗。

        在 timeout_s 秒后检查：如果仍在 state 状态，强制回 IDLE。

        注意: 调用方必须已持有 _state_lock。
        """
        def _check():
            with self._state_lock:
                if self._state == state and not self._shutdown_event.is_set():
                    logger.warning("状态 %s 超时 (%.1fs)，强制回 IDLE", state.name, timeout_s)
                    self._transition_locked(EngineState.IDLE)
                    if self._tray:
                        self._tray.show_notification("超时", "操作超时，已重置")
                        self._tray.set_state(EngineState.IDLE)

        timer = threading.Timer(timeout_s, _check)
        timer.daemon = True
        timer.start()
        self._watchdog_timer = timer

    def _cancel_watchdog(self):
        """取消当前超时看门狗。

        注意: 调用方必须已持有 _state_lock。
        """
        if self._watchdog_timer is not None:
            self._watchdog_timer.cancel()
            self._watchdog_timer = None

    # ============================================================
    # 关闭
    # ============================================================

    def shutdown(self):
        """优雅关闭引擎。

        设置关闭信号，取消超时看门狗，等待各子模块清理。
        """
        logger.info("CoreEngine 开始关闭...")
        self._shutdown_event.set()

        with self._state_lock:
            self._cancel_watchdog()

        # 停止子模块
        if self._recorder and self._recorder.is_recording:
            try:
                self._recorder.stop()
            except Exception as e:
                logger.warning("停止录音器失败: %s", e)
        if self._silence_detector:
            self._silence_detector.shutdown()
        if self._stt_engine:
            self._stt_engine.shutdown()

        logger.info("CoreEngine 关闭完成")

        if self._on_shutdown_complete:
            self._on_shutdown_complete()
