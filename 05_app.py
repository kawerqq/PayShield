#!/usr/bin/env python3
"""
CLI-прототип платёжного сервиса — кейс 1.

Использование:
    python 04_прототип.py payment.json

TODO для команды:
    1. Дописать validate() — правила V-3…V-9 из 02_правила.md.
    2. Дописать emulate_gateway() — 4 сценария (SUCCESS / INSUFFICIENT_FUNDS /
       CARD_DECLINED / TIMEOUT).
    3. Добавить чтение recipients.csv для проверки recipient.
    4. Обработать таймаут: exit code 3.
    5. Записать результат в payments_out.csv (append).
"""
import csv
import hashlib
import json
import os
import sys 
import re
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECIPIENTS_CSV = os.path.join(BASE_DIR, "01_данные", "recipients.csv")
OUT_CSV = os.path.join(BASE_DIR, "payments_out.csv")
FIELDNAMES = [
    "payment_id",
    "order_id",
    "recipient",
    "purpose",
    "amount",
    "currency",
    "status",
    "error_code",
    "idempotency_key",
    "created_at",
    "confirmed_at",
    "correlation_id"
]

def load_active_recipients():
    """Прочитать recipients.csv, вернуть set() активных ID."""
    active = set()
    try:
        with open(RECIPIENTS_CSV, encoding="windows-1251") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "ACTIVE":
                    active.add(row["recipient_id"])
    except FileNotFoundError:
        print(f"WARN: {RECIPIENTS_CSV} не найден", file=sys.stderr)
    return active


def validate(payload, active_recipients, existing_payments):
    """TODO: реализовать V-3…V-9. Вернуть [(field, code), ...]."""
    errors = []
    # ОБРАЗЕЦ — V-1, V-2
    for field in ["order_id", "recipient", "purpose", "amount", "currency"]:
        if not payload.get(field):
            errors.append((field, "VALIDATION_ERROR"))
    
    if "amount" in payload:
        try:
            amt = float(payload["amount"])
        except (TypeError, ValueError):
            errors.append(("amount", "VALIDATION_ERROR"))
        else:
            if amt <= 0 or amt > 1_000_000:
                errors.append(("amount", "AMOUNT_OUT_OF_RANGE"))
            if not re.fullmatch(r"\d+(?:\.\d{1,2})?", str(payload["amount"])):
                errors.append(("amount", "INVALID_AMOUNT_PRECISION"))
    if "order_id" in payload:
        if not re.fullmatch(r"^ord_[0-9]{4}$", payload["order_id"]):
            errors.append(("order_id", "INVALID_FORMAT"))
    if "currency" in payload:
        if payload["currency"] != "RUB":
            errors.append(("currency", "UNSUPPORTED_CURRENCY"))
    if "recipient" in payload:
        if payload["recipient"] not in active_recipients:
            errors.append(("recipient", "RECIPIENT_NOT_FOUND"))
    if "purpose" in payload:
        purpose = str(payload["purpose"])
        if len(purpose) > 200 or re.search(r"\d{13,19}", purpose):
            errors.append(("purpose", "INVALID_PURPOSE"))

    duplicate_payment = None

    for existing in existing_payments:
        if (
            existing.get("order_id") == payload.get("order_id")
            and existing.get("recipient") == payload.get("recipient")
        ):
            duplicate_payment = existing
            break

    # TODO: V-3 (order_id regex), V-4 (currency), V-5 (recipient),
    #       V-6 (purpose), V-7 (2 знака), V-8 (дубль), V-9 (idempotency)
    return errors, duplicate_payment


def emulate_gateway(order_id):
    """TODO: 4 сценария. Вернуть (status, error_code)."""
    h = int(hashlib.md5(order_id.encode()).hexdigest(), 16) % 10
    # ОБРАЗЕЦ — 0..6 = SUCCESS
    if h <= 6:
        return "SUCCESS", None
    elif h == 7:
        return "INSUFFICIENT_FUNDS", "INSUFFICIENT_FUNDS"
    elif h == 8:
        return "CARD_DECLINED", "CARD_DECLINED"
    else:
        return "TIMEOUT", "GATEWAY_TIMEOUT"
    # TODO: 7 = INSUFFICIENT_FUNDS, 8 = CARD_DECLINED, 9 = TIMEOUT

def load_existing_payments():
    """Прочитать ранее сохранённые платежи из payments_out.csv."""
    if not os.path.exists(OUT_CSV):
        return []

    with open(OUT_CSV, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)
    
def append_payment(payment):
    file_is_empty = not os.path.exists(OUT_CSV) or os.path.getsize(OUT_CSV) == 0

    # Разделить новую строку с предыдущей записью, даже если файл не оканчивался переводом строки.
    if not file_is_empty:
        with open(OUT_CSV, "rb+") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) not in (b"\n", b"\r"):
                f.seek(0, os.SEEK_END)
                f.write(b"\n")

    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)

        if file_is_empty:
            writer.writeheader()

        writer.writerow(payment)
        
def main():
    if len(sys.argv) < 2:
        print("Использование: python3 05_app.py payment.json")
        sys.exit(1)
    
    with open(sys.argv[1], encoding="utf-8") as f:
        payload = json.load(f)
    
    active = load_active_recipients()
    existing_payments = load_existing_payments()
    errors, duplicate_payment = validate(payload, active, existing_payments)

    if errors:
        print(json.dumps({
            "status": "ERROR",
            "error_code": "VALIDATION_ERROR",
            "errors": [{"field": f, "code": c} for f, c in errors],
        }, ensure_ascii=False, indent=2))
        sys.exit(2)

    if duplicate_payment is not None:
        print(json.dumps({
            "status": "ERROR",
            "error_code": "DUPLICATE_ORDER",
            "http_status": 409,
            "payment": duplicate_payment,
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    status, error_code = emulate_gateway(payload["order_id"])
    
    if status == "TIMEOUT":
        payment_status = "PENDING"
    else:
        payment_status = status

    payment_id = "pay_demo_" + payload["order_id"][-4:]
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    payment = {
        "payment_id": payment_id,
        "order_id": payload["order_id"],
        "recipient": payload["recipient"],
        "purpose": payload["purpose"],
        "amount": payload["amount"],
        "currency": payload["currency"],
        "status": payment_status,
        "error_code": error_code or "",
        "idempotency_key": payload.get("idempotency_key", ""),
        "created_at": created_at,
        "confirmed_at": "",
        "correlation_id": "",
    }

    append_payment(payment)

    
    print(json.dumps({
        "payment_id": payment_id,
        "status": payment_status,
        "error_code": error_code,
    }, ensure_ascii=False, indent=2))

    sys.exit(0 if status == "SUCCESS" else (3 if status == "TIMEOUT" else 1))


if __name__ == "__main__":
    main()
