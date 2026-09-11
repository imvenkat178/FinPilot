import { S, $, esc, api, icon, account, sections, human } from "./core.js";
let controller = null,
  generation = 0,
  hasConversation = false,
  suggestions = [];
const contextualPrompts = {
  accounts: [
    "Summarize my accounts",
    "Which account connections need attention?",
    "Explain my net worth",
  ],
  paychecks: [
    "How should I split my next paycheck?",
    "Why is my paycheck split uneven?",
    "What bills does my paycheck cover?",
  ],
  bills: [
    "What bills are coming up?",
    "Which payments need review?",
    "How much is due this month?",
  ],
  cashflow: [
    "Explain my cash forecast",
    "How much can I spend this week?",
    "How much should I keep in checking?",
  ],
  goals: [
    "How are my savings reserves funded?",
    "How much is protected for emergencies?",
    "How should I split my next paycheck?",
  ],
  debt: [
    "Compare my debt repayment strategies",
    "How would an extra payment affect my debt?",
    "Explain my credit utilization",
  ],
  rules: [
    "Explain my recurring rules",
    "Which automations are paused?",
    "How should I split my next paycheck?",
  ],
  protection: [
    "Explain my tax assumptions",
    "How is my cash protected?",
    "Which account connections need attention?",
  ],
};
function context() {
  const a = S.accountId ? account(S.accountId) : null;
  return {
    account: a,
    page: S.page,
    name:
      a?.nickname ||
      (S.page === "overview"
        ? "Your household"
        : sections.find((s) => s[0] === S.page)?.[1]) ||
      "Your household",
  };
}
export function initializeAssistant() {
  const panel = $("assistant-dialog");
  panel.dataset.mode = "drawer";
  panel.innerHTML = `<div class="assistant-header"><span class="assistant-symbol">${icon("spark")}</span><div class="copilot-heading"><h2 id="assistant-title">FinPilot assistant</h2><span id="assistant-model" class="copilot-preview">Answers grounded in your financial data</span></div><button class="icon-button" data-action="chat-history" aria-label="Recent conversations" title="Recent conversations">${icon("receipt")}</button><button class="icon-button" data-action="new-chat" aria-label="New conversation" title="New conversation">${icon("plus")}</button><button class="icon-button" data-action="close-assistant" aria-label="Close assistant" title="Close assistant">${icon("close")}</button></div><div class="copilot-context"><span>${icon("wallet")}<span id="copilot-context-name">Your household</span></span><span class="copilot-context-tag">VIEWING</span></div><div id="chat-log" role="log" aria-live="polite" aria-label="Assistant conversation"></div><div id="chat-suggestions" class="copilot-suggestions"></div><div class="copilot-composer"><form id="chat-form"><label class="sr-only" for="chat-input">Message FinPilot assistant</label><textarea id="chat-input" rows="2" maxlength="3000" placeholder="Ask about your money…" required></textarea><div class="composer-bottom"><span>${icon("shield")}Grounded in calculations</span><button id="chat-send" class="primary icon-button" aria-label="Send message">${icon("arrow-up")}</button></div></form><span class="copilot-disclosure">Review the sources and assumptions behind each answer.</span></div>`;
  welcome();
  $("chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    ask($("chat-input").value);
  });
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $("chat-form").requestSubmit();
    }
  });
  api("/api/ask/suggestions")
    .then((d) => {
      suggestions = d.suggestions || [];
      renderSuggestions();
    })
    .catch(() => {});
}
function welcome() {
  hasConversation = false;
  const a = context().account;
  $("chat-log").innerHTML =
    `<div class="copilot-welcome"><h3>What would you like to understand?</h3><p>Explore ${a ? `your ${esc(a.nickname)} account` : "your accounts, plans, and the numbers behind them"} with FinPilot.</p></div>`;
  renderSuggestions();
}
function renderSuggestions() {
  const defaults = [
    "How should I split my next paycheck?",
    "How much can I spend this week?",
    "How much should I keep in checking?",
  ];
  const prompts = (
    contextualPrompts[S.page] || (suggestions.length ? suggestions : defaults)
  ).slice(0, 3);
  $("chat-suggestions").innerHTML =
    `<span class="suggestion-label">${hasConversation ? "KEEP EXPLORING" : "YOU COULD ASK"}</span>${prompts.map((q) => `<button data-ask="${esc(q)}">${esc(q)}${icon("arrow")}</button>`).join("")}`;
}
export function updateAssistantContext() {
  const scope = context();
  $("copilot-context-name").textContent = scope.name;
  $("copilot-context-name").title = scope.name;
  $("assistant-model").textContent = S.data?.llm?.reachable
    ? "Connected model · Financial calculations"
    : "Calculator mode · Local model not connected";
  if (!hasConversation) welcome();
  else renderSuggestions();
}
export function openAssistant() {
  const detail = $("detail-dialog");
  if (detail.open) detail.close();
  if (!$("assistant-dialog").open) $("assistant-dialog").showModal();
  $("chat-input").focus();
}
export function closeAssistant() {
  if ($("assistant-dialog").open) $("assistant-dialog").close();
}
export function resetAssistant() {
  generation++;
  controller?.abort();
  controller = null;
  $("chat-send").disabled = false;
  $("chat-input").value = "";
  welcome();
  $("chat-input").focus();
}
export async function ask(question) {
  const q = String(question || "").trim();
  if (!q || controller) return;
  openAssistant();
  if (!hasConversation) $("chat-log").innerHTML = "";
  hasConversation = true;
  const scope = context();
  const current = ++generation;
  controller = new AbortController();
  $("chat-send").disabled = true;
  $("chat-input").value = "";
  $("chat-log").insertAdjacentHTML(
    "beforeend",
    `<div class="chat-message user">${esc(q)}</div><div id="pending-response" class="copilot-pending"><span class="assistant-symbol">${icon("spark")}</span><span>Checking your financial data…</span></div>`,
  );
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  try {
    const a = scope.account;
    const d = await api("/api/ask", {
      method: "POST",
      body: {
        question: q,
        context: {account_id: a?.id || null, page: scope.page},
        untrusted_context: a
          ? JSON.stringify({ viewing_account: a.id, account_name: a.nickname })
          : "",
        include_evidence: true,
      },
      signal: controller.signal,
      timeout: 120000,
    });
    if (current !== generation) return;
    if (d.workspace_changed) document.dispatchEvent(new Event("finpilot-data-changed"));
    const pending = $("pending-response");
    pending.outerHTML = responseHTML(d, scope);
    renderSuggestions();
  } catch (e) {
    if (current !== generation) return;
    const p = $("pending-response");
    if (p)
      p.outerHTML = `<div class="chat-error" role="alert">${esc(e.message)}<p>No answer is available yet. Try again or refresh your connection.</p><button class="text-button" data-ask="${esc(q)}">Try again ${icon("arrow")}</button></div>`;
  } finally {
    if (current === generation) {
      controller = null;
      $("chat-send").disabled = false;
      $("chat-log").scrollTop = $("chat-log").scrollHeight;
    }
  }
}

