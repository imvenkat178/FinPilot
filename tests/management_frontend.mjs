/** Management surface contracts, independent of a running server. */
import assert from 'node:assert/strict';
import { S } from '../finpilot/web/core.js';
import { handleManagementClick, initializeManagement } from '../finpilot/web/manage.js';
let html = '', errors = [];
const body = {get innerHTML(){return html},set innerHTML(value){html=value}};
const dialog = {open:false,showModal(){this.open=true},close(){this.open=false},querySelector(){return null}};
globalThis.document={getElementById(id){return {'detail-body':body,'detail-dialog':dialog,'assistant-dialog':{open:false}}[id] || null},addEventListener(){}};
S.data={as_of:'2026-09-10'};
S.workspace={household:{base_currency:'USD'},accounts:[{id:'test-account',nickname:'Checking & daily',type:'checking',institution:'Test bank',mask:'1234',current:{amount:'9007199254740993.01'},available:{amount:'4800.50'},included_in_planning:false}],income_sources:[{id:'test-source',name:'Salary',deposit_account_id:'test-account',net_amount:{amount:'2500'}}],income_events:[{id:'test-event',source_id:'test-source',expected_date:'2026-09-15',expected_amount:{amount:'2500'}}],reserves:[],liabilities:[],cards:[],policies:[],bills:[],transactions:[],tax:{tax_year:2026,federal_marginal:'0.24',state_marginal:'0.05'}};
initializeManagement({load:async()=>{},toast:(m)=>errors.push(m)});
for(const target of ['accounts','accounts:test-account','income','income-event:test-event','income-list','bills','reserves','policies','tax','import:test-account']){
  const handled=await handleManagementClick({dataset:{manage:target}});
  assert.equal(handled,true,target);
  assert.ok(html.includes('id="detail-title"'),target);
  assert.ok(!html.includes('undefined'),target);
  assert.ok(!html.includes('NaN'),target);
}
await handleManagementClick({dataset:{manage:'accounts:test-account'}});
assert.ok(html.includes('9007199254740993.01'),'Money input preserves stored decimal precision');
assert.ok(html.includes('Checking &amp; daily'),'Untrusted names are escaped');
assert.ok(!html.match(/name="included_in_planning" checked/),'Excluded account remains excluded');
assert.ok(!html.includes('send_transfer'),'Manual forms cannot grant provider capabilities');
// Async transaction history must display imported rows and respect a newer dialog.
const transactionResponse = {transactions:[{id:'imported-one',date:'2026-09-09',description:'Cafe & lunch',category:'Dining',kind:'purchase',amount:{amount:'-12.50',currency:'USD'}}],total:1,limit:50,offset:0};
const response = value => ({ok:true,status:200,headers:{get(){return null}},async json(){return value}});
let requested='';
globalThis.fetch=async url=>{requested=url;return response(transactionResponse)};
await handleManagementClick({dataset:{manage:'history:test-account'}});
assert.ok(requested.includes('account_id=test-account')&&requested.includes('limit=50'));
assert.ok(html.includes('Cafe &amp; lunch'),'Imported rows render after async fetch');
assert.ok(html.includes('1–1 of 1'),'History reports full paginated total');
await handleManagementClick({dataset:{manage:'transaction:imported-one'}});
assert.ok(html.includes('transaction-edit-form'),'Imported row opens correction form');
let finishRequest;
globalThis.fetch=()=>new Promise(resolve=>finishRequest=resolve);
const staleHistory=handleManagementClick({dataset:{manage:'history:test-account'}});
body.innerHTML='<h2>A different detail</h2>';
finishRequest(response(transactionResponse));
await staleHistory;
assert.equal(html,'<h2>A different detail</h2>','Late history cannot replace a detail opened by another module');
S.workspace.accounts=[];S.workspace.income_sources=[];S.workspace.income_events=[];
for(const target of ['income','bills','reserves','policies','import']){
  await handleManagementClick({dataset:{manage:target}});
  assert.ok(html.includes('Add an account'),'Empty workspace starts with account entry');
}
assert.deepEqual(errors,[]);
console.log('Management forms: 10 populated routes, 5 empty-workspace routes, async imported history, stale-dialog protection, safe text, exact amounts and planning inclusion pass.');
