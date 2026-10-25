/* The shared chat engine owns conversation execution; this panel only reads metadata. */
(() => {
    'use strict';
    const isClf=location.pathname==='/clf'||location.pathname==='/clf/records';
    let api, projectList=[], currentContext={}, activeSession=null, lastHistory=null, generation=0, searchVersion=0, searchResult=null, latestResult=null, rulesKey='', historyResultKey='', objectVersion=0, objectTimer;
    const node=(tag,text,className)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;};
    const value=item=>item===null||item===undefined?'—':typeof item==='object'?JSON.stringify(item,null,2):item===''?'（空）':String(item);
    const caption=text=>node('p',text,'clf-caption');
    const action=(text,handler)=>{const n=node('button',text,'clf-button');n.type='button';n.onclick=handler;return n;};
    const table=(headers,rows)=>{const t=node('table',undefined,'clf-table');const head=t.createTHead().insertRow();headers.forEach(text=>head.appendChild(node('th',text)));const body=t.createTBody();rows.forEach(items=>{const row=body.insertRow();items.forEach(item=>row.insertCell().textContent=value(item));});return t;};
    const message=text=>{if(api)api.notice(text);};
    const params=()=>new URLSearchParams({username:api.username(),session_id:api.session()});
    const content=()=>document.getElementById('clfDetails');
    const sourceInfo=(area,result)=>{const d=node('details');d.appendChild(node('summary','读取来源与版本'));d.append(caption('与设计助手共用项目工作 MDB。Designer 中保存的新改动，请先在设计进度中同步后再读取。'),node('code',result.source_file),node('code',result.source_sha256));area.appendChild(d);};

    function renderClfTechnical(area,result,title='函数调用顺序') {
        area.append(node('h3',result.name),caption(result.description||'没有说明'));
        const tags=node('div',undefined,'clf-state');
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

    function renderClf(area,result,title='现有规则') {
        const description=(result.description||'').trim();
        area.append(node('h3',title),node('strong',result.name,'clf-rule-name'),caption(description&&description!==result.name&&!/^[A-Za-z_][A-Za-z0-9_]*$/.test(description)?description:'业务含义尚需核对，可点击“解释规则”查看业务说明。'));
        const refs=result.references, rows=refs?.rows||[];
        const entry=rows.map(r=>[r.CDOName,r.FieldName,r.EventName||r.MethodName].filter(Boolean).join(' / '));
        area.append(caption(entry.length?'已声明关联：'+entry.slice(0,3).join('；'):'触发入口待确认。'));
        if(!refs?.available||refs.truncated)area.append(node('p','引用范围未完整确认，请先核对实际业务入口。','clf-error'));
        if(rows.some(r=>r.MethodName&&!r.EventName))area.append(caption('自定义方法关联仍需确认实际调用入口。'));
        const controls=node('div',undefined,'clf-actions');
        controls.append(action('解释规则',()=>api.sendText(`请用业务语言解释 ${result.name}。只简述当前规则、触发条件及必须确认的入口；完整函数和参数留在技术详情。`,{submit:true})),action('调整规则',()=>{
            document.getElementById('clfRuleForm').open=true;document.getElementById('clfCondition').focus();
        }));
        const link=node('a','验证规则','clf-button');
        link.href='/clf-test?'+new URLSearchParams({session_id:api.session(),project_id:currentContext.project_id||'',clf_name:result.name,source_sha256:result.source_sha256||currentContext.source_sha256||''});
        controls.append(link);area.append(controls);
        const details=node('details',undefined,'clf-technical');details.append(node('summary',`技术详情 · ${result.calls.length} 个调用`));
        renderClfTechnical(details,result);sourceInfo(details,result);area.append(details);
    }

    function operationSummary(op){
        if(op.action==='set_clf_parameter')return [op.owner+' / '+op.parameter,value(op.expected_value)+' → '+value(op.value)];
        if(op.action==='replace_clf_function')return [op.owner,op.expected_function+' → '+op.function];
        if(op.action==='add_clf_function')return [op.owner,'增加步骤：'+op.function];
        if(op.action==='remove_clf_function')return [op.owner,'移除步骤：'+op.expected_function];
        if(op.action==='reorder_clf_functions')return [op.owner,'调整已有步骤顺序'];
        if(op.action==='copy_clf')return [op.name,'基于 '+op.template+' 准备客户规则副本'];
        if(op.action==='create_clf')return [op.name,'准备新规则'];
        return [[op.owner,op.field,op.event].filter(Boolean).join(' / '),(op.expected_clf||'未绑定')+' → '+(op.clf||'解除绑定')];
    }

    function renderResult(result) {
        if(!isClf||!content())return;
        if(currentContext.mdb_file===result.source_file)currentContext.source_sha256=result.source_sha256;
        latestResult=result;document.getElementById('clfRulePicker').open=false;const area=content();area.replaceChildren();
        if(result.format==='designer_clf_v1'){renderClf(area,result);return;}
        area.appendChild(node('h3','规则方案'));
        const status=node('div',undefined,'clf-state');status.append(node('span',result.metadata_preflight_passed?'方案检查通过 · 尚未保存':'方案需要调整',`clf-tag ${result.metadata_preflight_passed?'ok':'pending'}`));area.append(status,caption('方案检查为内存预演；保存、发布和实际业务验证仍需后续完成。'));
        if(result.requirements?.length){area.appendChild(node('h4','本次规则'));for(const text of result.requirements)area.appendChild(caption(text));}
        if(result.conflicts?.length){area.appendChild(node('h4','待解决问题'));for(const item of result.conflicts)area.appendChild(node('p',item.message||value(item),'clf-error'));}
        const labels={create_clf:'新建 CLF',copy_clf:'复制 CLF',add_clf_function:'增加函数调用',replace_clf_function:'替换函数调用',remove_clf_function:'移除函数调用',reorder_clf_functions:'调整调用顺序',set_clf_parameter:'调整调用参数',bind_event:'绑定事件',replace_event_binding:'替换事件绑定',unbind_event:'解除事件绑定'};
        area.append(node('h4','本次调整'),table(['调整位置','修改前 → 修改后'],result.operations.map(operationSummary)));
        const implementation=node('details',undefined,'clf-technical');implementation.append(node('summary','技术变更与完整定义'),caption(result.notice),table(['动作','参数'],result.operations.map(op=>[labels[op.action]||op.action,Object.fromEntries(Object.entries(op).filter(([key])=>key!=='action'))])));
        for(const [label,items] of [['修改前',result.before],['内存预演后（未保存）',result.after]]){const section=node('details');section.appendChild(node('summary',label));for(const item of items)renderClfTechnical(section,item);implementation.appendChild(section);}
        sourceInfo(implementation,result);area.append(implementation);
        area.appendChild(node('h4','测试用例建议 · 尚未执行'));
        if(result.test_cases.length)area.appendChild(table(['用例 / 输入','预期结果'],result.test_cases.map(test=>[`${test.name}\n${value(test.inputs)}`,`${test.expected}\n状态：未执行`])));else area.appendChild(caption('还需补充正例、反例、空值及更新时未提交字段等测试用例。'));
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

    async function loadObjects(){
        if(!currentContext.project_id)return;
        const token=++objectVersion,session=api.session(),project=currentContext.project_id,query=params();
        query.set('search',document.getElementById('clfObjectSearch').value.trim());query.set('selected',currentContext.object_name||'');
        const result=await api.fetchJson('/api/clf/objects?'+query);
        if(token!==objectVersion||session!==api.session()||project!==currentContext.project_id)return;
        const select=document.getElementById('clfObject');select.replaceChildren(node('option','全部对象'));select.firstChild.value='';
        const labels={Product:'产品',ProductMaint:'产品维护',ShopFloor:'车间交易',Resource:'资源',Employee:'员工',Spec:'规范',Workflow:'工作流'};
        for(const item of result.records){const option=node('option',(labels[item.name]||item.label||item.name)+' · '+item.name);option.value=item.name;select.append(option);}
        select.value=currentContext.object_name||'';
        document.getElementById('clfObjectHint').textContent=result.total>50?'可输入对象名称查找更多；规则包含已声明的维护及父类关联。':'规则按已声明的对象关联筛选；动态调用仍需核对。';
    }

    const draftKey=()=>api?'clf-rule-draft:'+api.username()+':'+api.session():'';
    function restoreDraft(){
        let draft={};try{draft=JSON.parse(sessionStorage.getItem(draftKey())||'{}');}catch{}
        for(const id of ['clfTemplate','clfField','clfWhen','clfCondition','clfAction','clfError'])document.getElementById(id).value=draft[id]||(id==='clfTemplate'?'custom':id==='clfAction'?'preserve':'');
        document.getElementById('clfRuleForm').open=false;
    }
    function saveDraft(){const draft={};for(const id of ['clfTemplate','clfField','clfWhen','clfCondition','clfAction','clfError'])draft[id]=document.getElementById(id).value;sessionStorage.setItem(draftKey(),JSON.stringify(draft));}
    function planRule(event){
        event.preventDefault();if(!currentContext.project_id){message('请先选择设计项目');return;}
        const kind=document.getElementById('clfTemplate').value,field=document.getElementById('clfField').value.trim(),condition=document.getElementById('clfCondition').value.trim();
        if(kind!=='custom'&&!field){message('请填写需要检查的业务字段');return;}
        if(kind!=='required'&&!condition){message('请填写规则条件或希望调整的内容');return;}
        if(kind==='length'&&(!/^\d+$/.test(condition)||Number(condition)<1||!Number.isSafeInteger(Number(condition)))){message('长度上限应为正整数');return;}
        const templates={required:'字段必填'+(condition?'；附加条件：'+condition:''),length:'最大字符数为 '+condition,range:'允许范围为 '+condition,allowed:'仅允许以下值：'+condition,custom:condition};
        const clfName=latestResult?.format==='designer_clf_v1'?latestResult.name:currentContext.clf_name;
        const text=['请检查以下业务规则的修改方案：',
            '业务对象：'+(document.getElementById('clfObject').value||'请根据当前规则核实'),
            '已有规则：'+(clfName||'请先检索相关规则和实际入口'),
            '业务字段：'+(field||'根据规则核实'),
            '触发时机：'+(document.getElementById('clfWhen').value.trim()||'核对原有绑定后提出候选，存在歧义时列出待确认项'),
            '检查条件：'+templates[kind],
            '处理方式：'+({preserve:'沿用现有处理方式',block:'不符合条件时阻止操作并提示',existing:'满足条件时执行已有业务处理，具体动作按调整要求核实'}[document.getElementById('clfAction').value]||'沿用现有处理方式'),
            '错误提示：'+(document.getElementById('clfError').value.trim()||'沿用现有提示'),
            '请给出正常、异常和边界用例建议。'].join('\n');
        saveDraft();api.sendText(text,{submit:true});
    }

    async function selectProject(projectId,clfName='') {
        const session=api.session(), token=++generation;
        const context=await api.fetchJson('/api/clf/context',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:api.username(),session_id:session,project_id:projectId,clf_name:clfName,object_name:document.getElementById('clfObject').value})});
        if(token!==generation||session!==api.session())return;
        currentContext=context;renderRules(context);document.getElementById('clfProject').value=projectId;
        document.getElementById('clfSource').textContent=context.mdb_file||'';
        document.getElementById('clfProjectCaption').textContent='当前设计项目 · '+projectId.slice(0,8);
        document.getElementById('clfProjectDetails').open=false;
        if(!clfName){searchResult=null;latestResult=null;document.getElementById('clfRulePicker').open=true;content().replaceChildren(caption('选择相关规则，或直接描述需要调整的业务要求。'));await loadObjects();await search();}
        return context;
    }

    async function openClf(name,checksum) {
        const session=api.session(), token=++generation;
        try{const query=params();query.set('name',name);query.set('expected_sha256',checksum);
            const result=await api.fetchJson('/api/clf/detail?'+query);
            if(token!==generation||session!==api.session())return;
            const owners=[...new Set((result.references?.rows||[]).map(r=>r.CDOName).filter(Boolean))];
            if(!document.getElementById('clfObject').value&&result.references?.available&&!result.references.truncated&&owners.length===1){const option=node('option',owners[0]);option.value=owners[0];document.getElementById('clfObject').append(option);document.getElementById('clfObject').value=owners[0];document.getElementById('clfScope').value='related';}
            const selected=await selectProject(document.getElementById('clfProject').value,name);
            if(!selected||session!==api.session())return;
            renderResult(result);await search();
        }catch(error){if(session===api.session())message(error.message);}
    }

    async function search(more=false) {
        if(!api||!currentContext.project_id)return;
        const session=api.session(),token=++searchVersion,projectId=currentContext.project_id;
        const query=params();query.set('search',document.getElementById('clfSearch').value.trim());query.set('offset',more?searchResult?.records.length||0:0);query.set('object_name',document.getElementById('clfObject').value);query.set('scope',document.getElementById('clfScope').value);
        const button=document.getElementById('clfSearchBtn');button.disabled=true;
        try{const result=await api.fetchJson('/api/clf/rules?'+query);
            if(token!==searchVersion||session!==api.session()||projectId!==currentContext.project_id)return;
            if(more&&searchResult&&result.source_sha256!==searchResult.source_sha256){searchResult=null;throw Error('MDB 已更新，请重新搜索后读取');}
            searchResult={...result,records:[...(more?searchResult?.records||[]:[]),...result.records]};
            const list=document.getElementById('clfList');list.replaceChildren();
            for(const row of searchResult.records){const item=action('',()=>openClf(row.name,result.source_sha256));item.append(node('strong',row.name),node('span',(row.description||'尚无说明').replace(/\s+/g,' ').slice(0,140)));item.title=row.description||row.name;item.classList.toggle('selected',row.name===currentContext.clf_name);list.appendChild(item);}
            if(!searchResult.records.length)list.append(caption('当前范围没有匹配规则，可切换“全部规则”或更换业务对象。'));
            document.getElementById('clfListCount').textContent=`已显示 ${searchResult.records.length} / ${result.total} 条规则`;
            document.getElementById('clfMore').hidden=searchResult.records.length>=result.total;
        }catch(error){if(session===api.session())message(error.message);}finally{if(token===searchVersion)button.disabled=false;}
    }

    async function loadSession(data) {
        if(!api)return;
        const session=api.session(),token=++generation;++searchVersion;
        activeSession=session;currentContext={};searchResult=null;latestResult=null;rulesKey='';historyResultKey='';++objectVersion;content().replaceChildren(caption('正在读取当前设计项目…'));document.getElementById('clfRules').replaceChildren();document.getElementById('clfList').replaceChildren();document.getElementById('clfObject').replaceChildren(node('option','全部对象'));document.getElementById('clfObject').firstChild.value='';document.getElementById('clfObjectSearch').value='';document.getElementById('clfSearch').value='';document.getElementById('clfScope').value='related';restoreDraft();
        try{const [projects,context]=await Promise.all([api.fetchJson('/api/clf/projects?'+params()),api.fetchJson('/api/clf/context?'+params())]);
            if(token!==generation||session!==api.session())return;
            projectList=projects.projects;currentContext=context;const select=document.getElementById('clfProject');select.replaceChildren(node('option','请选择工作项目'));select.firstChild.value='';
            for(const item of projectList){const option=node('option',`项目 ${item.id.slice(0,8)} · InSite.mdb`);option.value=item.id;option.title=item.mdb_file;select.appendChild(option);}
            select.value=context.project_id||'';renderRules(context);document.getElementById('clfSource').textContent=context.mdb_file||'与设计助手共用工作 MDB，选择后开始读取。';
            document.getElementById('clfProjectCaption').textContent=context.project_id?'当前设计项目 · '+context.project_id.slice(0,8):'选择设计项目';document.getElementById('clfProjectDetails').open=!context.project_id&&projectList.length!==1;
            if(!context.project_id&&projectList.length===1)await selectProject(projectList[0].id);
            else if(context.project_id){await loadObjects();document.getElementById('clfScope').value='related';await search();}
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
        document.getElementById('inputBox').setAttribute('aria-label','业务规则需求');document.getElementById('inputBox').placeholder='例如：产品名称超过 40 个字符时，保存时拦截并提示…';
        const records=document.getElementById('recordsNav');records.href='/clf/records';records.lastChild.textContent='CLF 记录';
        document.querySelector('#recordsView h2').textContent='每一段逻辑，都有迹可循。';
        document.querySelector('#recordsView .records-description').textContent='打开 CLF 历史对话，继续查看业务规则、引用与方案预览。';
        document.getElementById('guideToggle').lastChild.textContent='CLF 指南';
        document.getElementById('designGuide').innerHTML='<div class="guide-title">CLF 分析与方案预览</div><p class="guide-intro">从已有逻辑出发，明确业务规则、事件入口和验证范围。</p><ol><li><strong>选择工作项目</strong><p>与设计助手使用同一个已保存的工作 MDB。</p></li><li><strong>读取已有逻辑</strong><p>搜索 CLF，查看真实函数顺序、调用参数、声明参数和引用入口。</p></li><li><strong>说明业务规则</strong><p>明确触发时机、默认值、允许值、跨字段条件与错误提示；空值和未提交字段需要分别处理。</p></li><li><strong>审核方案与用例</strong><p>核对预演前后差异和冲突。当前阶段不保存或发布，用例建议保持未执行。</p></li></ol><div class="guide-note"><strong>Excel 规则列</strong><p>可使用对象名、字段名、业务规则（或 CLF要求）、触发时机、错误提示。缺少归属时会保留原始单元格，供进一步确认。</p></div>';
        const welcome=document.getElementById('welcomeTemplate').content;
        welcome.querySelector('h2').textContent='Opcenter Designer CLF 逻辑助手';
        welcome.querySelector('.welcome-description').textContent='选择右侧业务对象，查看现有规则或直接说明调整要求。助手读取真实逻辑后生成方案，技术细节可按需展开。';
        const panel=node('aside',undefined,'clf-inspector');panel.setAttribute('aria-label','业务规则工作区');
        panel.innerHTML='<h2>业务规则</h2><p class="clf-caption">选对象 → 描述规则 → 检查方案</p><details id="clfProjectDetails"><summary id="clfProjectCaption">正在读取当前设计项目…</summary><label for="clfProject">切换工作项目</label><select id="clfProject"><option>正在加载…</option></select><p id="clfSource" class="clf-caption"></p></details><div class="clf-controls"><label for="clfObjectSearch">业务对象</label><input id="clfObjectSearch" placeholder="搜索对象，例如 Product 或 ShopFloor" autocomplete="off"><select id="clfObject" aria-label="业务对象"><option value="">全部对象</option></select><p id="clfObjectHint" class="clf-caption"></p><div class="clf-rule-filters"><select id="clfScope" aria-label="规则范围"><option value="related">对象相关规则</option><option value="customer" selected>客户规则优先</option><option value="all">全部规则</option></select><form id="clfSearchForm" class="clf-search"><input id="clfSearch" aria-label="搜索业务规则" placeholder="搜索规则名称或说明" autocomplete="off"><button id="clfSearchBtn" class="clf-button" type="submit">查询</button></form></div><p id="clfListCount" class="clf-caption"></p><div id="clfList" class="clf-list"></div><button id="clfMore" class="clf-button" type="button" hidden>加载更多</button></div><details id="clfRuleForm" class="clf-request"><summary>描述或调整规则</summary><form id="clfRuleRequest"><label>规则类型<select id="clfTemplate"><option value="custom">自定义规则</option><option value="required">字段必填</option><option value="length">长度上限</option><option value="range">数值范围</option><option value="allowed">允许值</option></select></label><label>业务字段<input id="clfField" placeholder="例如 ExtName（选填）"></label><label>何时执行<input id="clfWhen" placeholder="例如保存时；未指定则核对已有入口"></label><label>规则条件或调整要求<textarea id="clfCondition" rows="3" placeholder="描述需要允许或阻止的业务情况"></textarea></label><label>错误提示<input id="clfError" placeholder="留空时先核对已有提示"></label><p class="clf-caption">模板用于整理需求；方案检查通过后仍需保存、发布和业务验证。</p><button class="clf-button primary" type="submit">检查方案</button></form></details><div id="clfRules"></div><div id="clfDetails"><p class="clf-caption">正在准备工作区…</p></div>';
        document.querySelector('.content-layout').appendChild(panel);
        const picker=node('details');picker.id='clfRulePicker';picker.open=true;picker.appendChild(node('summary','选择或更换规则'));
        document.querySelector('.clf-rule-filters').before(picker);
        for(const element of [document.querySelector('.clf-rule-filters'),document.getElementById('clfListCount'),document.getElementById('clfList'),document.getElementById('clfMore')])picker.appendChild(element);
        document.getElementById('clfSearchForm').onsubmit=event=>{event.preventDefault();search();};document.getElementById('clfMore').onclick=()=>search(true);
        document.getElementById('clfObjectSearch').oninput=()=>{clearTimeout(objectTimer);objectTimer=setTimeout(()=>loadObjects().catch(e=>message(e.message)),250);};
        document.getElementById('clfObject').onchange=()=>{document.getElementById('clfScope').value='related';selectProject(currentContext.project_id).catch(e=>message(e.message));};
        document.getElementById('clfScope').onchange=()=>search().catch(e=>message(e.message));
        document.getElementById('clfRuleRequest').onsubmit=planRule;
        document.getElementById('clfTemplate').setAttribute('aria-label','规则类型');
        document.getElementById('clfScope').options[1].textContent='当前客户规则';
        const actionLabel=node('label','如何处理'),actionSelect=node('select');actionSelect.id='clfAction';actionSelect.setAttribute('aria-label','如何处理');
        for(const [key,label] of [['preserve','沿用现有处理'],['block','不符合条件时阻止并提示'],['existing','符合条件时执行已有业务处理']]){const option=node('option',label);option.value=key;actionSelect.append(option);}
        actionLabel.append(actionSelect);document.getElementById('clfError').closest('label').before(actionLabel);
        for(const id of ['clfTemplate','clfField','clfWhen','clfCondition','clfAction','clfError'])document.getElementById(id).oninput=saveDraft;
        document.getElementById('clfProject').onchange=event=>{if(event.target.value){document.getElementById('clfObject').value='';selectProject(event.target.value).catch(error=>{event.target.value=currentContext.project_id||'';message(error.message);});}else event.target.value=currentContext.project_id||'';};
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
