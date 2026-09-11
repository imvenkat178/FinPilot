"""Core domain objects -- spec section 13, "Core data objects".

Every object here carries the provenance fields the specification demands:
where a value came from, when it was last verified, and whether it is confirmed,
estimated or unknown. TR03: "Maintain provenance, last-verified dates, rule
versions, conflict handling, and conservative behavior for missing information."
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from .money import Money
from .dates import Cadence, Schedule


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Provenance and confidence -- EN10's confidence ladder, in the data model
# ---------------------------------------------------------------------------

class Verification(str, Enum):
    CONFIRMED = "confirmed"     # from an authoritative source, in date
    ESTIMATED = "estimated"     # derived or predicted; labeled as such
    USER_ENTERED = "user_entered"
    STALE = "stale"             # was confirmed, now past its freshness window
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    """EN10. Every calculation ships at a stated confidence with exactly one
    visible action that would make it exact."""
    EXACT = "exact"
    BOUNDED = "bounded"
    DIRECTIONAL = "directional"
    INSUFFICIENT = "insufficient"


@dataclass
class Provenance:
    source: str = "manual"
    as_of: Optional[date] = None
    verification: Verification = Verification.USER_ENTERED
    rule_version: Optional[str] = None
    evidence_ref: Optional[str] = None

    def to_json(self) -> dict:
        return {"source": self.source,
                "as_of": self.as_of.isoformat() if self.as_of else None,
                "verification": self.verification.value,
                "rule_version": self.rule_version,
                "evidence_ref": self.evidence_ref}


# ---------------------------------------------------------------------------
# Identity, entities, permission
# ---------------------------------------------------------------------------

class OwnerType(str, Enum):
    INDIVIDUAL = "individual"
    JOINT = "joint"
    TRUST = "trust"
    LLC = "llc"
    SOLE_PROP = "sole_proprietorship"
    BUSINESS = "business"


class Role(str, Enum):
    OWNER = "owner"
    PREPARER = "preparer"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    READ_ONLY = "read_only"


@dataclass
class Entity:
    """HN01. A legal owner. A household relationship grants neither ownership
    nor payment authority."""
    id: str = field(default_factory=lambda: _id("ent"))
    name: str = ""
    owner_type: OwnerType = OwnerType.INDIVIDUAL
    tax_id_last4: Optional[str] = None
    jurisdiction: str = "US"
    signers: list[str] = field(default_factory=list)
    second_signer_threshold: Optional[Money] = None
    beneficiaries: list[str] = field(default_factory=list)  # IN03 trust coverage


@dataclass
class ConsentGrant:
    """TR01. Enforced at every API, document, AI tool and execution boundary."""
    id: str = field(default_factory=lambda: _id("cg"))
    grantor_user: str = ""
    grantee_user: str = ""
    role: Role = Role.READ_ONLY
    account_ids: list[str] = field(default_factory=list)
    can_execute: bool = False
    revoked_at: Optional[datetime] = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


# ---------------------------------------------------------------------------
# Accounts and balances
# ---------------------------------------------------------------------------

class AccountType(str, Enum):
    CHECKING = "checking"
    SAVINGS = "savings"
    MONEY_MARKET_DEPOSIT = "money_market_deposit"
    CD = "cd"
    CREDIT_CARD = "credit_card"
    PERSONAL_LOAN = "personal_loan"
    AUTO_LOAN = "auto_loan"
    STUDENT_LOAN = "student_loan"
    MORTGAGE = "mortgage"
    BROKERAGE = "brokerage"
    BROKERAGE_SWEEP = "brokerage_sweep"
    MONEY_MARKET_FUND = "money_market_fund"
    RETIREMENT = "retirement"
    HSA = "hsa"
    SBLOC = "sbloc"
    MARGIN = "margin"
    CASH = "cash"
    BNPL = "bnpl"                     # BN01 -- installment obligations
    WALLET = "wallet"                 # PP01 -- peer transfer rails
    ESTIMATED_ASSET = "estimated_asset"   # AC01 -- labelled separately, never spendable


class LiquidityTier(str, Enum):
    """LQ02. Classified by verified accessibility, not by appearance."""
    IMMEDIATE = "immediate"
    SAME_DAY = "same_day"
    ONE_TO_THREE_DAYS = "1_3_days"
    DATED = "dated"                  # matures on a date
    INVESTMENT = "investment"        # settlement + market risk
    CONTINGENT_BORROWING = "contingent_borrowing"   # never ordinary cash
    ESTIMATED = "estimated"          # a user estimate, never payment capacity


class ProtectionType(str, Enum):
    """Section 19. Never one green 'insured' badge over a whole balance."""
    FDIC = "fdic"
    NCUA = "ncua"
    SIPC = "sipc"
    NONE = "none"
    UNRESOLVED = "unresolved"


class Capability(str, Enum):
    """Section 21: connection and payment capability are separate."""
    VIEW_BALANCE = "view_balance"
    VIEW_TRANSACTIONS = "view_transactions"
    VERIFY_OWNERSHIP = "verify_ownership"
    SEND_TRANSFER = "send_transfer"
    RECEIVE_TRANSFER = "receive_transfer"
    PAY_BILLER = "pay_biller"
    PRINCIPAL_ONLY = "principal_only"
    CHANGE_AUTOPAY = "change_autopay"


@dataclass
class Account:
    id: str = field(default_factory=lambda: _id("acc"))
    nickname: str = ""
    type: AccountType = AccountType.CHECKING
    entity_id: Optional[str] = None
    institution: str = ""
    institution_id: str = ""          # the actual insured institution, IN01
    mask: str = "0000"
    currency: str = "USD"

    # balance family -- AC04 requires these to be distinguishable
    current: Money = field(default_factory=lambda: Money.zero())
    available: Money = field(default_factory=lambda: Money.zero())
    pending: Money = field(default_factory=lambda: Money.zero())
    reserved: Money = field(default_factory=lambda: Money.zero())
    held: Money = field(default_factory=lambda: Money.zero())

    apy: Optional[Decimal] = None
    rate_tiers: list[tuple[Money, Decimal]] = field(default_factory=list)
    minimum_balance: Optional[Money] = None
    monthly_fee: Optional[Money] = None
    withdrawal_limit_per_month: Optional[int] = None

    liquidity_tier: LiquidityTier = LiquidityTier.IMMEDIATE
    protection: ProtectionType = ProtectionType.NONE
    ownership_category: str = "single"        # single | joint | trust | business
    co_owners: list[str] = field(default_factory=list)

    capabilities: set[Capability] = field(default_factory=set)
    included_in_planning: bool = True
    connection_healthy: bool = True
    connection_issue: str = ""        # human-readable reason when unhealthy
    last_synced_at: Optional[datetime] = None
    provenance: Provenance = field(default_factory=Provenance)

    # underlying bank allocations for a sweep program (IN02)
    sweep_allocations: dict[str, Money] = field(default_factory=dict)

    def can(self, cap: Capability) -> bool:
        return cap in self.capabilities

    @property
    def spendable(self) -> Money:
        """Available minus what is already reserved. Never the credit limit,
        never an investment valuation."""
        if self.type in (AccountType.CHECKING, AccountType.SAVINGS,
                         AccountType.MONEY_MARKET_DEPOSIT, AccountType.CASH,
                         AccountType.BROKERAGE_SWEEP):
            return (self.available - self.reserved).clamp_min_zero()
        return Money.zero(self.currency)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

class TxState(str, Enum):
    PENDING = "pending"
    POSTED = "posted"
    CORRECTED = "corrected"
    REVERSED = "reversed"
    RECONCILED = "reconciled"


class TxKind(str, Enum):
    PURCHASE = "purchase"
    INCOME = "income"
    INTERNAL_TRANSFER = "internal_transfer"     # must never inflate income
    CARD_REPAYMENT = "card_repayment"           # settles a liability
    LOAN_PAYMENT = "loan_payment"
    FEE = "fee"
    INTEREST = "interest"
    REFUND = "refund"
    REIMBURSEMENT = "reimbursement"             # PP01
    P2P = "p2p"
    DISPUTED = "disputed"
    PROVISIONAL_CREDIT = "provisional_credit"   # FD02: conditional, not income


@dataclass
class Transaction:
    id: str = field(default_factory=lambda: _id("tx"))
    account_id: str = ""
    date: date = field(default_factory=date.today)
    amount: Money = field(default_factory=lambda: Money.zero())  # negative = outflow
    description: str = ""
    merchant: str = ""
    mcc: Optional[str] = None
    category: str = "uncategorized"
    kind: TxKind = TxKind.PURCHASE
    state: TxState = TxState.POSTED
    transfer_group_id: Optional[str] = None
    linked_tx_id: Optional[str] = None          # refund -> original charge
    user_corrected: bool = False
    balance_already_reflected: bool = False

    @property
    def counts_as_income(self) -> bool:
        return self.kind == TxKind.INCOME and self.state in (
            TxState.POSTED, TxState.RECONCILED)

    @property
    def counts_as_spending(self) -> bool:
        return (self.kind in (TxKind.PURCHASE, TxKind.FEE) and self.amount.is_negative
                and self.state != TxState.REVERSED)


# ---------------------------------------------------------------------------
# Income and bills
# ---------------------------------------------------------------------------

@dataclass
class IncomeSource:
    id: str = field(default_factory=lambda: _id("inc"))
    name: str = "Primary employment"
    net_amount: Money = field(default_factory=lambda: Money.zero())
    schedule: Optional[Schedule] = None
    deposit_account_id: str = ""
    reliability: Decimal = Decimal("1.0")   # 1.0 = confirmed salary
    is_variable: bool = False
    entity_id: Optional[str] = None
    # EQ01: payroll deductions happen before this net amount reaches the bank
    pretax_deductions: dict[str, Money] = field(default_factory=dict)
    employer_match_formula: Optional[str] = None


@dataclass
class IncomeEvent:
    id: str = field(default_factory=lambda: _id("ie"))
    source_id: str = ""
    expected_date: date = field(default_factory=date.today)
    expected_amount: Money = field(default_factory=lambda: Money.zero())
    received_date: Optional[date] = None
    received_amount: Optional[Money] = None
    matched_tx_id: Optional[str] = None

    @property
    def is_received(self) -> bool:
        return self.received_amount is not None

    @property
    def effective_amount(self) -> Money:
        return self.received_amount if self.is_received else self.expected_amount


class BillOwner(str, Enum):
    """Section 14: exactly one execution owner per occurrence."""
    APP = "app"
    CREDITOR_AUTOPAY = "creditor_autopay"
    BANK_BILLPAY = "bank_billpay"
    USER = "user"


@dataclass
class Bill:
    id: str = field(default_factory=lambda: _id("bill"))
    name: str = ""
    amount: Money = field(default_factory=lambda: Money.zero())
    due_date: date = field(default_factory=date.today)
    schedule: Optional[Schedule] = None
    funding_account_id: str = ""
    payee_account_id: Optional[str] = None     # a liability we hold
    required: bool = True
    category: str = "other"
    execution_owner: BillOwner = BillOwner.USER
    amount_confirmed: bool = True
    autopay_confirmed: bool = False
    provenance: Provenance = field(default_factory=Provenance)
    # BN01: installment plans are obligations even though no biller statements
    installment_provider: Optional[str] = None

    def occurrence_id(self, when: date) -> str:
        return f"bill:{self.id}:{when.isoformat()}"


@dataclass
class BillOccurrence:
    """One dated obligation, shared by planning and every payment route.

    Funded means the source was debited; paid means the creditor confirmed
    application. Neither changes the next recurrence of the bill.
    """
    id: str = ""
    bill_id: str = ""
    due_date: date = field(default_factory=date.today)
    amount: Money = field(default_factory=lambda: Money.zero())
    funded: Money = field(default_factory=lambda: Money.zero())
    paid: Money = field(default_factory=lambda: Money.zero())

    @property
    def remaining(self) -> Money:
        return (self.amount - self.paid).clamp_min_zero()

    @property
    def cash_remaining(self) -> Money:
        return (self.amount - self.funded).clamp_min_zero()

    def to_json(self) -> dict:
        return {"id": self.id, "bill_id": self.bill_id,
                "due_date": self.due_date.isoformat(), "amount": self.amount.to_json(),
                "funded": self.funded.to_json(), "paid": self.paid.to_json(),
                "remaining": self.remaining.to_json(),
                "cash_remaining": self.cash_remaining.to_json()}


# ---------------------------------------------------------------------------
# Goals and reserves
# ---------------------------------------------------------------------------

class ReservePurpose(str, Enum):
    EMERGENCY = "emergency"
    ANNUAL_BILL = "annual_bill"
    TAX = "tax"
    GOAL = "goal"
    PROMO_PAYOFF = "promo_payoff"
    OPERATING_FLOOR = "operating_floor"


@dataclass
class Reserve:
    """PL06. Earmarked inside an existing account. The same dollar can never
    fund two reserves."""
    id: str = field(default_factory=lambda: _id("res"))
    name: str = ""
    account_id: str = ""
    purpose: ReservePurpose = ReservePurpose.GOAL
    target: Money = field(default_factory=lambda: Money.zero())
    funded: Money = field(default_factory=lambda: Money.zero())
    target_date: Optional[date] = None
    protected: bool = True          # excluded from sweeps and extra payments
    entity_id: Optional[str] = None

    @property
    def remaining(self) -> Money:
        return (self.target - self.funded).clamp_min_zero()


# ---------------------------------------------------------------------------
# Liabilities
# ---------------------------------------------------------------------------

class RateType(str, Enum):
    FIXED = "fixed"
    VARIABLE = "variable"
    PROMOTIONAL_ZERO = "promotional_zero"
    DEFERRED_INTEREST = "deferred_interest"


@dataclass
class BalanceBucket:
    """DB05. A card holds several buckets with different rates, and US
    allocation rules -- not the user -- decide where above-minimum money goes."""
    name: str = "purchase"
    balance: Money = field(default_factory=lambda: Money.zero())
    apr: Decimal = Decimal("0")
    rate_type: RateType = RateType.FIXED
    promo_expires: Optional[date] = None
    deferred_interest_accrued: Money = field(default_factory=lambda: Money.zero())


@dataclass
class Liability:
    id: str = field(default_factory=lambda: _id("lia"))
    account_id: str = ""
    name: str = ""
    type: AccountType = AccountType.CREDIT_CARD
    balance: Money = field(default_factory=lambda: Money.zero())
    apr: Decimal = Decimal("0")               # contractual note rate, not APR disclosure
    rate_type: RateType = RateType.FIXED
    minimum_payment: Money = field(default_factory=lambda: Money.zero())
    due_day: int = 1
    remaining_term_months: Optional[int] = None
    buckets: list[BalanceBucket] = field(default_factory=list)

    # mortgage detail -- DB04 keeps these out of the interest model
    escrow: Money = field(default_factory=lambda: Money.zero())
    mortgage_insurance: Money = field(default_factory=lambda: Money.zero())
    prepayment_penalty: Optional[str] = None
    servicer_principal_only_supported: bool = False

    # program-specific -- DB07
    student_loan_program: Optional[str] = None
    hardship_plan: Optional[str] = None
    entity_id: Optional[str] = None
    tax_deductible_interest: bool = False     # TX03: never assumed from the name
    provenance: Provenance = field(default_factory=Provenance)

    @property
    def monthly_rate(self) -> Decimal:
        return self.apr / Decimal("12")

    @property
    def total_required_payment(self) -> Money:
        """What must leave the bank each month, including escrow for a mortgage.
        Distinct from the amount that reduces principal and interest."""
        return self.minimum_payment + self.escrow + self.mortgage_insurance


# ---------------------------------------------------------------------------
# Cards and rewards
# ---------------------------------------------------------------------------

class GraceState(str, Enum):
    """RW01/S14. Losing the grace period makes new purchases accrue from the
    transaction date, which can exceed the reward."""
    INTACT = "intact"
    LOST = "lost"
    UNKNOWN = "unknown"


@dataclass
class RewardRule:
    id: str = field(default_factory=lambda: _id("rr"))
    card_id: str = ""
    category: str = "base"
    rate: Decimal = Decimal("0.01")
    cap_amount: Optional[Money] = None       # eligible spend cap per period
    cap_period: str = "year"                 # year | quarter | month | statement | anniversary
    cap_used: Money = field(default_factory=lambda: Money.zero())
    requires_activation: bool = False
    activated: bool = True
    excluded_merchants: list[str] = field(default_factory=list)
    excluded_channels: list[str] = field(default_factory=list)
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    rule_version: str = "v1"
    provenance: Provenance = field(default_factory=Provenance)

    def cap_remaining(self) -> Optional[Money]:
        if self.cap_amount is None:
            return None
        return (self.cap_amount - self.cap_used).clamp_min_zero()


@dataclass
class CardBenefit:
    """RW05. Conditional attributes, never valued at an insurance face limit."""
    name: str = ""
    kind: str = "credit"              # credit | protection | access
    value: Optional[Money] = None
    remaining: Optional[Money] = None
    period: str = "year"
    expires: Optional[date] = None
    conditions: str = ""
    enrolled: bool = False


@dataclass
class Card:
    id: str = field(default_factory=lambda: _id("card"))
    account_id: str = ""
    nickname: str = ""
    product: str = ""
    variant: str = ""
    mask: str = "0000"
    issuer: str = ""
    purchase_apr: Decimal = Decimal("0.24")
    credit_limit: Money = field(default_factory=lambda: Money.zero())
    statement_balance: Money = field(default_factory=lambda: Money.zero())
    current_balance: Money = field(default_factory=lambda: Money.zero())
    statement_close_day: int = 1        # CR02: reported utilization snapshot
    payment_due_day: int = 25
    grace_state: GraceState = GraceState.INTACT
    annual_fee: Money = field(default_factory=lambda: Money.zero())
    annual_fee_month: int = 1
    foreign_transaction_fee: Decimal = Decimal("0")
    reward_currency: str = "cashback"
    point_value: Decimal = Decimal("0.01")
    rules: list[RewardRule] = field(default_factory=list)
    benefits: list[CardBenefit] = field(default_factory=list)
    excluded_by_user: bool = False
    entity_id: Optional[str] = None

    @property
    def utilization(self) -> Decimal:
        if self.credit_limit.is_zero:
            return Decimal("0")
        return self.current_balance.ratio(self.credit_limit)


# ---------------------------------------------------------------------------
# Recurring policies -- EX01, the heart of section 14
# ---------------------------------------------------------------------------

class PolicyMethod(str, Enum):
    FIXED = "fixed"
    PERCENT_OF_INCOME = "percent_of_income"
    TARGET_BALANCE = "target_balance"
    TARGET_BY_DATE = "target_by_date"
    SURPLUS_SHARE = "surplus_share"


class PercentBase(str, Enum):
    """Section 14: 'Percentages must name their denominator.'"""
    NET_PAYCHECK = "net_paycheck"
    MONTHLY_INCOME = "monthly_income"
    SURPLUS_AFTER_COMMITMENTS = "surplus_after_commitments"


class PolicyPurpose(str, Enum):
    REQUIRED_DEBT = "required_debt"
    EXTRA_PRINCIPAL = "extra_principal"
    CARD_STATEMENT = "card_statement"
    BILL = "bill"
    EMERGENCY_RESERVE = "emergency_reserve"
    ANNUAL_RESERVE = "annual_reserve"
    TAX_RESERVE = "tax_reserve"
    GOAL = "goal"
    INVESTMENT_CASH = "investment_cash"
    SPENDING = "spending"
    BUFFER = "buffer"


class AuthorizationMode(str, Enum):
    """Section 21, table 14."""
    EXPLORE = "explore"
    ONE_TIME = "one_time"
    SCHEDULED = "scheduled"
    STANDING = "standing"


@dataclass
class Mandate:
    """EX05. Signer authority, limits, amendment history, revocation."""
    id: str = field(default_factory=lambda: _id("mdt"))
    mode: AuthorizationMode = AuthorizationMode.EXPLORE
    authorized_at: Optional[datetime] = None
    authorized_by: str = ""
    entity_id: Optional[str] = None
    second_signer: Optional[str] = None
    per_run_cap: Optional[Money] = None
    period_cap: Optional[Money] = None
    notice_days: int = 0                # Reg E varying-amount notice; RBI: 1 day
    jurisdiction: str = "US"            # GX02: authorization is jurisdictional
    revoked_at: Optional[datetime] = None
    amendments: list[dict] = field(default_factory=list)

    # Cumulative usage against `period_cap`, tracked per calendar month of
    # the payment's scheduled date (`period_key` is "YYYY-MM"). Previously
    # `period_cap` was accepted on every mandate but never actually
    # enforced anywhere -- only `per_run_cap` was checked -- which is how a
    # one-time mandate with a $100 period cap could authorize two separate
    # $100 payments.
    used_this_period: Money = field(default_factory=lambda: Money.zero())
    period_key: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.authorized_at is not None and self.revoked_at is None

    def _period_usage(self, when: Optional[date]) -> Money:
        key = f"{when.year:04d}-{when.month:02d}" if when else None
        if key is not None and key == self.period_key:
            return self.used_this_period
        return Money.zero(self.used_this_period.currency)

    def authorizes(self, amount: Money, when: Optional[date] = None) -> tuple[bool, str]:
        if not self.active:
            return False, "no active authorization"
        if self.mode == AuthorizationMode.EXPLORE:
            return False, "explore mode cannot move money"
        if self.per_run_cap and amount > self.per_run_cap:
            return False, f"amount exceeds per-run cap of {self.per_run_cap}"
        if self.mode == AuthorizationMode.ONE_TIME and self.used_this_period.is_positive:
            return False, ("a one-time mandate authorizes a single payment; it has "
                           "already been used")
        if self.period_cap:
            already_used = self._period_usage(when)
            if already_used + amount > self.period_cap:
                return False, (f"amount would bring this period's total to "
                               f"{already_used + amount}, over the period cap of "
                               f"{self.period_cap}")
        return True, "authorized"

    def record_use(self, amount: Money, when: Optional[date] = None) -> None:
        """Call only once a payment has actually consumed this authorization
        (at execution/submission), never merely at preflight preview -- a
        preflight that is checked but never submitted must not consume the
        mandate's cap."""
        key = f"{when.year:04d}-{when.month:02d}" if when else None
        if key != self.period_key:
            self.used_this_period = Money.zero(amount.currency)
            self.period_key = key
        self.used_this_period = self.used_this_period + amount

    def release_use(self, amount: Money, when: Optional[date] = None) -> None:
        """Undo `record_use` for a payment that turned out never to have
        gone out (a provider rejection, or status recovery confirming no
        submission ever existed) -- mirrors how a reservation is released
        for the same cases."""
        key = f"{when.year:04d}-{when.month:02d}" if when else None
        if key == self.period_key:
            self.used_this_period = (self.used_this_period - amount).clamp_min_zero()


