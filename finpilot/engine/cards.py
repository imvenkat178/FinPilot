"""Card selection by service -- spec sections 5 and 6.

"The principal numeric output is expected net value relative to the user's
available alternative." Reward value is computed after incremental fees,
foreign-transaction fees, lost discounts, price differences between channels,
and any expected additional borrowing cost. A promotional headline or an
aspirational point valuation is not evidence the user will realise that value.

Uncertain merchant coding produces a qualified comparison with a plausible
range, never an unconditional winner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional

from ..models import Card, Confidence, GraceState, RewardRule
from ..money import Money


class Channel(str, Enum):
    IN_PERSON = "in_person"
    ONLINE = "online"
    PORTAL = "portal"
    DIRECT = "direct"
    RECURRING_BILL = "recurring_bill"
    DELIVERY = "delivery"


@dataclass
class Purchase:
    amount: Money
    merchant: str = ""
    category: str = "base"
    channel: Channel = Channel.IN_PERSON
    mcc: Optional[str] = None
    mcc_certain: bool = True
    foreign: bool = False
    processing_fee_rate: Decimal = Decimal("0")     # a card-payment surcharge
    lost_discount: Money = None                     # e.g. an autopay-by-bank discount
    carries_balance_days: int = 0                   # 0 = paid in full within grace
    date: date = field(default_factory=date.today)

    def __post_init__(self):
        if self.lost_discount is None:
            self.lost_discount = Money.zero(self.amount.currency)


@dataclass
class Alternative:
    """The user's existing non-card option -- section 5 ranks against this."""
    name: str = "Existing bank or debit payment"
    reward: Money = None
    fee: Money = None
    discount_kept: Money = None

    def __post_init__(self):
        z = Money.zero("USD")
        self.reward = self.reward or z
        self.fee = self.fee or z
        self.discount_kept = self.discount_kept or z

    @property
    def net(self) -> Money:
        return self.reward - self.fee + self.discount_kept


@dataclass
class CardOption:
    card_id: str
    nickname: str
    mask: str
    eligible: bool
    reward_value: Money
    applied_rate: Decimal
    bonus_portion: Money
    base_portion: Money
    cap_remaining_before: Optional[Money]
    processing_fee: Money
    foreign_fee: Money
    lost_discount: Money
    incremental_interest: Money
    net_value: Money
    reward_units: Optional[Decimal]
    conversion_assumption: Optional[str]
    exclusions: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.EXACT

    def to_json(self) -> dict:
        return {
            "card_id": self.card_id, "nickname": self.nickname, "mask": self.mask,
            "eligible": self.eligible,
            "reward_value": self.reward_value.to_json(),
            "applied_rate": str(self.applied_rate),
            "bonus_portion": self.bonus_portion.to_json(),
            "base_portion": self.base_portion.to_json(),
            "cap_remaining_before": (self.cap_remaining_before.to_json()
                                     if self.cap_remaining_before else None),
            "processing_fee": self.processing_fee.to_json(),
            "foreign_fee": self.foreign_fee.to_json(),
            "lost_discount": self.lost_discount.to_json(),
            "incremental_interest": self.incremental_interest.to_json(),
            "net_value": self.net_value.to_json(),
            "reward_units": str(self.reward_units) if self.reward_units else None,
            "conversion_assumption": self.conversion_assumption,
            "exclusions": self.exclusions, "conditions": self.conditions,
            "confidence": self.confidence.value,
        }


@dataclass
class Ranking:
    purchase: Purchase
    options: list[CardOption]
    alternative: Alternative
    winner: Optional[CardOption]
    margin_over_next: Money
    qualified: bool
    explanation: str
    assumptions: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "purchase": {"amount": self.purchase.amount.to_json(),
                         "merchant": self.purchase.merchant,
                         "category": self.purchase.category,
                         "channel": self.purchase.channel.value,
                         "foreign": self.purchase.foreign},
            "alternative": {"name": self.alternative.name,
                            "net": self.alternative.net.to_json()},
            "winner": self.winner.to_json() if self.winner else None,
            "margin_over_next": self.margin_over_next.to_json(),
            "qualified": self.qualified, "explanation": self.explanation,
            "options": [o.to_json() for o in self.options],
            "assumptions": self.assumptions,
        }


