"""配置管理模块 — 读取/写入/校验 config.yaml，支持版本迁移"""

import os
import threading
import logging
from dataclasses import dataclass, field, fields, asdict
from typing import Optional, Any, Dict

import yaml

logger = logging.getLogger(__name__)

# 当前配置版本
CURRENT_CONFIG_VERSION = 4


# ============================================================
# 配置数据类
# ============================================================

@dataclass
class HotkeyConfig:
    """热键配置"""
    trigger: str = "f8"
    mode: str = "toggle"          # push_to_talk | toggle
    conflict_check: bool = True

    def __post_init__(self):
        valid_modes = ("push_to_talk", "toggle")
        if self.mode not in valid_modes:
            raise ValueError(f"hotkey.mode 无效值 '{self.mode}'，可选: {valid_modes}")


@dataclass
class STTConfig:
    """语音识别引擎配置"""
    model_size: str = "small"
    model_path: str = "./models/"
    language: Optional[str] = None   # None=auto
    device: str = "auto"             # auto | cpu | cuda
    compute_type: str = "int8"
    beam_size: int = 5

    def __post_init__(self):
        valid_sizes = ("tiny", "base", "small", "medium", "large-v3")
        if self.model_size not in valid_sizes:
            raise ValueError(f"stt.model_size 无效值 '{self.model_size}'，可选: {valid_sizes}")
        if self.beam_size < 1:
            raise ValueError(f"stt.beam_size 必须 >= 1，当前: {self.beam_size}")


@dataclass
class AudioConfig:
    """音频采集配置"""
    max_duration: int = 120
    silence_timeout: int = 8          # 0=关闭静音检测
    silence_threshold: float = 0.01
    silence_check_interval: float = 0.5
    device: Optional[str] = None      # None=系统默认，或设备名/索引

    def __post_init__(self):
        if self.max_duration < 1:
            raise ValueError(f"audio.max_duration 必须 >= 1，当前: {self.max_duration}")
        if self.silence_timeout < 0:
            raise ValueError(f"audio.silence_timeout 必须 >= 0，当前: {self.silence_timeout}")
        if not 0 < self.silence_threshold <= 1:
            raise ValueError(f"audio.silence_threshold 须在 (0, 1]，当前: {self.silence_threshold}")
        if self.silence_check_interval <= 0:
            raise ValueError(f"audio.silence_check_interval 必须 > 0，当前: {self.silence_check_interval}")


@dataclass
class InjectConfig:
    """文字注入配置"""
    method: str = "clipboard"
    auto_paste: bool = True
    paste_delay_ms: int = 100
    clipboard_backup: bool = True
    clipboard_restore: bool = True
    add_trailing_space: bool = False

    def __post_init__(self):
        if self.paste_delay_ms < 0:
            raise ValueError(f"inject.paste_delay_ms 必须 >= 0，当前: {self.paste_delay_ms}")


@dataclass
class SoundConfig:
    """提示音配置"""
    enabled: bool = True
    volume: float = 0.5
    start_sound: bool = True
    end_sound: bool = True
    complete_sound: bool = True

    def __post_init__(self):
        if not 0 <= self.volume <= 1:
            raise ValueError(f"sound.volume 须在 [0, 1]，当前: {self.volume}")


@dataclass
class WebConfig:
    """Web 配置界面配置"""
    port: int = 18921
    auto_open: bool = False

    def __post_init__(self):
        if not 1 <= self.port <= 65535:
            raise ValueError(f"web.port 须在 [1, 65535]，当前: {self.port}")


@dataclass
class StartupConfig:
    """启动配置"""
    minimize: bool = True
    auto_start: bool = False


@dataclass
class CommandConfig:
    """语音命令模式配置"""
    enabled: bool = True
    custom_commands: list = field(default_factory=list)
    sound_feedback: bool = True


@dataclass
class RealtimeConfig:
    """实时转写模式配置"""
    # 语音段分割
    segment_pause_threshold: float = 0.8   # 静音多久视为句子边界（秒）
    min_segment_duration: float = 0.3     # 最短语音段（秒），低于此跳过
    max_segment_duration: float = 30.0    # 最长语音段（秒），超长强制切分

    # VAD 参数
    vad_sensitivity: int = 2               # webrtcvad 灵敏度（0-3，越高越严格）
    vad_window_ms: int = 30                # VAD 窗口（毫秒，webrtcvad 要求 10/20/30）

    # 输出格式
    inject_method: str = "clipboard"       # clipboard | clipboard_no_restore | direct_type
    segment_separator: str = "\n"           # 段落分隔符
    auto_timestamp: bool = False           # 是否在每段前加时间戳

    def __post_init__(self):
        if not 0 <= self.vad_sensitivity <= 3:
            raise ValueError(f"realtime.vad_sensitivity 须在 [0, 3]，当前: {self.vad_sensitivity}")
        if self.segment_pause_threshold <= 0:
            raise ValueError("realtime.segment_pause_threshold 必须 > 0")
        if self.min_segment_duration < 0:
            raise ValueError("realtime.min_segment_duration 必须 >= 0")
        if self.max_segment_duration <= self.min_segment_duration:
            raise ValueError("realtime.max_segment_duration 必须 > min_segment_duration")


