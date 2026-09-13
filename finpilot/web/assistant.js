import { S, $, esc, api, icon, human } from "./core.js";
import { inlineManagementForm, formPayload, syncFields } from "./manage.js";
import { renderComponents, proposalHTML } from "./conversation-renderer.js";

let panel, controller = null, generation = 0, thread = null, turns = [], history = [],
  activeScope = {kind: "household"}, historyLoaded = false, retry = null;
const prompts = ["How should I split my next paycheck?", "Show spending by category", "Add an account"];
const scopeContract = s => s?.kind === "account" ? {kind:"account",account_id:s.account_id} :
  s?.kind === "platform" ? {kind:"platform",platform:s.platform} : {kind:"household"};
const key = () => crypto.randomUUID().replaceAll("-", "");

export function chatPage() {
  return `<section class="chat-page"><div class="chat-page-heading"><span class="eyebrow">YOUR FINANCIAL WORKSPACE</span><h1>Let's put your money in motion.</h1><p>Understand, plan, and manage your accounts in one conversation.</p></div><div id="chat-mount"></div></section>`;
}

export function initializeAssistant() {
  panel = document.createElement("section");
  panel.className = "conversation-shell";
  panel.innerHTML = `<aside class="conversation-sidebar"><div class="thread-heading"><strong>Conversations</strong><button class="icon-button" data-action="new-chat" title="New conversation" aria-label="New conversation">${icon("plus")}</button></div><button class="button new-conversation" data-action="new-chat">${icon("spark")}Start a conversation</button><nav id="chat-threads" aria-label="Your conversations"></nav><div class="chat-sidebar-note">Your accounts. Your decisions.<br>Every change starts with a preview.</div></aside><div class="conversation-main"><div class="conversation-toolbar"><div><span class="assistant-symbol">${icon("spark")}</span><strong id="assistant-title">FinPilot</strong><span id="assistant-model">Calculator mode</span></div><button class="icon-button" data-action="chat-history" title="Refresh conversations" aria-label="Refresh conversations">${icon("repeat")}</button></div><div class="conversation-scope"><label for="chat-scope">Working with</label><select id="chat-scope" aria-label="Conversation account scope"></select><span id="chat-scope-note">Choose an account or platform to focus your analysis.</span></div><div id="chat-log" role="log" aria-live="polite" aria-label="Financial conversation"></div><div id="chat-suggestions" class="conversation-suggestions"></div><div class="conversation-composer"><form id="chat-form"><label for="chat-input" class="sr-only">Message FinPilot</label><textarea id="chat-input" rows="2" maxlength="3000" placeholder="Ask, plan, or tell me what to change…" required></textarea><button id="chat-send" class="primary icon-button" aria-label="Send message">${icon("arrow-up")}</button></form><p id="chat-mode-note">Changes require your confirmation.</p></div></div>`;
  $("assistant-dialog").append(panel);
  $("chat-form").addEventListener("submit", e => { e.preventDefault(); ask($("chat-input").value); });
  $("chat-input").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("chat-form").requestSubmit(); }
  });
  panel.addEventListener("change", e => {
    if (e.target.id === "chat-scope") {
      const value = e.target.value;
      activeScope = value.startsWith("account:") ? {kind:"account",account_id:value.slice(8)} :
        value.startsWith("platform:") ? {kind:"platform",platform:value.slice(9)} : {kind:"household"};
      $("chat-scope-note").textContent = "This scope applies to your next question. Earlier answers keep their original scope.";
    }
    if (e.target.closest("[data-chat-record]")) syncFields(e.target.closest("form"));
  });
  panel.addEventListener("click", e => { handleClick(e).catch(showError); });
  panel.addEventListener("submit", e => {
    const form = e.target;
    if (!form.matches("[data-chat-record]")) return;
    e.preventDefault(); e.stopPropagation();
    submitBatch([{op:"upsert",collection:form.dataset.kind,record_id:form.dataset.id || null,values:formPayload(form)}],
      `Save ${human(form.dataset.kind)} details`, form);
  });
  renderLog();
}

