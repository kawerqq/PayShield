"""Интеграционные проверки CLI PayShield. Запуск: python3 -m unittest discover -s tests"""
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

class PayShieldCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="payshield-test-"))
        shutil.copy(PROJECT / "05_app.py", self.tmp / "05_app.py")
        shutil.copytree(PROJECT / "01_данные", self.tmp / "01_данные")
        self.out = self.tmp / "payments_out.csv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call_app(self, payload, name="input.json"):
        p = self.tmp / name
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(self.tmp / "05_app.py"), str(p)],
            cwd=self.tmp, capture_output=True, text=True
        )

    def rows(self):
        if not self.out.exists():
            return []
        with self.out.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def data(self, order_id, key, amount="1500.00"):
        return {"order_id": order_id, "recipient": "clinic_medservice",
                "purpose": "Оплата консультации", "amount": amount,
                "currency": "RUB", "idempotency_key": key}

    def test_success(self):
        r = self.call_app(self.data("ord_1001", "idem_t1_01", "2500.00"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "SUCCESS")
        self.assertEqual(len(self.rows()), 1)

    def test_decline(self):
        r = self.call_app(self.data("ord_1002", "idem_t1_02"))
        obj = json.loads(r.stdout)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(obj["status"], "DECLINED")
        self.assertEqual(obj["error_code"], "INSUFFICIENT_FUNDS")

    def test_validation(self):
        r = self.call_app(self.data("ord_1003", "idem_t1_03", "-10.00"))
        obj = json.loads(r.stdout)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(obj["error_code"], "AMOUNT_OUT_OF_RANGE")
        self.assertEqual(obj["http_status"], 422)
        self.assertEqual(len(self.rows()), 0)

    def test_idempotent_replay(self):
        data = self.data("ord_1004", "idem_t1_04", "100.00")
        first = self.call_app(data, "first.json")
        second = self.call_app(data, "second.json")
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertTrue(json.loads(second.stdout)["replayed"])
        self.assertEqual(len(self.rows()), 1)

    def test_timeout_and_safe_replay(self):
        data = self.data("ord_1018", "idem_t1_05")
        first = self.call_app(data, "first.json")
        self.assertEqual(first.returncode, 3)
        self.assertEqual(json.loads(first.stdout)["status"], "PENDING")
        log_before = (self.tmp / "gateway.log").read_text(encoding="utf-8")
        second = self.call_app(data, "second.json")
        self.assertTrue(json.loads(second.stdout)["replayed"])
        log_after = (self.tmp / "gateway.log").read_text(encoding="utf-8")
        self.assertEqual(log_before, log_after)
        self.assertIn('"result": "TIMEOUT"', log_after)

    def test_idempotency_conflict(self):
        self.call_app(self.data("ord_1001", "idem_conflict_1", "100.00"), "first.json")
        r = self.call_app(self.data("ord_1001", "idem_conflict_1", "200.00"), "second.json")
        self.assertEqual(json.loads(r.stdout)["error_code"], "IDEMPOTENCY_KEY_CONFLICT")
        self.assertEqual(len(self.rows()), 1)

    def test_non_string_recipient_is_validation_error(self):
        data = self.data("ord_1005", "idem_t1_05")
        data["recipient"] = ["clinic_medservice"]
        r = self.call_app(data)
        obj = json.loads(r.stdout)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(obj["error_code"], "RECIPIENT_NOT_FOUND")
        self.assertEqual(obj["http_status"], 404)
        self.assertEqual(len(self.rows()), 0)

    def test_duplicate_order_different_key(self):
        first = self.call_app(self.data("ord_1001", "idem_order_1", "100.00"), "first.json")
        second = self.call_app(self.data("ord_1001", "idem_order_2", "100.00"), "second.json")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        obj = json.loads(second.stdout)
        self.assertEqual(obj["error_code"], "DUPLICATE_ORDER")
        self.assertEqual(obj["http_status"], 409)
        self.assertEqual(len(self.rows()), 1)

if __name__ == "__main__":
    unittest.main()
