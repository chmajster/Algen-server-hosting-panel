from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP


def money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def dumps_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=json_default)
