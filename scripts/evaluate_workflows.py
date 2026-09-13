"""Sequential real llama3.2 capability evaluation; fallback is never a pass."""
import argparse,json,time
from pathlib import Path
from dataclasses import replace
from tests.chat_capability_manifest import CASES
from tests.chat_fixtures import workflow_client,materialize
from finpilot.ai.llm import LocalLLM,LLMConfig
from finpilot.ai.capabilities import catalog
from tests.test_chat_actions import confirm

PARAPHRASES={
"update_account":["Rename Everyday Checking to Daily Cash","I'd like Everyday Checking to be called Daily Cash"],
"update_income":["Set the Salary (semi-monthly) net paycheck amount to 3100","Edit Salary (semi-monthly): net amount should be 3100"],
"update_bill":["Please update the Mortgage payment bill to 1999.99","Set the amount on my Mortgage payment bill to 1999.99"],
"update_goal":["Set Emergency reserve's savings target to 16000","Edit the Emergency reserve goal so its target is 16000"],
"update_rule":["Call the Mortgage required payment rule Home allocation","Change the name of Mortgage required payment rule to Home allocation"],
"update_tax":["Update my federal marginal tax assumption to 25 percent","Change the federal marginal rate to 0.25"],
"correct_transaction":["Recategorize transaction {transaction} as subscriptions","Set the category of transaction {transaction} to subscriptions"],
"pause_rule":["Temporarily pause the Mortgage required payment rule","Stop running the Mortgage required payment rule"],
"resume_rule":["Turn Mortgage required payment rule back on","Unpause Mortgage required payment rule"],
"skip_rule":["Skip one upcoming occurrence of Mortgage required payment rule","Skip the next run of Mortgage required payment rule"],
"authorize_rule":["Prepare simulation authorization for Mortgage required payment capped at 2000 per run","Set the simulation per-run authorization cap for Mortgage required payment to 2000"],
"draft_bill_payment":["Prepare a payment draft for the Mortgage payment bill","Draft a one-time payment for Mortgage payment"],
"build_payment_drafts":["Prepare allocation drafts for September 2026","Generate the paycheck payment drafts for month 9 of 2026"],
"simulate_payment":["Simulate execution of payment group {group}","Run group {group} through the sample payment provider"],
"recover_payment":["Check and recover the sample payment leg {leg}","Recover the original simulated payment {leg}"],
"pause_execution":["Stop all payment execution","Globally pause automations"],
"resume_execution":["Restart future payment execution","Unpause global execution"],
"import_transactions":["Save the transaction rows from attachment {attachment}","Import CSV history from attachment {attachment}"],
"delete_document":["Remove Annual report from my document library","Delete my saved Annual report document"],
"delete_conversation":["Remove the saved chat Old conversation","Delete Old conversation from my conversation history"],
"disconnect_mcp":["Remove my Report source MCP connection","Disconnect the MCP connection named Report source"],
"revoke_mcp_access":["Remove the MCP grant Test access","Revoke the Test access token"],
"sync_bank":["Refresh my Fixture Bank data","Pull the latest data from Fixture Bank"],
"disconnect_bank":["Disconnect my linked Fixture Bank account","Remove the bank connection to Fixture Bank"],
"import_mcp":["Retrieve Report source's approved statement tool into documents","Import the statement from the Report source MCP connection"],
"create_account":["Add Travel Cash as a manual checking account with balance 100","Set up a manual checking account named Travel Cash holding 100"],
"create_income":["Add income source Bonus: monthly, net 250, Everyday Checking, next date 2026-10-01","Set up monthly Bonus income of 250 net arriving in Everyday Checking starting 2026-10-01"],
"create_bill":["Add Music as a monthly bill for 12.99 due 2026-10-05 from Everyday Checking","Set up a monthly Music bill, amount 12.99, next due 2026-10-05, funding account Everyday Checking"],
"create_goal":["Add Holiday as a savings goal with target 1000 in Emergency & Annual Savings","Set up a Holiday goal targeting 1000 using Emergency & Annual Savings"],
"create_rule":["Add Travel savings rule: goal, fixed 50, Everyday Checking to Emergency & Annual Savings, on_income","Set up a rule named Travel savings with purpose goal, method fixed, amount 50, source Everyday Checking, destination Emergency & Annual Savings, cadence on_income"],
}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--timeout",type=float,default=12)
    parser.add_argument("--output",default=".local/llama-workflows-normal.json")
    parser.add_argument("--only",default="")
    parser.add_argument("--paraphrases",action="store_true")
    parser.add_argument("--require-coverage",action="store_true",help="Exit nonzero if any interpretation or reviewed action fails.")
    args=parser.parse_args()
    selected=[c for c in CASES if not args.only or c.name in args.only.split(",")]
    cases=[(c,0) for c in selected]
    if args.paraphrases:
        cases += [(replace(c,question=q),i+1) for c in selected for i,q in enumerate(PARAPHRASES.get(c.name,[]))]
    rows=[]
    path=Path(args.output);path.parent.mkdir(exist_ok=True,parents=True)
    for case,variant in cases:
        with workflow_client() as c:
            old=c.runtime.llm
            c.runtime.llm=LocalLLM(LLMConfig(base_url="http://127.0.0.1:11434/v1",model="llama3.2:latest",
                timeout=args.timeout,failure_cooldown=0))
            old.close()
            question,expected=materialize(case,c)
            started=time.monotonic()
            row={"capability":case.name,"variant":variant,"question":question,"expected_arguments":expected,
                 "budget_seconds":args.timeout,"diagnostic":args.timeout>12}
            try:
                response=c.post("/api/ask",json={"question":question,"workflow_mode":"model","conversation_id":c.action_conversation})
                data=response.json()
                operations=data.get("workflow",{}).get("operations",[])
                expected_plan=[{"capability":case.name,"arguments":expected}]
                interpreted=(data.get("workflow",{}).get("planner",{}).get("accepted") is True and operations==expected_plan)
                row.update(http_status=response.status_code,latency_ms=round((time.monotonic()-started)*1000),
                    planner=data.get("workflow",{}).get("planner"),interpretation_accepted=interpreted,
                    actual_operations=operations,model_calls=data.get("generation",{}).get("calls",[]),
                    explanation=data.get("explanation",{"attempted":False,"accepted":False,"reason":"server-rendered results"}),
                    fallback=not interpreted,answer=data.get("answer",""),proposal_created=False,confirmed=False)
                props=[p["proposal"] for p in data.get("parts",[]) if p["type"]=="proposal"]
                if props:
                    row["proposal_created"]=True
                    row["preview"]=props[0]["preview"]
                    # Never confirm a wrong interpretation even in the fictional workspace.
                    if interpreted:
                        result=confirm(c,props[0])
                        row["confirmed"]=result.status_code==200 and bool(result.json().get("receipt"))
                        row["receipt"]=result.json().get("receipt")
                        row["database_revision"]=c.runtime.read(c.principal).revision
                        restored=c.get("/api/conversations/"+c.action_conversation).json()
                        row["receipt_persisted"]=any(a.get("action_receipts") for a in restored["answers"])
                row["answer_persisted"]=any(a.get("answer_id")==data.get("answer_id") for a in c.get("/api/conversations/"+c.action_conversation).json()["answers"])
            except Exception as exc:
                row.update(interpretation_accepted=False,fallback=True,error=type(exc).__name__,detail=str(exc)[:300])
            rows.append(row)
            path.write_text(json.dumps({"model":"llama3.2:latest","provider":"local Ollama","budget_seconds":args.timeout,
                "diagnostic":args.timeout>12,"sequential":True,"cases":rows,
                "accepted":sum(x.get("interpretation_accepted",False) for x in rows),"total":len(rows)},indent=2),encoding="utf-8")
            print(f'{len(rows)}/{len(cases)} {case.name}[{variant}]: {"PASS" if row.get("interpretation_accepted") else "LIMITED"} {row.get("latency_ms")} ms',flush=True)
    if args.require_coverage and any(not x.get("interpretation_accepted") or
            (x.get("proposal_created") and not (x.get("confirmed") and x.get("receipt_persisted"))) for x in rows):
        raise SystemExit(1)
if __name__=="__main__":
    main()