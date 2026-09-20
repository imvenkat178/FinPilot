"""Validated workspace edits, independent from HTTP and persistence.

A caller supplies its authorized household and commits the successful mutation in
one repository transaction. No browser payload can select another household.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum

from ..dates import Cadence, Schedule, DayRule, BusinessDayRule
from ..engine.tax import TaxProfile
from ..models import (
    Account, AccountType, Bill, BillOwner, Capability, Card, GraceState,
    Household, IncomeSource, IncomeEvent, Liability, LiquidityTier, Mandate,
    PolicyMethod, PolicyPurpose, PercentBase, ProtectionType, Provenance,
    RateType, RecurringPolicy, Reserve, ReservePurpose, Transaction, TxKind,
    TxState, Verification,
)
from ..money import Money

DEBT_TYPES = {AccountType.CREDIT_CARD, AccountType.PERSONAL_LOAN,
              AccountType.AUTO_LOAN, AccountType.STUDENT_LOAN, AccountType.MORTGAGE,
              AccountType.BNPL, AccountType.SBLOC, AccountType.MARGIN}
CASH_TYPES = {AccountType.CHECKING, AccountType.SAVINGS, AccountType.CASH,
              AccountType.MONEY_MARKET_DEPOSIT, AccountType.BROKERAGE_SWEEP}
MAX_CSV_ROWS = 5000
MAX_CSV_BYTES = 2_000_000


def _text(value, label, maximum=160, required=True):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        raise ValueError(f"{label} must contain {'1' if required else '0'}–{maximum} characters")
    return value


def _decimal(value, label, minimum=Decimal('0'), maximum=Decimal('1000000000000000')):
    if isinstance(value, (bool, float)) or value is None:
        raise ValueError(f"{label} must be a decimal string")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} must be a valid decimal") from None
    if not result.is_finite() or result < minimum or result > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return result


def _integer(value, label, minimum, maximum):
    number = _decimal(value, label, Decimal(minimum), Decimal(maximum))
    if number != number.to_integral_value():
        raise ValueError(f"{label} must be a whole number")
    return int(number)


def _boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false")
    return value


def _date(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} must use YYYY-MM-DD") from None
    if not 1900 <= result.year <= 2200:
        raise ValueError(f"{label} must be between 1900 and 2200")
    return result


def _fields(payload, allowed):
    if not isinstance(payload, dict):
        raise ValueError('The request must be an object')
    unknown = set(payload) - set(allowed)
    if unknown:
        raise ValueError('Unsupported fields: ' + ', '.join(sorted(unknown)))


class WorkspaceService:
    def __init__(self, household: Household, tax: TaxProfile):
        self.hh = household
        self.tax = tax

    def _money(self, value, label='Amount', minimum=Decimal('0')):
        amount = _decimal(value, label, minimum)
        money = Money(amount, self.hh.base_currency)
        if money.round().amount != amount:
            raise ValueError(f'{label} has too many decimal places for {self.hh.base_currency}')
        return money

    def _account(self, record_id, cash=False):
        if not isinstance(record_id, str):
            raise ValueError('Select an account in this workspace')
        account = self.hh.accounts.get(record_id)
        if account is None:
            raise ValueError('Select an account in this workspace')
        if account.currency != self.hh.base_currency:
            raise ValueError('This account currency differs from the workspace currency')
        if cash and account.type not in CASH_TYPES:
            raise ValueError('Select a cash account for funding')
        return account

    def _record(self, collection, record_id, factory):
        if record_id is None:
            return factory()
        if record_id not in collection:
            raise KeyError(record_id)
        return copy.deepcopy(collection[record_id])

    def _schedule(self, payload, anchor, default='once'):
        cadence = Cadence(payload.get('cadence', default))
        if cadence in (Cadence.ON_INCOME, Cadence.ON_DEPENDENCY):
            return None
        first = _integer(payload.get('day_of_month', anchor.day), 'Day of month', 1, 31)
        second = payload.get('second_day_of_month')
        second = _integer(second or 15, 'Second day', 1, 31) if cadence == Cadence.SEMIMONTHLY else None
        if second == first:
            raise ValueError('Choose two different days for twice-monthly schedules')
        end = _date(payload['end'], 'Schedule end') if payload.get('end') else None
        if end and end < anchor:
            raise ValueError('Schedule end must not precede its start')
        return Schedule(cadence=cadence, anchor=anchor, day_of_month=first,
                        second_day_of_month=second,
                        day_rule=DayRule(payload.get('day_rule', 'last_day_of_month')),
                        business_day_rule=BusinessDayRule(payload.get('business_day_rule', 'none')),
                        end=end)

    def upsert(self, kind: str, payload: dict, record_id: str | None = None) -> dict:
        handlers = {'accounts': self._accounts, 'income': self._income,
                    'bills': self._bills, 'reserves': self._reserves,
                    'policies': self._policies, 'tax': self._tax}
        if kind not in handlers:
            raise ValueError('Unknown workspace record type')
        if not isinstance(payload, dict):
            raise ValueError('The request must be an object')
        result = handlers[kind](payload, record_id)
        return {'kind': kind, **result}

    def _accounts(self, p, record_id):
        _fields(p, {'nickname','type','institution','mask','currency','current','available',
                    'included_in_planning','apy','minimum_balance','monthly_fee','as_of',
                    'apr','minimum_payment','due_day','remaining_term_months','escrow',
                    'mortgage_insurance','credit_limit','statement_balance','statement_close_day',
                    'payment_due_day','grace_state','annual_fee','foreign_transaction_fee'})
        a = self._record(self.hh.accounts, record_id, Account)
        old_type = a.type
        a.type = AccountType(p.get('type', a.type))
        if record_id and a.type != old_type:
            raise ValueError('Account type cannot change; create another account instead')
        a.nickname = _text(p.get('nickname', a.nickname), 'Account name')
        a.institution = _text(p.get('institution', a.institution), 'Institution', required=False)
        a.mask = _text(p.get('mask', a.mask), 'Account ending', 4, required=False)
        if ('mask' in p or not record_id) and a.mask and not a.mask.isdigit():
            raise ValueError('Account ending must contain up to four digits')
        a.currency = p.get('currency', self.hh.base_currency)
        if a.currency != self.hh.base_currency:
            raise ValueError(f'Use the workspace currency, {self.hh.base_currency}')
        if 'current' in p:
            current = self._money(p['current'], 'Balance', Decimal('-1000000000000000'))
            a.current = current
            if a.type in DEBT_TYPES:
                a.current = Money(-abs(current.amount), a.currency)
        elif not record_id:
            a.current = Money.zero(a.currency)
        if a.type in CASH_TYPES:
            a.available = self._money(p.get('available', a.current.amount if not record_id else a.available.amount), 'Available balance', Decimal('-1000000000000000'))
        else:
            a.available = Money.zero(a.currency)
        for field in ('pending','reserved','held'):
            if not record_id:
                setattr(a, field, Money.zero(a.currency))
        for field in ('minimum_balance','monthly_fee'):
            if field in p:
                setattr(a, field, self._money(p[field], field.replace('_',' ')))
        if 'apy' in p:
            a.apy = _decimal(p['apy'], 'APY', maximum=Decimal('1'))
        if 'included_in_planning' in p:
            a.included_in_planning = _boolean(p['included_in_planning'], 'Include in planning')
        if not record_id:
            a.capabilities = {Capability.VIEW_BALANCE, Capability.VIEW_TRANSACTIONS}
            a.protection = ProtectionType.UNRESOLVED
            a.liquidity_tier = (LiquidityTier.IMMEDIATE if a.type in CASH_TYPES else
                                LiquidityTier.ESTIMATED if a.type == AccountType.ESTIMATED_ASSET else
                                LiquidityTier.CONTINGENT_BORROWING if a.type in DEBT_TYPES else
                                LiquidityTier.INVESTMENT)
        a.provenance = Provenance(source='manual', as_of=_date(p.get('as_of', self.hh.as_of.isoformat()), 'Balance date'),
                                  verification=Verification.ESTIMATED if a.type == AccountType.ESTIMATED_ASSET else Verification.USER_ENTERED)
        funded = sum((r.funded.amount for r in self.hh.reserves.values() if r.account_id == a.id), Decimal('0'))
        if a.type in CASH_TYPES and funded > max(Decimal('0'), (a.available - a.reserved).amount):
            raise ValueError('Available balance cannot fall below funds assigned to savings goals')
        loan = None
        card = None
        if a.type in DEBT_TYPES:
            existing = next((l for l in self.hh.liabilities.values() if l.account_id == a.id), None)
            loan = copy.deepcopy(existing) if existing else Liability(account_id=a.id, type=a.type)
            loan.name, loan.balance, loan.provenance = a.nickname, Money(abs(a.current.amount), a.currency), copy.deepcopy(a.provenance)
            for field in ('apr',):
                if field in p:
                    setattr(loan, field, _decimal(p[field], 'APR', maximum=Decimal('1')))
            for field in ('minimum_payment','escrow','mortgage_insurance'):
                if field in p:
                    setattr(loan, field, self._money(p[field], field.replace('_',' ')))
            if 'due_day' in p:
                loan.due_day = _integer(p['due_day'], 'Payment due day', 1, 31)
            if 'remaining_term_months' in p:
                loan.remaining_term_months = _integer(p['remaining_term_months'], 'Remaining months', 1, 1200) if p['remaining_term_months'] else None
            if 'apr' in p and 'minimum_payment' in p and loan.minimum_payment.is_positive:
                loan.terms_complete = True
            if a.type == AccountType.CREDIT_CARD:
                existing_card = next((c for c in self.hh.cards.values() if c.account_id == a.id), None)
                card = copy.deepcopy(existing_card) if existing_card else Card(account_id=a.id, grace_state=GraceState.UNKNOWN, purchase_apr=loan.apr)
                card.nickname, card.issuer, card.mask = a.nickname, a.institution, a.mask
                card.current_balance = loan.balance
                if 'apr' in p:
                    card.purchase_apr = loan.apr
                for field in ('credit_limit','statement_balance','annual_fee'):
                    if field in p:
                        setattr(card, field, self._money(p[field], field.replace('_',' ')))
                for field in ('statement_close_day','payment_due_day'):
                    if field in p:
                        setattr(card, field, _integer(p[field], field.replace('_',' '), 1, 31))
                if 'payment_due_day' in p:
                    loan.due_day = card.payment_due_day
                if 'grace_state' in p:
                    card.grace_state = GraceState(p['grace_state'])
                if 'foreign_transaction_fee' in p:
                    card.foreign_transaction_fee = _decimal(p['foreign_transaction_fee'], 'Foreign transaction fee', maximum=Decimal('1'))
        self.hh.accounts[a.id] = a
        if loan:
            self.hh.liabilities[loan.id] = loan
        if card:
            self.hh.cards[card.id] = card
        return {'id': a.id, 'name': a.nickname}

    def _income(self, p, record_id):
        record_type = p.get('record_type', 'source')
        if record_type == 'event':
            _fields(p, {'record_type','source_id','expected_date','expected_amount','received_date','received_amount'})
            collection = {e.id: e for e in self.hh.income_events}
            event = self._record(collection, record_id, IncomeEvent)
            event.source_id = p.get('source_id', event.source_id)
            if not isinstance(event.source_id, str) or event.source_id not in self.hh.income_sources:
                raise ValueError('Select an income source in this workspace')
            event.expected_date = _date(p.get('expected_date', event.expected_date.isoformat()), 'Expected date')
            event.expected_amount = self._money(p.get('expected_amount', event.expected_amount.amount), 'Expected income')
            if 'received_amount' in p:
                event.received_amount = self._money(p['received_amount'], 'Received income') if p['received_amount'] not in ('', None) else None
                event.received_date = _date(p.get('received_date') or event.expected_date.isoformat(), 'Received date') if event.received_amount is not None else None
            if any(e.id != event.id and e.source_id == event.source_id and e.expected_date == event.expected_date for e in self.hh.income_events):
                raise ValueError('An income event already exists for this source and date')
            self.hh.income_events = [event if e.id == event.id else e for e in self.hh.income_events]
            if record_id is None:
                self.hh.income_events.append(event)
            return {'id': event.id, 'record_type': 'event'}
        if record_type != 'source':
            raise ValueError('Income record_type must be source or event')
        _fields(p, {'record_type','name','net_amount','deposit_account_id','is_variable',
                    'reliability','next_date','cadence','day_of_month','second_day_of_month',
                    'day_rule','business_day_rule','end','generate_events'})
        source = self._record(self.hh.income_sources, record_id, IncomeSource)
        source.name = _text(p.get('name', source.name), 'Income source')
        source.deposit_account_id = p.get('deposit_account_id', source.deposit_account_id)
        self._account(source.deposit_account_id, cash=True)
        source.net_amount = self._money(p.get('net_amount', source.net_amount.amount), 'Net income')
        if 'is_variable' in p:
            source.is_variable = _boolean(p['is_variable'], 'Variable income')
        if 'reliability' in p:
            source.reliability = _decimal(p['reliability'], 'Reliability', maximum=Decimal('1'))
        if source.net_amount.is_zero:
            raise ValueError('Net income must be greater than zero')
        anchor = _date(p.get('next_date', source.schedule.anchor.isoformat() if source.schedule else self.hh.as_of.isoformat()), 'Next income date')
        schedule_fields = {'cadence','day_of_month','second_day_of_month','day_rule','business_day_rule','end','next_date'}
        if not record_id or source.schedule is None or schedule_fields.intersection(p):
            source.schedule = self._schedule(p, anchor, source.schedule.cadence.value if source.schedule else 'monthly')
            if source.schedule is None:
                raise ValueError('Income requires a calendar schedule')
        generate = _boolean(p.get('generate_events', True), 'Generate events')
        events = list(self.hh.income_events)
        if generate:
            # Preserve received history. Recreate only future expectations for the edited source.
            events = [e for e in events if e.source_id != source.id or e.is_received or e.expected_date < self.hh.as_of]
            existing = {(e.source_id, e.expected_date) for e in events}
            horizon = self.hh.as_of + timedelta(days=366)
            for when in source.schedule.occurrences(max(anchor, self.hh.as_of), horizon):
                if (source.id, when) not in existing:
                    events.append(IncomeEvent(source_id=source.id, expected_date=when, expected_amount=source.net_amount))
        self.hh.income_sources[source.id] = source
        self.hh.income_events = sorted(events, key=lambda e: (e.expected_date, e.id))
        return {'id': source.id, 'name': source.name, 'record_type': 'source',
                'events': sum(e.source_id == source.id for e in events)}

    def _bills(self, p, record_id):
        _fields(p, {'name','amount','due_date','funding_account_id','payee_account_id',
                    'required','category','execution_owner','amount_confirmed','autopay_confirmed',
                    'cadence','day_of_month','second_day_of_month','day_rule','business_day_rule','end'})
        bill = self._record(self.hh.bills, record_id, Bill)
        bill.name = _text(p.get('name', bill.name), 'Bill name')
        bill.amount = self._money(p.get('amount', bill.amount.amount), 'Bill amount')
        if bill.amount.is_zero:
            raise ValueError('Bill amount must be greater than zero')
        bill.due_date = _date(p.get('due_date', bill.due_date.isoformat()), 'Due date')
        bill.funding_account_id = p.get('funding_account_id', bill.funding_account_id)
        self._account(bill.funding_account_id, cash=True)
        if 'payee_account_id' in p:
            bill.payee_account_id = p['payee_account_id'] or None
        if bill.payee_account_id:
            self._account(bill.payee_account_id)
            if bill.payee_account_id == bill.funding_account_id:
                raise ValueError('Funding and payee accounts must be different')
        bill.category = _text(p.get('category', bill.category), 'Category', 80)
        bill.execution_owner = BillOwner(p.get('execution_owner', bill.execution_owner))
        for field in ('required','amount_confirmed','autopay_confirmed'):
            if field in p:
                setattr(bill, field, _boolean(p[field], field.replace('_',' ')))
        if not record_id or {'cadence','due_date','day_of_month','second_day_of_month','day_rule','business_day_rule','end'}.intersection(p):
            bill.schedule = self._schedule(p, bill.due_date, bill.schedule.cadence.value if bill.schedule else 'once')
            if bill.schedule is None:
                raise ValueError('Bills require a calendar schedule')
        bill.provenance = Provenance(source='manual', as_of=self.hh.as_of, verification=Verification.USER_ENTERED)
        self.hh.bills[bill.id] = bill
        return {'id': bill.id, 'name': bill.name,
                'note': 'Bill saved. Review any recurring allocation rule separately; no payment authority changed.'}

    def _reserves(self, p, record_id):
        _fields(p, {'name','account_id','purpose','target','funded','target_date','protected'})
        reserve = self._record(self.hh.reserves, record_id, Reserve)
        reserve.name = _text(p.get('name', reserve.name), 'Goal name')
        reserve.account_id = p.get('account_id', reserve.account_id)
        account = self._account(reserve.account_id, cash=True)
        reserve.purpose = ReservePurpose(p.get('purpose', reserve.purpose))
        reserve.target = self._money(p.get('target', reserve.target.amount), 'Goal target')
        reserve.funded = self._money(p.get('funded', reserve.funded.amount), 'Funds assigned')
        if reserve.target.is_zero:
            raise ValueError('Goal target must be greater than zero')
        if reserve.funded > reserve.target:
            raise ValueError('Funds assigned cannot exceed the goal target')
        if 'target_date' in p:
            reserve.target_date = _date(p['target_date'], 'Target date') if p['target_date'] else None
        if 'protected' in p:
            reserve.protected = _boolean(p['protected'], 'Protected reserve')
        funded = sum((r.funded.amount for r in self.hh.reserves.values() if r.account_id == account.id and r.id != reserve.id), Decimal('0'))
        if funded + reserve.funded.amount > max(Decimal('0'), account.spendable.amount):
            raise ValueError('Combined goal funding exceeds the available unreserved balance in this account')
        self.hh.reserves[reserve.id] = reserve
        return {'id': reserve.id, 'name': reserve.name}

    def _policies(self, p, record_id):
        _fields(p, {'name','purpose','method','amount','monthly_target','percent','percent_base',
                    'target_balance','target_date','source_account_id','destination_account_id',
                    'destination_reserve_id','liability_id','bill_id','priority','paused','min_remaining_balance',
                    'allow_overfunding','cadence','anchor','day_of_month','second_day_of_month',
                    'day_rule','business_day_rule','end','eligible_income_source_ids','fee_limit'})
        rule = self._record(self.hh.policies, record_id, RecurringPolicy)
        rule.name = _text(p.get('name', rule.name), 'Rule name')
        rule.purpose = PolicyPurpose(p.get('purpose', rule.purpose))
        rule.method = PolicyMethod(p.get('method', rule.method))
        rule.source_account_id = p.get('source_account_id', rule.source_account_id)
        self._account(rule.source_account_id, cash=True)
        rule.destination_account_id = p.get('destination_account_id', rule.destination_account_id)
        destination = self._account(rule.destination_account_id)
        rule.priority = _integer(p.get('priority', rule.priority), 'Priority', 1, 10000)
        for field in ('amount','monthly_target','target_balance','min_remaining_balance','fee_limit'):
            if field in p:
                setattr(rule, field, self._money(p[field], field.replace('_',' ')) if p[field] not in ('', None) else (Money.zero(self.hh.base_currency) if field in ('amount','min_remaining_balance') else None))
        if 'percent' in p:
            rule.percent = _decimal(p['percent'], 'Percentage', maximum=Decimal('1'))
        rule.percent_base = PercentBase(p.get('percent_base', rule.percent_base))
        if 'target_date' in p:
            rule.target_date = _date(p['target_date'], 'Target date') if p['target_date'] else None
        for field in ('paused','allow_overfunding'):
            if field in p:
                setattr(rule, field, _boolean(p[field], field.replace('_',' ')))
        for field, collection in (('destination_reserve_id', self.hh.reserves), ('liability_id', self.hh.liabilities)):
            if field in p:
                setattr(rule, field, p[field] or None)
            target = getattr(rule, field)
            if target and (not isinstance(target, str) or target not in collection):
                raise ValueError('Selected goal or liability does not exist in this workspace')
            if target and collection[target].account_id != destination.id:
                raise ValueError('Selected goal or liability belongs to a different destination account')
        if 'bill_id' in p:
            rule.bill_id = p['bill_id'] or None
        bound_bill = getattr(rule, 'bill_id', None)
        if bound_bill:
            if not isinstance(bound_bill, str) or bound_bill not in self.hh.bills:
                raise ValueError('Select a bill in this workspace')
            bill = self.hh.bills[bound_bill]
            if bill.funding_account_id != rule.source_account_id:
                raise ValueError('The rule funding account must match its linked bill')
            if bill.payee_account_id and bill.payee_account_id != rule.destination_account_id:
                raise ValueError('The destination must match the linked bill payee')
        if 'eligible_income_source_ids' in p:
            ids = p['eligible_income_source_ids']
            if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(i, str) or i not in self.hh.income_sources for i in ids):
                raise ValueError('Select valid eligible income sources')
            rule.eligible_income_source_ids = list(dict.fromkeys(ids))
        if rule.method == PolicyMethod.FIXED and not (rule.monthly_target or rule.amount).is_positive:
            raise ValueError('A fixed rule requires a positive amount')
        if rule.method in (PolicyMethod.PERCENT_OF_INCOME, PolicyMethod.SURPLUS_SHARE) and rule.percent <= 0:
            raise ValueError('A percentage rule requires a percentage above zero')
        if rule.method in (PolicyMethod.TARGET_BALANCE, PolicyMethod.TARGET_BY_DATE) and (rule.target_balance is None or not rule.target_balance.is_positive):
            raise ValueError('A target rule requires a positive target balance')
        if rule.method == PolicyMethod.TARGET_BY_DATE and (not rule.target_date or rule.target_date <= self.hh.as_of):
            raise ValueError('Target-by-date rules require a future target date')
        if rule.method == PolicyMethod.SURPLUS_SHARE:
            rule.percent_base = PercentBase.SURPLUS_AFTER_COMMITMENTS
        if not record_id or {'cadence','anchor','day_of_month','second_day_of_month','day_rule','business_day_rule','end'}.intersection(p):
            rule.cadence = Cadence(p.get('cadence', rule.cadence))
            if rule.cadence == Cadence.ON_DEPENDENCY:
                raise ValueError('Dependency-driven rules require an execution dependency and cannot be created here')
            rule.schedule = self._schedule(p, _date(p.get('anchor', rule.schedule.anchor.isoformat() if rule.schedule else self.hh.as_of.isoformat()), 'Rule start'), rule.cadence.value)
        # Material edits require fresh authorization; never widen an existing mandate.
        if not record_id or set(p) - {'name','paused'}:
            rule.mandate = Mandate()
        self.hh.policies[rule.id] = rule
        return {'id': rule.id, 'name': rule.name, 'authorization_required': not rule.mandate.active}

    def _tax(self, p, record_id):
        _fields(p, {'tax_year','state','filing_status','federal_marginal','state_marginal',
                    'itemizes','niit_applies','niit_rate'})
        profile = copy.deepcopy(self.tax)
        profile.tax_year = _integer(p.get('tax_year', profile.tax_year), 'Tax year', 2000, 2200)
        profile.state = _text(p.get('state', profile.state), 'State or region', 80, required=False)
        profile.filing_status = _text(p.get('filing_status', profile.filing_status), 'Filing status', 60)
        for field in ('federal_marginal','state_marginal','niit_rate'):
            if field in p:
                setattr(profile, field, _decimal(p[field], field.replace('_',' '), maximum=Decimal('1')))
        for field in ('itemizes','niit_applies'):
            if field in p:
                setattr(profile, field, _boolean(p[field], field.replace('_',' ')))
        if profile.combined_marginal > 1:
            raise ValueError('Combined marginal rates cannot exceed 100%')
        profile.verified = False
        self.tax.__dict__.update(profile.__dict__)
        return {'id': 'profile', 'profile': profile.to_json()}

    @staticmethod
    def _transaction_json(tx):
        return {'id': tx.id, 'account_id': tx.account_id, 'date': tx.date.isoformat(),
                'description': tx.description, 'merchant': tx.merchant,
                'amount': tx.amount.to_json(), 'category': tx.category,
                'kind': tx.kind.value, 'state': tx.state.value,
                'user_corrected': tx.user_corrected,
                'counts_as_income': tx.counts_as_income,
                'counts_as_spending': tx.counts_as_spending}

    def _csv_rows(self, p):
        import re
        _fields(p, {'account_id','csv','mapping','date_format'})
        account = self._account(p.get('account_id'))
        if account.id.startswith('acc_plaid_') and account.connection_healthy:
            raise ValueError('This account updates from your bank connection. Import CSV files into a manual account, or disconnect the bank first.')
        raw = p.get('csv')
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_CSV_BYTES:
            raise ValueError('CSV must be text smaller than 2 MB')
        if not raw.strip():
            raise ValueError('Choose a CSV containing transactions')
        reader = csv.DictReader(io.StringIO(raw.lstrip('\ufeff')), strict=True)
        headers = reader.fieldnames or []
        if not headers or len(headers) > 50 or len(set(headers)) != len(headers) or any(not h or len(h) > 160 for h in headers):
            raise ValueError('CSV must have unique column headers (at most 50)')
        mapping = p.get('mapping')
        if mapping is None:
            lowered = {h.strip().lower(): h for h in headers}
            mapping = {k: lowered.get(k, '') for k in ('date','description','amount','category')}
        if not isinstance(mapping, dict) or set(mapping) - {'date','description','amount','category','kind'}:
            raise ValueError('Map date, description, amount and optional category/kind columns')
        for key in ('date','description','amount'):
            if mapping.get(key) not in headers:
                return {'headers': headers, 'mapping': mapping, 'rows': [], 'errors': [],
                        'needs_mapping': True, 'total': 0, 'duplicates': 0, 'new_count': 0}, []
        if any(value and value not in headers for value in mapping.values()):
            raise ValueError('A mapped column is not in the CSV')
        date_format = p.get('date_format', 'YYYY-MM-DD')
        if date_format not in ('YYYY-MM-DD','MM/DD/YYYY'):
            raise ValueError('Choose YYYY-MM-DD or MM/DD/YYYY for dates')
        transactions, errors, seen = [], [], Counter()
        current_ids = {t.id for t in self.hh.transactions}
        total = 0
        try:
            for index, row in enumerate(reader, 2):
                if not any(str(v or '').strip() for v in row.values()):
                    continue
                total += 1
                if total > MAX_CSV_ROWS:
                    raise ValueError('Import at most 5,000 transactions at a time')
                try:
                    if None in row:
                        raise ValueError('Row contains more values than the header')
                    date_text = str(row.get(mapping['date']) or '').strip()
                    if date_format == 'MM/DD/YYYY':
                        date_text = datetime.strptime(date_text, '%m/%d/%Y').date().isoformat()
                    when = _date(date_text, 'Transaction date')
                    description = _text(row.get(mapping['description']) or '', 'Description', 500)
                    amount_text = str(row.get(mapping['amount']) or '').strip()
                    negative = amount_text.startswith('(') and amount_text.endswith(')')
                    if negative:
                        amount_text = amount_text[1:-1]
                    amount_text = amount_text.removeprefix('$').strip()
                    if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', amount_text):
                        raise ValueError('Amount must be a signed decimal; outflows are negative')
                    amount_text = amount_text.replace(',', '')
                    if negative:
                        amount_text = '-' + amount_text
                    amount = self._money(amount_text, 'Transaction amount', Decimal('-1000000000000000'))
                    category = _text(row.get(mapping.get('category')) or 'uncategorized', 'Category', 80)
                    kind_value = row.get(mapping.get('kind')) if mapping.get('kind') else None
                    kind = TxKind(kind_value.strip()) if kind_value else (TxKind.PURCHASE if amount.is_negative else TxKind.INCOME)
                    identity = '\x1f'.join((account.id, when.isoformat(), str(amount.amount), account.currency, description.casefold()))
                    occurrence = seen[identity]
                    seen[identity] += 1
                    digest = hashlib.sha256(f'{identity}\x1f{occurrence}'.encode('utf-8')).hexdigest()[:32]
                    tx = Transaction(id='csv_' + digest, account_id=account.id, date=when,
                                     amount=amount, description=description, category=category,
                                     kind=kind, state=TxState.POSTED)
                    transactions.append(tx)
                except (ValueError, TypeError) as error:
                    if len(errors) < 30:
                        errors.append({'row': index, 'message': str(error)})
        except csv.Error as error:
            raise ValueError(f'CSV could not be read: {error}') from None
        if total == 0:
            raise ValueError('The CSV has no transaction rows')
        duplicates = sum(t.id in current_ids for t in transactions)
        rows = [{**self._transaction_json(t), 'duplicate': t.id in current_ids} for t in transactions[:100]]
        return {'headers': headers, 'mapping': mapping, 'rows': rows, 'errors': errors,
                'needs_mapping': False, 'total': total, 'duplicates': duplicates,
                'new_count': len(transactions) - duplicates,
                'preview_limit': 100, 'balance_changed': False,
                'note': 'History only: bank balances and expected income events remain unchanged. Classify transfers and refunds correctly before using spending totals.'}, transactions

    def preview_transactions(self, payload: dict) -> dict:
        preview, _ = self._csv_rows(payload)
        return preview

    def import_transactions(self, payload: dict) -> dict:
        preview, transactions = self._csv_rows(payload)
        if preview['needs_mapping']:
            raise ValueError('Map the required CSV columns before importing')
        if preview['errors']:
            raise ValueError('Fix the invalid rows before importing; no transactions were saved')
        existing = {t.id for t in self.hh.transactions}
        new = [t for t in transactions if t.id not in existing]
        self.hh.transactions.extend(new)
        return {'imported': len(new), 'duplicates': len(transactions) - len(new),
                'balance_changed': False, 'account_id': payload['account_id']}

    def update_transaction(self, record_id: str, payload: dict) -> dict:
        _fields(payload, {'description','category','kind'})
        original = next((t for t in self.hh.transactions if t.id == record_id), None)
        if original is None:
            raise KeyError(record_id)
        tx = copy.deepcopy(original)
        if 'description' in payload:
            tx.description = _text(payload['description'], 'Description', 500)
        if 'category' in payload:
            tx.category = _text(payload['category'], 'Category', 80)
        if 'kind' in payload:
            tx.kind = TxKind(payload['kind'])
        tx.user_corrected = True
        self.hh.transactions = [tx if t.id == record_id else t for t in self.hh.transactions]
        return self._transaction_json(tx)

