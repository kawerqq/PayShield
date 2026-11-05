import csv
from collections import Counter
import re

def analyze(path, name):
    print(f"\n=== {name} ===")
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Строк: {len(rows)}")
    print(f"Колонки: {list(rows[0].keys())}")
    for col in rows[0].keys():
        values = [r[col] for r in rows if r[col]]
        uniq = set(values)
        print(f"\n[{col}]")
        print(f"  непустых: {len(values)}/{len(rows)}")
        print(f"  уникальных: {len(uniq)}")
        print(f"  примеры: {list(uniq)[:3]}")
        # для числовых/длинных
        if col in ("amount",):
            nums = [float(v) for v in values]
            print(f"  min={min(nums)}, max={max(nums)}")
        if col in ("purpose", "order_id"):
            lens = [len(v) for v in values]
            print(f"  длина: min={min(lens)}, max={max(lens)}")

analyze("payments.csv", "payments")
analyze("recipients.csv", "recipients")