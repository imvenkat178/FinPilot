"""UI projection checks: precision, source identity, read-only state, and exposure."""
from pickle import dumps
from datetime import date
from decimal import Decimal
import json

import pytest
from tests.api_support import authenticated_client, read_workspace, edit_workspace
from finpilot.api.workspace import workspace_data
from finpilot.engine.tax import TaxProfile
from finpilot.models import Account, Bill, Transaction, TxKind
from finpilot.money import Money
from finpilot.seed.demo import demo_household


@pytest.fixture
def household():
    return demo_household()


@pytest.fixture
def client():
    with authenticated_client() as client:
        yield client


def by_id(rows, key):
    return next(row for row in rows if row["id"] == key)


def test_workspace_route_exposes_distinct_balances_and_source_links(client):
    household = read_workspace(client).household
    response = client.get("/api/workspace")
    assert response.status_code == 200
    data = response.json()
    assert data["household"]["name"] == household.name
    assert len(data["accounts"]) == len(household.accounts)
    checking = by_id(data["accounts"], "acc_checking")
    for name in ("current", "available", "pending", "reserved", "held", "spendable"):
        assert checking[name] == getattr(household.accounts["acc_checking"], name).to_json()
    assert checking["owner_display"] == "A. Rivera, J. Rivera"
    assert checking["ownership_category"] == "joint"
    assert checking["apy"] == "0.0001"
    assert checking["provenance"]["source"] == "aggregation"
    retirement = by_id(data["accounts"], "acc_401k")
    assert retirement["connection_healthy"] is False
    assert retirement["last_synced_at"].startswith("2026-07-31T09:00:00")
    assert retirement["available"]["amount"] == "0.00"
    assert retirement["liquidity_tier"] == "investment"
    assert data["transactions"] == []


def test_workspace_terms_schedules_and_computed_properties(household):
    data = workspace_data(household, TaxProfile())
    mortgage = by_id(data["liabilities"], "lia_mortgage")
    assert mortgage["total_required_payment"]["amount"] == "1800.00"
    assert mortgage["minimum_payment"]["amount"] == "1138.77"
    assert mortgage["escrow"]["amount"] == "661.23"
    card = by_id(data["cards"], "card_a")
    assert card["purchase_apr"] == "0.2399"
    assert card["rules"][0]["cap_used"]["amount"] == "1310.00"
    assert card["credit_limit"]["amount"] == "9000.00"
    reserve = by_id(data["reserves"], "res_emergency")
    assert reserve["remaining"]["amount"] == "5600.00"
    income = data["income_sources"][0]
    assert income["schedule"]["cadence"] == "semimonthly"
    assert income["schedule"]["second_day_of_month"] == 15
    assert "calendar_" not in income["schedule"]
    assert by_id(data["income_events"], "ie_sep15")["received_amount"] is None
    assert by_id(data["income_events"], "ie_sep01")["is_received"] is True
    assert data["tax"]["verified"] is False


def test_workspace_bill_rules_use_real_ids_and_exclude_extra_principal(household):
    household.bills["unmapped"] = Bill(
        id="unmapped", name="New utility", funding_account_id="acc_checking")
    data = workspace_data(household, TaxProfile())
    expected = {
        "bill_mortgage": ["pol_mortgage"], "bill_auto": ["pol_auto"],
        "bill_student": ["pol_student"], "bill_card": ["pol_card"],
        "bill_utilities": ["pol_utilities"], "bill_insurance": ["pol_insurance"],
        "unmapped": [],
    }
    assert {b["id"]: b["policy_ids"] for b in data["bills"]} == expected
    assert by_id(data["bills"], "bill_card")["execution_owner"] == "creditor_autopay"
    # The source discrepancy must stay visible, not be silently reconciled by UI.
    assert by_id(data["bills"], "bill_card")["amount"]["amount"] == "600.00"
    assert by_id(data["cards"], "card_a")["statement_balance"]["amount"] == "742.18"


def test_workspace_read_preserves_policy_state_and_shared_mandates(client):
    with edit_workspace(client) as context:
        household = context.household
        household.policies["pol_utilities"].skip_next = True
        household.policies["pol_insurance"].paused = True
        # An intentional shared domain mandate must survive storage and reads.
        household.policies["pol_insurance"].mandate = household.policies["pol_utilities"].mandate
    before = read_workspace(client).snapshot()
    for _ in range(2):
        data = client.get("/api/workspace").json()
        assert by_id(data["policies"], "pol_utilities")["skip_next"] is True
        assert by_id(data["policies"], "pol_insurance")["paused"] is True
    after = read_workspace(client)
    assert after.snapshot() == before
    assert after.household.policies["pol_utilities"].mandate is after.household.policies["pol_insurance"].mandate


