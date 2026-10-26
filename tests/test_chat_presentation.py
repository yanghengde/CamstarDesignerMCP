"""Presentation must preserve real completion/approval state and streamed answers."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
DOM = r"""
const assert=require('node:assert/strict');
class Element {
    constructor(){this.children=[];this.style={};this.className='';this.textContent='';this.classList={add(){}};this.isConnected=true;}
    append(...children){for(const child of children){child.remove();child.parentElement=this;this.children.push(child);}}
    appendChild(child){this.append(child);return child;}
    insertBefore(child,before){child.remove();child.parentElement=this;const i=before?this.children.indexOf(before):-1;if(i<0)this.children.push(child);else this.children.splice(i,0,child);}
    remove(){if(this.parentElement){const a=this.parentElement.children;a.splice(a.indexOf(this),1);this.parentElement=null;}}
    querySelectorAll(){return [];}
}
const document={createElement:()=>new Element()};
const window={};let clock=0;const performance={now:()=>clock};
const timers=new Map();let nextTimer=0;
const setInterval=fn=>{const id=++nextTimer;timers.set(id,fn);return id;};
const clearInterval=id=>timers.delete(id);
"""


def node(script):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node.js is required for presentation lifecycle regressions')
    result = subprocess.run([executable, '-'], input=script, text=True, encoding='utf-8',
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_clock_approval_stale_updates_reset_and_safe_markdown_fallback():
    node(DOM + (ROOT/'static/chat-presentation.js').read_text(encoding='utf-8') + r"""
const ui=window.ChatPresentation,container=new Element();
const first=ui.createWork(container);
clock=2600;for(const callback of timers.values())callback();
assert.match(first.details.children[0].textContent,/本页用时 2秒/);
first.step('query','call');first.step('query','call',true);
assert.equal(first.steps.children.length,2);assert.match(first.steps.children[1].textContent,/已返回结果/);
const now=Date.now();first.observe({id:'job',status:'waiting_approval',started_utc:new Date(now-6000).toISOString(),finished_utc:new Date(now-1000).toISOString()});
assert.match(first.details.children[0].textContent,/等待确认.*任务用时 5秒/);
first.finish('completed');first.observe({id:'job',status:'running'});
assert.match(first.details.children[0].textContent,/等待确认/);assert.equal(timers.size,0);
const historical=ui.createWork(container,null,{historical:true});
assert.equal(historical.details.children[0].textContent,'执行记录');
assert.doesNotMatch(historical.details.children[0].textContent,/秒/);
ui.reset(container);const active=ui.createWork(container);assert.equal(timers.size,1);
ui.reset(container);assert.equal(timers.size,0);
const bubble=new Element(),input='<img src=x onerror=alert(1)>';
ui.renderMarkdown(bubble,input);assert.equal(bubble.textContent,input);assert.equal(bubble.style.whiteSpace,'pre-wrap');
window.marked={parse:()=>'<unsafe>'};window.DOMPurify={sanitize:(html,options)=>{assert.equal(html,'<unsafe>');assert.equal(options.USE_PROFILES.html,true);return '<p>safe</p>';}};
ui.renderMarkdown(bubble,'markdown');assert.equal(bubble.innerHTML,'<p>safe</p>');assert.equal(bubble.style.whiteSpace,'');
""")


def test_streamed_tool_prelude_is_folded_but_final_answer_and_approval_remain():
    source = (ROOT/'static/index.html').read_text(encoding='utf-8')
    send = source.split('        async function handleSend() {', 1)[1].split("        newChatTopBtn.addEventListener", 1)[0]
    node(DOM + (ROOT/'static/chat-presentation.js').read_text(encoding='utf-8') + r"""
let currentSessionId='session',currentUsername='user',busy=false,backgroundRunning=false,backgroundJobId=null,backgroundTimer;
let historyLoading=false,attachmentUploading=false,reviewPreparing=false,requirementSubmitting=false,attachmentDrafts=[],abortController=null;
const input={value:'查询对象',style:{},focus(){}};
const messagesDiv=new Element(),typingIndicator=new Element();typingIndicator.querySelector=()=>({textContent:''});messagesDiv.append(typingIndicator);
const updateControls=()=>{},scrollToBottom=()=>{},renderAttachmentDrafts=()=>{},monitorBackground=()=>{};
const refreshSessionsList=async()=>{},refreshBackground=()=>{},showNotice=()=>{};
function createBubble(role){const wrapper=new Element(),bubble=new Element();wrapper.className='msg-wrapper '+role+'-wrapper';wrapper.append(bubble);messagesDiv.insertBefore(wrapper,typingIndicator);return bubble;}
function appendMessage(role,text){const bubble=createBubble(role);bubble.textContent=text;return bubble;}
const renderMarkdown=(bubble,text)=>window.ChatPresentation.renderMarkdown(bubble,text);
const events=[{type:'chat_job',id:'job',status:'running',session_id:'session'},
 {type:'message_saved',session_id:'session'}, {type:'stream_start'}, {type:'stream_chunk',content:'正在核对定义'},
 {type:'step',func:'get_designer_entity'}, {type:'stream_start'}, {type:'stream_chunk',content:'最终答复'},
 {type:'stream_end'}, {type:'chat_job',id:'job',status:'waiting_approval',session_id:'session'}];
const payload=new TextEncoder().encode(events.map(e=>'data: '+JSON.stringify(e)+'\n\n').join(''));
const fetch=async()=>({ok:true,headers:{get:name=>name==='X-Chat-Job-ID'?'job':'session'},body:new ReadableStream({start(c){c.enqueue(payload);c.close();}})});
""" + 'async function handleSend() {' + send + r"""
(async()=>{
 await handleSend();
 const replies=messagesDiv.children.filter(x=>x.className==='msg-wrapper agent-wrapper');
 assert.equal(replies.length,1);assert.equal(replies[0].children[0].textContent,'最终答复');
 const work=messagesDiv.children.find(x=>x.className==='work-summary');
 assert.match(work.children[0].textContent,/等待确认/);assert.doesNotMatch(work.children[0].textContent,/回复已结束/);
 assert(work.children[1].children.some(x=>x.className==='msg-wrapper agent-wrapper'&&x.children[0].textContent==='正在核对定义'));
 assert.equal(timers.size,0);
})().catch(error=>{console.error(error);process.exitCode=1});
""")
