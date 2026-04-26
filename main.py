"""语音输入工具 — 入口模块

按照设计文档 4.1 节实现：
- 全局异常处理
- 单实例检测（Windows）/ 跨平台跳过
- 日志初始化
- 配置加载
- CoreEngine 初始化
- 程序退出流程
"""

import sys
import os
import time
import logging
import logging.handlers
import platform
import threading
from pathlib import Path

# 项目根目录（支持打包后路径）
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后：exe 所在目录
    PROJECT_ROOT = Path(sys.executable).parent
    # 使用 runtime_hook 设置的用户数据目录
    user_data = os.environ.get('VOICE_INPUT_TOOL_USER_DATA', '')
    USER_DATA_DIR = Path(user_data) if user_data else PROJECT_ROOT
else:
    # 开发环境
    PROJECT_ROOT = Path(__file__).parent.resolve()
    USER_DATA_DIR = PROJECT_ROOT

# 日志目录（打包后使用用户数据目录）
LOG_DIR = USER_DATA_DIR / "logs"

logger = logging.getLogger(__name__)


# ============================================================
# 全局异常处理
# ============================================================

def global_exception_handler(exc_type, exc_value, exc_traceback):
    """未捕获异常的兜底处理
    
    记录到崩溃日志文件，避免程序静默崩溃。
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.critical(
        "未捕获的异常", exc_info=(exc_type, exc_value, exc_traceback)
    )


# ============================================================
# 日志初始化
# ============================================================

def setup_logging():
    """初始化日志系统：按天轮转 + 崩溃日志单独文件"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 格式
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s - %(name)s: %(message)s"
    )

    # 按天轮转的主日志
    main_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "app.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    main_handler.setFormatter(formatter)

    # 崩溃日志（仅 CRITICAL）
    crash_handler = logging.FileHandler(
        LOG_DIR / "crash.log", encoding="utf-8"
    )
    crash_handler.setLevel(logging.CRITICAL)
    crash_handler.setFormatter(formatter)

    # 控制台输出（开发阶段）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if os.getenv("VOICE_DEBUG") else logging.INFO)
    root.addHandler(main_handler)
    root.addHandler(crash_handler)
    root.addHandler(console_handler)

    logger.info("日志系统初始化完成，日志目录: %s", LOG_DIR)


# ============================================================
# 单实例检测
# ============================================================

class _SingleInstanceLock:
    """单实例锁（Windows Named Mutex / 跨平台 fallback）"""

    def __init__(self):
        self._mutex = None
        self._locked = False

    def acquire(self) -> bool:
        """尝试获取锁。返回 True 表示成功（无其他实例），False 表示已有实例运行。"""
        if platform.system() == "Windows":
            return self._acquire_windows()
        else:
            # macOS/Linux: 跳过单实例检测
            logger.debug("非 Windows 平台，跳过单实例检测")
            return True

    def _acquire_windows(self) -> bool:
        """Windows 平台使用 Named Mutex"""
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            mutex_name = "Global\\VoiceInputTool_SingleInstance"

            # CreateMutexW
            ERROR_ALREADY_EXISTS = 0xB7
            handle = kernel32.CreateMutexW(None, True, mutex_name)
            if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False

            self._mutex = handle
            self._locked = True
            return True
        except Exception as e:
            logger.warning("单实例检测失败（降级跳过）: %s", e)
            return True

    def release(self):
        """释放锁"""
        if self._locked and self._mutex:
            try:
                import ctypes
                ctypes.windll.kernel32.ReleaseMutex(self._mutex)
                ctypes.windll.kernel32.CloseHandle(self._mutex)
            except Exception:
                pass
            self._mutex = None
            self._locked = False


# ============================================================
# 主函数
# ============================================================