@dataclass
class RecurringPolicy:
    """EX01. The user's standing instruction for splitting income."""
    id: str = field(default_factory=lambda: _id("pol"))
    name: str = ""
    purpose: PolicyPurpose = PolicyPurpose.GOAL
    method: PolicyMethod = PolicyMethod.FIXED
    amount: Money = field(default_factory=lambda: Money.zero())
    percent: Decimal = Decimal("0")
    percent_base: PercentBase = PercentBase.NET_PAYCHECK
    target_balance: Optional[Money] = None
    target_date: Optional[date] = None
    monthly_target: Optional[Money] = None

    source_account_id: str = ""
    destination_account_id: str = ""
    destination_reserve_id: Optional[str] = None
    liability_id: Optional[str] = None

    schedule: Optional[Schedule] = None
    cadence: Cadence = Cadence.ON_INCOME
    eligible_income_source_ids: list[str] = field(default_factory=list)

    priority: int = 100                    # lower runs first
    min_remaining_balance: Money = field(default_factory=lambda: Money.zero())
    allow_overfunding: bool = False
    entity_id: Optional[str] = None
    mandate: Mandate = field(default_factory=Mandate)
    paused: bool = False
    skip_next: bool = False
    fee_limit: Optional[Money] = None

    # funded-to-date within the current monthly period (EX02/SC51)
    funded_this_period: Money = field(default_factory=lambda: Money.zero())
    bill_id: Optional[str] = None
    funded_period: Optional[str] = None
    funded_by_period: dict[str, Money] = field(default_factory=dict)
    skipped_dates: list[date] = field(default_factory=list)

    def funding_for_period(self, when: date, legacy_as_of: Optional[date] = None) -> Money:
        key = when.strftime("%Y-%m")
        if key in self.funded_by_period:
            return self.funded_by_period[key]
        legacy_key = self.funded_period or (
            legacy_as_of.strftime("%Y-%m") if legacy_as_of else None)
        return self.funded_this_period if key == legacy_key else Money.zero(self.amount.currency)

    def record_funding(self, amount: Money, when: date,
                       legacy_as_of: Optional[date] = None) -> None:
        key = when.strftime("%Y-%m")
        self.funded_by_period[key] = self.funding_for_period(when, legacy_as_of) + amount
        if self.funded_period is None or key >= self.funded_period:
            self.funded_period = key
            self.funded_this_period = self.funded_by_period[key]

    def reverse_funding(self, amount: Money, when: date,
                        legacy_as_of: Optional[date] = None) -> None:
        key = when.strftime("%Y-%m")
        self.funded_by_period[key] = (
            self.funding_for_period(when, legacy_as_of) - amount).clamp_min_zero()
        if self.funded_period == key or self.funded_period is None:
            self.funded_period = key
            self.funded_this_period = self.funded_by_period[key]

    @property
    def is_required(self) -> bool:
        return self.purpose in (PolicyPurpose.REQUIRED_DEBT,
                                PolicyPurpose.CARD_STATEMENT,
                                PolicyPurpose.BILL)

    @property
    def is_protected_reserve(self) -> bool:
        return self.purpose in (PolicyPurpose.EMERGENCY_RESERVE,
                                PolicyPurpose.ANNUAL_RESERVE,
                                PolicyPurpose.TAX_RESERVE,
                                PolicyPurpose.BUFFER)


