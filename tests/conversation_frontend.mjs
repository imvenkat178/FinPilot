/** Structured artifact contracts: escaped data, money signs, explicit actions. */
import assert from 'node:assert/strict';
import { chartHTML, renderComponents, proposalHTML, receiptHTML } from '../finpilot/web/conversation-renderer.js';
import { inlineManagementForm } from '../finpilot/web/manage.js';
import { S, sections } from '../finpilot/web/core.js';
import { chatPage } from '../finpilot/web/assistant.js';

assert.equal(sections[0][0],'chat');
assert.ok(chatPage().includes('id="chat-mount"'));
for(const kind of ['bar','line']) {
  const html=chartHTML({kind,title:'Cash <script>alert(1)</script>',currency:'USD',points:[
    {label:'Account <img src=x>',value:'-35.25'},{label:'Cash & savings',value:'99.75'}]});
  assert.ok(!html.includes('<script>')&&!html.includes('<img'));
  assert.ok(html.includes('&lt;script&gt;')&&html.includes('role="img"'));
  assert.ok(!html.includes('NaN')&&!html.includes('Infinity'));
}
assert.ok(chartHTML({kind:'bar',title:'Empty',points:[]}).includes('No recorded points'));
assert.ok(!chartHTML({kind:'line',title:'One',points:[{label:'Only',value:'0'}]}).includes('NaN'));
const table=renderComponents([{type:'table',title:'Records',columns:['Account'],rows:[['<script>bad</script>']]}]);
assert.ok(table.includes('&lt;script&gt;')&&!table.includes('<script>'));
assert.equal(renderComponents([{type:'html',html:'<script>evil()</script>'}]),'');
const proposal={id:'preview-1',state:'pending',digest:'a'.repeat(64),expires_at:'2026-09-12T20:00:00Z',
  requires_simulation_confirmation:true,preview:[{title:'Sample payment',operation:'simulate_group',request:{group_id:'group-1'},result:{simulation_only:true}}]};
const preview=proposalHTML(proposal);
assert.ok(preview.includes('data-chat-confirm="preview-1"'));
assert.ok(preview.includes('name="confirm_simulation"'));
assert.ok(!preview.includes('confirm_simulation" checked'));
for(const state of ['canceled','superseded','expired','stale']) {
  const html=proposalHTML({...proposal,state});
  assert.ok(!html.includes('data-chat-confirm='),state);
  assert.ok(!html.includes('Reviewed change processed'),state);
}
const receipt={id:'receipt-1',note:'Individual status is recorded.',at:'2026-09-12T19:00:00Z',
  payment_mode:'simulation',results:[{groups:[{group_id:'group-1'}]}]};
assert.ok(receiptHTML(receipt).includes('data-chat-simulate="group-1"'));
assert.ok(!receiptHTML({...receipt,payment_mode:'not_connected'}).includes('data-chat-simulate='));
assert.ok(!proposalHTML({...proposal,state:'applied',receipt}).includes('data-chat-confirm='));
S.data={as_of:'2026-09-10'};
S.workspace={household:{base_currency:'USD'},accounts:[],income_sources:[],income_events:[],liabilities:[],cards:[],policies:[],bills:[],reserves:[],tax:{}};
const form=inlineManagementForm('accounts',null,'one');
assert.ok(form.includes('data-chat-record')&&form.includes('Review changes'));
assert.ok(!form.includes('id="management-form"'),'Chat forms cannot submit directly to legacy save handler');
assert.ok(inlineManagementForm('policies',null,'two').includes('data-kind="accounts"'),'Start with account setup when needed');
assert.equal(inlineManagementForm('arbitrary',null,'three'),'');
console.log('Conversation UI contracts pass: primary route, escaped tables/charts, signed values, empty data, preview states, explicit simulation and inline setup.');
