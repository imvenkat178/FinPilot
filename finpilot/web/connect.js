import { S, api, esc, action } from "./core.js";

const SDK = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";
const STORAGE_KEY = "finpilot-bank-link";
let sdkPromise, activeLink, resumeStarted = false;
let hooks = { reload: async () => {}, toast: () => {} };
const userId = () => S.session?.user?.id;
const currentHost = () => document.getElementById("bank-connections");

function saveLink(value) {
  try {
    if (value) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch { /* Link still works without redirect if storage is unavailable. */ }
}
function readLink() {
  try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null"); }
  catch { return null; }
}
function clearRedirect() {
  const url = new URL(location.href);
  url.searchParams.delete("oauth_state_id");
  history.replaceState(null, "", url.pathname + url.search + url.hash);
}
function finishLink() {
  saveLink(null);
  clearRedirect();
  activeLink?.destroy();
  activeLink = null;
}
function loadSDK() {
  if (window.Plaid) return Promise.resolve(window.Plaid);
  if (sdkPromise) return sdkPromise;
  sdkPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    const timer = setTimeout(() => fail(), 15000);
    function fail() {
      clearTimeout(timer);
      script.remove();
      sdkPromise = null;
      reject(new Error("The secure bank sign-in could not load. Please try again."));
    }
    script.src = SDK;
    script.async = true;
    script.onload = () => {
      clearTimeout(timer);
      if (window.Plaid) resolve(window.Plaid);
      else fail();
    };
    script.onerror = fail;
    document.head.appendChild(script);
  });
  return sdkPromise;
}

async function launchLink(record, receivedRedirectUri) {
  const id = userId();
  if (!id || record.userId !== id) throw new Error("Your session changed. Start bank linking again.");
  const plaid = await loadSDK();
  if (id !== userId()) return;
  if (activeLink) activeLink.destroy();
  // A native modal would make the provider's iframe inert.
  document.getElementById("detail-dialog")?.close();
  activeLink = plaid.create({
    token: record.token,
    ...(receivedRedirectUri ? { receivedRedirectUri } : {}),
    onSuccess: async (publicToken) => {
      finishLink();
      if (id !== userId()) return;
      hooks.toast("Saving your bank connection…");
      try {
        const result = await api("/api/bank/exchange", {
          method: "POST", body: { public_token: publicToken }, timeout: 50000,
        });
        await hooks.reload();
        hooks.toast(result.connection.account_ids.length
          ? "Bank connected. Review its cached balances and activity in Accounts."
          : "Bank connected, but no supported account has complete balances yet. Review Connections.");
        await mountBankConnections();
      } catch (error) {
        if (id === userId()) hooks.toast(error.message + " Review Connections before linking again.", true);
      }
    },
    onExit: (error) => {
      finishLink();
      if (id === userId() && error) hooks.toast("Bank sign-in did not finish. Please try again.", true);
    },
  });
  activeLink.open();
}

export async function initializeBankLinking(options = {}) {
  hooks = { ...hooks, ...options };
  if (!userId()) {
    finishLink();
    resumeStarted = false;
    return;
  }
  const hasRedirect = new URL(location.href).searchParams.has("oauth_state_id");
  if (!hasRedirect || resumeStarted) return;
  resumeStarted = true;
  const record = readLink();
  if (!record || record.userId !== userId() || !record.token || !Number.isFinite(Date.parse(record.expires)) || Date.parse(record.expires) <= Date.now()) {
    finishLink();
    hooks.toast("Bank sign-in expired. Open Connections to start again.", true);
    return;
  }
  try {
    const state = await api("/api/bank/connections");
    if (!state.provider.configured) throw new Error(state.provider.reason);
    await launchLink(record, location.href);
  } catch (error) {
    finishLink();
    hooks.toast(error.message, true);
  }
}

