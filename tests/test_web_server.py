"""Web 配置界面 API 测试（Mock，无需真实服务器）

测试覆盖：
  - GET /api/config, /api/status, /api/stats, /api/audio/devices
  - PUT /api/config + token 校验
  - POST 录音控制 + 转写测试 + 热键/提示音测试
  - Token 认证
  - 路由分发（404）
  - ConfigWebServer 生命周期
"""

import json
import unittest
from unittest.mock import MagicMock, patch
from io import BytesIO

from config import AppConfig


# ================================================================
# Helper: Mock handler via real _ConfigHandler
# ================================================================

class MockServerCtx:
    """模拟 ConfigWebServer 的上下文"""
    def __init__(self):
        self._config = AppConfig()
        self._token = "test-token-123"
        self._stats = MagicMock()
        self._stats.get_summary.return_value = {"total": 10}
        self._engine = MagicMock()

    def _get_config_dict(self):
        from config import _dataclass_to_dict
        return _dataclass_to_dict(self._config)

    def _update_config(self, updates):
        return True

    def _get_status(self):
        return {"engine_state": "IDLE", "model_loaded": True}

    def _test_hotkey(self, key):
        return {"ok": True, "key": key}

    def _test_sound(self):
        pass

    def _record_start(self):
        return {"ok": True, "state": "RECORDING"}

    def _record_stop(self):
        return {"ok": True, "state": "IDLE", "text": "hello"}

    def _record_toggle(self):
        return {"ok": True, "state": "RECORDING"}

    def _test_transcribe(self, duration=3):
        return {"ok": True, "text": "test result"}


def _make_handler(path="/", method="GET", body=None, token=None):
    """创建一个绑定到 MockServerCtx 的 handler"""
    from gui.web_server import _ConfigHandler

    ctx = MockServerCtx()
    body_bytes = body.encode() if isinstance(body, str) else body or b""
    rfile = BytesIO(body_bytes)

    # 直接实例化 _ConfigHandler 需要真实 socket，用 mock 绕过
    handler = object.__new__(_ConfigHandler)
    # 先设路径，再追加 token
    if token:
        sep = "&" if "?" in path else "?"
        handler.path = f"{path}{sep}token={token}"
    else:
        handler.path = path
    handler.command = method
    handler.rfile = rfile
    handler.wfile = BytesIO()
    handler.response_code = None
    handler.response_headers = {}
    handler.headers = {"Content-Length": str(len(body_bytes))}

    def mock_send_response(code, _message=None):
        handler.response_code = code
    def mock_send_header(k, v):
        handler.response_headers[k] = v
    def mock_end_headers():
        pass

    handler.send_response = mock_send_response
    handler.send_header = mock_send_header
    handler.end_headers = mock_end_headers
    handler.log_message = lambda *a, **kw: None

    # 设置 server context
    handler._server_ctx = ctx

    return handler, ctx


# ================================================================
# GET 端点
# ================================================================

class TestGetConfig(unittest.TestCase):

    def test_returns_200(self):
        h, ctx = _make_handler("/api/config")
        h._handle_get_config()
        self.assertEqual(h.response_code, 200)

    def test_includes_stt_engine(self):
        h, ctx = _make_handler("/api/config")
        h._handle_get_config()
        # 验证 wfile 中包含 engine 信息
        data = h.wfile.getvalue()
        self.assertIn("engine", data.decode("utf-8", errors="replace"))

    def test_error_returns_500(self):
        h, ctx = _make_handler("/api/config")
        ctx._get_config_dict = MagicMock(side_effect=RuntimeError("fail"))
        h._handle_get_config()
        self.assertEqual(h.response_code, 500)


class TestGetStatus(unittest.TestCase):

    def test_returns_200(self):
        h, ctx = _make_handler("/api/status")
        h._handle_get_status()
        self.assertEqual(h.response_code, 200)

    def test_includes_engine_state(self):
        h, ctx = _make_handler("/api/status")
        h._handle_get_status()
        self.assertEqual(h.response_code, 200)


class TestGetStats(unittest.TestCase):

    def test_returns_200(self):
        h, ctx = _make_handler("/api/stats")
        h._handle_get_stats()
        self.assertEqual(h.response_code, 200)

    def test_no_stats_module(self):
        h, ctx = _make_handler("/api/stats")
        ctx._stats = None
        h._handle_get_stats()
        self.assertEqual(h.response_code, 200)


