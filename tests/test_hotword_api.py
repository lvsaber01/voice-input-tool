"""热词 Web API 单元测试。

直接测试 handler 方法，绕过 HTTP 解析层。
"""

import json
import os
import sys
import tempfile
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hotword import HotwordManager
from core.text_pipeline import TextPipeline
from gui.web_server import ConfigWebServer


class MockHandler:
    """模拟 _ConfigHandler，只暴露必要属性，并绑定 server handler 方法。"""

    def __init__(self, server_ctx, path="/api/hotwords", body=b"", headers=None):
        self._server_ctx = server_ctx
        self.path = path
        self.wfile = BytesIO()
        self.rfile = BytesIO(body)
        self.response_code = 200
        self._headers = {}
        self.headers = MagicMock()
        default_headers = {"Content-Length": str(len(body))}
        if headers:
            default_headers.update(headers)
        self.headers.get = lambda k, dv=None: default_headers.get(k, dv)

        # 绑定 _ConfigHandler 的热词相关方法
        from gui.web_server import _ConfigHandler
        for method_name in [
            '_handle_get_hotwords', '_handle_put_hotwords', '_handle_post_hotword',
            '_handle_delete_hotwords', '_handle_get_hotword_rules',
            '_handle_put_hotword_rules', '_handle_post_hotword_rule',
            '_handle_delete_hotword_rules',
        ]:
            if hasattr(_ConfigHandler, method_name):
                setattr(self, method_name, getattr(_ConfigHandler, method_name).__get__(self))

    def send_response(self, code):
        self.response_code = code

    def send_header(self, key, value):
        self._headers[key] = value

    def end_headers(self):
        pass

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, code, message):
        self._send_json(code, {"ok": False, "error": message})

    def _verify_token(self):
        """直接从 URL 提取 token。"""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        tokens = params.get("token", [])
        if not tokens:
            return False
        import secrets
        return secrets.compare_digest(tokens[0], self._server_ctx._token)

    def _read_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 1024 * 1024:
            self._send_error_json(413, "请求体过大")
            return None
        if content_length == 0:
            return b""
        return self.rfile.read(content_length)

    def get_response(self):
        return json.loads(self.wfile.getvalue())


def _make_server_ctx(hotwords_content="", rules_content=""):
    """创建带 HotwordManager 的 ConfigWebServer。"""
    from config import AppConfig
    config = AppConfig()

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(hotwords_content)
        hw_path = f.name
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(rules_content)
        rules_path = f.name

    mgr = HotwordManager(hotwords_file=hw_path, rules_file=rules_path)
    mgr._hotwords_file = hw_path
    mgr._rules_file = rules_path
    pipeline = TextPipeline(hotword_manager=mgr)

    server = ConfigWebServer(config=config, hotword_manager=mgr)
    return server, mgr, pipeline, hw_path, rules_path


class TestGetHotwordsAPI(unittest.TestCase):
    """GET /api/hotwords 测试。"""

    def setUp(self):
        self.ctx = _make_server_ctx(hotwords_content="CUDA\nKubernetes -> K8s\n")
        self.server, self.mgr, self.pipeline, self.hw_path, self.rules_path = self.ctx
        self.token = self.server._token

    def tearDown(self):
        os.unlink(self.hw_path)
        os.unlink(self.rules_path)

    def test_get_returns_list(self):
        h = MockHandler(self.server, "/api/hotwords")
        h._handle_get_hotwords()
        resp = h.get_response()
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["count"], 2)
        self.assertEqual(len(resp["hotwords"]), 2)

    def test_get_format(self):
        h = MockHandler(self.server, "/api/hotwords")
        h._handle_get_hotwords()
        resp = h.get_response()
        hw = resp["hotwords"][0]
        self.assertIn("source", hw)
        self.assertIn("target", hw)
        self.assertIn("category", hw)
        self.assertIn("text_replace", hw)
        self.assertIn("model_hotword", hw)