export function updateAssistantContext() {
  if (!panel) return;
  const mount = $("chat-mount");
  (mount || $("assistant-dialog")).append(panel);
  renderScope();
  $("assistant-model").textContent = S.data?.llm?.reachable ? "AI interpretation · Verified calculations" : "Calculator mode";
  $("chat-mode-note").textContent = S.workspace?.household?.payment_sandbox ?
    "Sample workspace · Payments are simulated · Changes require confirmation" : "Changes require confirmation · Live payments are not connected";
  if (mount && !historyLoaded) { historyLoaded = true; showHistory(true); }
}

function renderScope() {
  const accounts = S.workspace?.accounts || [];
  const platforms = [...new Set(accounts.map(a => a.institution).filter(Boolean))].sort();
  $("chat-scope").innerHTML = `<option value="household">Your household</option>` +
    `<optgroup label="Platforms">${platforms.map(p => `<option value="platform:${esc(p)}">${esc(p)}</option>`).join("")}</optgroup>` +
    `<optgroup label="Accounts">${accounts.map(a => `<option value="account:${esc(a.id)}">${esc(a.nickname)} · ${esc(a.mask)}</option>`).join("")}</optgroup>`;
  $("chat-scope").value = activeScope.kind === "account" ? "account:" + activeScope.account_id :
    activeScope.kind === "platform" ? "platform:" + activeScope.platform : "household";
}

function renderHistory() {
  $("chat-threads").innerHTML = history.length ? history.map(t => `<button class="thread-item ${thread?.id === t.id ? "selected" : ""}" data-chat-thread="${esc(t.id)}"><strong>${esc(t.title)}</strong><small>${esc(new Date(t.updated_at).toLocaleDateString())}</small></button>`).join("") : '<p class="thread-empty">Conversations will appear here as you work.</p>';
}

function responseHTML(d, turnKey) {
  const components = (d.components || []).map(c => c.type === "form" ? inlineManagementForm(c.collection, c.record_id, turnKey) : renderComponents([c])).join("");
  return `<article class="conversation-answer"><div class="chat-author">${icon("spark")}<strong>FinPilot</strong><span>${esc(d.scope?.label || "Your household")}</span></div><p class="answer-text">${esc(d.answer)}</p>${components}${d.proposal ? proposalHTML(d.proposal) : ""}${d.evidence ? `<details class="chat-evidence"><summary>Sources and calculation details</summary><p>Data as of ${esc(d.evidence.as_of)} · ${esc(d.evidence.scope.label)} · Saved data version ${esc(d.evidence.workspace_revision)}</p><pre>${esc(JSON.stringify(d.evidence, null, 2))}</pre></details>` : ""}</article>`;
}

function renderLog() {
  const log = $("chat-log");
  if (!log) return;
  log.innerHTML = turns.length ? turns.map((t, i) => `<div class="conversation-question">${esc(t.question)}</div>${t.pending ? `<div class="conversation-pending" role="status">${icon("spark")}Reading your accounts and preparing a response…</div>` : responseHTML(t.response, t.id || String(i))}`).join("") :
    `<div class="conversation-welcome"><span class="welcome-symbol">${icon("spark")}</span><h2>What would you like to do?</h2><p>See the whole picture, focus on one account, or turn a plan into a reviewed action.</p><div class="chat-welcome-grid"><button data-ask="Summarize my accounts">${icon("wallet")}Understand my accounts<small>Balances, availability, and what needs attention</small></button><button data-ask="How should I split my next paycheck?">${icon("split")}Plan my next paycheck<small>Bills, savings, debt, and existing investment accounts</small></button><button data-ask="Show spending by category">${icon("chart")}Explore my spending<small>Tables, charts, and questions about the details</small></button><button data-ask="Create a recurring rule">${icon("repeat")}Set up a recurring split<small>Make a plan, review it, and save it here</small></button></div></div>`;
  log.querySelectorAll("[data-chat-record]").forEach(form => syncFields(form));
  $("chat-suggestions").innerHTML = prompts.map(p => `<button data-ask="${esc(p)}">${esc(p)}</button>`).join("");
  log.scrollTop = log.scrollHeight;
}

