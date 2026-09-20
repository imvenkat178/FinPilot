"""One transaction category vocabulary for every data source, with aggregator mappings.

Aggregators label transactions with their own taxonomies. FinPilot stores one lowercase
vocabulary so card rewards, bills, budgets and chat filters behave the same for bank,
card and loan data, and each transaction keeps the provider's own label for review.
Supporting another aggregator means adding its label table and kind rules here.
"""
from __future__ import annotations

import re

from ..models import TxKind

UNCATEGORIZED = "uncategorized"
VOCABULARY = frozenset({
    "dining", "groceries", "gas", "transportation", "travel", "housing", "home", "utilities",
    "insurance", "medical", "pets", "personal_care", "entertainment", "shopping", "services",
    "education", "childcare", "donations", "taxes", "government", "income", "transfer",
    "debt_payment", "interest", "fees", UNCATEGORIZED,
})

# Plaid personal finance categories. Published taxonomy, checked 2026-09-14:
# https://plaid.com/documents/transactions-personal-finance-category-taxonomy.csv
PRIMARY_FROM_PLAID = {
    "INCOME": "income",
    "TRANSFER_IN": "transfer",
    "TRANSFER_OUT": "transfer",
    "LOAN_PAYMENTS": "debt_payment",
    "BANK_FEES": "fees",
    "ENTERTAINMENT": "entertainment",
    "FOOD_AND_DRINK": "dining",
    "GENERAL_MERCHANDISE": "shopping",
    "HOME_IMPROVEMENT": "home",
    "MEDICAL": "medical",
    "PERSONAL_CARE": "personal_care",
    "GENERAL_SERVICES": "services",
    "GOVERNMENT_AND_NON_PROFIT": "government",
    "TRANSPORTATION": "transportation",
    "TRAVEL": "travel",
    "RENT_AND_UTILITIES": "utilities",
}
# Detailed labels that belong to a narrower FinPilot category than their primary label.
# Liquor stores and vending machines are not dining for card reward purposes.
DETAILED_FROM_PLAID = {
    "FOOD_AND_DRINK_GROCERIES": "groceries",
    "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": "shopping",
    "FOOD_AND_DRINK_VENDING_MACHINES": "shopping",
    "GENERAL_MERCHANDISE_PET_SUPPLIES": "pets",
    "MEDICAL_VETERINARY_SERVICES": "pets",
    "GENERAL_SERVICES_AUTOMOTIVE": "transportation",
    "GENERAL_SERVICES_CHILDCARE": "childcare",
    "GENERAL_SERVICES_EDUCATION": "education",
    "GENERAL_SERVICES_INSURANCE": "insurance",
    "GOVERNMENT_AND_NON_PROFIT_DONATIONS": "donations",
    "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT": "taxes",
    "RENT_AND_UTILITIES_RENT": "housing",
    "TRANSPORTATION_GAS": "gas",
    "BANK_FEES_INTEREST_CHARGE": "interest",
}
PROVIDER_LABEL = re.compile(r"[A-Z][A-Z0-9_]{0,99}")


def plaid_category(primary: str, detailed: str = "") -> str:
    """The FinPilot category for Plaid labels. Unknown labels stay uncategorized."""
    return DETAILED_FROM_PLAID.get(detailed) or PRIMARY_FROM_PLAID.get(primary) or UNCATEGORIZED


def plaid_kind(primary: str, detailed: str, outflow: bool) -> TxKind:
    """Transfers, repayments and interest must never count as income or ordinary purchases."""
    if primary == "INCOME":
        return TxKind.INCOME
    if primary in ("TRANSFER_IN", "TRANSFER_OUT"):
        return TxKind.INTERNAL_TRANSFER
    if detailed == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT":
        return TxKind.CARD_REPAYMENT
    if primary == "LOAN_PAYMENTS":
        return TxKind.LOAN_PAYMENT
    if detailed == "BANK_FEES_INTEREST_CHARGE":
        return TxKind.INTEREST
    if primary == "BANK_FEES":
        return TxKind.FEE
    # A credit on a purchase category is conditional until matched, not income.
    return TxKind.PURCHASE if outflow else TxKind.PROVISIONAL_CREDIT
