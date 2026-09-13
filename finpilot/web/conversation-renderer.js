import { esc, human, icon } from "./core.js";

const text = value => value && typeof value === "object" ?
  value.display || (value.amount !== undefined ? `${value.amount} ${value.currency || ""}`.trim() : JSON.stringify(value)) : String(value ?? "—");
const humanFields = object => Object.entries(object || {}).map(([k,v]) => `<tr><th>${esc(human(k))}</th><td>${esc(text(v))}</td></tr>`).join("");

export function chartHTML(c) {
  const points = (c.points || []).filter(p => Number.isFinite(Number(p.value)));
  if (!points.length) return `<div class="chat-empty-chart"><strong>${esc(c.title)}</strong><p>No recorded points for this scope and period.</p></div>`;
  const values = points.map(p => Number(p.value)), low = Math.min(0,...values), high = Math.max(0,...values);
  const span = high - low || 1;
  let svg;
  if (c.kind === "line") {
    const x = i => 30 + i * 600 / Math.max(1,points.length-1), y = v => 160 - (v-low) / span * 130;
    const path = values.map((v,i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
    svg = `<svg viewBox="0 0 660 195" role="img" aria-label="${esc(c.title)}"><line class="chart-baseline" x1="30" x2="630" y1="${y(0)}" y2="${y(0)}"/><path class="chat-chart-line" d="${path}"/>${points.length === 1 ? `<circle cx="30" cy="${y(values[0])}" r="4" class="chat-chart-bar"/>` : ""}<text x="30" y="185">${esc(points[0].label)}</text><text x="630" y="185" text-anchor="end">${esc(points.at(-1).label)}</text><text x="30" y="18">${esc(high.toFixed(2))} ${esc(c.currency)}</text></svg>`;
  } else {
    const selected = points.slice(0,20), x = v => 200 + (v-low) / span * 360, zero = x(0);
    svg = `<svg viewBox="0 0 680 ${selected.length*30+20}" role="img" aria-label="${esc(c.title)}"><line class="chart-baseline" x1="${zero}" x2="${zero}" y1="0" y2="${selected.length*30}"/>${selected.map((p,i) => `<text x="4" y="${i*30+19}">${esc(p.label.length > 27 ? p.label.slice(0,26)+"…" : p.label)}</text><rect class="chat-chart-bar" x="${Math.min(zero,x(Number(p.value)))}" y="${i*30+4}" width="${Math.max(0,Math.abs(x(Number(p.value))-zero))}" height="20" rx="3"><title>${esc(p.label)}: ${esc(p.value)} ${esc(c.currency)}</title></rect><text x="670" y="${i*30+19}" text-anchor="end">${esc(p.value)}</text>`).join("")}</svg>`;
  }
  return `<figure class="chat-chart"><figcaption>${esc(c.title)} <span>${esc(c.currency)}${points.length > 20 && c.kind !== "line" ? " · First 20 groups" : ""}</span></figcaption>${svg}</figure>`;
}

export function renderComponents(components) {
  return (components || []).map(c => {
    if (c.type === "metrics") return `<div class="chat-metrics">${c.items.map(i=>`<div><span>${esc(i.label)}</span><strong>${esc(i.value)}</strong></div>`).join("")}</div>`;
    if (c.type === "chart") return chartHTML(c);
    if (c.type === "table") return `<section class="chat-table"><h3>${esc(c.title)}</h3><div class="table-wrap"><table><thead><tr>${c.columns.map(k=>`<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>${c.rows.map(r=>`<tr>${r.map(v=>`<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>${!c.rows.length ? '<p class="muted">No recorded rows for this selection.</p>' : ""}${c.truncated ? '<p class="muted">First 100 rows shown.</p>' : ""}</section>`;
    if (c.type === "notes") return `<details class="chat-notes"><summary>Assumptions and limits</summary><ul>${c.items.map(n=>`<li>${esc(n)}</li>`).join("")}</ul></details>`;
    if (c.type === "capability") return c.name === "connect_bank" ? `<button class="button primary" data-detail="connections">${icon("bank")}Connect a bank securely</button>` : `<div class="chat-setup-actions">${["an account","income","a bill","a savings goal","a recurring rule"].map(label=>`<button class="button" data-chat-setup="${esc(label)}">Add ${esc(label)}</button>`).join("")}</div>`;
    return "";
  }).join("");
}

export function receiptHTML(receipt) {
  const groups = receipt.results.flatMap(r => r.groups || (r.group_id ? [{id:r.group_id}] : []));
  return `<div class="chat-receipt"><h3>${icon("check")}Reviewed change processed</h3><p>${esc(receipt.note)}</p><small>Receipt ${esc(receipt.id)} · ${esc(new Date(receipt.at).toLocaleString())}</small>${receipt.results.map(r=>`<div class="table-wrap"><table><tbody>${humanFields(r)}</tbody></table></div>`).join("")}${receipt.payment_mode === "simulation" ? groups.map(g=>`<button class="button" data-chat-simulate="${esc(g.id || g.group_id)}">Review sample payment simulation</button>`).join("") : ""}</div>`;
}

export function proposalHTML(p) {
  if (p.state === "applied" && p.receipt) return receiptHTML(p.receipt);
  return `<section class="chat-proposal" data-proposal="${esc(p.id)}"><div class="proposal-title">${icon("shield")}<strong>Review this change</strong><span class="chip">${esc(human(p.state))}</span></div>${p.preview.map(row=>`<div class="proposal-operation"><h3>${esc(row.title)}</h3>${row.accounts && Object.values(row.accounts).length ? `<p>${Object.values(row.accounts).map(esc).join(" → ")}</p>` : ""}<div class="table-wrap"><table><tbody>${humanFields(row.request.values || row.request)}</tbody></table></div>${row.note ? `<p class="muted">${esc(row.note)}</p>` : ""}${row.before && Object.keys(row.before).length ? `<details><summary>Before and after</summary><pre>${esc(JSON.stringify({before:row.before,after:row.after},null,2))}</pre></details>` : ""}${row.operation !== "upsert" ? `<details><summary>Preview results and checks</summary><pre>${esc(JSON.stringify(row.result,null,2))}</pre></details>` : ""}</div>`).join("")}<p class="proposal-expiry">Review valid until ${esc(new Date(p.expires_at).toLocaleTimeString())}. Changes to your financial data require a fresh preview.</p>${p.requires_simulation_confirmation && p.state === "pending" ? '<label class="checkbox-line"><input type="checkbox" name="confirm_simulation"> I understand this uses sample money and simulates a payment.</label>' : ""}<p class="form-result" role="status"></p><div class="proposal-actions">${p.state === "pending" ? `<button class="button primary" data-chat-confirm="${esc(p.id)}">Confirm ${p.requires_simulation_confirmation ? "simulation" : "change"}</button><button class="button" data-chat-cancel="${esc(p.id)}">Cancel</button>` : ""}${["pending","stale","expired"].includes(p.state) ? `<button class="text-button" data-chat-refresh="${esc(p.id)}">Refresh preview</button>` : ""}</div></section>`;
}
