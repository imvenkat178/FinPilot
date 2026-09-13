"""Small, request-specific model schemas over the authoritative capability catalog.

This prepares a constrained interpretation task. It never supplies financial
values or confirms an operation, and full command validation still runs later.
"""
from copy import deepcopy
from decimal import Decimal
import re
from .capabilities import obj, records, label, Clarification

REFERENCES = {
    "account_id":"account", "funding_account_id":"account", "payee_account_id":"account",
    "deposit_account_id":"account", "source_account_id":"account", "destination_account_id":"account",
    "source_id":"income", "income_event_id":"income_event", "card_id":"card", "bill_id":"bill",
    "liability_id":"liability", "destination_reserve_id":"goal", "leg_id":"leg",
}
RATE_FIELDS = {"federal_marginal", "state_marginal", "niit_rate", "apr", "apy", "foreign_transaction_fee", "percent", "reliability", "destination_apy", "card_rate", "processing_fee_rate", "direct_rate", "portal_rate", "advance_rate", "reduced_advance_rate", "decline"}
CLUES = {
    "name":r"\b(name|rename|call|called)\b", "nickname":r"\b(name|nickname|rename|call|called)\b",
    "current":r"\b(balance|holding|holds)\b", "net_amount":r"\b(net|amount|paycheck)\b",
    "amount":r"\b(amount|fixed)\b|\bto \$?\d", "federal_marginal":r"\bfederal\b",
    "state_marginal":r"\bstate (?:tax|marginal|rate)\b", "apr":r"\bapr|interest rate\b",
    "apy":r"\bapy|yield\b", "record_type":r"\b(source|event)\b",
    "cadence":r"\b(once|weekly|biweekly|monthly|quarterly|yearly|annual|semi.monthly|on.income|each income|cadence)\b",
    "query":r"\b(matching|for|at|containing|named|called)\b",
    "category":r"\b(categor|recategor)|\b(dining|groceries|travel|gas|subscriptions)\b",
    "description":r"\bdescription\b", "kind":r"\bkind\b",
    "days":r"\b(days?|weeks?|fortnight)\b", "horizon_days":r"\b(days?|weeks?|horizon)\b",
    "hold_days":r"\b(days?|held|hold)\b", "months":r"\bmonths?\b",
    "year":r"\b20\d{2}\b|\byear\b", "month":r"\bmonth|january|february|march|april|may|june|july|august|september|october|november|december\b",
    "extra_payment":r"\bextra\b", "extra_monthly":r"\bextra|monthly principal\b",
    "use_worked_example":r"\b(worked|specification|fixture)\b",
    "lump_sum":r"\blump\b", "program_fee_per_year":r"\bfee\b",
    "processing_fee_rate":r"\b(processing|fee)\b", "card_rate":r"\b(rewards?|cashback|cash back)\b",
    "direct_rate":r"\bdirect.{0,30}(rate|reward)|(?:rate|reward).{0,30}direct\b",
    "portal_rate":r"\bportal.{0,30}(rate|reward)|(?:rate|reward).{0,30}portal\b",
    "channel":r"\b(online|in.store|channel)\b", "foreign":r"\b(foreign|international|abroad)\b",
    "mcc_certain":r"\bmcc|merchant code\b", "merchant":r"\bmerchant\b",
    "payment":r"\bpayment\b", "destination_apy":r"\bapy|yield\b", "transfer_fee":r"\bfee\b",
    "topic":r"\b(for|about|regarding|limitations|boundary|boundaries)\b",
    "occurrence_date":r"\b\d{4}-\d{2}-\d{2}\b|\b(tomorrow|today|occurrence date)\b",
    "mode":r"\b(recurring|one_time|one.time authorization|authorization mode)\b",
    "tool_name":r"\b(tool|statement)\b", "resource_uri":r"\bresource|[a-z]+://",
    "arguments":r"\barguments?\b|\{", "title":r"\btitle\b",
    "limit":r"\b(limit|first|last|top)\b", "offset":r"\b(offset|skip|next page)\b",
    "date_from":r"\b(since|from|after|between)\b.*\d", "date_to":r"\b(until|before|between|through)\b.*\d",
}

def clauses(question):
    return re.split(r"(?:\b(?:and|then|also)\s+|[;,]\s*)(?=(?:show|find|search|list|explain|update|set|create|add|compare|open|delete|remove|refresh|import|pause|resume)\b)", question, flags=re.I)

def entity_choices(ctx, external, kinds=None):
    data={kind:[{"id":v.id,"name":label(v)} for v in records(ctx,kind)]
          for kind in ("account","bill","income","goal","rule","transaction","card","liability","income_event","group","leg") if kinds is None or kind in kinds}
    data.update({kind:[{"id":v["id"],"name":label(v),**({"read_tools":v["read_tools"]} if "read_tools" in v else {})} for v in rows] for kind,rows in external.items() if kinds is None or kind in kinds})
    return data