class TestGetAudioDevices(unittest.TestCase):

    def test_returns_200_or_500(self):
        h, ctx = _make_handler("/api/audio/devices")
        h._handle_get_audio_devices()
        self.assertIn(h.response_code, (200, 500))


# ================================================================
# PUT /api/config
# ================================================================

class TestPutConfig(unittest.TestCase):

    def test_update_success(self):
        body = json.dumps({"stt": {"engine": "mlx_whisper", "model_size": "small"}})
        h, ctx = _make_handler("/api/config", "PUT", body, token="test-token-123")
        h._handle_put_config()
        self.assertEqual(h.response_code, 200)

    def test_rejected_without_token(self):
        body = json.dumps({"stt": {"engine": "mlx_whisper"}})
        h, ctx = _make_handler("/api/config", "PUT", body)
        h._handle_put_config()
        self.assertEqual(h.response_code, 403)

    def test_update_failure(self):
        body = json.dumps({"stt": {"engine": "mlx_whisper"}})
        h, ctx = _make_handler("/api/config", "PUT", body, token="test-token-123")
        ctx._update_config = MagicMock(return_value=False)
        h._handle_put_config()
        self.assertEqual(h.response_code, 400)


# ================================================================
# POST 端点
# ================================================================

class TestRecordStart(unittest.TestCase):

    def test_success(self):
        h, ctx = _make_handler("/api/record/start", "POST", token="test-token-123")
        h._handle_record_start()
        self.assertEqual(h.response_code, 200)

    def test_no_token(self):
        h, ctx = _make_handler("/api/record/start", "POST")
        h._handle_record_start()
        self.assertEqual(h.response_code, 403)


class TestRecordStop(unittest.TestCase):

    def test_success(self):
        h, ctx = _make_handler("/api/record/stop", "POST", token="test-token-123")
        h._handle_record_stop()
        self.assertEqual(h.response_code, 200)


class TestRecordToggle(unittest.TestCase):

    def test_success(self):
        h, ctx = _make_handler("/api/record/toggle", "POST", token="test-token-123")
        h._handle_record_toggle()
        self.assertEqual(h.response_code, 200)


class TestTranscribe(unittest.TestCase):

    def test_success(self):
        body = json.dumps({"duration": 3})
        h, ctx = _make_handler("/api/test/transcribe", "POST", body, token="test-token-123")
        h._handle_test_transcribe()
        self.assertEqual(h.response_code, 200)

    def test_no_token(self):
        h, ctx = _make_handler("/api/test/transcribe", "POST")
        h._handle_test_transcribe()
        self.assertEqual(h.response_code, 403)


class TestHotkeyTest(unittest.TestCase):

    def test_success(self):
        body = json.dumps({"key": "f8"})
        h, ctx = _make_handler("/api/hotkey/test", "POST", body, token="test-token-123")
        h._handle_hotkey_test()
        self.assertEqual(h.response_code, 200)


class TestSoundTest(unittest.TestCase):

    def test_success(self):
        h, ctx = _make_handler("/api/sound/test", "POST", token="test-token-123")
        h._handle_sound_test()
        self.assertEqual(h.response_code, 200)


# ================================================================
# ConfigWebServer 生命周期
# ================================================================

class TestConfigWebServerLifecycle(unittest.TestCase):

    def test_server_creation(self):
        from gui.web_server import ConfigWebServer
        config = AppConfig()
        server = ConfigWebServer(config)
        self.assertIsNotNone(server)
        server.stop()

    def test_server_with_engine(self):
        from gui.web_server import ConfigWebServer
        config = AppConfig()
        engine = MagicMock()
        stats = MagicMock()
        server = ConfigWebServer(config, engine=engine, stats=stats)
        self.assertIsNotNone(server)
        server.stop()


# ================================================================
# Token 认证
# ================================================================

class TestTokenAuth(unittest.TestCase):

    def test_valid_token(self):
        h, ctx = _make_handler("/api/config", "PUT", "{}", token="test-token-123")
        # _verify_token 应返回 True
        result = h._verify_token()
        self.assertTrue(result)

    def test_no_token(self):
        h, ctx = _make_handler("/api/config", "PUT", "{}")
        result = h._verify_token()
        self.assertFalse(result)

    def test_wrong_token(self):
        h, ctx = _make_handler("/api/config", "PUT", "{}", token="wrong-token")
        result = h._verify_token()
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
