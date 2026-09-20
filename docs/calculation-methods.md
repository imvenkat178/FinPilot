# Calculation methods

Every financial number in FinPilot comes from deterministic code in `finpilot/engine/`. The assistant chooses calculations and explains their results; it never computes amounts itself. This page describes each method, its defaults and its limits so the results can be reviewed.

Conventions:

- Amounts are exact decimals, never floating point. Rates are fractions: 5% is `0.05`.
- Results list their assumptions and a confidence label. `exact` means every input is confirmed; `bounded` means at least one input is estimated or unconfirmed.
- The worked examples below are asserted in `tests/test_worked_examples.py`. Engine docstrings cite sections of the product specification, which is not in this repository (roadmap `G13.5`).

These are planning estimates, not tax, legal or investment advice. Real contracts, servicers and tax situations can differ.

## Cash forecast

Engine: `LedgerEngine.forecast` in `finpilot/engine/ledger.py`. API: `GET /api/forecast`, 45 days by default.

1. Start from the account's available balance on the workspace's as-of date.
2. Add dated entries inside the window:
   - income events that land in the account, using the received date and amount once received, otherwise the expected ones marked unconfirmed
   - bill occurrences funded from the account, for the amount still unpaid; overdue occurrences are placed on the first day
   - pending transactions that the available balance does not already reflect
3. Roll a daily balance: closing = opening + inflows - outflows.

The result reports the ending balance, the low point (the lowest closing balance) and every day the balance would be negative. Without an account ID, the first checking account is used.

## Spending allowance

Engine: `LedgerEngine.spending_allowance`. API: `GET /api/allowance`, 14 days by default.

allowance = max(0, lowest projected closing balance in the window - protected reserves funded in that account)

The allowance uses the lowest balance, not the ending balance, because a positive balance at the end of the window does not pay a bill due earlier. Credit limits, home equity, retirement holdings and pending refunds are excluded. Confidence drops to `bounded` when the account has not refreshed recently or the window contains predicted rather than confirmed items.

## Paycheck allocation

Engine: `AllocationEngine` in `finpilot/engine/allocator.py`. API: `GET /api/plan`, the current month by default.

**Monthly targets.** Each active recurring rule gets a monthly target:

| Rule method | Monthly target |
| --- | --- |
| Fixed | The rule's monthly target, or its amount |
| Percent of income | Percent of monthly income, or of the surplus after required and protected commitments when the rule says so |
| Target balance | The linked reserve's remaining amount, or the target balance minus the account's available balance |
| Target by date | The remaining amount divided by the whole months left until the target date, at least one |
| Surplus share | Percent of the surplus after required and protected commitments |

Monthly income is the sum of the month's income events, or of the income sources' scheduled occurrences when there are no events. Due dates come from a linked bill, the liability's due day or the rule's schedule. Amounts already funded this period count toward the target, so a target is funded once across all paychecks.

**Each paycheck** is allocated in four passes:

1. Required or protected targets due before the next paycheck, earliest due date first. For the last paycheck in the month, every dated required target counts.
2. The protected checking floor, held back from what remains.
3. Required targets due later: deferred when a later paycheck in the month can fund them, otherwise funded now.
4. Everything else in the saved priority order. Percent-of-income and surplus-share rules come after fixed and protected targets. Targets without a deadline are spread evenly: remaining target ÷ (paychecks left after this one + 1).

A skipped occurrence is recorded as paused. When a required or protected item cannot be fully funded, it is listed as a shortfall with its consequence, such as a late fee or a lost grace period, and optional transfers stop. The monthly plan is marked overcommitted when its targets exceed expected income.

Tests: `test_section4_illustrative_paycheck`, `test_section4_shortfall_is_named_before_optional_allocations`, `test_mortgage_deadline_forces_first_paycheck`, `test_monthly_target_funded_once_across_paychecks`, `test_smaller_paycheck_preserves_required_and_pauses_optional`.

## Debt payoff comparison

Engine: `DebtSimulator` in `finpilot/engine/debt.py`. API: `POST /api/debt/compare` and `POST /api/debt/what-if`. Only debts with a known rate and required payment are simulated; a linked debt whose bank did not report them is listed under `missing_terms` and still counts in debt totals.

Each simulated month, for up to 720 months:

1. Interest accrues on every balance at APR ÷ 12, rounded to cents, and is added to the balance. Promotional zero-rate debts accrue nothing.
2. Required minimum payments are made, capped at the balance and the remaining budget.
3. The rest of the budget pays extra according to the strategy.

| Strategy | Extra payment order |
| --- | --- |
| Highest rate first | Highest APR first; the lowest-cost benchmark |
| Smallest balance first | Smallest balance first |
| Equal extra shares | Equal split, with any excess spilling to the smallest remaining balance |
| Monthly payment relief | Highest minimum-to-balance ratio first |
| Preserve liquidity | Minimum payments only |
| Promotional deadline first | Earliest promotional expiry first |
| Custom | A chosen order, then highest rate |

Every strategy uses the same budget and starting balances. Results show months to clear, total interest, total paid, the payoff order and each strategy's interest premium over highest rate first.

