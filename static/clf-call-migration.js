/* Explicit caller preparation only; writes continue through the reviewed CLF plan. */
(() => {
    'use strict';
    const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
    function mount(host,options){
        const entries=new Map();
        host.replaceChildren(node('h3','选择直接规则调用者'),node('p','迁移一个调用时，需要同时核对调用者库表达式和该调用者的全部事件入口。同名调用按 ID 区分；同一调用者每份方案只迁移一个调用。','core-note'));
        if(!options.enabled)host.append(node('p','后端尚未加载直接调用迁移功能。更新后端后可读取并准备迁移。','core-note'));
        if(!options.callers.length)host.append(node('p','当前没有直接规则调用者。','muted'));
        for(const caller of options.callers){
            const row=node('section',undefined,'core-migration-entry'),title=node('strong',caller.clf+' / 调用 '+caller.call_id+' / 顺序 '+caller.sequence),load=node('button','读取调用与入口参数','button'),body=node('div');
            load.type='button';load.disabled=!options.enabled;row.append(title,node('p','工作区 '+caller.workspace,'core-note'),load,body);host.append(row);
            load.onclick=async()=>{
                load.disabled=true;
                try{
                    const contract=await options.load(caller);if(!options.current())return;
                    body.replaceChildren();body.append(node('p',contract.supported?'已读取 '+contract.operation.expected_bindings.length+' 个调用者入口；原调用'+(contract.operation.expected_active?'启用':'禁用')+'，顺序 '+contract.operation.expected_sequence+'；默认不迁移。':contract.reason,'core-note'));
                    if(!contract.supported)return;
                    const check=node('input');check.type='checkbox';const label=node('label',undefined,'check');label.append(check,document.createTextNode('迁移此调用：我已核对库参数及以下全部入口'));
                    const forms=node('div');body.append(label,forms,node('p','原规则和未选调用保持原状。类型、方向和表达式的业务含义仍需通过真实用例验证。','core-note'));
                    const entry={caller,contract,check,forms,inputs:new Map(),draft:new Map()};entries.set(caller.clf+':'+caller.call_id,entry);paint(entry);load.textContent='重新读取原参数';
                }catch(error){options.error(error);}finally{load.disabled=!options.enabled;}
            };
        }
        function remember(entry){for(const [key,input] of entry.inputs)entry.draft.set(key,{value:input.value.value,dynamic:input.dynamic.checked});}
        function paint(entry){
            remember(entry);entry.inputs.clear();entry.forms.replaceChildren();const declarations=options.declarations();
            if(declarations.some(p=>!p.definition.Name)){entry.forms.append(node('p','请先填写有效的目标声明参数名称。','core-note'));return;}
            function form(title,key,parameters,open){
                const section=node('details'),summary=node('summary',title);section.open=open;section.append(summary);
                for(const target of declarations){
                    const definition=target.definition,source=parameters.find(p=>p.parameter===target.from),identity=key+':'+target.key;
                    const previous=entry.draft.get(identity)||{value:source?.value??'',dynamic:source?.dynamic??false};
                    const field=node('div',undefined,'core-caller-value'),label=node('label',definition.Name+' · '+[definition.Direction,definition.DataType,definition.CDOType,definition.ParamMultiplicity,definition.TypeMultiplicity].filter(Boolean).join(' / '));
                    const input=node('textarea');input.rows=2;input.value=previous.value;input.setAttribute('aria-label',title+' / '+definition.Name+' / 拟定表达式');label.append(input);
                    const dynamic=node('input');dynamic.type='checkbox';dynamic.checked=previous.dynamic;const flag=node('label',undefined,'check');flag.append(dynamic,document.createTextNode('动态参数'));
                    field.append(label,node('p',source?'原 '+source.parameter+'：'+(source.value===''?'（空字符串）':source.value):'新增参数：请明确填写表达式；空字符串也会原样提交。','core-expression'),flag);section.append(field);entry.inputs.set(identity,{value:input,dynamic});
                }
                if(!declarations.length)section.append(node('p','目标规则没有声明参数，将提交空参数清单。','core-note'));
                entry.forms.append(section);
            }
            form('调用者库参数','library',entry.contract.operation.expected_parameters,true);
            for(const binding of entry.contract.operation.expected_bindings)form([binding.owner,binding.field,binding.event].filter(Boolean).join(' / ')+' · 入口 '+binding.binding_id,'binding:'+binding.binding_id,binding.parameters.filter(p=>p.call_id===entry.caller.call_id),false);
        }
        return {
            repaint(){for(const entry of entries.values())paint(entry);},
            operations(target,existing){
                const result=[];for(const entry of entries.values()){
                    if(!entry.check.checked)continue;
                    if(!options.enabled||!options.current())throw new Error('调用迁移上下文已变化，请重新读取');
                    paint(entry);const declarations=options.declarations();
                    const names=declarations.map(p=>p.definition.Name);
                    if(names.some(n=>!n)||new Set(names.map(n=>n.toLowerCase())).size!==names.length)throw new Error('目标声明参数名称为空或重复');
                    if(declarations.length>50)throw new Error('本批每个调用最多核对 50 项声明参数');
                    if([...existing,...result].some(o=>o.action==='migrate_clf_call'&&o.owner===entry.caller.clf))throw new Error('同一调用者每份方案仅迁移一个调用，请分别准备');
                    function assignments(key){return declarations.map(p=>{const input=entry.inputs.get(key+':'+p.key);if(!input)throw new Error('拟定参数未完整加载，请重新读取');return {parameter:p.definition.Name,value:input.value.value,dynamic:input.dynamic.checked};});}
                    result.push({...entry.contract.operation,clf:target,target_declarations:declarations.map(p=>({...p.definition})),parameters:assignments('library'),bindings:entry.contract.operation.expected_bindings.map(b=>({binding_id:b.binding_id,parameters:assignments('binding:'+b.binding_id)}))});
                }return result;
            }
        };
    }
    window.ClfCallMigration={mount};
})();
