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

    def test_handover_identifies_physical_pieces_and_original_orders(self):
        orders = {row["id"] for row in records("orders")}
        parents = {row["id"]: row for row in records("inventory")}
        for row in records("orders"):
            if row["引用"]:
                self.assertIn(row["引用"], orders)
        for row in records("handover"):
            parent = parents[row["母件"]]
            self.assertEqual(row["批"], parent["批"])
            self.assertTrue(set(row["片"]).issubset(parent["可切片"]))
        self.assertNotEqual(parents["P2"]["表面"], parents["P3"]["表面"])


PRIMARY = 'orders'
LINE_ID = 'O1'
FIELDS = {
    "orders": [
        "id",
        "时间",
        "来自",
        "引用",
        "原文"
    ],
    "inventory": [
        "id",
        "批",
        "箱",
        "包装",
        "表面",
        "可切片"
    ],
    "lab": [
        "id",
        "来源",
        "原文"
    ],
    "handover": [
        "id",
        "时间",
        "批",
        "母件",
        "封签",
        "动作",
        "片",
        "原文"
    ]
}
