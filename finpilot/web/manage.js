import { S, $, esc, money, human, dateLabel, fullDate, api, accountType, icon, table, cells, empty, note } from './core.js';

let hooks = {}, csvStage = null, transactionRows = [], historyAccount = '', historyOffset = 0, historyQuery = '', viewGeneration = 0, captureForm = null;
const amount = (v, fallback = '') => v?.amount ?? v ?? fallback;
const currentDate = () => S.data?.as_of || new Date().toISOString().slice(0, 10);
const input = (name, label, value = '', type = 'text', extra = '') => `<label>${esc(label)}<input name="${esc(name)}" type="${type}" value="${esc(value)}" ${extra}></label>`;
const select = (name, label, value, options, extra = '') => `<label>${esc(label)}<select name="${esc(name)}" ${extra}>${options.map(([id, text]) => `<option value="${esc(id)}" ${String(id) === String(value) ? 'selected' : ''}>${esc(text)}</option>`).join('')}</select></label>`;
const checkbox = (name, label, checked) => `<label class="checkbox-line"><input type="checkbox" name="${esc(name)}" ${checked ? 'checked' : ''}> ${esc(label)}</label>`;
const number = (name, label, value = '', extra = '') => input(name, label, amount(value), 'number', `step="0.01" min="0" ${extra}`);
function decimalShift(value, places) {
  const match = String(value || '0').match(/^([+-]?)(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
  if (!match) return String(value || '0');
  const digits = match[2] + (match[3] || '');
  const position = match[2].length + places + Number(match[4] || 0);
  if (Math.abs(position) > 30) return String(value);
  const decimal = position <= 0 ? '0.' + '0'.repeat(-position) + digits : position >= digits.length ? digits + '0'.repeat(position-digits.length) : digits.slice(0,position) + '.' + digits.slice(position);
  const [whole, fraction = ''] = decimal.split('.');
  const cleanFraction = fraction.replace(/0+$/, '');
  return (match[1] === '-' ? '-' : '') + whole.replace(/^0+(?=\d)/, '') + (cleanFraction ? '.' + cleanFraction : '');
}
const rate = (name, label, value = '0') => input(name, label, decimalShift(value,2), 'number', 'data-rate step="any" min="0" max="100"');
const accounts = (cashOnly = false) => S.workspace.accounts.filter(a => !cashOnly || accountType(a) === 'Cash').map(a => [a.id, a.nickname]);
const record = (kind, id) => (S.workspace[kind] || []).find(r => r.id === id);
const dates = ['once','weekly','biweekly','semimonthly','monthly','quarterly','annually'];
const cadences = dates.map(c => [c, human(c)]);
const scope = (values, body) => `<fieldset class="management-fields" data-visible-for="${values}">${body}</fieldset>`;
export const manageButton = (label, target, primary = false) => `<button class="button ${primary ? 'primary' : ''}" data-manage="${esc(target)}">${icon(label.startsWith('Add') || label.startsWith('New') ? 'plus' : 'settings')}${esc(label)}</button>`;

function show(title, body, description = '') {
  viewGeneration++;
  const assistant = $('assistant-dialog');
  if (assistant?.open) assistant.close();
  $('detail-body').innerHTML = `<h2 id="detail-title">${esc(title)}</h2>${description ? `<p class="detail-sub">${esc(description)}</p>` : ''}${body}`;
  const dialog = $('detail-dialog');
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop = 0;
  dialog.querySelector('input:not([type="hidden"]),select,textarea,button')?.focus();
  syncFields();
}
function form(kind, id, title, fields, description = '') {
  if (captureForm) { captureForm.value = {kind, id, title, fields, description}; return; }
  show(title, `<form id="management-form" class="stack-form" data-kind="${kind}" data-id="${esc(id || '')}">${fields}<p class="form-result" role="status"></p><button type="submit" class="button primary">Save ${kind === 'tax' ? 'profile' : kind === 'income' ? 'income' : kind === 'policies' ? 'rule' : kind === 'reserves' ? 'goal' : kind === 'accounts' ? 'account' : 'bill'}</button></form>`, description);
}
function scheduleFields(schedule = {}, anchor = currentDate(), name = 'anchor', includeIncome = false) {
  return input(name, name === 'next_date' ? 'Next expected date' : name === 'due_date' ? 'Due date' : 'Start date', anchor, 'date', 'required') +
    select('cadence', 'Repeats', schedule.cadence || (includeIncome ? 'on_income' : 'monthly'), includeIncome ? [['on_income','On each paycheck'], ...cadences] : cadences) +
    input('day_of_month','Day of month',schedule.day_of_month || Number(anchor.slice(-2)), 'number','min="1" max="31" step="1"') +
    input('second_day_of_month','Second day (twice monthly)',schedule.second_day_of_month || 15,'number','min="1" max="31" step="1"') +
    select('day_rule','When the requested day does not exist',schedule.day_rule || 'last_day_of_month',[['last_day_of_month','Use the last day of the month'],['skip','Skip that month'],['first_of_next','Use the first day of next month']]);
}
function accountForm(id) {
  const a = record('accounts', id) || {}, loan = S.workspace.liabilities.find(l => l.account_id === id) || {}, card = S.workspace.cards.find(c => c.account_id === id) || {};
  const type = a.type || 'checking';
  const cash = 'checking savings money_market_deposit cash brokerage_sweep';
  const debt = 'credit_card mortgage auto_loan student_loan personal_loan bnpl sbloc margin';
  const types = ['checking','savings','brokerage_sweep','credit_card','mortgage','auto_loan','student_loan','personal_loan','brokerage','retirement','hsa','estimated_asset','cash','money_market_deposit','cd','bnpl'];
  form('accounts', id, id ? 'Edit account' : 'Add an account',
    input('nickname','Account name',a.nickname || '', 'text','required maxlength="160"') +
    select('type','Account type',type,types.map(t => [t,human(t)]), id ? 'disabled' : '') +
    input('institution','Institution',a.institution || '', 'text','maxlength="160"') +
    input('mask','Account ending (optional)',/^\d{1,4}$/.test(a.mask || '') ? a.mask : '', 'text','maxlength="4" inputmode="numeric" pattern="[0-9]{0,4}" autocomplete="off"') +
    input('current',`Current balance (${S.workspace.household.base_currency})`,debt.split(' ').includes(type) ? String(amount(a.current,'0')).replace(/^-/,'') : amount(a.current,'0'),'number','step="0.01" required') +
    scope(cash,number('available','Available balance',a.available || a.current || '0','required')) +
    scope(cash,rate('apy','APY (%)',a.apy || '0')) +
    input('as_of','Balance as of',a.provenance?.as_of || currentDate(),'date','required') +
    checkbox('included_in_planning','Include this account in planning',a.included_in_planning !== false) +
    scope(debt,`<h3>Loan or card terms</h3>${rate('apr','Annual interest rate (%)',loan.apr || card.purchase_apr || '0')}${number('minimum_payment','Required monthly payment',loan.minimum_payment || '0')}${input('due_day','Payment due day',loan.due_day || 1,'number','min="1" max="31" step="1"')}`) +
    scope('mortgage auto_loan student_loan personal_loan bnpl',input('remaining_term_months','Remaining term (months)',loan.remaining_term_months || '','number','min="1" max="1200" step="1"')) +
    scope('mortgage',number('escrow','Monthly escrow',loan.escrow || '0') + number('mortgage_insurance','Monthly mortgage insurance',loan.mortgage_insurance || '0')) +
    scope('credit_card',`<h3>Card statement</h3>${number('credit_limit','Credit limit',card.credit_limit || '0')}${number('statement_balance','Statement balance',card.statement_balance || '0')}${input('statement_close_day','Statement close day',card.statement_close_day || 1,'number','min="1" max="31" step="1"')}${input('payment_due_day','Card payment due day',card.payment_due_day || 25,'number','min="1" max="31" step="1"')}${select('grace_state','Grace period',card.grace_state || 'unknown',[['unknown','Unknown'],['intact','Intact'],['lost','Lost']])}${number('annual_fee','Annual fee',card.annual_fee || '0')}${rate('foreign_transaction_fee','Foreign transaction fee (%)',card.foreign_transaction_fee || '0')}`),
    'Enter the balance shown by your institution. Manual records do not create a bank connection or payment permission.');
}
function incomeForm(id, event = false) {
  if (!accounts(true).length) return accountForm();
  if (event) {
    const e = record('income_events', id) || {};
    form('income',id,id ? 'Update income event' : 'Add income event',
      '<input type="hidden" name="record_type" value="event">' +
      select('source_id','Income source',e.source_id || S.workspace.income_sources[0]?.id,S.workspace.income_sources.map(s => [s.id,s.name])) +
      input('expected_date','Expected date',e.expected_date || currentDate(),'date','required') +
      number('expected_amount','Expected net amount',e.expected_amount || '0','required') +
      number('received_amount','Actual amount received (leave blank if expected)',e.received_amount || '') +
      input('received_date','Date received',e.received_date || '','date'),
      'Recording received income updates the plan. The account balance remains the balance you entered.');
    return;
  }
  const s = record('income_sources',id) || {};
  const next = S.workspace.income_events.find(e => e.source_id === id && !e.is_received && e.expected_date >= currentDate());
  form('income',id,id ? 'Edit income source' : 'Add income source',
    '<input type="hidden" name="record_type" value="source">' + input('name','Income source',s.name || '','text','required maxlength="160"') +
    number('net_amount','Net amount per deposit',s.net_amount || '', 'required min="0.01"') +
    select('deposit_account_id','Deposit account',s.deposit_account_id || accounts(true)[0]?.[0],accounts(true)) +
    scheduleFields(s.schedule || {},next?.expected_date || currentDate(),'next_date') +
    checkbox('is_variable','Amount varies between deposits',s.is_variable || false) +
    checkbox('generate_events','Generate expected deposits for the next 12 months',true),
    'Expected deposits fund your paycheck plan. Received deposit history stays intact when the schedule changes.');
}
function incomeList() {
  const sources = S.workspace.income_sources;
  const events = S.workspace.income_events;
  show('Manage income', `${manageButton('Add income source','income',true)}${sources.length ? manageButton('Add income event','income-event') : ''}<h3 class="section-gap">Income sources</h3>${sources.map(s => `<button class="item-row" data-manage="income:${esc(s.id)}"><span class="row-body"><strong>${esc(s.name)}</strong><small>${esc(S.workspace.accounts.find(a => a.id === s.deposit_account_id)?.nickname || '')}</small></span><strong>${money(s.net_amount)}</strong>${icon('arrow')}</button>`).join('') || empty('No income sources yet')}<h3 class="section-gap">Expected & received deposits</h3>${table(['Date','Source','Amount','Status'],events.slice().sort((a,b) => a.expected_date.localeCompare(b.expected_date)).map(e => cells(`<button class="table-link" data-manage="income-event:${esc(e.id)}">${dateLabel(e.received_date || e.expected_date)}</button>`,esc(sources.find(s => s.id === e.source_id)?.name || 'Income'),money(e.received_amount || e.expected_amount),e.is_received ? 'Received' : 'Expected')))}`,
    'Manage the source records behind your paycheck plan.');
}
function billForm(id) {
  if (!accounts(true).length) return accountForm();
  const b = record('bills',id) || {};
  form('bills',id,id ? 'Edit bill' : 'Add bill',
    input('name','Bill name',b.name || '','text','required maxlength="160"') + number('amount','Amount',b.amount || '', 'required min="0.01"') +
    scheduleFields(b.schedule || {cadence:'monthly'},b.due_date || currentDate(),'due_date') +
    select('funding_account_id','Funding account',b.funding_account_id || accounts(true)[0]?.[0],accounts(true)) +
    select('payee_account_id','Linked loan or card (optional)',b.payee_account_id || '',[['','External biller'],...S.workspace.accounts.filter(a => ['Loans','Credit cards'].includes(accountType(a))).map(a => [a.id,a.nickname])]) +
    input('category','Category',b.category || 'other','text','required maxlength="80"') +
    select('execution_owner','Who handles payment?',b.execution_owner || 'user',[['user','I pay manually'],['creditor_autopay','Creditor autopay'],['bank_billpay','My bank bill pay'],['app','FinPilot (requires separate authorization)']]) +
    checkbox('required','Required obligation',b.required !== false) + checkbox('amount_confirmed','Amount is confirmed',b.amount_confirmed !== false) + checkbox('autopay_confirmed','Autopay has been confirmed',b.autopay_confirmed || false),
    'Saving a bill updates cash flow. Review linked recurring allocations separately; this does not send a payment.');
}
function reserveForm(id) {
  if (!accounts(true).length) return accountForm();
  const r = record('reserves',id) || {};
  form('reserves',id,id ? 'Edit savings goal' : 'Add savings goal',
    input('name','Goal name',r.name || '','text','required maxlength="160"') + select('account_id','Where the funds are held',r.account_id || accounts(true)[0]?.[0],accounts(true)) +
    select('purpose','Purpose',r.purpose || 'goal',['goal','emergency','annual_bill','tax','promo_payoff','operating_floor'].map(s => [s,human(s)])) +
    number('target','Target amount',r.target || '', 'required min="0.01"') + number('funded','Money already assigned to this goal',r.funded || '0','required') +
    input('target_date','Target date (optional)',r.target_date || '','date') + checkbox('protected','Protect these funds from other spending',r.protected !== false),
    'Goals earmark money already inside the selected account. Saving a goal does not move or add money.');
}
function policyForm(id) {
  if (!accounts(true).length) return accountForm();
  const p = record('policies',id) || {};
  const purposes = ['goal','spending','investment_cash','emergency_reserve','annual_reserve','tax_reserve','buffer','extra_principal','required_debt','card_statement','bill'];
  form('policies',id,id ? 'Edit recurring rule' : 'Add recurring rule',
    input('name','Rule name',p.name || '','text','required maxlength="160"') +
    select('purpose','Purpose',p.purpose || 'goal',purposes.map(s => [s,human(s)])) +
    select('method','How much to allocate?',p.method || 'fixed',[['fixed','Fixed monthly amount'],['percent_of_income','Percentage of income'],['target_balance','Top up an account balance'],['target_by_date','Reach a goal by a date'],['surplus_share','Share of remaining surplus']]) +
    scope('fixed',number('monthly_target','Monthly target',p.monthly_target || p.amount || '', 'required min="0.01"')) +
    scope('percent_of_income surplus_share',rate('percent','Percentage (%)',p.percent || '0.1') + select('percent_base','Percentage of',p.percent_base || 'net_paycheck',[['net_paycheck','Net paycheck'],['monthly_income','Monthly income'],['surplus_after_commitments','Surplus after commitments']])) +
    scope('target_balance target_by_date',number('target_balance','Target balance',p.target_balance || '', 'required min="0.01"')) +
    scope('target_by_date',input('target_date','Reach target by',p.target_date || '', 'date','required')) +
    select('source_account_id','Funding account',p.source_account_id || accounts(true)[0]?.[0],accounts(true)) +
    select('destination_account_id','Destination account',p.destination_account_id || accounts()[0]?.[0],accounts()) +
    select('destination_reserve_id','Related savings goal (optional)',p.destination_reserve_id || '',[['','No linked goal'],...S.workspace.reserves.map(r => [r.id,r.name])]) +
    select('bill_id','Related bill (optional)',p.bill_id || '',[['','No linked bill'],...S.workspace.bills.map(b => [b.id,b.name])]) +
    select('liability_id','Related loan or card (optional)',p.liability_id || '',[['','No linked liability'],...S.workspace.liabilities.map(l => [l.id,l.name])]) +
    input('priority','Priority (lower is funded first)',p.priority || 100,'number','step="1" min="1" max="10000" required') +
    scheduleFields(p.schedule || {cadence:p.cadence || 'on_income'},p.schedule?.anchor || currentDate(),'anchor',true) +
    number('min_remaining_balance','Keep at least this amount in funding account',p.min_remaining_balance || '0') +
    checkbox('paused','Keep this rule paused',p.paused || false) + checkbox('allow_overfunding','Allow funding above the target',p.allow_overfunding || false),
    'Rule changes update future allocations. Changes to amounts, destinations or schedules require fresh payment authorization.');
}
function taxForm() {
  const t = S.workspace.tax;
  form('tax','profile','Edit tax assumptions',input('tax_year','Tax year',t.tax_year,'number','min="2000" max="2200" step="1" required') +
    input('state','State or region',t.state || '','text','maxlength="80"') +
    select('filing_status','Filing status',t.filing_status || 'single',[['single','Single'],['married_filing_jointly','Married filing jointly'],['married_filing_separately','Married filing separately'],['head_of_household','Head of household'],['qualifying_surviving_spouse','Qualifying surviving spouse']]) +
    rate('federal_marginal','Federal marginal rate (%)',t.federal_marginal) + rate('state_marginal','State marginal rate (%)',t.state_marginal) +
    checkbox('itemizes','I itemize deductions',t.itemizes) + checkbox('niit_applies','Net investment income tax applies',t.niit_applies) + rate('niit_rate','Net investment income tax rate (%)',t.niit_rate || '0.038'),
    'These are your assumptions for comparisons. Entering them does not mark them as professionally verified.');
}
function importForm(accountId = '') {
  if (!S.workspace.accounts.length) return accountForm();
  csvStage = null;
  show('Import transaction history',`<form id="csv-preview-form" class="stack-form">${select('account_id','Account',accountId || accounts()[0]?.[0],accounts())}<label>CSV file<input id="transaction-csv-file" type="file" accept=".csv,text/csv"></label><label>CSV contents<textarea name="csv" rows="5" required maxlength="2000000" placeholder="date,description,amount,category&#10;2026-09-10,Coffee,-4.85,Dining"></textarea></label>${select('date_format','Date format','YYYY-MM-DD',[['YYYY-MM-DD','YYYY-MM-DD'],['MM/DD/YYYY','MM/DD/YYYY']])}<details><summary>Column mapping</summary><div class="stack-form">${['date','description','amount','category','kind'].map(k => select('map_'+k,human(k)+(k==='category'||k==='kind'?' (optional)':''),'',[['','Automatic']])).join('')}</div></details><p class="form-result" role="status"></p><button class="button primary" type="submit">Preview import</button></form><div id="csv-preview" class="section-gap"></div>`,
    'Preview up to 5,000 rows. Use negative amounts for outflows. Imported history never silently changes your recorded bank balance.');
}
async function transactionHistory(accountId = historyAccount, offset = 0, query = historyQuery) {
  historyAccount = accountId;
  historyOffset = Math.max(0, offset);
  historyQuery = query;
  show('Transaction history',empty('Loading transactions…'));
  const generation = viewGeneration, loading = $('detail-body').innerHTML;
  const params = new URLSearchParams({account_id:accountId,limit:'50',offset:String(historyOffset),q:query});
  const data = await api('/api/transactions?'+params);
  if (generation !== viewGeneration || !$('detail-dialog').open || $('detail-body').innerHTML !== loading) return;
  transactionRows = data.transactions || [];
  const total = data.total || 0;
  show('Transaction history',`<form id="transaction-search-form" class="inline-form">${input('q','Search descriptions or merchants',query,'search','maxlength="200"')}<button type="submit" class="button">Search</button></form><p class="muted section-gap">${total ? `${historyOffset + 1}–${Math.min(total,historyOffset + transactionRows.length)} of ${total}` : 'No'} transactions</p>${transactionRows.length ? table(['Date','Description','Category','Amount'],transactionRows.map(t => cells(dateLabel(t.date),`<button class="table-link" data-manage="transaction:${esc(t.id)}">${esc(t.description || t.merchant || 'Transaction')}</button><small>${esc(human(t.kind))}</small>`,esc(t.category),money(t.amount,true)))) : empty('No transactions found','Import a CSV or try another search.')}<div class="detail-actions"><button class="button" data-manage="history-page:${Math.max(0,historyOffset - 50)}" ${historyOffset === 0 ? 'disabled' : ''}>Previous</button><button class="button" data-manage="history-page:${historyOffset + 50}" ${historyOffset + transactionRows.length >= total ? 'disabled' : ''}>Next</button>${manageButton('Import CSV','import:'+accountId)}</div>`,S.workspace.accounts.find(a => a.id === accountId)?.nickname || 'Your recorded history');
}
function transactionForm(id) {
  const tx = transactionRows.find(t => t.id === id) || record('transactions',id);
  if (!tx) throw new Error('This transaction is no longer loaded. Open transaction history again.');
  show('Edit transaction',`<form id="transaction-edit-form" class="stack-form" data-id="${esc(id)}">${input('description','Description',tx.description,'text','maxlength="500" required')}${input('category','Category',tx.category,'text','maxlength="80" required')}${select('kind','Transaction type',tx.kind,['purchase','income','internal_transfer','card_repayment','loan_payment','fee','interest','refund','reimbursement','p2p','disputed','provisional_credit'].map(t => [t,human(t)]))}<p>${fullDate(tx.date)} · ${money(tx.amount,true)}</p><p class="form-result" role="status"></p><button class="button primary" type="submit">Save transaction</button></form>`,'Classify transfers and repayments separately so they do not inflate income or purchase spending.');
}
export function syncFields(form = $('management-form')) {
  if (!form) return;
  const value = form.dataset.kind === 'accounts' ? form.elements.type?.value : form.elements.method?.value;
  for (const section of form.querySelectorAll('[data-visible-for]')) {
    section.hidden = !section.dataset.visibleFor.split(' ').includes(value);
    section.disabled = section.hidden;
  }
}
export function formPayload(form) {
  const values = Object.fromEntries(new FormData(form));
  for (const input of form.querySelectorAll('input[type="checkbox"]')) if (!input.disabled) values[input.name] = input.checked;
  for (const input of form.querySelectorAll('[data-rate]')) if (!input.closest('fieldset[disabled]')) values[input.name] = decimalShift(input.value,-2);
  return values;
}
function errorOn(form, error) {
  const target = form?.querySelector('.form-result');
  if (target) { target.textContent = error.message; target.setAttribute('role','alert'); target.classList.add('error'); }
  else hooks.toast?.(error.message, true);
}
export function initializeManagement(config) {
  hooks = config;
  document.addEventListener('change', async e => {
    if (e.target.closest('#management-form')) syncFields();
    if (e.target.id === 'transaction-csv-file') {
      const file = e.target.files?.[0];
      if (!file) return;
      if (file.size > 2000000) { errorOn(e.target.form,new Error('Choose a CSV smaller than 2 MB.')); return; }
      try { e.target.form.elements.csv.value = await file.text(); csvStage = null; } catch (error) { errorOn(e.target.form,error); }
    }
    if (e.target.closest('#csv-preview-form')) csvStage = null;
  });
  document.addEventListener('input', e => { if (e.target.closest('#csv-preview-form')) csvStage = null; });
}
export async function handleManagementClick(el) {
  if (!el.dataset.manage) return false;
  const [kind,id] = el.dataset.manage.split(':');
  try {
    if (kind === 'accounts') accountForm(id);
    else if (kind === 'income') incomeForm(id);
    else if (kind === 'income-event') incomeForm(id,true);
    else if (kind === 'income-list') incomeList();
    else if (kind === 'bills') billForm(id);
    else if (kind === 'reserves') reserveForm(id);
    else if (kind === 'policies') policyForm(id);
    else if (kind === 'tax') taxForm();
    else if (kind === 'import') importForm(id);
    else if (kind === 'history') await transactionHistory(id,0,'');
    else if (kind === 'history-page') await transactionHistory(historyAccount,Number(id),historyQuery);
    else if (kind === 'transaction') transactionForm(id);
    else if (kind === 'commit-import') {
      if (!csvStage) throw new Error('Preview your latest file and mapping before importing.');
      el.disabled = true;
      const result = await api('/api/transactions/import',{method:'POST',body:csvStage});
      const target = csvStage.account_id;
      csvStage = null;
      await hooks.load();
      hooks.toast?.(`${result.imported} transactions imported; ${result.duplicates} duplicates skipped.`);
      await transactionHistory(target,0,'');
    }
  } catch (error) { hooks.toast?.(error.message,true); if (el.isConnected) el.disabled = false; }
  return true;
}
export function inlineManagementForm(kind, id, key) {
  const handlers = {accounts: accountForm, income: incomeForm, bills: billForm,
    reserves: reserveForm, policies: policyForm, tax: taxForm};
  if (!handlers[kind]) return '';
  const captured = {}; captureForm = captured;
  try { handlers[kind](id); } finally { captureForm = null; }
  const f = captured.value;
  if (!f) return '';
  return `<form class="stack-form chat-record-form" id="chat-record-${esc(key)}" data-chat-record data-kind="${esc(f.kind)}" data-id="${esc(f.id || '')}"><h3>${esc(f.title)}</h3><p>${esc(f.description)}</p>${f.fields}<p class="form-result" role="status"></p><button type="submit" class="button primary">Review changes</button></form>`;
}
export async function handleManagementSubmit(form) {
  if (!['management-form','csv-preview-form','transaction-search-form','transaction-edit-form'].includes(form.id)) return false;
  const button = form.querySelector('button[type="submit"]');
  if (button?.disabled) return true;
  if (button) button.disabled = true;
  try {
    const values = formPayload(form);
    if (form.id === 'management-form') {
      const kind = form.dataset.kind, id = form.dataset.id;
      const result = await api(`/api/manage/${encodeURIComponent(kind)}${id ? '/'+encodeURIComponent(id) : ''}`,{method:id?'PATCH':'POST',body:values});
      $('detail-dialog').close();
      await hooks.load();
      hooks.toast?.(result.note || `${result.name || human(kind === 'reserves'?'goal':kind === 'policies'?'rule':kind)} saved.`);
      if (kind === 'accounts') location.hash = 'accounts/'+result.id;
      if (kind === 'income') { location.hash='paychecks'; incomeList(); }
    } else if (form.id === 'csv-preview-form') {
      const mapping = Object.fromEntries(['date','description','amount','category','kind'].map(k => [k,values['map_'+k] || '']));
      const payload = {account_id:values.account_id,csv:values.csv,date_format:values.date_format};
      if (Object.values(mapping).some(Boolean)) payload.mapping=mapping;
      const result = await api('/api/transactions/preview',{method:'POST',body:payload});
      if (!form.isConnected) return true;
      for (const key of ['date','description','amount','category','kind']) {
        const select = form.elements['map_'+key];
        const selected = result.mapping?.[key] || '';
        select.innerHTML = `<option value="">${key==='category'||key==='kind'?'Not included':'Select column'}</option>`+result.headers.map(h=>`<option value="${esc(h)}" ${h===selected?'selected':''}>${esc(h)}</option>`).join('');
      }
      csvStage = !result.needs_mapping && !result.errors.length ? {...payload,mapping:result.mapping} : null;
      const target = $('csv-preview');
      target.innerHTML = result.needs_mapping ? note('Map the date, description and amount columns, then preview again.') : `<h3>${result.new_count} new · ${result.duplicates} duplicates</h3>${result.errors.map(e=>note(`Row ${e.row}: ${e.message}`)).join('')}${table(['Date','Description','Category','Amount'],result.rows.map(t=>cells(dateLabel(t.date),esc(t.description)+(t.duplicate?'<small>Already imported</small>':''),esc(t.category),money(t.amount,true))))}${result.total>100?'<p class="muted">First 100 rows shown. All rows have been validated.</p>':''}${note(result.note)}<button class="button primary" data-manage="commit-import" ${!csvStage || result.new_count === 0 ? 'disabled' : ''}>Import ${result.new_count} transactions</button>`;
      if (result.needs_mapping) form.querySelector('details').open=true;
    } else if (form.id === 'transaction-search-form') {
      await transactionHistory(historyAccount,0,values.q || '');
    } else {
      await api('/api/transactions/'+encodeURIComponent(form.dataset.id),{method:'PATCH',body:values});
      await hooks.load();
      hooks.toast?.('Transaction updated.');
      await transactionHistory(historyAccount,historyOffset,historyQuery);
    }
  } catch (error) { errorOn(form,error); }
  finally { if (button?.isConnected) button.disabled=false; }
  return true;
}
