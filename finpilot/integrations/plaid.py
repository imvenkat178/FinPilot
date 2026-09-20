"""Plaid aggregation adapter: balances, card and loan terms, and categorized transactions.

No credentials means no external calls. Protocol references:
https://plaid.com/docs/api/link/, https://plaid.com/docs/api/accounts/,
https://plaid.com/docs/api/products/transactions/ and
https://plaid.com/docs/api/products/liabilities/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import os
import re
import time

from cryptography.fernet import Fernet, InvalidToken
import httpx

from ..models import (Account, AccountType, Capability, Card, GraceState, Liability, LiquidityTier,
                      ProtectionType, Provenance, RateType, Transaction, TxState, Verification)
from ..money import Money
from .categories import PROVIDER_LABEL, plaid_category, plaid_kind

log = logging.getLogger(__name__)
BASE_URLS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
# Plaid account type and subtype strings, mapped to the FinPilot account types they create.
SUPPORTED = {
    "depository": {"checking": AccountType.CHECKING, "savings": AccountType.SAVINGS,
                   "money market": AccountType.MONEY_MARKET_DEPOSIT},
    "credit": {"credit card": AccountType.CREDIT_CARD},
    "loan": {"auto": AccountType.AUTO_LOAN, "consumer": AccountType.PERSONAL_LOAN,
             "mortgage": AccountType.MORTGAGE, "student": AccountType.STUDENT_LOAN},
}
DEBT_TYPES = frozenset((*SUPPORTED["credit"].values(), *SUPPORTED["loan"].values()))
TERMS_KIND = {AccountType.CREDIT_CARD: "credit", AccountType.STUDENT_LOAN: "student",
              AccountType.MORTGAGE: "mortgage"}
# Liabilities is an optional product. An item links and syncs without it, so these codes
# only mean that card and loan terms are not available for the item right now.
LIABILITIES_UNAVAILABLE = frozenset({"PRODUCTS_NOT_SUPPORTED", "PRODUCT_NOT_ENABLED", "PRODUCT_NOT_READY",
                                     "NO_LIABILITY_ACCOUNTS", "ADDITIONAL_CONSENT_REQUIRED", "INVALID_PRODUCT"})
ACCOUNT_SOURCE = "Plaid cached account snapshot"
TERMS_SOURCE = "Plaid liabilities"
INVALID = "INVALID_PROVIDER_RESPONSE"


def _identifier(value):
    """A provider identifier safe to log, or '-' when absent or unexpected."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) else "-"


class PlaidError(ValueError):
    def __init__(self, message="The bank connection could not be updated. Please try again.",
                 *, code="PROVIDER_UNAVAILABLE", status=502):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class PlaidConfig:
    client_id: str = ""
    secret: str = field(default="", repr=False)
    environment: str = ""
    token_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls):
        return cls(os.getenv("PLAID_CLIENT_ID", ""), os.getenv("PLAID_SECRET", ""),
                   os.getenv("PLAID_ENV", ""), os.getenv("FINPILOT_TOKEN_KEY", ""))

    @property
    def configured(self):
        if not self.client_id or not self.secret or self.environment not in BASE_URLS or not self.token_key:
            return False
        try:
            Fernet(self.token_key.encode())
            return True
        except (ValueError, TypeError):
            return False

    def public_status(self):
        return {"configured": self.configured,
                "environment": self.environment if self.environment in BASE_URLS else None,
                "supported_accounts": [kind.value.replace("_", " ") for group in SUPPORTED.values()
                                       for kind in group.values()],
                "balance_source": "Cached bank snapshot; not a realtime balance check.",
                "reason": "" if self.configured else "Bank linking has not been enabled on this server."}


