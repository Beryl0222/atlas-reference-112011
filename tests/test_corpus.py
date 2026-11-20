"""交接资料格式和原文完整性测试。"""
from pathlib import Path
import json
import subprocess
import sys
import unittest

from corpus import read_collection

ROOT = Path(__file__).resolve().parents[1]


def records(name):
    return read_collection(ROOT / "data" / (name + ".json"))


class MaterialTests(unittest.TestCase):
    def test_collections_are_utf8_record_arrays(self):
        for name, fields in FIELDS.items():
            with self.subTest(collection=name):
                rows = records(name)
                self.assertTrue(rows)
                for row in rows:
                    self.assertEqual(set(row), set(fields))
                    self.assertIsInstance(row["id"], str)
                    self.assertTrue(row["id"])

    def test_reader_retains_exact_text_and_order(self):
        result = subprocess.check_output(
            [sys.executable, "-m", "corpus", PRIMARY, "--id", LINE_ID],
            cwd=ROOT, text=True, encoding="utf-8",
        )
        original = [row for row in records(PRIMARY) if row["id"] == LINE_ID]
        self.assertTrue(original)
        self.assertEqual(json.loads(result), original)

    def test_repagination_and_figure_replacement_are_separate(self):
        paragraphs = [row for row in records("paragraphs") if row["id"] == "P1"]
        self.assertEqual(len({row["正文"] for row in paragraphs}), 1)
        self.assertEqual(len({row["位置"] for row in paragraphs}), 2)
        maps = [row for row in records("coverage") if row["id"] == "F1"]
        self.assertEqual(len({tuple(row["区域"]) for row in maps}), 2)
        units = {row["上单元"] for row in records("reaches")}
        self.assertTrue({"A", "B"}.issubset(units))


PRIMARY = 'paragraphs'
LINE_ID = 'P1'
FIELDS = {
    "paragraphs": [
        "id",
        "版次",
        "位置",
        "正文"
    ],
    "gazetteer": [
        "id",
        "名称",
        "生效",
        "止于",
        "范围",
        "沿革"
    ],
    "coverage": [
        "id",
        "版次",
        "图号",
        "页",
        "年",
        "区域"
    ],
    "desk": [
        "id",
        "版次",
        "入口",
        "对象",
        "时间",
        "内容"
    ],
    "reaches": [
        "id",
        "支系",
        "上单元",
        "下单元",
        "类型"
    ],
    "context": [
        "id",
        "版次",
        "章节",
        "时间口径",
        "编校备注"
    ]
}
