"""A valid balance must not be relabeled as spendable money by the model."""
import pytest
from finpilot.ai.graph import FinanceAgent
from finpilot.ai.guardrails import check_grounding, spending_allowance_claim_error
from finpilot.ai.llm import LLMConfig
from finpilot.ai.tools import ToolRegistry
from finpilot.seed.demo import demo_household


class DraftModel:
    available = True
    config = LLMConfig(model='test', timeout=1)
    def __init__(self, draft):
        self.draft = draft
    def complete(self, *args, **kwargs):
        return self.draft


def test_live_llama_regression_cannot_call_protected_savings_spendable():
    registry = ToolRegistry(demo_household())
    evidence = registry.call('get_spending_allowance', {'account_id': 'acc_savings', 'days': 7})
    assert evidence['amount']['amount'] == '0.00'
    assert evidence['low_point_balance']['amount'] == evidence['protected']['amount'] == '14500.00'
    draft = (f"You can spend about $14,500.00 through {evidence['through']}. "
             f"That is set by the lowest projected balance of $14,500.00 on {evidence['low_point_date']}, "
             "after $0.00 of required bills and $14,500.00 of protected reserves. Confidence: exact.")
    assert check_grounding(draft, [evidence]).ok  # Same numbers, wrong financial relationship.
    answer = FinanceAgent(registry, DraftModel(draft), compile_graph=False,
                          default_account_id='acc_savings').ask('How much can I spend this week?')
    assert answer.used_model is False
    assert answer.grounding['rejected_for'] == 'spending_allowance_claim'
    assert answer.answer.startswith('You can spend about $0.00')
    assert 'protected reserves is then excluded from spending' in answer.answer


@pytest.mark.parametrize('draft', [
    'You can spend about $0.00. Your lowest projected balance is $14,500.00, and all of it is protected.',
    'Your spending allowance is USD 0. The $14,500.00 remains protected.',
    "You've got roughly $0.00. The $14,500.00 remains protected.",
    'You have $0.00 available to spend. Protected reserves are $14,500.00.',
])
def test_correct_model_wording_remains_available(draft):
    registry = ToolRegistry(demo_household())
    answer = FinanceAgent(registry, DraftModel(draft), compile_graph=False,
                          default_account_id='acc_savings').ask('How much can I spend this week?')
    assert answer.used_model is True
    assert answer.answer == draft


@pytest.mark.parametrize('draft', [
    '$14,500.00 is safe to spend.',
    'Your spending allowance is $14,500.00.',
    "You've got roughly $14,500.00.",
    'You can spend $0.00. There is $14,500.00 available to spend.',
    'The balance is $14,500.00. You have enough for discretionary purchases.',
])
def test_wrong_or_unverifiable_spending_claim_is_rejected(draft):
    assert spending_allowance_claim_error(draft, {'amount': {'amount': '0.00'}})


def test_nonzero_spending_amount_accepts_supported_currency_formats():
    assert spending_allowance_claim_error('Your spending allowance is 2.5 thousand dollars.',
                                         {'amount': {'amount': '2500.00'}}) is None


@pytest.mark.parametrize('draft', [
    'You can spend $0.00 or up to $14,500.00.',
    'You can spend $0.00 to $14,500.00.',
    'You can spend $0.00 or\nup to $14,500.00.',
    'You can spend $0.00; alternatively, spend $14,500.00.',
    'Your spending allowance is $0.00, but you have $14,500.00 left for discretionary purchases.',
])
def test_additional_spending_sentence_amount_cannot_form_a_range_or_alternative(draft):
    registry = ToolRegistry(demo_household())
    evidence = registry.call('get_spending_allowance', {'account_id': 'acc_savings', 'days': 7})
    assert check_grounding(draft, [evidence]).ok
    answer = FinanceAgent(registry, DraftModel(draft), compile_graph=False,
                          default_account_id='acc_savings').ask('How much can I spend this week?')
    assert answer.used_model is False
    assert answer.grounding['rejected_for'] == 'spending_allowance_claim'
    assert answer.answer.startswith('You can spend about $0.00')


@pytest.mark.parametrize('draft', [
    'Your projected balance is $14,500.00. You can spend $0.00. Your reserves are $14,500.00.',
    'Your spending allowance is $0.00, or $0.00 available to spend. Reserves total $14,500.00.',
    'You can spend 0 U.S. dollars. The balance is $14,500.00.',
])
def test_decimals_and_dotted_currency_names_do_not_split_spending_sentence(draft):
    assert spending_allowance_claim_error(draft, {'amount': {'amount': '0.00'}}) is None


def test_ambiguous_combined_allowance_and_reserve_sentence_falls_back():
    # The check is intentionally conservative; this legitimate combined sentence
    # uses calculator wording instead of attempting to prove each clause's role.
    draft = 'You can spend $0.00 while $14,500.00 remains protected.'
    assert spending_allowance_claim_error(draft, {'amount': {'amount': '0.00'}})
