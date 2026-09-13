import {
  S,
  esc,
  val,
  money,
  pct,
  human,
  dateLabel,
  fullDate,
  account,
  accountName,
  accountType,
  accountIcon,
  access,
  chip,
  link,
  btn,
  action,
  heading,
  panel,
  empty,
  note,
  row,
  stat,
  table,
  cells,
  evidence,
  icon,
  reviews,
  sourceNote,
  cashAccounts,
  upcomingPay,
  billRows,
  forecastChart,
  allocationBar,
  accountCards,
  groups,
} from "./core.js";
import { manageButton } from "./manage.js";
import { executionButton } from "./execution.js";
import { chatPage } from "./assistant.js";
export function overview() {
  const d = S.data,
    w = S.workspace,
    pay = upcomingPay(),
    items = reviews(),
    featuredAccounts = cashAccounts().slice(0, 3),
    forecast = d.forecast,
    difference = items.find((item) => item.difference),
    qualifications = [
      w.accounts.some((a) => a.provenance?.verification === "estimated") &&
        "estimated assets",
      w.accounts.some((a) => !a.connection_healthy) &&
        "accounts needing review",
    ].filter(Boolean),
    number = (value) =>
      esc(money(value, true)).replace(/(\.\d{2})$/, "<span>$1</span>"),
    attentionRows = items.slice(0, 3).map((item) => {
      const content = `<span class="attention-icon ${item.difference ? "amber" : ""}">${icon(item.difference ? "receipt" : item.path?.endsWith("/details") ? "repeat" : "card")}</span><span><strong>${esc(item.title)}</strong><small>${esc(item.description)}</small></span>${icon("arrow")}`;
      return item.detail
        ? `<button class="attention-row" data-detail="${esc(item.detail)}">${content}</button>`
        : `<a class="attention-row" href="#${esc(item.path)}">${content}</a>`;
    });
  return (
    heading(
      "Overview",
      dateLabel(d.as_of, {
        weekday: "long",
        month: "long",
        day: "numeric",
        year: "numeric",
      }),
      btn("This month", "month", "calendar") +
        (pay
          ? btn(
              "Review paycheck",
              `paycheck:${pay.income_event_id}`,
              "arrow",
              true,
            )
          : ""),
    ) +
    `<section class="position-strip" aria-label="Financial overview">
      <div class="position-primary">
        <span class="position-label">Total net worth <button class="icon-button" data-detail="networth" aria-label="View net worth breakdown">${icon("info")}</button></span>
        <button class="position-number" data-detail="networth" aria-label="Total net worth ${esc(money(d.overview.net_worth, true))}, view breakdown">${number(d.overview.net_worth)}</button>
        <p>${w.accounts.length} ${w.accounts.length === 1 ? "account" : "accounts"}${qualifications.length ? ` · Includes ${esc(qualifications.join(" and "))}` : " · Based on recorded balances"}</p>
      </div>
      <div class="position-secondary">
        <span class="position-label">Cash across accounts</span>
        <button class="position-number" data-detail="cash" aria-label="Cash across accounts ${esc(money(d.overview.total_cash, true))}, view breakdown">${number(d.overview.total_cash)}</button>
        <button class="position-caption" data-detail="reserves">${money(d.overview.protected_reserves)} in protected reserves ${icon("arrow")}</button>
      </div>
      <div class="position-secondary">
        <span class="position-label">Next paycheck ${pay ? `<span class="quiet-date">${dateLabel(pay.pay_date).toUpperCase()}</span>` : ""}</span>
        ${pay ? `<button class="position-number" data-detail="paycheck:${esc(pay.income_event_id)}" aria-label="Next paycheck ${esc(money(pay.available, true))}, review allocation">${number(pay.available)}</button><button class="position-caption" data-detail="paycheck:${esc(pay.income_event_id)}">${pay.shortfalls?.length ? "Review funding shortfalls" : "View your allocation"} ${icon("arrow")}</button>` : `<span class="position-number">—</span><span class="position-caption">No upcoming paycheck scheduled</span>`}
      </div>
    </section>
    <section class="portfolio-section">
      <div class="portfolio-heading"><div><h2>Your accounts <span>${w.accounts.length}</span></h2><p>A closer look at your everyday cash.</p></div>${link("View all accounts", "accounts")}</div>
      <div class="portfolio-grid">${featuredAccounts.length ? featuredAccounts.map((a, i) => `<a class="portfolio-card tone-${i}" href="#accounts/${esc(a.id)}"><div class="portfolio-institution"><span class="institution-icon">${icon(accountIcon(a))}</span><span>${esc(a.institution || "Account")}</span><span class="portfolio-arrow">${icon("up")}</span></div><h3>${esc(a.nickname)}</h3>${a.mask ? `<span class="portfolio-mask">•• ${esc(a.mask)}</span>` : ""}<strong>${money(a.current, true)}</strong><div class="portfolio-footer"><span>${esc(access(a))}</span><span>View account ${icon("arrow")}</span></div></a>`).join("") : empty("No cash accounts yet", "Add your first account to start organizing your money.") + manageButton("Add first account", "accounts", true)}</div>
    </section>
    <div class="workspace-grid">
      <section class="panel forecast-panel">
        <div class="panel-head"><div><h2>Cash flow</h2><p>${esc(forecast?.account_name || "Checking balance")}${forecast?.start && forecast?.end ? ` · ${dateLabel(forecast.start)}–${dateLabel(forecast.end)}` : ""}</p></div>${link("View forecast", "cashflow")}</div>
        <div class="forecast-summary"><div><span>Projected closing balance</span><strong>${number(forecast?.ending_balance)}</strong></div><span class="forecast-key"><i></i>Projected balance</span></div>
        ${forecastChart(forecast, true)}
        <div class="forecast-bottom"><span>${icon("lock")} ${money(d.buffer?.operating_floor)} operating floor</span><button class="text-button" data-detail="forecast-assumptions">View assumptions ${icon("arrow")}</button></div>
      </section>
      <section class="panel review-panel">
        <div class="panel-head"><h2>Needs your attention</h2>${items.length ? `<span class="review-count">${items.length}</span>` : ""}</div>
        ${attentionRows.join("") || empty("You're up to date", "No account connections or card plans need review.")}
        ${items.length > 3 ? `<button class="text-button section-gap" data-detail="alerts">View all ${items.length} items ${icon("arrow")}</button>` : ""}
        <div class="intelligence-note"><div class="intelligence-note-title"><span>${icon("spark")}</span><strong>Make sense of the details</strong><small>AI</small></div><p>${difference ? "Ask FinPilot to explain the card difference and show the numbers behind it." : "Ask FinPilot to explain your account balances and the calculations behind your plan."}</p><button class="text-button" data-ask="${difference ? "Show my upcoming bills and explain the card statement difference" : "Explain my financial overview and next paycheck plan"}">${difference ? "Explain this difference" : "Explain my plan"} ${icon("arrow")}</button></div>
      </section>
    </div>`
  );
}
export function accountsPage() {
  const w = S.workspace,
    d = S.data,
    filters = [
      "All accounts",
      "Cash",
      "Investments",
      "Loans",
      "Credit cards",
      "Property",
      "Platforms",
    ];
  let list = w.accounts.filter(
    (a) =>
      (S.accountFilter === "All accounts" ||
        S.accountFilter === "Platforms" ||
        accountType(a) === S.accountFilter) &&
      `${a.nickname} ${a.institution} ${a.mask}`
        .toLowerCase()
        .includes(S.query.toLowerCase()),
  );
  return (
    heading(
      "Accounts",
      "Every account and platform, with its balance and data source.",
      manageButton("Add account", "accounts", true) + btn("Connections", "connections", "bank"),
    ) +
    `<div class="metrics">${stat("Cash", money(d.overview.total_cash), "Across cash accounts", "cash")}${stat("Protected reserves", money(d.overview.protected_reserves), "Inside existing accounts", "reserves")}${stat("Debt", money(d.overview.total_debt), "Recorded liabilities", "debt-total")}${stat("Net worth", money(d.overview.net_worth), "Calculated by FinPilot", "networth")}</div><div class="toolbar"><div class="filter-tabs" role="tablist" aria-label="Filter accounts">${filters.map((f) => `<button role="tab" aria-selected="${S.accountFilter === f}" data-filter="${f}">${f}</button>`).join("")}</div><label class="search-field">${icon("wallet")}<input id="account-search" type="search" aria-label="Search accounts" placeholder="Search accounts or platforms" value="${esc(S.query)}"></label></div><div id="account-results">${accountResults(list)}</div>`
  );
}
export function accountResults(list) {
  if (!list.length)
    return empty(
      "No accounts found",
      "Try a different account name or filter.",
    );
  if (S.accountFilter === "Platforms")
    return [...new Set(list.map((a) => a.institution))]
      .map(
        (name) =>
          `<section class="platform-group"><h2>${esc(name || "Unspecified institution")} <span>${list.filter((a) => a.institution === name).length} accounts</span></h2>${accountCards(list.filter((a) => a.institution === name))}</section>`,
      )
      .join("");
  return accountCards(list);
}
export function paycheckPage() {
  const plan = S.data.plan,
    selected =
      S.payPeriod === "all"
        ? plan.paychecks
        : plan.paychecks.filter((p) => p.income_event_id === S.payPeriod);
  return (
    heading(
      "Paycheck plan",
      "Deadlines first. Every allocation follows your saved priorities.",
      manageButton("Manage income", "income-list", true) + btn("How it works", "plan-logic", "info"),
    ) +
    `<div class="metrics">${stat("Expected income", money(plan.plan.expected_income), plan.month, "month")}${stat("Monthly targets", money(plan.plan.total_target), "All saved policies", "plan-logic")}${stat("Required targets", money(plan.plan.required_target), "Bills and debt obligations", "bills-total")}${stat("Protected reserves", money(S.data.overview.protected_reserves), "Already held in accounts", "reserves")}</div><div class="filter-tabs section-gap" role="tablist" aria-label="Paycheck period"><button role="tab" aria-selected="${S.payPeriod === "all"}" data-pay-period="all">Full month</button>${plan.paychecks.map((p) => `<button role="tab" aria-selected="${S.payPeriod === p.income_event_id}" data-pay-period="${esc(p.income_event_id)}">${dateLabel(p.pay_date)} · ${money(p.available)}</button>`).join("")}</div>${plan.plan.overcommitted ? note("Monthly targets exceed expected income. Review the funding shortfalls before proceeding.") : ""}<div class="paycheck-panels">${
      selected
        .map((p) =>
          panel(
            dateLabel(p.pay_date, { month: "long", day: "numeric" }) +
              " paycheck",
            `<div class="plan-summary"><div class="plan-total"><span class="amount">${money(p.available)}</span><span class="muted">${money(p.allocated)} allocated · ${money(p.unallocated)} unallocated</span></div>${chip(p.shortfalls?.length ? "Shortfall" : "Planned", p.shortfalls?.length ? "orange" : "green")}</div>${allocationBar(p)}<div class="allocation-legend">${groups.map((g) => `<span><i style="background:${g.color}"></i>${g.name}</span>`).join("")}</div>${table(
              ["Destination", "Due", "Allocated", "Monthly target"],
              p.allocations.map((a) =>
                cells(
                  `<button class="table-link" data-detail="allocation:${esc(p.income_event_id)}:${esc(a.policy_id)}">${esc(a.name)} ${icon("arrow")}</button><small>${esc(a.status === "deferred" ? "Deferred to a later paycheck" : human(a.urgency))}</small>`,
                  esc(a.due_date ? dateLabel(a.due_date) : "—"),
                  money(a.amount),
                  money(a.monthly_target),
                ),
              ),
            )}${(p.warnings || []).map(note).join("")}`,
            btn("Review", `paycheck:${p.income_event_id}`),
          ),
        )
        .join("") ||
      empty(
        "No income events this month",
        "Your plan will appear when income events are available.",
      )
    }</div>`
  );
}
export function billsPage() {
  let rows = S.data.bills.obligations;
  return (
    heading(
      "Bills & payments",
      "Upcoming obligations, funding sources, and payment drafts.",
      manageButton("Add bill", "bills", true) + btn("Payment activity", "payment-activity", "clock"),
    ) +
    `<div class="metrics">${stat("Due in 30 days", money(S.data.bills.total_due), `${rows.length} obligations`, "bills-total")}${stat("Next paycheck", money(upcomingPay()?.available), upcomingPay() ? dateLabel(upcomingPay().pay_date) : "None scheduled", "month")}${stat("Safe to spend", money(S.data.allowance.amount), `Through ${dateLabel(S.data.allowance.through)}`, "allowance")}${stat("Items to review", String(reviews().length), "Sources and payment plans", "alerts")}</div><div class="tabs" role="tablist" aria-label="Bills view">${["upcoming", "calendar", "activity"].map((t) => `<button role="tab" aria-selected="${S.billTab === t}" data-bill-tab="${t}">${human(t)}</button>`).join("")}</div>${S.billTab === "calendar" ? billCalendar() : S.billTab === "activity" ? paymentActivity() : panel("Upcoming obligations", billRows(rows), "", "Payment amounts and dates come from your current bill records.")}`
  );
}
function billCalendar() {
  const start = new Date(S.data.as_of + "T12:00:00"),
    y = start.getFullYear(),
    m = start.getMonth(),
    days = new Date(y, m + 1, 0).getDate(),
    offset = new Date(y, m, 1).getDay();
  const rows = S.data.bills.obligations;
  return panel(
    dateLabel(S.data.as_of, { month: "long", year: "numeric" }),
    `<div class="bill-calendar">${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => `<span class="day-label">${d}</span>`).join("")}${'<div class="calendar-cell blank"></div>'.repeat(offset)}${Array.from(
      { length: days },
      (_, i) => {
        const day = `${y}-${String(m + 1).padStart(2, "0")}-${String(i + 1).padStart(2, "0")}`;
        return `<div class="calendar-cell ${day === S.data.as_of ? "today" : ""}"><strong>${i + 1}</strong>${rows
          .filter((o) => o.due_date === day)
          .map(
            (o) =>
              `<button data-detail="bill:${esc(S.workspace.bills.find((b) => b.name === o.name)?.id)}" data-date="${day}">${esc(o.name)}<span>${money(o.amount)}</span></button>`,
          )
          .join("")}</div>`;
      },
    ).join("")}</div>`,
  );
}
export function paymentActivity() {
  const d = S.cache.get("execution");
  if (!d) return panel("Payment activity", empty("Loading payment drafts…"));
  return (
    panel(
      "Payment drafts & activity",
      d.groups?.length
        ? d.groups
            .map(
              (g) =>
                `<div class="payment-group"><h3>${esc(g.label || "Payment group")}</h3>${table(
                  ["Payment", "Amount", "State"],
                  (g.detail || []).map((l) =>
                    cells(
                      esc(
                        account(l.destination)?.nickname ||
                          l.destination ||
                          l.id,
                      ),
                      money(l.amount),
                      chip(
                        human(l.state),
                        ["blocked", "failed", "returned", "outcome_unknown"].includes(l.state)
                          ? "orange"
                          : "neutral",
                      ),
                    ),
                  ),
                )}<div class="detail-actions">${executionButton("Review payment group", `group:${g.group_id}`, true)}</div>${evidence("View payment record", g)}</div>`,
            )
            .join("")
        : empty(
            "No payment drafts yet",
            "Review a bill and prepare a draft to check its authorization and funding.",
          ),
    ) +
    note(
      "Simulation only. Preparing a draft does not submit a payment. A confirmed simulation updates this workspace’s balances and payment records without sending a live bank transfer.",
    )
  );
}
export function cashflowPage() {
  const fc = S.cache.get("forecast") || S.data.forecast;
  return (
    heading(
      "Cash flow",
      "See when money arrives, when it leaves, and what stays available.",
      btn("Projection assumptions", "forecast-assumptions", "info"),
    ) +
    `<div class="metrics">${stat("Projected ending", money(fc.ending_balance), dateLabel(fc.end), "forecast-assumptions")}${stat("Lowest balance", money(fc.low_point?.balance), dateLabel(fc.low_point?.date), "allowance")}${stat("Safe to spend", money(S.data.allowance.amount), `Through ${dateLabel(S.data.allowance.through)}`, "allowance")}${stat("Protected reserves", money(S.data.overview.protected_reserves), "Reserved within accounts", "reserves")}</div>${panel("Checking balance outlook", `<div class="chart-key">${chip(fc.negative_days?.length ? "Negative balance projected" : "Above zero", fc.negative_days?.length ? "orange" : "green")}<span>${esc(fc.account_name)} · ${fullDate(fc.start)}–${fullDate(fc.end)}</span></div>${forecastChart(fc)}<p class="chart-caption">Select a point to inspect its projected closing balance. This is a forecast, not transaction history.</p>`, `<div class="segmented" aria-label="Forecast horizon">${[30, 45, 60].map((n) => `<button data-range="${n}" aria-pressed="${S.forecastDays === n}">${n} days</button>`).join("")}</div>`)}<div class="section-gap">${panel(
      "Daily projection",
      table(
        ["Date", "Projected closing balance"],
        (fc.daily || []).map((d, i) =>
          cells(
            `<button class="table-link" data-detail="forecast:${i}">${fullDate(d.date)}</button>`,
            money(d.closing, true),
          ),
        ),
      ),
    )}</div>`
  );
}
export function goalCard(r) {
  const progress =
    val(r.target) > 0
      ? Math.round(Math.max(0, Math.min(100, (val(r.funded) / val(r.target)) * 100)))
      : 0;
  return `<button class="goal-card panel" data-detail="reserve:${esc(r.id)}"><span class="circle-icon">${icon(r.purpose === "operating_floor" ? "lock" : "target")}</span><h3>${esc(r.name)}</h3><p>${esc(accountName(r.account_id))}</p><strong class="amount">${money(r.funded)} <small>of ${money(r.target)}</small></strong><progress value="${progress}" max="100" aria-label="${esc(r.name)}">${progress.toFixed(0)}%</progress><div class="goal-card-bottom"><span>${progress.toFixed(0)}% funded</span><span>${r.target_date ? dateLabel(r.target_date) : r.protected ? "Protected" : "Goal"} ${icon("arrow")}</span></div></button>`;
}
export function goalsPage() {
  return (
    heading(
      "Savings goals",
      "Track reserves inside the accounts where the money is held.",
      manageButton("Add goal", "reserves", true) + link("View savings accounts", "accounts"),
    ) +
    `<div class="goals-grid">${S.workspace.reserves.map(goalCard).join("") || empty("No savings goals yet")}</div><div class="section-gap">${note("Reserves are earmarked portions of existing account balances. They are not added again to cash or net worth.")}</div>`
  );
}
export function debtComparison(d) {
  if (!d) return empty("Calculating comparison…");
  if (d.error) return empty("Comparison unavailable", d.error);
  return `<div class="comparison-summary">${row("Monthly budget", money(d.budget))}${row("Required payments", money(d.required_total))}${row("Extra payment", money(d.extra))}</div>${table(
    ["Strategy", "First payoff", "Time to clear", "Total interest"],
    (d.results || []).map((r) =>
      cells(
        esc(r.label),
        r.first_payoff
          ? `${esc(r.first_payoff.debt)}<small>Month ${r.first_payoff.month}</small>`
          : "—",
        `${r.months_to_clear} months`,
        money(r.total_interest, true),
      ),
    ),
  )}<p class="chart-caption">${esc(d.objective_note || "Figures use the stored loan balances and terms.")}</p>`;
}
export function debtPage() {
  return (
    heading(
      "Debt & credit",
      "Your loans, card statements, and repayment options.",
      btn("What the total includes", "debt-total", "info"),
    ) +
    `<div class="account-grid">${S.workspace.accounts
      .filter((a) => ["Loans", "Credit cards"].includes(accountType(a)))
      .map(
        (a) =>
          `<a class="account-card" href="#accounts/${esc(a.id)}"><div class="account-card-head"><span class="circle-icon">${icon(accountIcon(a))}</span>${chip(accountType(a))}</div><h3>${esc(a.nickname)}</h3><p>${esc([a.institution, a.mask].filter(Boolean).join(" · "))}</p><strong class="amount">${money(Math.abs(val(a.current)), true)}</strong><div class="account-card-footer"><span>View balance, terms & analytics</span>${icon("arrow")}</div></a>`,
      )
      .join(
        "",
      )}</div><div class="section-gap">${panel("Compare repayment strategies", `<form id="debt-form" class="inline-form"><label>Extra monthly payment<input name="extra" type="number" min="0" max="1000000" step="0.01" value="${val((S.cache.get("debt") || S.data.debt).extra)}" required></label><button class="button primary" type="submit">Compare strategies</button></form><div id="debt-result">${debtComparison(S.cache.get("debt") || S.data.debt)}</div>`, "", "One budget across the recorded liabilities. Missing card records appear in items to review.")}</div>`
  );
}
export function rulesPage() {
  const w = S.workspace,
    a = S.data.automation,
    r = S.data.recurring_activity;
  return (
    heading(
      "Recurring rules",
      "Control priorities, pause a rule, or skip its next occurrence.",
      manageButton("New rule", "policies", true),
    ) +
    `${a.globally_paused ? note("All future execution is paused. Individual rule states are shown below.") : ""}${panel(
      "Your rules",
      table(
        [
          "Rule & destination",
          "Monthly target",
          "Priority",
          "Status",
          "Controls",
        ],
        a.policies.map((p) => {
          const raw = w.policies.find((x) => x.id === p.id);
          return cells(
            `<button class="table-link" data-detail="rule:${esc(p.id)}">${esc(p.name)}</button><small>${esc(p.destination)}</small>`,
            money(p.amount),
            esc(p.priority),
            chip(
              p.paused
                ? "Paused"
                : raw?.skip_next
                  ? "Skipping next"
                  : p.authorized
                    ? "Active"
                    : "Needs authorization",
              p.paused || raw?.skip_next || !p.authorized ? "orange" : "green",
            ),
            `<div class="row-controls"><button class="rule-control" data-action="pause-rule" data-id="${esc(p.id)}" data-paused="${!p.paused}">${p.paused ? "Resume" : "Pause"}</button><button class="rule-control" data-action="skip-rule" data-id="${esc(p.id)}" ${raw?.skip_next ? "disabled" : ""}>${raw?.skip_next ? "Next skipped" : "Skip next"}</button></div>`,
          );
        }),
      ),
      executionButton(a.globally_paused ? "Resume future simulation" : "Pause all execution", a.globally_paused ? "resume" : "pause"),
    )}<div class="section-gap">${panel(
      "Upcoming occurrences",
      table(
        ["Rule", "Date", "Amount", "Run status"],
        (r.upcoming_runs || []).map((u) =>
          cells(
            `<button class="table-link" data-detail="rule:${esc(u.policy_id)}">${esc(u.name)}</button>`,
            dateLabel(u.date),
            esc(u.amount_note || money(u.amount)),
            `${chip(a.globally_paused ? "Execution paused" : u.will_run ? "On track" : "Will not run", !a.globally_paused && u.will_run ? "green" : "orange")}${u.reason_if_not ? `<small>${esc(u.reason_if_not)}</small>` : ""}`,
          ),
        ),
      ),
      "",
      r.note,
    )}</div>`
  );
}
export function protectionPage() {
  const c = S.data.coverage,
    t = S.workspace.tax;
  return (
    heading(
      "Tax & protection",
      "Coverage, tax assumptions, and liquidity in separate views.",
      manageButton("Edit tax profile", "tax") + btn("Data & assumptions", "assumptions", "info"),
    ) +
    `<div class="content-grid">${panel("Deposit protection", c.buckets?.length ? c.buckets.map((b) => `<div class="coverage-row"><h3>${esc(b.institution_name)}</h3><p>${esc(human(b.category))} · ${esc(human(b.regime))}</p>${row("Deposits", money(b.total))}${row("Category limit", money(b.limit))}${row("Estimated uncovered", money(b.uncovered))}${val(b.via_sweep) > 0 ? row("Via sweep program", money(b.via_sweep)) : ""}</div>`).join("") : empty("No deposit groupings"), "", `${money(c.total_uncovered)} estimated uncovered`)}${panel("Tax profile", row("Federal marginal rate", pct(t.federal_marginal)) + row("State marginal rate", pct(t.state_marginal)) + row("Itemizes deductions", t.itemizes ? "Yes" : "No") + row("Verification", t.verified ? "Verified" : "Unverified") + note("Confirm tax assumptions before using a comparison.") + `<form id="tax-form" class="stack-form"><label>Amount to compare<input name="amount" type="number" min="1" max="100000000" value="1000" step="0.01" required></label><label>Comparison period<select name="days"><option value="365">One year</option><option value="180">Six months</option><option value="90">Three months</option></select></label><button class="button primary">Compare savings & debt</button></form><div id="tax-result"></div>`)}${panel("Liquidity by availability", (S.data.liquidity.tiers || []).map((t) => `<a class="item-row" href="#accounts/${esc(t.account_id || t.id || "")}"><span class="row-body"><span class="row-title">${esc(t.name)}</span><span class="row-sub">${esc(t.treatment)}</span></span><span class="row-end">${money(t.available)}<small>${esc(t.tier_label || human(t.tier))}</small></span></a>`).join(""))}${panel("Coverage assumptions", (c.caveats || []).map(note).join("") + evidence("Coverage rules and calculation", c))}</div>`
  );
}
export const pages = {
  chat: chatPage,
  overview,
  accounts: accountsPage,
  paychecks: paycheckPage,
  bills: billsPage,
  cashflow: cashflowPage,
  goals: goalsPage,
  debt: debtPage,
  rules: rulesPage,
  protection: protectionPage,
};
