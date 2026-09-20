"""Aggregator transaction labels map onto one FinPilot category vocabulary."""
from finpilot.integrations.categories import (DETAILED_FROM_PLAID, PROVIDER_LABEL, PRIMARY_FROM_PLAID, UNCATEGORIZED,
                                               VOCABULARY, plaid_category, plaid_kind)
from finpilot.models import TxKind

# Primary labels in Plaid's published personal finance category taxonomy, checked 2026-09-14.
PLAID_TAXONOMY_PRIMARIES = {
    "INCOME", "TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS", "BANK_FEES", "ENTERTAINMENT",
    "FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "HOME_IMPROVEMENT", "MEDICAL", "PERSONAL_CARE",
    "GENERAL_SERVICES", "GOVERNMENT_AND_NON_PROFIT", "TRANSPORTATION", "TRAVEL", "RENT_AND_UTILITIES",
}


def test_every_plaid_label_maps_into_the_vocabulary():
    assert set(PRIMARY_FROM_PLAID) == PLAID_TAXONOMY_PRIMARIES
    assert set(PRIMARY_FROM_PLAID.values()) | set(DETAILED_FROM_PLAID.values()) <= VOCABULARY
    assert all(any(label.startswith(primary + "_") for primary in PLAID_TAXONOMY_PRIMARIES)
               for label in DETAILED_FROM_PLAID)
    assert all(PROVIDER_LABEL.fullmatch(label) for label in [*PRIMARY_FROM_PLAID, *DETAILED_FROM_PLAID])


def test_detailed_labels_refine_the_primary_category():
    assert plaid_category("FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE") == "dining"
    assert plaid_category("FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES") == "groceries"
    assert plaid_category("FOOD_AND_DRINK", "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR") == "shopping"
    assert plaid_category("TRANSPORTATION", "TRANSPORTATION_GAS") == "gas"
    assert plaid_category("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT") == "housing"
    assert plaid_category("RENT_AND_UTILITIES", "RENT_AND_UTILITIES_WATER") == "utilities"
    assert plaid_category("FOOD_AND_DRINK") == "dining"
    assert plaid_category("SOMETHING_NEW", "SOMETHING_NEW_DETAIL") == UNCATEGORIZED


def test_kinds_keep_transfers_repayments_and_interest_out_of_spending():
    assert plaid_kind("INCOME", "INCOME_WAGES", outflow=False) == TxKind.INCOME
    assert plaid_kind("TRANSFER_OUT", "TRANSFER_OUT_SAVINGS", outflow=True) == TxKind.INTERNAL_TRANSFER
    assert plaid_kind("TRANSFER_IN", "TRANSFER_IN_DEPOSIT", outflow=False) == TxKind.INTERNAL_TRANSFER
    assert plaid_kind("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", outflow=True) == TxKind.CARD_REPAYMENT
    assert plaid_kind("LOAN_PAYMENTS", "LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT", outflow=True) == TxKind.LOAN_PAYMENT
    assert plaid_kind("BANK_FEES", "BANK_FEES_INTEREST_CHARGE", outflow=True) == TxKind.INTEREST
    assert plaid_kind("BANK_FEES", "BANK_FEES_ATM_FEES", outflow=True) == TxKind.FEE
    assert plaid_kind("FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", outflow=True) == TxKind.PURCHASE
    assert plaid_kind("GENERAL_MERCHANDISE", "", outflow=False) == TxKind.PROVISIONAL_CREDIT