def _matching_rule(card: Card, purchase: Purchase) -> Optional[RewardRule]:
    best: Optional[RewardRule] = None
    for r in card.rules:
        if r.category != purchase.category:
            continue
        if r.effective_from and purchase.date < r.effective_from:
            continue
        if r.effective_to and purchase.date > r.effective_to:
            continue
        if purchase.merchant and purchase.merchant.lower() in \
                [m.lower() for m in r.excluded_merchants]:
            continue
        if purchase.channel.value in r.excluded_channels:
            continue
        if r.requires_activation and not r.activated:
            continue
        if best is None or r.rate > best.rate:
            best = r
    return best


def _base_rule(card: Card) -> RewardRule:
    for r in card.rules:
        if r.category == "base":
            return r
    return RewardRule(card_id=card.id, category="base", rate=Decimal("0.01"))


def evaluate_card(card: Card, purchase: Purchase) -> CardOption:
    cur = purchase.amount.currency
    zero = Money.zero(cur)
    exclusions: list[str] = []
    conditions: list[str] = []
    confidence = Confidence.EXACT

    if card.excluded_by_user:
        return CardOption(card.id, card.nickname, card.mask, False, zero,
                          Decimal(0), zero, zero, None, zero, zero, zero, zero,
                          zero, None, None, ["Excluded by you"], [], confidence)

    rule = _matching_rule(card, purchase)
    base = _base_rule(card)

    # marginal rate across a cap: bonus on the eligible remainder, base on the rest
    bonus_portion = zero
    base_portion = purchase.amount
    applied = base.rate
    cap_before: Optional[Money] = None
    if rule is not None:
        cap_before = rule.cap_remaining()
        if cap_before is None:
            bonus_portion = purchase.amount
            base_portion = zero
        else:
            bonus_portion = purchase.amount.min(cap_before)
            base_portion = purchase.amount - bonus_portion
            if bonus_portion < purchase.amount:
                conditions.append(
                    f"Only {bonus_portion} of this purchase still fits the "
                    f"{rule.category} cap; the remainder earns the base rate.")
        applied = rule.rate
        if rule.requires_activation and not rule.activated:
            exclusions.append("Category requires activation that is not confirmed")
    else:
        exclusions.append(f"No {purchase.category} rule on this card; base rate applies")

    units = (bonus_portion.amount * applied + base_portion.amount * base.rate)
    reward_value = Money(units * card.point_value / Decimal("0.01") * Decimal("0.01"), cur).round() \
        if card.reward_currency != "cashback" else Money(units, cur).round()
    conversion = None
    if card.reward_currency != "cashback":
        reward_value = Money(units * (card.point_value / Decimal("0.01")) * Decimal("0.01"), cur).round()
        conversion = (f"{units} {card.reward_currency} valued at "
                      f"{card.point_value} each. Points and the conversion "
                      "assumption are shown separately; a promotional valuation is "
                      "not evidence you will realise it.")

    processing_fee = (purchase.amount * purchase.processing_fee_rate).round()
    foreign_fee = (purchase.amount * card.foreign_transaction_fee).round() \
        if purchase.foreign else zero
    if purchase.foreign and card.foreign_transaction_fee > 0:
        conditions.append(
            f"Foreign transaction fee of {card.foreign_transaction_fee * 100}% applies. "
            "Local-currency and home-currency totals are kept distinct and the "
            "exchange rate is a dated assumption.")

    # incremental borrowing cost -- section 5 / S14
    incremental_interest = zero
    if card.grace_state == GraceState.LOST:
        days = purchase.carries_balance_days or 30
        incremental_interest = (purchase.amount * card.purchase_apr
                                * Decimal(days) / Decimal("365")).round()
        conditions.append(
            f"The purchase grace period on this card is lost, so new purchases "
            f"accrue from the transaction date. Modelled at {days} days.")
    elif card.grace_state == GraceState.UNKNOWN:
        confidence = Confidence.BOUNDED
        conditions.append(
            "Grace-period treatment on this card is unconfirmed, so no "
            "unconditional net benefit is claimed.")
    elif purchase.carries_balance_days > 0:
        incremental_interest = (purchase.amount * card.purchase_apr
                                * Decimal(purchase.carries_balance_days)
                                / Decimal("365")).round()
        conditions.append(
            f"Assumes the balance is carried for {purchase.carries_balance_days} days.")

    if not purchase.mcc_certain:
        confidence = Confidence.BOUNDED
        conditions.append(
            "Merchant coding for this purchase is uncertain. The ordering could "
            "change if it codes differently.")

    net = reward_value - processing_fee - foreign_fee - purchase.lost_discount \
        - incremental_interest

    return CardOption(
        card_id=card.id, nickname=card.nickname, mask=card.mask,
        eligible=not exclusions or rule is not None,
        reward_value=reward_value, applied_rate=applied,
        bonus_portion=bonus_portion, base_portion=base_portion,
        cap_remaining_before=cap_before, processing_fee=processing_fee,
        foreign_fee=foreign_fee, lost_discount=purchase.lost_discount,
        incremental_interest=incremental_interest, net_value=net,
        reward_units=units if card.reward_currency != "cashback" else None,
        conversion_assumption=conversion, exclusions=exclusions,
        conditions=conditions, confidence=confidence)


