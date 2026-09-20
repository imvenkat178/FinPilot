"""Aggregated card and loan balances, reported terms and bank categories, through the real HTTP boundaries.

Plaid is mocked with the transport from tests/test_bank_linking.py. Field names follow Plaid's
Liabilities and personal finance category documentation, checked 2026-09-14.
"""
from decimal import Decimal

from finpilot.integrations.plaid import provider_id
from tests.test_bank_linking import (account, bank, batch, context, link, register, saved,  # noqa: F401
                                     transaction)


def credit_account(account_id="bank-card", **balances):
    return {"account_id": account_id, "type": "credit", "subtype": "credit card", "name": "Rewards card",
            "mask": "2210", "balances": {"current": 742.18, "available": 8257.82, "limit": 9000,
                                         "iso_currency_code": "USD", **balances}}


def loan_account(account_id, subtype, current, name):
    return {"account_id": account_id, "type": "loan", "subtype": subtype, "name": name, "mask": "5510",
            "balances": {"current": current, "available": None, "limit": None, "iso_currency_code": "USD"}}


def reported_liabilities():
    return {
        "credit": [{"account_id": "bank-card", "aprs": [
            {"apr_percentage": 23.99, "apr_type": "purchase_apr", "balance_subject_to_apr": 742.18,
             "interest_charge_amount": 0},
            {"apr_percentage": 29.99, "apr_type": "cash_apr", "balance_subject_to_apr": None,
             "interest_charge_amount": None}],
            "is_overdue": False, "last_payment_amount": 120, "last_payment_date": "2026-08-20",
            "last_statement_issue_date": "2026-09-18", "last_statement_balance": 700.0,
            "minimum_payment_amount": 35, "next_payment_due_date": "2026-10-12"}],
        "student": [{"account_id": "bank-student", "interest_rate_percentage": 6.8, "minimum_payment_amount": 250,
                     "next_payment_due_date": "2026-10-20", "loan_name": "Consolidation",
                     "repayment_plan": {"type": "standard", "description": "Standard repayment"}}],
        "mortgage": [{"account_id": "bank-mortgage", "interest_rate": {"percentage": 4.5, "type": "fixed"},
                      "next_monthly_payment": 1800, "next_payment_due_date": "2026-10-05",
                      "maturity_date": "2046-09-01", "has_prepayment_penalty": False, "escrow_balance": 3200}],
    }


def test_cards_and_loans_track_balances_and_import_only_reported_terms(bank):
    client, r, mock, _ = bank
    register(client)
    mock.accounts = [account(), credit_account(), loan_account("bank-student", "student", 18400, "Student loan"),
                     loan_account("bank-mortgage", "mortgage", 180000, "Home mortgage"),
                     loan_account("bank-auto", "auto", 12000, "Car loan")]
    mock.liabilities = reported_liabilities()
    link(client)
    assert any(path == "/liabilities/get" for path, _ in mock.calls)
    hh = context(client, r).household
    debts = {item.name: item for item in hh.liabilities.values()}
    card = debts["Rewards card"]
    assert card.apr == Decimal("0.2399") and card.minimum_payment.amount == Decimal("35")
    assert card.due_day == 12 and card.terms_complete and card.balance.amount == Decimal("742.18")
    student = debts["Student loan"]
    assert student.apr == Decimal("0.068") and student.terms_complete
    assert student.student_loan_program == "Standard repayment"
    mortgage = debts["Home mortgage"]
    assert mortgage.apr == Decimal("0.045") and mortgage.due_day == 5 and not mortgage.terms_complete
    assert mortgage.minimum_payment.is_zero and mortgage.remaining_term_months > 200
    assert not debts["Car loan"].terms_complete
    (linked_card,) = hh.cards.values()
    assert linked_card.credit_limit.amount == Decimal("9000") and linked_card.statement_balance.amount == Decimal("700")
    assert (linked_card.statement_close_day, linked_card.payment_due_day) == (18, 12)
    assert linked_card.purchase_apr == Decimal("0.2399") and linked_card.grace_state.value == "unknown"
    card_account = hh.accounts[card.account_id]
    assert card_account.current.amount == Decimal("-742.18") and card_account.available.is_zero
    assert hh.total_debt().amount == Decimal("211142.18")
    cash = hh.accounts[saved(r).account_mapping["bank-checking"]]
    assert hh.net_worth().amount == cash.current.amount - Decimal("211142.18")
    comparison = client.post("/api/debt/compare", json={"extra_payment": 100}).json()
    assert sorted(item["name"] for item in comparison["debts"]) == ["Rewards card", "Student loan"]
    assert comparison["missing_terms"] == ["Car loan", "Home mortgage"]
    assert "escrow" in client.post("/api/mortgage/scenarios", json={}).json()["error"]
    notices = " ".join(saved(r).notices)
    assert "Car loan" in notices and "Home mortgage" in notices


