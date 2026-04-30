"""配置管理模块 — 读取/写入/校验 config.yaml，支持版本迁移"""

import os
import threading
import logging
from dataclasses import dataclass, field, fields, asdict
from typing import Optional, Any, Dict

import yaml

logger = logging.getLogger(__name__)

# 当前配置版本
CURRENT_CONFIG_VERSION = 8


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
            raise ValueError(f"hotkey.mode 无效值 '{self.mode}', 可选: {valid_modes}")


@dataclass
class StreamingConfig:
    """流式转写配置（FunASR streaming 模式）"""
    enabled: bool = True              # 是否启用流式
    max_queue_size: int = 300         # 音频队列上限（chunk 数）
    overflow_strategy: str = "drop_old"  # 队列溢出策略：drop_old | block
    
    # 注意：chunk_size 和 lookahead 是模型硬性参数，不可配置
    # FunASR streaming 固定使用 [0, 10, 5] = 600ms + 300ms lookahead
    
    def __post_init__(self):
        if self.max_queue_size < 10:
            raise ValueError(f"streaming.max_queue_size 必须 >= 10，当前: {self.max_queue_size}")
        valid_strategies = ("drop_old", "block")
        if self.overflow_strategy not in valid_strategies:
            raise ValueError(f"streaming.overflow_strategy 无效值 '{self.overflow_strategy}'，可选: {valid_strategies}")


@dataclass
class STTConfig:
    """语音识别引擎配置"""
    engine: str = "auto"              # auto | faster_whisper | funasr | mlx_whisper | qwen3_asr
    model_size: str = "large-v3-turbo"
    model_path: str = "./models/"
    language: Optional[str] = None   # None=auto
    device: str = "auto"             # auto | cpu | cuda
    compute_type: str = "int8"
    beam_size: int = 5
    # 镜像配置（中国用户）
    hf_endpoint: str = "https://hf-mirror.com"  # HuggingFace 镜像
    modelscope_endpoint: str = ""  # ModelScope 镜像（可选）
    # Qwen3-ASR 配置
    max_new_tokens: int = 256         # Qwen3-ASR 最大生成长度（仅 qwen3_asr 引擎使用）
    # 流式配置（仅 FunASR 支持）
    streaming: StreamingConfig = field(default_factory=StreamingConfig)

    def __post_init__(self):
        valid_engines = ("auto", "faster_whisper", "funasr", "mlx_whisper", "qwen3_asr")
        if self.engine not in valid_engines:
            raise ValueError(f"stt.engine 无效值 '{self.engine}'，可选: {valid_engines}")
        if self.engine in ("faster_whisper", "auto", "mlx_whisper"):
            valid_sizes = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
            if self.model_size not in valid_sizes:
                if self.model_size in ("paraformer-zh", "paraformer-zh-streaming"):
                    logger.info("model_size '%s' is FunASR-only, auto-switching to large-v3-turbo", self.model_size)
                    self.model_size = "large-v3-turbo"
                elif self.model_size in ("Qwen3-ASR-0.6B", "Qwen3-ASR-1.7B"):
                    logger.info("model_size '%s' is Qwen3-ASR-only, auto-switching to large-v3-turbo", self.model_size)
                    self.model_size = "large-v3-turbo"
                else:
                    raise ValueError(f"stt.model_size 无效值 '{self.model_size}'，可选: {valid_sizes}")
        elif self.engine == "funasr":
            # FunASR 支持的模型列表（验证过的）
            valid_funasr_sizes = ("paraformer-zh", "paraformer-zh-streaming", "paraformer-en", "SenseVoiceSmall", "Fun-ASR-Nano")
            if self.model_size not in valid_funasr_sizes:
                logger.info("model_size '%s' 不支持，auto-switching to paraformer-zh", self.model_size)
                self.model_size = "paraformer-zh"
        elif self.engine == "qwen3_asr":
            valid_qwen3_sizes = ("Qwen3-ASR-0.6B", "Qwen3-ASR-1.7B")
            if self.model_size not in valid_qwen3_sizes:
                logger.info("model_size '%s' 不支持，auto-switching to Qwen3-ASR-0.6B", self.model_size)
                self.model_size = "Qwen3-ASR-0.6B"
        if self.beam_size < 1:
            raise ValueError(f"stt.beam_size 必须 >= 1，当前: {self.beam_size}")
        
        # 处理 streaming 字段（可能是 dict 或 StreamingConfig）
        if isinstance(self.streaming, dict):
            self.streaming = StreamingConfig(**self.streaming)
        
        # 流式配置验证
        if self.streaming.enabled and self.engine != 'funasr':
            logger.warning("streaming.enabled=true 仅支持 FunASR，自动禁用")
            self.streaming.enabled = False

        # SenseVoice 不支持流式模式，自动禁用
        if self.model_size == "SenseVoiceSmall" and self.streaming.enabled:
            logger.warning("SenseVoiceSmall 不支持流式模式，已自动禁用 streaming")
            self.streaming.enabled = False


