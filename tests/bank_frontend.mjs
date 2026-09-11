/** Browser flow contracts using synthetic session data; never loads a real SDK. */
import assert from 'node:assert/strict';
import { S } from '../finpilot/web/core.js';
import { initializeBankLinking, mountBankConnections, handleBankAction } from '../finpilot/web/connect.js';
let settings = {configured:false, reason:'Bank linking is unavailable.', environment:null};
let rows = [], calls = [], scripts = [], errors = [], sdkOptions, opened = 0, destroyed = 0, reloads = 0;
let closed = 0;
const storage = new Map();
const host = {isConnected:true, innerHTML:'', setAttribute(){}, removeAttribute(){}};
const confirmBox = {innerHTML:'', querySelector(){return {focus(){}}}, replaceChildren(){this.innerHTML=''}};
const bankRow = {querySelector(){return confirmBox}};
globalThis.location = new URL('http://localhost/#accounts');
globalThis.history = {replaceState(_,__,url){globalThis.location = new URL(url, location.origin)}};
globalThis.sessionStorage = {getItem(k){return storage.get(k) || null},setItem(k,v){storage.set(k,v)},removeItem(k){storage.delete(k)}};
globalThis.window = {};
const sdk = {create(options){sdkOptions=options; return {open(){opened++}, destroy(){destroyed++}}}};
globalThis.document = {
  getElementById(id){return id==='bank-connections' ? host : id==='detail-dialog' ? {close(){closed++}} : null},
  createElement(){return {remove(){}}},
  head:{appendChild(script){scripts.push(script.src);window.Plaid=sdk;script.onload()}},
  dispatchEvent(){},
};
const response = data => ({ok:true,status:200,headers:{get(){return null}},async json(){return data}});
globalThis.fetch = async (url, options) => {
  calls.push([url, options]);
  if (url === '/api/bank/connections') return response({provider:settings,connections:rows});
  if (url === '/api/bank/link-token') return response({link_token:'link-synthetic',expiration:new Date(Date.now()+60000).toISOString()});
  if (url === '/api/bank/exchange') return response({connection:{account_ids:['account-synthetic']}});
  return response({});
};
S.session={user:{id:'test-user'},role:'owner',csrf_token:'csrf-synthetic'};
S.revision=1;
await initializeBankLinking({reload:async()=>reloads++,toast:(message,error)=>{if(error)errors.push(message)}});
await mountBankConnections(host);
assert.ok(host.innerHTML.includes(settings.reason));
assert.ok(!host.innerHTML.includes('data-action="bank-link"'));
assert.deepEqual(scripts,[], 'Unavailable configuration cannot load external bank SDK');
settings={configured:true,environment:'sandbox'};
rows=[{id:'bank-test',institution:'Test <bank>',account_ids:['account-synthetic'],status:'active',last_synced_at:'2026-09-11T12:00:00+00:00',notices:[]}];
await mountBankConnections(host);
assert.ok(host.innerHTML.includes('Test &lt;bank&gt;'));
assert.ok(host.innerHTML.includes('Plaid Sandbox'));
const button = name => ({dataset:{action:name,id:'bank-test'},disabled:false,isConnected:true,closest(selector){return selector==='.bank-connection' ? bankRow : confirmBox}});
await handleBankAction(button('bank-link'));
assert.deepEqual(scripts,['https://cdn.plaid.com/link/v2/stable/link-initialize.js']);
assert.equal(sdkOptions.token,'link-synthetic');
assert.equal(opened,1);
assert.equal(closed,1,'Native modal closes before opening provider iframe');
await sdkOptions.onSuccess('public-synthetic');
assert.equal(reloads,1);
const exchange = calls.find(([url])=>url==='/api/bank/exchange');
assert.equal(JSON.parse(exchange[1].body).public_token,'public-synthetic');
assert.equal(exchange[1].headers['X-CSRF-Token'],'csrf-synthetic');
assert.equal(storage.size,0);
// Sign-in change during provider interaction cannot exchange into another tenant.
await handleBankAction(button('bank-link'));
const callsBefore = calls.length;
S.session.user.id='other-user';
await sdkOptions.onSuccess('wrong-session-public');
assert.equal(calls.length,callsBefore);
S.session=null;
await initializeBankLinking();
S.session={user:{id:'test-user'},role:'owner',csrf_token:'csrf-synthetic'};
location = new URL('http://localhost/?oauth_state_id=synthetic#accounts');
storage.set('finpilot-bank-link',JSON.stringify({token:'link-oauth',userId:'test-user',expires:new Date(Date.now()+60000).toISOString()}));
await initializeBankLinking();
assert.equal(sdkOptions.token,'link-oauth');
assert.ok(sdkOptions.receivedRedirectUri.includes('oauth_state_id=synthetic'));
sdkOptions.onExit(null);
assert.equal(storage.size,0);
assert.ok(!location.search.includes('oauth_state_id'));
// Confirmation is a separate action; it must not revoke while simply opening it.
const beforeConfirm = calls.length;
await handleBankAction(button('bank-disconnect'));
assert.equal(calls.length,beforeConfirm);
assert.ok(confirmBox.innerHTML.includes('saved balances and transactions remain'));
await handleBankAction(button('bank-confirm-disconnect'));
assert.ok(calls.some(([url,options])=>url==='/api/bank/connections/bank-test'&&options.method==='DELETE'));
assert.equal(reloads,2);
assert.deepEqual(errors,[]);
console.log('Bank UI: unavailable state, safe rendering, official SDK gate, session-bound exchange/OAuth, CSRF and explicit disconnect pass.');