export async function mountBankConnections(container = currentHost()) {
  if (!container?.isConnected || !userId()) return;
  const id = userId();
  container.setAttribute("aria-busy", "true");
  container.innerHTML = '<p class="muted" role="status">Loading bank connections…</p>';
  try {
    const data = await api("/api/bank/connections");
    if (!container.isConnected || id !== userId()) return;
    const canManage = ["owner", "approver"].includes(S.session?.role);
    const available = data.provider.configured && canManage;
    container.innerHTML = `<div class="detail-section"><h3>Connected banks</h3>
      <p class="muted">Read-only bank, credit card and loan accounts. Balances are cached snapshots, and card and loan terms appear when your bank shares them.</p>
      ${data.provider.environment === "sandbox" ? '<p class="muted">Plaid Sandbox: test institutions and test data.</p>' : ""}
      ${!data.provider.configured ? `<p role="status">${esc(data.provider.reason)}</p>` : ""}
      ${available ? action("Connect a bank", "bank-link", true) : ""}
      ${data.connections.map(row => `<div class="bank-connection" data-bank-id="${esc(row.id)}">
        <div class="item-row"><span class="row-body"><strong class="row-title">${esc(row.institution)}</strong>
        <span class="row-sub">${row.account_ids.length} account${row.account_ids.length === 1 ? "" : "s"} · ${row.status === "active" ? "Connected" : "Disconnected"}</span>
        <span class="row-sub">${row.last_synced_at ? "Last fetched " + esc(new Date(row.last_synced_at).toLocaleString()) : "Not synced yet"}</span></span></div>
        ${row.notices.map(note => `<p class="muted">${esc(note)}</p>`).join("")}
        ${row.status === "active" && available ? `<div class="detail-actions">${action("Sync now", "bank-sync", false, `data-id="${esc(row.id)}"`)} ${action("Disconnect", "bank-disconnect", false, `data-id="${esc(row.id)}"`)}</div>` : ""}
        <div class="bank-confirmation"></div></div>`).join("")}
      ${!data.connections.length && data.provider.configured ? '<p class="muted">No bank connections yet.</p>' : ""}</div>`;
  } catch (error) {
    if (container.isConnected && id === userId()) container.innerHTML = `<p role="alert">${esc(error.message)}</p>${action("Try again", "bank-reload")}`;
  } finally { container.removeAttribute("aria-busy"); }
}

export async function handleBankAction(el) {
  const name = el.dataset.action;
  if (!name?.startsWith("bank-")) return false;
  if (el.disabled) return true;
  if (name === "bank-reload") { await mountBankConnections(); return true; }
  if (name === "bank-cancel-disconnect") {
    el.closest(".bank-confirmation").replaceChildren();
    return true;
  }
  if (name === "bank-disconnect") {
    const box = el.closest(".bank-connection").querySelector(".bank-confirmation");
    box.innerHTML = `<p role="status">Disconnect this bank? Syncing stops, and saved balances and transactions remain in your workspace.</p>
      ${action("Confirm disconnect", "bank-confirm-disconnect", false, `data-id="${esc(el.dataset.id)}"`)} ${action("Keep connected", "bank-cancel-disconnect")}`;
    box.querySelector("button")?.focus();
    return true;
  }
  el.disabled = true;
  const id = userId();
  try {
    if (name === "bank-link") {
      const state = await api("/api/bank/connections");
      if (!state.provider.configured) throw new Error(state.provider.reason);
      const data = await api("/api/bank/link-token", { method: "POST" });
      const record = { token: data.link_token, userId: id,
        expires: data.expiration || new Date(Date.now() + 15 * 60 * 1000).toISOString() };
      saveLink(record);
      await launchLink(record);
    } else if (name === "bank-sync" || name === "bank-confirm-disconnect") {
      const sync = name === "bank-sync";
      await api(`/api/bank/${sync ? "sync" : "connections"}/${encodeURIComponent(el.dataset.id)}`,
        { method: sync ? "POST" : "DELETE", timeout: 50000 });
      await hooks.reload();
      hooks.toast(sync ? "Bank data synced. Balances reflect the bank's cached snapshot." : "Bank disconnected. Your saved records are retained.");
      await mountBankConnections();
    }
  } catch (error) {
    if (id === userId()) hooks.toast(error.message, true);
  } finally { if (el.isConnected) el.disabled = false; }
  return true;
}