def matching_choices(rows, question, viewing):
    words=set(re.findall(r"[a-z0-9]+",question.casefold()))-{"the","a","an","my","for","to","of","from","in","and","with","payment","account","bill","rule","source","report"}
    exact=[];partial=[]
    for row in rows:
        name=row["name"].casefold()
        # File extensions are not usually part of a user's document reference.
        stem=re.sub(r"\.(?:txt|pdf|md)$","",name)
        if row["id"] in question or stem and re.search(r"(?<!\w)"+re.escape(stem)+r"(?!\w)",question,re.I):
            exact.append(row)
        elif words & set(re.findall(r"[a-z0-9]+",name)) or row["id"]==viewing.get("account_id"):
            partial.append(row)
    return (exact or partial)[:8]

def input_schema(cap, question, entities, viewing):
    properties=cap.schema.get("properties",{})
    required=list(cap.schema.get("required",[]))
    selected=set(required)
    field_question=question
    for name in sorted({row["name"] for rows in entities.values() for row in rows if row["name"]},key=len,reverse=True):
        field_question=re.sub(r"(?<!\w)"+re.escape(name)+r"(?!\w)","[record]",field_question,flags=re.I)
    low=field_question.lower()
    if cap.name=="create_income":
        event=bool(re.search(r"\bevent\b",low))
        required += (["source_id","expected_date","expected_amount"] if event else
                     ["name","net_amount","deposit_account_id","next_date","cadence"])
        selected.update(required)
    for key in properties:
        kind=cap.entity if key=="record_id" else REFERENCES.get(key)
        if cap.mode in {"read","navigate","handoff"} and kind and matching_choices(entities.get(kind,[]),question,viewing):
            selected.add(key)
        expression=CLUES.get(key,r"\b"+re.escape(key.replace("_"," "))+r"\b")
        if re.search(expression,low):
            selected.add(key)
    # The typed model should emit only explicit changes, never fill all slots
    # with defaults. Unknown wording retains the update fields for clarification.
    if cap.name.startswith("update_") and not selected-set(required):
        selected.update(properties)
    if cap.name=="create_income":
        selected -= ({"source_id","expected_date","expected_amount","received_date","received_amount"} if not event else
                     {"name","net_amount","deposit_account_id","next_date","cadence"})
    compact={}
    for key,rule in properties.items():
        if key not in selected:
            continue
        rule=deepcopy(rule)
        rule.pop("description",None)
        if rule.get("enum"):
            written=[value for value in rule["enum"] if isinstance(value,str) and
                     re.search(r"(?<!\w)"+re.escape(value).replace("_",r"[ _-]")+r"(?!\w)",field_question,re.I)]
            if key=="cadence" and re.search(r"\bon (?:each |every )?income\b",question,re.I):
                written.append("on_income")
            if written:
                rule["enum"]=list(dict.fromkeys(written))
        kind=cap.entity if key=="record_id" else REFERENCES.get(key)
        if kind:
            if kind=="income" and key=="record_id" and re.search(r"\bevent\b",low):
                kind="income_event"
            matches=matching_choices(entities.get(kind,[]),question,viewing)
            if matches:
                # Names save output tokens, then canonicalize against fresh,
                # tenant-owned records. Explicit identifiers remain available.
                rule["enum"]=list(dict.fromkeys([value for v in matches for value in (re.sub(r"\.(?:txt|pdf|md)$","",v["name"],flags=re.I) if kind=="document" else v["name"],v["name"],v["id"])]))
        if "pattern" in rule and "decimal" in properties[key].get("description","").lower():
            numbers=numeric_values(field_question,rate=key in RATE_FIELDS)
            if numbers:
                rule["enum"]=numbers
                if isinstance(rule.get("type"),list) and "null" in rule["type"]:
                    rule["enum"].append(None)

        if key in RATE_FIELDS and rule.get("type") == "number":
            values=numeric_values(field_question,rate=True)
            if values:
                rule["enum"]=[float(Decimal(value)) for value in values]

        if cap.name=="create_income" and key=="record_type":
            rule["enum"]=["event" if re.search(r"\bevent\b",low) else "source"]
        if cap.name=="import_mcp" and key=="tool_name":
            tools=[tool for row in matching_choices(entities.get("mcp",[]),question,viewing) for tool in row.get("read_tools",[])]
            if tools:
                rule["enum"]=list(dict.fromkeys(tools))
        if key in {"name","nickname"} and cap.name.startswith("update_"):
            rename=requested_rename(question,entities.get(cap.entity,[]),cap.entity)
            if rename:
                rule["enum"]=[rename["value"]]
        if key=="query" and cap.name in {"search_documents","search_transactions"}:
            search=re.fullmatch(r"(?:find|search|show|list) (?:my )?(?:documents|transactions) (?:for|matching|at|with) (.+?)[.!?]?",question.strip(),re.I)
            if search:
                rule["enum"]=[search[1].strip('"')]
        if key=="topic":
            topic=re.search(r"\b(?:for|about|regarding)\s+(.+?)[.!?]?$",question,re.I)
            if topic:
                rule["enum"]=[topic[1].strip()]
        if key in {"name","nickname"} and cap.name.startswith("create_"):
            new_name=requested_name(question,cap.entity)
            if new_name:
                rule["enum"]=[new_name]
        compact[key]=rule
    # Place required arguments first. Some local grammar decoders preserve
    # schema field order, so emitting an optional field must not skip cadence.
    compact={key:compact[key] for key in dict.fromkeys(required+list(compact)) if key in compact}
    return obj(compact,[key for key in required if key in compact])

