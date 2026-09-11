from copy import deepcopy
from pickle import dumps
from datetime import date
from decimal import Decimal

import pytest

from finpilot.engine.tax import TaxProfile
from finpilot.models import Household, Mandate, AuthorizationMode, now
from finpilot.seed.demo import demo_household
from finpilot.services.workspace import WorkspaceService

@pytest.fixture
def service():
    return WorkspaceService(demo_household(), TaxProfile())


def test_account_create_updates_card_and_liability_and_preserves_planning(service):
    created = service.upsert('accounts', {'nickname':'New card','type':'credit_card','current':'312.47',
        'credit_limit':'5000','statement_balance':'200','apr':'0.2499','minimum_payment':'25',
        'included_in_planning':False})
    account = service.hh.accounts[created['id']]
    card = next(c for c in service.hh.cards.values() if c.account_id == account.id)
    loan = next(l for l in service.hh.liabilities.values() if l.account_id == account.id)
    assert account.current.amount == Decimal('-312.47')
    assert account.available.amount == 0
    assert card.current_balance == loan.balance
    assert loan.balance.amount == Decimal('312.47')
    service.upsert('accounts', {'current':'400.18'}, account.id)
    assert service.hh.accounts[account.id].included_in_planning is False
    assert service.hh.cards[card.id].current_balance.amount == Decimal('400.18')
    assert service.hh.liabilities[loan.id].balance.amount == Decimal('400.18')
    assert not service.hh.accounts[account.id].capabilities.intersection({'send_transfer','pay_biller'})


@pytest.mark.parametrize('value', ['NaN','Infinity','-Infinity','1.001',True,1.25])
def test_invalid_money_is_rejected_atomically(service, value):
    before = dumps(service.hh)
    with pytest.raises(ValueError):
        service.upsert('accounts', {'nickname':'Changed','current':value}, 'acc_checking')
    assert dumps(service.hh) == before


def test_account_type_currency_and_unknown_reference_validation(service):
    with pytest.raises(ValueError): service.upsert('accounts', {'type':'credit_card'}, 'acc_checking')
    with pytest.raises(ValueError): service.upsert('accounts', {'currency':'EUR'}, 'acc_checking')
    with pytest.raises(KeyError): service.upsert('accounts', {'nickname':'X'}, 'other-user-account')
    with pytest.raises(ValueError): service.upsert('bills', {'name':'Rent','amount':'10','funding_account_id':'other-user-account'})


def test_reserves_use_combined_account_funds_and_edits_replace_own_allocation(service):
    result = service.upsert('reserves', {'name':'Travel','account_id':'acc_checking','target':'1000','funded':'500'})
    reserve = service.hh.reserves[result['id']]
    assert reserve.funded.amount == 500
    service.upsert('reserves', {'funded':'700'}, reserve.id)
    with pytest.raises(ValueError):
        service.upsert('reserves', {'name':'Excess','account_id':'acc_checking','target':'5000','funded':'3000'})
    with pytest.raises(ValueError): service.upsert('accounts', {'available':'500'}, 'acc_checking')
    with pytest.raises(ValueError): service.upsert('reserves', {'funded':'1200'}, reserve.id)


def test_income_generate_and_received_events_survive_edits(service):
    created = service.upsert('income', {'name':'Consulting','net_amount':'1200.33',
        'deposit_account_id':'acc_checking','next_date':'2026-09-15','cadence':'monthly'})
    events = [e for e in service.hh.income_events if e.source_id == created['id']]
    assert len(events) == 12
    event = events[0]
    service.upsert('income', {'record_type':'event','received_amount':'1100','received_date':'2026-09-16'}, event.id)
    service.upsert('income', {'net_amount':'1500','next_date':'2026-10-15','cadence':'monthly'}, created['id'])
    received = next(e for e in service.hh.income_events if e.id == event.id)
    assert received.received_amount.amount == 1100
    assert service.hh.accounts['acc_checking'].current.amount == 3200
    keys=[(e.source_id,e.expected_date) for e in service.hh.income_events]
    assert len(keys) == len(set(keys))


def test_bill_schedule_and_existing_rule_are_distinct(service):
    old = deepcopy(service.hh.policies['pol_card'])
    service.upsert('bills', {'amount':'742.18','cadence':'monthly','due_date':'2026-09-12'}, 'bill_card')
    assert service.hh.bills['bill_card'].schedule.cadence.value == 'monthly'
    assert service.hh.policies['pol_card'] == old
    assert service.hh.bills['bill_card'].provenance.verification.value == 'user_entered'


