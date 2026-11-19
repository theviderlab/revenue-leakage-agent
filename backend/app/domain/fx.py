"""Strict FX conversion (D20): exact date, only the published direction."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.errors import DomainError
from app.domain.models import Rate
from app.domain.util import money


@dataclass(frozen=True)
class Conversion:
    amount: Decimal
    from_currency: str
    to_currency: str
    on_date: date
    rate: Decimal
    converted: Decimal

    def to_dict(self) -> dict:
        from app.domain.util import num

        return {
            "amount": num(self.amount),
            "from_currency": self.from_currency,
            "to_currency": self.to_currency,
            "on_date": self.on_date.isoformat(),
            "rate": float(self.rate),
            "converted": num(self.converted),
        }


def find_rate(rates: tuple[Rate, ...], from_ccy: str, to_ccy: str, on_date: date) -> Rate | None:
    for r in rates:
        if r.from_currency == from_ccy and r.to_currency == to_ccy and r.on_date == on_date:
            return r
    return None


def convert(
    rates: tuple[Rate, ...], amount: Decimal, from_ccy: str, to_ccy: str, on_date: date
) -> Conversion:
    from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
    if from_ccy == to_ccy:
        return Conversion(amount, from_ccy, to_ccy, on_date, Decimal(1), money(amount))
    rate = find_rate(rates, from_ccy, to_ccy, on_date)
    if rate is None:
        published = ", ".join(
            f"{r.from_currency}->{r.to_currency} on {r.on_date.isoformat()}" for r in rates
        )
        raise DomainError(
            f"no exchange rate {from_ccy}->{to_ccy} on {on_date.isoformat()} "
            f"(only exact dates and the published direction are used; published: {published})"
        )
    return Conversion(amount, from_ccy, to_ccy, on_date, rate.rate, money(amount * rate.rate))
