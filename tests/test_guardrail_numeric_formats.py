"""Financial wording coverage without relying on a real model's behavior."""
from types import SimpleNamespace
import pytest

from finpilot.ai.graph import FinanceAgent
from finpilot.ai.guardrails import check_grounding
from finpilot.ai.tools import ToolRegistry
from finpilot.seed.demo import demo_household


@pytest.mark.parametrize("claim", [
    "999999 dollars", "999999 USD", "USD 999999", "999999 percent", "999999%",
    "$999999", "999999 euros", "999999 GBP", "999999 rupees",
    "$1k", "1k dollars", "1.2 million dollars", "$1.2 million", "2M USD", "3 billion",
])
def test_unbacked_currency_words_percent_and_magnitudes_are_rejected(claim):
    report = check_grounding("You can use " + claim + ".", [{"amount": "0"}])
    assert not report.ok and report.checked >= 1


@pytest.mark.parametrize("claim", ["$1", "$2", "$3", "$100", "1 dollar", "2 percent", "100%"])
def test_positive_constants_require_evidence(claim):
    assert not check_grounding(claim, [{"amount": "0"}]).ok


@pytest.mark.parametrize("claim", ["$1.2 million", "1.2 million dollars", "USD 1200000", "1,200k"])
def test_equivalent_financial_magnitudes_remain_grounded(claim):
    assert check_grounding(claim, [{"amount": "1200000.00"}]).ok


@pytest.mark.parametrize("field", ["id", "account_id", "institution_id", "mask", "mcc",
    "routing_number", "account_number", "last_four", "reference_number", "merchant", "nickname"])
def test_numeric_identifiers_and_names_do_not_authorize_money(field):
    assert not check_grounding("You have $1234.", [{field: "1234", "balance": "0"}]).ok
    assert not check_grounding("You have 1234 dollars.", [{field: "acc_1234", "balance": "0"}]).ok


def test_only_financial_values_not_description_digits_authorize_amounts():
    assert not check_grounding("You have $250000.", [{"description": "Account reference 250000"}]).ok
    assert not check_grounding("You have $20260911.", [{"as_of": "2026-09-11"}]).ok
    assert check_grounding("Your balance is $1234.", [{"balance": {"amount": "1234.00", "currency": "USD"}}]).ok
    assert check_grounding("APY is 4 percent.", [{"apy": "0.04"}]).ok
    assert check_grounding("Payoff takes 38 months as of 2026-09-11.",
        [{"months_to_clear": 38, "as_of": "2026-09-11T14:30:00+00:00"}]).ok


@pytest.mark.parametrize("claim", ["-$1234", "-1234 dollars", "USD -1234"])
def test_negative_sign_remains_part_of_the_evidence(claim):
    assert check_grounding(claim, [{"amount": "-1234"}]).ok
    assert not check_grounding(claim, [{"amount": "1234"}]).ok


def test_currency_word_bypass_is_rejected_in_full_answer_path():
    tools = ToolRegistry(demo_household())
    actual_cash = tools.get_money_overview()["total_cash"]["display"]
    class SyntheticModel:
        available = True
        config = SimpleNamespace(timeout=12, model="synthetic-wording")
        def complete(self, *args, **kwargs):
            return f"Cash is {actual_cash}. You can safely spend 999999 dollars today."
    result = FinanceAgent(tools, SyntheticModel(), compile_graph=False).ask("How much money do I have?")
    assert not result.used_model
    assert not result.grounding["ok"]
    assert "999999 dollars" in result.grounding["ungrounded"]
    assert "999999 dollars" not in result.answer


def test_rate_conversion_cannot_authorize_unrelated_money():
    assert not check_grounding("You can spend $100.", [{"amount": "1"}]).ok
    assert not check_grounding("You can spend $4.", [{"apy": "0.04"}]).ok
    assert check_grounding("APY is 4 percent.", [{"apy": "0.04"}]).ok
    assert check_grounding("APR is 4.25%.", [{"apr": "0.0425"}]).ok
    assert not check_grounding("APR is 100%.", [{"amount": "1"}]).ok


def test_calculator_scenario_duration_remains_grounded():
    assert check_grounding("With no income for 1 month(s), the plan changes.",
        [{"scenario": "no income for 1 month(s)"}]).ok


def test_coverage_prose_cannot_drop_estimate_and_ownership_caveat():
    tools = ToolRegistry(demo_household())
    class SyntheticModel:
        available = True
        config = SimpleNamespace(timeout=12, model="synthetic-coverage")
        def complete(self, *args, **kwargs):
            amount = tools.get_deposit_coverage()["total_uncovered"]["display"]
            return f"The cash in your different platforms is insured. Uncovered deposits total {amount}."
    result = FinanceAgent(tools, SyntheticModel(), compile_graph=False).ask("How is my cash protected?")
    assert not result.used_model
    assert result.grounding.get("rejected_for") == "coverage_caveat"
    assert not result.answer.startswith("The cash in your different platforms is insured.")


def test_coverage_prose_can_preserve_its_qualified_estimate():
    tools = ToolRegistry(demo_household())
    class SyntheticModel:
        available = True
        config = SimpleNamespace(timeout=12, model="synthetic-coverage")
        def complete(self, *args, **kwargs):
            amount = tools.get_deposit_coverage()["total_uncovered"]["display"]
            return f"Estimated uncovered deposits total {amount}. Confirm ownership and category with the institution."
    result = FinanceAgent(tools, SyntheticModel(), compile_graph=False).ask("How is my cash protected?")
    assert result.used_model and result.grounding["ok"]


@pytest.mark.parametrize("label", ["401k", "401K", "403b", "403B"])
def test_unprefixed_retirement_plan_names_are_labels(label):
    report = check_grounding(f"The {label} connection needs attention.", [{"accounts": []}])
    assert report.ok and report.checked == 0


@pytest.mark.parametrize("amount", ["$401k", "$403b", "USD 401k", "401k dollars", "999k", "-401k"])
def test_retirement_label_exception_does_not_exempt_monetary_claims(amount):
    report = check_grounding(f"Your balance is {amount}.", [{"amount": "0"}])
    assert not report.ok and report.checked >= 1


def test_qualified_401k_connection_answer_remains_grounded():
    tools = ToolRegistry(demo_household())
    class SyntheticModel:
        available = True
        config = SimpleNamespace(timeout=12, model="synthetic-connection")
        def complete(self, *args, **kwargs):
            data = tools.get_account_connections()
            account = next(row for row in data["accounts"] if row["id"] == "acc_401k")
            when = account["last_synced_at"][:10]
            return (f"Your 401k connection needs attention. Last synced {when}. "
                    "It is not currently updating; affected estimates need review.")
    result = FinanceAgent(tools, SyntheticModel(), compile_graph=False).ask("Is my 401k connection still working?")
    assert result.used_model and result.grounding["ok"]
    assert "401k" in result.answer
