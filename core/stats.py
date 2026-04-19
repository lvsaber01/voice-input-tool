"""使用统计模块

线程安全的轻量统计收集器。
按天 JSON 持久化到 ./stats/ 目录。
通过 EventBus 订阅事件，engine 无直接依赖。
"""

import json
import threading
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class UsageStats:
    """线程安全的使用统计收集器。

    每天一个 JSON 文件，跨天自动切换。
    所有 _data 读写通过 _lock 保护。
    """

    def __init__(self, stats_dir: str = "./stats"):
        self._dir = Path(stats_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._today = date.today()
        self._data = self._new_day_data()
        # 尝试加载今天已有数据
        self._load_today_into_data()

    def _new_day_data(self) -> dict:
        return {
            "date": self._today.isoformat(),
            "transcribe_count": 0,
            "empty_result_count": 0,
            "error_count": 0,
            "total_duration_ms": 0,
            "max_duration_triggered": 0,
            "command_triggered": 0,
            "languages": {},
            "text_lengths": [],
        }

    def _stats_file(self, d: date) -> Path:
        return self._dir / f"stats_{d.isoformat()}.json"

    def _load_today_into_data(self):
        path = self._stats_file(self._today)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                # 合并已有数据
                for key in ("transcribe_count", "empty_result_count",
                            "error_count", "total_duration_ms",
                            "max_duration_triggered", "command_triggered"):
                    if key in raw:
                        self._data[key] = raw[key]
                if "languages" in raw:
                    self._data["languages"] = raw["languages"]
                if "text_lengths" in raw:
                    self._data["text_lengths"] = raw["text_lengths"]
            except Exception as e:
                logger.warning("加载今日统计失败: %s", e)

    # ============================================================
    # 记录方法（作为 EventBus handler）
    # ============================================================

    def record_transcribe(self, text: str, duration_ms: int,
                          language: Optional[str] = None,
                          is_empty: bool = False):
        """记录一次转写完成事件。

        可直接作为 TRANSCRIBE_COMPLETE 的 handler。
        签名: (text, language, duration_ms)
        """
        with self._lock:
            self._check_date_rollover()
            self._data["transcribe_count"] += 1
            self._data["total_duration_ms"] += duration_ms
            if language:
                self._data["languages"][language] = self._data["languages"].get(language, 0) + 1
            if is_empty or not text or not text.strip():
                self._data["empty_result_count"] += 1
            else:
                self._data["text_lengths"].append(len(text))
        # 锁外 flush
        self.flush()

    def record_error(self, error: Optional[Exception] = None):
        """记录一次错误事件。可直接作为 TRANSCRIBE_ERROR 的 handler。"""
        with self._lock:
            self._check_date_rollover()
            self._data["error_count"] += 1
        self.flush()

    def record_max_duration(self):
        """记录最大时长触发。可直接作为 MAX_DURATION_TRIGGERED 的 handler。"""
        with self._lock:
            self._check_date_rollover()
            self._data["max_duration_triggered"] += 1
        self.flush()

    def record_command(self, command_name: Optional[str] = None):
        """记录一次命令执行。可直接作为 COMMAND_EXECUTED 的 handler。"""
        with self._lock:
            self._check_date_rollover()
            self._data["command_triggered"] += 1
        self.flush()

    # ============================================================
    # 查询
    # ============================================================

    def get_summary(self, days: int = 7) -> dict:
        """获取最近 N 天的汇总统计。

        锁内拷贝当天数据，锁外读历史文件。
        """
        with self._lock:
            self._check_date_rollover()
            today_data = dict(self._data)
            # 深拷贝可变字段
            today_data["languages"] = dict(self._data["languages"])
            today_data["text_lengths"] = list(self._data["text_lengths"])

        # 锁外读历史
        historical = []
        for i in range(1, days):
            target = date.today() - timedelta(days=i)
            path = self._stats_file(target)
            if path.exists():
                try:
                    historical.append(json.loads(path.read_text(encoding="utf-8")))
                except Exception:
                    pass

        all_days = historical + [today_data]

        # 汇总
        total_transcribe = sum(d.get("transcribe_count", 0) for d in all_days)
        total_empty = sum(d.get("empty_result_count", 0) for d in all_days)
        total_error = sum(d.get("error_count", 0) for d in all_days)
        total_duration = sum(d.get("total_duration_ms", 0) for d in all_days)
        total_max_dur = sum(d.get("max_duration_triggered", 0) for d in all_days)
        total_cmd = sum(d.get("command_triggered", 0) for d in all_days)

        # 语言分布
        lang_agg = {}
        for d in all_days:
            for lang, cnt in d.get("languages", {}).items():
                lang_agg[lang] = lang_agg.get(lang, 0) + cnt

        # 文本长度分布
        all_lengths = []
        for d in all_days:
            all_lengths.extend(d.get("text_lengths", []))

        return {
            "days": days,
            "total_transcribe": total_transcribe,
            "total_empty": total_empty,
            "total_error": total_error,
            "total_duration_ms": total_duration,
            "max_duration_triggered": total_max_dur,
            "command_triggered": total_cmd,
            "languages": lang_agg,
            "avg_text_length": round(sum(all_lengths) / len(all_lengths), 1) if all_lengths else 0,
            "daily": all_days,
        }

    # ============================================================
    # 内部方法
    # ============================================================

    def _check_date_rollover(self):
        """跨天切换（须在锁内调用）。"""
        today = date.today()
        if today != self._today:
            self._flush()
            self._today = today
            self._data = self._new_day_data()

    def flush(self):
        """将当天数据写入磁盘（线程安全）。"""
        with self._lock:
            self._flush()

    def _flush(self):
        """内部 flush（须在锁内调用）。"""
        self._data["date"] = self._today.isoformat()
        path = self._stats_file(self._today)
        try:
            # 写入临时文件后原子替换
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except Exception as e:
            logger.warning("统计持久化失败: %s", e)
