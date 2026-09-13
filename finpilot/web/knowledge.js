import { S, $, esc, api, icon, human } from "./core.js";

let selected = [], documents = [], epoch = 0, visible = false, callbacks = {};
let catalog = null, connectionId = null;
const MAX_SELECTED = 10;
const when = value => value ? new Date(value).toLocaleDateString(undefined, {month:"short", day:"numeric", year:"numeric"}) : "";
const label = value => String(value || "").slice(0, 150);

export function initializeKnowledge(options = {}) { callbacks = options; }
export function selectedDocuments() { return [...selected]; }
export function setSelectedDocuments(ids = [], availableDocuments) {
  if (Array.isArray(availableDocuments)) documents = availableDocuments;
  selected = [...new Set(ids)].filter(id => !Array.isArray(availableDocuments) || documents.some(doc => doc.id === id)).slice(0, MAX_SELECTED);
  callbacks.onSelection?.(selected, documents);
}
export function resetKnowledge() {
  epoch++; visible = false; documents = []; selected = []; catalog = null; connectionId = null;
  if ($("assistant-secondary")) { $("assistant-secondary").innerHTML = ""; $("assistant-secondary").hidden = true; }
  callbacks.onSelection?.(selected, documents);
}
export function closeKnowledge() {
  epoch++; visible = false;
  if ($("assistant-secondary")) { $("assistant-secondary").hidden = true; $("assistant-secondary").innerHTML = ""; }
}
function layout(title, subtitle, body, tab = "documents") {
  return `<div class="knowledge-heading"><button class="text-button" data-action="chat-back">${icon("arrow")}Back to chat</button><h3 tabindex="-1" id="knowledge-title">${esc(title)}</h3><p>${esc(subtitle)}</p></div><nav class="knowledge-tabs" aria-label="Assistant resources"><button data-action="knowledge-documents" ${tab === "documents" ? 'aria-current="page"' : ""}>Documents</button><button data-action="knowledge-mcp" ${tab === "mcp" ? 'aria-current="page"' : ""}>Connections</button><button data-action="knowledge-access" ${tab === "access" ? 'aria-current="page"' : ""}>MCP access</button></nav><div id="knowledge-status" role="status" class="knowledge-status"></div>${body}`;
}
function mount(html) {
  visible = true; callbacks.onOpen?.();
  $("assistant-secondary").hidden = false;
  $("assistant-secondary").innerHTML = html;
}
function error(message) { const node = $("knowledge-status"); if (node) { node.textContent = message; node.setAttribute("role", "alert"); } }
function current(ticket) { return visible && ticket === epoch; }
function loading(title, tab) { mount(layout(title, "Loading your private workspace…", '<div class="knowledge-loading" aria-busy="true">Loading…</div>', tab)); }
function documentRows() {
  return documents.length ? documents.map(doc => `<article class="knowledge-item"><div class="knowledge-item-heading"><span class="knowledge-icon">${icon("receipt")}</span><div><h4>${esc(doc.title)}</h4><p>${esc(doc.source_type === "mcp" ? "MCP import" : human(doc.source_type || "Document"))} · ${Number(doc.page_count) || 1} page${doc.page_count === 1 ? "" : "s"} · ${esc(when(doc.created_at))}</p></div></div><div class="knowledge-item-actions"><button class="button ${selected.includes(doc.id) ? "selected-source" : ""}" data-action="knowledge-select" data-id="${esc(doc.id)}" aria-pressed="${selected.includes(doc.id)}">${selected.includes(doc.id) ? icon("check") + "Selected" : icon("plus") + "Use in chat"}</button><button class="text-button" data-action="knowledge-view" data-id="${esc(doc.id)}">View</button><button class="text-button subtle-danger" data-action="knowledge-delete" data-id="${esc(doc.id)}" aria-label="Delete ${esc(doc.title)}">Delete</button></div></article>`).join("") : '<div class="knowledge-empty"><h4>Bring your documents into the conversation</h4><p>Add a statement, benefit guide, or financial note. FinPilot will attach the matching excerpts to its answers.</p></div>';
}
export async function showDocuments() {
  const ticket = ++epoch;
  loading("Your document library", "documents");
  try {
    const data = await api("/api/documents");
    if (!current(ticket)) return;
    documents = data.documents || [];
    setSelectedDocuments(selected.filter(id => documents.some(doc => doc.id === id)));
    mount(layout("Your document library", "Choose the documents FinPilot should use in this conversation.", `<form data-knowledge-form="upload" class="knowledge-upload"><label for="knowledge-file">Add a document</label><input id="knowledge-file" name="file" type="file" accept=".pdf,.txt,.md,text/plain,application/pdf,text/markdown" required aria-describedby="knowledge-upload-help"><p id="knowledge-upload-help">PDF, TXT or Markdown · Up to 2 MB · PDFs must contain selectable text.</p><button type="submit" class="button primary">${icon("plus")}Upload document</button><p class="form-result" role="status"></p></form><div class="knowledge-list-head"><span>${documents.length} document${documents.length === 1 ? "" : "s"}</span><span>${selected.length} selected</span></div><div id="knowledge-document-list">${documentRows()}</div><p class="knowledge-footnote">Documents are private to your account. Their contents inform answers; uploading does not change your financial records.</p>`));
  } catch (e) { if (current(ticket)) error(e.message); }
}
export async function showDocument(id, chunkId = "") {
  const ticket = ++epoch;
  loading("Document excerpts", "documents");
  try {
    const data = await api(`/api/documents/${encodeURIComponent(id)}`);
    if (!current(ticket)) return;
    const doc = data.document, chunks = data.chunks || [];
    const highlighted = chunks.find(chunk => chunk.chunk_id === chunkId);
    const ordered = highlighted ? [highlighted, ...chunks.filter(chunk => chunk !== highlighted)] : chunks;
    mount(layout(doc.title, "Original text retained with the source for review.", `<div class="knowledge-source-meta">${esc(human(doc.source_type || "Document"))} · Added ${esc(when(doc.created_at))}</div>${ordered.map((chunk, index) => `<section class="knowledge-excerpt ${chunk.chunk_id === chunkId ? "cited-excerpt" : ""}"><h4>${chunk.chunk_id === chunkId ? "Cited excerpt · " : ""}Page ${esc(chunk.page || 1)} <span>Excerpt ${esc(index + 1)}</span></h4><p>${esc(chunk.text)}</p></section>`).join("") || '<p class="note">No text excerpts are available.</p>'}<button class="button" data-action="knowledge-documents">Return to library</button>`));
  } catch (e) { if (current(ticket)) error(e.message); }
}
export async function showConnections() {
  const ticket = ++epoch;
  loading("Connected sources", "mcp");
  try {
    const data = await api("/api/mcp/connections");
    if (!current(ticket)) return;
    const servers = data.servers || [], connections = data.connections || [];
    const canConnect = servers.length > 0 && data.setup?.ready !== false;
    mount(layout("Connected sources", "Import data from an approved MCP source into your private document library.", `${canConnect ? `<form data-knowledge-form="connect" class="knowledge-form"><label for="mcp-server">Source</label><select id="mcp-server" name="server_id" required>${servers.map(server => `<option value="${esc(server.id)}">${esc(server.name)}</option>`).join("")}</select><label for="mcp-name">Connection name <span>(optional)</span></label><input id="mcp-name" name="name" maxlength="100" placeholder="My financial source"><label for="mcp-token">Access token <span>(if required by this source)</span></label><input id="mcp-token" name="token" type="password" autocomplete="off" maxlength="4000"><button type="submit" class="button primary">Connect source</button><p class="form-result" role="status"></p></form>` : `<div class="knowledge-empty"><h4>${servers.length ? "Source setup is incomplete" : "No external sources configured"}</h4><p>${esc(data.setup?.message || "Your workspace administrator can enable approved MCP servers. Once available, you can connect your account here.")}</p></div>`}<div class="knowledge-list-head"><span>${connections.length} connected source${connections.length === 1 ? "" : "s"}</span></div>${connections.map(connection => `<article class="knowledge-item"><div class="knowledge-item-heading"><span class="knowledge-icon">${icon("bank")}</span><div><h4>${esc(connection.name || connection.server_id)}</h4><p>Connected ${esc(when(connection.created_at))}</p></div></div><div class="knowledge-item-actions"><button class="button" data-action="knowledge-catalog" data-id="${esc(connection.id)}">Browse available data ${icon("arrow")}</button><button class="text-button subtle-danger" data-action="knowledge-disconnect" data-id="${esc(connection.id)}">Disconnect</button></div></article>`).join("")}<p class="knowledge-footnote">You choose what to import. Only approved read operations are available, and imported data retains its source.</p>`, "mcp"));
  } catch (e) { if (current(ticket)) error(e.message); }
}
async function showCatalog(id) {
  const ticket = ++epoch;
  loading("Available source data", "mcp");
  try {
    const data = await api(`/api/mcp/connections/${encodeURIComponent(id)}/catalog`, {timeout:45000});
    if (!current(ticket)) return;
    catalog = data; connectionId = id;
    const tools = data.tools || [], resources = data.resources || [];
    mount(layout("Available source data", "Choose a read operation or resource to import into your library.", `${tools.length ? `<form data-knowledge-form="import-tool" class="knowledge-form"><label for="mcp-tool">Read operation</label><select id="mcp-tool" name="tool_name">${tools.map(tool => `<option value="${esc(tool.name)}">${esc(tool.name)}</option>`).join("")}</select><div id="mcp-tool-help"></div><label for="mcp-arguments">Arguments <span>(JSON object)</span></label><textarea id="mcp-arguments" name="arguments" rows="4" spellcheck="false">{}</textarea><label for="mcp-import-title">Document title <span>(optional)</span></label><input id="mcp-import-title" name="title" maxlength="150"><button type="submit" class="button primary">Import as document</button><p class="form-result" role="status"></p></form>` : '<p class="note">This source has no approved read operations.</p>'}${resources.length ? `<div class="knowledge-list-head">Resources</div>${resources.map(resource => `<article class="knowledge-item"><h4>${esc(resource.name || resource.uri)}</h4><p>${esc(resource.description || "")}</p><button class="button" data-action="knowledge-import-resource" data-uri="${esc(resource.uri)}" data-title="${esc(resource.name || "Imported resource")}">Import resource</button></article>`).join("")}` : ""}`, "mcp"));
    updateToolHelp();
  } catch (e) { if (current(ticket)) error(e.message); }
}
export function updateToolHelp() {
  const tool = catalog?.tools?.find(item => item.name === $("mcp-tool")?.value);
  if ($("mcp-tool-help")) $("mcp-tool-help").innerHTML = tool ? `<p class="knowledge-footnote">${esc(tool.description || "")}</p><details class="evidence"><summary>Argument format</summary><pre>${esc(JSON.stringify(tool.inputSchema || {}, null, 2))}</pre></details>` : "";
}
export function buildMcpClientConfig({apiUrl, token, pythonPath, projectPath, module = "finpilot.integrations.mcp_server"}) {
  let url;
  try { url = new URL(apiUrl); } catch { throw new Error("The FinPilot server URL is unavailable. Reload this page and try again."); }
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw new Error("Use an HTTP or HTTPS FinPilot server URL without embedded credentials.");
  const absolutePath = value => value.startsWith("/") || /^[A-Za-z]:[\\/]/.test(value) || value.startsWith("\\\\");
  const python = String(pythonPath || "").trim(), project = String(projectPath || "").trim();
  if (!absolutePath(python)) throw new Error("Enter the absolute Python executable path on the computer running your MCP client.");
  if (!absolutePath(project)) throw new Error("Enter the absolute FinPilot project folder on the computer running your MCP client.");
  if ([python, project].some(value => /[\r\n\0]/.test(value))) throw new Error("Enter each path on a single line.");
  if (!token) throw new Error("This access token is no longer available. Create new read access.");
  return {mcpServers:{finpilot:{command:python, args:["-m",module], env:{PYTHONPATH:project, FINPILOT_API_URL:url.origin, FINPILOT_MCP_TOKEN:token}}}};
}
function renderCreatedToken(data) {
  const apiUrl = data.bridge?.api_url || globalThis.location?.origin || "";
  return `<section class="knowledge-token"><h4>Set up your MCP client</h4><p>This token is shown once. Keep the token and generated configuration private.</p><label for="mcp-created-token">Access token</label><textarea id="mcp-created-token" rows="3" readonly spellcheck="false">${esc(data.token)}</textarea><p>Use the FinPilot project and its installed Python dependencies on the computer running your MCP client. Enter the paths on that computer.</p><div class="knowledge-bridge-fields"><label for="mcp-python-path">Python executable</label><input id="mcp-python-path" placeholder="C:\\FinPilot\\.venv\\Scripts\\python.exe" autocomplete="off" spellcheck="false" aria-describedby="mcp-path-help"><label for="mcp-project-path">FinPilot project folder</label><input id="mcp-project-path" placeholder="C:\\FinPilot" autocomplete="off" spellcheck="false" aria-describedby="mcp-path-help"><p id="mcp-path-help">Use absolute paths without surrounding quotes. On macOS or Linux, use paths such as /home/you/finpilot/.venv/bin/python and /home/you/finpilot.</p><label for="mcp-api-url">FinPilot server</label><input id="mcp-api-url" value="${esc(apiUrl)}" readonly><input id="mcp-bridge-module" type="hidden" value="${esc(data.bridge?.module || "finpilot.integrations.mcp_server")}"></div><button class="button" data-action="knowledge-build-config">Create client configuration</button><div id="mcp-config-output" hidden><label for="mcp-client-config">MCP client JSON configuration</label><textarea id="mcp-client-config" rows="12" readonly spellcheck="false"></textarea><button class="button" data-action="knowledge-copy-config">Copy configuration</button><p>Merge the finpilot entry into your client's MCP server settings, then restart the client. Client configuration formats may vary.</p></div><p id="mcp-config-status" role="status"></p><button class="text-button" data-action="knowledge-access">I have saved my configuration</button></section>`;
}
function createClientConfig() {
  let config;
  try {
    config = buildMcpClientConfig({apiUrl:$("mcp-api-url")?.value, token:$("mcp-created-token")?.value, pythonPath:$("mcp-python-path")?.value, projectPath:$("mcp-project-path")?.value, module:$("mcp-bridge-module")?.value || undefined});
  } catch (e) {
    $("mcp-client-config").value = "";
    $("mcp-config-output").hidden = true;
    $("mcp-config-status").setAttribute("role", "alert");
    $("mcp-config-status").textContent = e.message;
    return null;
  }
  $("mcp-config-status").setAttribute("role", "status");
  const text = JSON.stringify(config, null, 2);
  $("mcp-client-config").value = text;
  $("mcp-config-output").hidden = false;
  $("mcp-config-status").textContent = "Configuration ready. It includes your private access token.";
  return text;
}
export async function showAccess() {
  const ticket = ++epoch;
  loading("FinPilot MCP access", "access");
  try {
    const data = await api("/api/mcp/tokens");
    if (!current(ticket)) return;
    mount(layout("FinPilot MCP access", "Give a compatible MCP client read access to your financial workspace.", `<form data-knowledge-form="grant" class="knowledge-form"><label for="mcp-grant-name">Client name</label><input id="mcp-grant-name" name="name" maxlength="100" placeholder="My local AI client" required><label for="mcp-grant-expiry">Access expires after</label><select id="mcp-grant-expiry" name="expires_days"><option value="7">7 days</option><option value="14">14 days</option><option value="30" selected>30 days</option></select><button type="submit" class="button primary">Create read access</button><p class="form-result" role="status"></p></form><div id="mcp-new-token"></div>${(data.tokens || []).map(token => `<article class="knowledge-item"><div class="knowledge-item-heading"><span class="knowledge-icon">${icon("shield")}</span><div><h4>${esc(token.name)}</h4><p>Expires ${esc(when(token.expires_at))}</p></div></div><button class="text-button subtle-danger" data-action="knowledge-revoke" data-id="${esc(token.id)}">Revoke access</button></article>`).join("")}<p class="knowledge-footnote">Access is limited to your workspace. Tokens cannot create payments or change your financial records. Revoke them here at any time.</p>`, "access"));
  } catch (e) { if (current(ticket)) error(e.message); }
}
async function upload(file) {
  if (!file || !file.size) throw new Error("Choose a non-empty PDF, TXT, or Markdown file.");
  if (file.size > 2_000_000) throw new Error("Choose a document smaller than 2 MB.");
  const user = S.session?.user?.id;
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 45000);
  try {
    const body = new FormData(); body.append("file", file);
    const response = await fetch("/api/documents", {method:"POST", body, credentials:"same-origin", headers:{"X-CSRF-Token":S.session?.csrf_token || ""}, signal:abort.signal});
    const data = await response.json();
    if (user !== S.session?.user?.id) throw new Error("Your session changed. Please try again.");
    if (response.status === 401 && S.session) document.dispatchEvent(new Event("finpilot-session-expired"));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The document could not be uploaded.");
    return data;
  } finally { clearTimeout(timer); }
}
function useImported(data) {
  if (data.document?.id && !selected.includes(data.document.id)) setSelectedDocuments([...selected, data.document.id]);
}
export async function handleKnowledgeSubmit(form) {
  const kind = form.dataset.knowledgeForm;
  if (!kind) return false;
  const ticket = epoch, button = form.querySelector('button[type="submit"]'), result = form.querySelector(".form-result");
  if (button.disabled) return true;
  button.disabled = true; result.textContent = "Working…";
  try {
    const values = new FormData(form);
    if (kind === "upload") {
      const data = await upload(values.get("file"));
      if (!current(ticket)) return true;
      useImported(data); const next = epoch + 1; await showDocuments();
      if (current(next) && $("knowledge-status")) $("knowledge-status").textContent = data.duplicate ? "This document is already in your library." : "Document uploaded and selected for this conversation.";
    } else if (kind === "connect") {
      await api("/api/mcp/connections", {method:"POST", body:{server_id:values.get("server_id"), ...(values.get("name") ? {name:values.get("name")} : {}), ...(values.get("token") ? {token:values.get("token")} : {})}, timeout:45000});
      if (!current(ticket)) return true;
      form.reset(); await showConnections();
    } else if (kind === "import-tool") {
      let args;
      try { args = JSON.parse(values.get("arguments") || "{}"); } catch { throw new Error("Enter valid JSON for the read operation arguments."); }
      if (!args || Array.isArray(args) || typeof args !== "object") throw new Error("Arguments must be a JSON object.");
      const data = await api(`/api/mcp/connections/${encodeURIComponent(connectionId)}/import`, {method:"POST",body:{tool_name:values.get("tool_name"), arguments:args, ...(values.get("title") ? {title:values.get("title")} : {})},timeout:45000});
      if (!current(ticket)) return true;
      useImported(data); const next = epoch + 1; await showDocuments();
      if (current(next)) $("knowledge-status").textContent = "Source imported and selected for this conversation.";
    } else if (kind === "grant") {
      const data = await api("/api/mcp/tokens", {method:"POST",body:{name:values.get("name"),expires_days:Number(values.get("expires_days"))}});
      if (!current(ticket)) return true;
      // Keep the one-time secret visible even if a subsequent token-list request fails.
      form.reset(); form.hidden = true;
      $("mcp-new-token").innerHTML = renderCreatedToken(data);
      $("mcp-new-token").scrollIntoView?.({block:"nearest"});
    }
  } catch (e) { if (current(ticket) && form.isConnected !== false) { result.textContent = e.message; result.setAttribute("role", "alert"); } }
  finally { if (button.isConnected !== false) button.disabled = false; }
  return true;
}
export async function handleKnowledgeAction(el) {
  const action = el.dataset.action, id = el.dataset.id;
  if (!action?.startsWith("knowledge-")) return false;
  if (el.disabled) return true;
  el.disabled = true;
  const ticket = epoch;
  try {
    if (action === "knowledge-documents") await showDocuments();
    else if (action === "knowledge-mcp") await showConnections();
    else if (action === "knowledge-access") await showAccess();
    else if (action === "knowledge-build-config") createClientConfig();
    else if (action === "knowledge-copy-config") {
      const text = createClientConfig();
      if (text === null) return true;
      try {
        if (!globalThis.navigator?.clipboard?.writeText) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(text);
        if (current(ticket)) $("mcp-config-status").textContent = "Configuration copied. Keep it private and paste it into your MCP client settings.";
      } catch {
        if (current(ticket)) {
          $("mcp-client-config").focus(); $("mcp-client-config").select();
          $("mcp-config-status").textContent = "Clipboard access is unavailable. The configuration is selected; copy it manually.";
        }
      }
    }
    else if (action === "knowledge-catalog") await showCatalog(id);
    else if (action === "knowledge-view") await showDocument(id, el.dataset.chunk || "");
    else if (action === "knowledge-select") {
      if (!selected.includes(id) && selected.length >= MAX_SELECTED) throw new Error(`Choose up to ${MAX_SELECTED} documents per conversation.`);
      setSelectedDocuments(selected.includes(id) ? selected.filter(value => value !== id) : [...selected, id]);
      await showDocuments();
    } else if (action === "knowledge-delete") {
      await api(`/api/documents/${encodeURIComponent(id)}`, {method:"DELETE"});
      if (!current(ticket)) return true;
      setSelectedDocuments(selected.filter(value => value !== id)); await showDocuments();
    } else if (action === "knowledge-disconnect") {
      await api(`/api/mcp/connections/${encodeURIComponent(id)}`, {method:"DELETE"});
      if (current(ticket)) await showConnections();
    } else if (action === "knowledge-revoke") {
      await api(`/api/mcp/tokens/${encodeURIComponent(id)}`, {method:"DELETE"});
      if (current(ticket)) await showAccess();
    } else if (action === "knowledge-import-resource") {
      const data = await api(`/api/mcp/connections/${encodeURIComponent(connectionId)}/import`, {method:"POST",body:{resource_uri:el.dataset.uri,title:label(el.dataset.title)},timeout:45000});
      if (!current(ticket)) return true;
      useImported(data); await showDocuments();
    }
  } catch (e) { if (current(ticket)) error(e.message); }
  finally { if (el.isConnected !== false) el.disabled = false; }
  return true;
}
