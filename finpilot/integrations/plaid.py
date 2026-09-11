"""Plaid Transactions adapter. No credentials means no external calls.

Protocol references: https://plaid.com/docs/api/link/
https://plaid.com/docs/api/products/transactions/ and /docs/api/accounts/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import time

from cryptography.fernet import Fernet, InvalidToken
import httpx

from ..models import (Account, AccountType, Capability, LiquidityTier,
                      ProtectionType, Provenance, Transaction, TxKind, TxState,
                      Verification)
from ..money import Money

BASE_URLS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
SUPPORTED = {"checking": AccountType.CHECKING, "savings": AccountType.SAVINGS,
             "money market": AccountType.MONEY_MARKET_DEPOSIT}


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
                "supported_accounts": list(SUPPORTED),
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
        except (httpx.HTTPError, ValueError):
            raise PlaidError("The bank provider is unavailable. Your saved data is unchanged.") from None
        if not isinstance(data, dict):
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
        if response.status_code >= 400 or data.get("error_code"):
            code = str(data.get("error_code", "PROVIDER_UNAVAILABLE"))
            code = code if re.fullmatch(r"[A-Z0-9_]{1,80}", code) else "PROVIDER_UNAVAILABLE"
            message = ("Your bank needs you to reconnect before it can sync."
                       if code in {"ITEM_LOGIN_REQUIRED", "ITEM_LOCKED", "USER_PERMISSION_REVOKED"}
                       else "The bank connection could not be updated. Please try again.")
            raise PlaidError(message, code=code)
        return data

    def create_link_token(self, user_id, redirect_uri=None):
        payload = {"user": {"client_user_id": user_id}, "client_name": "FinPilot",
            "products": ["transactions"], "country_codes": ["US"], "language": "en",
            "transactions": {"days_requested": 90},
            "account_filters": {"depository": {"account_subtypes": list(SUPPORTED)}}}
        if redirect_uri and redirect_uri.startswith("https://"):
            payload["redirect_uri"] = redirect_uri.rstrip("/") + "/"
        data = self._post("/link/token/create", payload)
        if not isinstance(data.get("link_token"), str) or not data["link_token"]:
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
        return {"link_token": data["link_token"], "expiration": data.get("expiration")}

    def exchange(self, public_token):
        data = self._post("/item/public_token/exchange", {"public_token": public_token})
        if not all(isinstance(data.get(k), str) and data[k] for k in ("access_token", "item_id")):
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
        return data["access_token"], data["item_id"]

    def account_snapshot(self, access_token):
        data = self._post("/accounts/get", {"access_token": access_token})
        if not isinstance(data.get("accounts"), list):
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
        institution_id = (data.get("item") or {}).get("institution_id") or ""
        institution_name = "Linked bank"
        if institution_id:
            details = self._post("/institutions/get_by_id", {
                "institution_id": institution_id, "country_codes": ["US"]})
            institution_name = (details.get("institution") or {}).get("name") or institution_name
        return data["accounts"], str(institution_id), str(institution_name)[:150]

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
                        raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
                    for kind in ("added", "modified", "removed"):
                        rows = data.get(kind, [])
                        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
                        changes.extend((kind, row) for row in rows)
                    if isinstance(data.get("accounts"), list):
                        accounts = data["accounts"]
                    next_cursor = data["next_cursor"]
                    if not data["has_more"]:
                        return {"cursor": next_cursor, "changes": changes, "accounts": accounts}
                    if next_cursor == cursor:
                        raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
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


def apply_accounts(household, connection, rows, now):
    """Import reported cash snapshots without inventing access, rates, or coverage."""
    mapping, notices = dict(connection.account_mapping or {}), []
    for account_id in mapping.values():
        account = household.accounts.get(account_id)
        if account:
            account.connection_healthy = False
            account.connection_issue = "The bank did not provide a complete balance snapshot."
            account.provenance.verification = Verification.STALE
    for raw in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("account_id"), str):
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
        if raw.get("type") != "depository" or raw.get("subtype") not in SUPPORTED:
            notices.append("Only checking, savings, and money-market deposit accounts are supported.")
            continue
        balances = raw.get("balances") or {}
        if not isinstance(balances, dict):
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
        currency = balances.get("iso_currency_code")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            notices.append("An account was omitted because its currency is unsupported.")
            continue
        try:
            current, available = _money(balances.get("current"), currency), _money(balances.get("available"), currency)
        except ValueError:
            notices.append("An account has incomplete balances and will be imported when the bank provides them.")
            continue
        account_id = mapping.get(raw["account_id"]) or provider_id(connection.id, raw["account_id"], "acc_plaid_")
        account = household.accounts.get(account_id)
        if account is None:
            account = Account(id=account_id, nickname=str(raw.get("name") or "Linked account")[:100],
                type=SUPPORTED[raw["subtype"]], currency=currency,
                institution=connection.institution_name, institution_id="", mask=str(raw.get("mask") or "")[:8],
                ownership_category="unverified", protection=ProtectionType.UNRESOLVED,
                liquidity_tier=LiquidityTier.IMMEDIATE if raw["subtype"] == "checking" else LiquidityTier.SAME_DAY,
                capabilities={Capability.VIEW_BALANCE, Capability.VIEW_TRANSACTIONS},
                pending=Money.zero(currency), reserved=Money.zero(currency), held=Money.zero(currency))
            household.accounts[account_id] = account
        if account.currency != currency:
            raise PlaidError("The bank changed an account currency; review is needed before importing it.", code="CURRENCY_CHANGED")
        account.current, account.available = current, available
        # Do not infer pending/held totals from current minus available: banks vary.
        account.last_synced_at = now
        account.connection_healthy = True
        account.connection_issue = ""
        account.provenance = Provenance(source="Plaid cached account snapshot",
            as_of=None, verification=Verification.UNKNOWN)
        mapping[raw["account_id"]] = account_id
    connection.account_mapping = mapping
    connection.notices = list(dict.fromkeys(notices))


def apply_transactions(household, connection, changes):
    existing = {tx.id: tx for tx in household.transactions}
    matched_pending = set()
    imported = 0
    for change, raw in changes:
        raw_id = raw.get("transaction_id")
        if not isinstance(raw_id, str) or not raw_id:
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
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
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE") from None
        if not isinstance(raw.get("pending"), bool):
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
        categories = raw.get("personal_finance_category") or {}
        if not isinstance(categories, dict):
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
        category = categories.get("primary") or "UNCATEGORIZED"
        if not isinstance(category, str) or len(category) > 100:
            raise PlaidError(code="INVALID_PROVIDER_RESPONSE")
        if old is not None and old.account_id != account_id:
            raise PlaidError(code="TRANSACTION_ACCOUNT_CHANGED")
        kind = (TxKind.INCOME if category == "INCOME" else
                TxKind.INTERNAL_TRANSFER if category.startswith("TRANSFER_") else
                TxKind.LOAN_PAYMENT if category == "LOAN_PAYMENTS" else
                TxKind.FEE if category == "BANK_FEES" else
                TxKind.PURCHASE if amount.is_negative else TxKind.PROVISIONAL_CREDIT)
        transaction = old or Transaction(id=tx_id, account_id=account_id)
        transaction.date, transaction.amount = when, amount
        transaction.state = TxState.PENDING if raw["pending"] else TxState.POSTED
        transaction.balance_already_reflected = True
        if not transaction.user_corrected:
            transaction.description = str(raw.get("name") or "Bank transaction")[:500]
            transaction.merchant = str(raw.get("merchant_name") or "")[:200]
            transaction.category, transaction.kind = category.lower(), kind
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
