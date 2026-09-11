import { S, $, esc, money, human, fullDate, account, accountName, api, chip, note, row, evidence, detailHeading, link, icon } from './core.js';

let hooks = {}, generation = 0;
const pendingStates = new Set(['draft', 'awaiting_authorization', 'authorized', 'scheduled', 'validating']);
const warningStates = new Set(['failed', 'returned', 'outcome_unknown']);
const isSandbox = () => S.workspace?.household?.payment_sandbox === true;
const simulationNotice = () => isSandbox() ? 'Sample workspace · simulation only. No live bank transfer is sent. Simulations change only this sample workspace’s fictional balances and payment records.' : 'Simulation is available in sample workspaces. Your recorded balances are not changed by this app.';
export const executionButton = (label, target, primary = false) => `<button type="button" class="button ${primary ? 'primary' : ''}" data-execution="${esc(target)}">${icon('shield')}${esc(label)}</button>`;
export function initializeExecution(options) { hooks = options; }
function show(title, subtitle, body) {
  const token = ++generation;
  if ($('assistant-dialog')?.open) $('assistant-dialog').close();
  $('detail-body').innerHTML = `<div data-execution-view="${token}">${detailHeading(title, subtitle)}${body}</div>`;
  const dialog = $('detail-dialog');
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop = 0;
  dialog.querySelector('input:not([type="hidden"]),select,button')?.focus();
  return token;
}
const current = token => token === generation && $('detail-dialog')?.open && $('detail-body').innerHTML.includes(`data-execution-view="${token}"`);
const confirmation = text => `<label class="checkbox-line"><input name="confirmed" type="checkbox" required> ${esc(text)}</label>`;
const form = (kind, id, body, label, disabled = false) => `<form class="stack-form section-gap" data-execution-form="${kind}" data-id="${esc(id || '')}">${body}<p class="form-result" role="status"></p><button type="submit" class="button primary" ${disabled ? 'disabled' : ''}>${esc(label)}</button></form>`;
const destination = leg => account(leg.destination)?.nickname || leg.destination || 'External payee';
const stateChip = state => chip(human(state), warningStates.has(state) ? 'orange' : state === 'reconciled' ? 'green' : 'neutral');

function authorization(id) {
  const policy = S.workspace.policies.find(p => p.id === id);
  if (!policy) throw new Error('This rule is no longer available. Refresh your workspace.');
  if (!isSandbox()) {
    show('Payment simulation', policy.name, note(simulationNotice()) + link('Open recurring rules', 'rules'));
    return;
  }
  const bill = S.workspace.bills.find(b => b.id === policy.bill_id);
  const cap = policy.mandate?.per_run_cap?.amount ?? policy.amount?.amount ?? policy.monthly_target?.amount ?? '';
  show('Authorize a simulated rule', policy.name,
    row('Funding account', accountName(policy.source_account_id)) +
    row('Destination', bill?.name || accountName(policy.destination_account_id)) +
    row('Purpose', human(policy.purpose)) + note(simulationNotice()) +
    form('authorize', id,
      '<label>Authorization<select name="mode"><option value="one_time">One simulated payment</option><option value="standing">Future eligible simulated payments</option></select></label>' +
      `<label>Maximum per payment (${esc(S.workspace.household.base_currency)})<input name="per_run_cap" type="number" inputmode="decimal" min="0.01" max="1000000000" step="0.01" value="${esc(cap)}" required></label>` +
      note('This replaces the authority for this rule. Existing drafts must be rebuilt. Funding, timing, account capability, and payment checks still apply. You can pause the rule or all execution at any time.') +
      confirmation('I authorize this rule under the selected scope and cap for the simulated provider.'), 'Save simulation authorization'));
}