class PlaidClient:
    def __init__(self, config=None, *, transport=None):
        self.config = config or PlaidConfig.from_env()
        self._transport = transport
        self._client = None
        self._deadline = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._client is not None:
            self._client.close()

    def require_configured(self):
        if not self.config.configured:
            raise PlaidError("Bank linking has not been enabled on this server.",
                             code="NOT_CONFIGURED", status=503)

    def encrypt(self, token):
        self.require_configured()
        return Fernet(self.config.token_key.encode()).encrypt(token.encode()).decode()

    def decrypt(self, ciphertext):
        self.require_configured()
        try:
            return Fernet(self.config.token_key.encode()).decrypt(ciphertext.encode()).decode()
        except (InvalidToken, AttributeError, UnicodeError):
            raise PlaidError("This bank connection needs operator attention before it can sync.",
                             code="TOKEN_UNAVAILABLE", status=503) from None

    def _post(self, endpoint, payload):
        self.require_configured()
        if self._deadline is None:
            self._deadline = time.monotonic() + 35
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise PlaidError("The bank update took too long. Retry to continue from the saved position.",
                             code="SYNC_TIMEOUT", status=504)
        if self._client is None:
            self._client = httpx.Client(transport=self._transport)
        try:
            response = self._client.post(BASE_URLS[self.config.environment] + endpoint,
                json={**payload, "client_id": self.config.client_id, "secret": self.config.secret},
                headers={"Plaid-Version": "2020-09-14"},
                timeout=httpx.Timeout(min(10, remaining), connect=min(2, remaining), pool=min(2, remaining)))
            data = json.loads(response.content, parse_float=Decimal)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Plaid %s %s failed before a response: %s", self.config.environment, endpoint,
                        type(exc).__name__)
            raise PlaidError("The bank provider is unavailable. Your saved data is unchanged.") from None
        if not isinstance(data, dict):
            raise PlaidError(code=INVALID)
        if response.status_code >= 400 or data.get("error_code"):
            code = str(data.get("error_code", "PROVIDER_UNAVAILABLE"))
            code = code if re.fullmatch(r"[A-Z0-9_]{1,80}", code) else "PROVIDER_UNAVAILABLE"
            # Operators need Plaid's code to act (INVALID_API_KEYS means the secret belongs to another
            # environment). Log only allowlisted identifiers, never the message, keys or tokens.
            log.warning("Plaid %s %s failed: status=%s error_type=%s error_code=%s request_id=%s",
                        self.config.environment, endpoint, response.status_code,
                        _identifier(data.get("error_type")), code, _identifier(data.get("request_id")))
            message = ("Your bank needs you to reconnect before it can sync."
                       if code in {"ITEM_LOGIN_REQUIRED", "ITEM_LOCKED", "USER_PERMISSION_REVOKED"}
                       else "The bank connection could not be updated. Please try again.")
            raise PlaidError(message, code=code)
        return data

    def create_link_token(self, user_id, redirect_uri=None):
        payload = {"user": {"client_user_id": user_id}, "client_name": "FinPilot",
            "products": ["transactions"], "optional_products": ["liabilities"],
            "country_codes": ["US"], "language": "en",
            "transactions": {"days_requested": 730},
            "account_filters": {kind: {"account_subtypes": list(subtypes)} for kind, subtypes in SUPPORTED.items()}}
        if redirect_uri and redirect_uri.startswith("https://"):
            payload["redirect_uri"] = redirect_uri.rstrip("/") + "/"
        data = self._post("/link/token/create", payload)
        if not isinstance(data.get("link_token"), str) or not data["link_token"]:
            raise PlaidError(code=INVALID)
        return {"link_token": data["link_token"], "expiration": data.get("expiration")}

    def sandbox_public_token(self, institution_id, products):
        """Create a Sandbox item without the Link browser flow, for automated verification only."""
        if self.config.environment != "sandbox":
            raise PlaidError("Test items can only be created in the Plaid Sandbox environment.",
                             code="NOT_SANDBOX", status=409)
        data = self._post("/sandbox/public_token/create",
                          {"institution_id": institution_id, "initial_products": list(products)})
        if not isinstance(data.get("public_token"), str) or not data["public_token"]:
            raise PlaidError(code=INVALID)
        return data["public_token"]

    def exchange(self, public_token):
        data = self._post("/item/public_token/exchange", {"public_token": public_token})
        if not all(isinstance(data.get(k), str) and data[k] for k in ("access_token", "item_id")):
            raise PlaidError(code=INVALID)
        return data["access_token"], data["item_id"]

    def account_snapshot(self, access_token):
        data = self._post("/accounts/get", {"access_token": access_token})
        if not isinstance(data.get("accounts"), list):
            raise PlaidError(code=INVALID)
        institution_id = (data.get("item") or {}).get("institution_id") or ""
        institution_name = "Linked bank"
        if institution_id:
            details = self._post("/institutions/get_by_id", {
                "institution_id": institution_id, "country_codes": ["US"]})
            institution_name = (details.get("institution") or {}).get("name") or institution_name
        return data["accounts"], str(institution_id), str(institution_name)[:150]

    def fetch_liabilities(self, access_token, accounts):
        """Reported card and loan terms keyed by provider account ID, or None when no debt is linked."""
        if not any(isinstance(raw, dict) and _account_type(raw) in DEBT_TYPES for raw in accounts):
            return None
        try:
            data = self._post("/liabilities/get", {"access_token": access_token})
        except PlaidError as exc:
            if exc.code in LIABILITIES_UNAVAILABLE:
                return {"available": False, "terms": {}}
            raise
        groups = data.get("liabilities")
        if not isinstance(groups, dict):
            raise PlaidError(code=INVALID)
        terms = {}
        for kind, parse in (("credit", _credit_terms), ("student", _student_terms), ("mortgage", _mortgage_terms)):
            rows = groups.get(kind) or []
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise PlaidError(code=INVALID)
            for row in rows:
                if isinstance(row.get("account_id"), str) and row["account_id"]:
                    terms[row["account_id"]] = parse(row)
        return {"available": True, "terms": terms}

    def fetch_updates(self, access_token, original_cursor):
        # A pagination mutation invalidates the whole batch, never just its last page.
        for attempt in range(3):
            cursor, changes, accounts = original_cursor, [], []
            try:
                for _ in range(100):
                    payload = {"access_token": access_token, "count": 500}
                    if cursor:
                        payload["cursor"] = cursor
                    data = self._post("/transactions/sync", payload)
                    if not isinstance(data.get("next_cursor"), str) or not isinstance(data.get("has_more"), bool):
                        raise PlaidError(code=INVALID)
                    for kind in ("added", "modified", "removed"):
                        rows = data.get(kind, [])
                        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                            raise PlaidError(code=INVALID)
                        changes.extend((kind, row) for row in rows)
                    if isinstance(data.get("accounts"), list):
                        accounts = data["accounts"]
                    next_cursor = data["next_cursor"]
                    if not data["has_more"]:
                        return {"cursor": next_cursor, "changes": changes, "accounts": accounts}
                    if next_cursor == cursor:
                        raise PlaidError(code=INVALID)
                    cursor = next_cursor
                raise PlaidError("This update is too large to complete now. Contact the operator.", code="SYNC_LIMIT")
            except PlaidError as exc:
                if exc.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" or attempt == 2:
                    raise
        raise PlaidError(code="SYNC_LIMIT")

    def remove(self, access_token):
        self._post("/item/remove", {"access_token": access_token})