def rank_cards(cards: list[Card], purchase: Purchase,
               alternative: Optional[Alternative] = None,
               repayment_feasible: bool = True) -> Ranking:
    cur = purchase.amount.currency
    alt = alternative or Alternative(reward=Money.zero(cur), fee=Money.zero(cur),
                                     discount_kept=Money.zero(cur))
    options = [evaluate_card(c, purchase) for c in cards]
    usable = [o for o in options if o.eligible and not o.exclusions]
    usable.sort(key=lambda o: o.net_value.amount, reverse=True)

    assumptions = [
        "Net value is reward value after incremental fees, price differences, lost "
        "discounts and any expected borrowing cost.",
        "Non-cash benefits are listed separately and are not given a face value.",
        "An annual fee already paid is not a cost of this purchase; annual "
        "ownership value is reviewed separately.",
    ]
    if not repayment_feasible:
        assumptions.insert(0,
            "Your statement-payment plan does not currently cover this purchase, so "
            "the card recommendation is withheld. The credit limit is not extra "
            "spending capacity.")
        return Ranking(purchase, options, alt, None, Money.zero(cur), True,
                       "Repayment is not feasible from the current plan, so no card "
                       "is recommended for this purchase.", assumptions)

    if not usable:
        return Ranking(purchase, options, alt, None, Money.zero(cur), True,
                       "No owned card is eligible for this purchase. Use your "
                       "existing bank or debit option.", assumptions)

    winner = usable[0]
    runner_net = usable[1].net_value if len(usable) > 1 else alt.net
    margin = winner.net_value - runner_net
    qualified = winner.confidence != Confidence.EXACT or not purchase.mcc_certain

    if winner.net_value <= alt.net:
        explanation = (
            f"{alt.name} is the better option here: it nets {alt.net} against "
            f"{winner.net_value} on {winner.nickname} ····{winner.mask}"
            + (f", because the {winner.processing_fee} card processing fee exceeds "
               f"the {winner.reward_value} of rewards." if winner.processing_fee.is_positive
               else "."))
    else:
        explanation = (
            f"{winner.nickname} ····{winner.mask} nets {winner.net_value} on this "
            f"{purchase.amount} {purchase.category} purchase at "
            f"{winner.applied_rate * 100}%"
            + (f", {margin} ahead of the next option." if len(usable) > 1
               else ", the only eligible card."))
    if qualified:
        explanation += (" This is a qualified comparison: at least one term is "
                        "unconfirmed, so the ordering could change.")
    return Ranking(purchase, options, alt, winner, margin, qualified, explanation,
                   assumptions)


# ---------------------------------------------------------------------------
# Channel price comparison -- section 6's hotel example
# ---------------------------------------------------------------------------

def compare_channels(direct_price: Money, direct_rate: Decimal,
                     portal_price: Money, portal_rate: Decimal,
                     label_direct: str = "Direct booking",
                     label_portal: str = "Portal booking") -> dict:
    """A higher earning rate cannot overcome a larger price premium by itself."""
    d_reward = (direct_price * direct_rate).round()
    p_reward = (portal_price * portal_rate).round()
    d_net = direct_price - d_reward
    p_net = portal_price - p_reward
    cheaper, dearer = ((label_direct, d_net), (label_portal, p_net)) \
        if d_net <= p_net else ((label_portal, p_net), (label_direct, d_net))
    return {
        "direct": {"label": label_direct, "price": direct_price.to_json(),
                   "rate": str(direct_rate), "reward": d_reward.to_json(),
                   "net_cost": d_net.to_json()},
        "portal": {"label": label_portal, "price": portal_price.to_json(),
                   "rate": str(portal_rate), "reward": p_reward.to_json(),
                   "net_cost": p_net.to_json()},
        "cheaper": cheaper[0],
        "difference": (dearer[1] - cheaper[1]).to_json(),
        "finding": (f"{cheaper[0]} costs {(dearer[1] - cheaper[1])} less after "
                    "rewards."),
        "caveat": ("Room type, cancellation terms, taxes and other mandatory "
                   "charges must be comparable for this to be a like-for-like "
                   "result. Additional qualifying benefits are shown separately."),
    }


