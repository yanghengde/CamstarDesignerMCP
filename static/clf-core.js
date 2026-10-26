/* Core workspace: metadata and inspections do not create conversations or replay services. */
(() => {
    'use strict';
    const $=id=>document.getElementById(id), query=new URLSearchParams(location.search), base='/api/clf/workspace';
    const state={username:'',boot:null,context:null,project:null,definition:null,call:0,binding:'',inspection:null,
        rules:null,records:null,offset:0,epoch:0,ruleEpoch:0,detailEpoch:0,recordEpoch:0,inspectEpoch:0,
        operation:'',editingOperation:null,catalog:null,schema:null,operationEpoch:0,assistant:null,asking:false,poll:null,planSource:null};
    let contextQueue=Promise.resolve();
    const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
    const button=(text,fn,cls='button')=>{const b=node('button',text,cls);b.type='button';b.onclick=()=>{if(b.disabled)return;b.disabled=true;Promise.resolve().then(fn).catch(showError).finally(()=>{if(b.isConnected)b.disabled=false;});};return b;};
    const val=v=>v===null?'null':v===undefined?'未取得':typeof v==='object'?JSON.stringify(v,null,2):v===''?'（空字符串）':String(v);
    const formatTime=v=>new Intl.DateTimeFormat('zh-CN',{dateStyle:'short',timeStyle:'medium',timeZone:'Asia/Shanghai'}).format(new Date(v))+' UTC+08:00';
    const labels={success:'成功',rejected:'业务拒绝',unknown:'未知',passed:'已配置断言通过',failed:'与预期不符',not_evaluated:'未评估',partial:'部分未评估',running:'处理中',error:'未完成',interrupted:'已中断'};
    const textStatus=v=>labels[v]||v;
    const params=extra=>new URLSearchParams({username:state.username,...extra});
    const notice=(text,error=false)=>{$('coreNotice').textContent=text;$('coreNotice').hidden=!text;$('coreNotice').classList.toggle('error',error);};
    const showError=e=>notice(e.message||String(e),true);
    async function api(path,options={}){const response=await fetch(path,{cache:'no-store',...options,headers:{'Content-Type':'application/json',...options.headers}});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));return data;}
    const send=(path,payload,method='POST')=>api(base+path,{method,body:JSON.stringify({username:state.username,...payload})});
    function fill(select,items,placeholder,selected=''){select.replaceChildren();const p=node('option',placeholder);p.value='';select.append(p);for(const [id,label] of items){const o=node('option',label);o.value=id;select.append(o);}select.value=selected;}
    function observation(label,value){const row=node('div',undefined,'core-observation');row.append(node('span',label),node('span',value));return row;}
    function details(label,value){const d=node('details');d.append(node('summary',label),node('pre',val(value)));return d;}
    function traceView(trace){
        const panel=node('details');panel.append(node('summary',trace?'服务器实际轨迹 · 按调用层次查看':'执行轨迹 · 未取得'));
        if(!trace){panel.append(node('p','本次未返回轨迹，定义顺序不会补充为执行路径。','core-note'));return panel;}
        panel.append(node('p','仅展示服务器实际返回的节点、属性和参数。部署来源未核对时，不与当前定义行强行匹配。','core-note'));
        let count=0,limited=false;
        function visit(item,depth){
            if(!item||typeof item!=='object'||depth>24||count>=1000){limited=true;return null;}count++;
            const part=node('details',undefined,'core-trace-node');part.open=depth<2;
            const label=(item.Name||'轨迹节点')+(item.Value!==undefined&&item.Value!==''?' · '+val(item.Value):'');
            const summary=node('summary',label.slice(0,160));summary.title=label;part.append(summary);
            if(label.length>160)part.append(node('pre',label));
            if(item.ElapsedTime!==undefined)part.append(observation('服务器记录耗时',val(item.ElapsedTime)));
            for(const [name,values] of [['属性',item.Attributes],['服务器返回参数',item.Parameters]])if(values){
                const rows=Array.isArray(values)?values.map(v=>v&&typeof v==='object'?[v.Name??v.name??'原始项',Object.hasOwn(v,'Value')?v.Value:Object.hasOwn(v,'value')?v.value:v]:['原始项',v]):Object.entries(values);
                part.append(node('h3',name),table(['名称','实际返回值'],rows));
            }
            if(Array.isArray(item.Children))for(const child of item.Children){const result=visit(child,depth+1);if(result)part.append(result);}
            return part;
        }
        const root=visit(trace,0);if(root)panel.append(root);if(limited)panel.append(node('p','当前树视图达到显示上限；请在原文中核对其余内容。','core-note'));
        panel.append(details('轨迹完整原文与结构',trace));return panel;
    }
    function table(headers,rows){const t=node('table',undefined,'core-table');const h=t.createTHead().insertRow();headers.forEach(x=>h.append(node('th',x)));for(const values of rows){const r=t.createTBody();const row=r.insertRow();values.forEach(v=>row.insertCell().textContent=val(v));}return t;}
    const guarded=fn=>event=>{event.preventDefault();Promise.resolve().then(()=>fn(event)).catch(showError);};
    function renderAnswer(text){
        const area=$('assistantAnswer');if(window.ChatPresentation&&window.marked&&window.DOMPurify){window.ChatPresentation.renderMarkdown(area,text);return;}area.replaceChildren();let list=null,code=null;
        const inline=(parent,value)=>{for(const part of value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)){if(part.startsWith('**')&&part.endsWith('**'))parent.append(node('strong',part.slice(2,-2)));else if(part.startsWith('`')&&part.endsWith('`'))parent.append(node('code',part.slice(1,-1)));else parent.append(document.createTextNode(part));}};
        for(const line of text.split('\n')){if(line.startsWith('```')){if(code)code=null;else{code=node('pre');area.append(code);}list=null;continue;}if(code){code.append(document.createTextNode(line+'\n'));continue;}if(!line.trim()){list=null;continue;}const match=line.match(/^\d+\.\s+(.*)/);if(match){if(!list){list=node('ol');area.append(list);}const item=node('li');inline(item,match[1]);list.append(item);}else{list=null;const p=node(/^#{1,4}\s/.test(line)?'h3':'p');inline(p,line.replace(/^#{1,4}\s+/,''));area.append(p);}}
    }
    function persist(){
        const snapshot={project_id:state.context.project_id,mode:state.context.mode,view:state.context.view,
            clf_name:state.context.clf_name||'',object_name:state.context.object_name||'',operation_id:state.operation||'',run_id:state.inspection?.record.id||'',
            binding_id:state.binding?Number(state.binding):null,call_id:state.definition?.calls[state.call]?.call_id||null,source_sha256:state.project?.source_sha256||''};
        for(const key of ['project_id','mode','view','clf_name','run_id']){if(snapshot[key])query.set(key,snapshot[key]);else query.delete(key);}
        history.replaceState(null,'','/clf?'+query);
        contextQueue=contextQueue.then(async()=>{const result=await send('/context',{...snapshot,revision:state.context.revision},'PUT');state.context.revision=result.revision;}).catch(showError);
        return contextQueue;
    }
    function setMode(mode){state.context.mode=mode;paintModes();persist();if(mode==='engineer'&&state.project&&!state.rules)searchRules().catch(showError);}
    function setView(view){state.context.view=view;paintModes();persist();if(view==='plan')loadPlans().catch(showError);if(view==='runs'&&state.project)ensureTesting().catch(showError);}
    async function ensureTesting(){
        if(!state.project||!state.context.clf_name)throw new Error('请先选择要验证的规则');
        const values=Object.fromEntries(new URL($('testCurrent').href,location.href).searchParams),key=JSON.stringify(values),epoch=state.epoch;
        const request=(state.testingQueue||Promise.resolve()).catch(()=>{}).then(()=>loadTesting(values,key,epoch));state.testingQueue=request;return request;
    }
    async function loadTesting(values,key,epoch){
        if(epoch!==state.epoch)return;
        if(state.testingKey===key&&window.clfTesting)return window.clfTesting.ready;
        $('testingHost').inert=true;
        if(!window.clfTesting){
            if(!state.testingLoad)state.testingLoad=(async()=>{
                const response=await fetch('/clf-testing-panel',{cache:'no-store'});if(!response.ok)throw new Error('验证表单加载失败');
                const markup=await response.text();if(epoch!==state.epoch)return;
                const template=document.createElement('template');template.innerHTML=markup;const panel=template.content.querySelector('#clfTestingPanel');panel.dataset.query=new URLSearchParams(values).toString();$('testingHost').replaceChildren(panel);panel.querySelector('.cases-panel').before($('engineResult'));
                await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='/testing.js?v=20261025-core2';script.onload=resolve;script.onerror=()=>reject(new Error('验证组件加载失败，请重试'));document.body.append(script);});
                await window.clfTesting.ready;state.testingKey=key;
            })().finally(()=>{state.testingLoad=null;});
            await state.testingLoad;
        }
        if(epoch!==state.epoch||!window.clfTesting)return;
        if(state.testingKey!==key){await window.clfTesting.selectContext(values);state.testingKey=key;}
        if(epoch===state.epoch)$('testingHost').inert=false;
    }
    function paintModes(){
        const business=state.context.mode==='business';$('businessView').hidden=!business;$('engineerView').hidden=business;
        $('modeBusiness').setAttribute('aria-selected',String(business));$('modeEngineer').setAttribute('aria-selected',String(!business));
        $('coreTitle').textContent=business?'业务检查':'工程排查';$('coreDescription').textContent=business?'从一条业务记录开始，查看结论、依据与下一步。':'沿用当前上下文，核对规则、入口配置和真实运行证据。';
        for(const view of ['logic','runs','plan']){$(view+'View').hidden=state.context.view!==view;$('tab'+view[0].toUpperCase()+view.slice(1)).setAttribute('aria-selected',String(state.context.view===view));}
        const q={project_id:state.context.project_id||'',clf_name:state.definition?.name||state.context.clf_name||'',source_sha256:state.project?.source_sha256||''};
        const mapped=state.boot.operations.find(o=>o.id===state.operation),original=state.inspection?.record.spec;
        const entry=original?.clf_name===q.clf_name?original:mapped?.clf_names.includes(q.clf_name)?mapped:null;
        if(entry)for(const key of ['environment_id','artifact_id','service','method'])q[key]=entry[key];
        $('testCurrent').href='/clf-test?'+new URLSearchParams(q);
        $('assistantContext').textContent=state.definition?state.definition.name+' · 当前工作源 '+state.definition.source_sha256.slice(0,12):'先选择一条规则，再围绕当前上下文提问。';
        document.querySelectorAll('[role=tab]').forEach(tab=>tab.tabIndex=tab.getAttribute('aria-selected')==='true'?0:-1);
    }
    function operations(){return state.boot.operations.filter(o=>o.project_id===state.context.project_id);}
    function renderOperations(){
        fill($('businessOperation'),operations().map(o=>[o.id,o.name+(o.effective_status==='active'?'':o.effective_status==='needs_review'?' · 待核对':o.status==='disabled'?' · 已停用':' · 草稿')]),'请选择已配置的业务操作',state.operation);
        const op=operations().find(o=>o.id===state.operation);
        $('operationHint').textContent=op?[op.service+' / '+op.method,...op.limitations].join('；'):operations().length?'选择一个业务操作查看关联记录。工程师可核对和维护映射。':'尚未配置业务操作。可由工程师配置真实服务与目标 CLF；现有运行仍可在工程视图查看。';
        $('configureOperation').textContent=op?'维护当前业务操作':'配置业务操作';
    }
    async function chooseProject(id,{initial=false}={}){
        window.clfTesting?.saveDraft();state.testingKey='';$('testingHost').inert=true;
        const requested=initial?(query.get('clf_name')||state.context.clf_name):'';
        const requestedRun=initial?(query.get('run_id')||state.context.run_id):'';
        if(initial&&requested)$('coreSearch').value=requested;
        const epoch=++state.epoch;++state.ruleEpoch;++state.detailEpoch;++state.recordEpoch;++state.inspectEpoch;clearTimeout(state.poll);
        state.project=null;state.definition=null;state.inspection=null;state.binding='';state.assistant=null;state.rules=null;state.offset=0;
        if(!initial){state.operation='';state.context.clf_name='';state.context.run_id='';$('coreSearch').value='';}
        state.context.project_id=id;$('coreProject').value=id;renderOperations();$('businessResult').hidden=true;$('engineResult').replaceChildren(node('p','选择一条真实运行记录，查看本次结果。','empty-state'));$('callList').replaceChildren();$('parameterView').replaceChildren();$('referencesView').replaceChildren();$('definitionHeading').replaceChildren(node('h2','正在读取工作来源…'));$('ruleList').replaceChildren();$('coreSource').textContent='正在核对来源…';
        if(!id){$('coreSource').textContent='请先选择工作项目';$('definitionHeading').replaceChildren(node('p','尚无工作来源。请先在设计助手建立项目。','empty-state'));$('recordList').replaceChildren();paintModes();return;}
        const project=await api(base+'/project/'+id+'?'+params());if(epoch!==state.epoch)return;state.project=project;
        $('coreSource').textContent='工作源 '+project.source_sha256.slice(0,12)+' · 部署关系待核对';$('definitionHeading').replaceChildren(node('h2','选择一条规则'));paintModes();
        await Promise.all([state.context.mode==='engineer'||query.get('clf_name')?searchRules():Promise.resolve(),loadRecords()]);if(epoch!==state.epoch)return;
        if(requested){await openDefinition(requested,true);paintModes();}
        if(requestedRun)await inspectRun(requestedRun);
        if(!initial)persist();
    }
    async function searchRules(more=false){
        if(!state.project)return;const epoch=++state.ruleEpoch,project=state.context.project_id,search=$('coreSearch').value.trim(),offset=more?(state.rules?.records.length||0):0;
        $('searchRules').disabled=true;
        try{const result=await api(base+'/rules/'+project+'?'+params({search,scope:$('coreScope').value,offset}));if(epoch!==state.ruleEpoch||project!==state.context.project_id)return;
            if(more&&state.rules?.source_sha256!==result.source_sha256)throw new Error('目录来源已变化，请刷新来源后重新搜索');
            state.rules={...result,records:[...(more?state.rules?.records||[]:[]),...result.records]};
            $('ruleList').replaceChildren(...state.rules.records.map(r=>{const b=button('',()=>openDefinition(r.name),'core-rule');b.append(node('strong',r.name),node('small',r.description||'尚无业务说明'));b.setAttribute('aria-pressed',String(r.name===state.context.clf_name));return b;}));
            if(!state.rules.records.length)$('ruleList').append(node('p','没有匹配的规则，可调整关键词或范围。','empty-state'));
            $('ruleCount').textContent=state.rules.records.length+' / '+result.total+' 条';$('moreRules').hidden=state.rules.records.length>=result.total;
        }finally{if(epoch===state.ruleEpoch)$('searchRules').disabled=false;}
    }
    async function openDefinition(name,restoreSelection=false){
        window.clfTesting?.saveDraft();state.testingKey='';$('testingHost').inert=true;
        if(!state.project)return;const epoch=++state.detailEpoch,project=state.context.project_id;
        if(state.inspection&&state.inspection.record.spec.clf_name!==name){state.inspection=null;++state.inspectEpoch;clearTimeout(state.poll);$('businessResult').hidden=true;$('engineResult').replaceChildren(node('p','规则已切换，请选择相符的运行记录。','empty-state'));}
        state.definition=null;state.binding='';$('callList').replaceChildren();$('parameterView').replaceChildren();$('referencesView').replaceChildren();
        $('definitionHeading').replaceChildren(node('p','正在读取真实定义与附着参数…','muted'));
        const d=await api(base+'/definition/'+project+'?'+params({name,source_sha256:state.project.source_sha256}));if(epoch!==state.detailEpoch||project!==state.context.project_id)return;
        state.definition=d;state.context.clf_name=d.name;state.call=0;state.binding='';state.assistant=null;if(!restoreSelection)state.context.mode='engineer';
        if(restoreSelection&&state.context.source_sha256===d.source_sha256){state.call=Math.max(0,d.calls.findIndex(c=>c.call_id===state.context.call_id));if(d.bindings?.some(b=>b.CLFEventMapID===state.context.binding_id))state.binding=String(state.context.binding_id);}
        window.ChatPresentation?.reset($('assistantWork'));$('assistantWork').replaceChildren();$('coreAssistant').hidden=true;$('fullAssistant').hidden=true;$('assistantAnswer').replaceChildren();renderDefinition();paintModes();persist();if(state.context.view==='runs')ensureTesting().catch(showError);
        $('ruleList').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.querySelector('strong')?.textContent===name)));
    }
    function renderDefinition(){
        const d=state.definition;if(!d)return;
        const h=$('definitionHeading');h.replaceChildren(node('h2',d.name),node('p',d.description||'没有已核对的业务说明。','muted'));
        const label=node('label','查看来源'),select=node('select');select.setAttribute('aria-label','查看来源');
        fill(select,(d.bindings||[]).map(b=>[String(b.CLFEventMapID),[b.owner_name,b.field_name,b.Name||b.CLFReference,'绑定 '+b.CLFEventMapID].filter(Boolean).join(' / ')]),'库定义',state.binding);
        select.onchange=()=>{state.binding=select.value;renderCalls();persist();};label.append(select);h.append(label,node('p','工作区 '+d.workspace+' · '+d.calls.length+' 个真实调用；定义顺序不代表本次执行路径。','core-note'));
        if(!d.binding_parameters_available)h.append(node('p','当前组件未返回入口附着参数。库默认值不能替代入口值。','core-note'));
        const src=details('完整来源与版本',{source_file:d.source_file,source_sha256:d.source_sha256,workspace:d.workspace,active_workspace:d.active_workspace});h.append(src);
        const refs=$('referencesView');refs.replaceChildren();renderDeclarations(refs,d);
        const r=d.references;refs.append(node('p',r.available?`官方引用 ${r.total??'未知'} 项${r.truncated?' · 已截断':''}；动态运行范围仍待核对。`:'引用读取失败：'+r.error,'muted'));
        refs.append(table(['对象 / 字段','事件 / 方法'],(r.rows||[]).map(x=>[[x.CDOName,x.FieldName].filter(Boolean).join(' / ')||'详见原文',x.EventName||x.MethodName||x])));
        refs.append(details('引用原文及范围',r));renderCalls();
    }
    const declarationKeys=['Name','Direction','DataType','ParamMultiplicity','TypeMultiplicity','FunctionParmUsage','CDOType','Description','Notes','DefaultValue'];
    const declarationValues=p=>Object.fromEntries(declarationKeys.map(k=>[k,p?.[k]??'']));
    function renderDeclarations(area,d){
        const section=node('section',undefined,'core-declarations');section.append(node('h3','CLF 声明参数 · '+d.declared_parameters.length),node('p','在 CLF 生命周期中由函数传入或设置，使用 CLF:: / DBCLF:: 引用；与调用参数表达式分别维护。','core-note'));
        const editor=d.declaration_editor,impact=editor?.impact;
        if(impact)section.append(node('p',`${impact.binding_count} 个事件绑定 · ${impact.rule_callers.length} 个直接规则调用者。${impact.notice}`,'core-note'));
        for(const p of d.declared_parameters){const row=node('div',undefined,'core-declaration-row'),description=node('div');description.append(node('strong',p.Name||p.name),node('small',[p.Direction,p.DataType,p.ParamMultiplicity,p.TypeMultiplicity,p.CDOType].filter(Boolean).join(' · ')));if(p.Description)description.append(node('p',p.Description,'core-note'));row.append(description);if(editor){const actions=node('div',undefined,'actions');actions.append(button('编辑',()=>editDeclaration('update',p)),button('准备删除',()=>editDeclaration('remove',p)));row.append(actions);}section.append(row);}
        if(editor)section.append(button(impact.customer_owned?'新增声明参数':'准备客户副本及参数',()=>editDeclaration('add')));else section.append(node('p','当前组件未返回声明编辑合同，请刷新或核对组件版本。','core-note'));
        section.append(details('声明参数完整原文与影响范围',{parameters:d.declared_parameters,editor}));area.append(section);
    }
    function editDeclaration(action,parameter){
        const d=state.definition,editor=d?.declaration_editor;if(!editor)throw new Error('请先读取声明参数合同');
        state.declarationEdit={action,parameter,project:state.context.project_id,source:state.project.source_sha256,clf:d.name,editor};
        const v=parameter?declarationValues(parameter):{Name:'',Direction:'In',DataType:'String',ParamMultiplicity:'0..1',TypeMultiplicity:'Scalar',FunctionParmUsage:'',CDOType:'',Description:'',Notes:'',DefaultValue:''};
        $('declarationTitle').textContent={add:'新增 CLF 声明参数',update:'修改 CLF 声明参数',remove:'准备删除 CLF 声明参数'}[action];
        $('declarationNotice').textContent=editor.impact.notice;$('declarationCopy').checked=!editor.impact.customer_owned||!editor.impact.structural_edit_allowed&&action!=='update';
        $('declarationCopy').disabled=!editor.impact.customer_owned;
        $('declarationTarget').value=(d.name.startsWith('Ex_')?d.name.slice(0,27)+'_V2':'Ex_'+d.name.slice(0,27));
        $('declarationName').value=v.Name;$('declarationDescription').value=v.Description;$('declarationNotes').value=v.Notes;$('declarationCdo').value=v.CDOType;
        function choices(id,rows,current){const values=[...rows];if(current!==''&&!values.some(([key])=>key===current))values.push([current,'当前值：'+current]);fill($(id),values,id==='declarationUsage'?'[Default]':'请选择',current);}
        choices('declarationDirection',editor.directions.map(x=>[x,x+' · '+({In:'传入',Out:'传出','In/Out':'传入并传出',Return:'返回值'}[x]||'')]),v.Direction);choices('declarationDataType',editor.data_types.map(x=>[x.Name,x.Name]),v.DataType);
        choices('declarationMultiplicity',editor.param_multiplicities.map(x=>[x.Name,x.Name+' · '+({'0..1':'可不填或单值','1..1':'必填单值','0..*':'可不填或多值','1..*':'至少一个值'}[x.Name]||x.Description)]),v.ParamMultiplicity);choices('declarationTypeMultiplicity',editor.type_multiplicities.map(x=>[x.Name,x.Name+' · '+({Scalar:'单个值',List:'列表',ListOrScalar:'单值或列表'}[x.Name]||x.Description)]),v.TypeMultiplicity);
        choices('declarationUsage',editor.usages.filter(x=>x.FunctionParmUsageId!==0).map(x=>[x.Name,x.Description||x.Name]),v.FunctionParmUsage);
        $('declarationError').hidden=true;paintDeclarationForm();$('declarationDialog').showModal();
    }
    function paintDeclarationForm(){
        const edit=state.declarationEdit;if(!edit)return;const copy=$('declarationCopy').checked,remove=edit.action==='remove',structural=copy||edit.editor.impact.structural_edit_allowed;
        $('declarationTargetLabel').hidden=!copy;$('declarationTarget').required=copy;$('declarationTarget').disabled=!copy;
        for(const id of ['declarationName','declarationDirection','declarationDataType','declarationMultiplicity','declarationTypeMultiplicity','declarationUsage','declarationCdo'])$(id).disabled=remove||!structural;
        $('declarationDescription').disabled=remove;$('declarationNotes').disabled=remove;$('declarationCdoLabel').hidden=$('declarationDataType').value!=='Object';
        $('declarationNotice').textContent=edit.editor.impact.notice+(!structural?' 当前原规则只允许修改说明和备注；结构变更请先选择客户副本。':'')+(remove?' 将准备删除该声明，仍被表达式引用时官方预检会阻止。':'');
    }
    function prepareDeclaration(){
        const edit=state.declarationEdit;if(!edit||edit.project!==state.context.project_id||edit.source!==state.project?.source_sha256||edit.clf!==state.definition?.name)throw new Error('规则上下文已变化，请关闭后重新准备');
        if(state.planSource&&state.planSource!==edit.source)throw new Error('现有方案来源已变化，请先核对方案');
        const ops=JSON.parse($('planOperations').value);if(!Array.isArray(ops))throw new Error('方案操作需要为数组');
        const copy=$('declarationCopy').checked,owner=copy?$('declarationTarget').value.trim():edit.clf;
        if(copy){if(!/^[A-Za-z_][A-Za-z0-9_]{0,29}$/.test(owner)||owner===edit.clf)throw new Error('请输入不同的有效客户副本名称');const existing=ops.find(o=>['copy_clf','create_clf'].includes(o.action)&&o.name===owner);if(existing&&(existing.action!=='copy_clf'||existing.template!==edit.clf))throw new Error('方案已存在另一个同名目标，请核对');if(!existing)ops.push({action:'copy_clf',name:owner,template:edit.clf});}
        const expected=declarationValues(edit.parameter),definition={...expected,Name:$('declarationName').value.trim(),Direction:$('declarationDirection').value,DataType:$('declarationDataType').value,ParamMultiplicity:$('declarationMultiplicity').value,TypeMultiplicity:$('declarationTypeMultiplicity').value,FunctionParmUsage:$('declarationUsage').value,CDOType:$('declarationDataType').value==='Object'?$('declarationCdo').value.trim():'',Description:$('declarationDescription').value,Notes:$('declarationNotes').value};
        const action=edit.action+'_clf_declaration',operation={action,owner};if(edit.action!=='add')Object.assign(operation,{parameter:edit.parameter.Name,expected});if(edit.action!=='remove')operation.definition=definition;
        ops.push(operation);$('planOperations').value=JSON.stringify(ops,null,2);savePlanDraft();$('declarationDialog').close();setView('plan');notice('声明参数变更已加入方案。请检查完整差异与影响范围，尚未保存工作 MDB。');
    }
    function renderCalls(){
        const d=state.definition;if(!d)return;
        $('callList').replaceChildren(...d.calls.map((c,i)=>{const b=button('',()=>{state.call=i;renderCalls();persist();},'core-call');b.setAttribute('aria-pressed',String(i===state.call));b.append(node('span',String(c.sequence).padStart(2,'0')),node('span',c.function||'未取得函数名'));b.lastChild.append(node('small','调用 ID '+c.call_id+(c.active===false?' · 已禁用':'')));return b;}));
        const call=d.calls[state.call],view=$('parameterView');view.replaceChildren();fill($('planParameter'),[],'选择当前函数的参数');
        if(!call){view.append(node('p','该规则没有调用。','muted'));return;}
        const binding=d.bindings?.find(b=>String(b.CLFEventMapID)===state.binding);
        for(const p of call.parameters){const section=node('section');section.append(node('h3',p.name),node('code',p.value,'core-expression'));
            const contract=d.parameter_definitions?.find(f=>f.name===call.function)?.parameters.find(x=>x.Name===p.name);
            section.append(observation('官方方向 / 类型',contract?[contract.Direction,contract.FriendlyDataType||contract.DataType,contract.ParamMultiplicity,contract.CDOType].filter(Boolean).join(' / '):'当前回执未提供参数类型定义'));
            if(contract?.Description)section.append(node('p',contract.Description,'core-note'));
            const attached=binding?.attached_parameters?.filter(x=>x.CLFFunctionID===call.call_id&&x.Name===p.name)||[];
            section.append(observation('库默认表达式',p.value),observation('所选入口实际附着值',!binding?'尚未选择入口':attached.length===1?attached[0].ValueExpression:attached.length?'存在多个候选，需核对原文':'该绑定未返回此参数，不能回填默认值'),observation('本次业务输入',state.inspection?'已取得请求；尚未建立该函数参数与输入字段的映射':'未选择运行记录'),observation('运行时解析值','本次未采集 · 不从定义表达式推算'));
            if(attached.length)section.append(details('附着参数的原始身份与工作区',attached));view.append(section);
        }
        if(!call.parameters.length)view.append(node('p','本次调用没有返回参数。','muted'));
        fill($('planParameter'),call.parameters.map(p=>[p.name,p.name]),'选择当前函数的参数');
    }
    async function loadRecords(){
        if(!state.project)return;const epoch=++state.recordEpoch,project=state.context.project_id;
        $('recordList').replaceChildren(node('p','正在查询真实测试回执…','muted'));
        const result=await api(base+'/records?'+params({project_id:project,operation_id:state.operation,search:$('recordSearch').value.trim(),offset:state.offset}));
        if(epoch!==state.recordEpoch||project!==state.context.project_id)return;state.records=result;
        $('recordCount').textContent=result.total+' 条 · 本工具测试记录';$('recordList').replaceChildren(...result.records.map(r=>{
            const row=node('div',undefined,'core-record'),text=node('span',r.clf_name+' · '+r.id.slice(0,12));text.append(node('small',(r.created_utc?formatTime(r.created_utc):'')+' · '+r.service+' / '+r.method+' · '+textStatus(r.status)));row.append(text,button('查看结果',()=>inspectRun(r.id)));return row;
        }));
        if(!result.records.length)$('recordList').append(node('p','当前范围没有记录。可在工程排查准备一次验证；查询零条不表示系统错误。','empty-state'));
        $('recordPrevious').disabled=state.offset===0;$('recordNext').disabled=state.offset+result.records.length>=result.total;
    }
    async function inspectRun(id){
        const epoch=++state.inspectEpoch,project=state.context.project_id;
        const result=await api(base+'/records/'+id+'?'+params());if(epoch!==state.inspectEpoch||project!==state.context.project_id)return;
        if(result.record.spec.project_id!==project)throw new Error('该记录来自另一个工作项目，请先明确切换项目');
        if(state.definition&&state.definition.name!==result.record.spec.clf_name){state.definition=null;state.binding='';$('definitionHeading').replaceChildren(node('h2','所选记录目标：'+result.record.spec.clf_name),node('p','进入工程排查可读取当前规则定义。','muted'));$('callList').replaceChildren();$('parameterView').replaceChildren();$('referencesView').replaceChildren();}
        state.context.clf_name=result.record.spec.clf_name||state.context.clf_name;
        state.inspection=result;window.clfTesting?.selectRun(id);renderInspection($('businessResult'),result);renderInspection($('engineResult'),result);$('businessResult').hidden=false;persist();
        clearTimeout(state.poll);if(result.record.status==='running'&&!window.clfTesting)state.poll=setTimeout(()=>inspectRun(id).catch(showError),2000);
    }
    function renderInspection(area,r){
        const record=r.record,spec=record.spec;area.replaceChildren();
        const conclusion=node('div',undefined,'core-evidence'+(!r.target_evidence.length||r.business_outcome==='unknown'?' warn':''));conclusion.append(node('h2',r.title),node('p',r.message||'没有返回业务提示。'));area.append(conclusion);
        const facts=node('div',undefined,'core-outcome');for(const [label,value] of [['请求执行',({queued:'等待执行',preparing:'前置校验',invoking:'正在调用',readback:'独立回读',completed:'已返回',rejected_before_invoke:'执行前阻止',readback_failed:'回读失败',unknown_after_invoke:'调用后结果未知'})[record.execution_phase]||textStatus(record.status)],['业务返回',textStatus(r.business_outcome)],['用例断言',textStatus(r.assertion_result)],['目标 CLF 名称证据',r.target_evidence.length?'已取得':'未取得，不能推定未执行'],['部署来源关系','待核对'],['业务记录',record.id],['发生时间',record.created_utc?formatTime(record.created_utc):'未取得'],['目标环境',record.result?.environment?.name||spec.environment_id+' · 修订 '+spec.environment_version],['真实入口',spec.service+' / '+spec.method],['当前工作源',spec.source_sha256===state.project?.source_sha256?'与本次工作源相同':'来自另一工作源版本']])facts.append(observation(label,value));area.append(facts,node('h3','下一步'),node('p',r.next_step));
        const actions=node('div',undefined,'actions');
        actions.append(button('进入工程排查',async()=>{setMode('engineer');setView('runs');if(spec.clf_name)await openDefinition(spec.clf_name);setView('runs');}),button('复制为测试用例',async()=>{const c=await send('/records/'+record.id+'/copy',{name:'复现 · '+spec.clf_name+' · '+record.id.slice(0,8)});setMode('engineer');state.context.clf_name=spec.clf_name;setView('runs');await ensureTesting();await window.clfTesting.loadCase(c.id);notice(c.notice);}));
        const exp=node('a','导出排查信息','button');exp.href=base+'/records/'+record.id+'/export?'+params();actions.append(exp);area.append(actions);
        if(area.id==='engineResult'){
            const compareBox=node('details'),label=node('label','与另一次运行对比'),select=node('select');select.setAttribute('aria-label','对比运行');
            fill(select,(state.records?.records||[]).filter(x=>x.id!==record.id).map(x=>[x.id,x.clf_name+' · '+x.id.slice(0,12)]),'请选择同项目运行');label.append(select);const result=node('div');
            compareBox.append(node('summary','比较输入、版本与结果'),label,button('对比两次运行',async()=>{if(!select.value)throw new Error('请选择另一条运行');const comparison=await api(base+'/compare?'+params({left:record.id,right:select.value}));result.replaceChildren(node('p',comparison.notice,'core-note'),table(['项目','本次','另一次'],comparison.rows.filter(x=>!x.same).map(x=>[x.name,x.left,x.right])));}),result);area.append(compareBox);
        }
        area.append(details('原始业务输入与预期',{data:spec.data,expected_outcome:spec.expected_outcome,message_contains:spec.message_contains,assertions:spec.assertions}),traceView(r.trace),details('完整回执、版本与限制',{...record,limitations:r.limitations}));
    }
    function draftKey(){return 'clf-core-plan:'+state.username+':'+state.context.project_id;}
    function savePlanDraft(){state.planSource=state.planSource||state.project?.source_sha256;sessionStorage.setItem(draftKey(),JSON.stringify({operations:$('planOperations').value,requirement:$('planRequirement').value,source_sha256:state.planSource}));$('planResult').replaceChildren();$('planSummary').textContent='方案已变更，需重新检查';}
    function restorePlanDraft(){let d={};try{d=JSON.parse(sessionStorage.getItem(draftKey())||'{}');}catch{}state.planSource=d.source_sha256||state.project?.source_sha256;$('planOperations').value=d.operations||'[]';$('planRequirement').value=d.requirement||'';$('planSummary').textContent=d.operations?'已恢复未提交方案，需核对来源':'尚无变更操作';$('planResult').replaceChildren();
        if(state.planSource&&state.project&&state.planSource!==state.project.source_sha256){$('planSummary').textContent='草稿来自旧工作源。请核对操作中的身份和原值，再采用当前来源。';$('planResult').append(button('已核对差异，采用当前来源',()=>{state.planSource=state.project.source_sha256;savePlanDraft();}));}}
    async function checkPlan(){
        if(!state.project)throw new Error('请先选择项目');if(state.planSource&&state.planSource!==state.project.source_sha256)throw new Error('草稿来源已变化，请先核对差异并采用当前来源');const project=state.context.project_id,epoch=state.epoch,source=state.project.source_sha256;
        const operations=JSON.parse($('planOperations').value);if(!Array.isArray(operations)||!operations.length)throw new Error('请先加入变更操作');$('checkPlan').disabled=true;
        try{const result=await send('/plans',{project_id:project,source_sha256:source,operations,requirements:[$('planRequirement').value].filter(Boolean)});if(epoch!==state.epoch)return;showPlan($('planResult'),result.result,result.id);await loadPlans();}finally{$('checkPlan').disabled=false;}
    }
    function planValue(operation,keys,fallback){for(const key of keys)if(Object.hasOwn(operation,key))return operation[key];return fallback;}
    function planRows(o){
        if(o.action==='copy_clf')return [[o.name+' / 客户规则','来源：'+o.template,'准备独立客户副本']];
        if(!o.action.endsWith('_clf_declaration'))return [[[o.owner,o.call_id,o.parameter,o.event].filter(Boolean).join(' / ')||o.name,planValue(o,['expected_value','expected_function','expected_clf'],'详见完整操作'),planValue(o,['value','function','clf'],o.action)]];
        const labels={Name:'参数名',Direction:'方向',DataType:'数据类型',ParamMultiplicity:'参数数量',TypeMultiplicity:'值类型',FunctionParmUsage:'特殊处理',CDOType:'具体对象类型',Description:'说明',Notes:'备注',DefaultValue:'默认值'};
        const before=o.expected,after=o.definition,rows=[];
        for(const key of declarationKeys){if(before&&after&&before[key]===after[key])continue;if(!before&&!after?.[key]||!after&&!before?.[key])continue;rows.push([o.owner+' / '+(before?.Name||after?.Name)+' / '+labels[key],before?before[key]:'（未声明）',after?after[key]:'（删除）']);}
        return rows.length?rows:[[o.owner+' / '+o.parameter,'原声明','无参数属性变化']];
    }
    function showPlan(area,r,planId){area.replaceChildren(node('h3',r.metadata_preflight_passed?'官方方案预检通过':'方案存在冲突'),node('p','预检时：'+r.notice,'core-note'),table(['修改位置','原值','拟定值'],r.operations.flatMap(planRows)),details('冲突与完整预演回执',r));if(planId){const delivery=node('section',undefined,'core-delivery');area.append(delivery);renderDelivery(delivery,planId,r).catch(showError);}}
    async function loadPlans(){if(!state.project)return;const project=state.context.project_id,epoch=state.epoch;const data=await api(base+'/plans?'+params({project_id:project}));if(epoch!==state.epoch)return;$('planHistory').replaceChildren(...data.plans.map(p=>{const d=node('details');d.append(node('summary',formatTime(p.updated_utc)+' · '+(p.result.metadata_preflight_passed?'预检通过':'存在冲突')+(p.result.source_sha256!==state.project.source_sha256?' · 旧工作源':'')));const body=node('div');showPlan(body,p.result,p.id);d.append(body);return d;}));}
    async function renderDelivery(area,planId,plan){
        const epoch=state.epoch,data=await api(base+'/delivery?'+params({plan_id:planId}));if(!area.isConnected||epoch!==state.epoch)return;
        area.replaceChildren(node('h3','实施与交付回执'),node('p','保存只修改当前项目工作 MDB。编译生成独立产物；发布检查覆盖整个项目周期，不会自动发布数据库。','core-note'));
        const saved=data.receipts.find(r=>r.action==='save'),compiled=data.receipts.find(r=>r.action==='compile'&&r.status==='completed'&&r.expected_sha256===saved?.result?.saved_sha256);
        const restored=data.receipts.some(r=>r.action==='restore'&&r.status==='completed');
        const busy=data.receipts.some(r=>['queued','saving','compiling','checking_publish','restoring'].includes(r.status));
        const review=node('label',undefined,'check'),check=node('input');check.type='checkbox';review.append(check,document.createTextNode('我已核对该方案差异、目标项目与当前来源，确认本次操作范围'));area.append(review);
        const controls=node('div',undefined,'actions');
        for(const [action,label,enabled,hash] of [
            ['save','保存到工作 MDB',!saved&&plan.metadata_preflight_passed&&plan.source_sha256===state.project?.source_sha256,plan.source_sha256],
            ['compile','编译已保存版本',saved?.status==='saved'&&!restored,saved?.result?.saved_sha256],
            ['check_publish','检查项目发布',Boolean(compiled)&&!restored,saved?.result?.saved_sha256],
            ['restore','恢复本次保存前快照',saved?.status==='saved'&&!restored,saved?.result?.saved_sha256]]){
            const b=button(label,async()=>{
                if(!check.checked)throw new Error('请先核对方案和操作范围');
                const key='clf-delivery:'+state.username+':'+planId+':'+action;if(!['save','restore'].includes(action)&&data.receipts.some(r=>r.action===action&&!['queued','compiling','checking_publish'].includes(r.status)))sessionStorage.removeItem(key);let identity=sessionStorage.getItem(key);if(!identity){identity=crypto.randomUUID().replaceAll('-','');sessionStorage.setItem(key,identity);}
                await send('/delivery',{plan_id:planId,request_id:identity,action,expected_sha256:hash,reviewed:true});
                await renderDelivery(area,planId,plan);
            },action==='save'?'primary':'button');b.disabled=!enabled||busy;controls.append(b);
        }
        area.append(controls);
        const reporting=node('section');area.append(button('查看验收与交付清单',async()=>{const result=await api(base+'/plans/'+planId+'/report?'+params());reporting.replaceChildren(node('h3','验收与交付清单 · '+(result.overall==='needs_review'?'需要核对':'待完成验收')),node('p',result.notice,'core-note'));const statuses={passed:'预检通过',conflict:'存在冲突',verified:'对应版本证据已核验',restored:'已恢复，保留历史',needs_review:'需要核对',historical:'历史回执，需重新核验',missing:'尚无对应证据',observed:'已有回执，范围待验收',not_recorded:'尚无该方案回执',applicability_unknown:'适用范围与交付待核对',unknown:'待核对'};reporting.append(table(['阶段','当前证据'],result.stages.map(s=>[s.name,statuses[s.status]||s.status])));const list=node('ul');for(const message of result.blockers)list.append(node('li',message));reporting.append(node('h3','未完成项与限制'),list,details('源版本、环境和测试范围',result));const download=node('a','导出本次交付清单','button');download.href=base+'/plans/'+planId+'/report/export?'+params();reporting.append(download);}),reporting);
        if(saved?.status==='saved')area.append(node('p',restored?'已恢复本次保存前快照。历史保存回执保留；后续变更需重新预检。':'已保存并回读工作 MDB。当前规则和旧测试用例需核对新来源；尚未发布数据库。','core-note'),button('读取已保存工作源',async()=>{await chooseProject(state.context.project_id,{initial:true});restorePlanDraft();await loadPlans();}));
        if(saved?.status==='saved'){const files=node('div',undefined,'actions');for(const [kind,label] of [['baseline','下载保存前快照'],['changes','下载官方差异'],['manifest','下载保存清单']]){const a=node('a',label,'button');a.href=base+'/delivery/'+saved.id+'/files/'+kind+'?'+params();files.append(a);}area.append(files);}
        const status={queued:'等待执行',saving:'官方保存与回读中',saved:'工作 MDB 已保存并回读',compiling:'官方编译中',checking_publish:'项目发布检查中',restoring:'恢复工作 MDB 快照中',completed:'本阶段已完成',needs_review:'来源可能已变化，需要核对',failed:'操作未完成',interrupted:'服务中断，结果待核对'};
        for(const receipt of data.receipts){const d=details(({save:'保存',compile:'编译',check_publish:'发布检查',restore:'恢复快照'}[receipt.action])+' · '+(status[receipt.status]||receipt.status),receipt);if(receipt.error)area.append(node('p',receipt.error+'；'+(receipt.notice||''),'core-delivery-error'));area.append(d);}
        if(busy)setTimeout(()=>{if(area.isConnected&&epoch===state.epoch)renderDelivery(area,planId,plan).catch(showError);},1500);
    }

    async function operationEditor(){
        if(!state.project)throw new Error('请先选择项目');state.editingOperation=operations().find(o=>o.id===state.operation)||null;
        const o=state.editingOperation;$('operationName').value=o?.name||'';$('operationClf').value=o?.clf_names.join(',')||state.definition?.name||'';$('operationStatus').value=o?.status||'draft';$('operationError').textContent='';
        fill($('operationEnvironment'),state.boot.environments.map(e=>[e.id,e.name]),'请选择环境',o?.environment_id||'');fill($('operationArtifact'),state.boot.artifacts.map(a=>[a.id,a.id.slice(0,12)+' · '+new Date(a.created*1000).toLocaleDateString()]),'请选择客户端',o?.artifact_id||'');
        fill($('operationService'),[],'请先选择客户端');fill($('operationMethod'),[],'请先选择服务');$('operationDialog').showModal();if(o)await loadOperationCatalog(o.service,o.method);
    }
    async function loadOperationCatalog(service='',method=''){
        const epoch=++state.operationEpoch,id=$('operationArtifact').value;state.catalog=null;state.schema=null;fill($('operationService'),[],'正在读取…');fill($('operationMethod'),[],'请选择服务');if(!id)return;
        const data=await api('/api/testing/catalog/'+id+'?'+params());if(epoch!==state.operationEpoch)return;state.catalog=data;fill($('operationService'),data.services.map(s=>[s,s]),'请选择真实服务',service);if(service)await loadOperationSchema(method);
    }
    async function loadOperationSchema(method=''){
        const epoch=++state.operationEpoch,id=$('operationArtifact').value,service=$('operationService').value;state.schema=null;fill($('operationMethod'),[],'正在读取…');if(!service)return;
        const schema=await api('/api/testing/catalog/'+id+'/'+encodeURIComponent(service)+'?'+params());if(epoch!==state.operationEpoch)return;state.schema=schema;fill($('operationMethod'),schema.methods.map(m=>[m.name,m.name+(m.writes?' · 可能写入':' · 读取')]),'请选择实际方法',method);
    }
    async function saveOperation(){
        if(!state.project||!state.catalog||!state.schema)throw new Error('请先选择真实客户端、服务和方法');const env=state.boot.environments.find(e=>e.id===$('operationEnvironment').value);if(!env)throw new Error('请选择环境');
        const o=state.editingOperation,payload={revision:o?.revision||0,name:$('operationName').value.trim(),project_id:state.context.project_id,source_sha256:state.project.source_sha256,environment_id:env.id,environment_version:env.version,artifact_id:$('operationArtifact').value,client_sha256:state.catalog.client_sha256,service:$('operationService').value,method:$('operationMethod').value,clf_names:$('operationClf').value.split(',').map(s=>s.trim()).filter(Boolean),status:$('operationStatus').value};
        $('saveOperation').disabled=true;
        try{const saved=await send('/operations'+(o?'/'+o.id:''),{operation:payload},o?'PUT':'POST');state.boot.operations=state.boot.operations.filter(x=>x.id!==saved.id).concat(saved);state.operation=saved.id;renderOperations();$('operationDialog').close();state.offset=0;await loadRecords();persist();notice('业务操作已保存。服务签名与目标定义已核对，实际触发关系仍需运行证据。');}finally{$('saveOperation').disabled=false;}
    }
    async function ask(){
        if(!state.definition||!state.project)throw new Error('请先选择规则');if(state.asking)return;const message=$('coreQuestion').value.trim();if(!message)throw new Error('请填写问题');
        const epoch=state.epoch,name=state.definition.name;state.asking=true;$('sendCoreQuestion').disabled=true;$('assistantAnswer').textContent='正在分析当前规则…';
        window.ChatPresentation?.reset($('assistantWork'));$('assistantWork').replaceChildren();const work=window.ChatPresentation?.createWork($('assistantWork'));let outcome='completed';
        try{const response=await fetch(base+'/assistant',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:state.username,project_id:state.context.project_id,source_sha256:state.project.source_sha256,clf_name:name,message,session_id:state.assistant,run_id:state.inspection?.record.spec.clf_name===name?state.inspection.record.id:'',binding_id:state.binding?Number(state.binding):null,call_id:state.definition.calls[state.call]?.call_id||null})});if(!response.ok)throw new Error((await response.json()).detail||'助手请求失败');
            if(epoch!==state.epoch||name!==state.definition?.name){await response.body.cancel();return;}
            state.assistant=response.headers.get('X-Chat-Session-ID');$('fullAssistant').href='/clf?session_id='+encodeURIComponent(state.assistant);$('fullAssistant').hidden=false;
            const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',answer='';
            while(true){const part=await reader.read();if(part.done)break;buffer+=decoder.decode(part.value,{stream:true});const chunks=buffer.split('\n\n');buffer=chunks.pop();for(const chunk of chunks){const line=chunk.split('\n').find(x=>x.startsWith('data: '));if(!line)continue;let event;try{event=JSON.parse(line.slice(6));}catch{continue;}if(epoch!==state.epoch||name!==state.definition?.name)continue;if(event.type==='chat_job')work?.observe(event);else if(event.type==='step'){work?.step(event.func);if(answer){work?.explanation(answer);answer='';$('assistantAnswer').replaceChildren();}}else if(event.type==='stream_start')answer='';else if(event.type==='done')answer=event.reply||answer;else if(event.type==='error'){answer=event.message;outcome='failed';}else if(event.type==='stream_chunk'||event.type==='chunk'||event.type==='content')answer+=event.content||event.text||'';if(answer)renderAnswer(answer);}}
            if(epoch===state.epoch&&name===state.definition?.name&&!answer)$('assistantAnswer').textContent='分析已结束，完整结果与工具证据可在对话中查看。';
        }catch(error){outcome='failed';throw error;}finally{work?.finish(outcome);state.asking=false;$('sendCoreQuestion').disabled=false;}
    }
    $('testCurrent').onclick=guarded(()=>ensureTesting());
    document.addEventListener('clf:test-result',event=>{const r=event.detail?.record;if(r?.spec.project_id===state.context.project_id&&r.spec.clf_name===state.context.clf_name){notice('');inspectRun(r.id).catch(showError);}});
    document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));
    document.querySelectorAll('[role=tablist]').forEach(group=>group.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const tabs=[...group.querySelectorAll('[role=tab]')],index=tabs.indexOf(document.activeElement);if(index<0)return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();tabs[next].click();});
    $('coreProject').onchange=guarded(async()=>{await chooseProject($('coreProject').value);restorePlanDraft();});$('coreSearchForm').onsubmit=guarded(()=>searchRules());$('moreRules').onclick=guarded(()=>searchRules(true));
    $('refreshCore').onclick=guarded(async()=>{await contextQueue;const data=await api(base+'/bootstrap?'+params());state.boot=data;for(const key of ['revision','binding_id','call_id','source_sha256'])state.context[key]=data.context[key];await chooseProject(state.context.project_id,{initial:true});restorePlanDraft();notice('已重新读取来源；旧方案和回执仍保留原版本。');});
    $('findRecords').onclick=guarded(()=>{state.offset=0;return loadRecords();});$('recordSearch').onkeydown=e=>{if(e.key==='Enter')$('findRecords').click();};
    $('recordPrevious').onclick=guarded(()=>{state.offset=Math.max(0,state.offset-20);return loadRecords();});$('recordNext').onclick=guarded(()=>{state.offset+=20;return loadRecords();});
    $('businessOperation').onchange=guarded(async()=>{state.operation=$('businessOperation').value;state.inspection=null;clearTimeout(state.poll);++state.inspectEpoch;$('businessResult').hidden=true;state.offset=0;renderOperations();await loadRecords();persist();});
    $('showRunHistory').onclick=()=>{setMode('business');state.operation='';renderOperations();loadRecords().catch(showError);};
    $('newInspection').onclick=guarded(async()=>{query.delete('run_id');query.delete('clf_name');history.replaceState(null,'','/clf');state.inspection=null;state.operation='';state.context.run_id='';state.context.clf_name='';state.definition=null;state.assistant=null;state.binding='';++state.inspectEpoch;++state.detailEpoch;clearTimeout(state.poll);$('businessResult').hidden=true;$('engineResult').replaceChildren(node('p','选择一条记录开始新的排查。','empty-state'));$('coreAssistant').hidden=true;await chooseProject(state.context.project_id);setMode('business');});
    $('configureOperation').onclick=guarded(operationEditor);$('closeOperation').onclick=()=>$('operationDialog').close();$('operationArtifact').onchange=guarded(()=>loadOperationCatalog());$('operationService').onchange=guarded(()=>loadOperationSchema());$('operationForm').onsubmit=e=>{e.preventDefault();saveOperation().catch(error=>$('operationError').textContent=error.message);};
    $('parameterPlan').onsubmit=guarded(()=>{const c=state.definition?.calls[state.call],name=$('planParameter').value,p=c?.parameters.find(p=>p.name===name);if(!p)throw new Error('请先选择函数与参数');let ops=JSON.parse($('planOperations').value);if(!Array.isArray(ops))throw new Error('方案操作需要为数组');ops.push({action:'set_clf_parameter',owner:state.definition.name,call_id:c.call_id,parameter:name,expected_value:p.value,value:$('planValue').value});$('planOperations').value=JSON.stringify(ops,null,2);savePlanDraft();notice('调用参数变更已加入方案，未保存 MDB。');});
    $('planForm').onsubmit=guarded(checkPlan);$('planOperations').oninput=savePlanDraft;$('planRequirement').oninput=savePlanDraft;
    $('closeDeclaration').onclick=()=>$('declarationDialog').close();$('declarationCopy').onchange=paintDeclarationForm;$('declarationDataType').onchange=paintDeclarationForm;
    $('declarationForm').onsubmit=event=>{event.preventDefault();try{prepareDeclaration();}catch(error){$('declarationError').textContent=error.message;$('declarationError').hidden=false;}};
    $('askCore').onclick=()=>{$('coreAssistant').hidden=false;paintModes();$('coreQuestion').focus();};$('closeCoreAssistant').onclick=()=>$('coreAssistant').hidden=true;$('coreAskForm').onsubmit=guarded(ask);
    (async()=>{state.username=(await api('/config')).username;state.boot=await api(base+'/bootstrap?'+params());state.context=state.boot.context;state.operation=state.context.operation_id||'';
        const project=query.get('project_id')||state.context.project_id||(state.boot.projects.length===1?state.boot.projects[0].id:'');
        fill($('coreProject'),state.boot.projects.map(p=>[p.id,'项目 '+p.id.slice(0,8)+' · InSite.mdb']),'请选择工作项目',project);
        if(project!==state.context.project_id){state.context.clf_name='';state.context.run_id='';state.operation='';}
        if(query.get('clf_name')){state.context.mode='engineer';state.context.clf_name=query.get('clf_name');}if(['business','engineer'].includes(query.get('mode')))state.context.mode=query.get('mode');if(query.get('view'))state.context.view=['logic','runs','plan'].includes(query.get('view'))?query.get('view'):'logic';
        paintModes();await chooseProject(project,{initial:true});restorePlanDraft();if(state.context.view==='plan')await loadPlans();if(!state.boot.projects.length)notice('尚无工作项目。请先在设计助手建立工作 MDB。');
    })().catch(showError);
})();