def provider_id(connection_id, value, prefix):
    return prefix + hashlib.sha256((connection_id + ":" + value).encode()).hexdigest()[:28]


def _money(value, currency):
    if value is None:
        raise ValueError("Balance is not available")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError("Amount is not finite")
        return Money(amount, currency)
    except (InvalidOperation, TypeError):
        raise ValueError("Amount is not valid") from None


def _account_type(raw):
    kind, subtype = raw.get("type"), raw.get("subtype")
    if not isinstance(kind, str) or not isinstance(subtype, str):
        return None
    return SUPPORTED.get(kind, {}).get(subtype)


def _number(value, *, negative=False):
    """A reported number or None. Plaid numbers arrive as Decimal or int."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise PlaidError(code=INVALID)
    amount = Decimal(value)
    if not amount.is_finite() or (amount < 0 and not negative):
        raise PlaidError(code=INVALID)
    return amount


def _rate(value):
    percentage = _number(value)
    if percentage is not None and percentage > 100:
        raise PlaidError(code=INVALID)
    return None if percentage is None else percentage / 100


def _day(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise PlaidError(code=INVALID)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise PlaidError(code=INVALID) from None


def _object(value):
    value = value or {}
    if not isinstance(value, dict):
        raise PlaidError(code=INVALID)
    return value


def _credit_terms(row):
    aprs = row.get("aprs") or []
    if not isinstance(aprs, list) or not all(isinstance(item, dict) for item in aprs):
        raise PlaidError(code=INVALID)
    purchase = next((item for item in aprs if item.get("apr_type") == "purchase_apr"), {})
    return {"kind": "credit", "apr": _rate(purchase.get("apr_percentage")),
            "minimum_payment": _number(row.get("minimum_payment_amount")),
            "next_due": _day(row.get("next_payment_due_date")),
            "statement_balance": _number(row.get("last_statement_balance"), negative=True),
            "statement_date": _day(row.get("last_statement_issue_date"))}


def _student_terms(row):
    plan = _object(row.get("repayment_plan"))
    program = plan.get("description") or plan.get("type")
    return {"kind": "student", "apr": _rate(row.get("interest_rate_percentage")),
            "minimum_payment": _number(row.get("minimum_payment_amount")),
            "next_due": _day(row.get("next_payment_due_date")),
            "program": program[:100] if isinstance(program, str) and program else None}


def _mortgage_terms(row):
    rate = _object(row.get("interest_rate"))
    penalty = row.get("has_prepayment_penalty")
    if penalty is not None and not isinstance(penalty, bool):
        raise PlaidError(code=INVALID)
    rate_type = rate.get("type")
    return {"kind": "mortgage", "apr": _rate(rate.get("percentage")),
            "rate_type": {"fixed": RateType.FIXED, "variable": RateType.VARIABLE}.get(
                rate_type.lower() if isinstance(rate_type, str) else None),
            "monthly_payment": _number(row.get("next_monthly_payment")),
            "next_due": _day(row.get("next_payment_due_date")),
            "maturity": _day(row.get("maturity_date")), "prepayment_penalty": penalty}


def _months_until(start, end):
    months = (end.year - start.year) * 12 + end.month - start.month - (1 if end.day < start.day else 0)
    return months if 1 <= months <= 1200 else None


def apply_accounts(household, connection, rows, now):
    """Import reported balances without inventing access, rates, coverage or terms."""
    mapping, notices = dict(connection.account_mapping or {}), []
    for account_id in mapping.values():
        account = household.accounts.get(account_id)
        if account:
            account.connection_healthy = False
            account.connection_issue = "The bank did not provide a complete balance snapshot."
            account.provenance.verification = Verification.STALE
    for raw in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("account_id"), str):
            raise PlaidError(code=INVALID)
        kind = _account_type(raw)
        if kind is None:
            notices.append("Only checking, savings, money-market, credit card, auto loan, personal loan, "
                           "mortgage and student loan accounts are supported.")
            continue
        balances = raw.get("balances") or {}
        if not isinstance(balances, dict):
            raise PlaidError(code=INVALID)
        currency = balances.get("iso_currency_code")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            notices.append("An account was omitted because its currency is unsupported.")
            continue
        debt = kind in DEBT_TYPES
        try:
            current = _money(balances.get("current"), currency)
            # Available credit is not money the household has, so debt accounts keep none.
            available = Money.zero(currency) if debt else _money(balances.get("available"), currency)
        except ValueError:
            notices.append("An account has incomplete balances and will be imported when the bank provides them.")
            continue
        account_id = mapping.get(raw["account_id"]) or provider_id(connection.id, raw["account_id"], "acc_plaid_")
        account = household.accounts.get(account_id)
        if account is None:
            account = Account(id=account_id, nickname=str(raw.get("name") or "Linked account")[:100],
                type=kind, currency=currency,
                institution=connection.institution_name, institution_id="", mask=str(raw.get("mask") or "")[:8],
                ownership_category="unverified",
                protection=ProtectionType.NONE if debt else ProtectionType.UNRESOLVED,
                liquidity_tier=(LiquidityTier.CONTINGENT_BORROWING if debt else
                                LiquidityTier.IMMEDIATE if kind == AccountType.CHECKING else LiquidityTier.SAME_DAY),
                capabilities={Capability.VIEW_BALANCE, Capability.VIEW_TRANSACTIONS},
                pending=Money.zero(currency), reserved=Money.zero(currency), held=Money.zero(currency))
            household.accounts[account_id] = account
        if account.type != kind:
            notices.append("The bank changed an account type. That account keeps its last balances until reviewed.")
            continue
        if account.currency != currency:
            raise PlaidError("The bank changed an account currency; review is needed before importing it.", code="CURRENCY_CHANGED")
        # Debt accounts hold the amount owed as a negative current value, like manual records.
        account.current = Money(-current.amount, currency) if debt else current
        account.available = available
        # Do not infer pending/held totals from current minus available: banks vary.
        account.last_synced_at = now
        account.connection_healthy = True
        account.connection_issue = ""
        account.provenance = Provenance(source=ACCOUNT_SOURCE, as_of=None, verification=Verification.UNKNOWN)
        mapping[raw["account_id"]] = account_id
    connection.account_mapping = mapping
    connection.notices = list(dict.fromkeys(notices))


def apply_liabilities(household, connection, rows, liabilities, now):
    """Track every linked debt balance and import only the terms the provider reports.

    A debt whose rate or required payment is unknown stays in debt totals with
    terms_complete=False, so payoff modelling leaves it out until someone enters the terms.
    A servicer's single mortgage payment can include escrow, which FinPilot keeps out of the
    interest model, so a linked mortgage waits for that split unless one was already entered.
    """
    notices = list(connection.notices or [])
    if liabilities is not None and not liabilities["available"]:
        notices.append("Your bank has not shared card and loan terms for this connection yet. Balances are "
                       "tracked, and payoff plans include a debt once its rate and payment are known.")
    reported = liabilities["terms"] if liabilities else {}
    snapshot = {raw["account_id"]: raw for raw in rows
                if isinstance(raw, dict) and isinstance(raw.get("account_id"), str)}
    missing, mortgages, cards = [], [], []
    for provider_account, account_id in (connection.account_mapping or {}).items():
        account = household.accounts.get(account_id)
        raw = snapshot.get(provider_account)
        if (account is None or account.type not in DEBT_TYPES or raw is None
                or not account.connection_healthy or _account_type(raw) != account.type):
            continue
        owed = Money(max(-account.current.amount, Decimal("0")), account.currency)
        terms = reported.get(provider_account) or {}
        if terms.get("kind") != TERMS_KIND.get(account.type):
            terms = {}
        liability = next((item for item in household.liabilities.values() if item.account_id == account_id), None)
        if liability is None:
            liability = Liability(id=provider_id(connection.id, provider_account, "lia_plaid_"),
                                  account_id=account_id, type=account.type, terms_complete=False)
            household.liabilities[liability.id] = liability
        liability.name, liability.balance = account.nickname, owed
        if terms.get("apr") is not None:
            liability.apr = terms["apr"]
        if terms.get("next_due") is not None:
            liability.due_day = terms["next_due"].day
        if terms.get("kind") in ("credit", "student"):
            if terms["minimum_payment"] is not None:
                liability.minimum_payment = Money(terms["minimum_payment"], account.currency)
                if terms["apr"] is not None:
                    liability.terms_complete = True
            if terms.get("program"):
                liability.student_loan_program = terms["program"]
        elif terms.get("kind") == "mortgage":
            if terms["rate_type"] is not None:
                liability.rate_type = terms["rate_type"]
            months = _months_until(household.as_of, terms["maturity"]) if terms["maturity"] else None
            if months:
                liability.remaining_term_months = months
            if terms["prepayment_penalty"]:
                liability.prepayment_penalty = "Reported by the servicer"
        liability.provenance = Provenance(source=TERMS_SOURCE if terms else ACCOUNT_SOURCE,
                                          as_of=None, verification=Verification.UNKNOWN)
        if not liability.terms_complete:
            (mortgages if account.type == AccountType.MORTGAGE else missing).append(account.nickname)
        if account.type == AccountType.CREDIT_CARD and not _apply_card(
                household, connection, provider_account, account, raw, terms, owed):
            cards.append(account.nickname)
    if missing:
        notices.append("Enter the interest rate and required payment for " + ", ".join(sorted(missing))
                       + " to include them in payoff plans.")
    if mortgages:
        notices.append("Your servicer reports one monthly payment that can include escrow. Enter principal and "
                       "interest and escrow separately for " + ", ".join(sorted(mortgages))
                       + " to include them in payoff and mortgage plans.")
    if cards:
        notices.append("Utilization timing and card choices need a credit limit, statement date, due date and "
                       "purchase APR for " + ", ".join(sorted(cards)) + ".")
    connection.notices = list(dict.fromkeys(notices))


def _apply_card(household, connection, provider_account, account, raw, terms, owed):
    """A card record needs a limit, statement date, due date and purchase APR; none of them is guessed."""
    card = next((item for item in household.cards.values() if item.account_id == account.id), None)
    limit = _number(_object(raw.get("balances")).get("limit"))
    statement, due, apr = terms.get("statement_date"), terms.get("next_due"), terms.get("apr")
    if card is None:
        if any(value is None for value in (limit, statement, due, apr)):
            return False
        card = Card(id=provider_id(connection.id, provider_account, "card_plaid_"), account_id=account.id,
                    grace_state=GraceState.UNKNOWN)
        household.cards[card.id] = card
    card.nickname, card.issuer, card.mask = account.nickname, account.institution, account.mask
    card.current_balance = owed
    if limit is not None:
        card.credit_limit = Money(limit, account.currency)
    if statement is not None:
        card.statement_close_day = statement.day
    if due is not None:
        card.payment_due_day = due.day
    if apr is not None:
        card.purchase_apr = apr
    if terms.get("statement_balance") is not None:
        card.statement_balance = Money(max(terms["statement_balance"], Decimal("0")), account.currency)
    return True


def apply_transactions(household, connection, changes):
    existing = {tx.id: tx for tx in household.transactions}
    matched_pending = set()
    imported = 0
    for change, raw in changes:
        raw_id = raw.get("transaction_id")
        if not isinstance(raw_id, str) or not raw_id:
            raise PlaidError(code=INVALID)
        tx_id = provider_id(connection.id, raw_id, "tx_plaid_")
        old = existing.get(tx_id)
        if change == "removed":
            if old is not None:
                old.state = TxState.REVERSED
            continue
        account_id = (connection.account_mapping or {}).get(raw.get("account_id"))
        if account_id not in household.accounts:
            continue
        account = household.accounts[account_id]
        currency = raw.get("iso_currency_code")
        if currency != account.currency:
            raise PlaidError("A transaction currency did not match its account. No update was saved.", code="CURRENCY_MISMATCH")
        try:
            amount = -_money(raw.get("amount"), currency)  # Plaid positive means outflow.
            when = date.fromisoformat(raw["date"])
        except (ValueError, KeyError, TypeError):
            raise PlaidError(code=INVALID) from None
        if not isinstance(raw.get("pending"), bool):
            raise PlaidError(code=INVALID)
        categories = raw.get("personal_finance_category") or {}
        if not isinstance(categories, dict):
            raise PlaidError(code=INVALID)
        primary, detailed = categories.get("primary") or "", categories.get("detailed") or ""
        if not isinstance(primary, str) or not isinstance(detailed, str) or len(primary) > 100 or len(detailed) > 100:
            raise PlaidError(code=INVALID)
        if old is not None and old.account_id != account_id:
            raise PlaidError(code="TRANSACTION_ACCOUNT_CHANGED")
        transaction = old or Transaction(id=tx_id, account_id=account_id)
        transaction.date, transaction.amount = when, amount
        transaction.state = TxState.PENDING if raw["pending"] else TxState.POSTED
        transaction.balance_already_reflected = True
        # The provider's own label stays visible even after someone corrects the category.
        transaction.provider_category = next((label for label in (detailed, primary) if PROVIDER_LABEL.fullmatch(label)), "")
        if not transaction.user_corrected:
            transaction.description = str(raw.get("name") or "Bank transaction")[:500]
            transaction.merchant = str(raw.get("merchant_name") or "")[:200]
            transaction.category = plaid_category(primary, detailed)
            transaction.kind = plaid_kind(primary, detailed, amount.is_negative)
        pending_id = raw.get("pending_transaction_id")
        if isinstance(pending_id, str) and pending_id:
            linked = provider_id(connection.id, pending_id, "tx_plaid_")
            transaction.linked_tx_id = linked
            matched_pending.add(linked)
        existing[tx_id] = transaction
        imported += 1
    for tx_id in matched_pending:
        if tx_id in existing:
            existing[tx_id].state = TxState.REVERSED
    household.transactions = list(existing.values())
    return imported