export function openAssistant() {
  if (S.accountId) activeScope = {kind:"account",account_id:S.accountId};
  if ($("detail-dialog").open) $("detail-dialog").close();
  if (S.page !== "chat") location.hash = "chat";
  renderScope(); $("chat-input")?.focus();
}
export function closeAssistant() { if ($("assistant-dialog").open) $("assistant-dialog").close(); }
export function resetAssistant({forget = false} = {}) {
  generation++; controller?.abort(); controller = null;
  thread = null; turns = []; retry = null;
  if (forget) { history = []; historyLoaded = false; }
  else historyLoaded = true;
  activeScope = {kind:"household"};
  if (!panel) return;
  $("chat-input").value = ""; $("chat-send").disabled = false;
  renderLog(); renderHistory(); renderScope();
}

async function ensureThread(current, signal) {
  if (thread) return;
  const created = await api("/api/conversations", {method:"POST",body:{scope:activeScope},signal});
  if (current === generation) thread = created;
}

export async function ask(question, useRetry = false) {
  const q = String(question || "").trim();
  if (!q || controller) return;
  openAssistant();
  const current = ++generation;
  controller = new AbortController(); const signal = controller.signal;
  const requestId = useRetry && retry?.question === q ? retry.id : key();
  const scope = useRetry && retry?.question === q ? retry.scope : {...activeScope};
  retry = {question:q,id:requestId,scope};
  $("chat-input").value = ""; $("chat-send").disabled = true;
  const pending = {id:requestId,question:q,pending:true}; turns.push(pending); renderLog();
  try {
    await ensureThread(current, signal);
    if (current !== generation) return;
    const d = await api(`/api/conversations/${thread.id}/messages`, {method:"POST",body:{
      question:q,client_message_id:requestId,conversation_revision:thread.revision,scope},signal,timeout:30000});
    if (current !== generation) return;
    thread.revision = d.conversation_revision; activeScope = scopeContract(d.scope);
    Object.assign(pending,{pending:false,response:d}); retry = null;
    if (d.proposal?.state === "pending") supersedeOtherPreviews(d.proposal.id);
    renderLog(); renderScope(); await showHistory(false);
  } catch (e) {
    if (current !== generation) return;
    turns = turns.filter(t => t !== pending); renderLog();
    $("chat-log").insertAdjacentHTML("beforeend", `<div class="chat-error" role="alert">${esc(e.message)}<button class="text-button" data-chat-retry="1">Retry this message</button></div>`);
    if (thread) {
      try { const latest = await api(`/api/conversations/${thread.id}`); if (current === generation) thread.revision = latest.revision; } catch {}
    }
  } finally { if (current === generation) { controller = null; $("chat-send").disabled = false; } }
}

function supersedeOtherPreviews(id) {
  for (const t of turns) if (t.response?.proposal?.id !== id && t.response?.proposal?.state === "pending") t.response.proposal.state = "superseded";
}

async function submitBatch(operations, question, form = null) {
  if (controller) return;
  const current = ++generation;
  controller = new AbortController(); const signal = controller.signal; $("chat-send").disabled = true;
  const button = form?.querySelector('[type="submit"]'); if (button) button.disabled = true;
  try {
    await ensureThread(current, signal);
    if (current !== generation) return;
    const d = await api(`/api/conversations/${thread.id}/proposals`, {method:"POST",body:{
      question,client_message_id:key(),conversation_revision:thread.revision,scope:activeScope,batch:{operations}},signal});
    if (current !== generation) return;
    thread.revision = d.conversation_revision; turns.push({id:key(),question,response:d});
    if (d.proposal?.state === "pending") supersedeOtherPreviews(d.proposal.id);
    renderLog(); await showHistory(false);
  } catch (e) {
    if (current !== generation) return;
    const target = form?.querySelector(".form-result");
    if (target) target.textContent = e.message; else showError(e);
  } finally {
    if (current === generation) { controller = null; $("chat-send").disabled = false; }
    if (button?.isConnected) button.disabled = false;
  }
}

