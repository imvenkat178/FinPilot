"""Bank commands shared by forms and reviewed chat execution."""
from sqlalchemy import select
from ..persistence.database import BankConnectionRow, utcnow
from ..integrations.plaid import apply_accounts, apply_transactions
from ..runtime import RevisionConflict
from ..models import Verification
from ..services.auth import AuthError

def owned(session,p,connection_id,lock=False):
    q=select(BankConnectionRow).where(BankConnectionRow.id==connection_id,BankConnectionRow.household_id==p.household_id)
    row=session.scalar(q.with_for_update() if lock else q)
    if row is None:
        raise KeyError("Bank connection not found")
    return row

def active(client,row):
    client.require_configured()
    if row.status!="active" or not row.encrypted_access_token:
        raise RevisionConflict("This bank connection has been disconnected.")
    if row.environment!=client.config.environment:
        raise RevisionConflict("This connection belongs to a different provider environment.")

def sync(r,p,client,connection_id,expected=None):
    with r.db.sessions() as s:
        before=owned(s,p,connection_id)
        active(client,before)
        version,cursor=before.version,before.cursor
        token=client.decrypt(before.encrypted_access_token)
    accounts,institution_id,institution_name=client.account_snapshot(token)
    updates=client.fetch_updates(token,cursor)
    at=utcnow()
    with r.transaction(p,"bank.sync",expected) as ctx:
        row=owned(ctx.db_session,p,connection_id,True)
        active(client,row)
        if row.version!=version or row.cursor!=cursor:
            raise RevisionConflict("Another bank update finished first. Refresh and retry.")
        row.institution_id,row.institution_name=institution_id,institution_name
        apply_accounts(ctx.household,row,accounts,at)
        imported=apply_transactions(ctx.household,row,updates["changes"])
        row.cursor,row.version,row.last_synced_at=updates["cursor"],version+1,at
    return row,imported,ctx.revision

def disconnect(r,p,client,connection_id,expected=None,expected_version=None):
    with r.db.sessions() as s:
        before=owned(s,p,connection_id)
        if expected_version is not None and before.version!=expected_version:
            raise RevisionConflict("This bank connection changed. Review it again.")
        version=before.version
        if expected is not None and r.read(p).revision!=expected:
            raise RevisionConflict("Your workspace changed. Review it again.")
        if before.status=="disconnected":
            return before,r.read(p).revision
        active(client,before)
        token=client.decrypt(before.encrypted_access_token)
    client.remove(token)
    with r.transaction(p,"bank.disconnect",expected) as ctx:
        row=owned(ctx.db_session,p,connection_id,True)
        if row.version!=version:
            raise RevisionConflict("The bank connection changed while the provider was disconnecting. Check its status.")
        row.status,row.encrypted_access_token="disconnected",None
        row.disconnected_at,row.version=utcnow(),row.version+1
        for account_id in (row.account_mapping or {}).values():
            account=ctx.household.accounts.get(account_id)
            if account:
                account.connection_healthy=False
                account.connection_issue="Bank connection disconnected; saved records are retained."
                account.provenance.verification=Verification.STALE
    return row,ctx.revision