def test_bank_categories_map_to_finpilot_categories_and_keep_user_corrections(bank):
    client, r, mock, _ = bank
    register(client)
    mock.updates = [batch(added=[
        transaction("groceries", personal_finance_category={"primary": "FOOD_AND_DRINK",
                                                            "detailed": "FOOD_AND_DRINK_GROCERIES"}),
        transaction("card-payment", amount=300, personal_finance_category={
            "primary": "LOAN_PAYMENTS", "detailed": "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"}),
        transaction("interest", amount=4.2, personal_finance_category={
            "primary": "BANK_FEES", "detailed": "BANK_FEES_INTEREST_CHARGE"}),
        transaction("pay", amount=-2500, personal_finance_category={"primary": "INCOME", "detailed": "INCOME_WAGES"}),
        transaction("new-label", personal_finance_category={"primary": "SOMETHING_NEW"}),
    ])]
    connection_id = link(client)

    def rows():
        return {tx.id: tx for tx in context(client, r).household.transactions}

    found = rows()
    groceries = found[provider_id(connection_id, "groceries", "tx_plaid_")]
    assert (groceries.category, groceries.kind.value) == ("groceries", "purchase")
    assert groceries.provider_category == "FOOD_AND_DRINK_GROCERIES"
    assert found[provider_id(connection_id, "card-payment", "tx_plaid_")].kind.value == "card_repayment"
    interest = found[provider_id(connection_id, "interest", "tx_plaid_")]
    assert (interest.category, interest.kind.value) == ("interest", "interest") and not interest.counts_as_spending
    pay = found[provider_id(connection_id, "pay", "tx_plaid_")]
    assert pay.counts_as_income and pay.category == "income"
    assert found[provider_id(connection_id, "new-label", "tx_plaid_")].category == "uncategorized"
    assert client.patch(f"/api/transactions/{groceries.id}", json={"category": "household"}).status_code == 200
    mock.updates = [batch("cursor-2", modified=[transaction("groceries", name="Market", personal_finance_category={
        "primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_RESTAURANT"})])]
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 200
    corrected = rows()[groceries.id]
    assert corrected.category == "household" and corrected.provider_category == "FOOD_AND_DRINK_RESTAURANT"


def test_unavailable_liabilities_product_is_a_notice_not_a_failed_sync(bank):
    client, r, mock, _ = bank
    register(client)
    mock.accounts = [account(), credit_account()]
    mock.liabilities_error = "PRODUCTS_NOT_SUPPORTED"
    connection_id = link(client)
    hh = context(client, r).household
    (liability,) = hh.liabilities.values()
    assert liability.balance.amount == Decimal("742.18") and not liability.terms_complete and not hh.cards
    assert any("not shared card and loan terms" in notice for notice in saved(r).notices)
    mock.liabilities_error = "ITEM_LOGIN_REQUIRED"
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 502


def test_csv_import_is_refused_for_bank_synced_accounts_until_disconnect(bank):
    client, r, mock, _ = bank
    register(client)
    connection_id = link(client)
    payload = {"account_id": saved(r).account_mapping["bank-checking"],
               "csv": "Date,Description,Amount\n2026-09-08,Coffee,-4.85\n",
               "mapping": {"date": "Date", "description": "Description", "amount": "Amount"}}
    refused = client.post("/api/transactions/preview", json=payload)
    assert refused.status_code == 422 and "updates from your bank" in refused.text
    assert client.delete(f"/api/bank/connections/{connection_id}").status_code == 200
    assert client.post("/api/transactions/preview", json=payload).status_code == 200


def test_entered_mortgage_terms_complete_a_linked_loan_and_survive_sync(bank):
    client, r, mock, _ = bank
    register(client)
    mock.accounts = [loan_account("bank-mortgage", "mortgage", 180000, "Home mortgage")]
    mock.liabilities = reported_liabilities()
    connection_id = link(client)
    account_id = saved(r).account_mapping["bank-mortgage"]
    response = client.patch(f"/api/manage/accounts/{account_id}",
                            json={"apr": "0.045", "minimum_payment": "1138.77", "escrow": "661.23"})
    assert response.status_code == 200, response.text
    mock.liabilities["mortgage"][0]["interest_rate"]["percentage"] = 4.25
    assert client.post(f"/api/bank/sync/{connection_id}").status_code == 200
    (mortgage,) = context(client, r).household.liabilities.values()
    assert mortgage.terms_complete and mortgage.apr == Decimal("0.0425")
    assert mortgage.minimum_payment.amount == Decimal("1138.77") and mortgage.escrow.amount == Decimal("661.23")
    assert "error" not in client.post("/api/mortgage/scenarios", json={"extra_monthly": "100"}).json()
    assert client.delete(f"/api/bank/connections/{connection_id}").status_code == 200
    (mortgage,) = context(client, r).household.liabilities.values()
    assert mortgage.provenance.verification.value == "stale"
