"""配置 Web 服务模块 — 完整实现

职责：提供 HTTP 配置页面（带 token 校验）。
使用 http.server.HTTPServer + BaseHTTPRequestHandler。
绑定 127.0.0.1，daemon 线程运行。
"""

import json
import logging
import os
import secrets
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# 请求体大小限制（1MB）
_MAX_BODY_SIZE = 1024 * 1024

# 模板目录
_TEMPLATES_DIR = Path(__file__).parent / "templates"


class _ConfigHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    # 由 ConfigWebServer 设置
    _server_ctx = None  # type: ConfigWebServer

    def log_message(self, format, *args):
        """覆盖默认日志输出，使用 logging 模块"""
        logger.debug("HTTP %s", format % args)

    def do_GET(self):
        """处理 GET 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_config_html()
        elif path == "/api/config":
            self._handle_get_config()
        elif path == "/api/status":
            self._handle_get_status()
        elif path == "/api/stats":
            self._handle_get_stats()
        elif path == "/api/audio/devices":
            self._handle_get_audio_devices()
        elif path == "/api/hotwords":
            self._handle_get_hotwords()
        elif path == "/api/hotword-rules":
            self._handle_get_hotword_rules()
        else:
            self._send_error_json(404, "未找到")

    def do_PUT(self):
        """处理 PUT 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            self._handle_put_config()
        elif path == "/api/hotwords":
            self._handle_put_hotwords()
        elif path == "/api/hotword-rules":
            self._handle_put_hotword_rules()
        else:
            self._send_error_json(404, "未找到")

    def do_POST(self):
        """处理 POST 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/hotkey/test":
            self._handle_hotkey_test()
        elif path == "/api/sound/test":
            self._handle_sound_test()
        elif path == "/api/record/start":
            self._handle_record_start()
        elif path == "/api/record/stop":
            self._handle_record_stop()
        elif path == "/api/record/toggle":
            self._handle_record_toggle()
        elif path == "/api/test/transcribe":
            self._handle_test_transcribe()
        elif path == "/api/hotwords":
            self._handle_post_hotword()
        elif path == "/api/hotword-rules":
            self._handle_post_hotword_rule()
        elif path == "/api/record/pause":
            self._handle_record_pause()
        elif path == "/api/session/export":
            self._handle_session_export()
        else:
            self._send_error_json(404, "未找到")

    def do_DELETE(self):
        """处理 DELETE 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/hotwords":
            self._handle_delete_hotwords()
        elif path == "/api/hotword-rules":
            self._handle_delete_hotword_rules()
        else:
            self._send_error_json(404, "未找到")

    # ============================================================
    # 路由实现
    # ============================================================

    def _serve_config_html(self):
        """返回 config.html 页面"""
        html_path = _TEMPLATES_DIR / "config.html"
        if not html_path.exists():
            self._send_error_json(404, "配置页面文件不存在")
            return

        try:
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self._send_error_json(500, f"读取配置页面失败: {e}")

    def _handle_get_config(self):
        """GET /api/config — 返回当前配置 JSON"""
        try:
            config_dict = self._server_ctx._get_config_dict()
            self._send_json(200, config_dict)
        except Exception as e:
            self._send_error_json(500, f"读取配置失败: {e}")

    def _handle_put_config(self):
        """PUT /api/config — 更新配置（需 token）"""
        # token 校验
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return

        try:
            body = self._read_body()
            if body is None:
                return

            updates = json.loads(body)
            success = self._server_ctx._update_config(updates)
            if success:
                self._send_json(200, {"ok": True, "message": "配置已保存"})
            else:
                self._send_error_json(400, "配置更新失败")
        except json.JSONDecodeError:
            self._send_error_json(400, "请求体不是有效的 JSON")
        except Exception as e:
            self._send_error_json(500, f"更新配置失败: {e}")

    def _handle_get_status(self):
        """GET /api/status — 返回引擎状态"""
        try:
            status = self._server_ctx._get_status()
            self._send_json(200, status)
        except Exception as e:
            self._send_error_json(500, f"获取状态失败: {e}")

    def _handle_hotkey_test(self):
        """POST /api/hotkey/test — 测试快捷键"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return

        try:
            body = self._read_body()
            if body is None:
                return
            data = json.loads(body) if body else {}
            key = data.get("key", "")

            # 简单测试：尝试解析键名
            result = self._server_ctx._test_hotkey(key)
            self._send_json(200, result)
        except Exception as e:
            self._send_error_json(500, f"测试快捷键失败: {e}")

    def _handle_sound_test(self):
        """POST /api/sound/test — 播放测试提示音"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return

        try:
            self._server_ctx._test_sound()
            self._send_json(200, {"ok": True, "message": "提示音已播放"})
        except Exception as e:
            self._send_error_json(500, f"播放提示音失败: {e}")

    def _handle_record_start(self):
        """POST /api/record/start — 开始录音"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            result = self._server_ctx._record_start()
            self._send_json(200, result)
        except Exception as e:
            self._send_error_json(500, f"开始录音失败: {e}")

    def _handle_record_stop(self):
        """POST /api/record/stop — 停止录音并转写"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            result = self._server_ctx._record_stop()
            self._send_json(200, result)
        except Exception as e:
            self._send_error_json(500, f"停止录音失败: {e}")

    def _handle_record_toggle(self):
        """POST /api/record/toggle — 切换录音状态"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            result = self._server_ctx._record_toggle()
            self._send_json(200, result)
        except Exception as e:
            self._send_error_json(500, f"切换录音失败: {e}")

    def _handle_record_pause(self):
        """POST /api/record/pause — 切换实时转写暂停/恢复"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            engine = self._server_ctx._engine
            if engine is None:
                self._send_error_json(503, "引擎未初始化")
                return
            engine.toggle_pause()
            from core.engine import EngineState
            state_name = engine._state.name
            self._send_json(200, {"state": state_name})
        except Exception as e:
            self._send_error_json(500, f"暂停/恢复失败: {e}")

    def _handle_session_export(self):
        """POST /api/session/export — 导出转写会话

        请求体: {"format": "txt"|"markdown", "path": "/path/to/file"}
        """
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            engine = self._server_ctx._engine
            if engine is None:
                self._send_error_json(503, "引擎未初始化")
                return
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            fmt = body.get("format", "txt")
            path = body.get("path")
            if not path:
                self._send_error_json(400, "缺少 path 参数")
                return
            segments = engine.get_session_segments()
            success = engine.export_session(path, format=fmt)
            if success:
                self._send_json(200, {"segments": len(segments), "path": path})
            else:
                self._send_error_json(500, "导出失败（无数据或写入错误）")
        except Exception as e:
            self._send_error_json(500, f"导出失败: {e}")

    def _handle_test_transcribe(self):
        """POST /api/test/transcribe — 用合成音频测试 STT（自动录音→停止→返回结果）

        请求体: {"duration": 3}  录音秒数，默认3秒
        """
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            body = self._read_body()
            data = json.loads(body) if body else {}
            duration = int(data.get("duration", 3))
            result = self._server_ctx._test_transcribe(duration)
            self._send_json(200, result)
        except Exception as e:
            self._send_error_json(500, f"转写测试失败: {e}")

    # ============================================================
    # 工具方法
    # ============================================================

    def _handle_get_stats(self):
        """GET /api/stats?days=7 — 返回使用统计"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        days = int(params.get("days", ["7"])[0])
        try:
            stats_obj = self._server_ctx._stats
            if stats_obj is None:
                self._send_json(200, {"ok": False, "error": "统计模块未启用"})
                return
            summary = stats_obj.get_summary(days=days)
            self._send_json(200, summary)
        except Exception as e:
            self._send_error_json(500, f"获取统计失败: {e}")

    def _handle_get_audio_devices(self):
        """GET /api/audio/devices — 返回输入设备列表"""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            input_devices = []
            for i, dev in enumerate(devices):
                if dev.get('max_input_channels', 0) > 0:
                    input_devices.append({
                        "index": i,
                        "name": dev.get('name', ''),
                        "sample_rate": dev.get('default_samplerate', 0),
                        "channels": dev.get('max_input_channels', 0),
                    })
            self._send_json(200, {"ok": True, "devices": input_devices})
        except ImportError:
            self._send_json(200, {"ok": True, "devices": [], "warning": "sounddevice 未安装"})
        except Exception as e:
            self._send_error_json(500, f"获取设备列表失败: {e}")

    # ─── 热词管理 API ───

    def _handle_get_hotwords(self):
        """GET /api/hotwords — 返回热词列表"""
        try:
            hm = self._server_ctx._get_hotword_manager()
            if hm is None:
                self._send_json(200, {"ok": True, "hotwords": [], "count": 0, "file": "hotwords.txt"})
                return
            entries = hm.get_all_entries()
            self._send_json(200, {
                "ok": True,
                "hotwords": [
                    {"source": e.source, "target": e.target, "category": e.category,
                     "text_replace": e.text_replace, "model_hotword": e.model_hotword}
                    for e in entries
                ],
                "count": len(entries),
                "file": "hotwords.txt",
            })
        except Exception as e:
            self._send_error_json(500, f"获取热词失败: {e}")

    def _handle_put_hotwords(self):
        """PUT /api/hotwords — 整体替换热词文件（需 token）"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            body = self._read_body()
            if body is None:
                return
            data = json.loads(body)
            hm = self._server_ctx._get_hotword_manager()
            if hm is None:
                self._send_error_json(503, "热词系统未启用")
                return
            items = data.get("hotwords", [])
            if not isinstance(items, list):
                self._send_error_json(400, "hotwords 必须是数组")
                return
            # 重建热词列表
            hm._entries = []
            for item in items:
                source = str(item.get("source", "")).strip()
                if not source:
                    continue
                target = str(item.get("target", "")).strip() or source
                category = str(item.get("category", "")).strip()
                from core.hotword import HotwordEntry
                hm._entries.append(HotwordEntry(
                    source=source, target=target, category=category,
                    text_replace=True, model_hotword=(source == target),
                ))
            # 保存并 reload
            if not hm.save_to_file():
                self._send_error_json(500, "保存热词文件失败")
                return
            tp = self._server_ctx._get_text_pipeline()
            if tp:
                tp.schedule_reload()
            self._send_json(200, {"ok": True, "message": "热词已保存并重载", "count": len(hm._entries)})
        except json.JSONDecodeError:
            self._send_error_json(400, "请求体不是有效的 JSON")
        except Exception as e:
            self._send_error_json(500, f"保存热词失败: {e}")

    def _handle_post_hotword(self):
        """POST /api/hotwords — 追加单条热词（需 token）"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            body = self._read_body()
            if body is None:
                return
            data = json.loads(body)
            source = str(data.get("source", "")).strip()
            target = str(data.get("target", "")).strip()
            # 校验
            if not source:
                self._send_error_json(400, "source 不能为空且长度 2-100")
                return
            if len(source) < 2 or len(source) > 100:
                self._send_error_json(400, "source 长度须在 2-100 之间")
                return
            if '\n' in source or '\r' in source:
                self._send_error_json(400, "source 不能包含换行符")
                return
            if len(target) > 200:
                self._send_error_json(400, "target 长度不能超过 200")
                return
            hm = self._server_ctx._get_hotword_manager()
            if hm is None:
                self._send_error_json(503, "热词系统未启用")
                return
            hm.add_hotword(source, target)
            if not hm.save_to_file():
                self._send_error_json(500, "保存热词文件失败")
                return
            tp = self._server_ctx._get_text_pipeline()
            if tp:
                tp.schedule_reload()
            self._send_json(200, {"ok": True, "message": "热词已追加"})
        except json.JSONDecodeError:
            self._send_error_json(400, "请求体不是有效的 JSON")
        except Exception as e:
            self._send_error_json(500, f"追加热词失败: {e}")

    def _handle_delete_hotwords(self):
        """DELETE /api/hotwords — 清空热词（需 token）"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            hm = self._server_ctx._get_hotword_manager()
            if hm is None:
                self._send_error_json(503, "热词系统未启用")
                return
            hm._entries = []
            if not hm.save_to_file():
                self._send_error_json(500, "保存热词文件失败")
                return
            tp = self._server_ctx._get_text_pipeline()
            if tp:
                tp.schedule_reload()
            self._send_json(200, {"ok": True, "message": "热词已清空"})
        except Exception as e:
            self._send_error_json(500, f"清空热词失败: {e}")

    def _handle_get_hotword_rules(self):
        """GET /api/hotword-rules — 返回正则规则列表"""
        try:
            hm = self._server_ctx._get_hotword_manager()
            if hm is None:
                self._send_json(200, {"ok": True, "rules": [], "count": 0})
                return
            rules = hm.get_all_rules()
            self._send_json(200, {
                "ok": True,
                "rules": [{"pattern": p, "replacement": r} for p, r in rules],
                "count": len(rules),
            })
        except Exception as e:
            self._send_error_json(500, f"获取正则规则失败: {e}")

    def _handle_put_hotword_rules(self):
        """PUT /api/hotword-rules — 整体替换规则文件（需 token）"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            body = self._read_body()
            if body is None:
                return
            data = json.loads(body)
            hm = self._server_ctx._get_hotword_manager()
            if hm is None:
                self._send_error_json(503, "热词系统未启用")
                return
            items = data.get("rules", [])
            if not isinstance(items, list):
                self._send_error_json(400, "rules 必须是数组")
                return
            hm._rules = []
            hm._compiled_rules = []
            import re as re_mod
            success = 0
            for item in items:
                pattern = str(item.get("pattern", "")).strip()
                replacement = str(item.get("replacement", "")).strip()
                if not pattern:
                    continue
                if not hm._validate_regex_complexity(pattern):
                    logger.warning("正则规则过于复杂，跳过: %s", pattern[:50])
                    continue
                try:
                    compiled = re_mod.compile(pattern)
                    hm._rules.append((pattern, replacement))
                    hm._compiled_rules.append((compiled, replacement))
                    success += 1
                except re_mod.error as e:
                    logger.warning("正则编译失败，跳过: %s, error=%s", pattern[:50], e)
            if not hm.save_rules_to_file():
                self._send_error_json(500, "保存规则文件失败")
                return
            tp = self._server_ctx._get_text_pipeline()
            if tp:
                tp.schedule_reload()
            self._send_json(200, {"ok": True, "message": f"规则已保存并重载", "count": success})
        except json.JSONDecodeError:
            self._send_error_json(400, "请求体不是有效的 JSON")
        except Exception as e:
            self._send_error_json(500, f"保存规则失败: {e}")

    def _handle_post_hotword_rule(self):
        """POST /api/hotword-rules — 追加单条规则（需 token）"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            body = self._read_body()
            if body is None:
                return
            data = json.loads(body)
            pattern = str(data.get("pattern", "")).strip()
            replacement = str(data.get("replacement", "")).strip()
            if not pattern:
                self._send_error_json(400, "pattern 不能为空")
                return
            if len(pattern) > 500:
                self._send_error_json(400, "pattern 长度不能超过 500")
                return
            if len(replacement) > 200:
                self._send_error_json(400, "replacement 长度不能超过 200")
                return
            import re as re_mod
            if not re_mod.compile(pattern):
                self._send_error_json(400, "pattern 不是有效的正则表达式")
                return
            hm = self._server_ctx._get_hotword_manager()
            if hm is None:
                self._send_error_json(503, "热词系统未启用")
                return
            if not hm.add_rule(pattern, replacement):
                self._send_error_json(400, "规则过于复杂或编译失败")
                return
            if not hm.save_rules_to_file():
                self._send_error_json(500, "保存规则文件失败")
                return
            tp = self._server_ctx._get_text_pipeline()
            if tp:
                tp.schedule_reload()
            self._send_json(200, {"ok": True, "message": "规则已追加"})
        except re_mod.error:
            self._send_error_json(400, "pattern 不是有效的正则表达式")
        except json.JSONDecodeError:
            self._send_error_json(400, "请求体不是有效的 JSON")
        except Exception as e:
            self._send_error_json(500, f"追加规则失败: {e}")

    def _handle_delete_hotword_rules(self):
        """DELETE /api/hotword-rules — 清空规则（需 token）"""
        if not self._verify_token():
            self._send_error_json(403, "Token 校验失败")
            return
        try:
            hm = self._server_ctx._get_hotword_manager()
            if hm is None:
                self._send_error_json(503, "热词系统未启用")
                return
            hm._rules = []
            hm._compiled_rules = []
            if not hm.save_rules_to_file():
                self._send_error_json(500, "保存规则文件失败")
                return
            tp = self._server_ctx._get_text_pipeline()
            if tp:
                tp.schedule_reload()
            self._send_json(200, {"ok": True, "message": "规则已清空"})
        except Exception as e:
            self._send_error_json(500, f"清空规则失败: {e}")

    def _verify_token(self) -> bool:
        """校验 URL 中的 token 参数"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        tokens = params.get("token", [])
        if not tokens:
            # 也检查 Header
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                tokens = [auth[7:]]
        if not tokens:
            return False
        return secrets.compare_digest(tokens[0], self._server_ctx._token)

    def _read_body(self) -> Optional[bytes]:
        """读取请求体（带大小限制）"""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > _MAX_BODY_SIZE:
            self._send_error_json(413, "请求体过大")
            return None
        if content_length == 0:
            return b""
        return self.rfile.read(content_length)

    def _send_json(self, code: int, data: dict):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, code: int, message: str):
        """发送错误 JSON 响应"""
        self._send_json(code, {"ok": False, "error": message})


class ConfigWebServer:
    """配置 Web 服务。

    绑定 127.0.0.1，启动时生成随机 token，通过 URL 参数校验。

    Args:
        config: AppConfig 实例
        engine: CoreEngine 实例（用于获取状态）
        on_config_changed: 配置变更回调
    """

    def __init__(self, config, engine=None, stats=None, hotword_manager=None, on_config_changed: Optional[Callable] = None):
        self._config = config
        self._engine = engine
        self._stats = stats
        self._hotword_manager = hotword_manager
        self._on_config_changed = on_config_changed
        self._token = secrets.token_hex(16)
        self._config_url = f"http://127.0.0.1:{config.web.port}?token={self._token}"
        self._http_server: Optional[HTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        # 配置路径：打包后使用 exe 同级目录，开发环境使用项目根目录
        user_data = os.environ.get('VOICE_INPUT_TOOL_USER_DATA', '')
        if user_data:
            self._config_path = str(Path(user_data) / "config.yaml")
        else:
            self._config_path = str(Path(__file__).parent.parent / "config.yaml")

    def start(self):
        """启动 Web 服务（后台 daemon 线程）"""
        port = self._config.web.port

        # 创建 handler 子类，注入 server_ctx
        handler = type("ConfigHandler", (_ConfigHandler,), {
            "_server_ctx": self,
        })

        try:
            self._http_server = HTTPServer(("127.0.0.1", port), handler)
        except OSError as e:
            logger.error("Web 服务启动失败（端口 %d 可能被占用）: %s", port, e)
            return

        self._server_thread = threading.Thread(
            target=self._http_server.serve_forever,
            name="web-server",
            daemon=True,
        )
        self._server_thread.start()
        logger.info("Web 配置服务启动: %s", self._config_url)

    def stop(self):
        """停止 Web 服务"""
        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server = None
        logger.info("Web 配置服务已停止")

    def get_config_url(self) -> str:
        """返回带 token 的配置页 URL"""
        return self._config_url

    # ============================================================
    # 供 Handler 调用的方法
    # ============================================================

    def _get_hotword_manager(self):
        """获取 HotwordManager 实例。"""
        if self._hotword_manager is not None:
            return self._hotword_manager
        if self._engine and hasattr(self._engine, '_hotword_manager'):
            return self._engine._hotword_manager
        return None

    def _get_text_pipeline(self):
        """获取 TextPipeline 实例。"""
        if self._engine and hasattr(self._engine, '_text_pipeline'):
            return self._engine._text_pipeline
        return None

    def _get_config_dict(self) -> dict:
        """获取当前配置字典"""
        from config import _dataclass_to_dict
        return _dataclass_to_dict(self._config)  # type: ignore

    def _update_config(self, updates: dict) -> bool:
        """更新配置（线程安全）"""
        try:
            from config import save_config, _flatten_to_appconfig, AppConfig

            # 合并更新到当前配置
            current_dict = self._get_config_dict()
            self._deep_merge(current_dict, updates)

            # 重新解析为 AppConfig（触发校验）
            new_config = _flatten_to_appconfig(current_dict)

            # 保存到文件
            save_config(self._config_path, new_config)

            # 更新内存中的配置
            self._config.__dict__.update(new_config.__dict__)

            # 通知回调
            if self._on_config_changed:
                self._on_config_changed(new_config)

            return True
        except Exception as e:
            logger.error("更新配置失败: %s", e)
            return False

    def _get_status(self) -> dict:
        """获取引擎状态"""
        status = {
            "engine_state": "unknown",
            "model_loaded": False,
        }
        if self._engine:
            status["engine_state"] = self._engine.state.name
            status["model_loaded"] = (
                self._engine._stt_engine is not None
                and self._engine._stt_engine.model is not None
            )
        return status

    def _test_hotkey(self, key: str) -> dict:
        """测试快捷键是否可用"""
        try:
            import keyboard
            # 尝试短暂注册后立即注销
            keyboard.add_hotkey(key, lambda: None, suppress=False)
            keyboard.remove_hotkey(key)
            return {"ok": True, "message": f"快捷键 '{key}' 可用"}
        except ImportError:
            return {"ok": False, "message": "keyboard 库不可用（需要 Windows 管理员权限）"}
        except Exception as e:
            return {"ok": False, "message": f"快捷键 '{key}' 不可用: {e}"}

    def _test_sound(self):
        """播放测试提示音"""
        if self._engine and self._engine._sound_player:
            self._engine._sound_player.play("start")

    def _record_start(self) -> dict:
        """开始录音"""
        if not self._engine:
            return {"ok": False, "error": "引擎未初始化"}
        state = self._engine.state
        if state.name == "RECORDING":
            return {"ok": False, "error": "已在录音中", "state": state.name}
        if state.name == "PROCESSING":
            return {"ok": False, "error": "正在处理中", "state": state.name}
        if state.name == "LOADING":
            return {"ok": False, "error": "模型加载中", "state": state.name}
        if state.name == "ERROR":
            return {"ok": False, "error": "引擎异常", "state": state.name}
        self._engine.on_hotkey_start()
        return {"ok": True, "state": self._engine.state.name, "message": "开始录音"}

    def _record_stop(self) -> dict:
        """停止录音并转写"""
        if not self._engine:
            return {"ok": False, "error": "引擎未初始化"}
        state = self._engine.state
        if state.name != "RECORDING":
            return {"ok": False, "error": "未在录音中", "state": state.name}
        self._engine.on_hotkey_stop()
        return {"ok": True, "state": self._engine.state.name, "message": "停止录音，正在识别..."}

    def _record_toggle(self) -> dict:
        """切换录音状态"""
        if not self._engine:
            return {"ok": False, "error": "引擎未初始化"}
        self._engine.on_hotkey_toggle()
        return {"ok": True, "state": self._engine.state.name}

    def _test_transcribe(self, duration: int) -> dict:
        """自动录音→停止→等待转写结果（用于自动化测试）

        返回: {"ok": True, "text": "...", "language": "zh", "duration_ms": 1500}
        """
        import time
        if not self._engine:
            return {"ok": False, "error": "引擎未初始化"}

        # 检查模型是否已加载
        if self._engine.state.name == "LOADING":
            return {"ok": False, "error": "模型加载中，请稍后"}
        if self._engine.state.name == "ERROR":
            return {"ok": False, "error": "引擎异常，请重启"}

        # 准备收集结果
        result_data = {"done": threading.Event()}
        original_callback = self._engine._on_stt_result

        def capture_callback(text, language, duration_ms, error):
            result_data["text"] = text
            result_data["language"] = language
            result_data["duration_ms"] = duration_ms
            result_data["error"] = str(error) if error else None
            result_data["done"].set()
            if original_callback:
                original_callback(text, language, duration_ms, error)

        self._engine._on_stt_result = capture_callback

        # 开始录音
        if self._engine.state.name == "IDLE":
            self._engine.on_hotkey_start()
            if self._engine.state.name != "RECORDING":
                self._engine._on_stt_result = original_callback
                return {"ok": False, "error": f"无法开始录音，状态: {self._engine.state.name}"}
        elif self._engine.state.name != "RECORDING":
            self._engine._on_stt_result = original_callback
            return {"ok": False, "error": f"当前状态不可录音: {self._engine.state.name}"}

        # 等待录音
        time.sleep(duration)

        # 停止录音
        self._engine.on_hotkey_stop()

        # 等待转写结果（最多30秒）
        if not result_data["done"].wait(timeout=30):
            self._engine._on_stt_result = original_callback
            return {"ok": False, "error": "转写超时（30秒）", "state": self._engine.state.name}

        self._engine._on_stt_result = original_callback

        if result_data.get("error"):
            return {"ok": False, "error": result_data["error"], "state": self._engine.state.name}
        return {
            "ok": True,
            "text": result_data.get("text", ""),
            "language": result_data.get("language"),
            "duration_ms": result_data.get("duration_ms"),
            "state": self._engine.state.name,
        }

    @staticmethod
    def _deep_merge(base: dict, override: dict):
        """深度合并字典（就地修改 base）"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ConfigWebServer._deep_merge(base[key], value)
            else:
                base[key] = value