# ---------------------------------------------------------------------------
# Household -- the aggregate root everything else hangs from
# ---------------------------------------------------------------------------

@dataclass
class Household:
    id: str = field(default_factory=lambda: _id("hh"))
    name: str = "Household"
    base_currency: str = "USD"
    jurisdiction: str = "US"
    members: list[str] = field(default_factory=list)
    entities: dict[str, Entity] = field(default_factory=dict)
    accounts: dict[str, Account] = field(default_factory=dict)
    liabilities: dict[str, Liability] = field(default_factory=dict)
    cards: dict[str, Card] = field(default_factory=dict)
    bills: dict[str, Bill] = field(default_factory=dict)
    income_sources: dict[str, IncomeSource] = field(default_factory=dict)
    income_events: list[IncomeEvent] = field(default_factory=list)
    reserves: dict[str, Reserve] = field(default_factory=dict)
    policies: dict[str, RecurringPolicy] = field(default_factory=dict)
    transactions: list[Transaction] = field(default_factory=list)
    consents: list[ConsentGrant] = field(default_factory=list)
    as_of: date = field(default_factory=date.today)
    bill_occurrences: dict[str, BillOccurrence] = field(default_factory=dict)
    # Hosted households follow the calendar; fixtures retain their explicit date.
    live_dates: bool = False
    # Assigned only at workspace creation; sample balances are fictional.
    payment_sandbox: bool = False

    def bill_occurrence(self, bill: Bill, when: date, *, persist: bool = False) -> BillOccurrence:
        key = bill.occurrence_id(when)
        occurrence = self.bill_occurrences.get(key)
        if occurrence is None:
            occurrence = BillOccurrence(
                id=key, bill_id=bill.id, due_date=when, amount=bill.amount,
                funded=Money.zero(bill.amount.currency), paid=Money.zero(bill.amount.currency))
            if persist:
                self.bill_occurrences[key] = occurrence
        return occurrence

    def bill_dates(self, bill: Bill, start: date, end: date) -> list[date]:
        dates = set(bill.schedule.occurrences(start, end) if bill.schedule else
                    ([bill.due_date] if start <= bill.due_date <= end else []))
        # A known, reopened obligation remains visible after its original due
        # date. It does not become a new recurring bill or disappear on return.
        dates.update(occ.due_date for occ in self.bill_occurrences.values()
                     if occ.bill_id == bill.id and occ.due_date < start
                     and occ.remaining.is_positive)
        return sorted(dates)

    def bill_for_policy(self, policy: RecurringPolicy) -> Optional[Bill]:
        if policy.purpose not in (PolicyPurpose.BILL, PolicyPurpose.REQUIRED_DEBT,
                                  PolicyPurpose.CARD_STATEMENT):
            return None
        if policy.bill_id:
            bill = self.bills.get(policy.bill_id)
            return bill if bill and bill.funding_account_id == policy.source_account_id else None
        matches = [bill for bill in self.bills.values()
                   if bill.funding_account_id == policy.source_account_id
                   and bill.payee_account_id
                   and bill.payee_account_id == policy.destination_account_id]
        return matches[0] if len(matches) == 1 else None

    def policy_trigger_dates(self, policy: RecurringPolicy, start: date, until: date) -> list[date]:
        if policy.schedule:
            return policy.schedule.occurrences(start, until)
        if policy.cadence != Cadence.ON_INCOME:
            return []
        dates: set[date] = set()
        source_ids = policy.eligible_income_source_ids or list(self.income_sources)
        for source_id in source_ids:
            source = self.income_sources.get(source_id)
            if source and source.schedule:
                dates.update(source.schedule.occurrences(start, until))
        for event in self.income_events:
            when = event.received_date or event.expected_date
            if start <= when <= until and event.source_id in source_ids:
                dates.add(when)
        return sorted(dates)

    def policy_skip_date(self, policy: RecurringPolicy) -> Optional[date]:
        if not policy.skip_next:
            return None
        if policy.skipped_dates:
            return max(policy.skipped_dates)
        # Compatibility for an older saved boolean: reads remain pure. New
        # commands persist the selected date with skip_policy_occurrence().
        dates = self.policy_trigger_dates(policy, self.as_of, self.as_of + timedelta(days=730))
        return dates[0] if dates else None

    def policy_skipped_on(self, policy: RecurringPolicy, when: date) -> bool:
        return when in policy.skipped_dates or self.policy_skip_date(policy) == when

    def skip_policy_occurrence(self, policy: RecurringPolicy) -> Optional[date]:
        dates = self.policy_trigger_dates(policy, self.as_of, self.as_of + timedelta(days=730))
        when = next((d for d in dates if d not in policy.skipped_dates), None)
        if when is not None:
            policy.skipped_dates.append(when)
            policy.skip_next = True
        return when

    # -- lookups ---------------------------------------------------------
    def account(self, aid: str) -> Account:
        return self.accounts[aid]

    def accounts_of(self, *types: AccountType) -> list[Account]:
        return [a for a in self.accounts.values() if a.type in types]

    @property
    def checking(self) -> list[Account]:
        return self.accounts_of(AccountType.CHECKING)

    @property
    def cash_accounts(self) -> list[Account]:
        return self.accounts_of(AccountType.CHECKING, AccountType.SAVINGS,
                                AccountType.MONEY_MARKET_DEPOSIT,
                                AccountType.CASH, AccountType.BROKERAGE_SWEEP)

    def total_cash(self, account_ids: Optional[set[str]] = None) -> Money:
        """`account_ids`, when given, restricts the total to that set of
        accounts -- a caller holding only a partial permission scope must
        pass its scope here rather than getting the whole household's cash
        by calling this with no argument."""
        t = Money.zero(self.base_currency)
        for a in self.cash_accounts:
            if account_ids is not None and a.id not in account_ids:
                continue
            if a.included_in_planning and a.currency == self.base_currency:
                t = t + a.available
        return t

    def total_debt(self, account_ids: Optional[set[str]] = None) -> Money:
        """`account_ids`, when given, restricts the total to liabilities
        whose linked account is in that set -- see `total_cash` above."""
        t = Money.zero(self.base_currency)
        for lia in self.liabilities.values():
            if account_ids is not None and lia.account_id not in account_ids:
                continue
            if lia.balance.currency == self.base_currency:
                t = t + lia.balance
        return t

    def protected_reserves(self, account_id: str | None = None,
                           account_ids: Optional[set[str]] = None) -> Money:
        """Money earmarked inside an account that the same dollar cannot also
        fund elsewhere (PL06). `account_id` narrows to one specific account's
        reserves; `account_ids`, when given, restricts to a permission scope
        (see `total_cash` above) -- the two can be combined but usually only
        one is used at a time."""
        t = Money.zero(self.base_currency)
        for r in self.reserves.values():
            if not r.protected:
                continue
            if account_id is not None and r.account_id != account_id:
                continue
            if account_ids is not None and r.account_id not in account_ids:
                continue
            t = t + r.funded
        return t

    def estimated_assets(self, account_ids: Optional[set[str]] = None) -> Money:
        """`account_ids`, when given, restricts the total to that set of
        accounts -- see `total_cash` above."""
        t = Money.zero(self.base_currency)
        for a in self.accounts.values():
            if account_ids is not None and a.id not in account_ids:
                continue
            if a.type == AccountType.ESTIMATED_ASSET:
                t = t + a.current
        return t

    def net_worth(self, account_ids: Optional[set[str]] = None) -> Money:
        """AC06. Contributions and transfers are not investment performance,
        and a credit limit is not an asset. Estimated assets are included but
        reported separately, never as verified balances. `account_ids`, when
        given, restricts both the asset side and the debt side to that set of
        accounts -- see `total_cash` above; a permission-scoped caller must
        pass its scope here rather than seeing the whole household's figure."""
        assets = Money.zero(self.base_currency)
        for a in self.accounts.values():
            if account_ids is not None and a.id not in account_ids:
                continue
            if a.type in (AccountType.CREDIT_CARD, AccountType.PERSONAL_LOAN,
                          AccountType.AUTO_LOAN, AccountType.STUDENT_LOAN,
                          AccountType.MORTGAGE, AccountType.SBLOC,
                          AccountType.MARGIN, AccountType.BNPL):
                continue
            if a.currency == self.base_currency:
                assets = assets + a.current
        return assets - self.total_debt(account_ids)

    def policies_sorted(self) -> list[RecurringPolicy]:
        return sorted(self.policies.values(), key=lambda p: (p.priority, p.name))
