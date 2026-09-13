"""Supplemental sequential Llama checks for compound plans and paycheck events."""
import argparse,json,time
from pathlib import Path
from tests.chat_fixtures import workflow_client
from finpilot.ai.llm import LocalLLM,LLMConfig
from tests.test_chat_actions import confirm

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--timeout",type=float,default=12)
    parser.add_argument("--output",default=".local/llama-workflow-scenarios.json")
    options=parser.parse_args()
    scenarios=[
        ("accounts_and_transactions","List all account records and find transactions for Music",
         [("list_records",{"kind":"account"}),("search_transactions",{"query":"Music"})]),
        ("documents_and_calculator","Search documents for annual fee and show my tax assumptions",
         [("search_documents",{"query":"annual fee"}),("get_tax_profile",{})]),
        ("charges_and_bill","Find transactions for Music and update bill Utilities amount to 210",
         [("search_transactions",{"query":"Music"}),("update_bill",{"record_id":"bill_utilities","amount":"210"})]),
        ("income_event","Create an income event for source Salary (semi-monthly), expected 2027-12-29 for 250",
         [("create_income",{"record_type":"event","source_id":"inc_salary","expected_date":"2027-12-29","expected_amount":"250"})])]
    rows=[]
    for name,question,plan in scenarios:
        with workflow_client() as c:
            c.runtime.llm.close()
            c.runtime.llm=LocalLLM(LLMConfig(base_url="http://127.0.0.1:11434/v1",model="llama3.2:latest",timeout=options.timeout,failure_cooldown=0))
            expected=[{"capability":name,"arguments":args} for name,args in plan]
            start=time.monotonic()
            answer=c.post("/api/ask",json={"question":question,"conversation_id":c.action_conversation,"workflow_mode":"model"}).json()
            actual=answer.get("workflow",{}).get("operations",[])
            accepted=answer.get("workflow",{}).get("planner",{}).get("accepted") is True and actual==expected
            row={"scenario":name,"question":question,"expected_operations":expected,"actual_operations":actual,
                 "interpretation_accepted":accepted,"latency_ms":round((time.monotonic()-start)*1000),
                 "planner":answer.get("workflow",{}).get("planner"),"explanation":answer.get("explanation"),
                 "model_calls":answer.get("generation",{}).get("calls",[]),"fallback":not accepted,
                 "answer":answer.get("answer"),"parts":[p["type"] for p in answer.get("parts",[])]}
            if accepted:
                for part in answer.get("parts",[]):
                    if part["type"]=="proposal":
                        result=confirm(c,part["proposal"]).json()
                        row["receipt"]=result.get("receipt")
                        row["database_revision"]=c.runtime.read(c.principal).revision
                        restored=c.get("/api/conversations/"+c.action_conversation).json()
                        row["receipt_persisted"]=any(a.get("action_receipts") for a in restored["answers"])
            rows.append(row)
            Path(options.output).write_text(json.dumps({"model":"llama3.2:latest",
                "budget_seconds":options.timeout,"diagnostic":options.timeout>12,"sequential":True,"cases":rows},indent=2),encoding="utf-8")
            print(name, "PASS" if accepted else "LIMITED",row["latency_ms"],flush=True)
if __name__=="__main__":
    main()
