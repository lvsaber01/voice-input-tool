"""ClipboardInjectorBase 单元测试"""

import unittest
import base64
import os
import tempfile
from unittest.mock import MagicMock
from platform_adapter.clipboard_base import ClipboardInjectorBase


class FakeConfig:
    restore_clipboard = True
    clipboard_backup = True
    clipboard_restore = True


class ConcreteInjector(ClipboardInjectorBase):
    """测试用具体实现"""
    def __init__(self, config):
        super().__init__(config)
        self.written = []
        self.clipboard_content = "original"

    def write_clipboard(self, text):
        self.written.append(text)
        return True

    def simulate_paste(self):
        return True

    def read_clipboard(self):
        return self.clipboard_content


class TestClipboardBase(unittest.TestCase):

    def test_backup_to_file_base64(self):
        """_backup_to_file 使用 base64 编码"""
        injector = ConcreteInjector(FakeConfig())
        injector._backup_to_file("测试中文内容")
        self.assertTrue(os.path.exists(injector._BACKUP_FILE))
        with open(injector._BACKUP_FILE, 'r') as f:
            encoded = f.read()
        decoded = base64.b64decode(encoded).decode('utf-8')
        self.assertEqual(decoded, "测试中文内容")
        os.unlink(injector._BACKUP_FILE)

    def test_restore_from_file_base64(self):
        """_restore_from_file 使用 base64 解码"""
        injector = ConcreteInjector(FakeConfig())
        content = base64.b64encode("恢复内容".encode('utf-8')).decode('ascii')
        with open(injector._BACKUP_FILE, 'w') as f:
            f.write(content)
        result = injector._restore_from_file()
        self.assertEqual(result, "恢复内容")
        os.unlink(injector._BACKUP_FILE)

    def test_restore_from_missing_file(self):
        """缺失文件返回 None"""
        injector = ConcreteInjector(FakeConfig())
        injector._BACKUP_FILE = "/tmp/nonexistent_backup_test_file"
        result = injector._restore_from_file()
        self.assertIsNone(result)

    def test_inject_empty_text(self):
        """空文本短路处理"""
        injector = ConcreteInjector(FakeConfig())
        result = injector.inject("")
        self.assertTrue(result)
        self.assertEqual(injector.written, [])

    def test_inject_normal_text(self):
        """正常注入流程"""
        injector = ConcreteInjector(FakeConfig())
        result = injector.inject("hello")
        self.assertTrue(result)
        self.assertTrue(len(injector.written) >= 1)
        self.assertEqual(injector.written[0], "hello")


if __name__ == "__main__":
    unittest.main()
