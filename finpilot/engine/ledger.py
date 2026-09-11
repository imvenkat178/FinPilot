"""Dated cash-flow ledger and forecast -- spec section 4, PL04 and PL05.

"A combined positive balance is insufficient if money arrives in checking after
the bill deadline. The spending estimate must respect the lowest balance over
the planning horizon, not just the ending balance."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from ..models import (Account, AccountType, Confidence, Household, TxKind)
from ..money import Money, msum


@dataclass
class LedgerEntry:
    date: date
    account_id: str
    label: str
    amount: Money            # signed
    kind: str
    confirmed: bool = True

    def to_json(self) -> dict:
        return {"date": self.date.isoformat(), "account_id": self.account_id,
                "label": self.label, "amount": self.amount.to_json(),
                "kind": self.kind, "confirmed": self.confirmed}


@dataclass
class DayPoint:
    date: date
    opening: Money
    inflow: Money
    outflow: Money
    closing: Money
    entries: list[LedgerEntry] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"date": self.date.isoformat(), "opening": self.opening.to_json(),
                "inflow": self.inflow.to_json(), "outflow": self.outflow.to_json(),
                "closing": self.closing.to_json(),
                "entries": [e.to_json() for e in self.entries]}


@dataclass
class Forecast:
    account_id: str
    account_name: str
    start: date
    end: date
    days: list[DayPoint]
    currency: str = "USD"

    @property
    def low_point(self) -> DayPoint:
        return min(self.days, key=lambda d: d.closing.amount)

    @property
    def ending(self) -> Money:
        return self.days[-1].closing if self.days else Money.zero(self.currency)

    @property
    def negative_days(self) -> list[DayPoint]:
        return [d for d in self.days if d.closing.is_negative]

    def to_json(self, include_days: bool = True) -> dict:
        lp = self.low_point if self.days else None
        return {
            "account_id": self.account_id, "account_name": self.account_name,
            "start": self.start.isoformat(), "end": self.end.isoformat(),
            "ending_balance": self.ending.to_json(),
            "low_point": ({"date": lp.date.isoformat(),
                           "balance": lp.closing.to_json()} if lp else None),
            "negative_days": [d.date.isoformat() for d in self.negative_days],
            "days": [d.to_json() for d in self.days] if include_days else [],
        }


@dataclass
class SpendingAllowance:
    """PL05. A dated allowance after required commitments and protected reserves,
    computed from the LOWEST balance, not the ending balance."""
    through: date
    amount: Money
    low_point_date: date
    low_point_balance: Money
    protected: Money
    required_before: Money
    confidence: Confidence
    assumptions: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"through": self.through.isoformat(), "amount": self.amount.to_json(),
                "low_point_date": self.low_point_date.isoformat(),
                "low_point_balance": self.low_point_balance.to_json(),
                "protected": self.protected.to_json(),
                "required_before": self.required_before.to_json(),
                "confidence": self.confidence.value,
                "assumptions": self.assumptions, "missing": self.missing}


class LedgerEngine:
    def __init__(self, household: Household):
        self.hh = household
        self.cur = household.base_currency

    def entries_for(self, account_id: str, start: date, end: date) -> list[LedgerEntry]:
        out: list[LedgerEntry] = []
        acct = self.hh.accounts.get(account_id)
        if acct is None:
            return out

        # income landing in this account
        for ev in self.hh.income_events:
            d = ev.received_date or ev.expected_date
            src = self.hh.income_sources.get(ev.source_id)
            target = src.deposit_account_id if src else account_id
            if target == account_id and start <= d <= end:
                out.append(LedgerEntry(d, account_id,
                                       src.name if src else "Income",
                                       ev.effective_amount, "income",
                                       confirmed=ev.is_received))

        # bills funded from this account
        for b in self.hh.bills.values():
            if b.funding_account_id != account_id:
                continue
            dates = self.hh.bill_dates(b, start, end)
            for d in dates:
                remaining = self.hh.bill_occurrence(b, d).cash_remaining
                if remaining.is_zero:
                    continue
                label = f"{b.name} (overdue {d.isoformat()})" if d < start else b.name
                out.append(LedgerEntry(max(d, start), account_id, label, -remaining,
                                       "bill" if b.required else "optional_bill",
                                       confirmed=b.amount_confirmed))

        # pending transactions not yet reflected in available
        for tx in self.hh.transactions:
            if tx.account_id == account_id and tx.state.value == "pending" \
                    and not tx.balance_already_reflected and start <= tx.date <= end:
                out.append(LedgerEntry(tx.date, account_id,
                                       tx.description or tx.merchant,
                                       tx.amount, "pending", confirmed=False))
        return sorted(out, key=lambda e: (e.date, e.label))

    def forecast(self, account_id: str, start: Optional[date] = None,
                 days: int = 45, extra: Optional[list[LedgerEntry]] = None
                 ) -> Forecast:
        acct = self.hh.accounts[account_id]
        start = start or self.hh.as_of
        end = start + timedelta(days=days)
        entries = self.entries_for(account_id, start, end) + list(extra or [])
        by_day: dict[date, list[LedgerEntry]] = {}
        for e in entries:
            by_day.setdefault(e.date, []).append(e)

        balance = acct.available
        points: list[DayPoint] = []
        d = start
        while d <= end:
            todays = by_day.get(d, [])
            inflow = msum([e.amount for e in todays if e.amount.is_positive], self.cur)
            outflow = msum([-e.amount for e in todays if e.amount.is_negative], self.cur)
            opening = balance
            balance = balance + inflow - outflow
            points.append(DayPoint(d, opening, inflow, outflow, balance, todays))
            d += timedelta(days=1)
        return Forecast(account_id, acct.nickname, start, end, points, self.cur)

    def household_forecast(self, start: Optional[date] = None, days: int = 45
                           ) -> list[Forecast]:
        return [self.forecast(a.id, start, days) for a in self.hh.cash_accounts
                if a.included_in_planning]

    def spending_allowance(self, account_id: str, through_days: int = 14,
                           protected: Optional[Money] = None) -> SpendingAllowance:
        fc = self.forecast(account_id, days=through_days)
        prot = protected or Money.zero(self.cur)
        for r in self.hh.reserves.values():
            if r.account_id == account_id and r.protected:
                prot = prot + r.funded
        lp = fc.low_point
        required_before = msum(
            [-e.amount for d in fc.days for e in d.entries
             if e.amount.is_negative and e.kind == "bill"], self.cur)
        allowance = (lp.closing - prot).clamp_min_zero()

        missing: list[str] = []
        conf = Confidence.EXACT
        acct = self.hh.accounts[account_id]
        if not acct.connection_healthy:
            missing.append(f"{acct.nickname} has not refreshed recently")
            conf = Confidence.BOUNDED
        unconfirmed = [e for d in fc.days for e in d.entries if not e.confirmed]
        if unconfirmed:
            conf = Confidence.BOUNDED if conf == Confidence.EXACT else conf
            missing.append(f"{len(unconfirmed)} forecast items are predicted rather "
                           "than confirmed")
        return SpendingAllowance(
            through=fc.end, amount=allowance, low_point_date=lp.date,
            low_point_balance=lp.closing, protected=prot,
            required_before=required_before, confidence=conf,
            assumptions=[
                "Computed from the lowest projected balance in the window, not the "
                "ending balance. A positive ending balance does not make a bill "
                "payable on the day it is due.",
                "Credit limits, home equity, retirement holdings and pending "
                "refunds are excluded: they are not ordinary bill-paying cash.",
            ],
            missing=missing)
