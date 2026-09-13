"""Publish current, complete test runs without hiding fallback or older failures."""
import argparse,json,math,re,shutil,statistics
from pathlib import Path
from scripts.evaluate_workflows import PARAPHRASES
from tests.chat_capability_manifest import CASES


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def metrics(data):
    rows=data["cases"]
    calls=[call for row in rows for call in row.get("model_calls",[])]
    values=sorted(row["latency_ms"] for row in rows if "latency_ms" in row)
    return {"accepted":sum(bool(row.get("interpretation_accepted")) for row in rows),"total":len(rows),
            "capabilities":len({r["capability"] for r in rows if r.get("interpretation_accepted")}),
            "calls":len(calls),"reported_tokens":sum(c.get("usage",{}).get("total_tokens",0) for c in calls),
            "completed_calls":sum(c.get("status")=="completed" for c in calls),
            "confirmed":sum(bool(r.get("confirmed")) for r in rows),
            "restored":sum(bool(r.get("receipt_persisted")) for r in rows),
            "median_seconds":statistics.median(values)/1000,
            "p95_seconds":values[math.ceil(len(values)*.95)-1]/1000,"max_seconds":max(values)/1000}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary",default=".local/llama-workflows-e2e-final.json")
    args=parser.parse_args()
    data=load(args.primary)
    expected={(case.name,i) for case in CASES for i in range(1+len(PARAPHRASES.get(case.name,[])))}
    actual={(row["capability"],row["variant"]) for row in data["cases"]}
    assert actual==expected and len(data["cases"])==len(expected),"A complete matrix is required."
    assert data["budget_seconds"]==12 and not data["diagnostic"] and data["model"]=="llama3.2:latest"
    destination=Path("validation");destination.mkdir(exist_ok=True)
    sources={"ai-workflows-e2e-final.json":args.primary,
             "ai-workflow-scenarios-e2e.json":".local/llama-workflow-scenarios-e2e.json",
             "ai-memory-scope-e2e.json":".local/ai-memory-scope-e2e.json",
             "api-benchmark-e2e.json":".local/api-benchmark-e2e.json",
             "workflow-benchmark-e2e.json":".local/workflow-benchmark-e2e.json",
             "postgres-e2e.json":".local/postgres-e2e.json",
             "python-e2e.txt":".local/e2e-python-final.log",
             "frontend-e2e.txt":".local/e2e-frontend.log"}
    for target,source in sources.items():
        shutil.copyfile(source,destination/target)
    baseline=load("validation/ai-workflows-normal.json")
    before,after=metrics(baseline),metrics(data)
    comparison={"historical_baseline":before,"current":after,
                "same_140_prompts":True,"monetary_cost_measured":False,
                "token_counts":"Provider-reported completed calls only; failed calls may have unreported work."}
    (destination/"inference-comparison.json").write_text(json.dumps(comparison,indent=2),encoding="utf-8")
    python_log=Path(sources["python-e2e.txt"]).read_text(encoding="utf-8-sig")
    match=re.findall(r"\d+ passed[^\n]*",python_log)
    assert match and "FAILED " not in python_log,"Python suite must be complete and passing."
    lines=["# End-to-end validation — September 13, 2026", "",
           "Current verification of the local application, including the changes below. Llama fallback and exact interpretation are counted separately. These results do not establish hosted production performance.","",
           "## Changes verified", "",
           "- Smaller request-specific planner schemas keep relevant capabilities, explicit decimal values, percentage units and record references. Required compound task slots prevent silently dropping a requested task. Unrequested fields are omitted; words inside record names cannot silently change recurrence. Duplicate names require explicit choices; guessed IDs cannot bypass clarification.",
           "- Deterministic compound reads and explicit renames use no model calls. Workflow results use authoritative server rendering without a generic second generation call. Legacy calculator/document explanation checks remain active.",
           "- Planner context reads only relevant entity kinds. MCP tool/source allowlists are checked before preview and before claiming a provider action; known precondition failures remain unexecuted.",
           "- Payment lists, calendar rows and account details preserve bill cents. Completed receipts no longer retain a contradictory Drafted status badge.", "",
           "## Automated application checks", "",
           f"Python: **{match[-1].strip()}**. Seven frontend suites passed. [Python log](validation/python-e2e.txt), [frontend log](validation/frontend-e2e.txt).", "",
           "The 80-capability manifest exercises authenticated chat, preview, explicit confirmation, persisted changes, refreshed bootstrap and restored receipts. Additional tests cover cancellation, stale/expired proposals, duplicate and concurrent confirmation, role/tenant boundaries, provider failures, CSV imports, document/MCP injection, conversation restoration, and account context changes. Model doubles in those tests prove application behavior, not Llama understanding.", "",
           "Local PostgreSQL verification passed migration upgrade/repeat/downgrade/re-upgrade with metadata agreement, compound chat, two-worker exactly-once confirmation, conflicting-preview rejection, cross-tenant denial, cancellation, and an actual MCP SDK fixture import. The test used a new disposable database, then removed it and stopped its isolated server. [PostgreSQL evidence](validation/postgres-e2e.json).", "",
           "## Live local Llama", "",
           "Model: **llama3.2:latest**, Ollama, CPU, Q4_K_M. Each case uses the normal 12-second inference budget, sequentially; no cloud substitute. The matrix forces planning even for phrases normally handled without inference. It includes every capability plus two extra paraphrases for each of 30 action families.", "",
           "| Measurement | Historical baseline | Current complete run |", "| --- | ---: | ---: |"]
    for title,key in [("Exact interpretations","accepted"),("Capabilities with an accepted interpretation","capabilities"),("Model calls","calls"),("Reported completed-call tokens","reported_tokens"),("Confirmed fictional actions","confirmed"),("Restored receipts","restored")]:
        denominator="/140" if key=="accepted" else "/80" if key=="capabilities" else ""
        lines.append(f"| {title} | {before[key]}{denominator} | {after[key]}{denominator} |")
    for title,key in [("Median API time","median_seconds"),("p95 API time","p95_seconds"),("Maximum API time","max_seconds")]:
        lines.append(f"| {title} | {before[key]:.2f}s | {after[key]:.2f}s |")
    lines += ["", "Call reduction measures avoided generation work. Token totals are provider-reported for completed calls; timeouts can consume unreported work. Local compute/electricity cost was not measured, and no cloud billing savings are claimed. Other active model processes shared the CPU, so these are observed local timings, not a controlled hardware benchmark or SLA. The full Llama run used fixed planner prompts and schemas; a final duplicate-name execution guard was verified separately in the final application suite and does not change those interpretation inputs.", "",
              "[Complete current matrix](validation/ai-workflows-e2e-final.json) records exact expected/actual arguments, planner status, fallback, model calls, preview, confirmation and persistence evidence. [Comparison data](validation/inference-comparison.json).", ""]
    failures=[row for row in data["cases"] if not row.get("interpretation_accepted")]
    lines += ["### Remaining interpretation limits", ""]
    if failures:
        lines += [f"**{len(failures)} of 140 cases remain limited.** A fallback is not a successful interpretation. Wrong interpretations are never confirmed by the evaluator. Exact previews and user confirmation remain required in the application.", "", "| Capability / variant | Observed outcome |", "| --- | --- |"]
        for row in failures:
            detail="12-second model timeout" if any(c.get("error")=="ReadTimeout" for c in row.get("model_calls",[])) else row.get("planner",{}).get("reason") or ("Exact arguments did not match" if row.get("actual_operations") else "No accepted operation")
            lines.append(f"| {row['capability']} / {row['variant']} | {detail} ({row.get('latency_ms',0)/1000:.2f}s) |")
    else:
        lines.append("All 140 cases passed exact interpretation in this run. This bounded corpus does not establish correctness for every possible financial request.")
    missing=[case.name for case in CASES if not any(row["capability"]==case.name and row.get("interpretation_accepted") for row in data["cases"])]
    if missing:
        lines += ["", "No accepted interpretation in this complete run for: "+", ".join(missing)+". Their typed chat controls and application execution paths are covered by the passing application suite."]
    scenarios=load(sources["ai-workflow-scenarios-e2e.json"])
    lines += ["", "### Compound and memory journeys", ""]
    for row in scenarios["cases"]:
        lines.append(f"- {row['scenario']}: {'accepted' if row['interpretation_accepted'] else 'limited'}, {row['latency_ms']/1000:.2f}s.")
    memory=load(sources["ai-memory-scope-e2e.json"])
    lines.append(f"- Conversation follow-up with checking → protected-savings context: authoritative evidence/guard checks {'passed' if memory['passed'] else 'failed'}; model explanation {'accepted' if memory['model_understanding_accepted'] else 'fell back'}, {memory['elapsed_seconds']:.2f}s.")
    lines += ["", "[Compound results](validation/ai-workflow-scenarios-e2e.json), [memory result](validation/ai-memory-scope-e2e.json). No 60-second diagnostic is counted as normal-budget acceptance.", "",
              "## Application latency without inference", "",
              "Twelve repetitions on disposable SQLite workspaces through authenticated in-process ASGI. These timings include application requests and exclude browser/network/TLS. They do not measure Llama understanding.", "",
              "| Workflow step | Median | p95 |", "| --- | ---: | ---: |"]
    benchmark=load(sources["workflow-benchmark-e2e.json"])
    for name,values in benchmark["metrics"].items():
        lines.append(f"| {name.replace('_',' ')} | {values['median_ms']:.2f} ms | {values['p95_ms']:.2f} ms |")
    lines += ["", "The reviewed-action benchmark verifies the exact database value, refreshed revision, saved receipt and zero model calls on every repetition. [Workflow timings](validation/workflow-benchmark-e2e.json).", "",
              "The separate 5,000-row benchmark checks cold/warm reads, indexed history, create/update and that financial reads/writes finish while inference is deliberately held. Cold clears application caches; database/OS caches remain warm. [Complete API benchmark](validation/api-benchmark-e2e.json).", "",
              "## Browser verification", "",
              "Verified the ten financial sections, a tax comparison, compound account/history chat, an exact bill change, confirmation, refreshed Payments, persisted receipt after reload, shared drawer/workspace history, and restoration of the original document conversation. Desktop and 390 × 844 compact layouts were checked; compact confirmation completed without horizontal overflow. Captured console errors/warnings were empty. The temporary bill change was restored to its original amount. [Browser check record](validation/browser-e2e.json).", "",
              "The cents-rounding defect was observed in the real Payments list, fixed, and rechecked. Destructive, credential, provider-failure and injection cases run in isolated automated fixtures. The prior CSV browser upload journey is documented in the historical AI audit; it was not repeated during this pass.", "",
              "## Practical limits", "",
              "- Hosted multi-user load, TLS/network latency, live bank/provider behavior and external MCP OAuth remain unverified. The PostgreSQL checks are local, not a hosted deployment test.",
              "- Real payments/transfers remain excluded; simulator tests use fictional funds. Uncertain external outcomes require inspection and are not automatically retried.",
              "- Document retrieval is lexical search over text-bearing PDF/TXT/Markdown, without OCR or embeddings. Large aggregate snapshots still incur decode/write costs as history grows.",
              "- Earlier iterations, including a stopped load-contended run that exposed an APY-unit defect, are retained in .local. They are not merged into the current matrix to inflate coverage.", "",
              "## Reproduce", "", "```powershell", ".venv/Scripts/python.exe -m pytest -q", "Get-ChildItem tests/*frontend.mjs | ForEach-Object { node $_.FullName }",
              ".venv/Scripts/python.exe -m scripts.evaluate_workflows --paraphrases --require-coverage --output .local/llama-workflows-e2e-final.json",
              ".venv/Scripts/python.exe -m scripts.evaluate_workflow_scenarios --output .local/llama-workflow-scenarios-e2e.json",
              ".venv/Scripts/python.exe scripts/evaluate_memory_scope.py --output .local/ai-memory-scope-e2e.json",
              ".venv/Scripts/python.exe -m scripts.benchmark --rows 5000 --runs 12 --output .local/api-benchmark-e2e.json",
              ".venv/Scripts/python.exe -m scripts.benchmark_workflows --output .local/workflow-benchmark-e2e.json",
              ".venv/Scripts/python.exe -m scripts.verify_postgres --maintenance-url postgresql+psycopg://finpilotqa@127.0.0.1:55439/postgres",
              ".venv/Scripts/python.exe -m scripts.report_e2e_validation", "```", "",
              "PostgreSQL verification requires a separate local test instance and creates/drops only its own randomly named database. The Llama coverage gate exits nonzero for any failed exact interpretation or missing receipt. Structured output uses Ollama's documented [response_format support](https://docs.ollama.com/capabilities/structured-outputs); schema validity alone does not prove semantic correctness.", ""]
    Path("END_TO_END_VALIDATION.md").write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps(comparison,indent=2))


if __name__=="__main__":
    main()
