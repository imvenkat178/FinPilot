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
  chip,
  link,
  btn,
  action,
  empty,
  note,
  row,
  table,
  cells,
  evidence,
  detailHeading,
  reviews,
  sourceNote,
  icon,
} from "./core.js";
import { manageButton } from "./manage.js";
import { executionButton } from "./execution.js";
export function detailContent(key, extra = {}) {
  const [type, id, other] = key.split(":"),
    d = S.data,
    w = S.workspace;
  const foot = (body) => `<div class="detail-actions">${body}</div>`;
  if (type === "cash")
    return (
      detailHeading(
        "Cash across accounts",
        "Availability and existing commitments remain separate.",
        d.overview.total_cash,
      ) +
      d.overview.accounts
        .filter((a) => val(a.spendable) > 0)
        .map(
          (a) =>
            `<a class="item-row" href="#accounts/${esc(a.id)}"><span class="row-body"><strong>${esc(a.name)}</strong><span class="row-sub">${human(a.liquidity_tier)}</span></span><strong>${money(a.current, true)}</strong>${icon("arrow")}</a>`,
        )
        .join("") +
      note(d.overview.notes[0])
    );
  if (type === "networth" || type === "debt-total") {
    const isNet = type === "networth";
    return (
      detailHeading(
        isNet ? "Net worth" : "Recorded debt",
        isNet
          ? "The household calculation, including estimated property."
          : "Balances recorded in the liability engine.",
        isNet ? d.overview.net_worth : d.overview.total_debt,
      ) +
      row("Recorded liabilities", money(d.overview.total_debt, true)) +
      row("Estimated assets", money(d.overview.estimated_assets, true)) +
      table(
        ["Liability", "Balance"],
        w.liabilities.map((l) =>
          cells(
            `<a href="#accounts/${esc(l.account_id)}">${esc(l.name)}</a>`,
            money(l.balance, true),
          ),
        ),
      ) +
      w.cards
        .filter(
          (c) =>
            !w.liabilities.some((l) => l.account_id === c.account_id) &&
            val(c.current_balance) > 0,
        )
        .map((c) =>
          note(
            `${c.nickname} has a ${money(c.current_balance, true)} card balance without a liability record. It is excluded from this debt calculation; reconcile the missing record.`,
          ),
        )
        .join("") +
      foot(link("View all accounts", "accounts"))
    );
  }
  if (type === "allowance")
    return (
      detailHeading(
        "Safe to spend",
        `Calculated through ${fullDate(d.allowance.through)}`,
        d.allowance.amount,
      ) +
      row("Projected low point", money(d.allowance.low_point_balance, true)) +
      row("Low point date", fullDate(d.allowance.low_point_date)) +
      row("Protected cash", money(d.allowance.protected)) +
      chip(human(d.allowance.confidence)) +
      `<div class="section-gap">${(d.allowance.assumptions || []).map(note).join("")}</div>` +
      foot(link("Open cash flow", "cashflow"))
    );
  if (type === "reserves")
    return (
      detailHeading(
        "Protected reserves",
        "Earmarked inside existing balances.",
        d.overview.protected_reserves,
      ) +
      w.reserves
        .map(
          (r) =>
            `<button class="item-row" data-detail="reserve:${esc(r.id)}"><span class="row-body"><strong>${esc(r.name)}</strong><span class="row-sub">${esc(accountName(r.account_id))}</span></span><strong>${money(r.funded)}</strong>${icon("arrow")}</button>`,
        )
        .join("") +
      note(
        "These reserves are already included in account balances; they are not additional assets.",
      )
    );
  if (type === "reserve") {
    const r = w.reserves.find((x) => x.id === id);
    if (!r) return missing();
    const policies = w.policies.filter((p) => p.destination_reserve_id === id);
    return (
      detailHeading(r.name, accountName(r.account_id), r.funded) +
      row("Target", money(r.target)) +
      row("Still needed", money(r.remaining)) +
      row("Target date", r.target_date ? fullDate(r.target_date) : "Ongoing") +
      row("Protection", r.protected ? "Protected" : "Not protected") +
      policies
        .map((p) => row(p.name, money(p.monthly_target || p.amount)))
        .join("") +
      foot(manageButton("Edit goal", `reserves:${r.id}`, true) + link("Open account", `accounts/${r.account_id}`))
    );
  }
  if (type === "alerts")
    return (
      detailHeading(
        "Items needing review",
        "Resolve data and payment differences before relying on the plan.",
      ) +
      (reviews()
        .map(
          (i) =>
            `<${i.path ? "a" : "button"} class="item-row" ${i.path ? `href="#${esc(i.path)}"` : `data-detail="${esc(i.detail)}"`}><span class="circle-icon orange">${icon("info")}</span><span class="row-body"><strong class="row-title">${esc(i.title)}</strong><span class="row-sub">${esc(i.description)}</span></span>${icon("arrow")}</${i.path ? "a" : "button"}>`,
        )
        .join("") || empty("No review items"))
    );
  if (type === "connections")
    return (
      detailHeading(
        "Account connections",
        "Connection health is separate from account balances.",
      ) +
      '<div id="bank-connections" aria-live="polite"></div>' +
      w.accounts
        .map(
          (a) => {
            const linked = /^Plaid\b/i.test(a.provenance?.source || "");
            const manual = /manual|user[ _-]?estimate/i.test(a.provenance?.source || "");
            const stamp = linked
              ? `Last fetched ${fullDate(a.last_synced_at)} · Cached snapshot`
              : a.provenance?.as_of ? `Balance recorded ${fullDate(a.provenance.as_of)}` : "Balance date not provided";
            const status = linked ? (a.connection_healthy ? "Healthy" : "Needs attention") : manual ? "Manual record" : "Recorded balance";
            return `<a class="item-row" href="#accounts/${esc(a.id)}/details"><span class="row-body"><strong class="row-title">${esc(a.nickname)}</strong><span class="row-sub">${esc([a.institution, stamp].filter(Boolean).join(" · "))}</span></span>${chip(status, linked ? (a.connection_healthy ? "green" : "orange") : "neutral")}</a>`;
          },
        )
        .join("")
    );
  if (type === "source") {
    const a = account(id);
    const linked = /^Plaid\b/i.test(a?.provenance?.source || "");
    return a
      ? detailHeading(a.nickname, "Balance source and account connection") +
          row("Source", a.provenance?.source) +
          row(linked ? "Source date" : "Balance recorded", fullDate(a.provenance?.as_of)) +
          row("Verification", human(a.provenance?.verification)) +
          (linked ? row("Cache retrieved", fullDate(a.last_synced_at)) : "") +
          row(
            linked ? "Connection" : "Record type",
            linked ? (a.connection_healthy ? "Healthy" : "Needs attention") : /manual|user[ _-]?estimate/i.test(a.provenance?.source || "") ? "Manual record" : "Recorded balance",
          ) +
          (linked && !a.connection_healthy ? note(a.connection_issue) : "") +
          note(
            linked ? "This is a cached snapshot from a read-only bank connection. The retrieval time does not establish when the institution last updated its balance. Review the recorded source and your institution before making a decision." : "This is a recorded account balance. A manual or sample record does not establish an active bank connection. Review the source and its date before relying on it.",
          ) +
          foot(link("Account details", `accounts/${a.id}/details`))
      : missing();
  }
  if (type === "month" || type === "plan-logic")
    return (
      detailHeading(
        type === "month" ? "Monthly plan" : "How your paycheck plan works",
        dateLabel(d.as_of, { month: "long", year: "numeric" }),
      ) +
      row("Expected income", money(d.plan.plan.expected_income)) +
      row("Monthly targets", money(d.plan.plan.total_target)) +
      row("Required targets", money(d.plan.plan.required_target)) +
      note(d.plan.rule) +
      foot(link("View paycheck plan", "paychecks"))
    );
  if (type === "paycheck") {
    const p = d.plan.paychecks.find((x) => x.income_event_id === id);
    const income = w.income_events.find((e) => e.id === id);
    if (!p) return missing();
    return (
      detailHeading(
        `${dateLabel(p.pay_date, { month: "long", day: "numeric" })} paycheck`,
        income?.is_received
          ? "Income received. Allocations remain a plan until executed."
          : "Expected income · Not received yet",
        p.available,
      ) +
      row("Allocated", money(p.allocated)) +
      row("Unallocated", money(p.unallocated)) +
      `<h3 class="section-gap">Destinations</h3>` +
      p.allocations
        .filter((a) => val(a.amount) > 0)
        .map(
          (a) =>
            `<button class="item-row" data-detail="allocation:${esc(id)}:${esc(a.policy_id)}"><span class="row-body"><strong class="row-title">${esc(a.name)}</strong><span class="row-sub">${a.due_date ? `Due ${dateLabel(a.due_date)}` : "Monthly target"}</span></span><strong>${money(a.amount)}</strong>${icon("arrow")}</button>`,
        )
        .join("") +
      (p.warnings || []).map(note).join("") +
      note("Reviewing an allocation does not move money.") +
      foot(executionButton("Prepare payment drafts", `build:${id}`, true) + manageButton("Update income", `income-event:${id}`) + link("Full plan", "paychecks"))
    );
  }
  if (type === "allocation") {
    const p = d.plan.paychecks.find((x) => x.income_event_id === id),
      a = p?.allocations.find((x) => x.policy_id === other);
    if (!a) return missing();
    return (
      detailHeading(
        a.name,
        `${dateLabel(p.pay_date)} paycheck allocation`,
        a.amount,
      ) +
      row("Monthly target", money(a.monthly_target)) +
      row("Already funded before run", money(a.funded_before)) +
      row("Due date", a.due_date ? fullDate(a.due_date) : "No fixed deadline") +
      row("Status", human(a.status)) +
      note(a.reason) +
      foot(
        link("Destination account", `accounts/${a.destination_account_id}`) +
          btn("Recurring rule", `rule:${a.policy_id}`),
      )
    );
  }
  if (type === "bill") {
    const b = w.bills.find((x) => x.id === id);
    if (!b) return missing();
    const card = w.cards.find((c) => c.account_id === b.payee_account_id);
    const diff = card ? val(card.statement_balance) - val(b.amount) : 0;
    const draft = S.cache.get(`draft:${id}:${extra.date || b.due_date || ""}`);
    const projected = !!(extra.date && extra.date !== b.due_date);
    return (
      detailHeading(
        b.name,
        `Due ${fullDate(extra.date || b.due_date)}`,
        b.amount,
      ) +
      row("Funding account", accountName(b.funding_account_id)) +
      row("Payment handled by", human(b.execution_owner)) +
      row("Amount", b.amount_confirmed ? "Confirmed" : "Estimated") +
      row("Autopay", b.autopay_confirmed ? "Confirmed" : "Not confirmed") +
      (diff
        ? note(
            `The statement is ${money(card.statement_balance, true)}, which differs from this ${money(b.amount, true)} bill. Reconcile the amount before payment.`,
          )
        : "") +
      row("Source", b.provenance?.source) +
      `${draft ? draftResult(draft) : ""}${projected ? note("This is the projected occurrence due " + fullDate(extra.date) + ". Draft checks apply to that specific date.") : ""}` +
      note(
        "Prepare a draft to check funding and authorization. This does not send a payment.",
      ) +
      foot(
        action(
          draft ? "Refresh draft checks" : "Prepare payment draft",
          "draft-bill",
          true,
          `data-id="${esc(id)}" data-date="${esc(extra.date || b.due_date)}"`,
        ) + manageButton("Edit bill", `bills:${id}`) +
          (b.payee_account_id
            ? link("Open account", `accounts/${b.payee_account_id}`)
            : ""),
      )
    );
  }
  if (type === "bills-total")
    return (
      detailHeading(
        "Upcoming obligations",
        `Next ${d.bills.window_days} days`,
        d.bills.total_due,
      ) +
      table(
        ["Bill", "Due", "Amount"],
        d.bills.obligations.map((b) =>
          cells(esc(b.name), dateLabel(b.due_date), money(b.amount)),
        ),
      ) +
      foot(link("Open bills & payments", "bills"))
    );
  if (type === "rule") {
    const p = w.policies.find((x) => x.id === id),
      status = d.automation.policies.find((x) => x.id === id);
    if (!p) return missing();
    return (
      detailHeading(
        p.name,
        `Priority ${p.priority} · ${human(p.method)}`,
        p.monthly_target || p.amount,
      ) +
      row("Destination", accountName(p.destination_account_id)) +
      row("Purpose", human(p.purpose)) +
      row("Cadence", human(p.schedule?.cadence || p.cadence || "on_income")) +
      row(
        "Status",
        p.paused ? "Paused" : p.skip_next ? "Skipping next" : "Active",
      ) +
      row("Authorization", human(status?.authorization)) +
      row("Authorized to execute", status?.authorized ? "Yes" : "No") +
      note(
        "Changes to rule status update future plan calculations. A paused or skipped rule does not recall submitted payments.",
      ) +
      foot(
        manageButton("Edit rule", `policies:${id}`) + executionButton(S.workspace.household.payment_sandbox ? (status?.authorized ? "Review authorization" : "Authorize simulation") : "Payment simulation", `authorize:${id}`, true) + action(
          p.paused ? "Resume rule" : "Pause rule",
          "pause-rule",
          false,
          `data-id="${esc(id)}" data-paused="${!p.paused}"`,
        ) +
          action(
            p.skip_next ? "Next already skipped" : "Skip next",
            "skip-rule",
            false,
            `data-id="${esc(id)}" ${p.skip_next ? "disabled" : ""}`,
          ),
      )
    );
  }
  if (type === "pause-all")
    return (
      detailHeading(
        "Pause all future execution",
        "This applies to every future payment run.",
      ) +
      note(
        "Already submitted payments cannot be recalled by this control. Individual rule settings remain available.",
      ) +
      foot(executionButton("Review pause", "pause", true))
    );
  if (type === "new-rule")
    return (
      detailHeading(
        "Create a recurring rule",
        "Save a fixed monthly target. Authorization is a separate step.",
      ) +
      `<form id="policy-form" class="stack-form"><label>Rule name<input name="name" maxlength="100" required placeholder="e.g. Travel fund"></label><label>Monthly amount<input name="amount" type="number" min="0.01" max="100000000" step="0.01" required></label><label>Destination account<select name="destination">${w.accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.nickname)}</option>`).join("")}</select></label><label>Purpose<select name="purpose"><option value="goal">Savings goal</option><option value="spending">Everyday spending</option><option value="investment_cash">Investment cash</option></select></label><label>Priority<input name="priority" type="number" value="100" min="1" max="10000" required></label>${note("The rule is saved without payment authority. Saving it cannot move money.")}<button class="button primary">Save recurring rule</button><div class="form-result" role="status"></div></form>`
    );
  if (type === "forecast") {
    const fc =
        S.page === "cashflow"
          ? S.cache.get("forecast") || d.forecast
          : d.forecast,
      p = fc.daily?.[Number(id)];
    return p
      ? detailHeading(
          "Projected closing balance",
          `${fullDate(p.date)} · ${fc.account_name}`,
          p.closing,
        ) +
          note(
            "This is a forecast from the ledger, not a posted account balance.",
          ) +
          row("Forecast ending", money(fc.ending_balance, true)) +
          row("Lowest projected balance", money(fc.low_point?.balance, true)) +
          foot(link("Explore cash flow", "cashflow"))
      : missing();
  }
  if (type === "forecast-assumptions")
    return (
      detailHeading(
        "Cash flow assumptions",
        "Income and obligation timing determine the daily balance.",
      ) +
      (d.allowance.assumptions || []).map(note).join("") +
      evidence(
        "Forecast inputs and results",
        S.page === "cashflow"
          ? S.cache.get("forecast") || d.forecast
          : d.forecast,
      )
    );
  if (type === "card") {
    const c = w.cards.find((x) => x.id === id);
    return c
      ? detailHeading(
          c.nickname,
          "Latest recorded credit card statement",
          c.statement_balance,
        ) +
          row("Current balance", money(c.current_balance, true)) +
          row("Credit limit", money(c.credit_limit)) +
          row("Payment due day", c.payment_due_day) +
          row("Statement closes", c.statement_close_day) +
          row("Grace period", human(c.grace_state)) +
          foot(link("Card analytics", `accounts/${c.account_id}/analytics`))
      : missing();
  }
  if (type === "payment-activity")
    return (
      detailHeading(
        "Payment activity",
        "Drafts and payment state from the execution engine.",
      ) + foot(link("Open activity", "bills/activity"))
    );
  if (type === "settings")
    return (
      detailHeading("Workspace settings", "Your account, appearance, and AI assistant.") +
      row("Signed in", S.session?.user?.email || S.session?.email || "Current workspace member") +
      `<div class="settings-appearance"><h3>Appearance</h3><button class="button" data-action="theme">Switch to ${document.documentElement.dataset.theme === "dark" ? "light" : "dark"} theme</button></div><h3 class="section-gap">AI assistant</h3>${note(d.llm?.reachable ? "The host-managed model is available. Answers use verified financial calculations." : "Calculator mode is available. The application host manages model connectivity.")}<div class="detail-actions"><button class="button" data-action="logout">Sign out</button></div>`
    );
  if (type === "assumptions")
    return (
      detailHeading(
        "Workspace data & assumptions",
        "The reference application uses the household records and financial engines.",
      ) +
      row("Household", d.household) +
      row("As of", fullDate(d.as_of)) +
      row("Payment provider", "Simulated") +
      row("AI model", d.llm.reachable ? "Connected" : "Calculator fallback") +
      d.overview.notes.map(note).join("")
    );
  return missing();
}
function missing() {
  return detailHeading(
    "Detail unavailable",
    "The record may have changed. Refresh your workspace and try again.",
  );
}
export function draftResult(d) {
  return `<div class="draft-result section-gap"><h3>Draft checks</h3>${chip(d.preflight?.ok ? "Ready for review" : "Blocked", d.preflight?.ok ? "green" : "orange")}${(d.preflight?.blockers || []).map((x) => note(typeof x === "string" ? x : JSON.stringify(x))).join("")}${note(d.note || "Draft prepared. No payment has been submitted.")}${evidence("View preflight checks", d.preflight)}${d.group_id ? `<div class="detail-actions">${executionButton("Review payment draft", `group:${d.group_id}`, true)}</div>` : ""}</div>`;
}
export function calculationResult(result) {
  if (!result || typeof result !== "object") return "";
  if (result.baseline && result.rows) return taxResult(result);
  if (result.scenarios) return mortgageResult(result);
  if (result.options && result.purchase) return cardResult(result);
  const items = Object.entries(result).filter(
    ([, v]) =>
      v === null || typeof v !== "object" || ("amount" in v && "currency" in v),
  );
  return `<div class="calculation-result">${items.map(([k, v]) => row(human(k), v && typeof v === "object" ? money(v, true) : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v ?? "Not provided"))).join("")}${evidence("Full calculation & assumptions", result)}</div>`;
}
function taxResult(r) {
  return `<div class="calculation-result"><h3>Net benefit over ${r.horizon_days} days</h3>${row("Amount compared", money(r.amount))}${table(
    ["Option", "Net benefit", "Vs. savings", "Cash retained"],
    [r.baseline, ...r.rows].map((x) =>
      cells(
        `<strong>${esc(x.label)}</strong><small>${esc(x.note)}</small>`,
        money(x.one_year_net_benefit, true),
        money(x.versus_baseline, true),
        money(x.liquidity_retained, true),
      ),
    ),
  )}${(r.assumptions || []).map(note).join("")}${chip(human(r.confidence))}${evidence("Calculation details", r)}</div>`;
}
function mortgageResult(r) {
  return `<div class="calculation-result"><h3>Mortgage scenarios</h3>${table(
    [
      "Scenario",
      "Monthly P&I",
      "Remaining term",
      "Lifetime interest",
      "Upfront cash",
    ],
    r.scenarios.map((x) =>
      cells(
        `<strong>${esc(x.name)}</strong><small>${esc(x.note)}</small>`,
        money(x.monthly_principal_interest, true),
        `${x.months_remaining} months`,
        money(x.lifetime_interest, true),
        money(x.upfront_cash, true),
      ),
    ),
  )}${note(r.note)}${evidence("Scenario calculations", r)}</div>`;
}
function cardResult(r) {
  return `<div class="calculation-result"><h3>${r.winner ? "Highest net value: " + esc(r.winner.nickname) : "No card recommendation"}</h3>${note(r.repayment_note || r.explanation || "Compare reward value after costs.")}${table(
    ["Card", "Reward value", "Fees", "Net value"],
    r.options.map((x) =>
      cells(
        `<strong>${esc(x.nickname)}</strong><small>${esc(x.eligible ? "Eligible" : x.exclusions?.join(", ") || "Not eligible")}</small>`,
        money(x.reward_value, true),
        money(val(x.processing_fee) + val(x.foreign_fee), true),
        money(x.net_value, true),
      ),
    ),
  )}${(r.assumptions || []).map(note).join("")}${evidence("Rewards calculation", r)}</div>`;
}
