import {
  S,
  esc,
  val,
  money,
  pct,
  human,
  fullDate,
  dateLabel,
  account,
  access,
  chip,
  link,
  btn,
  heading,
  panel,
  empty,
  note,
  row,
  table,
  cells,
  forecastChart,
  sourceNote,
  icon,
} from "./core.js";
import { manageButton } from "./manage.js";
import { goalCard } from "./pages.js";
export function accountPage() {
  const a = account(S.accountId);
  if (!a)
    return heading(
      "Account unavailable",
      "This account may no longer be in the workspace.",
      link("All accounts", "accounts"),
    );
  const w = S.workspace,
    loan = w.liabilities.find((l) => l.account_id === a.id),
    card = w.cards.find((c) => c.account_id === a.id),
    reserves = w.reserves.filter((r) => r.account_id === a.id);
  const tabs = [
    ["summary", "Summary"],
    ["activity", "Activity"],
    ["analytics", "Analytics"],
    ["details", "Details"],
  ];
  const currentTab = tabs.find(([id]) => id === S.tab) || tabs[0];
  const balanceLabel = card
    ? "Statement balance"
    : loan
      ? "Outstanding principal"
      : "Current balance";
  const balance = card
    ? card.statement_balance
    : loan
      ? loan.balance
      : a.current;
  const verification = a.provenance?.verification;
  const needsAttention = !a.connection_healthy || verification === "stale";
  const sourceStatus = chip(
    needsAttention ? "Needs attention" : human(verification),
    needsAttention ? "orange" : verification === "confirmed" ? "green" : "neutral",
  );
  return (
    `<a class="text-button back-link" href="#accounts">← All accounts</a>` +
    `<div class="account-detail-heading">` +
    heading(
      a.nickname,
      [a.institution, a.mask ? `Account ending ${a.mask}` : ""].filter(Boolean).join(" · "),
      manageButton("Edit account", `accounts:${a.id}`) + manageButton("Import CSV", `import:${a.id}`) + btn("Source & connection", `source:${a.id}`, "shield"),
    ) +
    `</div><div class="wide-summary account-summary"><div class="account-balance"><p>${balanceLabel}</p><strong class="amount">${money(balance, true)}</strong></div><div class="account-source"><strong>${esc(access(a))}</strong><p>${esc(sourceNote(a))}</p></div>${sourceStatus}</div><div class="tabs account-tabs" role="tablist" aria-label="Account views">${tabs.map(([id, label]) => `<button id="account-tab-${id}" class="account-tab ${currentTab[0] === id ? "active" : ""}" role="tab" aria-selected="${currentTab[0] === id}" aria-controls="account-panel" data-account-tab="${id}">${label}</button>`).join("")}</div><div id="account-panel" role="tabpanel" aria-labelledby="account-tab-${currentTab[0]}">${currentTab[0] === "activity" ? activity(a) : currentTab[0] === "analytics" ? analytics(a, loan, card, reserves) : currentTab[0] === "details" ? details(a, loan, card) : summary(a, loan, card, reserves)}</div>`
  );
}
function summary(a, loan, card, reserves) {
  const snapshot =
    row("Current balance", money(a.current, true)) +
    row("Available balance", money(a.available, true)) +
    row("Pending", money(a.pending, true)) +
    row("Reserved by account", money(a.reserved, true)) +
    row("Held funds", money(a.held, true)) +
    row("Institution", a.institution) +
    row("Ownership", a.owner_display || human(a.ownership_category));
  let focused = "";
  if (card)
    focused = panel(
      "Credit card statement",
      row("Statement balance", money(card.statement_balance, true)) +
        row("Current balance", money(card.current_balance, true)) +
        row("Credit limit", money(card.credit_limit)) +
        row("Utilization", pct(card.utilization)) +
        row("Payment due", "Day " + card.payment_due_day) +
        row("Grace period", human(card.grace_state)) +
        btn("Review statement", `card:${card.id}`),
    );
  else if (loan)
    focused = panel(
      "Loan at a glance",
      row("Outstanding balance", money(loan.balance, true)) +
        row("Contract rate", pct(loan.apr)) +
        row("Monthly principal & interest", money(loan.minimum_payment)) +
        row("Total required payment", money(loan.total_required_payment)) +
        row("Next due day", loan.due_day) +
        link("Compare repayment strategies", "debt"),
    );
  else if (a.type === "checking")
    focused = panel(
      "Available for your plan",
      row("Bank available", money(a.available)) +
        row("Operating floor", money(S.data.buffer.operating_floor)) +
        row("Required retained cash", money(S.data.buffer.required_retained)) +
        row("Safe to spend", money(S.data.allowance.amount)) +
        note(
          "Safe to spend follows the projected low point after obligations and protected reserves, not just the displayed bank balance.",
        ) +
        link("Explore checking cash flow", "cashflow"),
    );
  else if (reserves.length)
    focused = panel(
      "Reserved inside this account",
      reserves
        .map(
          (r) =>
            `<button class="item-row" data-detail="reserve:${esc(r.id)}"><span class="row-body"><strong class="row-title">${esc(r.name)}</strong><span class="row-sub">Target ${money(r.target)}</span></span><strong>${money(r.funded)}</strong>${icon("arrow")}</button>`,
        )
        .join(""),
    );
  else
    focused = panel(
      "Balance treatment",
      row("Account type", human(a.type)) +
        row("Availability", access(a)) +
        row("Used in planning", a.included_in_planning ? "Yes" : "No") +
        note(
          a.type === "estimated_asset"
            ? "This is an estimated asset value. It contributes to net worth and is never payment capacity."
            : a.type === "retirement"
              ? "Retirement assets contribute to net worth and are excluded from payment capacity."
              : "Transfer time and account restrictions affect whether this balance can fund a payment.",
        ),
    );
  return `<div class="content-grid">${panel("Account snapshot", snapshot)}${focused}</div>${!a.connection_healthy ? `<div class="section-gap">${note(a.connection_issue || "This connection needs attention. Confirm the balance at its source.")}</div>` : ""}`;
}
function activity(a) {
  const tx = S.workspace.transactions.filter((t) => t.account_id === a.id);
  const planned = S.data.plan.paychecks.flatMap((p) =>
    p.allocations
      .filter((x) => x.destination_account_id === a.id && val(x.amount) > 0)
      .map((x) => ({ ...x, date: p.pay_date, pay: p.income_event_id })),
  );
  const income = S.workspace.income_events.filter(
    (e) =>
      S.workspace.income_sources.find((s) => s.id === e.source_id)
        ?.deposit_account_id === a.id,
  );
  return (
    panel(
      S.workspace.transactions_truncated ? "Recent transactions" : "Transactions",
      tx.length
        ? table(
            ["Date", "Description", "Type", "State", "Amount"],
            tx.map((t) =>
              cells(
                fullDate(t.date),
                esc(t.description || t.merchant || "Transaction"),
                human(t.kind),
                chip(human(t.state)),
                money(t.amount, true),
              ),
            ),
          )
        : empty(
            "No transactions on file",
            "Import transaction history to see activity for this account.",
          ),
      manageButton("View all history", `history:${a.id}`),
      S.workspace.transactions_truncated ? "Recent records are shown here. Open full history to search every transaction." : "Recorded transactions are separate from planned allocations.",
    ) +
    `<div class="section-gap">${panel(
      "Paycheck plan activity",
      planned.length
        ? table(
            ["Planned date", "Destination", "Allocation"],
            planned.map((p) =>
              cells(
                dateLabel(p.date),
                `<button class="table-link" data-detail="allocation:${esc(p.pay)}:${esc(p.policy_id)}">${esc(p.name)}</button>`,
                money(p.amount),
              ),
            ),
          )
        : empty("No incoming allocations in this plan"),
      "",
      "Planned allocations are separate from posted transactions.",
    )}</div>${
      income.length
        ? `<div class="section-gap">${panel(
            "Income events",
            table(
              ["Source", "Date", "Amount", "Status"],
              income.map((e) =>
                cells(
                  esc(
                    S.workspace.income_sources.find((s) => s.id === e.source_id)
                      ?.name || "Income",
                  ),
                  dateLabel(e.received_date || e.expected_date),
                  money(e.received_amount || e.expected_amount),
                  chip(
                    e.is_received ? "Received" : "Expected",
                    e.is_received ? "green" : "neutral",
                  ),
                ),
              ),
            ),
          )}</div>`
        : ""
    }`
  );
}
function analytics(a, loan, card, reserves) {
  if (card)
    return `<div class="content-grid">${panel("Credit utilization", `<div class="utilization-hero"><strong>${pct(card.utilization)}</strong><span>of ${money(card.credit_limit)} credit limit</span></div><progress value="${Math.min(100, Number(card.utilization) * 100)}" max="100" aria-label="Credit utilization"></progress>${row("Current balance", money(card.current_balance, true))}${row("Statement closes", "Day " + card.statement_close_day)}${row("Payment due", "Day " + card.payment_due_day)}<form id="utilization-form" class="stack-form"><input type="hidden" name="card_id" value="${esc(card.id)}"><label>Illustrative payment<input type="number" name="payment" value="100" min="0" step="0.01" required></label><button class="button primary">Check timing & utilization</button></form><div id="utilization-result"></div>`)}${panel("Card costs & rewards", row("Purchase APR", pct(card.purchase_apr)) + row("Annual fee", money(card.annual_fee)) + row("Foreign transaction fee", pct(card.foreign_transaction_fee)) + row("Reward currency", human(card.reward_currency)) + `<form id="card-form" class="stack-form"><label>Purchase amount<input name="amount" type="number" min="0.01" value="80" step="0.01" required></label><label>Category<select name="category"><option value="dining">Dining</option><option value="groceries">Groceries</option><option value="travel">Travel</option><option value="base">Other purchases</option></select></label><button class="button">Compare cards for purchase</button></form><div id="card-result"></div>`)}</div>`;
  if (loan)
    return `<div class="content-grid">${panel("Repayment inputs", row("Balance", money(loan.balance, true)) + row("Contract rate", pct(loan.apr)) + row("Rate type", human(loan.rate_type)) + row("Principal & interest", money(loan.minimum_payment)) + row("Escrow", money(loan.escrow)) + row("Mortgage insurance", money(loan.mortgage_insurance)) + row("Remaining term", loan.remaining_term_months ? loan.remaining_term_months + " months" : "Not provided"))}${panel("Explore repayment options", note("Compare the household’s recorded liabilities under one monthly budget. The calculator uses stored balances and rates.") + link("Open strategy comparison", "debt") + (a.type === "mortgage" ? `<form id="mortgage-form" class="stack-form"><label>Extra monthly payment<input name="extra" type="number" min="0" value="100" step="0.01" required></label><label>One-time principal payment<input name="lump" type="number" min="0" value="0" step="0.01" required></label><button class="button primary">Compare mortgage scenarios</button></form><div id="mortgage-result"></div>` : ""))}</div>`;
  if (a.type === "checking")
    return panel(
      "Checking balance outlook",
      forecastChart(S.data.forecast) +
        note(
          "The ledger projects future balances from income and obligations. It does not represent past investment performance.",
        ) +
        link("Explore the daily forecast", "cashflow"),
    );
  if (reserves.length)
    return `<div class="goals-grid">${reserves.map(goalCard).join("")}</div>`;
  return `<div class="content-grid">${panel("Balance & liquidity", row("Current value", money(a.current, true)) + row("Available balance", money(a.available, true)) + row("APY", pct(a.apy)) + row("Availability", access(a)) + row("Source date", fullDate(a.provenance?.as_of)))}${panel("Historical performance", empty("No performance history on file", "A current balance alone cannot establish growth, investment returns, or a trend."))}</div>`;
}
function details(a, loan, card) {
  const linked = /^Plaid\b/i.test(a.provenance?.source || "");
  const recordType = /manual|user[ _-]?estimate/i.test(a.provenance?.source || "") ? "Manual record" : "Recorded balance";
  return `<div class="content-grid">${panel("Account details", row("Institution", a.institution) + row("Account ending", a.mask) + row("Account type", human(a.type)) + row("Currency", a.currency) + row("Ownership", human(a.ownership_category)) + row("Owner", a.owner_display) + row("APY", pct(a.apy)) + row("Monthly fee", money(a.monthly_fee)) + row("Minimum balance", money(a.minimum_balance)))}${panel("Source & connection", row("Source", a.provenance?.source) + row("Verification", human(a.provenance?.verification)) + row(linked ? "Source date" : "Balance recorded", fullDate(a.provenance?.as_of)) + (linked ? row("Cache retrieved", fullDate(a.last_synced_at)) + row("Connection", a.connection_healthy ? "Healthy" : "Needs attention") + note("Cached read-only snapshot. Retrieval time does not establish the institution’s balance update time.") : row("Record type", recordType)) + (linked && !a.connection_healthy ? note(a.connection_issue) : "") + `<h3 class="section-gap">Account capabilities</h3><div class="capability-list">${a.capabilities.map((c) => chip(human(c))).join("")}</div>`)}${
    Object.keys(a.sweep_allocations || {}).length
      ? panel(
          "Underlying sweep deposits",
          Object.entries(a.sweep_allocations)
            .map(([bank, amount]) => row(bank, money(amount)))
            .join("") + link("View deposit protection", "protection"),
        )
      : ""
  }${loan ? panel("Loan terms", row("Principal-only support", loan.servicer_principal_only_supported ? "Supported" : "Unconfirmed") + row("Prepayment penalty", loan.prepayment_penalty || "Not provided") + row("Student loan program", loan.student_loan_program || "Not provided") + row("Interest tax deductibility", loan.tax_deductible_interest ? "Flagged for tax review" : "Not assumed") + row("Verification", human(loan.provenance?.verification))) : ""}${card ? panel("Card terms", row("Product", card.product) + row("Variant", card.variant || "Not specified") + row("Grace period", human(card.grace_state)) + row("Annual fee", money(card.annual_fee)) + row("Purchase APR", pct(card.purchase_apr)) + row("Foreign transaction fee", pct(card.foreign_transaction_fee))) : ""}</div>`;
}