@dataclass
class AudioConfig:
    """音频采集配置"""
    max_duration: int = 120
    silence_timeout: int = 8          # 0=关闭静音检测
    silence_threshold: float = 0.01
    silence_check_interval: float = 0.5
    device: Optional[str] = None      # None=系统默认，或设备名/索引
    sample_rate: Optional[int] = None  # None=auto | 16000 | 48000 等

    def __post_init__(self):
        if self.max_duration < 1:
            raise ValueError(f"audio.max_duration 必须 >= 1，当前: {self.max_duration}")
        if self.silence_timeout < 0:
            raise ValueError(f"audio.silence_timeout 必须 >= 0，当前: {self.silence_timeout}")
        if not 0 < self.silence_threshold <= 1:
            raise ValueError(f"audio.silence_threshold 须在 (0, 1]，当前: {self.silence_threshold}")
        if self.silence_check_interval <= 0:
            raise ValueError(f"audio.silence_check_interval 必须 > 0，当前: {self.silence_check_interval}")
        # 处理 sample_rate
        if self.sample_rate is not None:
            if isinstance(self.sample_rate, str):
                if self.sample_rate.lower() == 'auto':
                    self.sample_rate = None
                else:
                    try:
                        self.sample_rate = int(self.sample_rate)
                    except ValueError:
                        logger.warning("audio.sample_rate 无效值 '%s'，回退为 auto", self.sample_rate)
                        self.sample_rate = None


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
class HotwordConfig:
    """热词系统配置"""
    enabled: bool = True                # 热词系统总开关
    hotwords_file: str = "hotwords.txt"  # 热词数据文件路径
    rules_file: str = "hot-rules.txt"    # 正则规则文件路径
    case_sensitive: bool = False         # 热词匹配是否区分大小写
    min_word_length: int = 2             # 热词最短字符数（防误替换）


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
    hotword: HotwordConfig = field(default_factory=HotwordConfig)


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
    "hotword": HotwordConfig,
    "streaming": StreamingConfig,  # 新增
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


def _migrate_v4_to_v5(raw: Dict[str, Any]) -> Dict[str, Any]:
    """v4 → v5: 添加镜像配置
    
    - stt.hf_endpoint: 默认 hf-mirror.com（解决中国用户 HuggingFace 超时）
    - stt.modelscope_endpoint: 默认空（可选）
    """
    raw["config_version"] = 5
    stt = raw.get("stt", {})
    if "hf_endpoint" not in stt:
        stt["hf_endpoint"] = "https://hf-mirror.com"
    if "modelscope_endpoint" not in stt:
        stt["modelscope_endpoint"] = ""
    raw["stt"] = stt
    return raw


def _migrate_v5_to_v6(raw: Dict[str, Any]) -> Dict[str, Any]:
    """v5 → v6: 新增 Qwen3-ASR 引擎支持

    - 新增 max_new_tokens 字段（默认 256）
    - 新增 qwen3_asr 引擎类型
    - 无需强制迁移引擎（保持用户现有配置）
    """
    raw["config_version"] = 6
    stt = raw.get("stt", {})
    if "max_new_tokens" not in stt:
        stt["max_new_tokens"] = 256
    raw["stt"] = stt
    return raw


def _migrate_v7_to_v8(raw: Dict[str, Any]) -> Dict[str, Any]:
    """v7 → v8: 新增 hotword 配置段"""
    raw["config_version"] = 8
    if "hotword" not in raw:
        raw["hotword"] = {
            "enabled": True,
            "hotwords_file": "hotwords.txt",
            "rules_file": "hot-rules.txt",
            "case_sensitive": False,
            "min_word_length": 2,
        }
    return raw


def _migrate_v6_to_v7(raw: Dict[str, Any]) -> Dict[str, Any]:
    """v6 → v7: 新增 audio.sample_rate 字段

    - audio.sample_rate: None=auto（自动检测设备采样率）
    """
    raw["config_version"] = 7
    audio = raw.get("audio", {})
    if "sample_rate" not in audio:
        audio["sample_rate"] = None  # auto
    raw["audio"] = audio
    return raw


# 迁移注册表: version → migration_function
CONFIG_MIGRATIONS = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
    4: _migrate_v4_to_v5,
    5: _migrate_v5_to_v6,
    6: _migrate_v6_to_v7,
    7: _migrate_v7_to_v8,
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