def rate_facts(question):
    return [{"written":m[0],"fraction":format(Decimal(m[1])/100,"f")}
            for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s*(?:%|percent\b)",question,re.I)]


def numeric_values(question,rate=False):
    # Derive candidates from the user's text, never from stored balances or
    # model defaults. Dates/record IDs must not become monetary amounts.
    text=re.sub(r"\b\d{4}-\d{2}-\d{2}\b","",question)
    values=[]
    for match in re.finditer(r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)(?:\s*(%|percent\b))?(?![\w.])",text,re.I):
        value=Decimal(match[1].replace(",",""))
        if rate and match[2]:
            value/=100
        elif rate and not 0<=value<=1:
            continue
        values.append(format(value,"f"))
    return list(dict.fromkeys(values))[:24]


def requested_name(question,kind):
    quoted=re.search(r"\b(?:named|called)\s+([\"'])(.+?)\1",question,re.I)
    if quoted:
        return quoted[2]
    endings=r"(?=,|:|\s+(?:with|holding|targeting|using|in|for|amount|net|starting|due|funded|deposited|purpose|source|from)\b|$)"
    patterns=[r"\b(?:named|called)\s+[\"']?(.+?)[\"']?"+endings,
              r"^\s*(?:please\s+)?(?:add|create)\s+(.+?)\s+as\s+(?:a|an)\b",
              r"^\s*(?:please\s+)?(?:add|create)\s+"+re.escape(kind)+r"\s+(.+?)"+endings,
              r"^\s*(?:please\s+)?(?:add|create|set up)\s+(?:a\s+)?(?:(?:monthly|weekly|quarterly|annual|yearly|once|one-time)\s+)?(.+?)\s+(?:as a savings goal|as a goal|goal|rule|bill|income)\b"]
    for pattern in patterns:
        match=re.search(pattern,question,re.I)
        if match:
            name=match[1].strip(" \"'")
            # Avoid turning descriptive creation prefixes into names.
            if name and name.lower() not in {"new","a","an"} and not re.match(r"^(?:(?:a|an) )?(?:monthly|checking|manual|savings|income|weekly|source)\b",name,re.I):
                return name
    return None


def extraction_schema(schema):
    """Required but unknown inputs use null and become server clarifications."""
    result=deepcopy(schema)
    for key in result["required"]:
        rule=result["properties"][key]
        types=rule.get("type", "string")
        rule["type"]=list(dict.fromkeys((types if isinstance(types,list) else [types])+["null"]))
        if "enum" in rule and None not in rule["enum"]:
            rule["enum"].append(None)
    return result


def requested_rename(question,choices,kind):
    if re.search(r"\b(create|add|set up|setup)\b",question,re.I):
        return None
    if not re.search(r"\b(rename|name|nickname|call|called)\b",question,re.I):
        return None
    matches=[]
    for row in choices:
        found=re.search(r"(?<!\w)"+re.escape(row["name"])+r"(?!\w)",question,re.I)
        if not found:
            continue
        tail=question[found.end():].strip(" :\"'")
        tail=re.sub(r"^(?:account|bill|goal|rule|income)\s+","",tail,flags=re.I)
        tail=re.sub(r"^(?:nickname|name)\s+","",tail,flags=re.I)
        tail=re.sub(r"^(?:to be called|to|should be|as)\s+","",tail,flags=re.I)
        if tail and not re.search(r"\b(?:and|then|also)\s+(?:change|set|update|create|add)\b",tail,re.I):
            matches.append({"record_id":row["id"],"value":tail.strip(" \"'.!?")})
    return matches[0] if len(matches)==1 else None


def require_explicit_duplicate_choices(cap,args,question,ctx,external):
    """A guessed internal ID cannot disambiguate identical visible names."""
    for field,value in args.items():
        kind=cap.entity if field=="record_id" else REFERENCES.get(field)
        if not kind or not isinstance(value,str):
            continue
        if re.search(r"(?<![\w-])"+re.escape(value)+r"(?![\w-])",question):
            continue
        choices=external.get(kind)
        if choices is None:
            choices=records(ctx,kind)
        candidates=[{"id":row["id"] if isinstance(row,dict) else row.id,"label":label(row)} for row in choices]
        target=next((row for row in candidates if row["id"]==value),None)
        if target:
            duplicates=[row for row in candidates if row["label"].casefold()==target["label"].casefold()]
            if len(duplicates)>1:
                raise Clarification("More than one record has this name. Choose the exact record before review.",[field],duplicates[:20])
