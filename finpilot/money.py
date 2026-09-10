"""Money primitives.

Spec section 13: "Money uses decimal arithmetic and explicit currency. Rules
specify rounding and accrual conventions."

Every amount in this application is a Money, and a Money always carries its
currency. Two Money values of different currencies never add. This is the
GX01 requirement from the Version 4 expansion, implemented from the start so
the ledger never has to be migrated.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN, getcontext
from typing import Iterable

getcontext().prec = 28

# Minor-unit exponent per currency. JPY has none; the Gulf dinars have three.
MINOR_UNITS: dict[str, int] = {
    "USD": 2, "EUR": 2, "GBP": 2, "CAD": 2, "AUD": 2, "INR": 2, "BRL": 2,
    "SGD": 2, "MXN": 2, "JPY": 0, "KRW": 0, "KWD": 3, "BHD": 3, "TND": 3,
}


class CurrencyMismatch(ValueError):
    """Raised when unlike currencies are combined without explicit conversion."""


@dataclass(frozen=True, order=False)
class Money:
    amount: Decimal
    currency: str = "USD"

    # ---- construction -------------------------------------------------
    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        object.__setattr__(self, "currency", self.currency.upper())

    @classmethod
    def of(cls, value, currency: str = "USD") -> "Money":
        if isinstance(value, Money):
            if value.currency != currency.upper():
                raise CurrencyMismatch(f"{value.currency} is not {currency}")
            return value
        return cls(Decimal(str(value)), currency)

    @classmethod
    def zero(cls, currency: str = "USD") -> "Money":
        return cls(Decimal("0"), currency)

    # ---- arithmetic ---------------------------------------------------
    def _check(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency} and {other.currency} without a "
                "dated, sourced conversion"
            )

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __mul__(self, factor) -> "Money":
        return Money(self.amount * Decimal(str(factor)), self.currency)

    __rmul__ = __mul__

    def __truediv__(self, divisor) -> "Money":
        return Money(self.amount / Decimal(str(divisor)), self.currency)

    def ratio(self, other: "Money") -> Decimal:
        self._check(other)
        if other.amount == 0:
            return Decimal("0")
        return self.amount / other.amount

    # ---- comparison ---------------------------------------------------
    def __lt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount >= other.amount

    def __eq__(self, other) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.currency == other.currency and self.amount == other.amount

    def __hash__(self) -> int:
        return hash((self.amount, self.currency))

    # ---- rounding -----------------------------------------------------
    @property
    def exponent(self) -> Decimal:
        places = MINOR_UNITS.get(self.currency, 2)
        return Decimal(1).scaleb(-places)

    def round(self) -> "Money":
        """Round to the currency's minor unit, half up. The interest convention
        in spec section 8: 'rounds interest to cents, then makes payments'."""
        return Money(self.amount.quantize(self.exponent, rounding=ROUND_HALF_UP),
                     self.currency)

    def floor(self) -> "Money":
        return Money(self.amount.quantize(self.exponent, rounding=ROUND_DOWN),
                     self.currency)

    # ---- predicates ---------------------------------------------------
    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def clamp_min_zero(self) -> "Money":
        return self if self.amount >= 0 else Money.zero(self.currency)

    def min(self, other: "Money") -> "Money":
        self._check(other)
        return self if self.amount <= other.amount else other

    def max(self, other: "Money") -> "Money":
        self._check(other)
        return self if self.amount >= other.amount else other

    # ---- presentation -------------------------------------------------
    def __str__(self) -> str:
        places = MINOR_UNITS.get(self.currency, 2)
        sign = "-" if self.amount < 0 else ""
        q = abs(self.amount).quantize(self.exponent, rounding=ROUND_HALF_UP)
        symbol = {"USD": "$", "GBP": "£", "EUR": "€", "INR": "₹"}.get(
            self.currency, self.currency + " ")
        return f"{sign}{symbol}{q:,.{places}f}"

    def __repr__(self) -> str:
        return f"Money({self.amount}, {self.currency!r})"

    def to_json(self) -> dict:
        return {"amount": str(self.round().amount), "currency": self.currency,
                "display": str(self)}


def msum(items: Iterable[Money], currency: str = "USD") -> Money:
    """Sum Money values. Unlike currencies raise rather than silently combine."""
    total = Money.zero(currency)
    for it in items:
        total = total + it
    return total


def allocate(total: Money, weights: list[Decimal]) -> list[Money]:
    """Split `total` by weights with deterministic assignment of the final cent.

    Spec section 14: "Rounding uses cents, with the final cent assigned
    deterministically." Largest-remainder method; ties go to the earliest index,
    so the same inputs always produce the same split.
    """
    if not weights:
        return []
    wsum = sum(weights)
    if wsum == 0:
        return [Money.zero(total.currency) for _ in weights]
    exp = total.exponent
    raw = [total.amount * Decimal(w) / Decimal(wsum) for w in weights]
    floored = [r.quantize(exp, rounding=ROUND_DOWN) for r in raw]
    remainder = (total.amount.quantize(exp, rounding=ROUND_HALF_UP)
                 - sum(floored))
    steps = int((remainder / exp).to_integral_value())
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - floored[i]), i))
    for k in range(steps):
        floored[order[k % len(order)]] += exp
    return [Money(f, total.currency) for f in floored]


USD = "USD"
ZERO = Money.zero(USD)
