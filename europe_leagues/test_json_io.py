import json
import tempfile
import unittest
from pathlib import Path

import runtime.paths  # noqa: F401  规避 storage 包循环导入的初始化顺序
from storage._jsonio import atomic_write_json, safe_read_json


class JsonIoTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / 'x.json'

    def test_atomic_write_then_read(self):
        atomic_write_json(self.path, {'a': 1, '中文': '值'})
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), {'a': 1, '中文': '值'})

    def test_atomic_write_leaves_no_tmp(self):
        atomic_write_json(self.path, {'a': 1})
        leftovers = list(self.dir.glob('*.tmp.*'))
        self.assertEqual(leftovers, [])

    def test_missing_file_returns_default(self):
        self.assertEqual(safe_read_json(self.path, {}), {})

    def test_corrupt_file_is_backed_up_not_silently_dropped(self):
        self.path.write_text('{not valid json', encoding='utf-8')
        out = safe_read_json(self.path, {})
        self.assertEqual(out, {})
        # 损坏文件应被备份为 .corrupt，而非静默丢弃
        self.assertTrue((self.dir / 'x.json.corrupt').exists())
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