- **Compare:** budget = all minimum payments + the extra payment, 500 when none is given. It compares highest rate, smallest balance and equal shares.
- **What if:** runs highest rate first with minimums only and with the extra amount, then reports months and interest saved next to the 30-day spending allowance of the first checking account.

Not modelled: new purchases, escrow, insurance, penalties, promotions and fees.

Tests: `test_section8_budget_split`, `test_section8_each_strategy`, `test_section8_premiums`, `test_section8_first_month_allocation`, `test_no_balance_ever_goes_negative`.

## Mortgage scenarios

Engine: `mortgage_scenarios` and `biweekly_comparison` in `finpilot/engine/debt.py`. API: `POST /api/mortgage/scenarios` and `POST /api/mortgage/biweekly`. A linked mortgage is modelled once principal and interest and escrow are entered separately.

- **Amortized payment:** P × i(1 + i)^n ÷ ((1 + i)^n - 1), with i = APR ÷ 12. With a zero rate, P ÷ n.
- **Run-off:** monthly interest at APR ÷ 12 rounded to cents; each payment is capped at the balance.
- **Scenarios:** continue scheduled payments; add extra principal every month; pay a lump sum and keep the installment; pay a lump sum and recast. A recast re-amortizes over the remaining term; the app assumes a 250 recast fee and a 240-month term when none is recorded.
- **Biweekly:** a half payment is the monthly payment ÷ 2. Twenty-six half payments pay one extra monthly payment a year, so the engine adds that extra ÷ 12 to the monthly plan before comparing. Most of the saving comes from paying more money, not from the cadence.
- **Promotional payoff reserve:** per paycheck = (promotional balance - other expected reductions) ÷ paychecks remaining.

Escrow, taxes and insurance are excluded. Test: `test_section18_promo_reserve`.

## Card selection

Engine: `rank_cards` in `finpilot/engine/cards.py`. API: `POST /api/cards/choose` and `POST /api/cards/utilization`.

For each card:

1. Pick the highest-rate category rule that applies on the purchase date and is not excluded by merchant, channel or missing activation.
2. Apply the bonus rate to the part of the purchase within the remaining category cap and the base rate to the rest. Without a base rule, the base rate is 1%.
3. Value points at the card's point value; cash back is valued directly.
4. net value = reward - processing fee - foreign transaction fee - lost discount - incremental interest

Incremental interest is amount × purchase APR × days ÷ 365. It applies for 30 days when the card's grace period is lost, or for the stated days when a balance will be carried. Unknown grace-period status makes the result `bounded`.

The card with the highest net value wins and is compared with paying by bank. Uncertain merchant coding makes the result a qualified comparison. The app recommends no card when the 30-day spending allowance of the first checking account is below the purchase amount.

Related comparisons:

- **Card or bank for a bill:** card net = reward - processing fee - lost autopay discount; bank net = -bank fee. The assistant assumes a 2% reward and a 2.95% processing fee unless told otherwise.
- **Booking channels:** net cost = price - price × reward rate for each channel.
- **Interest versus reward:** interest = amount × APR × days ÷ 365, compared with amount × reward rate.
- **Utilization timing:** utilization = balance ÷ credit limit. Issuers report it at statement close, so paying before the close lowers the reported figure without changing interest. The planned payment defaults to the current balance.

Tests: `test_section6_dinner`, `test_section6_hotel_channels`, `test_section6_bill_with_card_fee`, `test_section6_interest_exceeds_reward`.

## Buffer, liquidity and payment timing

Engine: `finpilot/engine/liquidity.py`. API: `GET /api/buffer` and `GET /api/liquidity`.

**Operating buffer**

- drawdown = max(0, required bills and pending outflows + optional bills - confirmed inflows within the replenishment window)
- floor = the higher of the operating-floor reserve target and the bank minimum balance
- required retained = drawdown + floor + uncertainty allowance
- sweepable = max(0, available - required retained)

The app uses a 30-day horizon, a 5-day replenishment window and an uncertainty allowance of 10% of the outflows. Worked example: available 8,000, bills 3,500, spending 600, floor 1,000 and uncertainty 400 retain 5,500 and leave 2,500 sweepable (`test_section15_buffer`).

**Liquidity tiers** by account type:

| Tier | Account types |
| --- | --- |
| Available now | Checking, cash |
| Same day | Savings, money market deposit accounts |
| 1-3 days | Brokerage sweep |
| On a date | Certificates of deposit |
| Investment | Brokerage, retirement, money market funds |
| Borrowing capacity | Securities-based lines, margin |
| Estimated | Estimated assets |

Debts are excluded. Near-term cash is the available balance in the first two tiers.

**Growth and interest**

- APY growth = principal × ((1 + APY)^(days ÷ 365) - 1)
- simple interest = principal × APR × days ÷ 365

**Sweep assessment:** gain = growth at the destination APY - growth at the source APY; tax = gain × combined marginal rate; net = gain - tax - transfer fee. A move is worthwhile only when the net is at least 1.00. The assistant also blocks moves larger than the sweepable amount.

