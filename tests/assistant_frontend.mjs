/** Assistant rendering and lifecycle checks with deterministic API responses; no model/server calls. */
import assert from 'node:assert/strict';
import { S } from '../finpilot/web/core.js';
import { initializeAssistant, updateAssistantContext, ask, resetAssistant, showHistory, resumeConversation, deleteConversation, backToConversation } from '../finpilot/web/assistant.js';
const elements = {};
for (const id of ['assistant-dialog','detail-dialog','chat-form','chat-input','chat-send','chat-log','chat-suggestions','copilot-context-name','assistant-model','assistant-secondary','chat-composer','chat-selected-documents']) {
  elements[id]={innerHTML:'',textContent:'',value:'',dataset:{},open:false,disabled:false,scrollHeight:0,scrollTop:0,
    showModal(){this.open=true},close(){this.open=false},focus(){},addEventListener(){},
    insertAdjacentHTML(_,html){this.innerHTML+=html}};
}
const pending={set outerHTML(html){elements['chat-log'].innerHTML=elements['chat-log'].innerHTML.replace(/<div id="pending-response"[\s\S]*?<\/div>/,html)}};
globalThis.document={getElementById(id){return id==='pending-response' ? (elements['chat-log'].innerHTML.includes('id="pending-response"') ? pending : null) : elements[id]},dispatchEvent(){}};
S.data={llm:{reachable:true}};
S.workspace={accounts:[{id:'savings',nickname:'Savings & reserves'}]};
S.page='accounts';S.accountId='savings';S.session={user:{id:'current-user'},csrf_token:'csrf-test'};
const response = (data,status=200)=>({ok:status===200,status,headers:{get(){return null}},async json(){return data}});
let calls=[],nextAnswer={},savedAnswers=[],savedConversations=[];
globalThis.fetch=async(path,options)=>{
  calls.push({path,body:options.body&&JSON.parse(options.body)});
  if(path==='/api/ask/suggestions')return response({suggestions:[]});
  if(path==='/api/documents')return response({documents:[{id:'doc-guide',title:'Benefits guide'}]});
  if(path==='/api/conversations')return response({conversations:savedConversations});
  if(path==='/api/conversations/chat-saved')return options.method === 'DELETE' ? response({deleted:true}) : response({conversation:{id:'chat-saved',document_ids:['doc-guide','deleted-doc']},answers:savedAnswers});
  return response(nextAnswer);
};
const answer = overrides => ({conversation_id:'chat-new',question:'Explain my forecast',answer:'Your forecast uses the selected savings account.',tools_called:['get_cash_forecast'],used_model:true,grounding:{ok:true},evidence:[{account_id:'savings'}],trace:[{node:'compose',mode:'model'},{node:'verify',result:'accepted'}],model_status:{reachable:true},...overrides});
initializeAssistant();
updateAssistantContext();
assert.ok(elements['assistant-dialog'].innerHTML.includes('Conversations are saved to your account'));
assert.ok(!elements['assistant-dialog'].innerHTML.includes('Each question is answered independently'));
nextAnswer=answer({});await ask('Explain my forecast');
assert.match(elements['chat-log'].innerHTML,/response-ai-label">AI/);
assert.ok(elements['chat-log'].innerHTML.includes('Model response'));
assert.equal(elements['chat-suggestions'].hidden,true,'Suggestions give the conversation room after the first answer');
assert.ok(elements['chat-log'].innerHTML.includes('Savings &amp; reserves'));
assert.deepEqual(calls.find(c=>c.path==='/api/ask').body.context,{account_id:'savings',page:'accounts'});
assert.equal(calls.find(c=>c.path==='/api/ask').body.conversation_id,null);
await ask('And next month?');
assert.equal(calls.filter(c=>c.path==='/api/ask').at(-1).body.conversation_id,'chat-new','Follow-up uses the same saved conversation');
resetAssistant();
S.workspace.transactions=[{id:'tx-loaded',account_id:'savings'}];
nextAnswer=answer({question:'How much can I spend?',answer:'You can spend $1,350.00.',record_refs:[
  {kind:'account',id:'savings',label:'Savings <reserve>',account_id:'savings'},
  {kind:'transaction',id:'tx-loaded',label:'2026-09-10 - Coffee - $4.85',account_id:'savings'},
  {kind:'transaction',id:'tx-older',label:'2026-01-02 - Rent - $1,800.00',account_id:'savings'},
  {kind:'bill',id:'bill-rent',label:'Rent',account_id:'savings'},
  {kind:'policy',id:'pol-save',label:'Save monthly',account_id:'savings'},
  {kind:'reserve',id:'res-trip',label:'Trip',account_id:'savings'}],
  evidence_confidence:{level:'medium',score:75,reasons:['Data for Savings is stale <now>.']},
  figures:[{token:'F1',text:'$1,350.00',sources:['get_spending_allowance.amount']},{token:'F2',text:'two',sources:[]}]});
await ask('How much can I spend?');
{
  const html=elements['chat-log'].innerHTML;
  assert.ok(html.includes('data-level="medium">Medium confidence</span>'),'Evidence confidence replaces the calculation label');
  assert.ok(html.includes('RECORD SOURCES'));
  assert.ok(html.includes('href="#accounts/savings"'),'Accounts link to the account page');
  assert.ok(html.includes('data-manage="transaction:tx-loaded"'),'Loaded transactions open their record');
  assert.ok(html.includes('data-manage="history:savings"'),'Other transactions open the account history');
  assert.ok(html.includes('data-detail="bill:bill-rent"')&&html.includes('data-detail="rule:pol-save"')&&html.includes('data-detail="reserve:res-trip"'));
  assert.ok(html.includes('Savings &lt;reserve&gt;')&&!html.includes('Savings <reserve>'),'Record labels are escaped');
  assert.ok(html.includes('WHY MEDIUM CONFIDENCE')&&html.includes('stale &lt;now&gt;'));
  assert.ok(html.includes('WHERE EACH FIGURE COMES FROM')&&html.includes('Spending allowance: amount'));
  assert.ok(!html.includes('<strong>two</strong>'),'Figures without a source field are not listed');
}
delete S.workspace.transactions;
resetAssistant();nextAnswer=answer({used_model:false,model_status:{reachable:false},trace:[{node:'compose',mode:'template',reason:'model call failed: private-debug-string'},{node:'verify',result:'accepted'}]});await ask('Explain my forecast');
assert.match(elements['chat-log'].innerHTML,/response-ai-label">CALC/);
assert.ok(elements['chat-log'].innerHTML.includes('Model unavailable for this answer'));
assert.ok(!elements['chat-log'].innerHTML.includes('private-debug-string'));
assert.ok(elements['assistant-model'].textContent.includes('Model unavailable'),'Header reflects latest answer health rather than old bootstrap');
resetAssistant();nextAnswer=answer({used_model:false,grounding:{ok:true},trace:[{node:'compose',mode:'model'},{node:'verify',result:'rejected',reason:'Claimed a completed payment'}]});await ask('Explain my forecast');
assert.ok(elements['chat-log'].innerHTML.includes('Model wording rejected'),'Non-numeric rejected model claims are disclosed');
resetAssistant();nextAnswer=answer({used_model:false,grounding:{ok:false},trace:[{node:'compose',mode:'template',reason:'no local model reachable'},{node:'verify',result:'rejected'}]});await ask('Explain my forecast');
assert.ok(!elements['chat-log'].innerHTML.includes('Model wording rejected'),'Template verification is never attributed to nonexistent model wording');
assert.ok(elements['chat-log'].innerHTML.includes('Model not connected'));
resetAssistant();nextAnswer=answer({used_model:false,state:'drafted',workflow:{planner:{accepted:false}},parts:[],action_receipts:[{status:'succeeded',revision:8}]});
await ask('Show my saved action');
assert.ok(!elements['chat-log'].innerHTML.includes('<span>Drafted</span>'),'Historical draft state must not contradict current action receipts');
savedAnswers=[answer({question:'Saved <question>',at:'2026-09-12T09:00:00Z',revision:7,viewing:{page:'accounts',name:'Saved savings account'}})];
savedConversations=[{id:'chat-saved',title:'A saved <conversation>',updated_at:'2026-09-12T09:00:00Z'}];
await showHistory();
assert.ok(elements['chat-log'].innerHTML.includes('A saved &lt;conversation&gt;'));
assert.ok(elements['chat-log'].innerHTML.includes('data-action="chat-resume"'));
assert.ok(elements['chat-composer'].hidden);
await resumeConversation('chat-saved');
assert.ok(!elements['chat-composer'].hidden);
assert.ok(elements['chat-log'].innerHTML.includes('Saved &lt;question&gt;'));
assert.ok(elements['chat-log'].innerHTML.includes('Saved savings account'));
assert.ok(elements['chat-log'].innerHTML.includes('Workspace revision 7'));
nextAnswer=answer({conversation_id:'chat-saved',memory_turns:1,intent:'document_retrieval',tools_called:['retrieve_documents','get_money_overview'],answer:'The document says <not markup> [3].',document_sources:[{id:'doc-guide',title:'Benefits <guide>',page:3,citation:3,chunk_id:'chunk-a',text:'PRIVATE_SOURCE_EXCERPT'}]});
await ask('What about the guide?');
const followup=calls.filter(c=>c.path==='/api/ask').at(-1);
assert.equal(followup.body.conversation_id,'chat-saved');
assert.deepEqual(followup.body.document_ids,['doc-guide']);
assert.ok(elements['chat-log'].innerHTML.includes('Benefits &lt;guide&gt;'));
assert.ok(elements['chat-log'].innerHTML.includes('data-chunk="chunk-a"'));
assert.ok(elements['chat-log'].innerHTML.includes('SOURCE [3]'),'Citation buttons retain server source numbers');
assert.ok(elements['chat-log'].innerHTML.includes('Model-selected document excerpts'));
assert.ok(!elements['chat-log'].innerHTML.split('<div class="copilot-response">').at(-1).includes('EXPLORE THE SOURCE'),'Retrieval answers use citation buttons, not unrelated financial links');
assert.ok(elements['chat-log'].innerHTML.includes('Used 1 recent conversation turn'));
assert.ok(!elements['chat-log'].innerHTML.includes('PRIVATE_SOURCE_EXCERPT'));
await deleteConversation('chat-saved');
assert.ok(calls.some(c=>c.path==='/api/conversations/chat-saved'));
backToConversation();
assert.ok(!elements['chat-log'].innerHTML.includes('Benefits &lt;guide&gt;'));
assert.ok(elements['chat-selected-documents'].hidden);
let complete;
globalThis.fetch=()=>new Promise(resolve=>complete=resolve);
const oldRequest=ask('Old request');
resetAssistant();
complete(response(answer({answer:'PRIVATE_OLD_ANSWER'})));
await oldRequest;
assert.ok(!elements['chat-log'].innerHTML.includes('PRIVATE_OLD_ANSWER'),'Cleared conversation ignores late responses');
assert.equal(elements['chat-send'].disabled,false);
globalThis.fetch=async()=>response({detail:'Assistant unavailable <now>'},503);
await ask('Retry this <question>');
assert.ok(elements['chat-log'].innerHTML.includes('role="alert"'));
assert.ok(elements['chat-log'].innerHTML.includes('Assistant unavailable &lt;now&gt;'));
assert.ok(elements['chat-log'].innerHTML.includes('data-ask="Retry this &lt;question&gt;"'));
assert.equal(elements['chat-send'].disabled,false);
console.log('Assistant UI: AI/CALC attribution, latest model status, accurate rejection reasons, scoped request, saved history, reset race, saved conversation resume/delete, document selection/citations, memory disclosure, record links, evidence confidence and error retry pass.');
