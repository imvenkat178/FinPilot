/** Private source-library and MCP workflows with deterministic API contracts. */
import assert from "node:assert/strict";
import { S } from "../finpilot/web/core.js";
import { initializeKnowledge, resetKnowledge, selectedDocuments, setSelectedDocuments, showDocuments, showDocument, showConnections, showAccess, handleKnowledgeAction, handleKnowledgeSubmit, buildMcpClientConfig } from "../finpilot/web/knowledge.js";

const nodes = {};
for (const id of ["assistant-secondary", "knowledge-status", "mcp-tool", "mcp-tool-help", "mcp-new-token", "mcp-created-token", "mcp-python-path", "mcp-project-path", "mcp-api-url", "mcp-bridge-module", "mcp-config-output", "mcp-client-config", "mcp-config-status"]) {
  nodes[id] = {innerHTML:"",textContent:"",hidden:false,value:"read_report",setAttribute(key,value){this[key]=value},scrollIntoView(){},focus(){this.focused=true},select(){this.selected=true}};
}
globalThis.document = {getElementById(id){return nodes[id]},dispatchEvent(){}};
S.session = {user:{id:"user-one"},csrf_token:"csrf-one"};
class MockFormData {
  constructor(form) { this.values = new Map(Object.entries(form?.values || {})); }
  append(name,value) { this.values.set(name,value); }
  get(name) { return this.values.get(name); }
}
globalThis.FormData = MockFormData;
const documentOne = {id:"doc-one",title:"Statement <one>",status:"ready",source_type:"upload",page_count:2,created_at:"2026-09-12T10:00:00Z"};
const imported = {id:"doc-mcp",title:"Imported report",source_type:"mcp",page_count:1,created_at:"2026-09-12T10:00:00Z"};
let docs = [documentOne], connections = [], servers = [], setup = undefined, calls = [], responseOverride = null, selectionChanges = [];
const response = (data,status=200) => ({ok:status>=200&&status<300,status,headers:{get(){return null}},async json(){return data}});
function wire() {
  globalThis.fetch = async (path, options) => {
    calls.push({path,method:options.method,headers:options.headers,body:typeof options.body === "string" ? JSON.parse(options.body) : options.body});
    if (responseOverride) return responseOverride(path,options);
    if (path === "/api/documents" && options.method === "POST") return response({document:documentOne,duplicate:true},201);
    if (path === "/api/documents") return response({documents:docs});
    if (path === "/api/documents/doc-one" && options.method === "DELETE") { docs=[]; return response({deleted:true}); }
    if (path === "/api/documents/doc-one") return response({document:documentOne,chunks:[{id:"doc-one",page:2,chunk_id:"chunk-x",text:"Literal <script>do not execute</script> source."}]});
    if (path === "/api/mcp/connections" && options.method === "GET") return response({servers,connections,configured:servers.length>0,setup});
    if (path === "/api/mcp/connections" && options.method === "POST") {connections=[{id:"conn-one",server_id:"source",name:"My source"}]; return response({connection:connections[0]},201);}
    if (path === "/api/mcp/connections/conn-one/catalog") return response({tools:[{name:"read_report",description:"Read <data> only",inputSchema:{type:"object",properties:{period:{type:"string"}}}}],resources:[{uri:"finance://report/one",name:"Monthly report",description:"Report resource"}]});
    if (path === "/api/mcp/connections/conn-one/import") {docs=[imported];return response({document:imported,imported:true});}
    if (path === "/api/mcp/tokens" && options.method === "POST") return response({token:"PRIVATE_TOKEN_ONCE",grant:{id:"grant-one"},command:"python -m finpilot.integrations.mcp_server",environment:["FINPILOT_API_URL","FINPILOT_MCP_TOKEN"],bridge:{module:"finpilot.integrations.mcp_server",api_url:"https://finpilot.example"}},201);
    if (path === "/api/mcp/tokens") return response({tokens:[{id:"grant-one",name:"Local client",expires_at:"2026-10-12T10:00:00Z"}]});
    if (options.method === "DELETE") return response({deleted:true});
    throw new Error(`Unexpected endpoint ${path}`);
  };
}
function button(action,id,extra={}) { return {dataset:{action,id,...extra},disabled:false,isConnected:true}; }
function form(kind,values) {
  const submit = {disabled:false,isConnected:true}, result = {textContent:"",setAttribute(key,value){this[key]=value}};
  return {dataset:{knowledgeForm:kind},values,isConnected:true,querySelector(selector){return selector.startsWith("button") ? submit : result},reset(){this.values={}},result};
}
initializeKnowledge({onSelection(ids){selectionChanges.push([...ids])}});
wire();
await showDocuments();
assert.ok(nodes["assistant-secondary"].innerHTML.includes("Statement &lt;one&gt;"));
assert.ok(nodes["assistant-secondary"].innerHTML.includes('accept=".pdf,.txt,.md'));
await handleKnowledgeAction(button("knowledge-select","doc-one"));
assert.deepEqual(selectedDocuments(),["doc-one"]);
assert.ok(nodes["assistant-secondary"].innerHTML.includes('aria-pressed="true"'));
await showDocument("doc-one","chunk-x");
assert.ok(nodes["assistant-secondary"].innerHTML.includes("Cited excerpt"));
assert.ok(nodes["assistant-secondary"].innerHTML.includes("Literal &lt;script&gt;do not execute&lt;/script&gt;"));
assert.ok(!nodes["assistant-secondary"].innerHTML.includes("<script>"));
await showDocuments();
const uploaded = form("upload",{file:{name:"statement.txt",size:15}});
await handleKnowledgeSubmit(uploaded);
const uploadCall = calls.find(call => call.path === "/api/documents" && call.method === "POST");
assert.equal(uploadCall.headers["X-CSRF-Token"],"csrf-one");
assert.ok(!uploadCall.headers["Content-Type"],"Browser supplies the multipart boundary");
assert.equal(uploadCall.body.get("file").name,"statement.txt");
assert.deepEqual(selectedDocuments(),["doc-one"],"Duplicate upload does not duplicate selection");
assert.ok(nodes["knowledge-status"].textContent.includes("already in your library"));
const tooBig = form("upload",{file:{name:"too-big.pdf",size:2_000_001}});
const beforeOversize = calls.length;
await handleKnowledgeSubmit(tooBig);
assert.ok(tooBig.result.textContent.includes("smaller than 2 MB"));
assert.equal(calls.length,beforeOversize,"Oversize upload is rejected before sending bytes");
await handleKnowledgeAction(button("knowledge-delete","doc-one"));
assert.deepEqual(selectedDocuments(),[]);
assert.ok(calls.some(call => call.path === "/api/documents/doc-one" && call.method === "DELETE"));
await showConnections();
assert.ok(nodes["assistant-secondary"].innerHTML.includes("No external sources configured"));
assert.ok(!nodes["assistant-secondary"].innerHTML.includes('data-knowledge-form="connect"'));
servers=[{id:"source",name:"Approved <source>"}];
await showConnections();
assert.ok(nodes["assistant-secondary"].innerHTML.includes("Approved &lt;source&gt;"));
setup={ready:false,message:"Credential storage must be configured <first>."};
connections=[{id:"conn-old",server_id:"source",name:"Existing connection"}];
await showConnections();
assert.ok(nodes["assistant-secondary"].innerHTML.includes("Credential storage must be configured &lt;first&gt;."));
assert.ok(!nodes["assistant-secondary"].innerHTML.includes('data-knowledge-form="connect"'),"Do not collect credentials before the server can store them");
assert.ok(nodes["assistant-secondary"].innerHTML.includes("Existing connection"),"Existing connections remain available for removal");
setup={ready:true}; connections=[];
await showConnections();
await handleKnowledgeSubmit(form("connect",{server_id:"source",name:"My source",token:"provider-token"}));
assert.equal(calls.find(call => call.path === "/api/mcp/connections" && call.method === "POST").body.token,"provider-token");
await handleKnowledgeAction(button("knowledge-catalog","conn-one"));
assert.ok(nodes["mcp-tool-help"].innerHTML.includes("Read &lt;data&gt; only"));
const invalid = form("import-tool",{tool_name:"read_report",arguments:"[]"});
const beforeInvalid = calls.length;
await handleKnowledgeSubmit(invalid);
assert.ok(invalid.result.textContent.includes("JSON object"));
assert.equal(calls.length,beforeInvalid);
await handleKnowledgeSubmit(form("import-tool",{tool_name:"read_report",arguments:'{"period":"2026-09"}',title:"Monthly statement"}));
const importCall = calls.find(call => call.path.endsWith("/import"));
assert.deepEqual(importCall.body,{tool_name:"read_report",arguments:{period:"2026-09"},title:"Monthly statement"});
assert.deepEqual(selectedDocuments(),["doc-mcp"]);
await showAccess();
assert.ok(!nodes["assistant-secondary"].innerHTML.includes('value="90"'),"Expiry choices respect backend 30-day limit");
const grantForm = form("grant",{name:"Local client",expires_days:"30"});
const beforeGrant = calls.length;
await handleKnowledgeSubmit(grantForm);
assert.ok(nodes["mcp-new-token"].innerHTML.includes("PRIVATE_TOKEN_ONCE"));
assert.ok(nodes["mcp-new-token"].innerHTML.includes("https://finpilot.example"));
assert.ok(nodes["mcp-new-token"].innerHTML.includes('autocomplete="off"'));
assert.equal(calls.length,beforeGrant+1,"A newly created one-time token is displayed without relying on another request");
assert.equal(grantForm.hidden,true,"Do not replace the only copy of a token with an accidental second grant");
const configInputs={apiUrl:"https://finpilot.example/",token:"PRIVATE_TOKEN_ONCE",pythonPath:"C:\\Users\\A B\\FinPilot\\.venv\\Scripts\\python.exe",projectPath:"C:\\Users\\A B\\FinPilot"};
const config=buildMcpClientConfig(configInputs);
assert.deepEqual(config.mcpServers.finpilot,{command:configInputs.pythonPath,args:["-m","finpilot.integrations.mcp_server"],env:{PYTHONPATH:configInputs.projectPath,FINPILOT_API_URL:"https://finpilot.example",FINPILOT_MCP_TOKEN:"PRIVATE_TOKEN_ONCE"}});
assert.deepEqual(JSON.parse(JSON.stringify(config)),config,"Paths with spaces and backslashes produce valid JSON without shell quoting");
assert.equal(buildMcpClientConfig({...configInputs,pythonPath:"/home/me/.venv/bin/python",projectPath:"/home/me/finpilot"}).mcpServers.finpilot.env.PYTHONPATH,"/home/me/finpilot");
for (const invalid of [{pythonPath:"python"},{projectPath:"relative/folder"},{apiUrl:"file:///tmp/server"},{apiUrl:"https://user:pass@example.com"},{pythonPath:"/home/me/python\nother"},{token:""}]) {
  assert.throws(()=>buildMcpClientConfig({...configInputs,...invalid}));
}
nodes["mcp-created-token"].value=configInputs.token;
nodes["mcp-python-path"].value=configInputs.pythonPath;
nodes["mcp-project-path"].value=configInputs.projectPath;
nodes["mcp-api-url"].value=configInputs.apiUrl;
nodes["mcp-bridge-module"].value="finpilot.integrations.mcp_server";
await handleKnowledgeAction(button("knowledge-build-config"));
assert.equal(nodes["mcp-config-output"].hidden,false);
assert.deepEqual(JSON.parse(nodes["mcp-client-config"].value),config);
let copied;
Object.defineProperty(globalThis,"navigator",{configurable:true,value:{clipboard:{async writeText(value){copied=value}}}});
await handleKnowledgeAction(button("knowledge-copy-config"));
assert.equal(copied,nodes["mcp-client-config"].value);
assert.ok(nodes["mcp-config-status"].textContent.includes("copied"));
globalThis.navigator.clipboard.writeText=async()=>{throw new Error("denied")};
await handleKnowledgeAction(button("knowledge-copy-config"));
assert.equal(nodes["mcp-client-config"].selected,true);
assert.ok(nodes["mcp-config-status"].textContent.includes("copy it manually"));
nodes["mcp-python-path"].value="relative/python";
await handleKnowledgeAction(button("knowledge-copy-config"));
assert.equal(nodes["mcp-config-status"].role,"alert");
assert.ok(nodes["mcp-config-status"].textContent.includes("absolute Python executable path"));
resetKnowledge();
assert.equal(nodes["assistant-secondary"].innerHTML,"");
assert.deepEqual(selectedDocuments(),[]);
let finish;
globalThis.fetch = () => new Promise(resolve => finish=resolve);
const late = showDocuments();
resetKnowledge();
finish(response({documents:[{...documentOne,title:"OLD_PRIVATE_DOCUMENT"}]}));
await late;
assert.equal(nodes["assistant-secondary"].innerHTML,"","Late response after signout cannot reinsert document data");
wire();
await showDocuments();
responseOverride = async () => {S.session={user:{id:"user-two"},csrf_token:"csrf-two"};return response({document:documentOne},201)};
const sessionChange = form("upload",{file:{name:"private.txt",size:12}});
await handleKnowledgeSubmit(sessionChange);
assert.ok(sessionChange.result.textContent.includes("session changed"));
assert.deepEqual(selectedDocuments(),[],"Upload result cannot attach a previous user's document to the new session");
console.log("Knowledge UI: library selection, escaped excerpts, multipart upload/size/CSRF, duplicate/delete, MCP availability/catalog/import, one-time token retention, portable client configuration/copy fallback, credential readiness, reset races and changed-session privacy pass.");
