#!/usr/bin/env python3
"""PayShield — учебный CLI-прототип платёжного сервиса (кейс 1).

Запуск:
    python3 05_app.py payment.json

Важно: это консольный прототип, а не HTTP-сервер. OpenAPI описывает целевой
HTTP-контракт. Прототип принимает Idempotency-Key в JSON как idempotency_key.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
RECIPIENTS_CANDIDATES = [
    BASE_DIR / "01_данные" / "recipients.csv",
    BASE_DIR / "recipients.csv",
]
OUT_CSV = BASE_DIR / "payments_out.csv"
SERVICE_LOG = BASE_DIR / "payment_service.log"
GATEWAY_LOG = BASE_DIR / "gateway.log"
IDEMPOTENCY_TTL = timedelta(hours=24)
ORDER_ID_PATTERN = re.compile(r"^ord_[0-9]{4}$")
AMOUNT_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]{1,2})?$")
PAN_PATTERN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
LABELED_CVV_PATTERN = re.compile(r"(?i)\b(?:cvv|cvc)\s*[:=]?\s*\d{3,4}\b")

FIELDNAMES = [
    "payment_id", "order_id", "recipient", "purpose", "amount", "currency",
    "status", "error_code", "idempotency_key", "created_at", "confirmed_at",
    "correlation_id", "response_json",
]

logging.basicConfig(
    filename=SERVICE_LOG,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("PayShield")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def load_active_recipients() -> set[str]:
    """Возвращает ID получателей со статусом ACTIVE."""
    path = next((p for p in RECIPIENTS_CANDIDATES if p.exists()), None)
    if path is None:
        raise FileNotFoundError(
            "Не найден recipients.csv: положите его в 01_данные/ или рядом с 05_app.py"
        )
    active: set[str] = set()
    with path.open("r", encoding="cp1251", newline="") as file:
        for row in csv.DictReader(file):
            if row.get("status", "").strip().upper() == "ACTIVE":
                active.add(row.get("recipient_id", "").strip())
    return active


def load_existing_payments() -> list[dict[str, str]]:
    if not OUT_CSV.exists() or OUT_CSV.stat().st_size == 0:
        return []
    with OUT_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        old_fields = reader.fieldnames or []
    # Мягкая миграция старого журнала: добавляем response_json в конец,
    # сохраняя существующие записи и не меняя исходный payments.csv.
    if old_fields != FIELDNAMES:
        temporary = OUT_CSV.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in FIELDNAMES})
        temporary.replace(OUT_CSV)
    return rows


def validate(
    payload: dict[str, Any], active_recipients: set[str]
) -> list[tuple[str, str, str]]:
    """Проверки V-1…V-7. Возвращает (поле, код, сообщение)."""
    errors: list[tuple[str, str, str]] = []
    required = ("order_id", "recipient", "purpose", "amount", "currency", "idempotency_key")
    for field in required:
        value = payload.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append((field, "VALIDATION_ERROR", "Обязательное поле не заполнено"))

    order_id = payload.get("order_id")
    if order_id not in (None, "") and (
        not isinstance(order_id, str) or not ORDER_ID_PATTERN.fullmatch(order_id)
    ):
        errors.append(("order_id", "INVALID_FORMAT", "Формат должен быть ord_ и 4 цифры"))

    amount_value = payload.get("amount")
    if amount_value not in (None, ""):
        try:
            amount = Decimal(str(amount_value))
            if not amount.is_finite():
                raise InvalidOperation
            if amount <= 0 or amount > Decimal("1000000"):
                errors.append(("amount", "AMOUNT_OUT_OF_RANGE", "Сумма должна быть > 0 и ≤ 1 000 000"))
            if not AMOUNT_PATTERN.fullmatch(str(amount_value)):
                errors.append(("amount", "INVALID_AMOUNT_PRECISION", "Допускается не более 2 знаков после точки"))
        except (InvalidOperation, ValueError, TypeError):
            errors.append(("amount", "AMOUNT_TYPE_ERROR", "Сумма должна быть числом"))

    currency = payload.get("currency")
    if currency not in (None, "") and currency != "RUB":
        errors.append(("currency", "UNSUPPORTED_CURRENCY", "Поддерживается только RUB"))

    recipient = payload.get("recipient")
    if recipient not in (None, "") and (
        not isinstance(recipient, str) or recipient not in active_recipients
    ):
        errors.append(("recipient", "RECIPIENT_NOT_FOUND", "Получатель отсутствует или неактивен"))

    purpose = payload.get("purpose")
    if purpose not in (None, ""):
        if not isinstance(purpose, str) or not (1 <= len(purpose) <= 200):
            errors.append(("purpose", "INVALID_PURPOSE", "Назначение должно содержать от 1 до 200 символов"))
        elif PAN_PATTERN.search(purpose) or LABELED_CVV_PATTERN.search(purpose):
            errors.append(("purpose", "INVALID_PURPOSE", "Назначение не должно содержать PAN или CVV"))

    key = payload.get("idempotency_key")
    if key not in (None, "") and (
        not isinstance(key, str) or not (8 <= len(key) <= 64)
    ):
        errors.append(("idempotency_key", "INVALID_FORMAT", "Ключ должен содержать 8–64 символа"))

    return errors


def is_key_active(payment: dict[str, str]) -> bool:
    created_at = payment.get("created_at", "")
    if not created_at:
        return True  # безопасная совместимость со старыми строками CSV
    try:
        timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = utc_now() - timestamp.astimezone(timezone.utc)
    return timedelta(0) <= age < IDEMPOTENCY_TTL


def check_idempotency(
    payload: dict[str, Any], existing_payments: list[dict[str, str]]
) -> tuple[str, dict[str, str] | None]:
    """NEW / REPLAY / CONFLICT; ключ действует 24 часа."""
    key = payload.get("idempotency_key")
    if not key:
        return "NEW", None
    compared_fields = ("order_id", "recipient", "purpose", "amount", "currency")
    for existing in existing_payments:
        if existing.get("idempotency_key") != key or not is_key_active(existing):
            continue
        for field in compared_fields:
            if field == "amount":
                try:
                    same = Decimal(str(payload.get(field))) == Decimal(str(existing.get(field)))
                except InvalidOperation:
                    same = False
            else:
                same = str(payload.get(field)) == str(existing.get(field))
            if not same:
                return "CONFLICT", existing
        return "REPLAY", existing
    return "NEW", None


def find_duplicate_order(
    payload: dict[str, Any], existing_payments: list[dict[str, str]]
) -> dict[str, str] | None:
    for existing in existing_payments:
        if (
            existing.get("order_id") == payload.get("order_id")
            and existing.get("recipient") == payload.get("recipient")
        ):
            return existing
    return None


def emulate_gateway(order_id: str) -> tuple[str, str | None]:
    """Детерминированный шлюз: SUCCESS / DECLINED / TIMEOUT."""
    bucket = int(hashlib.md5(order_id.encode("utf-8")).hexdigest(), 16) % 10
    if bucket <= 6:
        return "SUCCESS", None
    if bucket == 7:
        return "DECLINED", "INSUFFICIENT_FUNDS"
    if bucket == 8:
        return "DECLINED", "CARD_DECLINED"
    return "TIMEOUT", "GATEWAY_TIMEOUT"


def append_payment(payment: dict[str, Any]) -> None:
    file_is_empty = not OUT_CSV.exists() or OUT_CSV.stat().st_size == 0
    if not file_is_empty:
        with OUT_CSV.open("rb+") as file:
            file.seek(-1, 2)
            if file.read(1) not in (b"\n", b"\r"):
                file.seek(0, 2)
                file.write(b"\n")
    with OUT_CSV.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if file_is_empty:
            writer.writeheader()
        writer.writerow(payment)


def log_gateway(
    order_id: str, correlation_id: str, result: str, error_code: str | None
) -> None:
    record = {
        "timestamp": iso_now(),
        "order_id": order_id,
        "correlation_id": correlation_id,
        "result": result,
        "error_code": error_code,
        "duration_ms": 0,
    }
    with GATEWAY_LOG.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def emit(response: dict[str, Any], exit_code: int) -> int:
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("Использование: python3 05_app.py payment.json", file=sys.stderr)
        return 1
    input_path = Path(args[0])
    correlation_id = "corr_" + uuid.uuid4().hex[:12]
    try:
        with input_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            raise ValueError("Корень JSON должен быть объектом")
        active = load_active_recipients()
        existing = load_existing_payments()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.exception("Не удалось прочитать входные данные correlation_id=%s", correlation_id)
        return emit({
            "status": "ERROR", "error_code": "INVALID_REQUEST",
            "message": str(exc), "http_status": 400,
            "retryable": False, "correlation_id": correlation_id,
        }, 2)

    errors = validate(payload, active)
    if errors:
        logger.warning("Validation failed correlation_id=%s errors=%s", correlation_id, errors)
        validation_http_status = 404 if errors[0][1] == "RECIPIENT_NOT_FOUND" else 422
        return emit({
            "status": "ERROR",
            "error_code": errors[0][1],
            "message": errors[0][2],
            "http_status": validation_http_status,
            "retryable": False,
            "correlation_id": correlation_id,
            "details": [
                {"field": field, "error_code": code, "message": message}
                for field, code, message in errors
            ],
        }, 2)

    idem_state, idem_payment = check_idempotency(payload, existing)
    if idem_state == "CONFLICT":
        logger.warning("Idempotency conflict correlation_id=%s", correlation_id)
        return emit({
            "status": "ERROR", "error_code": "IDEMPOTENCY_KEY_CONFLICT",
            "message": "Idempotency-Key уже использован с другими данными",
            "http_status": 409, "retryable": False,
            "correlation_id": correlation_id,
        }, 1)

    if idem_state == "REPLAY" and idem_payment is not None:
        saved = idem_payment.get("response_json", "")
        if saved:
            try:
                response = json.loads(saved)
            except json.JSONDecodeError:
                response = None
        else:
            response = None
        if response is None:
            response = {
                "payment_id": idem_payment.get("payment_id"),
                "status": idem_payment.get("status"),
                "error_code": idem_payment.get("error_code") or None,
                "payment": {k: v for k, v in idem_payment.items() if k != "response_json"},
            }
        response["replayed"] = True
        response["http_status"] = 200
        logger.info("Idempotent replay correlation_id=%s payment_id=%s",
                    correlation_id, idem_payment.get("payment_id"))
        return emit(response, 0)

    duplicate = find_duplicate_order(payload, existing)
    if duplicate is not None:
        logger.warning("Duplicate order correlation_id=%s order_id=%s",
                       correlation_id, payload["order_id"])
        return emit({
            "status": "ERROR", "error_code": "DUPLICATE_ORDER",
            "message": "Платёж по этой паре order_id + recipient уже существует",
            "http_status": 409, "retryable": False,
            "correlation_id": correlation_id,
            "existing_payment": {k: v for k, v in duplicate.items() if k != "response_json"},
        }, 1)

    gateway_result, gateway_error = emulate_gateway(payload["order_id"])
    log_gateway(payload["order_id"], correlation_id,
                "TIMEOUT" if gateway_result == "TIMEOUT" else gateway_result,
                gateway_error)
    if gateway_result == "TIMEOUT":
        payment_status = "PENDING"
        http_status = 504
        exit_code = 3
    elif gateway_result == "DECLINED":
        payment_status = "DECLINED"
        http_status = 402
        exit_code = 1
    else:
        payment_status = "SUCCESS"
        http_status = 200
        exit_code = 0

    payment_id = "pay_demo_" + payload["order_id"][-4:]
    payment = {
        "payment_id": payment_id,
        "order_id": payload["order_id"],
        "recipient": payload["recipient"],
        "purpose": payload["purpose"],
        "amount": str(Decimal(str(payload["amount"])).quantize(Decimal("0.01"))),
        "currency": payload["currency"],
        "status": payment_status,
        "error_code": gateway_error or "",
        "idempotency_key": payload["idempotency_key"],
        "created_at": iso_now(),
        "confirmed_at": iso_now() if payment_status in ("SUCCESS", "DECLINED") else "",
        "correlation_id": correlation_id,
        "response_json": "",
    }
    response = {
        "payment_id": payment_id,
        "status": payment_status,
        "error_code": gateway_error,
        "http_status": http_status,
        "retryable": payment_status == "PENDING",
        "correlation_id": correlation_id,
        "payment": {k: v for k, v in payment.items() if k != "response_json"},
    }
    payment["response_json"] = json.dumps(response, ensure_ascii=False)
    append_payment(payment)
    logger.info("Payment processed correlation_id=%s payment_id=%s status=%s",
                correlation_id, payment_id, payment_status)
    return emit(response, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
