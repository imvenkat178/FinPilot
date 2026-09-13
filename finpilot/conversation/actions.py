"""Command handlers shared by previews and commits; no model calls or bank I/O."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from ..api.workspace import workspace_data
from ..engine.allocator import AllocationEngine
from ..models import AuthorizationMode, Mandate
from ..money import Money
from ..runtime import WorkspaceContext
from ..services.workspace import WorkspaceService
from .contracts import ActionBatch


def checked(result):
    if "error" in result:
        raise ValueError(result["error"])
    return result


def apply_operation(ctx, operation, actor_id, *, preview=False):
    hh, ex = ctx.household, ctx.execution
    service = WorkspaceService(hh, ctx.tax)
    o = operation
    if o.op == "upsert":
        return service.upsert(o.collection, o.values, o.record_id)
    if o.op == "edit_transaction":
        return service.update_transaction(o.transaction_id, {"category": o.category})
    if o.op == "pause_policy":
        return checked(ctx.registry.pause_recurring_policy(o.policy_id, o.paused))
    if o.op == "skip_policy":
        return checked(ctx.registry.skip_next_occurrence(o.policy_id))
    if o.op == "authorize_policy":
        if not hh.payment_sandbox:
            raise ValueError("Payment authorization is currently available only in sample workspaces; live payment integration is not configured")
        policy = hh.policies[o.policy_id]
        cap = Money(Decimal(o.per_run_cap), hh.base_currency)
        if not cap.is_positive or cap.amount > Decimal("1000000000"):
            raise ValueError("Enter a positive authorization cap up to 1 billion")
        policy.mandate = Mandate(entity_id=policy.entity_id, jurisdiction=hh.jurisdiction,
            mode=AuthorizationMode(o.mode), per_run_cap=cap,
            authorized_by=actor_id, authorized_at=datetime.now(timezone.utc))
        return {"policy_id": policy.id, "name": policy.name, "mode": o.mode,
                "per_run_cap": cap.to_json(), "simulation_only": True}
    if o.op == "draft_bill":
        return checked(ctx.registry.pay_bill_once(o.bill_id,
            o.occurrence_date.isoformat() if o.occurrence_date else None))
    if o.op == "build_paycheck":
        _, runs = AllocationEngine(hh).allocate_month(o.year, o.month)
        if o.income_event_id:
            runs = [run for run in runs if run.income_event_id == o.income_event_id]
            if not runs:
                raise ValueError("Paycheck not found in this month")
        if not runs:
            raise ValueError("No paycheck is scheduled for this month; add an income source first")
        return {"groups": [ex.build_group_from_allocation(run).to_json() for run in runs],
                "note": "Payment drafts only. No money submitted."}
    if o.op == "simulate_group":
        if not hh.payment_sandbox:
            raise ValueError("Simulated payments are available only in sample workspaces; live transfers are not configured")
        group = ex.groups[o.group_id]
        if preview:
            return {"group": group.to_json(), "simulation_only": True,
                    "preflight": [ex.preflight(leg).to_json() for leg in group.legs]}
        return {**ex.run_group(group, settle=True), "simulation_only": True}
    if o.op == "pause_all":
        return ex.pause_all()
    if o.op == "resume_all":
        ex.paused = False
        return {"globally_paused": False, "note": "Future execution resumed; canceled drafts remain canceled."}
    raise ValueError("Unsupported financial command")


def public_record(ctx, collection, record_id):
    data = workspace_data(ctx.household, ctx.tax)
    if collection == "tax":
        return data["tax"]
    key = "income_sources" if collection == "income" else collection
    return next((r for r in data[key] if r["id"] == record_id), {})


def account_labels(hh):
    return {a.id: f"{a.nickname} · {a.institution} · ending {a.mask}"
            for a in hh.accounts.values()}


def prepare_actions(ctx, batch: ActionBatch, actor_id):
    # A fresh decoded snapshot preserves shared mandates without attempting to
    # deepcopy engine locks. Preview changes can never reach authoritative data.
    draft = WorkspaceContext.from_row(SimpleNamespace(snapshot=ctx.snapshot(), revision=ctx.revision))
    rows, normalized = [], []
    labels = account_labels(draft.household)
    for o in batch.operations:
        before = public_record(draft, o.collection, o.record_id) if o.op == "upsert" else None
        result = apply_operation(draft, o, actor_id, preview=True)
        if o.op == "draft_bill" and o.occurrence_date is None:
            o = o.model_copy(update={"occurrence_date": date.fromisoformat(result["due_date"])})
        normalized.append(o)
        row = {"operation": o.op, "request": o.model_dump(mode="json"), "result": result}
        if o.op == "upsert":
            after = public_record(draft, o.collection, result["id"])
            row.update(title=("Update " if o.record_id else "Create ") +
                       (after.get("nickname") or after.get("name") or o.collection),
                       before=before, after=after)
            if o.collection == "policies":
                row["note"] = ("Allocation amounts and income percentages define monthly targets, funded across paychecks by deadlines and priorities. "
                               "Saving a rule does not authorize a payment. Material changes require fresh payment authorization.")
        else:
            row["title"] = o.op.replace("_", " ").capitalize()
        # Labels resolve owned IDs while preserving the precise request.
        row["accounts"] = {v: labels[v] for v in labels if v in str(o.model_dump())}
        rows.append(row)
    return ActionBatch(operations=normalized), rows