def bill_payment_comparison(bill_amount: Money, card_rate: Decimal,
                            card_processing_fee_rate: Decimal,
                            bank_payment_fee: Optional[Money] = None,
                            lost_autopay_discount: Optional[Money] = None) -> dict:
    """RW06 / section 6's $1,000 bill example."""
    cur = bill_amount.currency
    bank_fee = bank_payment_fee or Money.zero(cur)
    lost = lost_autopay_discount or Money.zero(cur)
    reward = (bill_amount * card_rate).round()
    fee = (bill_amount * card_processing_fee_rate).round()
    card_net = reward - fee - lost
    bank_net = -bank_fee
    better = "card" if card_net > bank_net else "bank"
    return {
        "card": {"reward": reward.to_json(), "processing_fee": fee.to_json(),
                 "lost_discount": lost.to_json(), "net": card_net.to_json()},
        "bank": {"fee": bank_fee.to_json(), "net": bank_net.to_json()},
        "better": better,
        "difference": (card_net - bank_net if better == "card"
                       else bank_net - card_net).to_json(),
        "finding": (f"Paying by bank avoids a {(bank_net - card_net)} net cost."
                    if better == "bank"
                    else f"The card is {(card_net - bank_net)} better on this bill."),
    }


def interest_vs_reward(amount: Money, apr: Decimal, days: int,
                       reward_rate: Decimal, day_count: int = 365) -> dict:
    """Section 6's fourth row: when interest exceeds the reward."""
    interest = (amount * apr * Decimal(days) / Decimal(day_count)).round()
    reward = (amount * reward_rate).round()
    return {
        "amount": amount.to_json(), "apr": str(apr), "days": days,
        "added_interest": interest.to_json(), "reward": reward.to_json(),
        "net": (reward - interest).to_json(),
        "finding": (f"Interest exceeds rewards by {(interest - reward)} before other "
                    "costs." if interest > reward else
                    f"Rewards exceed interest by {(reward - interest)}."),
        "caveat": ("A simplified estimate. Actual issuer accrual, posting and "
                   "repayment rules govern the production calculation."),
    }


# ---------------------------------------------------------------------------
# CR02 -- statement-close utilization timing (Version 4 addition)
# ---------------------------------------------------------------------------

def utilization_timing(card: Card, planned_payment: Money,
                       today: date) -> dict:
    """The calculation missing from Version 3.

    Reported utilization is a snapshot at statement close, not at the due date.
    Paying before close lowers what the issuer reports while still preserving the
    grace period; paying at the due date does not.
    """
    cur = card.current_balance.currency
    limit = card.credit_limit
    before = card.utilization
    after_balance = (card.current_balance - planned_payment).clamp_min_zero()
    after = after_balance.ratio(limit) if not limit.is_zero else Decimal(0)

    close_day = card.statement_close_day
    due_day = card.payment_due_day
    return {
        "card": f"{card.nickname} ····{card.mask}",
        "credit_limit": limit.to_json(),
        "current_balance": card.current_balance.to_json(),
        "statement_close_day": close_day,
        "payment_due_day": due_day,
        "reported_utilization_if_paid_at_due_date": f"{round(before * 100, 1)}%",
        "reported_utilization_if_paid_before_close": f"{round(after * 100, 1)}%",
        "planned_payment": planned_payment.to_json(),
        "grace_period_effect": (
            "Paying the full required statement amount by the payment due date "
            "preserves the grace period in both cases. Paying earlier does not "
            "forfeit it."),
        "finding": (
            f"Paying {planned_payment} before the statement closes on day "
            f"{close_day} would have the issuer report about "
            f"{round(after * 100, 1)}% utilization instead of "
            f"{round(before * 100, 1)}%. The interest outcome is unchanged; only "
            "the reported figure differs."),
        "caveat": (
            "Score effects are shown as factors and direction. No point figure is "
            "quoted, because the model cannot support one."),
    }
