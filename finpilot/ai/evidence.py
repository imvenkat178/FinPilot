"""Evidence links and a deterministic evidence-confidence rubric for assistant answers.

Record references are taken from the structured results behind an answer and kept only when
the ID exists in the household, so a link can never point at an invented record. Confidence is
a documented rubric over those records and the calculation's own confidence, not a model's
opinion of itself.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from jsonschema import Draft202012Validator

from .facts import figure_sources, tokenize

REF_KEYS = {"account_id": "account", "funding_account_id": "account", "source_account_id": "account",
            "destination_account_id": "account", "bill_id": "bill", "card_id": "card",
            "liability_id": "liability", "policy_id": "policy", "reserve_id": "reserve",
            "transaction_id": "transaction", "income_source_id": "income"}
LIST_REF_KEYS = {"account_ids": "account", "bill_ids": "bill", "card_ids": "card", "liability_ids": "liability",
                 "policy_ids": "policy", "reserve_ids": "reserve", "transaction_ids": "transaction",
                 "income_source_ids": "income"}
ENGINE_DEDUCTIONS = {"bounded": 15, "approximate": 15, "directional": 30, "insufficient": 50}
NO_INSIGHT_INTENTS = {"boundary", "out_of_domain", "memory_saved", "unknown"}


def _index(household) -> dict[str, str]:
    index: dict[str, str] = {}
    for kind, collection in (("account", household.accounts), ("bill", household.bills),
                             ("liability", household.liabilities), ("card", household.cards),
                             ("policy", household.policies), ("reserve", household.reserves),
                             ("income", household.income_sources)):
        for record_id in collection:
            index.setdefault(record_id, kind)
    for transaction in household.transactions:
        index.setdefault(transaction.id, "transaction")
    return index


def _label(kind: str, record) -> tuple[str, Optional[str]]:
    if kind == "account":
        return record.nickname + (f" ····{record.mask}" if record.mask else ""), record.id
    if kind == "transaction":
        text = record.description or record.merchant or "Transaction"
        return f"{record.date.isoformat()} · {text} · {record.amount}", record.account_id
    if kind == "bill":
        return record.name, record.funding_account_id
    if kind == "card":
        return record.nickname or record.product or "Card", record.account_id
    if kind in ("liability", "reserve"):
        return record.name, record.account_id
    if kind == "policy":
        return record.name, record.destination_account_id
    return getattr(record, "name", ""), None


def record_refs(results: Iterable[Any], household, limit: int = 20) -> list[dict]:
    """Links to the household records that the structured results behind an answer mention."""
    index = _index(household)
    tables = {"account": household.accounts, "bill": household.bills, "liability": household.liabilities,
              "card": household.cards, "policy": household.policies, "reserve": household.reserves,
              "income": household.income_sources}
    transactions: Optional[dict] = None
    seen, refs = set(), []

    def add(expected: Optional[str], value: Any) -> None:
        nonlocal transactions
        if len(refs) >= limit or not isinstance(value, str) or not value:
            return
        kind = index.get(value)
        if kind is None or (expected and kind != expected) or (kind, value) in seen:
            return
        seen.add((kind, value))
        if kind == "transaction":
            if transactions is None:
                transactions = {tx.id: tx for tx in household.transactions}
            record = transactions[value]
        else:
            record = tables[kind][value]
        label, account_id = _label(kind, record)
        refs.append({"kind": kind, "id": value, "label": label[:160], "account_id": account_id})

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in REF_KEYS:
                    add(REF_KEYS[key], value)
                elif key in LIST_REF_KEYS and isinstance(value, list):
                    for item in value:
                        add(LIST_REF_KEYS[key], item)
                elif key == "id":
                    add(None, value)
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for result in results or []:
        walk(result)
    return refs


def evidence_confidence(result: dict, household, refs: list[dict]) -> Optional[dict]:
    """High, medium or low, with a 0-100 score and the reasons each deduction was made."""
    intent = result.get("intent")
    evidence = [item for item in (result.get("evidence") or []) if isinstance(item, dict)]
    if intent in NO_INSIGHT_INTENTS or (not evidence and not result.get("document_sources")):
        return None
    if any(item.get("error") for item in evidence):
        return {"level": "low", "score": 20,
                "reasons": ["A calculation could not run with the data available."]}
    score, reasons = 100, []
    if intent == "document_retrieval":
        score -= 30
        reasons.append("Document excerpts are quoted source statements, not verified account data.")
    elif intent == "memory_retrieval":
        score -= 40
        reasons.append("The answer repeats what was said earlier in this conversation.")
    engine = str(result.get("confidence") or "").lower()
    if engine in ENGINE_DEDUCTIONS:
        score -= ENGINE_DEDUCTIONS[engine]
        reasons.append(f"The calculation reports {engine} confidence.")
    missing = [note for item in evidence for note in (item.get("missing") or []) if isinstance(note, str)]
    if missing:
        score -= 15
        reasons.append("Some inputs still need confirming: " + "; ".join(missing[:3]) + ".")
    account_ids = {ref["account_id"] for ref in refs if ref.get("account_id")}
    accounts = [household.accounts[aid] for aid in sorted(account_ids) if aid in household.accounts]
    stale = [a.nickname for a in accounts if a.provenance.verification.value == "stale" or not a.connection_healthy]
    if stale:
        score -= 25
        reasons.append("Data for " + ", ".join(stale[:3]) + " is stale or its connection needs attention.")
    estimated = [a.nickname for a in accounts if a.provenance.verification.value == "estimated"]
    if estimated:
        score -= 15
        reasons.append("Values for " + ", ".join(estimated[:3]) + " are estimates.")
    if any(a.provenance.verification.value == "unknown" for a in accounts):
        score -= 5
        reasons.append("Bank balances are cached snapshots without a reported effective time.")
    debts = [household.liabilities[ref["id"]] for ref in refs
             if ref["kind"] == "liability" and ref["id"] in household.liabilities]
    debts += [item for item in household.liabilities.values() if item.account_id in account_ids and item not in debts]
    incomplete = sorted({item.name for item in debts if not item.terms_complete})
    if incomplete:
        score -= 10
        reasons.append("Rates or required payments are missing for " + ", ".join(incomplete[:3]) + ".")
    if result.get("assumptions"):
        score -= 5
        reasons.append("The answer depends on stated assumptions.")
    score = max(0, min(100, score))
    if not reasons:
        reasons.append("Figures come from the calculators and current records, and every check passed.")
    return {"level": "high" if score >= 85 else "medium" if score >= 60 else "low",
            "score": score, "reasons": reasons}


EVIDENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["record_refs", "evidence_confidence", "figures"],
    "properties": {
        "record_refs": {"type": "array", "maxItems": 20, "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "id", "label", "account_id"],
            "properties": {
                "kind": {"enum": ["account", "bill", "liability", "card", "policy", "reserve", "income", "transaction"]},
                "id": {"type": "string", "minLength": 1},
                "label": {"type": "string", "maxLength": 160},
                "account_id": {"type": ["string", "null"]}}}},
        "evidence_confidence": {"oneOf": [{"type": "null"}, {
            "type": "object", "additionalProperties": False, "required": ["level", "score", "reasons"],
            "properties": {
                "level": {"enum": ["high", "medium", "low"]},
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "reasons": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}}}]},
        "figures": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["token", "text", "sources"],
            "properties": {
                "token": {"type": "string", "pattern": "^F[0-9]{1,3}$"},
                "text": {"type": "string", "minLength": 1},
                "sources": {"type": "array", "maxItems": 3, "items": {"type": "string", "minLength": 1}}}}},
    },
}
_EVIDENCE_VALIDATOR = Draft202012Validator(EVIDENCE_SCHEMA)


def attach_evidence(result: dict, household) -> dict:
    """Add figure sources, record links and confidence, then validate them before they are shown.

    Output that fails the schema is dropped rather than repaired, and the trace says why.
    """
    if "figures" not in result:
        result["figures"] = figure_sources(tokenize(result.get("answer") or ""), result.get("evidence") or [])
    refs = record_refs(result.get("evidence") or [], household)
    result["record_refs"] = refs
    result["evidence_confidence"] = evidence_confidence(result, household, refs)
    parts = {key: result[key] for key in ("record_refs", "evidence_confidence", "figures")}
    error = next(_EVIDENCE_VALIDATOR.iter_errors(parts), None)
    if error is not None:
        result.update(record_refs=[], evidence_confidence=None, figures=[])
        if isinstance(result.get("trace"), list):
            result["trace"].append({"node": "evidence", "result": "rejected", "reason": error.message[:200]})
    return result
