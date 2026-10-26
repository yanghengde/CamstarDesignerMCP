/* Shared presentation only: no execution, approval, history or business API calls. */
(() => {
    'use strict';
    const rows=new Map();
    const labels={running:'正在处理',completed:'回复已结束',waiting_approval:'等待确认',failed:'处理未完成',stopped:'已停止',disconnected:'连接中断，后台状态待核对',history:'执行记录'};
    const terminal=new Set(['completed','waiting_approval','failed','stopped']);
    function duration(milliseconds){
        const seconds=Math.max(0,Math.floor(milliseconds/1000));
        return seconds<1?'不足1秒':seconds<60?seconds+'秒':Math.floor(seconds/60)+'分'+String(seconds%60).padStart(2,'0')+'秒';
    }
    function renderMarkdown(element,text){
        element.classList.add('chat-markdown');element.style.whiteSpace='';
        if(window.marked&&window.DOMPurify){
            element.innerHTML=window.DOMPurify.sanitize(window.marked.parse(text),{USE_PROFILES:{html:true}});
            element.querySelectorAll('table').forEach(table=>{const scroll=document.createElement('div');scroll.className='table-scroll';table.replaceWith(scroll);scroll.appendChild(table);});
            if(window.hljs)element.querySelectorAll('pre code').forEach(block=>window.hljs.highlightElement(block));
        }else{element.textContent=text;element.style.whiteSpace='pre-wrap';}
    }
    function resizeComposer(input){
        if(!input.getClientRects().length)return;
        input.style.height='auto';input.style.height=input.scrollHeight+'px';
        input.style.overflowY=input.scrollHeight>input.clientHeight?'auto':'hidden';
    }
    function createWork(container,before=null,{historical=false}={}){
        const details=document.createElement('details'),summary=document.createElement('summary'),steps=document.createElement('div');
        details.className='work-summary';steps.className='work-steps';details.append(summary,steps);container.insertBefore(details,before);
        const note=document.createElement('p');note.textContent=historical?'该轮历史未保留页面计时；详细过程可在运行日志查看。':'记录本次实际返回的处理步骤；工具返回不代表业务验证通过。';steps.append(note);
        let state=historical?'history':'running',jobId='',serverStart=null,serverEnd=null,ended=null,timer=null,disposed=false,confirmedTerminal=false;
        const started=performance.now(),calls=new Map();
        function paint(){
            if(disposed)return;
            const timed=state!=='history';
            const elapsed=serverStart!==null?(serverEnd??Date.now())-serverStart:(ended??performance.now())-started;
            summary.textContent=(labels[state]||labels.history)+(timed?' · '+(serverStart!==null?'任务用时 ':'本页用时 ')+duration(elapsed):'');
            summary.title=serverStart!==null?'按后台任务记录的开始与结束时间计算':'仅本页观察到的等待时间；不推定业务耗时';
        }
        function tick(){
            clearInterval(timer);timer=null;
            if(state==='running'&&!disposed)timer=setInterval(()=>{if(!details.isConnected)api.dispose();else paint();},1000);
        }
        const api={
            details,steps,
            get jobId(){return jobId;},
            step(name,identity='',returned=false){
                if(disposed||!name)return;
                let row=identity&&calls.get(identity);if(!row){row=document.createElement('div');steps.append(row);if(identity)calls.set(identity,row);}
                row.textContent=(returned?'已返回结果：':'调用：')+name;
            },
            explanation(text){if(!text||disposed)return;const block=document.createElement('div');renderMarkdown(block,text);steps.append(block);},
            moveExplanation(element){if(element&&!disposed)steps.append(element);},
            observe(info){
                if(disposed||!info)return;if(jobId&&info.id&&jobId!==info.id)return;
                if(info.id)jobId=info.id;
                if(confirmedTerminal&&info.status==='running')return;
                const begin=Date.parse(info.started_utc),finish=Date.parse(info.finished_utc);
                if(Number.isFinite(begin)&&begin<=Date.now())serverStart=begin;
                if(Number.isFinite(finish)&&serverStart!==null&&finish>=serverStart)serverEnd=finish;
                if(info.status&&Object.hasOwn(labels,info.status)){state=info.status;if(terminal.has(state)){confirmedTerminal=true;ended??=performance.now();}else if(state==='running')ended=null;}
                paint();tick();
            },
            finish(outcome='completed'){if(disposed||confirmedTerminal)return;state=outcome;ended=performance.now();paint();tick();},
            dispose(){disposed=true;clearInterval(timer);timer=null;const list=rows.get(container);if(list){const index=list.indexOf(api);if(index>=0)list.splice(index,1);if(!list.length)rows.delete(container);}}
        };
        if(!rows.has(container))rows.set(container,[]);rows.get(container).push(api);paint();tick();return api;
    }
    function reset(container){for(const row of [...(rows.get(container)||[])])row.dispose();}
    function observe(container,info){const row=rows.get(container)?.at(-1);row?.observe(info);}
    window.ChatPresentation={renderMarkdown,resizeComposer,createWork,reset,observe,duration};
})();