**Payment timing:** the latest safe start date moves the due date to the preceding business day, then back 4 business days: 2 for the payment rail, 1 for the biller cutoff and 1 for safety. The engine compares after-tax cash earnings until that date with the borrowing cost avoided by paying earlier, and recommends paying now when waiting costs more. Worked example: 10,000 held 10 days at 5% APY earns about 13.38, or about 9.50 after a 29% tax rate, while delaying a 24% loan reduction costs 65.75, so paying earlier is about 56.25 better.

Tests: `test_section16_apy_growth_ten_days`, `test_section16_after_tax`, `test_section16_delaying_a_loan_reduction_costs_more`, `test_section16_small_amount_is_not_worth_a_fee`.

## Savings versus debt

Engine: `compare_savings_vs_debt` in `finpilot/engine/tax.py`. API: `POST /api/tax/net-benefit`, 365 days by default.

- **Tax rate on interest:** federal marginal + state marginal + net investment income tax when it applies. Treasury interest skips the state rate. The default profile is 24% federal and 5% state, not itemizing and not verified.
- **Baseline:** gross interest = amount × APY × days ÷ 365; net interest = gross × (1 - tax rate).
- **Each debt:** applied = min(amount, balance); avoided interest = applied × rate × years. When the interest is deductible and the profile itemizes, the benefit is reduced by avoided interest × federal marginal rate. Cash that is not applied keeps earning after-tax interest.
- **Break-even cash yield:** debt benefit ÷ ((1 - tax rate) × applied × years), the highest across debts.

The app uses the first savings account with a known APY. Results are `exact` only when the tax profile is verified. Worked example: 10,000 at 5% for a year earns 500 gross and 355 net at 24% plus 5%. Paying down a nondeductible 4% debt instead gives 400, or 45 more; a 3% debt gives 300, or 55 less; a 4% debt with a fully usable 24% deduction gives 304, or 51 less.

Interest deductibility is never assumed from the loan type. Mortgage, student loan, auto loan and investment interest are conditional; personal credit card interest is generally not deductible.

Tests: `test_section17_baseline_after_tax_interest`, `test_section17_table12`, `test_section17_table12_margins`, `test_treasury_interest_is_exempt_from_state_tax`.

## Deposit insurance coverage

Engine: `build_coverage` in `finpilot/engine/coverage.py`. API: `GET /api/coverage`.

Coverage limits are versioned by effective date:

| Regime | Standard limit |
| --- | --- |
| FDIC (US banks) | 250,000, with a 1.25 million trust cap |
| NCUA (US credit unions) | 250,000; a combined trust rule applies from 1 December 2026 |
| FSCS (UK) | £85,000, raised to £120,000 from 1 December 2025 |
| EU deposit guarantee schemes | €100,000 |
| DICGC (India) | ₹5 lakh |
| FCS (Australia) | A$250,000 |

Deposit balances are grouped by institution, owner and ownership category. For each group, covered = min(total, limit) and uncovered = total - limit. Sweep programs are looked through to their participating banks, and any balance without a confirmed allocation or institution is listed as unresolved. Money market funds and brokerage balances are shown under SIPC custody protection, which is not deposit insurance. Adding accounts at the same institution does not add coverage.

- **Remedy:** moving the uncovered excess plus a 500 buffer below the limit.
- **Collateral stress:** borrowing capacity = collateral value × advance rate, tested against market declines (30% by default) and reduced advance rates; any drawn amount above capacity is a deficiency.

Tests: `test_section19_coverage_example`, `test_ncua_trust_rule_changes_on_1_december_2026`, `test_fscs_limit_is_versioned_by_effective_date`, `test_adding_accounts_does_not_multiply_coverage`, `test_section20_collateral_stress`.

## Recurring rule activity

Engine: `finpilot/engine/recurring.py`. API: `GET /api/recurring/activity`, 60 days by default.

For each recurring rule, the engine lists the dated runs inside the horizon and whether each will run or is stopped by a pause, a one-time skip or missing authorization. It shows the consequence before the date arrives rather than after a run fails.

## Evidence confidence

Assistant answers carry an evidence confidence level from `evidence_confidence` in `finpilot/ai/evidence.py`. Refusals, unrelated requests, saved notes and unrecognized requests get none.

An answer starts at 100 and loses points once for each condition that applies. A calculation error sets the score to 20 instead.

| Condition | Points |
| --- | --- |
| The answer quotes document excerpts rather than account data | -30 |
| The answer repeats earlier conversation text | -40 |
| The calculation reports `bounded` or `approximate` confidence | -15 |
| The calculation reports `directional` confidence | -30 |
| The calculation reports `insufficient` confidence | -50 |
| Inputs still need confirming | -15 |
| A linked account is stale or its connection needs attention | -25 |
| A linked account holds estimated values | -15 |
| A linked account balance has no reported effective time | -5 |
| A linked debt lacks its rate or required payment | -10 |
| The answer depends on stated assumptions | -5 |

Scores of 85 or more are `high`, 60 to 84 `medium` and below 60 `low`. Linked accounts are the accounts of every record the answer links.
