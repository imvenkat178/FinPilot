"""Recurring rule activity.

Spec table 7 (Screens), "Recurring rules and activity": "Monthly targets,
paycheck triggers, standing mandates, upcoming runs, dependencies, pause and
skip actions, receipts, and unresolved payments."

The allocator answers "how much, in total, this month." This module answers
the other half of that screen: for each standing policy, which dated
occurrences fall inside the planning horizon, and -- for each one -- will it
actually run, or is something (pause, a one-time skip, a missing
authorization) about to silently stop it. Section 21's authorization table
already carries the machinery (`Mandate.active`); this only makes its
consequence visible before the date arrives rather than after a run failed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from ..models import Household, RecurringPolicy


@dataclass
class UpcomingRun:
    policy_id: str
    name: str
    purpose: str
    date: date
    amount_note: str
    will_run: bool
    reason_if_not: str = ""

    def to_json(self) -> dict:
        return {"policy_id": self.policy_id, "name": self.name,
                "purpose": self.purpose, "date": self.date.isoformat(),
                "amount_note": self.amount_note, "will_run": self.will_run,
                "reason_if_not": self.reason_if_not}


class RecurringActivityEngine:
    def __init__(self, household: Household):
        self.hh = household

    def _will_run(self, p: RecurringPolicy, occurrence_date: date,
                  is_next_occurrence: bool) -> tuple[bool, str]:
        if p.paused:
            return False, "policy is paused"
        if p.skip_next and is_next_occurrence:
            return False, "user chose to skip the next occurrence"
        if not p.mandate.active:
            return False, "no active authorization for this policy"
        if p.mandate.mode.value == "explore":
            return False, "authorization is explore-only; it cannot move money"
        return True, ""

    def _trigger_dates(self, p: RecurringPolicy, start: date, until: date) -> list[date]:
        """A policy's own `schedule` is calendar-driven (e.g. a fixed monthly
        transfer date). A `cadence` of ON_INCOME instead runs whenever an
        eligible paycheck lands -- so its trigger dates come from the income
        source(s) it is funded from, the same source the allocator itself
        reads (`month_income_dates`)."""
        if p.schedule:
            return p.schedule.occurrences(start, until)
        if p.cadence.value == "on_income":
            dates: set[date] = set()
            for sid in (p.eligible_income_source_ids or list(self.hh.income_sources)):
                src = self.hh.income_sources.get(sid)
                if src and src.schedule:
                    dates.update(src.schedule.occurrences(start, until))
            # dated events already on the books (e.g. this month's confirmed
            # paycheck) take precedence over the schedule's generic guess
            for ev in self.hh.income_events:
                d = ev.received_date or ev.expected_date
                if start <= d <= until and ev.source_id in (
                        p.eligible_income_source_ids or [ev.source_id]):
                    dates.add(d)
            return sorted(dates)
        return []

    def upcoming_runs(self, horizon_days: int = 60,
                      as_of: Optional[date] = None) -> list[UpcomingRun]:
        as_of = as_of or self.hh.as_of
        until = as_of + timedelta(days=horizon_days)
        runs: list[UpcomingRun] = []
        for p in self.hh.policies_sorted():
            dates = self._trigger_dates(p, as_of, until)
            for i, d in enumerate(dates):
                will_run, reason = self._will_run(p, d, is_next_occurrence=(i == 0))
                note = str(p.monthly_target or p.amount)
                if p.percent:
                    note = f"{p.percent}% of {p.percent_base.value}"
                runs.append(UpcomingRun(
                    policy_id=p.id, name=p.name, purpose=p.purpose.value,
                    date=d, amount_note=note, will_run=will_run,
                    reason_if_not=reason))
        return sorted(runs, key=lambda r: (r.date, r.name))

    def unresolved(self) -> list[dict]:
        """Policies with a standing schedule but nothing that would let them
        actually run -- the 'unresolved payments' half of the screen, caught
        before a paycheck arrives rather than reported as a failure after."""
        out = []
        for p in self.hh.policies_sorted():
            has_trigger = bool(p.schedule) or p.cadence.value == "on_income"
            if p.paused or not has_trigger:
                continue
            if not p.mandate.active:
                out.append({"policy_id": p.id, "name": p.name,
                           "issue": "no active authorization"})
            elif p.mandate.mode.value == "explore":
                out.append({"policy_id": p.id, "name": p.name,
                           "issue": "authorization is explore-only"})
        return out
