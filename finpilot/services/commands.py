"""Financial commands shared by HTTP forms and reviewed assistant proposals.

Commands only touch the supplied workspace snapshot. The caller owns the
transaction; running them on a detached snapshot produces an exact preview.
"""
from datetime import date
from decimal import Decimal
from ..models import AuthorizationMode, Mandate, now
from ..money import Money
from ..engine.allocator import AllocationEngine
from ..execution.engine import LegState
from .workspace import WorkspaceService


def execute(ctx, name, args):
    args = dict(args)
    service = WorkspaceService(ctx.household, ctx.tax)
    if name.startswith(('create_', 'update_')) and name.split('_', 1)[1] in {
            'account', 'income', 'bill', 'goal', 'rule', 'tax'}:
        verb, kind = name.split('_', 1)
        record_id = args.pop('record_id', None)
        if verb == 'update' and kind != 'tax' and not record_id:
            raise ValueError('Select the record to update.')
        if verb == 'create' and record_id:
            raise ValueError('A new record cannot replace an existing record.')
        kinds = {'account':'accounts', 'income':'income', 'bill':'bills',
                 'goal':'reserves', 'rule':'policies', 'tax':'tax'}
        return service.upsert(kinds[kind], args, record_id)
    if name == 'correct_transaction':
        return service.update_transaction(args.pop('record_id'), args)
    if name == 'import_transactions':
        return service.import_transactions(ctx.csv_attachments[args['attachment_id']])
    if name in {'pause_rule', 'resume_rule', 'skip_rule', 'authorize_rule'}:
        policy = ctx.household.policies[args['record_id']]
        if name == 'skip_rule':
            result = ctx.registry.skip_next_occurrence(policy.id)
        elif name == 'authorize_rule':
            cap = Decimal(args['per_run_cap'])
            if not cap.is_finite() or not 0 < cap <= 1000000000:
                raise ValueError('Authorization cap must be positive and bounded.')
            mandate = Mandate(entity_id=policy.entity_id, jurisdiction=ctx.household.jurisdiction)
            mandate.mode = AuthorizationMode(args.get('mode', 'standing'))
            # The authenticated executor supplies the actor, never model input.
            mandate.authorized_by = getattr(ctx, 'actor_id', '')
            mandate.authorized_at = now()
            mandate.per_run_cap = Money(cap, ctx.household.base_currency)
            policy.mandate = mandate
            result = {'policy': policy.id, 'mode': mandate.mode.value, 'active': mandate.active,
                      'authorized_by': mandate.authorized_by, 'per_run_cap': mandate.per_run_cap.to_json(),
                      'note': 'Authority is scoped to this saved rule in the simulated payment provider.'}
        else:
            policy.paused = name == 'pause_rule'
            result = {'policy': policy.id, 'paused': policy.paused}
    elif name == 'draft_bill_payment':
        if args['record_id'] not in ctx.household.bills:
            raise KeyError('Bill not found')
        result = ctx.registry.pay_bill_once(args['record_id'], occurrence_date=args.get('occurrence_date'))
    elif name == 'build_payment_drafts':
        hh = ctx.household
        _, runs = AllocationEngine(hh).allocate_month(args.get('year') or hh.as_of.year,
                                                    args.get('month') or hh.as_of.month)
        if args.get('income_event_id'):
            runs = [x for x in runs if x.income_event_id == args['income_event_id']]
            if not runs:
                raise KeyError('Paycheck not found')
        result = {'groups': [ctx.execution.build_group_from_allocation(x).to_json() for x in runs],
                  'note': 'Payment drafts only. Nothing has been submitted.'}
    elif name in {'simulate_payment', 'recover_payment'}:
        if not ctx.household.payment_sandbox:
            raise ValueError('Payment simulation is available only in sample workspaces. No real money is moved.')
        if name == 'simulate_payment':
            result = ctx.execution.run_group(ctx.execution.groups[args['record_id']], settle=True)
        else:
            leg = ctx.execution._find_leg(args['record_id'])
            if leg is None:
                raise KeyError('Payment not found')
            if leg.state == LegState.OUTCOME_UNKNOWN:
                ctx.execution.recover_unknown(leg)
            result = {'leg': leg.to_json(), 'action': 'Checked the original provider identity; no replacement was submitted.'}
    elif name == 'pause_execution':
        result = ctx.execution.pause_all()
    elif name == 'resume_execution':
        ctx.execution.paused = False
        result = {'globally_paused': False, 'note': 'Future eligible runs may resume. Canceled drafts remain canceled.'}
    else:
        raise ValueError('Unknown financial command')
    if 'error' in result:
        raise ValueError(result['error'])
    return result