function showError(e) { $("chat-log")?.insertAdjacentHTML("beforeend", `<p class="chat-error" role="alert">${esc(e.message)}</p>`); }

async function selectThread(id) {
  generation++; controller?.abort(); controller = null; const current = generation;
  $("chat-send").disabled = true;
  try {
    const data = await api(`/api/conversations/${encodeURIComponent(id)}`);
    if (current !== generation) return;
    thread = data; turns = data.turns; retry = null; activeScope = scopeContract(data.scope);
    renderLog(); renderHistory(); renderScope();
    if (data.has_more) $("chat-log").insertAdjacentHTML("afterbegin", `<button class="text-button" data-chat-older="${esc(data.turns[0].sequence)}">Load earlier messages</button>`);
  } catch (e) { if (current === generation) showError(e); }
  finally { if (current === generation) $("chat-send").disabled = false; }
}

export async function showHistory(restore = false) {
  const current = generation;
  try {
    const result = await api("/api/conversations");
    if (current !== generation) return;
    history = result.conversations; renderHistory();
    if (restore && !thread && !turns.length && history.length) await selectThread(history[0].id);
  } catch (e) { if (current === generation) $("chat-threads").innerHTML = `<p class="chat-error">${esc(e.message)}</p>`; }
}

async function handleClick(e) {
  const button = e.target.closest("button"); if (!button) return;
  if (button.dataset.chatThread) return selectThread(button.dataset.chatThread);
  if (button.dataset.chatRetry) return retry && ask(retry.question, true);
  if (button.dataset.chatSetup) return ask(`Add ${button.dataset.chatSetup}`);
  if (button.dataset.chatSimulate) return submitBatch([{op:"simulate_group",group_id:button.dataset.chatSimulate}], "Review this sample payment simulation");
  if (button.dataset.chatOlder && thread) {
    const current = generation, id = thread.id;
    const data = await api(`/api/conversations/${id}?before=${encodeURIComponent(button.dataset.chatOlder)}`);
    if (current !== generation || thread?.id !== id) return;
    turns = [...data.turns,...turns]; renderLog();
    if (data.has_more) $("chat-log").insertAdjacentHTML("afterbegin", `<button class="text-button" data-chat-older="${esc(data.turns[0].sequence)}">Load earlier messages</button>`);
    return;
  }
  const proposalId = button.dataset.chatConfirm || button.dataset.chatCancel || button.dataset.chatRefresh;
  if (!proposalId || controller) return;
  const proposal = turns.find(t => t.response?.proposal?.id === proposalId)?.response.proposal;
  if (!proposal) return;
  if (button.dataset.chatRefresh) return submitBatch(proposal.operations, "Refresh this action preview using my current data");
  const current = generation, id = thread.id; button.disabled = true;
  try {
    const result = await api(`/api/conversations/${id}/proposals/${proposalId}/${button.dataset.chatConfirm ? "confirm" : "cancel"}`, {
      method:"POST",body:button.dataset.chatConfirm ? {digest:proposal.digest,confirm:true,
        confirm_simulation:!!button.closest(".chat-proposal")?.querySelector('[name="confirm_simulation"]')?.checked} : undefined});
    if (current !== generation || thread?.id !== id) return;
    if (result.conversation_revision) thread.revision = Math.max(thread.revision, result.conversation_revision);
    for (const t of turns) if (t.response?.proposal?.id === proposalId) {
      t.response.proposal.state = button.dataset.chatConfirm ? "applied" : "canceled";
      if (button.dataset.chatConfirm) t.response.proposal.receipt = result;
    }
    renderLog();
    if (button.dataset.chatConfirm) document.dispatchEvent(new Event("finpilot-data-changed"));
  } catch (error) {
    if (current !== generation || thread?.id !== id) return;
    const target = button.closest(".chat-proposal")?.querySelector(".form-result");
    if (target) target.textContent = error.message; else showError(error);
    button.disabled = false;
  }
}
