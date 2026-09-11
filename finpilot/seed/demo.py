"""Demonstration households.

`demo_household()` is the fictional household from spec section 14: $3,000 on
the first and $3,000 on the fifteenth, a protected $1,000 checking floor, and a
$6,000 monthly plan whose deadlines make an even split infeasible.

All figures are fictional, as the source specification requires.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal as D

from ..dates import BusinessDayRule, Cadence, Schedule
from ..models import (Account, AccountType, Bill, BillOwner, Capability, Card,
                      CardBenefit, ConsentGrant, Entity, GraceState, Household,
                      IncomeEvent, IncomeSource, Liability, LiquidityTier,
                      Mandate, AuthorizationMode, OwnerType, PercentBase,
                      PolicyMethod, PolicyPurpose, ProtectionType, Provenance,
                      RateType, RecurringPolicy, Reserve, ReservePurpose,
                      RewardRule, Role, Verification, now)
from ..money import Money as M

TODAY = date(2026, 9, 10)


def _m(v) -> M:
    return M(D(str(v)), "USD")


def demo_household() -> Household:
    hh = Household(id="hh_demo", name="Rivera household", as_of=TODAY, payment_sandbox=True)

    person = Entity(id="ent_primary", name="A. Rivera", owner_type=OwnerType.INDIVIDUAL)
    partner = Entity(id="ent_partner", name="J. Rivera", owner_type=OwnerType.INDIVIDUAL)
    hh.entities = {person.id: person, partner.id: partner}
    hh.members = ["user_primary", "user_partner"]

    verified = Provenance(source="aggregation", as_of=TODAY,
                          verification=Verification.CONFIRMED)

    pay_caps = {Capability.VIEW_BALANCE, Capability.VIEW_TRANSACTIONS,
                Capability.VERIFY_OWNERSHIP, Capability.SEND_TRANSFER,
                Capability.RECEIVE_TRANSFER, Capability.PAY_BILLER}

    checking = Account(
        id="acc_checking", nickname="Everyday Checking", type=AccountType.CHECKING,
        entity_id=person.id, institution="First Meridian Bank",
        institution_id="bank_meridian", mask="4417",
        current=_m(3200), available=_m(3200), apy=D("0.0001"),
        minimum_balance=_m(0), liquidity_tier=LiquidityTier.IMMEDIATE,
        protection=ProtectionType.FDIC, ownership_category="joint",
        co_owners=[person.id, partner.id],
        capabilities=pay_caps, provenance=verified)

    savings = Account(
        id="acc_savings", nickname="Emergency & Annual Savings",
        type=AccountType.SAVINGS, entity_id=person.id,
        institution="First Meridian Bank", institution_id="bank_meridian",
        mask="9021", current=_m(14500), available=_m(14500), apy=D("0.0425"),
        withdrawal_limit_per_month=6, liquidity_tier=LiquidityTier.SAME_DAY,
        protection=ProtectionType.FDIC, ownership_category="joint",
        co_owners=[person.id, partner.id],
        capabilities={Capability.VIEW_BALANCE, Capability.SEND_TRANSFER,
                      Capability.RECEIVE_TRANSFER, Capability.VERIFY_OWNERSHIP},
        provenance=verified)

    brokerage = Account(
        id="acc_brokerage", nickname="Brokerage Cash", type=AccountType.BROKERAGE_SWEEP,
        entity_id=person.id, institution="Halden Securities",
        institution_id="brk_halden", mask="3388",
        current=_m(8200), available=_m(8200), apy=D("0.0380"),
        liquidity_tier=LiquidityTier.ONE_TO_THREE_DAYS,
        protection=ProtectionType.FDIC, ownership_category="single",
        sweep_allocations={"bank_pinnacle": _m(5200), "bank_coastal": _m(3000)},
        capabilities={Capability.VIEW_BALANCE, Capability.RECEIVE_TRANSFER},
        provenance=verified)

    retirement = Account(
        id="acc_401k", nickname="Employer 401(k)", type=AccountType.RETIREMENT,
        entity_id=person.id, institution="Halden Retirement",
        institution_id="brk_halden", mask="7742",
        current=_m(184000), available=_m(0),
        liquidity_tier=LiquidityTier.INVESTMENT, protection=ProtectionType.SIPC,
        capabilities={Capability.VIEW_BALANCE}, provenance=verified,
        # a demo connection-health scenario: the account is still visible from
        # its last successful sync, but the link itself needs attention.
        connection_healthy=False,
        connection_issue="Requires re-authentication with Halden Retirement",
        last_synced_at=datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc))

    # liability-side accounts
    card_a_acct = Account(id="acc_card_a", nickname="Everyday Rewards Card",
                          type=AccountType.CREDIT_CARD, entity_id=person.id,
                          institution="Meridian Card Services",
                          institution_id="bank_meridian", mask="2210",
                          current=_m(-742.18), available=_m(0),
                          capabilities={Capability.VIEW_BALANCE, Capability.PAY_BILLER,
                                        Capability.RECEIVE_TRANSFER},
                          provenance=verified)
    card_b_acct = Account(id="acc_card_b", nickname="Voyager Signature Card",
                          type=AccountType.CREDIT_CARD, entity_id=partner.id,
                          institution="Voyager Bank", institution_id="bank_voyager",
                          mask="8806", current=_m(-311.44), available=_m(0),
                          capabilities={Capability.VIEW_BALANCE, Capability.PAY_BILLER,
                                        Capability.RECEIVE_TRANSFER},
                          provenance=verified)
    mortgage_acct = Account(id="acc_mortgage", nickname="Home Mortgage",
                            type=AccountType.MORTGAGE, entity_id=person.id,
                            institution="Northgate Servicing", institution_id="srv_northgate",
                            mask="5510", current=_m(-180000),
                            capabilities={Capability.VIEW_BALANCE, Capability.PAY_BILLER,
                                          Capability.PRINCIPAL_ONLY},
                            provenance=verified)
    auto_acct = Account(id="acc_auto", nickname="Auto Loan", type=AccountType.AUTO_LOAN,
                        entity_id=person.id, institution="Meridian Auto Finance",
                        institution_id="bank_meridian", mask="6633",
                        current=_m(-12000),
                        capabilities={Capability.VIEW_BALANCE, Capability.PAY_BILLER},
                        provenance=verified)
    student_acct = Account(id="acc_student", nickname="Student Loan",
                           type=AccountType.STUDENT_LOAN, entity_id=partner.id,
                           institution="Federal Servicing Co", institution_id="srv_fed",
                           mask="1188", current=_m(-18400),
                           capabilities={Capability.VIEW_BALANCE, Capability.PAY_BILLER},
                           provenance=Provenance(source="manual", as_of=TODAY,
                                                 verification=Verification.USER_ENTERED))

    home = Account(
        id="acc_home", nickname="Primary residence (estimated)",
        type=AccountType.ESTIMATED_ASSET, entity_id=person.id,
        institution="User estimate", institution_id="", mask="----",
        current=_m(412000), available=_m(0),
        liquidity_tier=LiquidityTier.ESTIMATED, protection=ProtectionType.NONE,
        capabilities=set(),
        provenance=Provenance(source="user estimate", as_of=TODAY,
                              verification=Verification.ESTIMATED))

    for a in (checking, savings, brokerage, retirement, home, card_a_acct, card_b_acct,
              mortgage_acct, auto_acct, student_acct):
        hh.accounts[a.id] = a

    # ---- liabilities ---------------------------------------------------
    mortgage = Liability(
        id="lia_mortgage", account_id=mortgage_acct.id, name="Home mortgage",
        type=AccountType.MORTGAGE, balance=_m(180000), apr=D("0.045"),
        minimum_payment=_m("1138.77"), due_day=5, remaining_term_months=240,
        escrow=_m("661.23"), servicer_principal_only_supported=True,
        tax_deductible_interest=False,          # never assumed from the name
        entity_id=person.id, provenance=verified)
    auto = Liability(
        id="lia_auto", account_id=auto_acct.id, name="Auto loan",
        type=AccountType.AUTO_LOAN, balance=_m(12000), apr=D("0.06"),
        minimum_payment=_m(250), due_day=10, remaining_term_months=52,
        entity_id=person.id, provenance=verified)
    student = Liability(
        id="lia_student", account_id=student_acct.id, name="Student loan",
        type=AccountType.STUDENT_LOAN, balance=_m(18400), apr=D("0.068"),
        minimum_payment=_m(250), due_day=20, student_loan_program="unconfirmed",
        entity_id=partner.id,
        provenance=Provenance(source="manual", as_of=TODAY,
                              verification=Verification.USER_ENTERED))
    card_liab = Liability(
        id="lia_card_a", account_id=card_a_acct.id, name="Everyday Rewards Card",
        type=AccountType.CREDIT_CARD, balance=_m("742.18"), apr=D("0.2399"),
        minimum_payment=_m(35), due_day=12, entity_id=person.id, provenance=verified)
    for l in (mortgage, auto, student, card_liab):
        hh.liabilities[l.id] = l

    # ---- cards ---------------------------------------------------------
    card_a = Card(
        id="card_a", account_id=card_a_acct.id, nickname="Everyday Rewards",
        product="Meridian Everyday Rewards", variant="2024", mask="2210",
        issuer="Meridian", purchase_apr=D("0.2399"), credit_limit=_m(9000),
        statement_balance=_m("742.18"), current_balance=_m("742.18"),
        statement_close_day=18, payment_due_day=12, grace_state=GraceState.INTACT,
        annual_fee=_m(0), foreign_transaction_fee=D("0.03"),
        reward_currency="cashback", point_value=D("0.01"),
        rules=[
            RewardRule(card_id="card_a", category="dining", rate=D("0.04"),
                       cap_amount=_m(1500), cap_period="quarter", cap_used=_m(1310),
                       provenance=verified, rule_version="2026.03"),
            RewardRule(card_id="card_a", category="groceries", rate=D("0.03"),
                       cap_amount=_m(6000), cap_period="year", cap_used=_m(2100),
                       excluded_merchants=["Warehouse Club", "SuperMart"],
                       provenance=verified, rule_version="2026.03"),
            RewardRule(card_id="card_a", category="base", rate=D("0.01"),
                       provenance=verified, rule_version="2026.03"),
        ],
        benefits=[CardBenefit(name="Extended warranty", kind="protection",
                              conditions="Purchase must be paid in full with this "
                                         "card; claim within 90 days of the incident.",
                              enrolled=True)],
        entity_id=person.id)

    card_b = Card(
        id="card_b", account_id=card_b_acct.id, nickname="Voyager Signature",
        product="Voyager Signature", variant="2025", mask="8806", issuer="Voyager",
        purchase_apr=D("0.2149"), credit_limit=_m(15000),
        statement_balance=_m("311.44"), current_balance=_m("311.44"),
        statement_close_day=26, payment_due_day=20, grace_state=GraceState.INTACT,
        annual_fee=_m(95), annual_fee_month=4, foreign_transaction_fee=D("0"),
        reward_currency="points", point_value=D("0.012"),
        rules=[
            RewardRule(card_id="card_b", category="travel", rate=D("0.03"),
                       provenance=verified, rule_version="2026.01"),
            RewardRule(card_id="card_b", category="dining", rate=D("0.02"),
                       provenance=verified, rule_version="2026.01"),
            RewardRule(card_id="card_b", category="base", rate=D("0.01"),
                       provenance=verified, rule_version="2026.01"),
        ],
        benefits=[
            CardBenefit(name="Trip cancellation protection", kind="protection",
                        conditions="Conditional. Coverage limits, enrollment, payment "
                                   "requirements, exclusions and claim deadlines "
                                   "apply; no reimbursement is guaranteed.",
                        enrolled=True),
            CardBenefit(name="Annual travel credit", kind="credit", value=_m(120),
                        remaining=_m(45), period="year", expires=date(2026, 12, 31),
                        conditions="Applies only to eligible travel purchases you "
                                   "already intend to make.", enrolled=True),
        ],
        entity_id=partner.id)

    hh.cards = {card_a.id: card_a, card_b.id: card_b}

    # ---- income --------------------------------------------------------
    src = IncomeSource(
        id="inc_salary", name="Salary (semi-monthly)", net_amount=_m(3000),
        schedule=Schedule(Cadence.SEMIMONTHLY, TODAY.replace(day=1),
                          day_of_month=1, second_day_of_month=15,
                          business_day_rule=BusinessDayRule.PRECEDING),
        deposit_account_id=checking.id, reliability=D("1.0"),
        pretax_deductions={"401(k)": _m(450), "Health premium": _m(210)},
        employer_match_formula="100% of the first 3%, then 50% of the next 2%",
        entity_id=person.id)
    hh.income_sources[src.id] = src
    hh.income_events = [
        IncomeEvent(id="ie_sep01", source_id=src.id, expected_date=date(2026, 9, 1),
                    expected_amount=_m(3000), received_date=date(2026, 9, 1),
                    received_amount=_m(3000)),
        IncomeEvent(id="ie_sep15", source_id=src.id, expected_date=date(2026, 9, 15),
                    expected_amount=_m(3000)),
    ]

    # ---- bills ---------------------------------------------------------
    def bill(bid, name, amount, day, funding, payee=None, required=True,
             owner=BillOwner.APP, category="other", confirmed=True):
        return Bill(id=bid, name=name, amount=_m(amount),
                    due_date=date(2026, 9, day),
                    schedule=Schedule(Cadence.MONTHLY, date(2026, 9, day),
                                      day_of_month=day),
                    funding_account_id=funding, payee_account_id=payee,
                    required=required, category=category, execution_owner=owner,
                    amount_confirmed=confirmed, provenance=verified)

    bills = [
        bill("bill_mortgage", "Mortgage payment", 1800, 5, checking.id,
             mortgage_acct.id, category="housing"),
        bill("bill_utilities", "Utilities", 150, 8, checking.id, category="utilities"),
        bill("bill_auto", "Auto loan payment", 250, 10, checking.id, auto_acct.id,
             category="debt"),
        bill("bill_card", "Card statement", 600, 12, checking.id, card_a_acct.id,
             category="debt", owner=BillOwner.CREDITOR_AUTOPAY),
        bill("bill_student", "Student loan payment", 250, 20, checking.id,
             student_acct.id, category="debt"),
        bill("bill_insurance", "Home & auto insurance", 250, 22, checking.id,
             category="insurance"),
    ]
    for b in bills:
        hh.bills[b.id] = b

    # ---- reserves ------------------------------------------------------
    reserves = [
        Reserve(id="res_floor", name="Checking operating floor", account_id=checking.id,
                purpose=ReservePurpose.OPERATING_FLOOR, target=_m(1000),
                funded=_m(1000), protected=True),
        Reserve(id="res_emergency", name="Emergency reserve", account_id=savings.id,
                purpose=ReservePurpose.EMERGENCY, target=_m(18000), funded=_m(12400),
                protected=True),
        Reserve(id="res_annual", name="Annual bills reserve", account_id=savings.id,
                purpose=ReservePurpose.ANNUAL_BILL, target=_m(3600), funded=_m(2100),
                target_date=date(2027, 3, 1), protected=True),
    ]
    for r in reserves:
        hh.reserves[r.id] = r

    # ---- recurring policies -- the user's concurrent split --------------
    standing = Mandate(mode=AuthorizationMode.STANDING, authorized_at=now(),
                       authorized_by="user_primary", per_run_cap=_m(2500),
                       period_cap=_m(7000), notice_days=10, jurisdiction="US")

    def policy(pid, name, purpose, method, priority, dest, amount=None,
               liability=None, reserve=None, target_balance=None, percent=None,
               base=PercentBase.NET_PAYCHECK, source=None):
        return RecurringPolicy(
            id=pid, name=name, purpose=purpose, method=method,
            amount=_m(amount) if amount is not None else M.zero("USD"),
            monthly_target=_m(amount) if amount is not None else None,
            percent=D(str(percent)) if percent is not None else D("0"),
            percent_base=base,
            target_balance=_m(target_balance) if target_balance is not None else None,
            source_account_id=source or checking.id, destination_account_id=dest,
            destination_reserve_id=reserve, liability_id=liability,
            cadence=Cadence.ON_INCOME, priority=priority,
            eligible_income_source_ids=[src.id], entity_id=person.id,
            mandate=standing)

    policies = [
        # protected floor: a target-balance rule, already satisfied, so it asks
        # for nothing this month
        policy("pol_floor", "Checking operating floor", PolicyPurpose.BUFFER,
               PolicyMethod.TARGET_BALANCE, 1, checking.id,
               target_balance=1000, reserve="res_floor"),
        # required, deadline-driven
        policy("pol_mortgage", "Mortgage required payment", PolicyPurpose.REQUIRED_DEBT,
               PolicyMethod.FIXED, 10, mortgage_acct.id, 1800, liability="lia_mortgage"),
        policy("pol_auto", "Auto loan required payment", PolicyPurpose.REQUIRED_DEBT,
               PolicyMethod.FIXED, 11, auto_acct.id, 250, liability="lia_auto"),
        policy("pol_student", "Student loan required payment", PolicyPurpose.REQUIRED_DEBT,
               PolicyMethod.FIXED, 12, student_acct.id, 250, liability="lia_student"),
        policy("pol_card", "Credit card statement", PolicyPurpose.CARD_STATEMENT,
               PolicyMethod.FIXED, 13, card_a_acct.id, 600, liability="lia_card_a"),
        policy("pol_utilities", "Utilities", PolicyPurpose.BILL,
               PolicyMethod.FIXED, 20, checking.id, 150),
        policy("pol_insurance", "Home & auto insurance", PolicyPurpose.BILL,
               PolicyMethod.FIXED, 21, checking.id, 250),
        # protected reserves
        policy("pol_emergency", "Emergency reserve", PolicyPurpose.EMERGENCY_RESERVE,
               PolicyMethod.FIXED, 30, savings.id, 700, reserve="res_emergency"),
        policy("pol_annual", "Annual bills reserve", PolicyPurpose.ANNUAL_RESERVE,
               PolicyMethod.FIXED, 31, savings.id, 300, reserve="res_annual"),
        # optional, in the user's saved priority order
        policy("pol_spending", "Cash & debit spending", PolicyPurpose.SPENDING,
               PolicyMethod.FIXED, 25, checking.id, 700),
        policy("pol_brokerage", "Brokerage cash contribution",
               PolicyPurpose.INVESTMENT_CASH, PolicyMethod.FIXED, 60,
               brokerage.id, 500),
        policy("pol_extra", "Additional principal on selected debt",
               PolicyPurpose.EXTRA_PRINCIPAL, PolicyMethod.FIXED, 70,
               auto_acct.id, 500, liability="lia_auto"),
    ]
    for p in policies:
        hh.policies[p.id] = p

    # policy due dates come from the attached bills / liabilities
    hh.bills["bill_utilities"].payee_account_id = None
    hh.policies["pol_utilities"].schedule = Schedule(
        Cadence.MONTHLY, date(2026, 9, 8), day_of_month=8)
    hh.policies["pol_insurance"].schedule = Schedule(
        Cadence.MONTHLY, date(2026, 9, 22), day_of_month=22)

    hh.consents = [
        ConsentGrant(grantor_user="user_primary", grantee_user="user_partner",
                     role=Role.REVIEWER,
                     account_ids=[checking.id, savings.id, mortgage_acct.id],
                     can_execute=False),
    ]
    return hh


# ---------------------------------------------------------------------------
# HNW household -- exercises entities, coverage and collateral
# ---------------------------------------------------------------------------

def hnw_household() -> Household:
    hh = Household(id="hh_hnw", name="Calloway family", as_of=TODAY)
    individual = Entity(id="ent_ind", name="M. Calloway", owner_type=OwnerType.INDIVIDUAL)
    trust = Entity(id="ent_trust", name="Calloway Family Trust", owner_type=OwnerType.TRUST,
                   beneficiaries=["b1", "b2", "b3"], signers=["M. Calloway", "R. Vance"],
                   second_signer_threshold=M(D("100000"), "USD"))
    llc = Entity(id="ent_llc", name="Calloway Holdings LLC", owner_type=OwnerType.LLC,
                 signers=["M. Calloway"])
    hh.entities = {e.id: e for e in (individual, trust, llc)}

    prov = Provenance(source="aggregation", as_of=TODAY,
                      verification=Verification.CONFIRMED)
    accounts = [
        Account(id="a_pinnacle_chk", nickname="Pinnacle Checking",
                type=AccountType.CHECKING, entity_id=individual.id,
                institution="Pinnacle Bank", institution_id="bank_pinnacle",
                mask="1001", current=M(D("180000"), "USD"), available=M(D("180000"), "USD"),
                protection=ProtectionType.FDIC, ownership_category="single",
                capabilities={Capability.VIEW_BALANCE, Capability.SEND_TRANSFER},
                provenance=prov),
        Account(id="a_sweep", nickname="Halden Brokerage Sweep",
                type=AccountType.BROKERAGE_SWEEP, entity_id=individual.id,
                institution="Halden Securities", institution_id="brk_halden",
                mask="2002", current=M(D("90000"), "USD"), available=M(D("90000"), "USD"),
                protection=ProtectionType.FDIC, ownership_category="single",
                sweep_allocations={"bank_pinnacle": M(D("90000"), "USD")},
                capabilities={Capability.VIEW_BALANCE}, provenance=prov),
        Account(id="a_coastal_sav", nickname="Coastal Savings",
                type=AccountType.SAVINGS, entity_id=individual.id,
                institution="Coastal Bank", institution_id="bank_coastal",
                mask="3003", current=M(D("200000"), "USD"), available=M(D("200000"), "USD"),
                apy=D("0.0440"), protection=ProtectionType.FDIC,
                ownership_category="single",
                capabilities={Capability.VIEW_BALANCE, Capability.RECEIVE_TRANSFER,
                              Capability.SEND_TRANSFER}, provenance=prov),
        Account(id="a_trust_cu", nickname="Trust share account",
                type=AccountType.SAVINGS, entity_id=trust.id,
                institution="Harbor Credit Union", institution_id="cu_harbor",
                mask="4004", current=M(D("900000"), "USD"), available=M(D("900000"), "USD"),
                protection=ProtectionType.NCUA, ownership_category="trust",
                capabilities={Capability.VIEW_BALANCE}, provenance=prov),
        Account(id="a_llc_op", nickname="LLC operating account",
                type=AccountType.CHECKING, entity_id=llc.id,
                institution="Pinnacle Bank", institution_id="bank_pinnacle",
                mask="5005", current=M(D("240000"), "USD"), available=M(D("240000"), "USD"),
                protection=ProtectionType.FDIC, ownership_category="business",
                capabilities={Capability.VIEW_BALANCE, Capability.SEND_TRANSFER},
                provenance=prov),
        Account(id="a_sbloc", nickname="Securities-backed line",
                type=AccountType.SBLOC, entity_id=individual.id,
                institution="Halden Lending", institution_id="brk_halden",
                mask="6006", current=M(D("-350000"), "USD"),
                liquidity_tier=LiquidityTier.CONTINGENT_BORROWING,
                protection=ProtectionType.NONE,
                capabilities={Capability.VIEW_BALANCE}, provenance=prov),
    ]
    for a in accounts:
        hh.accounts[a.id] = a
    return hh


# ---------------------------------------------------------------------------
# Worked-example fixtures straight from the specification
# ---------------------------------------------------------------------------

def section8_debts():
    from ..engine.debt import DebtSnapshot
    return [
        DebtSnapshot("card", "Credit card", _m(8000), D("0.24"), _m(240)),
        DebtSnapshot("personal", "Personal loan", _m(2000), D("0.08"), _m(100)),
        DebtSnapshot("auto", "Auto loan", _m(12000), D("0.06"), _m(300)),
        DebtSnapshot("mortgage", "Mortgage", _m(180000), D("0.045"), _m("1138.77")),
    ]


SECTION8_BUDGET = _m("2278.77")

# Display names for institutions that appear only as sweep-program allocations.
SWEEP_BANK_NAMES = {
    "bank_pinnacle": "Pinnacle Bank (via sweep)",
    "bank_coastal": "Coastal Bank (via sweep)",
    "cu_harbor": "Harbor Credit Union",
    "brk_halden": "Halden Securities",
}
