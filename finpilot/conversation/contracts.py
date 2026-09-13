"""Versioned, allowlisted conversation contracts. No model-generated code or HTML."""
from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Contract):
    kind: Literal["household", "account", "platform"] = "household"
    account_id: str | None = Field(None, max_length=100)
    platform: str | None = Field(None, min_length=1, max_length=150)

    @model_validator(mode="after")
    def consistent(self):
        if self.kind == "account" and not self.account_id:
            raise ValueError("Choose an account")
        if self.kind == "platform" and not self.platform:
            raise ValueError("Choose a platform")
        if (self.kind != "account" and self.account_id) or (self.kind != "platform" and self.platform):
            raise ValueError("Scope contains conflicting selectors")
        return self


class Upsert(Contract):
    op: Literal["upsert"] = "upsert"
    collection: Literal["accounts", "income", "bills", "reserves", "policies", "tax"]
    record_id: str | None = Field(None, max_length=100)
    values: dict = Field(max_length=35)

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.values)) > 12000:
            raise ValueError("Too many record details")
        return self


class PausePolicy(Contract):
    op: Literal["pause_policy"] = "pause_policy"
    policy_id: str = Field(min_length=1, max_length=100)
    paused: StrictBool = True


class SkipPolicy(Contract):
    op: Literal["skip_policy"] = "skip_policy"
    policy_id: str = Field(min_length=1, max_length=100)


class AuthorizePolicy(Contract):
    op: Literal["authorize_policy"] = "authorize_policy"
    policy_id: str = Field(min_length=1, max_length=100)
    per_run_cap: str = Field(pattern=r"^[0-9]{1,10}(\.[0-9]{1,2})?$")
    mode: Literal["one_time", "scheduled", "standing"] = "one_time"


class DraftBill(Contract):
    op: Literal["draft_bill"] = "draft_bill"
    bill_id: str = Field(min_length=1, max_length=100)
    occurrence_date: date | None = None


class BuildPaycheck(Contract):
    op: Literal["build_paycheck"] = "build_paycheck"
    year: int = Field(ge=1900, le=2200)
    month: int = Field(ge=1, le=12)
    income_event_id: str | None = Field(None, max_length=100)


class SimulateGroup(Contract):
    op: Literal["simulate_group"] = "simulate_group"
    group_id: str = Field(min_length=1, max_length=100)


class PauseAll(Contract):
    op: Literal["pause_all", "resume_all"]


class EditTransaction(Contract):
    op: Literal["edit_transaction"] = "edit_transaction"
    transaction_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)


Operation = Annotated[Upsert | PausePolicy | SkipPolicy | AuthorizePolicy | DraftBill |
                      BuildPaycheck | SimulateGroup | PauseAll | EditTransaction,
                      Field(discriminator="op")]


class ActionBatch(Contract):
    operations: list[Operation] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def separate_execution(self):
        # Each authorization/execution deserves its own review. In particular,
        # no model can smuggle a mandate or payment into a paycheck-edit batch.
        if len(self.operations) > 1 and any(o.op in {"authorize_policy", "simulate_group"}
                                             for o in self.operations):
            raise ValueError("Review each authorization or simulated payment separately")
        return self


class ReadQuery(Contract):
    tool: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict, max_length=15)
    presentation: Literal["auto", "table", "chart"] = "auto"


class Plan(Contract):
    kind: Literal["read", "action", "clarify", "capability", "form"]
    query: ReadQuery | None = None
    action: ActionBatch | None = None
    scope: Scope | None = None
    clarification: str = Field("", max_length=600)
    capability: Literal["connect_bank", "help", "live_payments"] | None = None
    form_collection: Literal["accounts", "income", "bills", "reserves", "policies", "tax"] | None = None
    record_id: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def consistent(self):
        if (self.kind == "read") != (self.query is not None):
            raise ValueError("A read plan requires exactly one query")
        if (self.kind == "action") != (self.action is not None):
            raise ValueError("An action plan requires exactly one batch")
        if self.kind == "clarify" and not self.clarification:
            raise ValueError("Ask for the missing details")
        if self.kind == "capability" and not self.capability:
            raise ValueError("Select an available capability")
        if self.kind == "form" and not self.form_collection:
            raise ValueError("Select a record type")
        return self


class NewConversation(Contract):
    title: str = Field("New conversation", min_length=1, max_length=120)
    scope: Scope = Field(default_factory=Scope)


class MessageIn(Contract):
    client_message_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    question: str = Field(min_length=1, max_length=3000)
    scope: Scope | None = None
    conversation_revision: int | None = Field(None, ge=0)


class ConfirmIn(Contract):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: Literal[True]
    confirm_simulation: StrictBool = False
