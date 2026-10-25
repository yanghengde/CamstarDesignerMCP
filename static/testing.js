/* Both test pages use the same typed client, environment store and durable receipts. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const node = (tag, text, className) => { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(className)el.className=className; return el; };
    const query = new URLSearchParams(location.search);
    const kind = location.pathname === '/clf-test' ? 'clf' : 'wcf';
    const state = {username:'',boot:null,context:null,catalog:null,schema:null,fields:new Map(),rows:[],clf:null,clfSearch:null,clfEpoch:0,epoch:0,schemaEpoch:0,pins:null,selectedRun:null,pending:new Set(),submitting:false,timer:null};
    const labels = {running:'正在执行',passed:'交易断言通过',failed:'与预期不符',error:'未完成测试',interrupted:'执行中断，业务结果未知'};
    const stringify = value => JSON.stringify(value,null,2);
    const parseInput = text => JSON.parse(text,(_key,value)=>{if(typeof value==='number'&&Number.isInteger(value)&&!Number.isSafeInteger(value))throw new Error('输入整数超出此页面可精确表示的范围，请缩小测试值。');return value;});
    const notice = (message,error=false) => { $('notice').textContent=message; $('notice').classList.toggle('error',error); $('notice').hidden=!message; };
    const uuid = () => crypto.randomUUID().replaceAll('-','');
    const params = extra => new URLSearchParams({username:state.username,...extra});
    const selectedEnvironment = () => state.boot?.environments.find(env=>env.id===$('environmentSelect').value);
    const selectedArtifact = () => state.boot?.artifacts.find(item=>item.id===$('artifactSelect').value);
    const action = (id,fn) => $(id).addEventListener('click',event=>{event.preventDefault();Promise.resolve().then(fn).catch(error=>notice(error.message,true));});
    async function api(path,options={}) {
        const response=await fetch(path,{cache:'no-store',...options,headers:{'Content-Type':'application/json',...options.headers}});
        const result=await response.json();
        if(!response.ok){const error=new Error(typeof result.detail==='string'?result.detail:stringify(result.detail||result.error||result));error.status=response.status;throw error;}
        return result;
    }
    const send = (path,body,method='POST') => api(path,{method,body:JSON.stringify({username:state.username,...body})});
    function fillSelect(select,items,placeholder,selected='') {
        select.replaceChildren();
        if(placeholder!==null){const option=node('option',placeholder);option.value='';select.append(option);}
        for(const [value,label] of items){const option=node('option',label);option.value=value;select.append(option);}
        select.value=selected;
    }
    function toggle(id,button) {$(id).hidden=!$(id).hidden;$(button).setAttribute('aria-expanded',String(!$(id).hidden));}
    function resetResult(){state.selectedRun=null;$('exportResult').hidden=true;$('result').replaceChildren(node('div','选择输入后执行测试，结果将显示在这里。','empty-state'));}
    function busy(){const running=state.pending.size>0||state.submitting;document.body.dataset.busy=String(running);$('runButton').disabled=running||(kind==='clf'&&(!state.clf||!state.schema||!$('methodSelect').value));$('runBatch').disabled=running;const writes=state.schema?.methods.find(method=>method.name===$('methodSelect').value)?.writes;$('runButton').textContent=running?'测试在后台执行':kind==='clf'?(writes?'验证规则（会写入业务数据）':'验证规则'):writes?'执行写入测试':'执行读取测试';}
    async function requestIdentity(type,body){const key='designer-testing-'+kind+'-'+type;const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(body)));const fingerprint=Array.from(new Uint8Array(bytes),byte=>byte.toString(16).padStart(2,'0')).join('');let pending;try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}if(pending&&pending.fingerprint!==fingerprint)throw new Error('上一提交的回执未确认，请先在测试记录中核对；重试相同输入将沿用原请求标识。');const identity=pending?.id||uuid();sessionStorage.setItem(key,JSON.stringify({id:identity,fingerprint}));return {key,identity};}
    function environmentEditor(create=false) {
        const env=create?null:selectedEnvironment();
        $('environmentEditor').dataset.identity=env?.id||'';
        $('environmentEditor').dataset.version=String(env?.version||0);
        $('environmentName').value=env?.name||'';$('environmentEndpoint').value=env?.endpoint||state.boot?.defaults.endpoint||'';
        $('environmentUser').value=env?.business_user||'';$('environmentPassword').value='';$('environmentPassword').placeholder=env?.has_password?'留空保留已保存密码':'填写业务登录密码';
        $('environmentServer').value=env?.server_name||'';$('environmentTimeout').value=env?.timeout||state.boot?.defaults.timeout||60;$('clearPassword').checked=false;
    }
    async function saveEnvironment(){
        const identity=$('environmentEditor').dataset.identity;
        const environment={name:$('environmentName').value.trim(),endpoint:$('environmentEndpoint').value.trim(),business_user:$('environmentUser').value.trim(),server_name:$('environmentServer').value.trim(),timeout:Number($('environmentTimeout').value),version:Number($('environmentEditor').dataset.version),... (location.pathname==='/wcf-test/settings'?{project_id:$('projectSelect').value,artifact_id:$('artifactSelect').value}:{})};
        if($('clearPassword').checked)environment.password='';else if($('environmentPassword').value)environment.password=$('environmentPassword').value;
        const saved=await send('/api/testing/environments'+(identity?'/'+identity:''),{environment},identity?'PUT':'POST');
        state.boot.environments=state.boot.environments.filter(env=>env.id!==saved.id).concat(saved);
        fillSelect($('environmentSelect'),state.boot.environments.map(env=>[env.id,env.name]),'请选择环境',saved.id);
        environmentEditor();$('environmentState').textContent='配置已保存，业务登录未检查';notice('环境配置已保存，两张测试页面共用。');updatePreview();
    }
    async function checkEnvironment(){
        const env=selectedEnvironment();if(!env)throw new Error('请先保存并选择测试环境');if(!selectedArtifact())throw new Error('请选择客户端产物');
        $('checkEnvironment').disabled=true;$('environmentState').textContent='正在检查真实业务登录…';
        try{const result=await send('/api/testing/environments/'+env.id+'/check',{artifact_id:$('artifactSelect').value});if(result.environment_version!==env.version)throw new Error('测试环境已由其他页面修改，请刷新后重新检查');$('environmentState').textContent=result.login_verified?'业务登录成功 · 部署版本待核对':result.outcome==='error'?'连接或客户端错误':'业务登录被拒绝';notice(result.message||result.notice,!result.login_verified);}
        finally{$('checkEnvironment').disabled=false;}
    }
    async function bootstrap(){
        const old={project:$('projectSelect').value,artifact:$('artifactSelect').value,environment:$('environmentSelect').value};
        const data=await api('/api/testing/bootstrap?'+params(query.get('session_id')?{session_id:query.get('session_id')}:{ }));state.boot=data;
        const linked=location.pathname==='/wcf-test/settings'?data.environments.find(e=>e.id===(old.environment||data.environments[0]?.id)):null;
        const pickProject=old.project||linked?.project_id||query.get('project_id')||data.context.project_id||(kind==='clf'?data.defaults.project_id:'')||(data.projects.length===1?data.projects[0].id:'');
        const pickArtifact=old.artifact||linked?.artifact_id||query.get('artifact_id')||data.context.artifact_id||(kind==='clf'?data.defaults.artifact_id:'')||data.artifacts[0]?.id||'';
        fillSelect($('projectSelect'),data.projects.map(p=>[p.id,p.id.slice(0,8)+' · '+(p.server_mdb||p.mdb_file).split(/[\\/]/).pop()]),'请选择工作项目',pickProject);
        fillSelect($('artifactSelect'),data.artifacts.map(a=>[a.id,new Date(a.created*1000).toLocaleString()+' · '+(a.partial_package?'部分':'全量')+' · '+a.id.slice(0,8)]),'请选择 WCF 产物',pickArtifact);
        fillSelect($('environmentSelect'),data.environments.map(env=>[env.id,env.name]),'请选择测试环境',old.environment||query.get('environment_id')||(kind==='clf'?data.defaults.environment_id:data.environments[0]?.id)||'');
        if(kind==='clf')$('clfDefaultConnection').textContent=$('environmentSelect').value?'使用系统已有环境与业务账号，无需重复填写。':'尚未匹配系统默认业务连接，请展开连接与来源核对。';
        $('writeTargetState').textContent=data.write_target_confirmed?'已配置允许操作的测试目标。':'写入测试尚未启用：需要配置 DESIGNER_TEST_TARGET_CONFIRMED=true。';
        environmentEditor();if(!data.environments.length){$('environmentEditor').hidden=false;$('configureEnvironment').setAttribute('aria-expanded','true');}
        renderCases();renderHistory();
        try{const key='designer-testing-'+kind+'-run';const pending=JSON.parse(sessionStorage.getItem(key)||'null');if(pending&&data.history.some(run=>run.request_id===pending.id))sessionStorage.removeItem(key);}catch{}
        for(const run of data.history)if(run.status==='running')state.pending.add(run.id);
        busy();schedulePoll();await loadSources();
        const recent=data.history.find(run=>run.spec.kind===kind&&run.spec.project_id===$('projectSelect').value&&(kind!=='clf'||state.clf&&run.spec.clf_name===state.clf.name&&run.spec.source_sha256===state.context?.source_sha256));
        if(recent){state.selectedRun=recent.id;renderResult(recent);}
        if(!data.projects.length)notice('尚无工作项目。请先在设计助手中建立工作 MDB，再回到这里测试。');
        else if(!data.artifacts.length)notice('尚无可用 WCF 客户端产物。请先在设计进度中完成 WCF 生成。');
    }
    async function loadSources(){
        const epoch=++state.epoch;++state.schemaEpoch;state.context=null;state.catalog=null;state.schema=null;state.clf=null;state.pins=null;state.loadedCase=null;state.fields.clear();state.rows=[];
        $('caseVersion').hidden=true;$('definitionPanel').hidden=true;resetResult();$('fieldRows').replaceChildren();fillSelect($('methodSelect'),[],'请选择服务');fillSelect($('serviceSelect'),[],null);$('clfList').replaceChildren();$('catalogState').textContent='正在读取来源…';
        const projectId=$('projectSelect').value,artifactId=$('artifactSelect').value;
        const results=await Promise.allSettled([projectId?api('/api/testing/project/'+projectId+'?'+params()):Promise.resolve(null),artifactId?api('/api/testing/catalog/'+artifactId+'?'+params()):Promise.resolve(null)]);
        if(epoch!==state.epoch)return;
        if(results[0].status==='fulfilled')state.context=results[0].value;else notice(results[0].reason.message,true);
        if(results[1].status==='fulfilled')state.catalog=results[1].value;else notice(results[1].reason.message,true);
        $('sourceCaption').textContent=state.context?'工作 MDB '+state.context.source_sha256.slice(0,12)+' · 客户端 '+(state.catalog?.client_sha256||'未选择').slice(0,12):'请选择工作项目';
        $('sourceDetails').textContent=stringify({project:projectId,source_file:state.context?.source_file,source_sha256:state.context?.source_sha256,client_sha256:state.catalog?.client_sha256,compiled_sha256:state.catalog?.compiled_sha256,artifact_project_link:'待核对',deployment_version:'待核对'});
        $('changeList').replaceChildren(...(state.context?.changes||[]).map(change=>node('p',Object.values(change).join(' · '))));
        renderServices();renderCases();
        if(state.catalog?.services.length&&kind!=='clf'){const owners=state.context?.changes.map(c=>c.owner||c.name).filter(Boolean)||[];const candidate=state.catalog.services.find(service=>owners.some(owner=>service===owner+'Maint'))||state.catalog.services.find(service=>service==='ProductMaint')||state.catalog.services[0];$('serviceSelect').value=candidate;await loadService(candidate);}
        if(kind==='clf'&&state.context){const expected=query.get('source_sha256');if(expected&&expected!==state.context.source_sha256){notice('规则来源已更新，请点击“刷新来源”核对当前版本后再验证。',true);return;}await searchClfs(false);const requested=query.get('clf_name')||state.boot.context.clf_name;if(requested)await selectClf(requested);}
    }
    function renderServices(){
        const selected=$('serviceSelect').value,search=$('serviceSearch').value.toLowerCase().trim();
        const services=(state.catalog?.services||[]).filter(name=>name.toLowerCase().includes(search));fillSelect($('serviceSelect'),services.map(name=>[name,name]),null,selected);
        $('catalogState').textContent=state.catalog?services.length+' / '+state.catalog.services.length+' 个真实客户端服务':'选择 WCF 产物后读取目录。';
    }
    function collectFields(schema,prefix=''){
        for(const field of schema.fields||[]){const path=prefix+field.name;state.fields.set(path,field.schema);if(field.schema.kind==='object'&&field.schema.fields)collectFields(field.schema,path+'.');}
    }
    async function loadService(name){
        if(!name)return;const epoch=++state.schemaEpoch,sourceEpoch=state.epoch,artifactId=$('artifactSelect').value;
        state.schema=null;state.fields.clear();state.rows=[];resetResult();$('serviceLabel').textContent=name;$('runButton').disabled=true;
        try{const schema=await api('/api/testing/catalog/'+artifactId+'/'+encodeURIComponent(name)+'?'+params());if(epoch!==state.schemaEpoch||sourceEpoch!==state.epoch)return;
            if(schema.client_sha256!==state.catalog?.client_sha256)throw new Error('读取期间客户端版本已改变，请刷新来源');state.schema=schema;collectFields(schema.fields);
            $('fieldOptions').replaceChildren(...Array.from(state.fields,([name,schema])=>{const option=node('option');option.value=name;option.label=shortType(schema);return option;}));
            fillSelect($('methodSelect'),schema.methods.map(method=>[method.name,method.name+(method.writes?' · 可能写入':' · 读取')]),'请选择实际方法',schema.methods.find(m=>m.name==='Load')?.name||schema.methods[0]?.name||'');
            if(kind==='clf'&&state.fields.has('ObjectToChange.Name')){addField('ObjectToChange.Name');if(state.fields.has('ObjectToChange.Revision'))addField('ObjectToChange.Revision');}
            else if(kind==='clf'&&state.fields.has('Container.Name'))addField('Container.Name');
            else if(state.fields.has('ObjectToChange'))addField('ObjectToChange');
            const changed=new Set((state.context?.changes||[]).map(change=>change.field||change.name));
            for(const [path,field] of state.fields)if(path.startsWith('ObjectChanges.')&&changed.has(path.split('.').pop())&&['primitive','scalar','enum'].includes(field.kind))addField(path);
            if(!state.rows.length){const field=Array.from(state.fields.keys()).find(path=>!path.includes('.'));if(field)addField(field);}
            $('rawMode').checked=false;$('fieldForm').hidden=false;$('rawDataLabel').hidden=true;$('allowWrite').checked=false;$('enableReadback').checked=false;$('readbackDataLabel').hidden=true;renderRows();updateMethod();updatePreview();
        }finally{busy();}
    }
    function shortType(schema){return (schema.value_type||schema.type||'').replace('System.','').replace('Camstar.WCF.ObjectStack.','');}
    function defaultValue(schema){if(schema.kind==='array')return '[]';if(schema.kind==='enum')return schema.values?.[0]||'';if(schema.kind==='object'){const fields=new Set((schema.fields||[]).map(f=>f.name));return stringify(fields.has('Name')?{Name:'',...(fields.has('Revision')?{Revision:''}:{})}:{});}if(shortType(schema)==='Boolean')return 'false';if(/Int|Double|Decimal|Single/.test(shortType(schema)))return '0';return '';}
    function addField(path){if(!state.fields.has(path))throw new Error('请选择客户端契约中存在的字段路径');if(state.rows.some(row=>row.path===path))return;if(state.rows.some(row=>path.startsWith(row.path+'.')||row.path.startsWith(path+'.')))throw new Error('同一对象不能同时使用整对象输入和其子字段输入');const schema=state.fields.get(path);state.rows.push({path,schema,mode:'value',input:defaultValue(schema)});renderRows();updatePreview();}
    function rowValue(row){if(row.mode==='omitted')return undefined;if(row.mode==='null')return null;if(row.mode==='empty')return '';const type=shortType(row.schema);if(row.schema.kind==='object'||row.schema.kind==='array')return parseInput(row.input);if(type==='Boolean')return row.input==='true';if(/^(Int16|Int32|Int64|Double|Decimal|Single)$/.test(type)){if(!row.input.trim()||!Number.isFinite(Number(row.input)))throw new Error(row.path+' 需要有效数值');const value=Number(row.input);if(Number.isInteger(value)&&!Number.isSafeInteger(value))throw new Error(row.path+' 超出此页面可精确表示的整数范围');return value;}return row.input;}
    function formData(){const data={};for(const row of state.rows){const value=rowValue(row);if(value===undefined)continue;const parts=row.path.split('.');let current=data;for(const part of parts.slice(0,-1)){if(!Object.hasOwn(current,part))current[part]={};current=current[part];}current[parts.at(-1)]=value;}return data;}
    function getData(){const data=$('rawMode').checked?parseInput($('rawData').value):formData();if(data===null||Array.isArray(data)||typeof data!=='object')throw new Error('服务数据必须为 JSON 对象');return data;}
    function renderRows(){
        $('fieldRows').replaceChildren();for(const row of state.rows){const area=node('div',undefined,'field-row');const heading=node('div',undefined,'field-row-heading');const label=node('label',row.path);label.append(node('small',shortType(row.schema)));const remove=node('button','移除','button subtle');remove.type='button';remove.setAttribute('aria-label','移除 '+row.path);remove.onclick=()=>{state.rows=state.rows.filter(item=>item!==row);renderRows();updatePreview();};heading.append(label,remove);area.append(heading);
            const mode=node('select');mode.setAttribute('aria-label',row.path+' 输入状态');fillSelect(mode,[['value','填写值'],['null','null（按 SDK 序列化）'],['omitted','不提交此字段'],...(shortType(row.schema)==='String'?[['empty','空字符串']]:[])],null,row.mode);
            let input;if(row.schema.kind==='object'||row.schema.kind==='array'){input=node('textarea');input.rows=3;input.spellcheck=false;}else if(shortType(row.schema)==='Boolean'||row.schema.kind==='enum'){input=node('select');const values=row.schema.kind==='enum'?row.schema.values:['true','false'];fillSelect(input,values.map(value=>[value,value]),null,row.input);}else input=node('input');input.className='field-value';input.value=row.input;input.disabled=row.mode!=='value';input.setAttribute('aria-label',row.path+' 值');input.id='field-'+state.rows.indexOf(row);label.htmlFor=input.id;
            mode.onchange=()=>{row.mode=mode.value;input.disabled=row.mode!=='value';updatePreview();};input.oninput=()=>{row.input=input.value;updatePreview();};area.append(mode,input);$('fieldRows').append(area);
        }
    }
    function updateMethod(){const method=state.schema?.methods.find(m=>m.name===$('methodSelect').value);$('enableReadback').disabled=!method?.writes||!state.schema?.methods.some(m=>m.name==='Load');if($('enableReadback').disabled){$('enableReadback').checked=false;$('readbackDataLabel').hidden=true;}$('allowWrite').disabled=!method?.writes;updatePreview();busy();}
    function getSpec(){
        const env=selectedEnvironment(),artifact=selectedArtifact();if(!env)throw new Error('请先保存并选择测试环境');if(!state.context||!state.catalog||!state.schema||!artifact)throw new Error('请先选择项目、WCF 产物和服务');if(!$('methodSelect').value)throw new Error('请选择实际服务方法');if(kind==='clf'&&!state.clf)throw new Error('请选择需要验证的 CLF');
        const assertions=parseInput($('assertions').value);if(!Array.isArray(assertions))throw new Error('断言必须为 JSON 数组');
        const pins=state.pins||{};return {kind,project_id:$('projectSelect').value,source_sha256:pins.source_sha256||state.context.source_sha256,artifact_id:artifact.id,client_sha256:pins.client_sha256||state.catalog.client_sha256,environment_id:env.id,environment_version:pins.environment_version||env.version,service:state.schema.service,method:$('methodSelect').value,data:getData(),return_fields:$('returnFields').value.split(/[,\n]/).map(value=>value.trim()).filter(Boolean),expected_outcome:$('expectedOutcome').value,message_contains:$('expectedMessage').value,assertions,clf_name:kind==='clf'?state.clf.name:'',collect_trace:$('collectTrace').checked,allow_write:$('allowWrite').checked,readback:$('enableReadback').checked,readback_data:$('enableReadback').checked?parseInput($('readbackData').value):{}};
    }
    function updatePreview(){try{const data=getData();if(!$('rawMode').checked)$('rawData').value=stringify(data);$('requestPreview').textContent=stringify(getSpec());}catch(error){$('requestPreview').textContent=error.message;}}
    async function startRun(){
        if(state.submitting||state.pending.size)return;
        const spec=getSpec();if(spec.method&&!state.schema.methods.find(m=>m.name===spec.method)?.writes)spec.allow_write=false;
        state.submitting=true;busy();resetResult();$('result').replaceChildren(node('div','正在提交测试请求…','result-status running'));let request;
        try{request=await requestIdentity('run',spec);const record=await send('/api/testing/runs',{request_id:request.identity,spec});sessionStorage.removeItem(request.key);state.selectedRun=record.id;state.boot.history=[record,...state.boot.history.filter(r=>r.id!==record.id)];if(record.status==='running')state.pending.add(record.id);renderResult(record);renderHistory();schedulePoll();notice('测试已在后台接收。刷新、切换页面不会重复提交或取消业务调用。');}
        catch(error){if(request&&error.status&&error.status<500)sessionStorage.removeItem(request.key);resetResult();throw error;}
        finally{state.submitting=false;busy();}
    }

    function schedulePoll(){if(state.timer||!state.pending.size)return;state.timer=setTimeout(poll,1500);}
    async function poll(){state.timer=null;try{for(const identity of [...state.pending]){const record=await api('/api/testing/runs/'+identity+'?'+params());state.boot.history=[record,...state.boot.history.filter(r=>r.id!==identity)];if(record.status!=='running')state.pending.delete(identity);if(state.selectedRun===identity)renderResult(record);}renderHistory();}catch(error){notice('读取测试进度失败：'+error.message,true);}finally{busy();schedulePoll();}}
    function line(area,label,value,pending=false){const row=node('div',undefined,'result-line');row.append(node('span',label),node('strong',value,pending?'pending':''));area.append(row);}
    function renderTrace(area,trace,depth=0){if(!trace||depth>16)return;const block=node('div',undefined,'trace-node');block.append(node('strong',[trace.Name,trace.Value].filter(Boolean).join(' · ')||'服务器轨迹项'));if(trace.ElapsedTime)block.append(node('div',trace.ElapsedTime));if(trace.Parameters?.length){const detail=node('details');detail.append(node('summary','实际调用参数'),node('pre',stringify(trace.Parameters)));block.append(detail);}if(trace.Attributes?.length){const detail=node('details');detail.append(node('summary','轨迹属性'),node('pre',stringify(trace.Attributes)));block.append(detail);}for(const child of trace.Children||[])renderTrace(block,child,depth+1);area.append(block);}
    function renderResult(record){
        const area=$('result');area.replaceChildren();const result=record.result;area.append(node('div',labels[record.status]||record.status,'result-status '+record.status));area.append(node('p',record.spec.service+' / '+record.spec.method+' · '+new Date(record.created_utc).toLocaleString(),'result-heading'));area.append(node('p','记录 '+record.id.slice(0,12)+' · 项目 '+record.spec.project_id.slice(0,8),'result-heading'));
        $('exportResult').hidden=false;$('exportResult').href='/api/testing/runs/'+record.id+'/export?'+params();
        if(!result){area.append(node('p','正在执行真实客户端调用，结果将自动更新。','muted'));return;}
        if(result.error)area.append(node('pre',result.error));
        line(area,'业务结果',result.outcome==='success'?'成功':result.outcome==='rejected'?'业务拒绝':'未知');
        if(result.message)line(area,'业务提示',result.message);
        line(area,'耗时',result.elapsed_ms===undefined?'未记录':result.elapsed_ms+' ms');
        if(result.source_current!==undefined)line(area,'当前 MDB 版本',result.source_current?'来源未变':'测试期间已变化',!result.source_current);
        line(area,'已部署程序集版本','待独立核对',true);
        if(record.spec.kind==='clf')line(area,'目标 CLF 触发证据',result.clf_trigger_verified?'已取得明确轨迹证据':'未验证',!result.clf_trigger_verified);
        if(result.mutation_may_have_occurred)area.append(node('p','写入的实际结果可能已发生，请先核对业务数据再重试。','pending'));
        for(const check of result.checks||[]){const row=node('div',undefined,'check-result'+(check.passed?'':' failed'));row.append(node('strong',(check.passed?'✓ ':'× ')+check.name));row.append(node('pre',stringify({expected:check.expected,actual:check.actual})));area.append(row);}
        for(const [label,value] of [['业务返回',result.response||result.primary_response],['独立 Load 回读',result.readback],['请求与来源',record.spec]])if(value){const details=node('details');details.append(node('summary',label),node('pre',stringify(value)));area.append(details);}
        const trace=result.response?.trace;const details=node('details');details.append(node('summary',trace?'服务器实际执行轨迹':'执行轨迹 · 未取得'));if(trace)renderTrace(details,trace);else details.append(node('p','运行环境未返回轨迹；MDB 中的函数顺序不作为执行轨迹。','muted'));area.append(details);
        if(result.notice)area.append(node('p',result.notice,'muted'));
    }
    function renderHistory(){const list=$('historyList');list.replaceChildren();const runs=(state.boot?.history||[]).filter(run=>run.spec.kind===kind).sort((a,b)=>b.created_utc.localeCompare(a.created_utc));if(!runs.length)list.append(node('p','尚无测试记录。','muted'));for(const run of runs){const row=node('div',undefined,'history-row');const label=node('div',run.spec.service+' · '+run.spec.method);label.append(node('small',new Date(run.created_utc).toLocaleString()+' · '+(labels[run.status]||run.status)));const button=node('button','查看结果','button subtle');button.onclick=()=>{state.selectedRun=run.id;renderResult(run);};row.append(label,button);list.append(row);}}
    function renderCases(){const area=$('caseList');area.replaceChildren();const cases=(state.boot?.cases||[]).filter(test=>test.spec.kind===kind&&(!$('projectSelect').value||test.spec.project_id===$('projectSelect').value));if(!cases.length)area.append(node('p','配置输入和预期结果后，保存第一条回归用例。','muted'));for(const test of cases){const row=node('div',undefined,'case-row');const label=node('label');const checkbox=node('input');checkbox.type='checkbox';checkbox.dataset.caseId=test.id;const name=node('span',test.name+(test.group?' · '+({normal:'正常',exception:'异常',boundary:'边界',custom:'自定义'}[test.group]||test.group):'')+' · 修订 '+(test.revision||1));const env=state.boot.environments.find(env=>env.id===test.spec.environment_id);name.append(node('small',test.spec.service+' / '+test.spec.method+' · '+(env?.name||'环境已删除')+' · '+test.spec.source_sha256.slice(0,8)));label.append(checkbox,name);const actions=node('div',undefined,'case-row-actions');const load=node('button','载入','button subtle');load.onclick=()=>loadCase(test).catch(error=>notice(error.message,true));const remove=node('button','删除','button subtle');remove.onclick=async()=>{try{await api('/api/testing/cases/'+test.id+'?'+params(),{method:'DELETE'});state.boot.cases=state.boot.cases.filter(item=>item.id!==test.id);renderCases();}catch(error){notice(error.message,true);}};actions.append(load,remove);row.append(label,actions);area.append(row);}}
    async function loadCase(test){const spec=test.spec;if(!state.boot.projects.some(p=>p.id===spec.project_id))throw new Error('用例的工作项目已不存在');if(!state.boot.environments.some(env=>env.id===spec.environment_id))throw new Error('用例的测试环境已不存在');if(!state.boot.artifacts.some(a=>a.id===spec.artifact_id))throw new Error('用例的客户端产物不在当前目录中，请核对或刷新来源');$('projectSelect').value=spec.project_id;$('artifactSelect').value=spec.artifact_id;$('environmentSelect').value=spec.environment_id;await loadSources();$('serviceSelect').value=spec.service;await loadService(spec.service);$('methodSelect').value=spec.method;$('rawMode').checked=true;$('fieldForm').hidden=true;$('rawDataLabel').hidden=false;$('rawData').value=stringify(spec.data);$('returnFields').value=spec.return_fields.join('\n');$('expectedOutcome').value=spec.expected_outcome;$('expectedMessage').value=spec.message_contains;$('assertions').value=stringify(spec.assertions);$('collectTrace').checked=spec.collect_trace;$('allowWrite').checked=false;updateMethod();$('enableReadback').checked=spec.readback;$('readbackDataLabel').hidden=!spec.readback;$('readbackData').value=stringify(spec.readback_data);if(kind==='clf')await selectClf(spec.clf_name,false);state.pins={source_sha256:spec.source_sha256,client_sha256:spec.client_sha256,environment_version:spec.environment_version};$('caseVersion').hidden=false;$('caseName').value=test.name;state.loadedCase=test;if($('caseGroup'))$('caseGroup').value=test.group||'custom';updatePreview();notice('已载入用例。旧版本保持固定；来源或环境变更时，先核对再采用当前版本。');}
    async function saveCase(){const name=$('caseName').value.trim();if(!name)throw new Error('请填写用例名称');const saved=await send('/api/testing/cases',{name,spec:getSpec(),group:$('caseGroup')?.value||'custom',parent_run_id:state.loadedCase?.parent_run_id||'',parent_case_id:state.loadedCase?.id||''});state.boot.cases.unshift(saved);renderCases();notice('回归用例已保存；写入授权不会随用例保存。');}
    async function runBatch(){
        if(state.submitting||state.pending.size)return;
        const case_ids=[...$('caseList').querySelectorAll('input:checked')].map(input=>input.dataset.caseId);if(!case_ids.length)throw new Error('请选择需要运行的回归用例');
        const body={case_ids,allow_write:$('allowWrite').checked};state.submitting=true;busy();resetResult();$('result').replaceChildren(node('div','正在接收批量测试…','result-status running'));let request;
        try{request=await requestIdentity('batch',body);const result=await send('/api/testing/batches',{request_id:request.identity,...body});sessionStorage.removeItem(request.key);for(const run of result.runs){state.boot.history=[run,...state.boot.history.filter(r=>r.id!==run.id)];if(run.status==='running')state.pending.add(run.id);}if(result.runs.length){state.selectedRun=result.runs[0].id;renderResult(result.runs[0]);}renderHistory();schedulePoll();notice('已接收 '+result.runs.length+' 条回归用例，真实调用按顺序执行；结果可在测试记录查看。');}
        catch(error){if(request&&error.status&&error.status<500)sessionStorage.removeItem(request.key);resetResult();throw error;}
        finally{state.submitting=false;busy();}
    }

    async function searchClfs(more){if(!state.context)throw new Error('请选择工作项目');const epoch=state.epoch,search=$('clfSearch').value.trim(),offset=more?state.clfSearch?.records.length||0:0;const result=await api('/api/testing/clfs/'+$('projectSelect').value+'?'+params({search,offset}));if(epoch!==state.epoch||search!==$('clfSearch').value.trim())return;state.clfSearch=more?{...result,records:[...state.clfSearch.records,...result.records]}:result;$('clfList').replaceChildren();for(const clf of state.clfSearch.records){const button=node('button',clf.name,'catalog-entry'+(state.clf?.name===clf.name?' selected':''));button.type='button';button.onclick=()=>selectClf(clf.name).catch(error=>notice(error.message,true));$('clfList').append(button);}$('clfMore').hidden=state.clfSearch.records.length>=result.total;}
    async function selectClf(name,resolveEntry=true){
        if(!state.context)throw new Error('请选择工作项目');
        const selection=++state.clfEpoch,epoch=state.epoch,projectId=$('projectSelect').value;
        const definition=await api('/api/testing/clfs/'+projectId+'/detail?'+params({name,source_sha256:state.context.source_sha256}));
        if(epoch!==state.epoch||selection!==state.clfEpoch)return;
        state.clf=definition;$('clfSelection').textContent='当前规则：'+definition.name+' · 正在核对业务入口';$('definitionPanel').hidden=false;
        const area=$('clfDefinition');area.replaceChildren(node('h3',definition.name));
        for(const call of definition.calls){const card=node('div',undefined,'definition-call');card.append(node('strong',String(call.sequence)+' · '+call.function),node('small',' · 调用 ID '+call.call_id+(call.active===false?' · 已禁用':'')));const detail=node('details');detail.append(node('summary','定义参数'),node('pre',stringify(call.parameters)));card.append(detail);area.append(card);}
        for(const [label,value] of [['CLF 声明参数',definition.declared_parameters],['官方引用与事件入口',definition.references]]){const details=node('details');details.append(node('summary',label),node('pre',stringify(value)));area.append(details);}
        for(const button of $('clfList').children)button.classList.toggle('selected',button.textContent===name);
        if(!resolveEntry){$('clfSelection').textContent='当前规则：'+definition.name+' · 沿用已保存入口 '+state.schema?.service+' / '+$('methodSelect').value;updateMethod();return;}
        const refs=definition.references,rows=refs?.rows||[];
        const candidates=[...new Set(rows.map(r=>r.CDOName).filter(name=>state.catalog?.services.includes(name)))];
        if(refs?.available&&!refs.truncated&&candidates.length===1){
            $('serviceSelect').value=candidates[0];await loadService(candidates[0]);
            if(epoch!==state.epoch||selection!==state.clfEpoch)return;
            const methods=[...new Set(rows.filter(r=>r.CDOName===candidates[0]).map(r=>r.MethodName||r.EventName).filter(name=>state.schema?.methods.some(m=>m.name===name)))];
            $('methodSelect').value=methods.length===1?methods[0]:'';
            $('clfSelection').textContent='当前规则：'+definition.name+' · '+candidates[0]+(methods.length===1?' / '+methods[0]:' · 请选择实际触发操作');
        }else{
            state.schema=null;state.fields.clear();state.rows=[];$('fieldRows').replaceChildren();fillSelect($('methodSelect'),[],'请先选择业务入口');
            $('clfSelection').textContent='当前规则：'+definition.name+' · 触发入口待确认，请展开下方入口选择';
            $('clfEntryOptions').open=true;
        }
        updateMethod();
    }

    function configureClfTest(){
        if(kind!=='clf')return;
        document.body.classList.add('clf-test-mode');
        const caseLabel=node('label','用例分组'),group=node('select');group.id='caseGroup';fillSelect(group,[['normal','正常'],['exception','异常'],['boundary','边界'],['custom','自定义']],null,'custom');caseLabel.append(group);$('caseName').closest('label').after(caseLabel);
        document.querySelector('.eyebrow').textContent='沿用当前规则，核对业务效果';
        const page=document.querySelector('.page'),context=node('p','正在读取系统默认连接…','muted');context.id='clfDefaultConnection';
        const connection=node('details',undefined,'clf-test-advanced');connection.append(node('summary','连接与来源 · 使用系统默认配置'));
        const bar=document.querySelector('.environment-bar');bar.before(context,connection);
        connection.append(bar,$('environmentEditor'),document.querySelector('.context-grid'),document.querySelector('.source-details'));
        const catalog=document.querySelector('.catalog'),entry=node('details',undefined,'panel catalog clf-entry-options');entry.id='clfEntryOptions';
        entry.append(node('summary','选择或更换规则、业务入口'));catalog.replaceWith(entry);catalog.classList.remove('panel','catalog');entry.append(catalog);entry.open=!(query.get('clf_name')||query.get('session_id'));
        const panel=$('definitionPanel'),definition=node('details',undefined,'panel definition-panel');definition.id='definitionPanel';definition.hidden=true;definition.append(node('summary','技术详情 · 原始 CLF 定义与引用'),$('clfDefinition'));panel.replaceWith(definition);
        const advanced=node('details',undefined,'clf-test-advanced');advanced.append(node('summary','高级输入与技术证据'));
        const actions=$('runButton').closest('.actions');actions.before(advanced);
        for(const part of [$('rawMode').closest('label'),$('rawDataLabel'),$('returnFields').closest('label'),$('assertions').closest('details'),$('readbackDataLabel'),$('collectTrace').closest('label'),$('requestPreview').closest('details'),document.querySelector('.input-note')])advanced.append(part);
        const add=node('details',undefined,'clf-extra-fields');add.append(node('summary','补充业务输入字段'));const fields=document.querySelector('.add-field');fields.replaceWith(add);add.append(fields);
        document.querySelector('.request-panel h2').textContent='输入与预期';
        $('methodSelect').parentElement.firstChild.textContent='触发操作';
    }
    action('configureEnvironment',()=>{environmentEditor();toggle('environmentEditor','configureEnvironment');});action('newEnvironment',()=>environmentEditor(true));action('saveEnvironment',saveEnvironment);action('checkEnvironment',checkEnvironment);action('historyToggle',()=>toggle('historyPanel','historyToggle'));action('refreshButton',()=>{query.delete('source_sha256');return bootstrap();});action('addField',()=>{addField($('fieldPath').value.trim());$('fieldPath').value='';});action('runButton',startRun);action('saveCaseButton',saveCase);action('runBatch',runBatch);action('clfMore',()=>searchClfs(true));
    action('adoptCurrentVersion',()=>{state.pins=null;$('caseVersion').hidden=true;updatePreview();notice('已采用当前页面的项目、客户端和环境版本，请核对请求后再执行或保存。');});
    action('generateAssertions',()=>{const data=getData(),assertions=[];function walk(value,prefix=''){for(const [key,item] of Object.entries(value)){const path=prefix+key;if(item&&typeof item==='object'&&!Array.isArray(item))walk(item,path+'.');else if(!path.startsWith('ObjectToChange.'))assertions.push({path,operator:'equals',value:item});}}walk(data);$('assertions').value=stringify(assertions);$('returnFields').value=assertions.map(a=>a.path).join('\n');updatePreview();});
    $('environmentSelect').onchange=()=>{environmentEditor();if(location.pathname==='/wcf-test/settings'){const env=selectedEnvironment();$('projectSelect').value=env?.project_id||'';$('artifactSelect').value=env?.artifact_id||'';loadSources().catch(error=>notice(error.message,true));}$('environmentState').textContent='业务登录未检查';updatePreview();};
    $('projectSelect').onchange=()=>loadSources().catch(error=>notice(error.message,true));$('artifactSelect').onchange=()=>loadSources().catch(error=>notice(error.message,true));$('serviceSearch').oninput=renderServices;$('serviceSelect').onchange=()=>loadService($('serviceSelect').value).catch(error=>notice(error.message,true));$('methodSelect').onchange=updateMethod;
    $('rawMode').onchange=()=>{try{if($('rawMode').checked)$('rawData').value=stringify(formData());else{const data=parseInput($('rawData').value);if(!data||Array.isArray(data)||typeof data!=='object')throw new Error('服务数据必须为 JSON 对象');const rows=[];for(const [path,value] of Object.entries(data)){if(!state.fields.has(path))throw new Error('JSON 中的 '+path+' 不在当前契约内，请修正后再切换表单');const schema=state.fields.get(path);rows.push({path,schema,mode:value===null?'null':'value',input:typeof value==='object'?stringify(value):String(value)});}state.rows=rows;renderRows();}}catch(error){$('rawMode').checked=!$('rawMode').checked;notice(error.message,true);}$('fieldForm').hidden=$('rawMode').checked;$('rawDataLabel').hidden=!$('rawMode').checked;updatePreview();};
    $('enableReadback').onchange=()=>{$('readbackDataLabel').hidden=!$('enableReadback').checked;if($('enableReadback').checked){try{const data=getData();$('readbackData').value=stringify(data.ObjectToChange?{ObjectToChange:data.ObjectToChange}:{});}catch(error){notice(error.message,true);}}updatePreview();};
    for(const id of ['returnFields','rawData','expectedOutcome','expectedMessage','assertions','allowWrite','collectTrace','readbackData'])$(id).addEventListener('input',updatePreview);
    $('clfSearchForm').onsubmit=event=>{event.preventDefault();searchClfs(false).catch(error=>notice(error.message,true));};
    const title=kind==='clf'?'CLF 逻辑测试':'WCF 接口测试';document.title='Opcenter Designer · '+title;$('pageTitle').textContent=title;$('breadcrumb').textContent=title;$('pageDescription').textContent=kind==='clf'?'通过实际业务服务触发规则，对比预期结果，保留运行轨迹与来源证据。':'从真实客户端契约生成表单，执行服务调用并核对返回内容。';$(kind==='clf'?'clfNav':'wcfNav').classList.add('active');$(kind==='clf'?'clfNav':'wcfNav').setAttribute('aria-current','page');$('clfCatalog').hidden=kind!=='clf';$('clfSelection').hidden=kind!=='clf';$('collectTrace').checked=kind==='clf';
    if(location.pathname==='/wcf-test/settings'){document.body.classList.add('settings-only');$('pageTitle').textContent='测试环境设置';$('pageDescription').textContent='为环境关联工作项目和默认客户端，保存后返回 WCF 测试。';$('environmentEditor').hidden=false;const back=node('a','返回 WCF 测试','button');back.href='/wcf-test';document.querySelector('.page-heading').append(back);}
    configureClfTest();
    api('/config').then(async config=>{state.username=config.username;$('usernameLabel').textContent=config.username;await bootstrap();
        if(kind==='clf'){
            const back=node('a','返回 CLF 工作区','button');back.href='/clf?'+new URLSearchParams({project_id:$('projectSelect').value,clf_name:state.clf?.name||query.get('clf_name')||'',view:'runs'});document.querySelector('.page-heading').append(back);
            if(query.get('case_id')){const saved=state.boot.cases.find(c=>c.id===query.get('case_id'));if(!saved)throw new Error('用例不存在或不属于当前用户');await loadCase(saved);}
            else if(query.get('service')){const service=query.get('service'),method=query.get('method');if(!state.catalog?.services.includes(service))throw new Error('指定的服务不在当前客户端契约内');$('serviceSelect').value=service;await loadService(service);if(!state.schema.methods.some(m=>m.name===method))throw new Error('指定的方法不在当前服务契约内');$('methodSelect').value=method;updateMethod();notice('已沿用工作区明确选择的服务与方法。请核对输入和本次写入影响后再执行。');}
        }
    }).catch(error=>notice('加载测试工作台失败：'+error.message,true));
})();
