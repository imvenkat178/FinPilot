"""Read-only presentation data for the financial workspace.

This is an explicit allowlist, rather than a serialization of the household.
Adding a credential or an internal field to a domain object must never make it
appear in the browser. Calculated financial results remain in the existing
calculator endpoints; the projection supplies their source records and links.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from heapq import nlargest
from typing import Any

from ..dates import Schedule
from ..engine.tax import TaxProfile
from ..models import (
    Account, BalanceBucket, Bill, Card, CardBenefit, Household, IncomeEvent,
    IncomeSource, Liability, Mandate, PolicyPurpose, Provenance, RecurringPolicy,
    Reserve, RewardRule, Transaction,
)
from ..money import Money


# Names follow the domain model so balances retain their distinct meanings.
_FIELDS: dict[type, tuple[str, ...]] = {
    Account: (
        "id", "nickname", "type", "entity_id", "institution", "institution_id",
        "mask", "currency", "current", "available", "pending", "reserved", "held",
        "apy", "rate_tiers", "minimum_balance", "monthly_fee",
        "withdrawal_limit_per_month", "liquidity_tier", "protection",
        "ownership_category", "capabilities", "included_in_planning",
        "connection_healthy", "connection_issue", "last_synced_at", "provenance",
        "sweep_allocations", "spendable",
    ),
    Bill: (
        "id", "name", "amount", "due_date", "schedule", "funding_account_id",
        "payee_account_id", "required", "category", "execution_owner",
        "amount_confirmed", "autopay_confirmed", "provenance", "installment_provider",
    ),
    Reserve: (
        "id", "name", "account_id", "purpose", "target", "funded", "target_date",
        "protected", "entity_id", "remaining",
    ),
    Liability: (
        "id", "account_id", "name", "type", "balance", "apr", "rate_type",
        "minimum_payment", "due_day", "remaining_term_months", "buckets", "escrow",
        "mortgage_insurance", "prepayment_penalty", "servicer_principal_only_supported",
        "student_loan_program", "hardship_plan", "entity_id", "tax_deductible_interest",
        "provenance", "total_required_payment", "terms_complete",
    ),
    BalanceBucket: (
        "name", "balance", "apr", "rate_type", "promo_expires", "deferred_interest_accrued",
    ),
    Card: (
        "id", "account_id", "nickname", "product", "variant", "mask", "issuer",
        "purchase_apr", "credit_limit", "statement_balance", "current_balance",
        "statement_close_day", "payment_due_day", "grace_state", "annual_fee",
        "annual_fee_month", "foreign_transaction_fee", "reward_currency", "point_value",
        "rules", "benefits", "excluded_by_user", "entity_id", "utilization",
    ),
    RewardRule: (
        "id", "card_id", "category", "rate", "cap_amount", "cap_period", "cap_used",
        "requires_activation", "activated", "excluded_merchants", "excluded_channels",
        "effective_from", "effective_to", "rule_version", "provenance",
    ),
    CardBenefit: (
        "name", "kind", "value", "remaining", "period", "expires", "conditions", "enrolled",
    ),
    RecurringPolicy: (
        "id", "name", "purpose", "method", "amount", "percent", "percent_base",
        "target_balance", "target_date", "monthly_target", "source_account_id",
        "destination_account_id", "destination_reserve_id", "liability_id", "bill_id", "schedule",
        "cadence", "eligible_income_source_ids", "priority", "min_remaining_balance",
        "allow_overfunding", "entity_id", "mandate", "paused", "skip_next", "fee_limit",
        "funded_this_period", "is_required", "is_protected_reserve",
    ),
    Mandate: (
        "mode", "active", "per_run_cap", "period_cap", "notice_days", "jurisdiction",
    ),
    IncomeSource: (
        "id", "name", "net_amount", "schedule", "deposit_account_id", "reliability",
        "is_variable", "entity_id", "pretax_deductions", "employer_match_formula",
    ),
    IncomeEvent: (
        "id", "source_id", "expected_date", "expected_amount", "received_date",
        "received_amount", "matched_tx_id", "is_received", "effective_amount",
    ),
    Transaction: (
        "id", "account_id", "date", "amount", "description", "merchant", "mcc",
        "category", "kind", "state", "transfer_group_id", "linked_tx_id",
        "user_corrected", "counts_as_income", "counts_as_spending", "provider_category",
    ),
    Provenance: ("source", "as_of", "verification", "rule_version"),
    Schedule: (
        "cadence", "anchor", "day_of_month", "second_day_of_month", "day_rule",
        "business_day_rule", "end",
    ),
}


def _encode(value: Any) -> Any:
    # Money is a dataclass, but must use its currency-aware decimal formatter.
    if isinstance(value, Money):
        return value.to_json()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if type(value) in _FIELDS:
        return {name: _encode(getattr(value, name)) for name in _FIELDS[type(value)]}
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, set):
        return sorted(_encode(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"No workspace projection for {type(value).__name__}")


def _bill_policy_ids(hh: Household, bill: Bill) -> list[str]:
    """Display related required rules without treating extra debt as a bill rule.

    An explicit bill binding wins. Legacy display associations use debt account
    links, or the same name and funding account for external bills. These are
    presentation associations only and never grant payment authority.
    An unmatched bill stays unmatched instead of manufacturing a policy ID.
    """
    related = []
    for policy in hh.policies.values():
        if not policy.is_required or policy.source_account_id != bill.funding_account_id:
            continue
        bound_bill = getattr(policy, "bill_id", None)
        if bound_bill:
            if bound_bill == bill.id:
                related.append(policy.id)
            continue
        if bill.payee_account_id:
            liability = hh.liabilities.get(policy.liability_id)
            if (policy.destination_account_id == bill.payee_account_id
                    or (liability and liability.account_id == bill.payee_account_id)):
                related.append(policy.id)
        elif (policy.purpose == PolicyPurpose.BILL
              and policy.name.casefold().strip() == bill.name.casefold().strip()):
            related.append(policy.id)
    return related


def workspace_data(hh: Household, tax: TaxProfile, *, transaction_limit: int | None = None) -> dict:
    """Project the selected household without changing any planning state."""
    accounts = []
    for account in hh.accounts.values():
        row = _encode(account)
        owner_ids = account.co_owners or ([account.entity_id] if account.entity_id else [])
        owners = [hh.entities[owner_id] for owner_id in owner_ids if owner_id in hh.entities]
        row["owner_display"] = ", ".join(owner.name for owner in owners) or "Owner not provided"
        entity = hh.entities.get(account.entity_id)
        row["owner_type"] = entity.owner_type.value if entity else None
        accounts.append(row)

    bills = []
    for bill in hh.bills.values():
        row = _encode(bill)
        row["policy_ids"] = _bill_policy_ids(hh, bill)
        bills.append(row)

    transactions = hh.transactions if transaction_limit is None else nlargest(
        transaction_limit, hh.transactions, key=lambda tx: (tx.date, tx.id))
    return {
        "household": {
            "id": hh.id, "name": hh.name, "base_currency": hh.base_currency,
            "jurisdiction": hh.jurisdiction, "as_of": hh.as_of.isoformat(),
            "payment_sandbox": getattr(hh, "payment_sandbox", False),
        },
        "accounts": accounts,
        "bills": bills,
        "reserves": [_encode(item) for item in hh.reserves.values()],
        "liabilities": [_encode(item) for item in hh.liabilities.values()],
        "cards": [_encode(item) for item in hh.cards.values()],
        "policies": [_encode(item) for item in hh.policies_sorted()],
        "income_sources": [_encode(item) for item in hh.income_sources.values()],
        "income_events": [_encode(item) for item in hh.income_events],
        "transactions": [_encode(item) for item in transactions],
        "tax": tax.to_json(),
    }
