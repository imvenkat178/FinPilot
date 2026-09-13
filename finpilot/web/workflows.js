import { S, $, esc, api, icon, human } from './core.js';
let hooks = {}, capabilities = [], entities = {}, epoch = 0;
const proposals = new Map();
const rates = new Set(['apy','apr','foreign_transaction_fee','federal_marginal','state_marginal','niit_rate','percent','reliability']);
export function initializeWorkflowUI(options) { hooks = options; }
export function resetWorkflowUI() { epoch++; proposals.clear(); capabilities=[]; entities={}; }
export function rememberProposals(values=[]) {
  for (const p of values) {
    const old=proposals.get(p.id);
    if (!old || p.version>old.version || (p.version===old.version && (old.status==='proposed' || p.status!=='proposed'))) proposals.set(p.id,p);
  }
}
function isMoney(value) { return value && typeof value==='object' && typeof value.currency==='string' && typeof value.amount!=='object' && value.amount!==undefined; }
export function displayValue(value) {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value !== 'object') return String(value);
  if (isMoney(value)) return value.display || `${value.currency || ''} ${value.amount}`.trim();
  if (Array.isArray(value)) return value.map(displayValue).join(', ');
  return Object.entries(value).map(([k,v])=>`${human(k)}: ${displayValue(v)}`).join(' · ');
}
export function shiftDecimal(value, places) {
  const raw=String(value).trim();
  if(!/^-?\d+(?:\.\d+)?$/.test(raw)) throw Error('Enter an exact decimal number.');
  const negative=raw.startsWith('-'), unsigned=negative?raw.slice(1):raw;
  const [whole,fraction='']=unsigned.split('.');
  const digits=whole+fraction, at=whole.length+places;
  const result=at<=0?'0.'+'0'.repeat(-at)+digits:at>=digits.length?digits+'0'.repeat(at-digits.length):digits.slice(0,at)+'.'+digits.slice(at);
  return (negative?'-':'')+result.replace(/^0+(?=\d)/,'').replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');
}
function dataHTML(data,depth=0) {
  if (data===null || typeof data!=='object' || isMoney(data)) return `<span>${esc(displayValue(data))}</span>`;
  if (Array.isArray(data)) {
    if (!data.length) return '<p class="muted">No matching records.</p>';
    if (data.every(x=>x && typeof x==='object' && !Array.isArray(x))) {
      const preferred=['name','nickname','description','title','date','expected_date','expected_amount','received_amount','amount','current','available','category','state','status','action'];
      const keys=[...new Set(data.flatMap(x=>Object.keys(x)))];
      const columns=preferred.filter(k=>keys.includes(k)).slice(0,6);
      if (columns.length) return `<div class="workflow-table"><table><thead><tr>${columns.map(k=>`<th>${esc(human(k))}</th>`).join('')}</tr></thead><tbody>${data.slice(0,100).map(row=>`<tr>${columns.map(k=>`<td>${esc(displayValue(row[k]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${data.length>100?'<p>Showing the first 100 results.</p>':''}`;
    }
    return `<ul>${data.slice(0,30).map(v=>`<li>${depth<2?dataHTML(v,depth+1):esc(displayValue(v))}</li>`).join('')}</ul>`;
  }
  return `<dl class="workflow-data">${Object.entries(data).filter(([k])=>!k.startsWith('_')).map(([k,v])=>
    `<div><dt>${esc(human(k))}</dt><dd>${typeof v==='object' && v && !isMoney(v) ? `<details ${depth===0?'open':''}><summary>View ${esc(human(k).toLowerCase())}</summary>${depth<4?dataHTML(v,depth+1):esc(displayValue(v))}</details>`:esc(displayValue(v))}</dd></div>`).join('')}</dl>`;
}
function argumentValue(entry,key,value) {
  if(entry.reference_labels?.[key]) return entry.reference_labels[key];
  if(rates.has(key) && value!==null) return shiftDecimal(value,2)+'%';
  if(isMoney(entry.after?.[key])) return displayValue(entry.after[key]);
  return displayValue(value);
}
function proposalHTML(value) {
  rememberProposals([value]);
  const p=proposals.get(value.id);
  const pending=p.status==='proposed';
  return `<section class="workflow-proposal" data-proposal-id="${esc(p.id)}"><div class="workflow-card-heading"><span class="chip">${esc(human(p.status))}</span><strong>${p.receipt?(p.receipt.no_financial_changes?'Already up to date':'Action completed'):'Review your changes'}</strong></div>
    ${p.preview.entries.map(e=>`<article><h4>${esc(human(e.capability))}</h4><dl class="workflow-changes">${Object.entries(e.arguments).map(([key,v])=>`<div><dt>${esc(human(key.replace(/_id$/,'')))}</dt><dd>${e.before && key in e.before ? `<span class="change-before">${esc(displayValue(e.before[key]))}</span> `:''}<strong>${esc(argumentValue(e,key,v))}</strong></dd></div>`).join('')}</dl>${(e.effects||[]).map(t=>`<p class="workflow-effect">${icon('info')}${esc(t)}</p>`).join('')}<details><summary>Review calculated effects</summary>${dataHTML({before:e.before,after:e.after,result:e.result || e.target || {}})}</details></article>`).join('')}
    ${p.receipt?`<p class="workflow-success" role="status">${icon('check')}Saved · Workspace revision ${esc(p.receipt.revision)}</p><details><summary>Execution receipt</summary>${dataHTML(p.receipt.results)}</details>`:
      pending?`<p class="workflow-expiry">Review expires ${esc(new Date(p.expires_at).toLocaleTimeString())}. Changes are checked again when you confirm.</p><div class="workflow-buttons"><button class="button primary" data-workflow="confirm" data-id="${esc(p.id)}" data-version="${p.version}">Confirm</button><button class="button" data-workflow="edit" data-id="${esc(p.id)}">Edit</button><button class="text-button" data-workflow="refresh" data-id="${esc(p.id)}" data-version="${p.version}">Refresh preview</button><button class="text-button" data-workflow="cancel" data-id="${esc(p.id)}" data-version="${p.version}">Cancel</button></div>`:
      `<p role="status">${p.status==='outcome_unknown'?'The outcome could not be confirmed. Check the connection before preparing another action.':esc(human(p.status))}</p>`}
      <p class="workflow-error" role="alert"></p></section>`;
}
function capabilityButtons(caps) {
  return `<div class="workflow-capabilities">${caps.map(c=>`<button class="workflow-capability" data-workflow="capability" data-capability="${esc(c.name)}" ${c.available===false?'disabled':''}><strong>${esc(human(c.name))}</strong><span>${esc(c.description)}</span><small>${c.available===false?esc(c.unavailable_reason||'Currently unavailable'):c.review_required?'Review before saving':c.mode==='handoff'?'Open secure control':'Read or explore'}</small></button>`).join('')}</div>`;
}
export function workflowPartsHTML(answer) {
  const parts=answer.parts || [];
  return parts.map(part=>{
    if(part.type==='proposal') return proposalHTML(part.proposal);
    if(part.type==='proposals') return part.proposals.map(proposalHTML).join('');
    if(part.type==='capabilities') return capabilityButtons(part.capabilities);
    if(part.type==='result') {
      if(part.capability==='explain_capabilities') return capabilityButtons(part.data.capabilities);
      return `<section class="workflow-result"><h4>${esc(human(part.capability))}</h4>${dataHTML(part.data)}</section>`;
    }
    if(part.type==='clarification') {
      const ops=answer.workflow?.operations||[], op=part.operation || ops[0];
      return `<div class="workflow-clarification"><p>${esc(part.question)}</p>${(part.candidates||[]).map(c=>`<button class="button" data-workflow="clarify" ${op?`data-operations="${esc(JSON.stringify(ops))}" data-operation-index="${part.operation_index||0}" data-field="${esc(part.fields?.[0]||'record_id')}"`:''} data-value="${esc(c.id)}">${esc(c.label)}</button>`).join('')}${op?`<button class="button" data-workflow="complete-inputs" data-capability="${esc(op.capability)}" data-operations="${esc(JSON.stringify(ops))}">Complete details</button>`:''}</div>`;
    }
    if(part.type==='navigation') return `<a class="button" href="#${esc(part.page)}${part.account_id?'/'+esc(part.account_id):''}">Open ${esc(human(part.page))} ${icon('arrow')}</a>`;
    if(part.type==='handoff') return `<button class="button" data-workflow="handoff" data-capability="${esc(part.capability)}" data-arguments="${esc(JSON.stringify(part.arguments || {}))}">${esc(human(part.capability))} ${icon('arrow')}</button>`;
    return '';
  }).join('');
}
async function fetchCatalog() {
  const ticket=epoch;
  const data=await api('/api/assistant/capabilities');
  if(ticket!==epoch) return false;
  capabilities=data.capabilities || []; entities=data.entities || {};
  return true;
}
export async function showWorkflows() {
  if(!await fetchCatalog()) return;
  hooks.open?.();
  hooks.append?.(`<div class="workflow-library"><h3>What would you like to do?</h3><p>Ask naturally, or choose a workflow. Changes are prepared for your review.</p>${[["write","Edit your finances"],["read","Understand and compare"],["private","Manage saved information"],["provider","Refresh and import"],["handoff","Connections and uploads"],["navigate","Navigation"]].map(([mode,label])=>`<details class="workflow-category"><summary>${label}</summary>${capabilityButtons(capabilities.filter(c=>c.mode===mode))}</details>`).join("")}</div>`);
}
function choicesFor(cap,key,args) {
  const map={account_id:'account',funding_account_id:'account',payee_account_id:'account',deposit_account_id:'account',
    source_account_id:'account',destination_account_id:'account',source_id:'income',income_event_id:'income_event',
    card_id:'card',bill_id:'bill',liability_id:'liability',destination_reserve_id:'goal'};
  const kind=key==='record_id' ? (cap.entity==='income'&&args.record_type==='event'?'income_event':cap.entity) : map[key];
  return entities[kind] || [];
}
function fieldsHTML(cap,args={},index=0) {
  return `<fieldset data-capability="${esc(cap.name)}" data-index="${index}"><legend>${esc(human(cap.name))}</legend>${Object.entries(cap.input_schema.properties).sort(([a],[b])=>Number(b in args || cap.input_schema.required?.includes(b))-Number(a in args || cap.input_schema.required?.includes(a))).map(([key,rule])=>{
    const value=args[key]; const required=cap.input_schema.required?.includes(key);
    const options=choicesFor(cap,key,args);
    const attr=`name="${index}.${esc(key)}" data-key="${esc(key)}" ${required?'required':''}`;
    const title=human(key.replace(/_id$/,'').replace(/^record$/,'record to update'))+(rates.has(key)?' (%)':'')+(required?'':' (optional)');
    let input;
    if(options.length) input=`<select ${attr}><option value="">Choose…</option>${options.map(o=>`<option value="${esc(o.id)}" ${value===o.id?'selected':''}>${esc(o.label||o.name||o.title||o.id)}</option>`).join('')}</select>`;
    else if(rule.enum) input=`<select ${attr}><option value="">Choose…</option>${rule.enum.map(v=>`<option value="${esc(v)}" ${value===v?'selected':''}>${esc(human(v))}</option>`).join('')}</select>`;
    else if(rule.type==='boolean') input=`<select ${attr}><option value="">Unchanged</option><option value="true" ${value===true?'selected':''}>Yes</option><option value="false" ${value===false?'selected':''}>No</option></select>`;
    else if(rule.type==='object'||rule.type==='array') input=`<textarea ${attr} rows="3" placeholder="${rule.type==='array'?'One ID per line':'key=value, one per line'}">${esc(value?(rule.type==='array'?value.join('\n'):Object.entries(value).map(([k,v])=>k+'='+String(v)).join('\n')):'')}</textarea>`;
    else input=`<input ${attr} type="${rule.format==='date'?'date':rule.type==='integer'||rule.type==='number'?'number':'text'}" value="${esc(value==null?'':rates.has(key)?shiftDecimal(value,2):value)}" ${rule.maxLength?`maxlength="${rule.maxLength}"`:''} ${rule.minimum!==undefined?`min="${rule.minimum}"`:''} ${rule.maximum!==undefined?`max="${rule.maximum}"`:''} step="any">`;
    return `<label>${esc(title)}${input}${Array.isArray(rule.type)&&rule.type.includes('null')?`<span class="workflow-clear"><input type="checkbox" data-clear="${esc(key)}" ${value===null?'checked':''}>Clear this value</span>`:''}</label>`;
  }).join('')}</fieldset>`;
}
function readFields(form) {
  return [...form.querySelectorAll('fieldset[data-capability]')].map(fieldset=>{
    const cap=capabilities.find(c=>c.name===fieldset.dataset.capability);
    const args={};
    for(const input of fieldset.querySelectorAll('[data-key]')) {
      const key=input.dataset.key, rule=cap.input_schema.properties[key], value=input.value.trim();
      if(fieldset.querySelector('[data-clear="'+key+'"]')?.checked) {args[key]=null;continue;}
      if(!value) continue;
      if(rule.type==='boolean') args[key]=value==='true';
      else if(rule.type==='integer'||rule.type==='number') args[key]=Number(value);
      else if(rule.type==='array') args[key]=value.split('\n').map(v=>v.trim()).filter(Boolean);
      else if(rule.type==='object') args[key]=Object.fromEntries(value.split('\n').filter(Boolean).map(line=>{
        const at=line.indexOf('='); if(at<1) throw Error('Use key=value for each argument.'); return [line.slice(0,at).trim(),line.slice(at+1).trim()];
      }));
      else args[key]=rates.has(key)?shiftDecimal(value,-2):value;
    }
    return {capability:cap.name,arguments:args};
  });
}
async function showForm(operations,proposal=null) {
  if(!await fetchCatalog()) return;
  const fields=operations.map((op,i)=>fieldsHTML(capabilities.find(c=>c.name===op.capability),op.arguments,i)).join('');
  hooks.open?.();
  hooks.append?.(`<form class="workflow-form" data-workflow-form="${proposal?'edit':'prepare'}" ${proposal?`data-id="${esc(proposal.id)}" data-version="${proposal.version}"`:''}>${fields}<div class="workflow-buttons"><button class="button primary" type="submit">${proposal?'Update preview':'Continue'}</button></div><p class="workflow-error" role="alert"></p></form>`);
}
function replaceProposal(p) {
  rememberProposals([p]);
  for(const node of document.querySelectorAll('[data-proposal-id]')) if(node.dataset.proposalId===p.id) node.outerHTML=proposalHTML(p);
}
export async function handleWorkflowAction(el) {
  const action=el.dataset.workflow;if(!action) return false;
  const ticket=epoch;
  if(el.disabled) return true;
  try {
    if(action==='library') { await showWorkflows(); return true; }
    if(action==='clarify') {
      if(el.dataset.operations) {
        const ops=JSON.parse(el.dataset.operations), op=ops[Number(el.dataset.operationIndex||0)];
        op.arguments[el.dataset.field||'record_id']=el.dataset.value;
        await hooks.ask?.('Complete '+human(op.capability),ops);
      } else await hooks.ask?.(el.dataset.value);
      return true;
    }
    if(action==='handoff') { await hooks.handoff?.(el.dataset.capability,JSON.parse(el.dataset.arguments||'{}')); return true; }
    if(action==='capability'||action==='complete-inputs') {
      if(!await fetchCatalog()) return true;
      const cap=capabilities.find(c=>c.name===el.dataset.capability);
      if(cap.mode==='handoff' && (cap.name==='upload_csv' || !Object.keys(cap.input_schema.properties).length)) await hooks.handoff?.(cap.name,{});
      else await showForm(el.dataset.operations?JSON.parse(el.dataset.operations):[{capability:cap.name,arguments:JSON.parse(el.dataset.arguments||'{}')}]);
      return true;
    }
    const p=proposals.get(el.dataset.id) || await api('/api/assistant/proposals/'+encodeURIComponent(el.dataset.id));
    if(action==='edit') { await showForm(p.operations,p); return true; }
    el.disabled=true;
    const suffix=action==='refresh'?'':'/'+action;
    const updated=await api('/api/assistant/proposals/'+encodeURIComponent(p.id)+suffix,
      {method:action==='refresh'?'PATCH':'POST',body:{version:Number(el.dataset.version)}});
    if(ticket!==epoch) return true;
    replaceProposal(updated);
    if(updated.receipt) document.dispatchEvent(new Event('finpilot-data-changed'));
  } catch(error) {
    const node=el.closest('.workflow-proposal')?.querySelector('.workflow-error');
    if(node) node.textContent=error.message;
    else hooks.error?.(error.message);
  } finally { if(el.isConnected) el.disabled=false; }
  return true;
}
export async function handleWorkflowSubmit(form) {
  if(!form.dataset.workflowForm) return false;
  const ticket=epoch;
  const button=form.querySelector('button[type=submit]'); if(button.disabled) return true; button.disabled=true;
  try {
    if(form.dataset.workflowForm==='csv') {
      const body=new FormData(form);
      const conversationId=await hooks.ensureConversation();
      if(ticket!==epoch) return true;
      body.append('conversation_id',conversationId);
      const result=await api('/api/assistant/csv-preview',{method:'POST',body});
      if(ticket!==epoch) return true;
      if(result.proposal) {
        hooks.append?.(proposalHTML(result.proposal)); form.remove();
      } else form.querySelector('.workflow-error').textContent=result.preview.needs_mapping
        ? 'Choose column names below and preview again.'
        : (result.preview.errors||[]).map(e=>`Row ${e.row}: ${e.message}`).join('. ');
      return true;
    }
    const operations=readFields(form);
    if(form.dataset.workflowForm==='edit') {
      const p=await api('/api/assistant/proposals/'+encodeURIComponent(form.dataset.id),
        {method:'PATCH',body:{version:Number(form.dataset.version),operations}});
      if(ticket!==epoch) return true;
      replaceProposal(p); form.remove();
    } else {
      const question=operations.map(op=>human(op.capability)).join('; ');
      await hooks.ask?.(question,operations);
      form.remove();
    }
  } catch(error) { if(ticket===epoch && form.isConnected) form.querySelector('.workflow-error').textContent=error.message; }
  finally { if(button.isConnected) button.disabled=false; }
  return true;
}
export async function showCsvForm(args={}) {
  if(!await fetchCatalog()) return;
  hooks.open?.();
  hooks.append?.(`<form class="workflow-form" data-workflow-form="csv"><h3>Import transaction history</h3><p>Upload a UTF-8 CSV. Review its exact rows before saving. Account balances stay unchanged.</p><label>Account<select name="account_id" required>${(entities.account||[]).map(a=>`<option value="${esc(a.id)}" ${args.account_id===a.id?'selected':''}>${esc(a.label)}</option>`).join('')}</select></label><label>CSV file<input type="file" name="file" accept=".csv,text/csv" required></label><details><summary>Column mapping (optional)</summary>${['date','description','amount','category','kind'].map(k=>`<label>${human(k)} column<input name="map_${k}" placeholder="${k}"></label>`).join('')}<label>Date format<select name="date_format"><option>YYYY-MM-DD</option><option>MM/DD/YYYY</option></select></label></details><button class="button primary" type="submit">Preview import</button><p class="workflow-error" role="alert"></p></form>`);
}
