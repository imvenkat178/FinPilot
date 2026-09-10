# FinPilot

A working implementation of the *AI Personal Finance Application* specification
(Version 3) with the Version 4 additions folded in.

The centre of the product is the paycheck-splitting engine: concurrent recurring
policies funded from real income events, with deadlines — not percentages —
deciding what each paycheck must cover. Around it sit the debt, card, liquidity,
tax and coverage calculators, a payment-execution engine with a durable
lifecycle, an interactive money dashboard, and an assistant that runs on a
**local Llama** and is not allowed to produce a number of its own.

---

## Quick start

```bash
pip install fastapi uvicorn pydantic httpx pytest langgraph langchain-core langsmith
./run.sh serve                     # http://127.0.0.1:8099
```

Point it at your local model:

```bash
export FINPILOT_LLM_BASE_URL=http://localhost:11434/v1   # Ollama
export FINPILOT_LLM_MODEL=llama3.2
./run.sh serve
```

Any OpenAI-compatible endpoint works — Ollama (`:11434`), llama.cpp server
(`:8080`), LM Studio (`:1234`), vLLM (`:8000`). With no endpoint set the app
probes those ports; with none reachable it answers from the calculators alone,
correctly, and says so in the header.

```bash
./run.sh test        # 133 tests
./run.sh queries     # the user-query suite, end to end
```

---

## What is in here

```
finpilot/
  money.py        Decimal money with explicit currency; unlike currencies never add
  dates.py        Cadences, day-of-month rules, business days, settlement calendars
  models.py       The section 13 core objects, each carrying its own provenance
  engine/
    allocator.py  Paycheck and monthly allocation          §4, §14   <- the core
    debt.py       Repayment simulator, mortgage, recast     §7, §8, §18
    ledger.py     Dated cash ledger, forecast, allowance    §4
    liquidity.py  Dynamic buffer, tiers, sweeps, timing     §15, §16
    cards.py      Card ranking, caps, grace state           §5, §6
    tax.py        Net benefit comparison                    §17
    coverage.py   Deposit insurance, collateral stress      §19, §20
  execution/
    engine.py     Lifecycle, idempotency, recovery, faults  §21
  ai/
    llm.py        Local model client with autodetection
    tools.py      27 calculation tools with permission scope
    router.py     Deterministic intent routing + templates
    guardrails.py Grounding, injection defence, scope, answer state
    graph.py      The LangGraph agent
    mock_server.py A local-model test double that can misbehave on purpose
  api/app.py      38 REST endpoints
  web/index.html  The dashboard
```

---

## The allocation engine

Three rules, and everything else follows.

**The monthly plan is the target ledger; paychecks are its funding events.** If
a monthly goal is $700 and $400 is already funded, the next run allocates at
most the remaining $300.

**A deadline before the next paycheck is funded from this one.** This is the
rule that makes even splits fail:

```
Paycheck 2026-09-01, $3,000 in                 Paycheck 2026-09-15, $3,000 in
  $1,800  Mortgage       due 09-05  ← urgent     $250  Student loan  due 09-20
  $  150  Utilities      due 09-08  ← urgent     $250  Insurance     due 09-22
  $  250  Auto loan      due 09-10  ← urgent     $500  Spending
  $  600  Card statement due 09-12  ← urgent     $700  Emergency reserve
  $  200  Spending       (remainder)             $300  Annual reserve
                                                 $500  Brokerage cash
                                                 $500  Extra principal
```

Splitting the $1,800 mortgage across both paychecks leaves the 5th unfunded.
The engine reserves the deadline first and gives the remainder to the next
target in the user's saved priority order.

**Required obligations and protected reserves take precedence over optional
percentages** — and only over *percentages*. A fixed-amount target such as
household spending keeps the position the user gave it, which is what "the saved
priority order" means when a paycheck arrives short.

That table above is Table 10 of the specification, reproduced to the cent by
`tests/test_worked_examples.py`.

---

## The assistant

```
scope_guard → classify → execute_tools → compose → verify → finalize
```

A LangGraph state machine where the model has exactly two jobs: read a JSON
tool result, and write a sentence. It never chooses a number.

The graph runs in **router mode** by default — the deterministic router picks
the calculator, because a 1B local model is not a reliable free-choice tool
caller. `tool_choice: "model"` switches to genuine model-driven tool calling for
a capable model. The guardrails are identical either way, because they run on
the output rather than on the model's good intentions.

### Four guardrails, enforced mechanically