@dataclass
class AppConfig:
    """应用总配置"""
    config_version: int = 2
    mode: str = "batch"                    # batch | realtime
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
    sound: SoundConfig = field(default_factory=SoundConfig)
    web: WebConfig = field(default_factory=WebConfig)
    startup: StartupConfig = field(default_factory=StartupConfig)
    command: CommandConfig = field(default_factory=CommandConfig)
    realtime: RealtimeConfig = field(default_factory=RealtimeConfig)


# ============================================================
# 子配置类注册表（用于反序列化）
# ============================================================

_SUB_CONFIG_TYPES: Dict[str, type] = {
    "hotkey": HotkeyConfig,
    "stt": STTConfig,
    "audio": AudioConfig,
    "inject": InjectConfig,
    "sound": SoundConfig,
    "web": WebConfig,
    "startup": StartupConfig,
    "command": CommandConfig,
    "realtime": RealtimeConfig,
}


# ============================================================
# 反序列化工具
# ============================================================

def _dict_to_dataclass(cls: type, data: Dict[str, Any]) -> Any:
    """将 dict 反序列化为 dataclass 实例。
    
    - 忽略多余 key（向前兼容）
    - 缺失字段使用 dataclass 默认值
    """
    if not isinstance(data, dict):
        return data

    valid_fields = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return cls(**filtered)


def _flatten_to_appconfig(raw: Dict[str, Any]) -> AppConfig:
    """将原始 YAML dict 转换为 AppConfig。
    
    顶层简单字段直接传入 AppConfig，
    子配置节用 _dict_to_dataclass 反序列化。
    """
    kwargs: Dict[str, Any] = {}

    # 提取顶层简单字段
    app_fields = {f.name for f in fields(AppConfig)}
    for k, v in raw.items():
        if k in _SUB_CONFIG_TYPES and isinstance(v, dict):
            kwargs[k] = _dict_to_dataclass(_SUB_CONFIG_TYPES[k], v)
        elif k in app_fields:
            kwargs[k] = v
        # 忽略多余 key

    return AppConfig(**kwargs)


def _dataclass_to_dict(obj: Any) -> Any:
    """递归将 dataclass 实例转为可序列化的 dict"""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _dataclass_to_dict(v) for k, v in asdict(obj).items()}
    return obj


# ============================================================
# 版本迁移
# ============================================================

def _migrate_v1_to_v2(raw: Dict[str, Any]) -> Dict[str, Any]:
    """v1 → v2: 新增 inject.clipboard_restore 字段，新增 config_version 字段"""
    raw["config_version"] = 2
    inject = raw.get("inject", {})
    if "clipboard_restore" not in inject:
        inject["clipboard_restore"] = True
    raw["inject"] = inject
    return raw


def _migrate_v2_to_v3(raw: Dict[str, Any]) -> Dict[str, Any]:
    """v2 → v3: 新增 audio.device 字段 + command 配置节"""
    raw["config_version"] = 3
    audio = raw.get("audio", {})
    if "device" not in audio:
        audio["device"] = None
    raw["audio"] = audio
    if "command" not in raw:
        raw["command"] = {"enabled": True, "custom_commands": [], "sound_feedback": True}
    return raw


def _migrate_v3_to_v4(raw: Dict[str, Any]) -> Dict[str, Any]:
    """v3 → v4: 强制优化默认值
    
    - stt.language: null → zh (auto对短音频不可靠)
    - inject.method: clipboard → keyboard (绕过剪贴板锁定问题)
    """
    raw["config_version"] = 4
    stt = raw.get("stt", {})
    if stt.get("language") is None:
        stt["language"] = "zh"
    raw["stt"] = stt
    inject = raw.get("inject", {})
    inject["method"] = "keyboard"
    raw["inject"] = inject
    return raw


# 迁移注册表: version → migration_function
CONFIG_MIGRATIONS = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
}


# ============================================================
# 保存 / 加载
# ============================================================

_config_lock = threading.Lock()


def load_config(path: str) -> AppConfig:
    """加载配置文件，执行版本迁移。
    
    如果文件不存在，创建默认配置并保存。
    """
    if not os.path.exists(path):
        logger.info("配置文件不存在，创建默认配置: %s", path)
        config = AppConfig()
        save_config(path, config)
        return config

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raw = {}

    # 版本迁移
    original_version = raw.get("config_version", 1)
    version = original_version
    while version < CURRENT_CONFIG_VERSION:
        migrator = CONFIG_MIGRATIONS.get(version)
        if migrator:
            raw = migrator(raw)
            version = raw.get("config_version", version + 1)
        else:
            # 无迁移函数，直接递增
            version += 1
            raw["config_version"] = version

    config = _flatten_to_appconfig(raw)
    logger.info("配置加载完成 (version=%d)", config.config_version)
    # 如果发生了迁移，自动保存
    if original_version < CURRENT_CONFIG_VERSION:
        try:
            save_config(path, config)
            logger.info("配置迁移已自动保存 (v%d -> v%d)", original_version, CURRENT_CONFIG_VERSION)
        except Exception as e:
            logger.warning("迁移后保存失败: %s", e)
    return config


def save_config(path: str, config: AppConfig) -> None:
    """线程安全的配置保存（原子写入）"""
    with _config_lock:
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    _dataclass_to_dict(config),
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            os.replace(tmp_path, path)
            logger.info("配置已保存: %s", path)
        except Exception:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
