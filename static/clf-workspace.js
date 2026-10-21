/* The shared chat engine owns conversation execution; this panel only reads metadata. */
(() => {
    'use strict';
    const isClf=location.pathname==='/clf'||location.pathname==='/clf/records';
    let api, projectList=[], currentContext={}, activeSession=null, lastHistory=null, generation=0, searchVersion=0, searchResult=null, latestResult=null, rulesKey='', historyResultKey='';
    const node=(tag,text,className)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;};
    const value=item=>item===null||item===undefined?'—':typeof item==='object'?JSON.stringify(item,null,2):item===''?'（空）':String(item);
    const caption=text=>node('p',text,'clf-caption');
    const action=(text,handler)=>{const n=node('button',text,'clf-button');n.type='button';n.onclick=handler;return n;};
    const table=(headers,rows)=>{const t=node('table',undefined,'clf-table');const head=t.createTHead().insertRow();headers.forEach(text=>head.appendChild(node('th',text)));const body=t.createTBody();rows.forEach(items=>{const row=body.insertRow();items.forEach(item=>row.insertCell().textContent=value(item));});return t;};
    const message=text=>{if(api)api.notice(text);};
    const params=()=>new URLSearchParams({username:api.username(),session_id:api.session()});
    const content=()=>document.getElementById('clfDetails');
    const sourceInfo=(area,result)=>{const d=node('details');d.appendChild(node('summary','读取来源与版本'));d.append(caption('与设计助手共用项目工作 MDB。Designer 中保存的新改动，请先在设计进度中同步后再读取。'),node('code',result.source_file),node('code',result.source_sha256));area.appendChild(d);};

    function renderClf(area,result,title='函数调用顺序') {
        area.append(node('h3',result.name),caption(result.description||'没有说明'));
        const tags=node('div',undefined,'clf-state');
        const testLink=node('a','在测试页验证','clf-button');testLink.href='/clf-test?'+new URLSearchParams({session_id:api.session(),project_id:currentContext.project_id||'',clf_name:result.name});area.appendChild(testLink);
        tags.append(node('span',`工作区 ${result.workspace??'未知'}`,'clf-tag'),node('span',`${result.calls.length} 个调用`,'clf-tag'));
        area.append(tags,node('h4',title));
        if(!result.calls.length)area.appendChild(caption('没有读取到函数调用。'));
        const list=node('ol',undefined,'clf-flow');
        for(const call of result.calls){const item=node('li',undefined,'clf-call');const heading=node('div',undefined,'clf-call-title');heading.append(node('span',String(call.sequence??'—').padStart(2,'0'),'clf-seq'),node('strong',call.function||'未返回函数名'));item.appendChild(heading);item.appendChild(caption(`调用 ID ${call.call_id??'未知'}${call.active===false?' · 已禁用':''}`));const details=node('details');details.appendChild(node('summary',`${call.parameters.length} 项调用参数`));details.appendChild(table(['参数','表达式'],call.parameters.map(p=>[p.name,p.value])));item.appendChild(details);list.appendChild(item);}
        area.appendChild(list);
        const declared=node('details');declared.appendChild(node('summary',`CLF 声明参数 · ${result.declared_parameters.length}`));
        if(result.declared_parameters.length)declared.appendChild(table(['名称','定义'],result.declared_parameters.map(p=>[p.Name||p.name,p])));else declared.appendChild(caption('当前 CLF 没有声明参数。'));
        area.appendChild(declared);
        if(result.references){const refs=result.references;area.appendChild(node('h4','引用与事件入口'));
            if(!refs.available)area.appendChild(node('p',`引用未读到：${refs.error}`,'clf-error'));
            else {area.appendChild(caption(`官方检索返回 ${refs.total??'未知数量'} 项${refs.truncated?'，当前结果已截断':''}；动态调用和实际运行影响仍需另行核对。`));area.appendChild(table(['对象 / 字段','事件 / 方法'],refs.rows.map(row=>[row.CDOName?[row.CDOName,row.FieldName].filter(Boolean).join('.'):'引用定义',row.EventName||row.MethodName||row])));}
        }
    }

    function renderResult(result) {
        if(!isClf||!content())return;
        if(currentContext.mdb_file===result.source_file)currentContext.source_sha256=result.source_sha256;
        latestResult=result;const area=content();area.replaceChildren();
        if(result.format==='designer_clf_v1'){renderClf(area,result);sourceInfo(area,result);const btn=action('让助手解释此 CLF',()=>api.sendText(`请解释 ${result.name} 的处理顺序、参数含义和事件入口，并说明需要人工核实的部分。`));area.prepend(btn);return;}
        area.appendChild(node('h3','CLF 方案预览'));
        const status=node('div',undefined,'clf-state');status.append(node('span',result.metadata_preflight_passed?'内存预检通过':'内存预检未通过',`clf-tag ${result.metadata_preflight_passed?'ok':'pending'}`),node('span','尚未保存','clf-tag'),node('span','尚未发布','clf-tag'),node('span','业务运行未验证','clf-tag pending'));area.append(status,caption(result.notice));
        if(result.requirements?.length){area.appendChild(node('h4','本次规则'));for(const text of result.requirements)area.appendChild(caption(text));}
        if(result.conflicts?.length){area.appendChild(node('h4','待解决问题'));for(const item of result.conflicts)area.appendChild(node('p',item.message||value(item),'clf-error'));}
        const labels={create_clf:'新建 CLF',copy_clf:'复制 CLF',add_clf_function:'增加函数调用',replace_clf_function:'替换函数调用',remove_clf_function:'移除函数调用',reorder_clf_functions:'调整调用顺序',set_clf_parameter:'调整调用参数',bind_event:'绑定事件',replace_event_binding:'替换事件绑定',unbind_event:'解除事件绑定'};
        area.append(node('h4','拟执行操作'),table(['动作','参数'],result.operations.map(op=>[labels[op.action]||op.action,Object.fromEntries(Object.entries(op).filter(([key])=>key!=='action'))])));
        for(const [label,items] of [['修改前',result.before],['内存预演后（未保存）',result.after]]){const section=node('details');section.appendChild(node('summary',label));for(const item of items)renderClf(section,item);area.appendChild(section);}
        area.appendChild(node('h4','测试用例建议 · 尚未执行'));
        if(result.test_cases.length)area.appendChild(table(['用例 / 输入','预期结果'],result.test_cases.map(test=>[`${test.name}\n${value(test.inputs)}`,`${test.expected}\n状态：未执行`])));else area.appendChild(caption('还需补充正例、反例、空值及更新时未提交字段等测试用例。'));
        sourceInfo(area,result);
    }

    function renderRules(context) {
        const area=document.getElementById('clfRules');area.replaceChildren();
        const handoff=context.excel_handoff, rules=context.uploaded_rules||handoff?.rules||[];
        if(!rules.length)return;
        area.append(node('h3',`Excel 业务规则 · ${rules.length}`),caption('以下是原始需求，尚未实现或运行验证；对象或字段缺失时需要进一步确认。'));
        if(handoff){const back=node('a','返回原设计对话','clf-caption');back.href=`/?session_id=${encodeURIComponent(handoff.source_session_id)}`;area.appendChild(back);}
        for(const rule of rules){const card=node('div',undefined,'clf-rule');card.append(node('small',`${rule.file} · ${rule.sheet}!${rule.cell}${rule.hidden?'（隐藏表）':''}`),caption(`${rule.owner||'待确认对象'} / ${rule.field||'待确认字段'}${rule.trigger?' · '+rule.trigger:''}`),node('p',rule.rule));if(rule.error_message)card.appendChild(caption(`错误提示：${rule.error_message}`));area.appendChild(card);}
        area.appendChild(action('分析这些业务规则',()=>api.sendText('请逐条分析右侧 Excel 业务规则，核对对象、字段、触发时机与现有 CLF。说明字段属性、CLF 或页面配置各自承担哪些要求；保留单元格来源，先提出方案与测试用例，不保存。')));
    }

    async function selectProject(projectId,clfName='') {
        const session=api.session(), token=++generation;
        const context=await api.fetchJson('/api/clf/context',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:api.username(),session_id:session,project_id:projectId,clf_name:clfName})});
        if(token!==generation||session!==api.session())return;
        currentContext=context;renderRules(context);document.getElementById('clfProject').value=projectId;
        document.getElementById('clfSource').textContent=context.mdb_file||'';
        if(!clfName){searchResult=null;latestResult=null;content().replaceChildren(caption('搜索并选择 CLF，查看真实函数、参数和引用；也可以直接在左侧描述业务需求。'));await search();}
        return context;
    }

    async function openClf(name,checksum) {
        const session=api.session(), token=++generation;
        try{const query=params();query.set('name',name);query.set('expected_sha256',checksum);
            const result=await api.fetchJson('/api/clf/detail?'+query);
            if(token!==generation||session!==api.session())return;
            const selected=await selectProject(document.getElementById('clfProject').value,name);
            if(!selected||session!==api.session())return;
            renderResult(result);
        }catch(error){if(session===api.session())message(error.message);}
    }

    async function search(more=false) {
        if(!api||!currentContext.project_id)return;
        const session=api.session(),token=++searchVersion,projectId=currentContext.project_id;
        const query=params();query.set('search',document.getElementById('clfSearch').value.trim());query.set('offset',more?searchResult?.records.length||0:0);
        const button=document.getElementById('clfSearchBtn');button.disabled=true;
        try{const result=await api.fetchJson('/api/clf/list?'+query);
            if(token!==searchVersion||session!==api.session()||projectId!==currentContext.project_id)return;
            if(more&&searchResult&&result.source_sha256!==searchResult.source_sha256){searchResult=null;throw Error('MDB 已更新，请重新搜索后读取');}
            searchResult={...result,records:[...(more?searchResult?.records||[]:[]),...result.records]};
            const list=document.getElementById('clfList');list.replaceChildren();
            for(const row of searchResult.records){const item=action(row.name,()=>openClf(row.name,result.source_sha256));item.title=row.description||row.name;list.appendChild(item);}
            document.getElementById('clfListCount').textContent=`已显示 ${searchResult.records.length} / ${result.total} 个 CLF`;
            document.getElementById('clfMore').hidden=searchResult.records.length>=result.total;
        }catch(error){if(session===api.session())message(error.message);}finally{if(token===searchVersion)button.disabled=false;}
    }

    async function loadSession(data) {
        if(!api)return;
        const session=api.session(),token=++generation;++searchVersion;
        activeSession=session;currentContext={};searchResult=null;latestResult=null;rulesKey='';historyResultKey='';content().replaceChildren(caption('正在读取工作项目…'));document.getElementById('clfRules').replaceChildren();document.getElementById('clfList').replaceChildren();
        try{const [projects,context]=await Promise.all([api.fetchJson('/api/clf/projects?'+params()),api.fetchJson('/api/clf/context?'+params())]);
            if(token!==generation||session!==api.session())return;
            projectList=projects.projects;currentContext=context;const select=document.getElementById('clfProject');select.replaceChildren(node('option','请选择工作项目'));select.firstChild.value='';
            for(const item of projectList){const option=node('option',`项目 ${item.id.slice(0,8)} · InSite.mdb`);option.value=item.id;option.title=item.mdb_file;select.appendChild(option);}
            select.value=context.project_id||'';renderRules(context);document.getElementById('clfSource').textContent=context.mdb_file||'与设计助手共用工作 MDB，选择后开始读取。';
            if(!context.project_id&&projectList.length===1)await selectProject(projectList[0].id);
            else if(context.project_id)await search();
            else content().replaceChildren(caption(projectList.length?'请选择本次分析使用的工作项目。':'尚无项目工作 MDB。请先在设计助手建立项目，再回到这里。'));
            if(session!==api.session())return;
            const result=data?.clf_results?.at(-1);
            if(result&&result.source_sha256===currentContext.source_sha256)renderResult(result);
            else if(context.clf_name&&searchResult)await openClf(context.clf_name,searchResult.source_sha256);
            else if(currentContext.project_id)content().replaceChildren(caption('搜索并选择 CLF，查看真实函数、参数和引用；也可以直接在左侧描述业务需求。'));
        }catch(error){if(session===api.session())content().replaceChildren(node('p',error.message,'clf-error'));}
    }

    async function fromExcel(username,sessionId,ids) {
        const response=await fetch('/api/clf/from-excel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,source_session_id:sessionId,attachment_ids:ids})});
        const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'规则带入失败');location.assign(data.url);
    }

    function configure() {
        if(!isClf)return;
        document.body.classList.add('clf-mode');document.getElementById('newChatTopBtn').lastChild.textContent='新建 CLF 对话';
        document.getElementById('chatColumn').setAttribute('aria-label','CLF 逻辑对话');
        document.getElementById('inputBox').setAttribute('aria-label','业务规则需求');
        const records=document.getElementById('recordsNav');records.href='/clf/records';records.lastChild.textContent='CLF 记录';
        document.querySelector('#recordsView h2').textContent='每一段逻辑，都有迹可循。';
        document.querySelector('#recordsView .records-description').textContent='打开 CLF 历史对话，继续查看业务规则、引用与方案预览。';
        document.getElementById('guideToggle').lastChild.textContent='CLF 指南';
        document.getElementById('designGuide').innerHTML='<div class="guide-title">CLF 分析与方案预览</div><p class="guide-intro">从已有逻辑出发，明确业务规则、事件入口和验证范围。</p><ol><li><strong>选择工作项目</strong><p>与设计助手使用同一个已保存的工作 MDB。</p></li><li><strong>读取已有逻辑</strong><p>搜索 CLF，查看真实函数顺序、调用参数、声明参数和引用入口。</p></li><li><strong>说明业务规则</strong><p>明确触发时机、默认值、允许值、跨字段条件与错误提示；空值和未提交字段需要分别处理。</p></li><li><strong>审核方案与用例</strong><p>核对预演前后差异和冲突。当前阶段不保存或发布，用例建议保持未执行。</p></li></ol><div class="guide-note"><strong>Excel 规则列</strong><p>可使用对象名、字段名、业务规则（或 CLF要求）、触发时机、错误提示。缺少归属时会保留原始单元格，供进一步确认。</p></div>';
        const welcome=document.getElementById('welcomeTemplate').content;
        welcome.querySelector('.eyebrow').textContent='CLF LOGIC WORKSPACE';welcome.querySelector('h2').textContent='读懂逻辑，再完善业务规则。';
        welcome.querySelector('.welcome-description').textContent='结合真实函数、参数与事件，解释现有 CLF，预览规则方案。当前阶段只读分析，不保存或发布。';
        const prompts=[['解释现有 CLF','理解函数顺序、参数和触发入口','请解释右侧选择的 CLF；如果尚未选择，请先帮助我检索相关 CLF。'],['分析业务规则','明确默认值、校验与异常处理','请帮我规划字段业务规则。先确认目标对象、触发时机、输入输出与异常提示，再读取真实函数提出方案，不保存。'],['核对影响与测试','查看引用范围，补充业务用例','请分析右侧 CLF 的引用和事件入口，列出修改时的影响范围及需要验证的正例、反例、空值与未提交字段场景。']];
        welcome.querySelectorAll('.prompt-card').forEach((card,index)=>{const item=prompts[index];card.querySelector('strong').textContent=item[0];card.querySelector('p').textContent=item[1];card.dataset.prompt=item[2];});
        welcome.querySelector('.welcome-footnote').textContent='方案预检、MDB 保存、发布和业务运行验证分别记录。';
        const panel=node('aside',undefined,'clf-inspector');panel.setAttribute('aria-label','CLF 逻辑详情');
        panel.innerHTML='<h2>逻辑详情</h2><p class="clf-caption">读取解释 · 影响分析 · 方案预检</p><div class="clf-state"><span class="clf-tag ok">只读分析</span><span class="clf-tag">共用项目 MDB</span></div><div class="clf-controls"><label for="clfProject">工作项目</label><select id="clfProject"><option>正在加载…</option></select><p id="clfSource" class="clf-caption"></p><label for="clfSearch">搜索 CLF 名称</label><form id="clfSearchForm" class="clf-search"><input id="clfSearch" placeholder="输入名称或关键词" autocomplete="off"><button id="clfSearchBtn" class="clf-button primary" type="submit">查询</button></form><p id="clfListCount" class="clf-caption"></p><div id="clfList" class="clf-list"></div><button id="clfMore" class="clf-button" type="button" hidden>加载更多</button></div><div id="clfRules"></div><div id="clfDetails"><p class="clf-caption">正在准备工作区…</p></div>';
        document.querySelector('.content-layout').appendChild(panel);
        document.getElementById('clfSearchForm').onsubmit=event=>{event.preventDefault();search();};document.getElementById('clfMore').onclick=()=>search(true);
        document.getElementById('clfProject').onchange=event=>{if(event.target.value)selectProject(event.target.value).catch(error=>{event.target.value=currentContext.project_id||'';message(error.message);});else event.target.value=currentContext.project_id||'';};
    }

    configure();
    function history(data){
        lastHistory=data;
        if(!api)return;
        if(location.pathname==='/clf/records')return;
        if(activeSession!==api.session()){loadSession(data);return;}
        const result=data.clf_results?.at(-1), resultKey=JSON.stringify([result?.id,result?.source_sha256]);
        const key=JSON.stringify((data.messages||[]).filter(m=>m.role==='user').map(m=>[m.content?.slice(0,80),m.attachments?.map(item=>[item.id,item.sha256])]));
        if(rulesKey!==key||historyResultKey!==resultKey){rulesKey=key;historyResultKey=resultKey;const session=api.session(),token=generation;
            api.fetchJson('/api/clf/context?'+params()).then(context=>{
                if(session!==api.session()||token!==generation)return;
                currentContext=context;renderRules(context);
                if(result&&result.source_sha256===context.source_sha256&&(result.id!==latestResult?.id||result.source_sha256!==latestResult?.source_sha256))renderResult(result);
                else if(result&&result.source_sha256!==context.source_sha256&&!latestResult)content().replaceChildren(caption('历史结果来自其他 MDB 版本，请重新读取或预检。'));
            }).catch(error=>{if(session===api.session()&&token===generation)document.getElementById('clfRules').replaceChildren(node('p',error.message,'clf-error'));});
        }
    }
    window.clfWorkspace={
        async initialize(options){api=options;if(location.pathname!=='/clf/records')await loadSession(lastHistory);},
        history,
        result:renderResult,fromExcel,
    };
})();