def main():
    """程序入口"""
    # 0. 启用 faulthandler 捕获 segfault 等致命错误
    import faulthandler
    import atexit
    crash_log_path = LOG_DIR / 'crash.log'
    crash_log_path.parent.mkdir(parents=True, exist_ok=True)
    crash_file = open(crash_log_path, 'a', encoding='utf-8')
    faulthandler.enable(file=crash_file, all_threads=True)
    # atexit 写入正常退出标记，帮助区分崩溃 vs 正常退出
    atexit.register(lambda: (
        crash_file.write(f'\n=== NORMAL EXIT {time.strftime("%Y-%m-%d %H:%M:%S")} ===\n'),
        crash_file.flush(),
        crash_file.close()
    ))

    # 1. 全局异常兜底
    sys.excepthook = global_exception_handler
    threading.excepthook = lambda args: logger.critical(
        "线程未捕获异常: %s in %s", args.exc_value, args.thread
    )

    # 2. 日志初始化
    setup_logging()
    logger.info("=" * 50)
    logger.info("语音输入工具启动")
    logger.info("平台: %s %s", platform.system(), platform.release())
    logger.info("Python: %s", sys.version)

    # 3. 单实例检测
    instance_lock = _SingleInstanceLock()
    if not instance_lock.acquire():
        logger.warning("已有实例运行，退出")
        print("语音输入工具已在运行中。", file=sys.stderr)
        sys.exit(1)

    try:
        # 4. 加载配置（支持打包后路径）
        if getattr(sys, 'frozen', False):
            # 打包后：exe 目录的默认配置（模板）
            # PyInstaller datas 文件在 _internal (sys._MEIPASS) 下
            internal_dir = Path(getattr(sys, '_MEIPASS', ''))
            exe_config = internal_dir / "config.example.yaml" if internal_dir else PROJECT_ROOT / "config.example.yaml"
            user_config = USER_DATA_DIR / "config.yaml"
            
            # 首次启动：复制默认配置到用户目录
            if not user_config.exists() and exe_config.exists():
                import shutil
                shutil.copy2(exe_config, user_config)
                logger.info("首次启动，复制默认配置到用户目录")
            
            # 优先使用用户配置
            config_path = str(user_config if user_config.exists() else exe_config)
        else:
            config_path = str(PROJECT_ROOT / "config.yaml")
        
        logger.info("加载配置: %s", config_path)

        from config import load_config, AppConfig
        config = load_config(config_path)

        # 5. 确保 model_path 目录存在（支持打包后路径）
        model_path = Path(config.stt.model_path)
        if not model_path.is_absolute():
            if getattr(sys, 'frozen', False):
                # 打包后：使用用户数据目录
                models_dir = USER_DATA_DIR / "models"
                model_path = models_dir / model_path
            else:
                model_path = PROJECT_ROOT / model_path
        model_path.mkdir(parents=True, exist_ok=True)

        # 6. 定义退出回调
        def on_shutdown_complete():
            logger.info("关闭完成，准备退出")
            if tray:
                tray.stop()
            if web_server:
                web_server.stop()

        # 7. 初始化系统托盘（需要在 engine 之前创建，因为 engine 引用它）
        from gui.tray import TrayIcon

        def _on_tray_start_stop():
            """托盘菜单开始/停止录音"""
            engine.on_tray_start_stop()

        def _on_tray_retry_model():
            """托盘菜单重试加载模型"""
            engine.on_tray_retry_model()

        def _on_tray_settings():
            """托盘菜单打开设置"""
            import webbrowser
            if web_server:
                url = web_server.get_config_url()
                webbrowser.open(url)
                logger.info("已打开设置页面: %s", url)

        def _on_tray_quit():
            """托盘菜单退出"""
            logger.info("用户通过托盘菜单退出")
            engine.shutdown()
            if tray:
                tray.stop()

        def _on_tray_switch_mode():
            """托盘菜单切换模式"""
            current = config.mode
            new_mode = "realtime" if current == "batch" else "batch"
            config.mode = new_mode
            if tray:
                tray.set_mode(new_mode)
            logger.info("模式切换: %s → %s", current, new_mode)

        tray = TrayIcon(
            on_start=_on_tray_start_stop,
            on_stop=_on_tray_start_stop,
            on_settings=_on_tray_settings,
            on_quit=_on_tray_quit,
            on_retry=_on_tray_retry_model,
            on_switch_mode=_on_tray_switch_mode,
        )

        # 8. 初始化 CoreEngine
        from core.engine import CoreEngine
        engine = CoreEngine(
            config=config,
            tray=tray,
            on_shutdown_complete=on_shutdown_complete,
        )

        logger.info("CoreEngine 初始化完成，状态: LOADING")

        # 创建使用统计（支持打包后路径）
        from core.stats import UsageStats
        stats_dir = USER_DATA_DIR / "stats" if getattr(sys, 'frozen', False) else PROJECT_ROOT / "stats"
        stats = UsageStats(stats_dir=str(stats_dir))

        # 创建 VAD 指示器
        from gui.vad_indicator import create_vad_indicator
        vad_indicator = create_vad_indicator()

        # EventBus 订阅
        from core.events import EngineEvent
        events = engine.events

        # Stats 订阅
        events.subscribe(EngineEvent.TRANSCRIBE_COMPLETE, stats.record_transcribe)
        events.subscribe(EngineEvent.TRANSCRIBE_ERROR, lambda e: stats.record_error())
        events.subscribe(EngineEvent.MAX_DURATION_TRIGGERED, stats.record_max_duration)
        events.subscribe(EngineEvent.COMMAND_EXECUTED, lambda name: stats.record_command())

        # VAD 指示器订阅
        events.subscribe(EngineEvent.RMS_UPDATE, vad_indicator.update)
        events.subscribe(EngineEvent.RECORDING_STARTED, lambda: vad_indicator.show())
        events.subscribe(EngineEvent.RECORDING_STOPPED, lambda: vad_indicator.hide())

        # 启动时检测残留剪贴板备份文件并清理
        import tempfile
        import glob
        backup_pattern = os.path.join(tempfile.gettempdir(), "voice_input_tool_cb_*.bak")
        for bak_file in glob.glob(backup_pattern):
            try:
                os.remove(bak_file)
                logger.info("清理残留剪贴板备份: %s", bak_file)
            except Exception as e:
                logger.warning("清理备份文件失败 %s: %s", bak_file, e)

        # 9. 启动 Web 配置服务
        from gui.web_server import ConfigWebServer
        web_server = ConfigWebServer(
            config=config,
            engine=engine,
            stats=stats,
            on_config_changed=None,  # 可扩展：配置变更通知
        )
        web_server.start()

        # 10. 异步加载模型
        engine._load_model_async()

        # 11. 初始化热键管理器
        try:
            from core.hotkey import HotkeyManager
            hotkey_manager = HotkeyManager(
                config=config.hotkey,
                on_start=engine.on_hotkey_start,
                on_stop=engine.on_hotkey_stop,
                on_toggle=engine.on_hotkey_toggle,
            )
            result = hotkey_manager.register()
            if hasattr(result, '__len__') and len(result) == 2:
                ok, err_msg = result
                if not ok:
                    logger.warning("热键注册失败: %s", err_msg)
                    if tray:
                        tray.show_notification("热键注册失败", str(err_msg))
                    hotkey_manager = None
                else:
                    logger.info("热键已注册: %s (%s)", config.hotkey.trigger, config.hotkey.mode)
            else:
                logger.info("热键已注册: %s (%s)", config.hotkey.trigger, config.hotkey.mode)
        except ImportError:
            logger.warning("keyboard 库不可用（非 Windows 或非管理员），跳过热键注册")
            hotkey_manager = None
        except Exception as e:
            logger.warning("热键注册失败: %s", e)
            hotkey_manager = None

        logger.info("语音输入工具启动完成，进入托盘主循环")

        # 12. 进入 pystray 主循环（阻塞，必须在主线程）
        tray.run()

        # 主循环退出后清理
        engine.shutdown()
        if hotkey_manager:
            try:
                hotkey_manager.unregister()
            except Exception:
                pass

    except Exception as e:
        logger.critical("启动失败: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        instance_lock.release()
        logger.info("语音输入工具已退出")
        logging.shutdown()


if __name__ == "__main__":
    main()
