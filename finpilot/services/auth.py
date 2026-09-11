"""Verified identity and revocable opaque sessions for hosted workspaces."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from ..models import Entity, Household, Mandate
from ..seed.demo import demo_household
from ..engine.tax import TaxProfile
from ..execution.engine import ExecutionEngine
from ..persistence.database import (UserRow, HouseholdRow, MembershipRow, SessionRow, RateLimitRow, TransactionRow)
from ..persistence.codec import encode

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(24))
SESSION_SECONDS = 60 * 60 * 12


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_for(token: str) -> str:
    return hmac.new(token.encode(), b"finpilot-csrf-v1", hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Principal:
    user_id: str
    household_id: str
    role: str
    name: str
    email: str
    token: str = ""


class AuthError(ValueError):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


class AuthService:
    def __init__(self, database):
        self.db = database
        self._lock = RLock()

    def throttle(self, identity: str, limit=12, seconds=60):
        """Database-backed counters work across hosted worker processes."""
        key = digest(identity)
        now = int(time.time())
        # The insert can race on the first request; retry the existing row.
        for attempt in range(2):
            try:
                with self._lock, self.db.sessions.begin() as session:
                    row = session.execute(select(RateLimitRow).where(RateLimitRow.key == key).with_for_update()).scalar_one_or_none()
                    if row is None:
                        session.add(RateLimitRow(key=key, window_start=now, attempts=1))
                    elif now - row.window_start >= seconds:
                        row.window_start, row.attempts = now, 1
                    elif row.attempts >= limit:
                        raise AuthError("Too many attempts. Please try again shortly.", 429)
                    else:
                        row.attempts += 1
                return
            except IntegrityError:
                if attempt:
                    raise

    def register(self, name: str, email: str, password: str, sample=False):
        name, email = name.strip(), email.strip().casefold()
        if not 1 <= len(name) <= 100 or not re.fullmatch(r"[^\s@]{1,64}@[^\s@.]+(?:\.[^\s@.]+)+", email) or len(email) > 254:
            raise AuthError("Enter a valid name and email address.", 422)
        if not 12 <= len(password) <= 128:
            raise AuthError("Use a password between 12 and 128 characters.", 422)
        user_id, household_id = "usr_" + uuid.uuid4().hex, "hh_" + uuid.uuid4().hex
        password_hash = HASHER.hash(password)
        hh = demo_household() if sample else Household()
        hh.live_dates = not sample
        hh.payment_sandbox = bool(sample)
        hh.id = household_id
        hh.name = f"{name}'s workspace"
        hh.members = [user_id]
        if sample:
            for entity in hh.entities.values():
                entity.signers = [user_id]
            # A sample user's existing demo mandates are never payment authority.
            for policy in hh.policies.values():
                policy.mandate = Mandate(entity_id=policy.entity_id, jurisdiction=hh.jurisdiction)
        else:
            entity = Entity(name=name, signers=[user_id])
            hh.entities[entity.id] = entity
        tax = TaxProfile()
        ex = ExecutionEngine(hh)
        snapshot = encode({"household": hh, "tax": tax, "groups": ex.groups,
                           "reservations": ex.reservations, "paused": ex.paused,
                           "provider_submitted": {}, "provider_faults": {}, "provider_calls": []})
        token = secrets.token_urlsafe(48)
        try:
            with self.db.sessions.begin() as session:
                session.add(UserRow(id=user_id, email=email, name=name, password_hash=password_hash))
                session.add(HouseholdRow(id=household_id, name=hh.name, revision=1, snapshot=snapshot))
                session.flush()
                from ..api.workspace import _encode
                session.add_all([TransactionRow(household_id=household_id, id=tx.id,
                    account_id=tx.account_id, posted_on=tx.date.isoformat(),
                    category=tx.category or "", description=tx.description or "", payload=_encode(tx))
                    for tx in hh.transactions])
                session.add(MembershipRow(user_id=user_id, household_id=household_id, role="owner"))
                session.add(SessionRow(token_hash=digest(token), user_id=user_id, household_id=household_id,
                                       expires_at=datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)))
        except IntegrityError as exc:
            raise AuthError("An account with this email already exists. Sign in instead.", 409) from exc
        return Principal(user_id, household_id, "owner", name, email, token)

    def login(self, email: str, password: str):
        email = email.strip().casefold()
        if len(password) > 128 or len(email) > 254:
            raise AuthError("Email or password is incorrect.")
        with self.db.sessions() as session:
            user = session.execute(select(UserRow).where(UserRow.email == email)).scalar_one_or_none()
            try:
                valid = HASHER.verify(user.password_hash if user else DUMMY_HASH, password)
            except (VerificationError, InvalidHashError):
                valid = False
            if not user or not valid:
                raise AuthError("Email or password is incorrect.")
            membership = session.execute(select(MembershipRow).where(MembershipRow.user_id == user.id)).scalars().first()
            if not membership:
                raise AuthError("No workspace is available for this account.", 403)
            token = secrets.token_urlsafe(48)
            if HASHER.check_needs_rehash(user.password_hash):
                user.password_hash = HASHER.hash(password)
            session.add(SessionRow(token_hash=digest(token), user_id=user.id, household_id=membership.household_id,
                                   expires_at=datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)))
            session.execute(delete(SessionRow).where(SessionRow.expires_at < datetime.now(timezone.utc)))
            session.commit()
            return Principal(user.id, membership.household_id, membership.role, user.name, user.email, token)

    def authenticate(self, token: str):
        if not token or len(token) > 200:
            raise AuthError("Sign in to open your workspace.")
        with self.db.sessions() as session:
            result = session.execute(select(SessionRow, UserRow, MembershipRow)
                .join(UserRow, UserRow.id == SessionRow.user_id)
                .join(MembershipRow, (MembershipRow.user_id == SessionRow.user_id) &
                      (MembershipRow.household_id == SessionRow.household_id))
                .where(SessionRow.token_hash == digest(token))).first()
            if not result:
                raise AuthError("Your session has expired. Please sign in again.")
            record, user, membership = result
            expiry = record.expires_at.replace(tzinfo=timezone.utc) if record.expires_at.tzinfo is None else record.expires_at
            if expiry <= datetime.now(timezone.utc):
                raise AuthError("Your session has expired. Please sign in again.")
            return Principal(user.id, record.household_id, membership.role, user.name, user.email, token)

    def logout(self, token: str):
        with self.db.sessions.begin() as session:
            session.execute(delete(SessionRow).where(SessionRow.token_hash == digest(token)))
