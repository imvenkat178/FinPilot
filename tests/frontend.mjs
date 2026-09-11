/** Render-contract checks against a running app's reference data. Run with node tests/frontend.mjs. */
import assert from "node:assert/strict";
import {
  S,
  sections,
  access,
  accountType,
  money,
} from "../finpilot/web/core.js";
import { pages, paymentActivity, rulesPage } from "../finpilot/web/pages.js";
import { accountPage } from "../finpilot/web/accounts.js";
import { detailContent, calculationResult } from "../finpilot/web/details.js";
const origin = process.env.FINPILOT_TEST_URL || "http://127.0.0.1:8100";
const identityResponse = await fetch(origin + "/api/auth/register", {method:"POST",
  headers:{"Content-Type":"application/json"},body:JSON.stringify({name:"Render contract QA",
    email:`render-${crypto.randomUUID()}@example.com`,password:"local-render-contract-tests",sample_data:true})});
assert.equal(identityResponse.status,201);
const cookie=identityResponse.headers.get("set-cookie").split(";")[0];
S.session=await identityResponse.json();
const get = async (path) => {
  const r = await fetch(origin + path,{headers:{Cookie:cookie}});
  assert.equal(r.status, 200, path);
  return r.json();
};
[S.data, S.workspace] = await Promise.all([
  get("/api/dashboard"),
  get("/api/workspace"),
]);
globalThis.document = { documentElement: { dataset: { theme: "light" } } };
const targets = new Set([
  "cash",
  "networth",
  "debt-total",
  "allowance",
  "reserves",
  "alerts",
  "connections",
  "month",
  "plan-logic",
  "bills-total",
  "forecast-assumptions",
  "settings",
  "new-rule",
  "pause-all",
  "payment-activity",
  "assumptions",
]);
let renders = 0;
const inspect = (html) => {
  assert.ok(html.length > 100);
  assert.ok(!html.includes("undefined"), "No missing fields");
  assert.ok(!html.includes("NaN"), "No invalid calculations");
  for (const m of html.matchAll(/data-detail="([^"]+)"/g)) targets.add(m[1]);
  renders++;
};
for (const [page] of sections) {
  S.page = page;
  S.accountId = null;
  inspect(pages[page]());
}
for (const a of S.workspace.accounts) {
  for (const tab of ["summary", "activity", "analytics", "details"]) {
    S.page = "accounts";
    S.accountId = a.id;
    S.tab = tab;
    const html = accountPage();
    inspect(html);
    assert.ok(html.includes(a.nickname.replaceAll("&", "&amp;")));
  }
  if (["Loans", "Credit cards"].includes(accountType(a)))
    assert.ok(!access(a).includes("Available"));
}
S.page = "overview";
S.accountId = null;
for (const key of targets) {
  const html = detailContent(key);
  assert.ok(html.includes('id="detail-title"'), key);
  assert.ok(!html.includes("Detail unavailable"), key);
  assert.ok(!html.includes("undefined"), key);
}
S.cache.set("forecast", { ...S.data.forecast, end: "2099-01-01" });
S.page = "overview";
assert.ok(
  !detailContent("forecast-assumptions").includes("2099-01-01"),
  "Overview assumptions retain the displayed forecast horizon",
);
S.page = "cashflow";
assert.ok(
  detailContent("forecast-assumptions").includes("2099-01-01"),
  "Cash flow assumptions use the selected forecast horizon",
);
S.cache.delete("forecast");
S.page = "overview";
const scheduled = S.workspace.bills.find((b) => b.schedule);
assert.ok(
  detailContent("bill:" + scheduled.id, { date: "2099-01-01" }).includes(
    'data-date="2099-01-01"',
  ),
  "Future recurrence draft targets the exact occurrence date",
);
S.data.automation.globally_paused = true;
assert.ok(
  rulesPage().includes("Execution paused"),
  "Global pause reflected in occurrence status",
);
S.data.automation.globally_paused = false;
const destination = S.workspace.accounts[0];
S.cache.set("execution", {
  groups: [
    {
      label: "A test draft",
      group_id: "test-group",
      legs: 1,
      detail: [
        {
          id: "opaque-test-leg",
          destination: destination.id,
          amount: { amount: "1.00", currency: "USD" },
          state: "draft",
        },
      ],
    },
  ],
});
assert.ok(
  paymentActivity().includes(destination.nickname),
  "Activity resolves account identity",
);
const [tax, mortgage] = await Promise.all([
  get("/api/tax/net-benefit?amount=1000"),
  get("/api/mortgage/scenarios?extra_monthly=100&lump_sum=1000"),
]);
assert.ok(calculationResult(tax).includes("<table>"));
assert.ok(
  calculationResult(tax).includes(
    money(tax.baseline.one_year_net_benefit, true),
  ),
);
assert.ok(calculationResult(mortgage).includes("<table>"));
console.log(
  JSON.stringify({
    mainSections: sections.length,
    accountTabViews: S.workspace.accounts.length * 4,
    detailTargets: targets.size,
    renders,
    financialSourceContracts: "pass",
    draftDateGuard: "pass",
    globalPause: "pass",
    calculatorTables: "pass",
  }),
);
