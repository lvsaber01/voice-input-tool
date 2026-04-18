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
        else:
            self._send_error_json(404, "未找到")

    def do_PUT(self):
        """处理 PUT 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            self._handle_put_config()
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

    # ============================================================
    # 工具方法
    # ============================================================

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

    def __init__(self, config, engine=None, on_config_changed: Optional[Callable] = None):
        self._config = config
        self._engine = engine
        self._on_config_changed = on_config_changed
        self._token = secrets.token_hex(16)
        self._config_url = f"http://127.0.0.1:{config.web.port}?token={self._token}"
        self._http_server: Optional[HTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
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

    @staticmethod
    def _deep_merge(base: dict, override: dict):
        """深度合并字典（就地修改 base）"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ConfigWebServer._deep_merge(base[key], value)
            else:
                base[key] = value
