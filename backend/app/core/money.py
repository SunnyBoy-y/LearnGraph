"""Exact native-currency amounts shared by schemas, migrations and billing."""
from decimal import Decimal, InvalidOperation, localcontext
from functools import wraps
from typing import Any


def decimal_amount(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Amount must be a finite non-negative decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Amount must be a finite non-negative decimal")
    # Per-million charges and unit multipliers can have more fractional
    # places than the supplier's rate. Preserve those places in the ledger.
    if len(result.as_tuple().digits) > 192 or result.adjusted() > 63 or result.as_tuple().exponent < -96:
        raise ValueError("Amount exceeds the supported decimal precision")
    return result


def amount_text(value: Any) -> str:
    number = decimal_amount(value)
    if not number:
        return "0"
    result = format(number, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def input_amount_text(value: Any) -> str:
    """Bound editable inputs while leaving room for exact computed charges."""
    number = decimal_amount(value)
    if number.adjusted() > 24 or number.as_tuple().exponent < -24:
        raise ValueError("Input amount supports at most 25 integer and 24 fractional places")
    return amount_text(number)


def sum_amounts(values: Any) -> str:
    with localcontext() as context:
        context.prec = 256
        return amount_text(sum((decimal_amount(value) for value in values if value is not None), Decimal(0)))


def currency_code(value: str) -> str:
    currency = value.strip().upper()
    if currency == "RMB":
        currency = "CNY"
    if currency not in {"USD", "CNY"}:
        raise ValueError("Currency must be USD or CNY")
    return currency


def exact_money(function):
    """Keep budget addition/subtraction as exact as the ledger amounts."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext() as context:
            context.prec = 256
            return function(*args, **kwargs)
    return wrapped
