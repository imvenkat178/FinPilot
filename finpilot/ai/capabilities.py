"""Typed assistant capability catalog; model plans never acquire write authority."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re
from jsonschema import Draft202012Validator, FormatChecker
from ..api.workspace import _encode
from ..models import AccountType, PolicyPurpose, PolicyMethod, ReservePurpose, TxKind, BillOwner, GraceState, AuthorizationMode, PercentBase
from .input_safety import reject_credentials
from ..dates import Cadence, DayRule, BusinessDayRule

def obj(properties=None, required=()):
    return {"type":"object", "properties":properties or {}, "required":list(required), "additionalProperties":False}

def text(maximum=200, enum=None):
    out = {"type":"string","minLength":1,"maxLength":maximum}
    if enum is not None:
        out["enum"] = [v.value if hasattr(v,"value") else v for v in enum]
    return out

DECIMAL = {"type":"string", "pattern":r"^-?\d{1,16}(?:\.\d{1,12})?$", "maxLength":30,
           "description":"Exact decimal string. Rates use fractions: 5% is 0.05."}
BOOL = {"type":"boolean"}
DATE = {"type":"string","format":"date","maxLength":10}
ID = text(200)
def integer(low, high):
    return {"type":"integer","minimum":low,"maximum":high}

SCHEDULE = {"cadence":text(enum=Cadence),"day_of_month":integer(1,31),
    "second_day_of_month":integer(1,31),"day_rule":text(enum=DayRule),
    "business_day_rule":text(enum=BusinessDayRule),"end":DATE}
FIELDS = {
 "account": {
    **{k:text(160) for k in ("nickname","institution")},
    "mask":{"type":"string","pattern":r"^\d{0,4}$"},"type":text(enum=AccountType),
    "currency":text(3), "included_in_planning":BOOL, "as_of":DATE,
    **{k:DECIMAL for k in ("current","available","apy","minimum_balance","monthly_fee","apr","minimum_payment","escrow","mortgage_insurance","credit_limit","statement_balance","annual_fee","foreign_transaction_fee")},
    **{k:integer(1,31) for k in ("due_day","statement_close_day","payment_due_day")},
    "remaining_term_months":integer(1,1200),"grace_state":text(enum=GraceState)},
 "income": {**SCHEDULE,"record_type":text(enum=["source","event"]),"name":text(160),
    "net_amount":DECIMAL,"deposit_account_id":ID,"is_variable":BOOL,"reliability":DECIMAL,
    "next_date":DATE,"generate_events":BOOL,"source_id":ID,"expected_date":DATE,
    "expected_amount":DECIMAL,"received_date":DATE,"received_amount":DECIMAL},
 "bill": {**SCHEDULE,"name":text(160),"amount":DECIMAL,"due_date":DATE,
    "funding_account_id":ID,"payee_account_id":ID,"required":BOOL,"category":text(80),
    "execution_owner":text(enum=BillOwner),"amount_confirmed":BOOL,"autopay_confirmed":BOOL},
 "goal": {"name":text(160),"account_id":ID,"purpose":text(enum=ReservePurpose),
    "target":DECIMAL,"funded":DECIMAL,"target_date":DATE,"protected":BOOL},
 "rule": {**SCHEDULE,"name":text(160),"purpose":text(enum=PolicyPurpose),
    "method":text(enum=PolicyMethod),
    **{k:DECIMAL for k in ("amount","monthly_target","percent","target_balance","min_remaining_balance","fee_limit")},
    "percent_base":text(enum=PercentBase),
    "target_date":DATE,"anchor":DATE,
    **{k:ID for k in ("source_account_id","destination_account_id","destination_reserve_id","liability_id","bill_id")},
    "priority":integer(1,10000),"paused":BOOL,"allow_overfunding":BOOL,
    "eligible_income_source_ids":{"type":"array","items":ID,"maxItems":100}},
 "tax": {"tax_year":integer(2000,2200),"state":text(80),"filing_status":text(60),
    **{k:DECIMAL for k in ("federal_marginal","state_marginal","niit_rate")},
    "itemizes":BOOL,"niit_applies":BOOL},
}
for _kind, _fields in {
    "income":["received_amount","end"],
    "bill":["payee_account_id","end"],
    "goal":["target_date"],
    "rule":["target_date","monthly_target","target_balance","fee_limit","destination_reserve_id","liability_id","bill_id","end"],
    "account":["remaining_term_months"],
}.items():
    for _field in _fields:
        FIELDS[_kind][_field] = {**FIELDS[_kind][_field], "type":[FIELDS[_kind][_field]["type"],"null"]}
FIELDS["account"]["institution"] = {"type":"string","maxLength":160}
CREATE_REQUIRED = {"account":["nickname","type","current"],
    "income":["record_type"], "bill":["name","amount","due_date","funding_account_id","cadence"],
    "goal":["name","account_id","target"],"rule":["name","purpose","method","source_account_id","destination_account_id","cadence"],
    "tax":[]}
COLLECTIONS = {"account":"accounts","bill":"bills","goal":"reserves","rule":"policies",
               "income":"income_sources","transaction":"transactions","card":"cards","liability":"liabilities"}

class Clarification(ValueError):
    def __init__(self, message, fields=None, candidates=None):
        super().__init__(message)
        self.fields = fields or []
        self.candidates = candidates or []

@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    mode: str
    schema: dict
    entity: str = ""
    sample_only: bool = False

    def public(self, ctx, role=None):
        roles=["owner","approver"] if self.mode=="write" or self.name in {"sync_bank","disconnect_bank","connect_bank"} else ["owner","approver","viewer"]
        reason=("This workspace role cannot make financial changes." if role is not None and role not in roles else
                "Only available in sample workspaces." if self.sample_only and not ctx.household.payment_sandbox else None)
        return {"name":self.name, "description":self.description,"mode":self.mode,
            "input_schema":self.schema,"entity":self.entity,
            "authorization":{"scope":"private" if self.mode=="private" or self.entity in {"document","mcp","grant","conversation"} else "workspace",
                             "roles":roles},"review_required":self.mode in {"write","private","provider"},
            "result_format":{"read":"result","write":"proposal","private":"proposal","provider":"proposal","handoff":"handoff","navigate":"navigation"}[self.mode],
            "available":reason is None,"unavailable_reason":reason}

def catalog(ctx):
    result = {}
    def add(name, description, mode, schema=None, entity="", sample_only=False):
        result[name] = Capability(name,description,mode,schema or obj(),entity,sample_only)
    for name in ctx.registry.names():
        spec = ctx.registry.spec(name)
        if spec.read_only:
            add(name,spec.description,"read",spec.parameters)
    for kind, fields in FIELDS.items():
        for verb in (["update"] if kind=="tax" else ["create","update"]):
            properties = dict(fields)
            if verb=="update" and kind!="tax":
                properties["record_id"] = ID
            required = CREATE_REQUIRED[kind] if verb=="create" else ([] if kind=="tax" else ["record_id"])
            add(f"{verb}_{kind}",f"{verb.title()} {'an' if kind in {'account','income'} else 'a'} {kind} with a preview before saving.",
                "write",obj(properties,required),kind)
    for name in ("pause_rule","resume_rule","skip_rule"):
        add(name,name.replace("_"," ").title(),"write",obj({"record_id":ID},["record_id"]),"rule")
    add("authorize_rule","Review simulation-only rule authorization and cap.","write",
        obj({"record_id":ID,"per_run_cap":DECIMAL,"mode":text(enum=AuthorizationMode)},["record_id","per_run_cap"]),"rule")
    add("draft_bill_payment","Prepare a one-time bill payment draft; no submission.","write",
        obj({"record_id":ID,"occurrence_date":DATE},["record_id"]),"bill")
    add("build_payment_drafts","Prepare paycheck allocation payment drafts.","write",
        obj({"year":integer(1900,2200),"month":integer(1,12),"income_event_id":ID}))
    for name, entity in [("simulate_payment","group"),("recover_payment","leg")]:
        add(name,name.replace("_"," ").title()+" using the sample provider.","write",
            obj({"record_id":ID},["record_id"]),entity,True)
    for name in ("pause_execution","resume_execution"):
        add(name,name.replace("_"," ").title(),"write")
    add("correct_transaction","Correct a transaction description, category or kind. Amount/date/account are immutable.","write",
        obj({"record_id":ID,"description":text(500),"category":text(100),"kind":text(enum=TxKind)},["record_id"]),"transaction")
    add("import_transactions","Import an uploaded CSV attachment after exact row review. Requires a saved attachment; never generate CSV rows.","write",
        obj({"attachment_id":ID},["attachment_id"]))
    add("upload_csv","Upload a CSV in the attachment form to prepare a reviewed transaction import.","handoff",
        obj({"account_id":ID}))
    add("list_records","List source records and their IDs: accounts, income, bills, goals, rules, cards or liabilities.","read",
        obj({"kind":text(enum=list(COLLECTIONS)),"query":text(200)},["kind"]))
    add("search_transactions","Find and summarize transactions, with exact server totals and bounded pagination.","read",
        obj({"account_id":ID,"query":text(200),"category":text(100),"date_from":DATE,"date_to":DATE,
             "limit":integer(1,100),"offset":integer(0,1000000)}))
    add("list_audit","Show recent workspace changes.","read")
    add("list_payment_groups","Show payment drafts and sample execution status.","read")
    add("list_documents","List private document titles.","read")
    add("search_documents","Retrieve exact source passages from private documents.","read",
        obj({"query":text(3000),"document_ids":{"type":"array","items":ID,"maxItems":10}},["query"]))
    add("read_document","Open source excerpts for one document.","read",obj({"record_id":ID},["record_id"]),"document")
    add("list_conversations","Find your saved conversations.","read")
    add("list_connections","Show bank and MCP connection readiness without credentials.","read")
    add("browse_mcp","Browse approved remote tools and resources.","read",obj({"record_id":ID},["record_id"]),"mcp")
    for name,entity in [("delete_document","document"),("delete_conversation","conversation"),
                         ("disconnect_mcp","mcp"),("revoke_mcp_access","grant")]:
        add(name,name.replace("_"," ").title(),"private",obj({"record_id":ID},["record_id"]),entity)
    for name in ("sync_bank","disconnect_bank"):
        add(name,name.replace("_"," ").title(),"provider",obj({"record_id":ID},["record_id"]),"bank")
    add("import_mcp","Import an approved MCP tool/resource result into private documents.","provider",
        obj({"record_id":ID,"tool_name":text(128),"resource_uri":text(2000),
             "arguments":{"type":"object","maxProperties":30},"title":text(150)},["record_id"]),"mcp")
    for name in ("connect_bank","connect_mcp","create_mcp_access","upload_document","select_documents","new_conversation"):
        add(name,name.replace("_"," ").title()+" using the secure application control.","handoff")
    add("open_conversation","Resume a saved conversation.","handoff",obj({"record_id":ID},["record_id"]),"conversation")
    add("navigate","Open an application screen.","navigate",
        obj({"page":text(enum=["overview","accounts","paychecks","bills","cashflow","goals","debt","rules","protection","assistant"]),
             "account_id":ID},["page"]))
    add("explain_capabilities","Show everything available through chat.","read")
    return result

def records(ctx, kind):
    if kind == "group":
        return list(ctx.execution.groups.values())
    if kind == "leg":
        return [leg for group in ctx.execution.groups.values() for leg in group.legs]
    if kind == "income_event":
        return ctx.household.income_events
    value = getattr(ctx.household,COLLECTIONS.get(kind,kind),{})
    return list(value.values()) if isinstance(value,dict) else list(value)

def label(record):
    if isinstance(record,dict):
        return record.get("nickname") or record.get("name") or record.get("title") or record.get("description") or record.get("id","")
    return (getattr(record,"nickname","") or getattr(record,"name","") or
            getattr(record,"description","") or getattr(record,"id",""))

def resolve(value, choices, field="record_id"):
    if not isinstance(value,str) or not value.strip():
        raise Clarification("Choose the record you mean.",[field])
    values = [{"id":x.get("id"),"label":label(x)} if isinstance(x,dict) else
              {"id":x.id,"label":label(x)} for x in choices]
    exact = [x for x in values if x["id"]==value or x["label"].casefold()==value.casefold()]
    matches = exact or [x for x in values if value.casefold() in x["label"].casefold()]
    if len(matches) != 1:
        raise Clarification("Choose the exact record; the name did not identify one item.",[field],(matches or values)[:20])
    return matches[0]["id"]

def validate(cap, args):
    reject_credentials(args)
    errors = sorted(Draft202012Validator(cap.schema,format_checker=FormatChecker()).iter_errors(args),key=lambda x:str(x.path))
    if errors:
        error=errors[0]
        missing = [x for x in cap.schema.get("required",[]) if x not in args] if isinstance(args,dict) else []
        raise Clarification("Please provide valid inputs: "+error.message[:300],missing or [str(x) for x in error.path])
    if cap.name=="create_income":
        required = (["source_id","expected_date","expected_amount"] if args.get("record_type")=="event" else
                    ["name","net_amount","deposit_account_id","next_date","cadence"])
        missing = [x for x in required if x not in args]
        if missing:
            raise Clarification("Complete the income details before review.",missing)
    if cap.name.startswith("update_") and not (set(args)-{"record_id","record_type"}):
        raise Clarification("What would you like to change?",list(FIELDS.get(cap.entity,{})))
    if cap.name=="correct_transaction" and not (set(args)-{"record_id"}):
        raise Clarification("What transaction detail should change?",["category","description","kind"])
    return dict(args)

def canonicalize(cap,args,ctx,external_choices=None):
    args=validate(cap,args)
    if not cap.public(ctx)["available"]:
        raise ValueError("This operation is only available in sample workspaces.")
    if "record_id" in args and cap.entity:
        entity = "income_event" if cap.entity=="income" and args.get("record_type")=="event" else cap.entity
        choices=(external_choices or {}).get(entity)
        args["record_id"]=resolve(args["record_id"],choices if choices is not None else records(ctx,entity))
    references={"account_id":"account","funding_account_id":"account","payee_account_id":"account",
        "deposit_account_id":"account","source_account_id":"account","destination_account_id":"account",
        "source_id":"income","income_event_id":"income_event","card_id":"card","bill_id":"bill",
        "liability_id":"liability","destination_reserve_id":"goal"}
    for field,kind in references.items():
        if field in args and args[field] is not None:
            args[field]=resolve(args[field],records(ctx,kind),field)
    if "eligible_income_source_ids" in args:
        args["eligible_income_source_ids"]=[resolve(v,records(ctx,"income"),"eligible_income_source_ids") for v in args["eligible_income_source_ids"]]
    if "document_ids" in args and external_choices is not None:
        args["document_ids"]=[resolve(v,external_choices.get("document",[]),"document_ids") for v in args["document_ids"]]
    if cap.name=="plan_coverage_remedy":
        buckets=ctx.registry.get_deposit_coverage()["buckets"]
        choices=list({b["institution_id"]:{"id":b["institution_id"],"name":b["institution_name"]} for b in buckets}.values())
        for field in ("source_institution","destination_institution"):
            if field in args:
                args[field]=resolve(args[field],choices,field)
    return args
