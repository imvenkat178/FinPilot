import { icon } from "./icons.js";
export { icon };
export const S = {
  session: null,
  revision: null,
  data: null,
  workspace: null,
  page: "chat",
  accountId: null,
  tab: "summary",
  accountFilter: "All accounts",
  query: "",
  billTab: "upcoming",
  payPeriod: "all",
  forecastDays: 45,
  cache: new Map(),
};
export const sections = [
  ["chat", "FinPilot chat", "spark"],
  ["overview", "Overview", "grid"],
  ["accounts", "Accounts", "wallet"],
  ["paychecks", "Paycheck plan", "split"],
  ["bills", "Bills & payments", "receipt"],
  ["cashflow", "Cash flow", "chart"],
  ["goals", "Savings goals", "target"],
  ["debt", "Debt & credit", "card"],
  ["rules", "Recurring rules", "repeat"],
  ["protection", "Tax & protection", "shield"],
];
export const groups = [
  {
    name: "Bills & essentials",
    purposes: ["required_debt", "card_statement", "bill"],
    color: "#70647e",
  },
  {
    name: "Savings & reserves",
    purposes: ["emergency_reserve", "annual_reserve", "tax_reserve", "buffer"],
    color: "#aaa0bd",
  },
  { name: "Everyday spending", purposes: ["spending"], color: "#c9c2d4" },
  { name: "Investment cash", purposes: ["investment_cash"], color: "#809397" },
  {
    name: "Extra principal & goals",
    purposes: ["extra_principal", "goal"],
    color: "#958caa",
  },
];
export const $ = (id) => document.getElementById(id);
export const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const val = (value) => Number(value?.amount ?? value ?? 0) || 0;
export function money(value, cents = false) {
  if (value === null || value === undefined) return "—";
  const n = Number(value?.amount ?? value);
  return Number.isFinite(n)
    ? new Intl.NumberFormat("en-US", {
        style: "currency",
        currency:
          value?.currency || S.workspace?.household?.base_currency || "USD",
        minimumFractionDigits: cents ? 2 : 0,
        maximumFractionDigits: cents ? 2 : 0,
      }).format(n)
    : "—";
}
export const pct = (v) =>
  v === null || v === undefined
    ? "—"
    : `${(Number(v) * 100).toFixed(2).replace(/\.00$/, "")}%`;
export const human = (s) =>
  String(s ?? "Unknown")
    .replaceAll("_", " ")
    .replace(/^./, (c) => c.toUpperCase());
export function dateLabel(date, opts = { month: "short", day: "numeric" }) {
  if (!date) return "Not provided";
  const d = new Date(String(date).slice(0, 10) + "T12:00:00");
  return Number.isNaN(d.getTime())
    ? "Not provided"
    : d.toLocaleDateString("en-US", opts);
}
export const fullDate = (d) =>
  dateLabel(d, { month: "long", day: "numeric", year: "numeric" });
export const account = (id) => S.workspace.accounts.find((a) => a.id === id);
export const accountName = (id) =>
  account(id)?.nickname || "Account unavailable";
export const accountType = (a) =>
  [
    "checking",
    "savings",
    "money_market_deposit",
    "cash",
    "brokerage_sweep",
  ].includes(a.type)
    ? "Cash"
    : a.type === "credit_card"
      ? "Credit cards"
      : [
            "mortgage",
            "auto_loan",
            "student_loan",
            "personal_loan",
            "bnpl",
            "sbloc",
            "margin",
          ].includes(a.type)
        ? "Loans"
        : a.type === "estimated_asset"
          ? "Property"
          : "Investments";
export const accountIcon = (a) =>
  ({
    Cash: "bank",
    "Credit cards": "card",
    Loans: "receipt",
    Property: "house",
    Investments: "chart",
  })[accountType(a)];
export const access = (a) =>
  accountType(a) === "Loans"
    ? "Outstanding debt"
    : accountType(a) === "Credit cards"
      ? "Credit card balance"
      : {
          immediate: "Available now",
          same_day: "Same day",
          "1_3_days": "1–3 business days",
          investment: "Investment asset",
          estimated: "Estimated asset",
          dated: "Date restricted",
          contingent_borrowing: "Credit facility",
        }[a.liquidity_tier] || human(a.liquidity_tier);
export const chip = (s, t = "neutral") =>
  `<span class="chip ${t}">${esc(s)}</span>`;
export const link = (label, path) =>
  `<a class="text-button" href="#${esc(path)}">${esc(label)} ${icon("arrow")}</a>`;
export const btn = (label, detail, ico = "arrow", primary = false) =>
  `<button class="button ${primary ? "primary" : ""}" data-detail="${esc(detail)}">${icon(ico)}${esc(label)}</button>`;
export const action = (label, name, primary = false, extra = "") =>
  `<button class="button ${primary ? "primary" : ""}" data-action="${esc(name)}" ${extra}>${esc(label)}</button>`;
