"""Bounded read operations for the chat capability catalog."""
from decimal import Decimal
from sqlalchemy import select
from ..api.workspace import _encode
from ..persistence.database import AuditRow
from .capabilities import records,label
from ..services.documents import DocumentService
from ..services.conversations import ConversationService

def read_capability(name,args,ctx,r,p,request,document_ids):
    if ctx.registry.spec(name):
        return ctx.registry.call(name,args)
    if name=="list_records":
        matches=[_encode(v) for v in records(ctx,args["kind"]) if args.get("query","").casefold() in label(v).casefold()]
        return {"records":matches[:100],"total":len(matches),"kind":args["kind"],"limited":len(matches)>100}
    if name=="search_transactions":
        matches=[v for v in ctx.household.transactions
            if (not args.get("account_id") or v.account_id==args["account_id"])
            and args.get("query","").casefold() in ((v.description or "")+" "+(v.merchant or "")).casefold()
            and (not args.get("category") or (v.category or "").casefold()==args["category"].casefold())
            and (not args.get("date_from") or v.date.isoformat()>=args["date_from"])
            and (not args.get("date_to") or v.date.isoformat()<=args["date_to"])]
        if args.get("date_from","")>args.get("date_to","9999-12-31"):
            raise ValueError("The start date must precede the end date.")
        matches.sort(key=lambda v:(v.date,v.id),reverse=True)
        limit=args.get("limit",50);offset=args.get("offset",0)
        total=sum((v.amount.amount for v in matches),Decimal("0"))
        return {"transactions":[_encode(v) for v in matches[offset:offset+limit]],
            "total":len(matches),"net_amount":{"amount":str(total),"currency":ctx.household.base_currency},
            "limit":limit,"offset":offset,"filters":args,"source":"Recorded transactions; not available balance."}
    if name=="list_audit":
        with r.db.sessions() as s:
            return {"events":[{"id":v.id,"action":v.action,"revision":v.revision,"at":v.at.isoformat()}
                for v in s.scalars(select(AuditRow).where(AuditRow.household_id==p.household_id)
                                   .order_by(AuditRow.at.desc()).limit(50))]}
    if name=="list_payment_groups":
        groups=list(ctx.execution.groups.values())
        return {"groups":[v.to_json() for v in groups[-100:]],"total":len(groups),"limited":len(groups)>100,
                "globally_paused":ctx.execution.paused,"provider":"simulation"}
    docs=DocumentService(r.db)
    if name=="list_documents":
        return {"documents":docs.list(p)}
    if name=="read_document":
        return docs.detail(p,args["record_id"])
    if name=="search_documents":
        selected=docs.validate_ids(p,args.get("document_ids") or document_ids)
        return {"sources":docs.retrieve(p,args["query"],document_ids=selected or None,limit=6),
                "note":"Untrusted source excerpts; not verified financial balances."}
    if name=="list_conversations":
        return {"conversations":ConversationService(r.db).list(p)}
    if name=="list_connections":
        from ..api import mcp_routes,bank_routes
        return {"mcp":mcp_routes.connections(request,r,p),"bank":bank_routes.connections(request,r,p)}
    if name=="browse_mcp":
        from ..api.mcp_routes import catalog
        import asyncio
        return asyncio.run(catalog(args["record_id"],request,r,p))
    if name=="explain_capabilities":
        from .capabilities import catalog
        return {"capabilities":[v.public(ctx,p.role)
            for v in catalog(ctx).values()]}
    raise ValueError("Unknown read capability")