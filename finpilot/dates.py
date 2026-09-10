"""Date, cadence and settlement-timing primitives.

Spec section 13: "Dates retain the user's timezone, contractual due-date
semantics, and business-day or settlement assumptions."

Spec section 14 requires: weekly, every two weeks, twice monthly, monthly,
quarterly, annually, on a verified paycheck deposit, or after another transfer
becomes available -- with "a documented rule for a requested day that does not
exist in that month" and last-business-day support.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Iterator


class Cadence(str, Enum):
    ONCE = "once"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"          # every 14 days
    SEMIMONTHLY = "semimonthly"    # twice a month, e.g. 1st and 15th
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUALLY = "annually"
    ON_INCOME = "on_income"        # triggered by a matched deposit
    ON_DEPENDENCY = "on_dependency"  # after another leg becomes available


class DayRule(str, Enum):
    """What to do when a requested day does not exist in a month."""
    LAST_DAY_OF_MONTH = "last_day_of_month"   # 31st in February -> 28th/29th
    SKIP = "skip"
    FIRST_OF_NEXT = "first_of_next"


class BusinessDayRule(str, Enum):
    NONE = "none"
    PRECEDING = "preceding"        # move earlier -- the safe choice for paying
    FOLLOWING = "following"
    MODIFIED_FOLLOWING = "modified_following"


# US federal-holiday approximation for the settlement calendar. Production
# needs a maintained per-provider, per-rail calendar (spec section 16); this is
# enough to make the timing engine behave correctly in tests and demos.
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    delta = (weekday - d.weekday()) % 7
    return d + timedelta(days=delta + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def us_holidays(year: int) -> set[date]:
    h = {
        date(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),      # MLK
        _nth_weekday(year, 2, 0, 3),      # Presidents
        _last_weekday(year, 5, 0),        # Memorial
        date(year, 6, 19),                # Juneteenth
        date(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),      # Labor
        _nth_weekday(year, 10, 0, 2),     # Columbus
        date(year, 11, 11),               # Veterans
        _nth_weekday(year, 11, 3, 4),     # Thanksgiving
        date(year, 12, 25),
    }
    # observed shifts
    out = set()
    for d in h:
        if d.weekday() == 5:
            out.add(d - timedelta(days=1))
        elif d.weekday() == 6:
            out.add(d + timedelta(days=1))
        else:
            out.add(d)
    return out


class Calendar:
    """Business-day calendar for one jurisdiction. GX02: jurisdiction is a
    dimension on every rule object, calendars included."""

    def __init__(self, jurisdiction: str = "US"):
        self.jurisdiction = jurisdiction
        self._cache: dict[int, set[date]] = {}

    def holidays(self, year: int) -> set[date]:
        if year not in self._cache:
            self._cache[year] = us_holidays(year) if self.jurisdiction == "US" else set()
        return self._cache[year]

    def is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays(d.year)

    def adjust(self, d: date, rule: BusinessDayRule) -> date:
        if rule == BusinessDayRule.NONE:
            return d
        if rule == BusinessDayRule.PRECEDING:
            while not self.is_business_day(d):
                d -= timedelta(days=1)
            return d
        if rule in (BusinessDayRule.FOLLOWING, BusinessDayRule.MODIFIED_FOLLOWING):
            start_month = d.month
            nxt = d
            while not self.is_business_day(nxt):
                nxt += timedelta(days=1)
            if rule == BusinessDayRule.MODIFIED_FOLLOWING and nxt.month != start_month:
                return self.adjust(d, BusinessDayRule.PRECEDING)
            return nxt
        return d

    def add_business_days(self, d: date, n: int) -> date:
        step = 1 if n >= 0 else -1
        remaining = abs(n)
        cur = d
        while remaining:
            cur += timedelta(days=step)
            if self.is_business_day(cur):
                remaining -= 1
        return cur

    def subtract_business_days(self, d: date, n: int) -> date:
        return self.add_business_days(d, -n)

    def last_business_day(self, year: int, month: int) -> date:
        d = date(year, month, calendar.monthrange(year, month)[1])
        return self.adjust(d, BusinessDayRule.PRECEDING)


DEFAULT_CALENDAR = Calendar("US")


def day_in_month(year: int, month: int, day: int, rule: DayRule) -> date | None:
    """Resolve a requested day-of-month, applying the documented rule when the
    day does not exist (spec section 14)."""
    last = calendar.monthrange(year, month)[1]
    if day <= last:
        return date(year, month, day)
    if rule == DayRule.LAST_DAY_OF_MONTH:
        return date(year, month, last)
    if rule == DayRule.SKIP:
        return None
    nxt_y, nxt_m = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(nxt_y, nxt_m, 1)


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


@dataclass
class Schedule:
    """A dated occurrence generator for a policy, bill or income stream."""
    cadence: Cadence
    anchor: date
    day_of_month: int | None = None
    second_day_of_month: int | None = None   # semimonthly
    day_rule: DayRule = DayRule.LAST_DAY_OF_MONTH
    business_day_rule: BusinessDayRule = BusinessDayRule.NONE
    end: date | None = None
    calendar_: Calendar = field(default=DEFAULT_CALENDAR)

    def occurrences(self, start: date, until: date) -> list[date]:
        return list(self._iter(start, until))

    def _iter(self, start: date, until: date) -> Iterator[date]:
        if self.cadence in (Cadence.ON_INCOME, Cadence.ON_DEPENDENCY):
            return                     # driven by events, not the calendar
        if self.cadence == Cadence.ONCE:
            if start <= self.anchor <= until:
                yield self._adj(self.anchor)
            return

        if self.cadence in (Cadence.WEEKLY, Cadence.BIWEEKLY):
            step = 7 if self.cadence == Cadence.WEEKLY else 14
            cur = self.anchor
            while cur < start:
                cur += timedelta(days=step)
            while cur <= until and (self.end is None or cur <= self.end):
                yield self._adj(cur)
                cur += timedelta(days=step)
            return

        if self.cadence == Cadence.SEMIMONTHLY:
            d1 = self.day_of_month or 1
            d2 = self.second_day_of_month or 15
            y, m = start.year, start.month
            while date(y, m, 1) <= until:
                for dd in sorted({d1, d2}):
                    occ = day_in_month(y, m, dd, self.day_rule)
                    if occ and start <= occ <= until and (self.end is None or occ <= self.end):
                        yield self._adj(occ)
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            return

        step_months = {Cadence.MONTHLY: 1, Cadence.QUARTERLY: 3,
                       Cadence.ANNUALLY: 12}[self.cadence]
        dd = self.day_of_month or self.anchor.day
        y, m = self.anchor.year, self.anchor.month
        # wind forward to the window
        while True:
            occ = day_in_month(y, m, dd, self.day_rule)
            if occ is None or occ >= start:
                break
            y_m = m - 1 + step_months
            y, m = y + y_m // 12, y_m % 12 + 1
        while True:
            occ = day_in_month(y, m, dd, self.day_rule)
            if occ is not None:
                if occ > until or (self.end and occ > self.end):
                    return
                if occ >= start:
                    yield self._adj(occ)
            y_m = m - 1 + step_months
            y, m = y + y_m // 12, y_m % 12 + 1
            if date(y, m, 1) > until + timedelta(days=400):
                return

    def _adj(self, d: date) -> date:
        return self.calendar_.adjust(d, self.business_day_rule)


def occurrences_per_year(cadence: Cadence) -> int:
    return {Cadence.WEEKLY: 52, Cadence.BIWEEKLY: 26, Cadence.SEMIMONTHLY: 24,
            Cadence.MONTHLY: 12, Cadence.QUARTERLY: 4, Cadence.ANNUALLY: 1,
            Cadence.ONCE: 1}.get(cadence, 12)