| Guardrail | What it catches |
|---|---|
| **Numeric grounding** | Every figure, percentage and date in the answer must appear in the tool result. An invented `$4,812.37` is rejected and the calculator's own wording is used instead. |
| **Completed-action claims** | "I have scheduled the payment" is only permitted when a tool result actually shows `submitted`, `processing` or `reconciled`. |
| **Untrusted echo** | A draft that reproduces text planted in a retrieved statement is rejected — this is the one a numeric check cannot see, because the injected sentence carries no figures. |
| **Scope** | Security selection, new-product recommendation, eligibility determination and legal advice are refused, and the refusal still shows its evidence. |

Every rejection is visible in the trace and in the dashboard, which labels each
answer *the model wrote it · N figures verified* or *calculator wording*.

### Testing against a model that misbehaves

`finpilot/ai/mock_server.py` is a test double, not a model. It can be told to
behave like a small local Llama going wrong:

```bash
python -m finpilot.ai.mock_server --misbehave hallucinate claim_action obey chatty
```

All four are caught. The injection case is the one worth noting: an earlier
build passed it, because an echoed instruction contains no numbers for the
grounding check to test. That is why `echoes_untrusted` exists.

---

## Execution

The lifecycle is durable and explicit:

```
draft → awaiting_authorization → authorized → scheduled → validating →
submitted → processing → funds_available → credited_by_biller → reconciled
                    ↘ failed   ↘ returned   ↘ outcome_unknown
```

Illegal transitions raise. Two invariants are structural rather than
conventional:

* an **idempotency key** derived from the payment's intent means a retry never
  creates a second payment; and
* an **`outcome_unknown`** leg blocks any replacement until status recovery
  resolves it — a network timeout triggers a query against the provider with the
  original identity, never a new submission.

`SimulatedProvider` injects timeouts, NSF, rejections and post-settlement
returns so the recovery paths are exercised deterministically:

```bash
curl -X POST localhost:8099/api/execution/build
curl -X POST localhost:8099/api/execution/inject-fault \
     -d '{"leg_id":"leg_...","fault":"timeout"}' -H 'Content-Type: application/json'
curl -X POST localhost:8099/api/execution/run/grp_...
```

A returned funding transfer cancels the legs that depended on it, reopens the
obligation, and leaves completed legs alone. `pause-all` distinguishes what it
cancelled from what was already sent and cannot be recalled.

---

## Version 4 additions built in

The expansion review identified gaps in Version 3. These are implemented rather
than noted:

* **GX01 multi-currency ledger.** Currency is a first-class attribute on every
  amount, with per-currency minor units (JPY 0, KWD 3). `Money(100,"USD") +
  Money(100,"EUR")` raises. This is cheap now and expensive after a second
  market's data exists.
* **GX02/GX05 jurisdiction as a rule dimension.** `REGIMES` holds FDIC, both
  NCUA trust rules either side of 1 December 2026, and FSCS either side of the
  December 2025 increase to £120,000, plus DGS, DICGC and FCS. `regime_for()`
  resolves by jurisdiction and date.
* **CR02 statement-close utilization timing** — the calculation Version 3 was
  missing. Reported utilization is a snapshot at statement close, not at the due
  date, so the timing optimizer now carries a third objective beside interest
  and yield.
* **EN10 confidence ladder.** Every calculation returns a `Confidence` and names
  what would make it exact, instead of refusing to answer.
* **AC01 estimated assets** labelled separately: included in net worth, never in
  payment capacity.

---

## Tests

```
tests/test_worked_examples.py   45   every figure in §4, §6, §8, §14–§20
tests/test_execution.py         19   lifecycle, idempotency, recovery, faults
tests/test_ai.py                46   routing, grounding, injection, scope, states
tests/test_core.py              23   money, dates, ledger integrity
tests/query_suite.py            32   user questions end to end, with and without a model
```

The worked-example suite is the regression harness that matters: if one of those
fails, the engine has drifted from the document. Among them —

* §8's three repayment strategies to the cent, including the $326.11 and
  $1,428.27 premiums and the first-month split of $740 / $100 / $300 / $1,138.77;
* §14's Table 10, both paychecks, all eleven destinations;
* §15's buffer ($5,500 retained, $2,500 sweepable);
* §16's $13.38 gross, $9.50 after tax, $65.75 avoided interest, $56.25 net;
* §17's Table 12 including the deduction row at $304;
* §19's $270,000 aggregate, $20,000 excess, $20,500 remedy;
* §20's collateral stress to the $70,000 deficiency.

---

## Boundaries the code enforces

The specification's product boundary is not a disclaimer here, it is behaviour.
The application will not recommend opening an account, select a security,
determine tax or program eligibility, or describe a payment as sent before the
payment service confirms it. `explain_product_boundary` is a real tool, and the
refusal path calls it so the answer carries evidence like any other.

Provider integration is simulated. A sandbox success is not production approval,
and the licensing, provider-arrangement and regulatory work named in §21 remains
a launch dependency that no amount of code discharges.

All figures in the demo households are fictional.