@pytest.mark.parametrize("amount,currency", [
    ("9007199254740993.01", "USD"), ("52.345", "KWD"), ("1234", "JPY"),
])
def test_workspace_money_stays_currency_aware_and_never_becomes_float(household, amount, currency):
    account = Account(id="precision", currency=currency,
                      current=Money(Decimal(amount), currency),
                      available=Money(Decimal(amount), currency),
                      pending=Money.zero(currency), reserved=Money.zero(currency),
                      held=Money.zero(currency))
    household.accounts[account.id] = account
    data = workspace_data(household, TaxProfile())
    # The JSON round trip must preserve a balance larger than a JS safe integer.
    row = by_id(json.loads(json.dumps(data))["accounts"], "precision")
    assert row["current"]["amount"] == amount
    assert row["current"] == account.current.to_json()


def test_workspace_transactions_keep_recorded_identity_and_classification(household):
    household.transactions.append(Transaction(
        id="tx_transfer", account_id="acc_checking", date=date(2026, 9, 10),
        amount=Money.of("-25.34"), kind=TxKind.INTERNAL_TRANSFER,
        description="Savings transfer", transfer_group_id="transfer_1"))
    transaction = workspace_data(household, TaxProfile())["transactions"][0]
    assert transaction["id"] == "tx_transfer"
    assert transaction["amount"]["amount"] == "-25.34"
    assert transaction["counts_as_income"] is False
    assert transaction["counts_as_spending"] is False
    assert transaction["transfer_group_id"] == "transfer_1"


def test_workspace_projection_does_not_expose_private_identity_or_new_fields(household):
    household.entities["ent_primary"].tax_id_last4 = "1234"
    household.accounts["acc_checking"].access_token = "do-not-serialize"
    household.policies["pol_utilities"].mandate.amendments.append(
        {"internal_note": "private-approval-history"})
    household.accounts["acc_checking"].provenance.evidence_ref = "private-evidence-location"
    data = workspace_data(household, TaxProfile())
    serialized = json.dumps(data)
    for private_key in ("consents", "members", "tax_id_last4", "authorized_by", "amendments",
                        "access_token", "evidence_ref", "do-not-serialize",
                        "private-approval-history", "private-evidence-location"):
        assert private_key not in serialized
    assert "entities" not in data
    assert "signers" not in serialized
    mandate = by_id(data["policies"], "pol_utilities")["mandate"]
    assert mandate["active"] is True
    assert mandate["per_run_cap"]["amount"] == "2500.00"


def test_workspace_is_selected_by_the_authenticated_household(client):
    household_id = client.principal.household_id
    response = client.get("/api/workspace", params={"household": household_id})
    assert response.status_code == 200
    assert response.json()["household"]["id"] == household_id
    for other in ("hnw", "demo", "missing"):
        response = client.get("/api/workspace", params={"household": other})
        assert response.status_code == 404
        assert response.json()["detail"] == "Workspace not found."


def test_assets_are_served_without_exposing_files_outside_web(client):
    root = client.get("/")
    asset = client.get("/assets/index.html")
    assert root.status_code == asset.status_code == 200
    assert "text/html" in asset.headers["content-type"]
    import re
    # Root versions the relative module tree; the retained static HTML has
    # otherwise identical content and still cannot expose files outside WEB.
    normalized = re.sub(r"/assets/v[0-9a-f]{16}/", "/assets/", root.text)
    assert asset.text.splitlines() == normalized.splitlines()
    assert client.get("/assets/not-a-real-file.js").status_code == 404
    assert client.get("/assets/%2E%2E/api/app.py").status_code == 404



def test_debt_compare_zero_extra_uses_only_required_payments(client):
    household = read_workspace(client).household
    response = client.get("/api/debt/compare", params={"extra_payment": 0})
    assert response.status_code == 200
    data = response.json()
    assert data["extra"]["amount"] == "0.00"
    assert data["budget"] == data["required_total"]
    for strategy in data["results"]:
        payments = strategy["first_month_allocation"]
        assert all(payments[liability.name] == liability.minimum_payment.to_json()
                   for liability in household.liabilities.values())
    # An omitted amount retains the pre-existing suggested extra payment.
    suggested = client.get("/api/debt/compare").json()
    assert suggested["extra"]["amount"] == "500.00"


def test_card_utilization_zero_payment_preserves_current_utilization(client):
    household = read_workspace(client).household
    response = client.get("/api/cards/utilization", params={"card_id": "card_a", "payment": 0})
    assert response.status_code == 200
    data = response.json()
    assert data["planned_payment"]["amount"] == "0.00"
    assert (data["reported_utilization_if_paid_before_close"]
            == data["reported_utilization_if_paid_at_due_date"])
    assert data["reported_utilization_if_paid_before_close"] == "8.2%"
    # Omitting payment still compares paying the full current card balance.
    suggested = client.get("/api/cards/utilization", params={"card_id": "card_a"}).json()
    assert suggested["planned_payment"] == household.cards["card_a"].current_balance.to_json()
    assert suggested["reported_utilization_if_paid_before_close"] == "0.0%"