export const heading = (title, sub, actions = "") =>
  `<div class="page-heading"><div><h1>${esc(title)}</h1><p>${esc(sub)}</p></div><div class="heading-actions">${actions}</div></div>`;
export const panel = (title, body, actions = "", sub = "") =>
  `<section class="panel"><div class="panel-inner"><div class="panel-head"><div><h2>${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ""}</div>${actions}</div>${body}</div></section>`;
export const empty = (title, description = "") =>
  `<div class="empty-state">${icon("receipt")}<h3>${esc(title)}</h3>${description ? `<p>${esc(description)}</p>` : ""}</div>`;
export const note = (text) =>
  `<div class="mini-banner">${icon("info")}<span>${esc(text)}</span></div>`;
export const row = (label, value) =>
  `<div class="summary-line"><span>${esc(label)}</span><strong>${esc(value ?? "—")}</strong></div>`;
export const detailHeading = (title, sub, amount) =>
  `<h2 id="detail-title">${esc(title)}</h2><p class="detail-sub">${esc(sub)}</p>${amount !== undefined ? `<div class="detail-amount">${money(amount, true)}</div>` : ""}`;
export const stat = (label, value, sub, detail) =>
  `<button class="metric" data-detail="${esc(detail)}"><span class="metric-label">${esc(label)}</span><strong class="metric-value">${esc(value)}</strong><span class="metric-bottom">${esc(sub)} ${icon("arrow")}</span></button>`;
