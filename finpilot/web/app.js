import { initializeBankLinking, mountBankConnections, handleBankAction } from "./connect.js";
import { initializeExecution, handleExecutionClick, handleExecutionSubmit } from "./execution.js";
import { initializeAuth, signOut } from "./auth.js";
import { initializeManagement, handleManagementClick, handleManagementSubmit } from "./manage.js";
import {
  S,
  $,
  sections,
  esc,
  val,
  money,
  api,
  icon,
  account,
  accountType,
  heading,
  empty,
  action,
  reviews,
  fullDate,
} from "./core.js";
import {
  pages,
  accountResults,
  paymentActivity,
  debtComparison,
} from "./pages.js";
import { accountPage } from "./accounts.js";
import { detailContent, calculationResult } from "./details.js";
import {
  initializeAssistant,
  updateAssistantContext,
  openAssistant,
  closeAssistant,
  resetAssistant,
  ask,
  showHistory,
} from "./assistant.js";
let loadSequence = 0,
  toastTimer,
  detailKey = "",
  detailExtra = {};
function fillIcons() {
  document
    .querySelectorAll("[data-icon]")
    .forEach((el) => (el.innerHTML = icon(el.dataset.icon)));
}
export async function load({ initial = false } = {}) {
  const seq = ++loadSequence;
  $("main").setAttribute("aria-busy", "true");
  try {
    const payload = await api("/api/bootstrap");
    const { dashboard: data, workspace } = payload;
    if (seq !== loadSequence) return;
    S.revision = payload.revision;
    S.data = data;
    S.workspace = workspace;
    S.capabilities = payload.capabilities || {};
    const sandbox = !!workspace.household.payment_sandbox;
    document.querySelector('.prototype-label').textContent = sandbox ? 'Sample workspace' : 'Private workspace';
    document.querySelector('footer span:last-child').textContent = sandbox ? 'FinPilot · Sample data · Simulated payments' : 'FinPilot · Financial planning';
    S.cache.clear();
    S.forecastDays = 45;
    $("household-name").textContent = data.household;
    $("household-avatar").textContent = data.household
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase();
    $("footer-status").textContent =
      `${data.household} · Data as of ${fullDate(data.as_of)}`;
    render();
  } catch (e) {
    if (seq !== loadSequence) return;
    if (initial || !S.data)
      $("main").innerHTML =
        heading(
          "Unable to load your workspace",
          "Your financial data could not be retrieved.",
        ) +
        empty("Connection unavailable", e.message) +
        action("Try again", "refresh", true);
    else
      toast(
        "Refresh failed. Previously loaded data remains visible. " + e.message,
        true,
      );
  } finally {
    if (seq === loadSequence) $("main").setAttribute("aria-busy", "false");
  }
}
function parseRoute() {
  let parts;
  try {
    parts = decodeURIComponent(location.hash.slice(1)).split("/");
  } catch {
    parts = ["overview"];
  }
  if (parts[0] === "main") {
    document.querySelector("main").focus();
    return;
  }
  S.page = sections.some((s) => s[0] === parts[0]) ? parts[0] : "overview";
  S.accountId = S.page === "accounts" ? parts[1] || null : null;
  S.tab = ["summary", "activity", "analytics", "details"].includes(parts[2])
    ? parts[2]
    : "summary";
  if (S.page === "bills")
    S.billTab = ["upcoming", "calendar", "activity"].includes(parts[1])
      ? parts[1]
      : "upcoming";
}
function render() {
  const focusedTab =
    document.activeElement?.getAttribute("role") === "tab"
      ? { ...document.activeElement.dataset }
      : null;
  parseRoute();
  if (!S.data) return;
  $("navigation").innerHTML = sections
    .map(
      ([id, label, ico], i) =>
        `${i === 7 ? '<span class="nav-separator"></span>' : ""}<a href="#${id}" ${id === S.page ? 'class="active" aria-current="page"' : ""}>${icon(ico)}<span>${{ paychecks: "Paychecks", bills: "Payments", goals: "Goals", debt: "Credit & loans", rules: "Rules" }[id] || label}</span>${id === "accounts" && S.data.connections.unhealthy_count ? `<span class="nav-count">${S.data.connections.unhealthy_count}</span>` : ""}</a>`,
    )
    .join("");
  const title = sections.find((s) => s[0] === S.page)?.[1] || "Overview";
  $("breadcrumb").textContent = S.accountId
    ? `Accounts / ${account(S.accountId)?.nickname || "Unavailable"}`
    : title;
  document.title = `${S.accountId ? account(S.accountId)?.nickname || "Account" : title} — FinPilot`;
  $("main").dataset.page = S.page;
  $("main").innerHTML = S.accountId ? accountPage() : pages[S.page]();
  document.querySelector(".notification i").hidden = reviews().length === 0;
  fillIcons();
  updateAssistantContext();
  if (focusedTab) {
    [...document.querySelectorAll('[role="tab"]')]
      .find((el) =>
        Object.entries(focusedTab).every(
          ([key, value]) => el.dataset[key] === value,
        ),
      )
      ?.focus();
  }
  if (
    S.page === "bills" &&
    S.billTab === "activity" &&
    !S.cache.has("execution")
  )
    loadActivity();
}
async function loadActivity() {
  try {
    const result = await api("/api/execution/groups");
    S.cache.set("execution", result);
    if (S.page === "bills" && S.billTab === "activity") render();
  } catch (e) {
    toast(e.message, true);
    if (S.page === "bills" && S.billTab === "activity") {
      $("main").insertAdjacentHTML(
        "beforeend",
        empty("Could not load activity", e.message),
      );
    }
  }
}
function openDetail(key, extra = {}) {
  if (!S.data) return;
  closeAssistant();
  detailKey = key;
  detailExtra = extra;
  $("detail-body").innerHTML = detailContent(key, extra);
  fillIcons();
  if (!$("detail-dialog").open) $("detail-dialog").showModal();
  else $("detail-dialog").scrollTop = 0;
  if (key === "connections") mountBankConnections();
}
function closeDetail() {
  if ($("detail-dialog").open) $("detail-dialog").close();
}
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  toastTimer = setTimeout(() => ($("toast").hidden = true), 6000);
}
function syncMenu() {
  const narrow = window.matchMedia("(max-width:760px)").matches;
  const nav = document.querySelector(".sidebar");
  if (!narrow) nav.classList.remove("open");
  // The approved navigation is an in-flow tab strip, including on mobile.
  nav.inert = false;
  document.querySelector(".app-shell").inert = false;
  document
    .querySelector('[data-action="menu"]')
    .setAttribute(
      "aria-expanded",
      String(narrow && nav.classList.contains("open")),
    );
}
function closeMenu() {
  document.querySelector(".sidebar").classList.remove("open");
  syncMenu();
}
async function busy(button, fn) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  try {
    await fn();
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}
async function handleAction(el) {
  const name = el.dataset.action,
    id = el.dataset.id;
  if (name?.startsWith("bank-")) return handleBankAction(el);
  if (name === "logout") return signOut();
  if (name === "assistant") return openAssistant();
  if (name === "close-assistant") return closeAssistant();
  if (name === "chat-history") return showHistory();
  if (name === "new-chat") return resetAssistant();
  if (name === "close-detail") return closeDetail();
  if (name === "menu") {
    const isOpen = document.querySelector(".sidebar").classList.toggle("open");
    el.setAttribute("aria-expanded", String(isOpen));
    syncMenu();
    if (isOpen) document.querySelector("#navigation a")?.focus();
    return;
  }
  if (name === "close-menu") return closeMenu();
  if (name === "refresh") return busy(el, () => load({ initial: !S.data }));
  if (name === "theme") {
    const theme =
      document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("finpilot-theme", theme);
    } catch {}
    openDetail("settings");
    return;
  }
  return busy(el, async () => {
    if (name === "pause-rule") {
      await api(
        `/api/policies/${encodeURIComponent(id)}/pause?paused=${el.dataset.paused}`,
        { method: "POST" },
      );
      closeDetail();
      await load();
      toast(
        el.dataset.paused === "true"
          ? "Rule paused. Future allocations updated."
          : "Rule resumed. Future allocations updated.",
      );
    } else if (name === "skip-rule") {
      await api(`/api/policies/${encodeURIComponent(id)}/skip-next`, {
        method: "POST",
      });
      closeDetail();
      await load();
      toast("Next occurrence skipped. Your plan has been updated.");
    } else if (name === "pause-all") {
      await api("/api/execution/pause-all", { method: "POST" });
      closeDetail();
      await load();
      toast("All future execution is paused.");
    } else if (name === "draft-bill") {
      const result = await api(
        `/api/bills/${encodeURIComponent(id)}/pay-once${el.dataset.date ? "?occurrence_date=" + encodeURIComponent(el.dataset.date) : ""}`,
        { method: "POST" },
      );
      S.cache.set(`draft:${id}:${el.dataset.date || ""}`, result);
      S.cache.delete("execution");
      if ($("detail-dialog").open) openDetail(`bill:${id}`, detailExtra);
      toast(
        result.preflight?.ok
          ? "Draft prepared for review. No payment sent."
          : "Draft blocked. Review the displayed checks.",
      );
    }
  });
}
document.addEventListener("click", async (e) => {
  const el = e.target.closest("button,a,[data-detail]");
  if (!el) return;
  if (el.dataset.execution) {e.preventDefault(); await handleExecutionClick(el); return;}
  if (el.dataset.manage) {e.preventDefault(); await handleManagementClick(el); return;}
  if (el.dataset.detail) {
    e.preventDefault();
    openDetail(el.dataset.detail, { date: el.dataset.date });
  } else if (el.dataset.ask) {
    e.preventDefault();
    ask(el.dataset.ask);
  } else if (el.dataset.action) {
    e.preventDefault();
    handleAction(el);
  } else if (el.dataset.filter) {
    S.accountFilter = el.dataset.filter;
    render();
    document.querySelector(`[data-filter="${S.accountFilter}"]`)?.focus();
  } else if (el.dataset.payPeriod) {
    S.payPeriod = el.dataset.payPeriod;
    render();
  } else if (el.dataset.accountTab) {
    location.hash = `accounts/${S.accountId}/${el.dataset.accountTab}`;
  } else if (el.dataset.billTab) {
    location.hash = `bills/${el.dataset.billTab}`;
  } else if (el.dataset.range) {
    busy(el, async () => {
      const days = Number(el.dataset.range);
      const result = await api(`/api/forecast?days=${days}`);
      S.forecastDays = days;
      S.cache.set("forecast", result);
      if (S.page === "cashflow") render();
    });
  } else if (el.tagName === "A" && el.getAttribute("href")?.startsWith("#")) {
    closeDetail();
    closeAssistant();
    closeMenu();
  }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "account-search") {
    S.query = e.target.value;
    const list = S.workspace.accounts.filter(
      (a) =>
        (S.accountFilter === "All accounts" ||
          S.accountFilter === "Platforms" ||
          accountType(a) === S.accountFilter) &&
        `${a.nickname} ${a.institution} ${a.mask}`
          .toLowerCase()
          .includes(S.query.toLowerCase()),
    );
    $("account-results").innerHTML = accountResults(list);
  }
});
document.addEventListener("submit", async (e) => {
  const form = e.target;
  if (["chat-form", "auth-form"].includes(form.id)) return;
  e.preventDefault();
  if (form.dataset.executionForm) {await handleExecutionSubmit(form); return;}
  if (await handleManagementSubmit(form)) return;
  const values = Object.fromEntries(new FormData(form));
  const button = form.querySelector('button[type="submit"],button:not([type])');
  await busy(button, async () => {
    try {
      if (form.id === "debt-form") {
        const r = await api(
          `/api/debt/compare?extra_payment=${encodeURIComponent(values.extra)}`,
        );
        S.cache.set("debt", r);
        if ($("debt-result")) $("debt-result").innerHTML = debtComparison(r);
      } else if (form.id === "tax-form") {
        const r = await api(
          `/api/tax/net-benefit?amount=${encodeURIComponent(values.amount)}&horizon_days=${encodeURIComponent(values.days)}`,
        );
        if ($("tax-result")) $("tax-result").innerHTML = calculationResult(r);
      } else if (form.id === "utilization-form") {
        const r = await api(
          `/api/cards/utilization?card_id=${encodeURIComponent(values.card_id)}&payment=${encodeURIComponent(values.payment)}`,
        );
        if ($("utilization-result"))
          $("utilization-result").innerHTML = calculationResult(r);
      } else if (form.id === "card-form") {
        const r = await api("/api/cards/choose", {
          method: "POST",
          body: { amount: Number(values.amount), category: values.category },
        });
        if ($("card-result")) $("card-result").innerHTML = calculationResult(r);
      } else if (form.id === "mortgage-form") {
        const r = await api(
          `/api/mortgage/scenarios?extra_monthly=${encodeURIComponent(values.extra)}&lump_sum=${encodeURIComponent(values.lump)}`,
        );
        if ($("mortgage-result"))
          $("mortgage-result").innerHTML = calculationResult(r);
      } else if (form.id === "policy-form") {
        const r = await api("/api/policies", {
          method: "POST",
          body: {
            name: values.name.trim(),
            amount: Number(values.amount),
            purpose: values.purpose,
            method: "fixed",
            destination_account_id: values.destination,
            priority: Number(values.priority),
          },
        });
        closeDetail();
        await load();
        location.hash = "rules";
        toast(`${r.name} saved without payment authority.`);
      } else if (form.id === "model-form") {
        const r = await api("/api/llm/configure", {
          method: "POST",
          body: {
            base_url: values.base_url,
            model: values.model,
            temperature: 0.1,
          },
          timeout: 45000,
        });
        await load();
        if (form.isConnected)
          form.querySelector(".form-result").textContent = r.reachable
            ? "Local model connected."
            : "Configuration saved. The model is not reachable yet.";
      }
    } catch (err) {
      const error =
        form.querySelector(".form-result") || document.createElement("p");
      error.className = "form-result error";
      error.setAttribute("role", "alert");
      error.textContent = err.message;
      if (!error.isConnected) form.append(error);
      throw err;
    }
  });
});
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (S.data) openAssistant();
  }
  if (e.key === "Escape") closeMenu();
  if (
    (e.key === "Enter" || e.key === " ") &&
    e.target.matches(".chart-point")
  ) {
    e.preventDefault();
    openDetail(e.target.dataset.detail);
  }
  if (
    ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key) &&
    e.target.matches('[role="tab"]')
  ) {
    const tabs = [
        ...e.target
          .closest('[role="tablist"]')
          .querySelectorAll('[role="tab"]'),
      ],
      index = tabs.indexOf(e.target),
      next =
        e.key === "Home"
          ? 0
          : e.key === "End"
            ? tabs.length - 1
            : (index + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) %
              tabs.length;
    e.preventDefault();
    tabs[next].focus();
    tabs[next].click();
  }
});
window.addEventListener("hashchange", () => {
  const previousPage = S.page;
  const previousAccountId = S.accountId;
  closeDetail();
  closeAssistant();
  closeMenu();
  render();
  if (S.page !== previousPage || S.accountId !== previousAccountId)
    $("main").focus({ preventScroll: true });
  window.scrollTo(0, 0);
});
try {
  const theme = localStorage.getItem("finpilot-theme");
  if (["light", "dark"].includes(theme))
    document.documentElement.dataset.theme = theme;
} catch {}
window.matchMedia("(max-width:760px)").addEventListener("change", syncMenu);
let assistantReady = false;
initializeManagement({load, toast});
initializeExecution({load, toast});
fillIcons();
syncMenu();
document.addEventListener("finpilot-data-changed", () => load());
initializeAuth({
  onError: (message) => toast(message, true),
  onAuthenticated: async () => {
    if (!assistantReady) {initializeAssistant(); assistantReady = true;}
    await load({initial: true});
    await initializeBankLinking({reload: () => load(), toast});
  },
  onSignedOut: () => {
    initializeBankLinking({reload: () => load(), toast});
    ++loadSequence;
    closeDetail(); closeAssistant();
    if (assistantReady) resetAssistant();
    S.data = null; S.workspace = null; S.revision = null; S.cache.clear();
    S.accountId = null; S.query = ""; S.accountFilter = "All accounts";
    $("main").innerHTML = ""; $("navigation").innerHTML = "";
    $("detail-body").innerHTML = "";
    $("chat-log")?.replaceChildren();
  }
});