function responseHTML(d, scope) {
    const toolNames = (d.tools_called || []).join(" ");
    const path =
      [
        [/money_overview|account_connections|liquidity/, "accounts"],
        [/paycheck|allocation/, "paychecks"],
        [/cash_forecast|spending_allowance|buffer/, "cashflow"],
        [/obligations|pay_bill|execution/, "bills"],
        [/debt|mortgage|card|utilization/, "debt"],
        [/tax|coverage|savings_vs_debt/, "protection"],
        [/recurring|automation|pause|skip/, "rules"],
      ].find(([pattern]) => pattern.test(toolNames))?.[1] || scope.page;
    const tools = [...new Set(d.tools_called || [])];
    const assumptions = Array.isArray(d.assumptions)
      ? d.assumptions
      : d.assumptions
        ? [d.assumptions]
        : [];
    const sourceName =
      sections.find((s) => s[0] === path)?.[1] || "Financial details";
    return `<div class="copilot-response"><div class="response-author"><span class="assistant-symbol">${icon("spark")}</span><span>FinPilot<span class="response-ai-label">${d.used_model ? "AI" : "CALC"}</span></span></div><p class="answer-text">${esc(d.answer || "No answer was returned. Please try a more specific question.")}</p><div class="answer-status"><span>${esc(d.used_model ? "Model response · Calculation sources attached" : "Calculator response")}${d.grounding?.ok === false ? " · Model wording rejected" : ""}</span>${d.confidence ? `<span>${esc(human(d.confidence))} confidence</span>` : ""}${d.state && d.state !== "informational" ? `<span>${esc(human(d.state))}</span>` : ""}</div><a class="response-source" href="#${esc(path)}"><span>${icon("receipt")}<span><small>EXPLORE THE SOURCE</small>${esc(sourceName)}</span></span>${icon("arrow")}</a><details class="evidence"><summary>View sources and assumptions</summary><div class="response-provenance"><p>Asked while viewing <strong>${esc(scope.name)}</strong>.</p>${tools.length ? `<span class="suggestion-label">CALCULATIONS USED</span><ul>${tools.map((name) => `<li>${esc(human(name.replace(/^(get|check)_/, "")))}</li>`).join("")}</ul>` : ""}${assumptions.length ? `<span class="suggestion-label">ASSUMPTIONS & NOTES</span><ul>${assumptions.map((note) => `<li>${esc(note)}</li>`).join("")}</ul>` : ""}</div><details class="evidence"><summary>View full calculation data</summary><pre>${esc(JSON.stringify(d.evidence ?? {}, null, 2))}</pre></details></details></div>`;
}

export async function showHistory() {
  if (controller) return;
  openAssistant();
  const current=++generation;
  $('chat-log').innerHTML='<p class="note">Loading your recent conversations…</p>';
  try {
    const {answers}=await api('/api/ask/history?limit=10');
    if(current!==generation)return;
    hasConversation=answers.length>0;
    if(!hasConversation){welcome();return;}
    $('chat-log').innerHTML=answers.slice().reverse().map(d=>
      `<div class="chat-message user">${esc(d.question)}</div><p class="auth-help">Saved ${esc(new Date(d.at).toLocaleString())} · Workspace revision ${esc(d.revision)}</p>`+
      responseHTML(d,{page:d.viewing?.page || 'overview',name:d.viewing?.name || 'Your household'})
    ).join('');
    renderSuggestions();
    $('chat-log').scrollTop=$('chat-log').scrollHeight;
  }catch(error){if(current===generation)$('chat-log').innerHTML=`<p class="chat-error" role="alert">${esc(error.message)}</p>`;}
}