def test_complete_rule_methods_and_authority_reset(service):
    source = 'acc_checking'
    dest = 'acc_savings'
    result = service.upsert('policies', {'name':'Rainy day','source_account_id':source,
        'destination_account_id':dest,'method':'percent_of_income','percent':'0.15',
        'percent_base':'net_paycheck','purpose':'goal'})
    rule = service.hh.policies[result['id']]
    assert rule.percent == Decimal('0.15')
    assert not rule.mandate.active
    service.upsert('policies', {'method':'target_by_date','target_balance':'2000',
        'target_date':'2027-02-01'}, rule.id)
    rule = service.hh.policies[rule.id]
    rule.mandate = Mandate(mode=AuthorizationMode.STANDING, authorized_at=now())
    service.upsert('policies', {'name':'Rename only'}, rule.id)
    assert service.hh.policies[rule.id].mandate.active
    service.upsert('policies', {'target_balance':'3000'}, rule.id)
    assert not service.hh.policies[rule.id].mandate.active
    with pytest.raises(ValueError): service.upsert('policies', {'percent':'2'}, rule.id)
    with pytest.raises(ValueError): service.upsert('policies', {'liability_id':'lia_mortgage'}, rule.id)


def test_tax_remains_unverified_and_validates_combined_rate(service):
    tax=service.tax
    service.upsert('tax', {'federal_marginal':'0.22','state_marginal':'0.04','itemizes':True})
    assert tax.combined_marginal == Decimal('0.26')
    assert tax.verified is False
    before=deepcopy(tax)
    with pytest.raises(ValueError): service.upsert('tax', {'federal_marginal':'0.99','state_marginal':'0.99'})
    assert tax == before


CSV='Date,Description,Amount,Category\n2026-09-08,Coffee,-4.85,Dining\n2026-09-08,Coffee,-4.85,Dining\n2026-09-09,Salary,1200,Income\n'
PAYLOAD={'account_id':'acc_checking','csv':CSV,'mapping':{'date':'Date','description':'Description','amount':'Amount','category':'Category'}}


def test_csv_preview_duplicate_import_and_correction_never_change_balances(service):
    balances=deepcopy(service.hh.accounts)
    income=deepcopy(service.hh.income_events)
    preview=service.preview_transactions(PAYLOAD)
    assert preview['new_count'] == 3
    assert len(service.hh.transactions) == 0
    assert service.import_transactions(PAYLOAD)['imported'] == 3
    assert service.import_transactions(PAYLOAD)['duplicates'] == 3
    assert service.hh.accounts == balances
    assert service.hh.income_events == income
    assert len({t.id for t in service.hh.transactions}) == 3
    tx=service.hh.transactions[-1]
    result=service.update_transaction(tx.id, {'kind':'internal_transfer','category':'Transfer'})
    assert result['counts_as_income'] is False
    assert service.import_transactions(PAYLOAD)['duplicates'] == 3
    assert service.hh.transactions[-1].category == 'Transfer'


def test_invalid_csv_is_atomic_bounded_and_supports_mapping(service):
    payload={**PAYLOAD,'csv':CSV+'invalid,Error,NaN,Bad\n'}
    preview=service.preview_transactions(payload)
    assert preview['errors'][0]['row'] == 5
    with pytest.raises(ValueError): service.import_transactions(payload)
    assert not service.hh.transactions
    assert service.preview_transactions({**PAYLOAD,'mapping':{}})['needs_mapping']
    with pytest.raises(ValueError): service.import_transactions({**PAYLOAD,'csv':'Date,Description,Amount,Category\n'+'2026-09-01,A,-1,Food\n'*5001})
    with pytest.raises(ValueError): service.preview_transactions({**PAYLOAD,'account_id':'another-household'})


def test_empty_household_can_complete_first_account_income_bill_goal(service):
    svc=WorkspaceService(Household(as_of=date(2026,9,10)),TaxProfile())
    account=svc.upsert('accounts',{'nickname':'Checking','type':'checking','current':'5000','available':'4800'})['id']
    svc.upsert('income',{'name':'Salary','net_amount':'2500','deposit_account_id':account,'cadence':'biweekly','next_date':'2026-09-15'})
    svc.upsert('bills',{'name':'Rent','amount':'1000','funding_account_id':account,'due_date':'2026-10-01','cadence':'monthly'})
    svc.upsert('reserves',{'name':'Emergency','account_id':account,'target':'10000','funded':'2000'})
    assert len(svc.hh.accounts)==len(svc.hh.bills)==len(svc.hh.reserves)==1
    assert svc.hh.income_events
