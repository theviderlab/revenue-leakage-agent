"""Dates and money helpers (D31): Decimal everywhere, cents rounded half-up per period."""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.domain.errors import DomainError

CENT = Decimal("0.01")


def add_months(d: date, months: int) -> date:
    year, month = divmod(d.month - 1 + months, 12)
    year += d.year
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def parse_date(value: Any, field: str = "date") -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as e:
        raise DomainError(f"{field}: '{value}' is not a valid YYYY-MM-DD date") from e


def to_decimal(value: Any, field: str = "amount") -> Decimal:
    if isinstance(value, bool):
        raise DomainError(f"{field}: '{value}' is not a number")
    try:
        d = Decimal(str(value))
    except InvalidOperation as e:
        raise DomainError(f"{field}: '{value}' is not a number") from e
    if not d.is_finite():
        raise DomainError(f"{field}: '{value}' is not a finite number")
    return d


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def cents_amount(value: Any, field: str = "amount") -> Decimal:
    """A user-supplied amount: must have at most two decimals (never rounded silently)."""
    d = to_decimal(value, field)
    if d != money(d):
        raise DomainError(f"{field}: {value} has more than two decimals")
    return money(d)


def num(value: Decimal) -> int | float:
    """JSON number: an int when whole (as in data/), else a float with cents."""
    return int(value) if value == value.to_integral_value() else float(value)
