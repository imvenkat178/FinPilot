import { initializeWorkflowUI, resetWorkflowUI, workflowPartsHTML, rememberProposals, showCsvForm } from "./workflows.js";
import { S, $, esc, api, icon, account, sections, human } from "./core.js";
import { initializeKnowledge, resetKnowledge, closeKnowledge, selectedDocuments, setSelectedDocuments, showDocuments, showConnections, showAccess } from "./knowledge.js";
let controller = null,
  generation = 0,
  hasConversation = false,
  suggestions = [],
  conversationId = null,
  answers = [],
  view = "chat";
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
  panel.innerHTML = `<div class="assistant-header"><span class="assistant-symbol">${icon("spark")}</span><div class="copilot-heading"><h2 id="assistant-title">FinPilot assistant</h2><span id="assistant-model" class="copilot-preview">Answers grounded in your financial data</span></div><button class="icon-button" data-action="new-chat" aria-label="New conversation" title="New conversation">${icon("plus")}</button><button class="icon-button" data-action="close-assistant" aria-label="Close assistant" title="Close assistant">${icon("close")}</button></div><div class="copilot-context"><span>${icon("wallet")}<span id="copilot-context-name">Your household</span></span><span class="copilot-context-tag">VIEWING</span></div><div class="assistant-tools"><button class="text-button" data-workflow="library">Workflows</button><button class="text-button" data-action="chat-history">${icon("receipt")}Conversations</button><button class="text-button" data-action="knowledge-documents">${icon("plus")}Documents & sources</button></div><div id="assistant-secondary" hidden></div><div id="chat-log" role="log" aria-live="polite" aria-label="Assistant conversation"></div><div id="chat-suggestions" class="copilot-suggestions"></div><div id="chat-composer" class="copilot-composer"><div id="chat-selected-documents" class="chat-selected-documents" hidden></div><form id="chat-form"><label class="sr-only" for="chat-input">Message FinPilot assistant</label><textarea id="chat-input" rows="2" maxlength="3000" placeholder="Ask about your money…" required></textarea><div class="composer-bottom"><span>${icon("shield")}Private conversation</span><button id="chat-send" class="primary icon-button" aria-label="Send message">${icon("arrow-up")}</button></div></form><span class="copilot-disclosure">Conversations are saved to your account. FinPilot uses recent turns and your selected documents; financial calculations use current workspace data.</span><span class="copilot-disclosure-compact">Saved to your account · Recent conversation context</span></div>`;
  initializeKnowledge({
    onOpen() { setView("sources"); },
    onSelection(ids, documents) {
      const node = $("chat-selected-documents");
      if (!node) return;
      node.hidden = ids.length === 0;
      node.innerHTML = `<span>${ids.length} document${ids.length === 1 ? "" : "s"} selected</span><button class="text-button" data-action="knowledge-documents">Manage sources</button><div>${ids.map(id => `<span class="source-pill">${icon("receipt")}${esc(documents.find(doc => doc.id === id)?.title || "Saved document")}</span>`).join("")}</div>`;
    },
  });
  initializeWorkflowUI({
    ask, ensureConversation: async () => {
      if(!conversationId) {
        const ticket=generation, session=S.session;
        const data=await api('/api/conversations',{method:'POST',body:{title:'New conversation'}});
        if(ticket!==generation || session!==S.session) throw Error('The conversation changed. Open the upload again.');
        conversationId=data.conversation.id; rememberUrl(conversationId);
      }
      return conversationId;
    }, open: () => {openAssistant(); if(view !== "chat") backToConversation();},
    append: html => {
      const log=$("chat-log"); log.insertAdjacentHTML("beforeend", html);
      const last=log.lastElementChild;
      if(last?.matches?.(".workflow-form,.workflow-library")) last.scrollIntoView?.({block:"start"});
      else log.scrollTop=log.scrollHeight;
    },
    error: text => $("chat-log").insertAdjacentHTML("beforeend", '<p class="chat-error" role="alert">'+esc(text)+'</p>'),
    handoff: async (name,args) => {
      if (["upload_document","select_documents"].includes(name)) return showDocuments();
      if (name === "upload_csv") return showCsvForm(args);
      if (name === "connect_mcp") return showConnections();
      if (name === "create_mcp_access") return showAccess();
      if (name === "new_conversation") return resetAssistant();
      if (name === "open_conversation") return resumeConversation(args.record_id);
      document.dispatchEvent(new CustomEvent("finpilot-chat-handoff",{detail:{name,args}}));
    }
  });
  welcome();
  $("chat-form").addEventListener("submit", (e) => { e.preventDefault(); ask($("chat-input").value); });
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("chat-form").requestSubmit(); }
  });
  api("/api/ask/suggestions").then((d) => { suggestions = d.suggestions || []; renderSuggestions(); }).catch(() => {});
  if (typeof location !== "undefined") {
    const saved = new URLSearchParams(location.search).get("conversation");
    if (saved) resumeConversation(saved);
  }
}
function setView(next) {
  view = next;
  $("chat-log").hidden = next === "sources";
  $("chat-suggestions").hidden = next !== "chat";
  $("chat-composer").hidden = next !== "chat";
  if (next !== "sources") closeKnowledge();
}
function rememberUrl(id) {
  if (typeof window === "undefined" || !window.history?.replaceState || !window.location?.href) return;
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("conversation", id); else url.searchParams.delete("conversation");
  window.history.replaceState(null, "", url);
}
export function backToConversation() {
  setView("chat");
  if (controller) { $("chat-input").focus(); return; }
  if (!answers.length) welcome();
  else {
    hasConversation = true;
    $("chat-log").innerHTML = answers.map(savedAnswerHTML).join("");
    renderSuggestions();
    $("chat-log").scrollTop = $("chat-log").scrollHeight;
  }
  $("chat-input").focus();
}
function savedAnswerHTML(d) {
  return `<div class="chat-message user">${esc(d.question)}</div>${d.at ? `<p class="saved-answer-meta">${esc(new Date(d.at).toLocaleString())} · Workspace revision ${esc(d.revision)}</p>` : ""}` + responseHTML(d, {page:d.viewing?.page || "overview", name:d.viewing?.name || "Your household"});
}
function welcome() {
  hasConversation = false;
  const a = context().account;
  $("chat-log").innerHTML =
    `<div class="copilot-welcome"><h3>What would you like to do?</h3><p>Explore ${a ? `your ${esc(a.nickname)} account` : "your accounts, plans, and the numbers behind them"} with FinPilot.</p></div>`;
  renderSuggestions();
}
function renderSuggestions() {
  $("chat-suggestions").hidden = view !== "chat" || hasConversation;
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
  renderModelStatus();
  if (view === "chat" && !hasConversation) welcome();
  else renderSuggestions();
}
function renderModelStatus(status) {
  if (status && S.data) S.data.llm = { ...S.data.llm, ...status };
  $("assistant-model").textContent = S.data?.llm?.reachable
    ? "Connected model · Financial calculations"
    : "Calculator mode · Model unavailable";
}
export function openAssistant() {
  if(S.page === "assistant") mountAssistantLayout();
  const detail = $("detail-dialog");
  if (detail.open) detail.close();
  if (!$("assistant-dialog").open) { if(S.page === "assistant") $("assistant-dialog").show(); else $("assistant-dialog").showModal(); }
  $("chat-input").focus();
}
export function closeAssistant() {
  if(S.page === "assistant") return;
  if ($("assistant-dialog").open) $("assistant-dialog").close();
}
export function resetAssistant() {
  generation++;
  controller?.abort();
  controller = null;
  conversationId = null;
  answers = [];
  resetKnowledge();
  resetWorkflowUI();
  setView("chat");
  rememberUrl(null);
  $("chat-send").disabled = false;
  $("chat-input").value = "";
  welcome();
  $("chat-input").focus();
}
export async function ask(question, workflowInput = null) {
  const q = String(question || "").trim();
  if (!q || controller) return;
  openAssistant();
  if (view !== "chat") backToConversation();
  if (!hasConversation) $("chat-log").innerHTML = "";
  hasConversation = true;
  renderSuggestions();
  const scope = context();
  const current = ++generation;
  controller = new AbortController();
  $("chat-send").disabled = true;
  $("chat-input").value = "";
  $("chat-log").insertAdjacentHTML(
    "beforeend",
    `<div class="chat-message user">${esc(q)}</div><div id="pending-response" class="copilot-pending"><span class="assistant-symbol">${icon("spark")}</span><span>Checking your data and selected sources…</span></div>`,
  );
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  try {
    const a = scope.account;
    const d = await api("/api/ask", {
      method: "POST",
      body: {
        question: q,
        ...(workflowInput ? {workflow_input: workflowInput} : {}),
        conversation_id: conversationId,
        document_ids: selectedDocuments(),
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
    if (d.model_status) renderModelStatus(d.model_status);
    if (d.workspace_changed) document.dispatchEvent(new Event("finpilot-data-changed"));
    conversationId = d.conversation_id || conversationId;
    rememberUrl(conversationId);
    refreshWorkspaceHistory();
    answers.push({...d, question:q, viewing:d.viewing || {page:scope.page, name:scope.name}});
    const pending = $("pending-response");
    if (pending) pending.outerHTML = responseHTML(d, scope);
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
    const trace = Array.isArray(d.trace) ? d.trace : [];
    const modelComposed = trace.some(step => step.node === "compose" && step.mode === "model");
    const modelRejected = modelComposed && trace.some(step => step.node === "verify" && step.result === "rejected");
    const composeFallback = trace.find(step => step.node === "compose" && step.mode === "template");
    const documentSources = Array.isArray(d.document_sources) ? d.document_sources : [];
    const documentAnswer = d.intent === "document_retrieval" || documentSources.length > 0;
    const memoryAnswer = d.intent === "memory_retrieval";
    const responseStatus = d.workflow ? (d.workflow.planner?.accepted ? "AI interpretation · Verified application results" : "Application workflow") : d.used_model
      ? documentAnswer ? "Model-selected document excerpts · Sources attached" : memoryAnswer ? "Model-selected conversation excerpts" : "Model response · Calculation sources attached"
      : documentAnswer ? documentSources.length ? "Document excerpts · Sources attached" : "Document search"
      : memoryAnswer ? "Saved conversation excerpts"
      : modelRejected
        ? "Calculator response · Model wording rejected"
        : composeFallback?.reason?.startsWith("model call failed")
          ? "Calculator response · Model unavailable for this answer"
          : composeFallback?.reason === "no local model reachable"
            ? "Calculator response · Model not connected"
            : "Calculator response";
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
    const tools = [...new Set(d.tools_called || [])].filter(name => !["retrieve_documents", "conversation_memory"].includes(name));
    const assumptions = Array.isArray(d.assumptions)
      ? d.assumptions
      : d.assumptions
        ? [d.assumptions]
        : [];
    const sourceName =
      sections.find((s) => s[0] === path)?.[1] || "Financial details";
    return `<div class="copilot-response"><div class="response-author"><span class="assistant-symbol">${icon("spark")}</span><span>FinPilot<span class="response-ai-label">${d.workflow ? "ACTION" : d.used_model ? "AI" : documentAnswer ? "SOURCE" : memoryAnswer ? "MEMORY" : "CALC"}</span></span></div><p class="answer-text">${esc(d.answer || "No answer was returned. Please try a more specific question.")}</p>${workflowPartsHTML(d)}<div class="answer-status"><span>${esc(responseStatus)}</span>${d.confidence ? `<span>${esc(human(d.confidence))} confidence</span>` : ""}${d.state && d.state !== "informational" && !(d.workflow && d.state === "drafted") ? `<span>${esc(human(d.state))}</span>` : ""}</div>${d.memory_turns ? `<div class="answer-memory">${icon("repeat")}Used ${esc(d.memory_turns)} recent conversation turn${d.memory_turns === 1 ? "" : "s"}</div>` : ""}${documentSources.length ? `<div class="answer-document-sources"><span class="suggestion-label">DOCUMENT SOURCES</span>${documentSources.map((source, i) => `<button class="response-source" data-action="knowledge-view" data-id="${esc(source.id)}" data-chunk="${esc(source.chunk_id)}"><span>${icon("receipt")}<span><small>SOURCE [${esc(source.citation || i + 1)}] · PAGE ${esc(source.page || 1)}</small>${esc(source.title)}</span></span>${icon("arrow")}</button>`).join("")}</div>` : ""}${!documentAnswer && !memoryAnswer && tools.length ? `<a class="response-source" href="#${esc(path)}"><span>${icon("receipt")}<span><small>EXPLORE THE SOURCE</small>${esc(sourceName)}</span></span>${icon("arrow")}</a>` : ""}<details class="evidence"><summary>View sources and assumptions</summary><div class="response-provenance"><p>Asked while viewing <strong>${esc(scope.name)}</strong>.</p>${tools.length ? `<span class="suggestion-label">CALCULATIONS USED</span><ul>${tools.map((name) => `<li>${esc(human(name.replace(/^(get|check)_/, "")))}</li>`).join("")}</ul>` : ""}${assumptions.length ? `<span class="suggestion-label">ASSUMPTIONS & NOTES</span><ul>${assumptions.map((note) => `<li>${esc(note)}</li>`).join("")}</ul>` : ""}</div><details class="evidence"><summary>View full response data</summary><pre>${esc(JSON.stringify(d.evidence ?? {}, null, 2))}</pre></details></details></div>`;
}

export async function showHistory() {
  if (controller) return;
  openAssistant(); setView("history");
  const current = ++generation;
  $("chat-log").innerHTML = '<p class="note">Loading your saved conversations…</p>';
  try {
    const {conversations} = await api('/api/conversations');
    if (current !== generation) return;
    $("chat-log").innerHTML = `<div class="conversation-history"><div class="knowledge-heading"><button class="text-button" data-action="chat-back">${icon("arrow")}Back to chat</button><h3>Saved conversations</h3><p>Pick up where you left off. Each conversation keeps its own recent context and selected documents.</p></div>${conversations.length ? conversations.map(conversation => `<article class="conversation-item"><button class="conversation-open" data-action="chat-resume" data-id="${esc(conversation.id)}"><strong>${esc(conversation.title || "Untitled conversation")}</strong><span>${esc(new Date(conversation.updated_at).toLocaleString())}${conversation.id === conversationId ? " · Current conversation" : ""}</span></button><button class="icon-button subtle-danger" data-action="chat-delete" data-id="${esc(conversation.id)}" aria-label="Delete conversation ${esc(conversation.title || "Untitled conversation")}" title="Delete conversation">${icon("close")}</button></article>`).join("") : '<div class="knowledge-empty"><h4>Your conversations will appear here</h4><p>Ask your first question to start a saved conversation.</p></div>'}</div>`;
  } catch(error) { if(current === generation) $("chat-log").innerHTML = `<div class="chat-error" role="alert">${esc(error.message)}<button class="text-button" data-action="chat-history">Try again</button><button class="text-button" data-action="chat-back">Back to chat</button></div>`; }
}
export async function resumeConversation(id) {
  if (controller) return;
  openAssistant(); setView("history");
  const current = ++generation;
  $("chat-log").innerHTML = '<p class="note">Opening conversation…</p>';
  try {
    const data = await api(`/api/conversations/${encodeURIComponent(id)}`);
    if (current !== generation) return;
    const ids = data.conversation.document_ids || [];
    const library = ids.length ? await api("/api/documents") : null;
    if (current !== generation) return;
    conversationId = data.conversation.id;
    answers = data.answers || [];
    rememberProposals(data.proposals || []);
    const attached=new Set(answers.flatMap(a=>(a.parts||[]).flatMap(p=>p.type==='proposal'?[p.proposal.id]:[])));
    const unattached=(data.proposals||[]).filter(p=>!attached.has(p.id));
    if(unattached.length) answers.push({question:'CSV / reviewed actions',answer:'Saved action reviews for this conversation.',parts:[{type:'proposals',proposals:unattached}],workflow:{resolved:true},viewing:{name:'Your household'}});
    if(data.pending_workflow?.pending && !answers.some(a=>a.workflow?.pending))
      answers.push({question:'Pending workflow',answer:'Complete your previous request.',workflow:data.pending_workflow,parts:[{type:'clarification',question:'Complete the pending workflow details.'}]});
    setSelectedDocuments(ids, library?.documents);
    rememberUrl(conversationId);
    backToConversation();
  } catch (e) { if (current === generation) $("chat-log").innerHTML = `<div class="chat-error" role="alert">${esc(e.message)}<button class="text-button" data-action="chat-history">Back to conversations</button></div>`; }
}
export async function deleteConversation(id) {
  if (controller) return;
  const current = generation;
  try {
    await api(`/api/conversations/${encodeURIComponent(id)}`, {method:"DELETE"});
    if (current !== generation) return;
    if (id === conversationId) resetAssistant();
    await showHistory();
  } catch (e) { if (current === generation) $("chat-log").insertAdjacentHTML("afterbegin", `<p class="chat-error" role="alert">${esc(e.message)}</p>`); }
}

export function detachAssistantWorkspace() {
  const panel=$("assistant-dialog");
  if(panel && panel.parentElement?.id === "ai-workspace-chat") {
    if(panel.open) panel.close();
    document.body.appendChild(panel);
  }
}
export function mountAssistantLayout() {
  const panel=$("assistant-dialog"), host=$("ai-workspace-chat");
  if(!panel) return;
  if(S.page === "assistant" && host) {
    if(panel.parentElement!==host) {
      if(panel.open) panel.close();
      host.appendChild(panel);
    }
    panel.dataset.mode="workspace";
    panel.setAttribute("aria-modal","false");
    if(!panel.open) panel.show();
    refreshWorkspaceHistory();
  } else {
    panel.dataset.mode="drawer";
    panel.setAttribute("aria-modal","true");
  }
}
export async function refreshWorkspaceHistory() {
  const host=$("ai-conversation-list");
  if(!host) return;
  try {
    const data=await api("/api/conversations");
    if(!host.isConnected) return;
    host.innerHTML=(data.conversations || []).map(c=>'<button class="ai-history-item" data-action="chat-resume" data-id="'+esc(c.id)+'"><strong>'+esc(c.title)+'</strong><span>'+esc(new Date(c.updated_at).toLocaleDateString())+'</span></button>').join("") || '<p class="muted">Your first conversation starts here.</p>';
  } catch(error) { if(host.isConnected) host.textContent=error.message; }
}