class TestGetHotwordRulesAPI(unittest.TestCase):
    """GET /api/hotword-rules 测试。"""

    def setUp(self):
        self.ctx = _make_server_ctx(rules_content=r"[，。] = ,")
        self.server, self.mgr, self.pipeline, self.hw_path, self.rules_path = self.ctx

    def tearDown(self):
        os.unlink(self.hw_path)
        os.unlink(self.rules_path)

    def test_get_returns_rules(self):
        h = MockHandler(self.server, "/api/hotword-rules")
        h._handle_get_hotword_rules()
        resp = h.get_response()
        self.assertTrue(resp["ok"])
        self.assertGreaterEqual(resp["count"], 1)


class TestPutHotwordsAPI(unittest.TestCase):
    """PUT /api/hotwords 测试。"""

    def setUp(self):
        self.ctx = _make_server_ctx(hotwords_content="old")
        self.server, self.mgr, self.pipeline, self.hw_path, self.rules_path = self.ctx
        self.token = self.server._token

    def tearDown(self):
        os.unlink(self.hw_path)
        os.unlink(self.rules_path)

    def test_put_replace_success(self):
        body = json.dumps({"hotwords": [{"source": "NewWord", "target": "NW"}]}).encode()
        h = MockHandler(self.server, f"/api/hotwords?token={self.token}", body)
        h._handle_put_hotwords()
        resp = h.get_response()
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["count"], 1)

    def test_put_without_token_fails(self):
        body = json.dumps({"hotwords": [{"source": "Test", "target": "T"}]}).encode()
        h = MockHandler(self.server, "/api/hotwords", body)
        h._handle_put_hotwords()
        self.assertEqual(h.response_code, 403)


class TestPostHotwordAPI(unittest.TestCase):
    """POST /api/hotwords 测试。"""

    def setUp(self):
        self.ctx = _make_server_ctx(hotwords_content="")
        self.server, self.mgr, self.pipeline, self.hw_path, self.rules_path = self.ctx
        self.token = self.server._token

    def tearDown(self):
        os.unlink(self.hw_path)
        os.unlink(self.rules_path)

    def test_post_add_success(self):
        body = json.dumps({"source": "TestWord", "target": "TW"}).encode()
        h = MockHandler(self.server, f"/api/hotwords?token={self.token}", body)
        h._handle_post_hotword()
        resp = h.get_response()
        self.assertTrue(resp["ok"])

    def test_post_empty_source_fails(self):
        body = json.dumps({"source": "", "target": "T"}).encode()
        h = MockHandler(self.server, f"/api/hotwords?token={self.token}", body)
        h._handle_post_hotword()
        self.assertEqual(h.response_code, 400)

    def test_post_short_source_fails(self):
        body = json.dumps({"source": "A", "target": "T"}).encode()
        h = MockHandler(self.server, f"/api/hotwords?token={self.token}", body)
        h._handle_post_hotword()
        self.assertEqual(h.response_code, 400)

    def test_post_newline_in_source_fails(self):
        body = json.dumps({"source": "line1\nline2", "target": "T"}).encode()
        h = MockHandler(self.server, f"/api/hotwords?token={self.token}", body)
        h._handle_post_hotword()
        self.assertEqual(h.response_code, 400)


class TestDeleteHotwordsAPI(unittest.TestCase):
    """DELETE /api/hotwords 测试。"""

    def setUp(self):
        self.ctx = _make_server_ctx(hotwords_content="CUDA\n")
        self.server, self.mgr, self.pipeline, self.hw_path, self.rules_path = self.ctx
        self.token = self.server._token

    def tearDown(self):
        os.unlink(self.hw_path)
        os.unlink(self.rules_path)

    def test_delete_clears_all(self):
        h = MockHandler(self.server, f"/api/hotwords?token={self.token}")
        h._handle_delete_hotwords()
        resp = h.get_response()
        self.assertTrue(resp["ok"])
        self.assertEqual(len(self.mgr._entries), 0)

    def test_delete_without_token_fails(self):
        h = MockHandler(self.server, "/api/hotwords")
        h._handle_delete_hotwords()
        self.assertEqual(h.response_code, 403)


