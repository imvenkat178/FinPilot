"""Publish reproducible workflow validation evidence, keeping model failures visible."""
import json,statistics,shutil
from pathlib import Path

def main():
    destination=Path("validation")
    destination.mkdir(exist_ok=True)
    primary=json.loads(Path(".local/llama-workflows-constrained-normal.json").read_text(encoding="utf-8"))
    rows=primary["cases"]
    assert len(rows)==140,"Refuse to publish a partial model matrix as complete."
    outputs={"normal":primary}
    for key,source in [("scenarios",".local/llama-workflow-scenarios.json"),("diagnostic",".local/llama-workflows-diagnostic.json"),("scenario-diagnostic",".local/llama-workflow-scenarios-diagnostic.json")]:
        if Path(source).exists():
            outputs[key]=json.loads(Path(source).read_text(encoding="utf-8"))
    for key,data in outputs.items():
        (destination/("ai-workflows-"+key+".json")).write_text(json.dumps(data,indent=2),encoding="utf-8")
    passed=sum(x.get("interpretation_accepted",False) for x in rows)
    confirmed=sum(x.get("confirmed",False) for x in rows)
    persisted=sum(x.get("receipt_persisted",False) for x in rows)
    explanations=sum(x.get("explanation",{}).get("accepted",False) for x in rows)
    attempts=sum(x.get("explanation",{}).get("attempted",False) for x in rows)
    understood={x["capability"] for x in rows if x.get("interpretation_accepted")}
    latency=sorted(x["latency_ms"] for x in rows if "latency_ms" in x)
    report=f"""# AI workflow validation

Current implementation audit: September 13, 2026. The workflow controls and reviewed executor are implemented. Complete natural-language coverage is **not established** unless every capability and required paraphrase passes interpretation; fallbacks never count as model understanding.

## Automated application checks

The final Python result is recorded in [python-suite.txt](validation/python-suite.txt). Seven frontend suites cover main screens, management, execution, bank linking, assistant history/sources and reviewed workflows; see [frontend-suites.txt](validation/frontend-suites.txt).

The executable [capability manifest](tests/chat_capability_manifest.py) covers all 80 catalog entries. Each write case runs request, preview, explicit confirmation, database/revision verification, refreshed bootstrap, duplicate confirmation and saved receipt checks. Additional tests cover edits, cancellation, expired/stale proposals, separate workers, conflicting confirmations, roles/tenants, CSV mappings and duplicates, private attachments, interrupted providers, compound reads/changes, clarification restoration, credential rejection and injected document/MCP instructions. Paycheck event creation/editing, regenerated expectations, received history, card terms and cleared rule mandates have dedicated assertions.

SQLite migration upgrade/repeat/downgrade/upgrade matches SQLAlchemy metadata. PostgreSQL DDL compiles. A live PostgreSQL server and hosted load/concurrency checks were unavailable; SQLite checks do not replace them.

## Local Llama results: normal 12-second budget

Provider: local Ollama, model: llama3.2:latest (3.2B Q4_K_M, CPU). No cloud model substituted. Calls are sequential, with one planning call and at most one explanation sharing the inference budget. The typed planner constrains operation names and argument fields, followed by full server validation. The HTTP/API duration includes additional local work.

| Measurement | Result |
| --- | --- |
| Exact canonical interpretations | {passed}/140 |
| Distinct capabilities with at least one accepted interpretation | {len(understood)}/80 |
| Reviewed fictional actions confirmed | {confirmed} |
| Confirmed receipts restored from conversations | {persisted} |
| Accepted model introductions | {explanations}/{attempts} attempted |
| Median / p95 / maximum API duration | {statistics.median(latency)/1000:.2f}s / {latency[int(.95*(len(latency)-1))]/1000:.2f}s / {max(latency)/1000:.2f}s |

The matrix forces model planning even for requests that normally take a deterministic route, so it measures planner understanding rather than overall application success. The later compound shortlist correction leaves the candidate lists unchanged for all 80 base requests; supplemental scenario runs exercise that correction.\n\nThe matrix includes one base question for each capability and two additional paraphrases for each of 30 action families. Each case compares the exact canonical operation and arguments, not merely successful text generation or schema acceptance. Only a correctly interpreted fictional proposal is confirmed. The evidence records expected/actual arguments, planner status, model calls, explanation acceptance, fallback, preview, receipt, revision and persistence separately.

Financial calculations and consequential numbers remain server-rendered. A workflow model introduction cannot replace the calculator wording; numerical introductions are rejected. Ordinary deterministic calculator routes retain their established answer checks. These guards do not prove complete semantic correctness.

All MCP tests use the actual Python SDK with fictional ASGI/Streamable HTTP fixtures. Bank calls use mocked adapters. No live bank credentials or real funds were used. Interactive browser checks were performed separately from heavy regression testing; other machine workload, prompt caching and CPU contention are not controlled production benchmarks.

[Complete normal-budget evidence](validation/ai-workflows-normal.json)

## Capability interpretation matrix

Base is the exact manifest request. Variants shows accepted additional paraphrases; a dash means that read/handoff capability has no action-family variants.

| Capability | Base | Additional variants |
| --- | --- | --- |
"""
    for row in rows:
        if row["variant"]!=0:
            continue
        variants=[x for x in rows if x["capability"]==row["capability"] and x["variant"]!=0]
        variant_result=f'{sum(x["interpretation_accepted"] for x in variants)}/{len(variants)}' if variants else "-"
        report+=f'| {row["capability"]} | {"Pass" if row["interpretation_accepted"] else "Limited"} | {variant_result} |\n'
    if "scenarios" in outputs:
        report+="\n## Supplemental normal-budget scenarios\n\n"
        for row in outputs["scenarios"]["cases"]:
            report+=f'- {row["scenario"]}: {"accepted" if row["interpretation_accepted"] else "limited"}, {row["latency_ms"]/1000:.2f}s.\n'
        report+="\n[Compound and paycheck-event evidence](validation/ai-workflows-scenarios.json)\n"
    if "diagnostic" in outputs:
        data=outputs["diagnostic"]
        report+=f"""
## Separate 60-second diagnostics

These are diagnostic runs, not normal-budget acceptance. {data["accepted"]}/{data["total"]} exact interpretations passed. See [diagnostic evidence](validation/ai-workflows-diagnostic.json) for individual latency, planner, explanation and persistence results.
"""
    if "scenario-diagnostic" in outputs:
        report+="\nCompound/paycheck-event diagnostic results (60 seconds each, separate from normal acceptance):\n\n"
        for row in outputs["scenario-diagnostic"]["cases"]:
            report+=f'- {row["scenario"]}: {"accepted" if row["interpretation_accepted"] else "limited"}, {row["latency_ms"]/1000:.2f}s.\n'
        report+="\n[Supplemental diagnostic evidence](validation/ai-workflows-scenario-diagnostic.json)\n"
    report+="""
## Browser journeys

Verified on the actual local application at desktop 1366 x 900 and compact 390 x 844:

- Create a fictional bill using chat controls, inspect preview, confirm, see it on Payments, reopen the drawer, and reload its saved receipt.
- Prepare an amount change through chat, edit its preview inline, then cancel; no change is saved.
- Upload a fictional CSV, inspect the exact row, confirm its import and restore the receipt after reload.
- Resume the existing benefits conversation, restore selected documents and inspect the cited original passage.
- No horizontal page overflow on compact layout and no captured console errors/warnings.

The file chooser stalled during the CSV journey; the eventual upload, preview, confirmation and restoration succeeded. This browser-tool delay is separate from the measured API model latencies. Destructive source tests and credential/access tests use isolated automated fixtures.

## Limits and release status

- Some local-model interpretations/timeouts remain limited. Structured workflow controls and calculator fallbacks preserve application access; they are not evidence of successful model understanding.
- A diagnostic success cannot establish the default 12-second target. No hosted latency SLA or full production readiness is claimed.
- Real payments and transfers are excluded. Sample execution is confined to fictional sample workspaces.
- Live provider credentials, institutional behavior, external MCP OAuth and production PostgreSQL deployment/load remain unverified.
- Retrieval is private lexical search over text-bearing PDF/TXT/Markdown, without OCR or semantic embeddings.
- The existing aggregate snapshot grows with financial history; the application remains a modular monolith, not an independently scaled ledger platform.
- Provider outcomes may remain unknown after external failures. No automatic retry or claim of a completed external operation is made.

## Reproduce

    .venv/Scripts/python.exe -m pytest -q
    Get-ChildItem tests/*frontend.mjs | ForEach-Object { node $_.FullName }
    .venv/Scripts/python.exe -m scripts.evaluate_workflows --paraphrases --require-coverage --output .local/llama-workflows-constrained-normal.json
    .venv/Scripts/python.exe -m scripts.evaluate_workflow_scenarios
    .venv/Scripts/python.exe -m scripts.report_workflow_validation

The --require-coverage flag exits nonzero for any failed interpretation or missing confirmed receipt; generating a report does not override that gate. The evaluator uses disposable fictional workspaces and disables only fixture request throttling. Production throttles and confirmation checks remain enabled.

Structured output uses the OpenAI-compatible response_format supported by [Ollama's official documentation](https://docs.ollama.com/capabilities/structured-outputs). Schemas constrain syntax; the evaluator separately checks interpretation.
"""
    shutil.copyfile(".local/chat-full-python-final.log",destination/"python-suite.txt")
    shutil.copyfile(".local/chat-frontend-final.log",destination/"frontend-suites.txt")
    Path("AI_VALIDATION.md").write_text(report,encoding="utf-8")
    print(f"Published {passed}/140 exact interpretations, {confirmed} confirmations; failures retained.")

if __name__=="__main__":
    main()