function preflightContent(result) {
  if (!result) return note('Current checks are unavailable. Refresh this draft before running a simulation.');
  return chip(result.ok ? 'Checks passed' : 'Needs review', result.ok ? 'green' : 'orange') +
    (result.blockers || []).map(note).join('') +
    `<details class="evidence"><summary>View every check</summary>${Object.entries(result.checks || {}).map(([name, passed]) => row(human(name), passed ? 'Passed' : 'Needs review')).join('')}</details>`;
}
function legContent(leg, preflight) {
  const pending = pendingStates.has(leg.state);
  const policy = S.workspace.policies.find(p => p.id === leg.policy_id);
  return `<section class="payment-group section-gap"><h3>${esc(destination(leg))}</h3>${stateChip(leg.state)}${row('Amount', money(leg.amount, true))}${row('From', accountName(leg.source_account_id))}${row('Scheduled for', leg.scheduled_for ? fullDate(leg.scheduled_for) : 'No date recorded')}${row('Payment type', human(leg.kind))}${row('Fee', money(leg.fee))}${row('Priority', leg.optional ? 'Optional allocation' : 'Required allocation')}${leg.failure_reason ? note(leg.failure_reason) : ''}${pending ? preflightContent(preflight) : ''}${leg.application?.explanation ? note(leg.application.explanation) : ''}${leg.state === 'outcome_unknown' ? note('The provider outcome is unknown. Check the original payment status before considering any replacement.') + executionButton('Check original payment status', `recover:${leg.id}:${leg.group_id}`) : ''}${pending && policy && isSandbox() ? `<div class="detail-actions">${executionButton('Review rule authorization', `authorize:${policy.id}`)}<button class="button" data-detail="rule:${esc(policy.id)}">Open rule</button></div>` : ''}${evidence('Payment record and audit trail', leg)}</section>`;
}
async function groupReview(id, message = '') {
  const token = show('Review payment simulation', 'Loading current payment state and checks.', note('Checking the draft…'));
  try {
    const result = await api(`/api/execution/groups/${encodeURIComponent(id)}`);
    if (!current(token)) return;
    const group = result.group, legs = group.detail || [], checks = result.preflight || {};
    const pending = legs.filter(l => pendingStates.has(l.state));
    const processing = legs.some(l => l.state === 'processing');
    const unknown = legs.some(l => l.state === 'outcome_unknown');
    const canRun = isSandbox() && !result.globally_paused && !unknown && (pending.length > 0 || processing) && pending.every(l => checks[l.id]?.ok === true);
    let controls = '';
    if (isSandbox() && (pending.length || processing)) {
      controls = form('simulate', id,
        note('Each payment is tracked separately. A later check or provider failure can produce a partial result; review every payment state afterward.') +
        (result.globally_paused ? note('All execution is paused. Resume future execution before simulating eligible drafts.') + executionButton('Review resume', 'resume') : '') +
        (!canRun ? note('Resolve the items above and refresh checks before continuing. Changes to a rule or its authority require a new draft.') : '') +
        confirmation('I understand this changes only the sample workspace’s fictional balances and records. Run this reviewed payment simulation.'), 'Run payment simulation', !canRun);
    }
    show(group.label || 'Payment group', 'Review the source, destination, and current state of every payment.',
      note(simulationNotice()) + (message ? note(message) : '') +
      (legs.length ? legs.map(leg => legContent(leg, checks[leg.id])).join('') : note('This group contains no payable allocations. Review your paycheck plan and recurring rules.')) + controls +
      `<div class="detail-actions">${executionButton('Refresh checks', `group:${id}`)}${link('Payment activity', 'bills/activity')}</div>`);
  } catch (error) {
    if (current(token)) show('Payment review unavailable', 'Current payment status could not be loaded.', note(error.message) + executionButton('Try again', `group:${id}`));
  }
}
async function buildPaycheck(id) {
  const income = S.workspace.income_events.find(e => e.id === id);
  if (!income) throw new Error('This paycheck is no longer available. Refresh your workspace.');
  const payDate = income.received_date || income.expected_date;
  const query = new URLSearchParams({income_event_id:id});
  if (payDate) { query.set('year', payDate.slice(0,4)); query.set('month', String(Number(payDate.slice(5,7)))); }
  const token = show('Prepare paycheck drafts', 'Checking this paycheck’s planned allocations.', note('Preparing reviewable drafts. No payment is being submitted.'));
  try {
    const result = await api(`/api/execution/build?${query}`, {method:'POST'});
    await hooks.load?.();
    if (!current(token)) return;
    const groups = result.groups || [];
    if (groups.length === 1) return groupReview(groups[0].group_id);
    show('Paycheck drafts', 'No payment has been submitted.', note(result.note || simulationNotice()) +
      (groups.length ? groups.map(g => `<div class="item-row"><strong>${esc(g.label || 'Payment group')}</strong>${executionButton('Review draft', `group:${g.group_id}`)}</div>`).join('') : note('No draft allocations are available for this paycheck. Review its income and recurring rules.')) + link('Open paycheck plan', 'paychecks'));
  } catch (error) {
    if (current(token)) show('Could not prepare drafts', 'No payment has been submitted.', note(error.message) + executionButton('Try again', `build:${id}`));
  }
}
function globalControl(resume) {
  show(resume ? 'Resume future simulation' : 'Pause all execution', 'Applies to this workspace’s payment simulator.',
    note(resume ? 'Future eligible runs may resume. Canceled drafts stay canceled and must be reviewed and rebuilt.' : 'This stops future eligible runs and cancels drafts that have not been submitted. Payments already submitted cannot be recalled with this control.') +
    form(resume ? 'resume' : 'pause', '', confirmation(resume ? 'Resume future eligible simulated payments.' : 'Pause future execution and cancel unsubmitted drafts.'), resume ? 'Resume future simulation' : 'Pause all execution'));
}
async function recover(legId, groupId) {
  const token = show('Check payment status', 'Recovering the original simulated payment identity.', note('Checking status without submitting a replacement…'));
  try {
    const result = await api(`/api/execution/recover/${encodeURIComponent(legId)}`, {method:'POST'});
    await hooks.load?.();
    if (current(token)) await groupReview(groupId || result.leg.group_id, result.action);
  } catch (error) {
    if (current(token)) show('Status check unavailable', 'No replacement has been submitted.', note(error.message) + executionButton('Review payment', `group:${groupId}`));
  }
}
export async function handleExecutionClick(el) {
  const target = el.dataset.execution;
  if (!target) return false;
  const [kind, id, groupId] = target.split(':');
  try {
    if (kind === 'authorize') authorization(id);
    else if (kind === 'build') await buildPaycheck(id);
    else if (kind === 'group') await groupReview(id);
    else if (kind === 'recover') await recover(id, groupId);
    else if (kind === 'pause' || kind === 'resume') globalControl(kind === 'resume');
    else throw new Error('This payment action is unavailable.');
  } catch (error) { hooks.toast?.(error.message, true); }
  return true;
}
export async function handleExecutionSubmit(element) {
  const kind = element.dataset.executionForm;
  if (!kind) return false;
  if (element.dataset.submitting === 'true') return true;
  const data = new FormData(element), id = element.dataset.id;
  const status = element.querySelector('.form-result'), button = element.querySelector('button[type="submit"]');
  if (!data.has('confirmed')) { status.textContent = 'Review the confirmation before continuing.'; return true; }
  if (!element.reportValidity()) return true;
  const token = generation;
  element.dataset.submitting = 'true';
  button.disabled = true;
  status.textContent = 'Saving…';
  try {
    if ((kind === 'authorize' || kind === 'simulate') && !isSandbox()) throw new Error(simulationNotice());
    if (kind === 'authorize') {
      const result = await api(`/api/policies/${encodeURIComponent(id)}/authorize`, {method:'POST',body:{mode:String(data.get('mode')),per_run_cap:String(data.get('per_run_cap')).trim()}});
      await hooks.load?.();
      if (current(token)) show('Simulation authorization saved', 'Applies only to the selected recurring rule.', row('Authorization', human(result.mode)) + row('Maximum per payment', money(result.per_run_cap, true)) + note(result.note) + note('Rebuild existing drafts to use the new authorization. No payment has been submitted.') + `<div class="detail-actions"><button class="button" data-detail="rule:${esc(id)}">Open rule</button>${link('Paycheck plan', 'paychecks')}${link('Bills and drafts', 'bills')}</div>`);
      hooks.toast?.('Simulation authorization saved.');
    } else if (kind === 'simulate') {
      await api(`/api/execution/run/${encodeURIComponent(id)}`, {method:'POST',body:{confirm_simulation:true}});
      await hooks.load?.();
      if (current(token)) await groupReview(id, 'Simulation finished. Review each payment below; a group can contain different outcomes.');
    } else if (kind === 'pause' || kind === 'resume') {
      const result = await api(`/api/execution/${kind === 'pause' ? 'pause-all' : 'resume'}`, {method:'POST'});
      await hooks.load?.();
      if (current(token)) show(kind === 'pause' ? 'Execution paused' : 'Future simulation resumed', 'Workspace payment controls updated.', note(result.note) + (kind === 'pause' ? row('Unsubmitted drafts canceled', result.canceled?.length || 0) + row('Already submitted', result.already_sent_cannot_be_stopped?.length || 0) : '') + `<div class="detail-actions">${executionButton(kind === 'pause' ? 'Review resume' : 'Review pause', kind === 'pause' ? 'resume' : 'pause')}${link('Payment activity', 'bills/activity')}</div>`);
    } else throw new Error('This payment form is unavailable.');
  } catch (error) {
    if (current(token)) status.textContent = error.message;
    else hooks.toast?.(error.message, true);
  } finally { button.disabled = false; delete element.dataset.submitting; }
  return true;
}