class TestPutRulesAPI(unittest.TestCase):
    """PUT /api/hotword-rules 测试。"""

    def setUp(self):
        self.ctx = _make_server_ctx(rules_content="")
        self.server, self.mgr, self.pipeline, self.hw_path, self.rules_path = self.ctx
        self.token = self.server._token

    def tearDown(self):
        os.unlink(self.hw_path)
        os.unlink(self.rules_path)

    def test_put_replace_success(self):
        body = json.dumps({"rules": [{"pattern": r"\d+", "replacement": "NUM"}]}).encode()
        h = MockHandler(self.server, f"/api/hotword-rules?token={self.token}", body)
        h._handle_put_hotword_rules()
        resp = h.get_response()
        self.assertTrue(resp["ok"])


class TestPostRuleAPI(unittest.TestCase):
    """POST /api/hotword-rules 测试。"""

    def setUp(self):
        self.ctx = _make_server_ctx(rules_content="")
        self.server, self.mgr, self.pipeline, self.hw_path, self.rules_path = self.ctx
        self.token = self.server._token

    def tearDown(self):
        os.unlink(self.hw_path)
        os.unlink(self.rules_path)

    def test_post_add_success(self):
        body = json.dumps({"pattern": r"\d+", "replacement": "NUM"}).encode()
        h = MockHandler(self.server, f"/api/hotword-rules?token={self.token}", body)
        h._handle_post_hotword_rule()
        resp = h.get_response()
        self.assertTrue(resp["ok"])

    def test_post_invalid_pattern_fails(self):
        body = json.dumps({"pattern": "[invalid", "replacement": "X"}).encode()
        h = MockHandler(self.server, f"/api/hotword-rules?token={self.token}", body)
        h._handle_post_hotword_rule()
        self.assertEqual(h.response_code, 400)

    def test_post_empty_pattern_fails(self):
        body = json.dumps({"pattern": "", "replacement": "X"}).encode()
        h = MockHandler(self.server, f"/api/hotword-rules?token={self.token}", body)
        h._handle_post_hotword_rule()
        self.assertEqual(h.response_code, 400)


class TestDeleteRulesAPI(unittest.TestCase):
    """DELETE /api/hotword-rules 测试。"""

    def setUp(self):
        self.ctx = _make_server_ctx(rules_content=r"[，] = ,")
        self.server, self.mgr, self.pipeline, self.hw_path, self.rules_path = self.ctx
        self.token = self.server._token

    def tearDown(self):
        os.unlink(self.hw_path)
        os.unlink(self.rules_path)

    def test_delete_clears_all(self):
        h = MockHandler(self.server, f"/api/hotword-rules?token={self.token}")
        h._handle_delete_hotword_rules()
        resp = h.get_response()
        self.assertTrue(resp["ok"])
        self.assertEqual(len(self.mgr._rules), 0)


class TestHotwordManagerAccessor(unittest.TestCase):
    """_get_hotword_manager / _get_text_pipeline 测试。"""

    def test_get_hotword_manager_from_server(self):
        ctx = _make_server_ctx()
        server = ctx[0]
        self.assertIsNotNone(server._get_hotword_manager())

    def test_get_pipeline_from_engine(self):
        ctx = _make_server_ctx()
        server = ctx[0]
        server._engine = MagicMock()
        server._engine._hotword_manager = ctx[1]
        server._engine._text_pipeline = ctx[2]
        server._hotword_manager = None
        self.assertIsNotNone(server._get_hotword_manager())
        self.assertIsNotNone(server._get_text_pipeline())


class TestNoHotwordManager(unittest.TestCase):
    """热词系统未启用时的 API 行为。"""

    def test_get_hotwords_returns_empty(self):
        from config import AppConfig
        config = AppConfig()
        server = ConfigWebServer(config=config)
        h = MockHandler(server, "/api/hotwords")
        h._handle_get_hotwords()
        resp = h.get_response()
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["count"], 0)

    def test_put_hotwords_returns_503(self):
        from config import AppConfig
        config = AppConfig()
        server = ConfigWebServer(config=config)
        token = server._token
        body = json.dumps({"hotwords": [{"source": "Test", "target": "T"}]}).encode()
        h = MockHandler(server, f"/api/hotwords?token={token}", body)
        h._handle_put_hotwords()
        self.assertEqual(h.response_code, 503)


if __name__ == "__main__":
    unittest.main()