export function table(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
export const cells = (...values) =>
  `<tr>${values.map((v) => `<td>${v}</td>`).join("")}</tr>`;
export const evidence = (title, data) =>
  `<details class="evidence"><summary>${esc(title)}</summary><pre>${esc(JSON.stringify(data, null, 2))}</pre></details>`;
export async function api(
  path,
  { method = "GET", body, signal, timeout = 30000 } = {},
) {
  const controller = new AbortController();
  const userId = S.session?.user?.id;
  const timer = setTimeout(() => controller.abort(), timeout);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  try {
    const r = await fetch(path, {
      method,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" && S.session ? {"X-CSRF-Token": S.session.csrf_token} : {}),
        ...(method !== "GET" && S.revision !== null ? {"If-Match": String(S.revision)} : {}),
      },
      credentials: "same-origin",
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const d = await r.json();
    if (r.status === 401 && S.session) document.dispatchEvent(new Event("finpilot-session-expired"));
    if (userId !== S.session?.user?.id) throw new Error("Your session changed. Please try again.");
    const updatedRevision = r.headers.get("X-Workspace-Revision");
    if (r.ok && updatedRevision) S.revision = Number(updatedRevision);
    if (!r.ok || d.error)
      throw new Error(
        typeof d.detail === "string"
          ? d.detail
          : Array.isArray(d.detail) ? d.detail.map(item => item.msg).join(". ") : d.error || `Request failed (${r.status})`,
      );
    return d;
  } catch (e) {
    if (e.name === "AbortError")
      throw new Error("The request timed out or was cancelled. Try again.");
    throw e;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
export function reviews() {
  const w = S.workspace;
  const items = [];
  for (const c of w.cards) {
    const bill = w.bills.find((b) => b.payee_account_id === c.account_id);
    if (bill && val(c.statement_balance) > val(bill.amount))
      items.push({
        title: `${c.nickname}: statement differs from plan`,
        description: `${money(bill.amount, true)} planned · ${money(c.statement_balance, true)} on the statement`,
        detail: `bill:${bill.id}`,
        difference: val(c.statement_balance) - val(bill.amount),
      });
    else if (!bill && val(c.statement_balance) > 0)
      items.push({
        title: `${c.nickname}: statement has no bill rule`,
        description: `${money(c.statement_balance, true)} needs review; ${w.liabilities.some((l) => l.account_id === c.account_id) ? "review its funding" : "also absent from the debt calculation"}.`,
        path: `accounts/${c.account_id}`,
      });
  }
  for (const a of w.accounts.filter((a) => !a.connection_healthy))
    items.push({
      title: `${a.nickname}: connection needs attention`,
      description:
        a.connection_issue || "Review the last successful connection.",
      path: `accounts/${a.id}/details`,
    });
  return items;
}
export function sourceNote(a) {
  return `${human(a.provenance?.verification)} · ${a.provenance?.source || "Source not provided"} · ${fullDate(a.provenance?.as_of)}`;
}
export function cashAccounts() {
  return S.workspace.accounts.filter((a) => accountType(a) === "Cash");
}
export function upcomingPay() {
  return S.data.plan.paychecks.find((p) => p.pay_date >= S.data.as_of);
}
export function billFor(o) {
  return S.workspace.bills.find((b) => b.name === o.name);
}
export function billRows(obligations, compact = false) {
  return (
    obligations
      .map((o) => {
        const b = billFor(o);
        return `<button class="upcoming-row" data-detail="bill:${esc(b?.id || "")}" data-date="${esc(o.due_date)}"><span class="date-square">${dateLabel(o.due_date, { month: "short" }).toUpperCase()}<b>${dateLabel(o.due_date, { day: "numeric" })}</b></span><span class="row-body"><span class="row-title">${esc(o.name)}</span><span class="row-sub">${esc(compact ? human(o.execution_owner) : o.funding_account + " · " + human(o.execution_owner))}</span></span><span class="row-end">${money(o.amount)}</span>${icon("arrow")}</button>`;
      })
      .join("") || empty("Nothing due in this window")
  );
}
export function forecastChart(fc, compact = false) {
  const pts = (fc?.daily || []).map((d) => ({
    date: d.date,
    value: Number(d.closing),
  }));
  if (!pts.length)
    return empty(
      "No forecast available",
      fc?.error || "Add an eligible account to see its projected balance.",
    );
  const W = 650,
    H = 220,
    L = 48,
    R = 18,
    T = 24,
    B = 30,
    lo = Math.min(0, ...pts.map((p) => p.value)),
    hi = Math.max(1, ...pts.map((p) => p.value)),
    spread = Math.max(hi - lo, 100),
    min = lo - spread * 0.08,
    max = hi + spread * 0.12;
  const x = (i) => L + (i / Math.max(pts.length - 1, 1)) * (W - L - R),
    y = (n) => T + ((max - n) / (max - min)) * (H - T - B);
  const coords = pts.map((p, i) => [x(i), y(p.value)]),
    path = coords.map((p, i) => (i ? "L" : "M") + p.join(",")).join(" ");
  const ticks = [lo, (lo + hi) / 2, hi];
  const indices = [
    ...new Set([
      0,
      Math.round((pts.length - 1) / 3),
      Math.round(((pts.length - 1) * 2) / 3),
      pts.length - 1,
    ]),
  ];
  const hit = compact ? indices : pts.map((_, i) => i);
  return `<div class="chart api-chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Projected balance for ${esc(fc.account_name)}. Lowest ${esc(money(fc.low_point?.balance))} on ${esc(fc.low_point?.date)}."><defs><linearGradient id="forecast-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7298dd" stop-opacity=".19"/><stop offset="1" stop-color="#7298dd" stop-opacity="0"/></linearGradient></defs>${ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--border)" stroke-dasharray="3 5"/><text x="${L - 7}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${esc(money(v))}</text>`).join("")}<path d="${path} L${x(pts.length - 1)},${H - B} L${L},${H - B}Z" fill="url(#forecast-fill)"/><path d="${path}" fill="none" stroke="#5178c8" stroke-width="2.3"/>${hit.map((i) => `<g role="button" tabindex="0" class="chart-point" data-detail="forecast:${i}" aria-label="${fullDate(pts[i].date)}: ${money(pts[i].value, true)}"><circle cx="${x(i)}" cy="${y(pts[i].value)}" r="11" fill="transparent"/><circle cx="${x(i)}" cy="${y(pts[i].value)}" r="${indices.includes(i) ? 3 : 1.5}" fill="white" stroke="#5178c8" stroke-width="1.5"/><title>${fullDate(pts[i].date)}: ${money(pts[i].value, true)}</title></g>`).join("")}${indices.map((i) => `<text x="${x(i)}" y="${H - 5}" text-anchor="middle" font-size="11" fill="var(--muted)">${dateLabel(pts[i].date)}</text>`).join("")}</svg></div>`;
}
export function allocationBar(pay) {
  const total = val(pay.available) || 1;
  return `<div class="allocation-bar">${groups
    .map((g, i) => {
      const n = (pay.allocations || [])
        .filter((a) => g.purposes.includes(a.purpose))
        .reduce((s, a) => s + val(a.amount), 0);
      return n
        ? `<button style="width:${(n / total) * 100}%;--segment:${g.color}" data-detail="paycheck:${esc(pay.income_event_id)}" aria-label="${esc(g.name)}: ${money(n)}" title="${esc(g.name)}: ${money(n)}"></button>`
        : "";
    })
    .join("")}</div>`;
}
export function accountCards(list) {
  return `<div class="account-grid">${list.map((a) => `<a class="account-card" href="#accounts/${esc(a.id)}"><div class="account-card-head"><span class="circle-icon">${icon(accountIcon(a))}</span>${chip(!a.connection_healthy ? "Needs attention" : human(a.provenance?.verification), !a.connection_healthy ? "orange" : "neutral")}</div><h3>${esc(a.nickname)}</h3><p>${esc([a.institution, a.mask].filter(Boolean).join(" \u00b7 "))}</p><strong class="amount">${money(a.current, true)}</strong><div class="account-card-footer"><span>${esc(access(a))}</span>${icon("arrow")}</div></a>`).join("")}</div>`;
}
