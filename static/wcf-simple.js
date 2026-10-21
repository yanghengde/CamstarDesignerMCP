/* Browse at most five real WCF records and edit an existing loaded row. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const element = (tag, text) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const names = {Product:'产品', Resource:'资源', Employee:'员工', Factory:'工厂', Operation:'工序',
    Workflow:'工作流', Spec:'规范', UOM:'计量单位', Name:'名称', Revision:'版本', Description:'描述',
    ExtName:'扩展名称', LastEditTime:'最后修改时间', LastEditedBy:'最后修改人', Status:'状态'};
  const storage = 'designer-wcf-simple-request';
  let user = '', boot = null, schema = null, loadRecord = null, busy = false, epoch = 0;
  const inputs = new Map();
  const title = service => names[service.replace(/Maint$/, '')] || service.replace(/Maint$/, '');
  const queryParams = () => new URLSearchParams({username:user});
  const json = value => JSON.stringify(value, null, 2);
  function notice(message, error = false) {
    $('notice').textContent = message; $('notice').hidden = !message;
    $('notice').classList.toggle('error', error);
  }
  async function api(path, body) {
    const response = await fetch(path, {cache:'no-store', ...(body ? {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:user, ...body})
    } : {})});
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : json(data.detail));
      error.status = response.status; throw error;
    }
    return data;
  }
  function refresh() {
    $('queryButton').disabled = busy || !schema?.can_query;
    $('queryButton').textContent = busy ? '正在处理…' : '查询前 5 条';
    $('objectSelect').disabled = busy; $('objectSearch').disabled = busy;
    $('saveButton').disabled = busy || !loadRecord || !boot?.write_target_confirmed ||
      !Array.from(inputs.values()).some(row => row.dirty);
    $('closeEditor').disabled = busy; $('cancelEdit').disabled = busy;
    for (const button of document.querySelectorAll('[data-edit-row]')) {
      button.disabled = busy || !schema?.can_edit || button.dataset.service !== schema.service;
    }
  }
  function renderObjects() {
    const search = $('objectSearch').value.trim().toLowerCase(), selected = $('objectSelect').value;
    $('objectSelect').replaceChildren(element('option', '选择测试对象'));
    $('objectSelect').firstChild.value = '';
    for (const service of boot?.services || []) {
      if (service !== selected && search && !service.toLowerCase().includes(search) && !title(service).includes(search)) continue;
      const option = element('option', title(service) + ' · ' + service); option.value = service;
      $('objectSelect').append(option);
    }
    $('objectSelect').value = selected;
  }
  async function selectObject() {
    const ticket = ++epoch;
    schema = null; loadRecord = null;
    $('result').replaceChildren(element('p', '点击“查询前 5 条”获取当前对象的数据。')); refresh();
    const service = $('objectSelect').value;
    $('serviceCaption').textContent = service || '选择对象后即可查询，无需填写对象名称。';
    if (!service) return;
    const result = await api('/api/testing/simple/schema/' + encodeURIComponent(service) + '?' + queryParams());
    if (ticket !== epoch) return;
    schema = result; refresh();
    notice(schema.can_query ? '' : '此对象暂不支持列表查询，请选择其他对象。', !schema.can_query);
  }
  function responseValue(record) {
    return (record.result?.readback || record.result?.response || record.result?.primary_response)?.value?.ObjectChanges || {};
  }
  function showResult(record) {
    const area = $('result'); area.replaceChildren();
    const selection = record.result?.response?.selection, rows = selection?.rows?.slice(0, 5) || [];
    const isQuery = record.spec.method === 'GetEnvironment' && record.spec.selection_limit === 5;
    const passed = record.status === 'passed';
    const status = element('div', record.status === 'running' ? '正在请求 WCF…' :
      passed ? (isQuery ? (rows.length ? 'WCF 查询通过' : 'WCF 查询通过，暂无数据') : '操作通过') : 'WCF 请求未通过');
    status.className = 'result-status ' + record.status; area.append(status);
    if (record.result?.error) area.append(element('p', record.result.error));
    else if (!passed && record.result?.message) area.append(element('p', record.result.message));
    if (passed && isQuery) {
      const headers = selection?.headers || [];
      const columns = ['Name', 'Revision', 'Description', 'LastEditTime'].filter(name => headers.includes(name));
      if (!rows.length) area.append(element('p', '返回 0 条记录，本次接口查询成功。'));
      else {
        const wrap = element('div'); wrap.className = 'table-scroll';
        const table = element('table'), head = element('tr'), thead = element('thead'), body = element('tbody');
        for (const column of columns) head.append(element('th', names[column] || column));
        head.append(element('th', '操作')); thead.append(head); table.append(thead);
        rows.forEach((values, index) => {
          const row = element('tr');
          for (const column of columns) row.append(element('td', values[headers.indexOf(column)] ?? ''));
          const action = element('td'), button = element('button', '编辑');
          button.type = 'button'; button.className = 'button edit-row';
          button.dataset.editRow = String(index); button.dataset.service = record.spec.service;
          button.setAttribute('aria-label', '编辑 ' + (values[headers.indexOf('Name')] || '记录'));
          button.onclick = handle(() => editRow(record, index));
          button.disabled = busy || !schema?.can_edit || record.spec.service !== schema.service;
          action.append(button); row.append(action); body.append(row);
        });
        table.append(body); wrap.append(table); area.append(wrap);
        area.append(element('small', '本次返回 ' + rows.length + ' 条，最多查询 5 条。'));
      }
      $('connection').textContent = '● 已连接';
    } else if (passed) {
      const list = element('dl');
      for (const [key, value] of Object.entries(responseValue(record))) {
        if (typeof value !== 'object') list.append(element('dt', names[key] || key), element('dd', String(value ?? '')));
      }
      area.append(list);
    }
    if (record.result?.mutation_may_have_occurred) area.append(element('p', '保存结果尚未确认，请先重新查询核对。'));
    const detail = element('details');
    detail.append(element('summary', '查看请求记录'), element('pre', json(record))); area.append(detail);
  }
  function renderHistory() {
    $('historyList').replaceChildren();
    for (const record of boot.history) {
      const operation = {GetEnvironment:'查询', Load:'读取记录', ExecuteTransaction:'修改'}[record.spec.method] || '历史操作';
      const button = element('button', title(record.spec.service) + ' · ' + operation + ' · ' + new Date(record.created_utc).toLocaleString());
      button.className = 'button'; button.onclick = () => { if (!busy) { showResult(record); refresh(); } };
      $('historyList').append(button);
    }
  }
  async function requestAction(action, extra = {}, progress) {
    const body = {action, service:$('objectSelect').value, ...extra}, fingerprint = json(body);
    const pending = JSON.parse(sessionStorage.getItem(storage) || 'null');
    if (pending && pending.fingerprint !== fingerprint) throw new Error('上次请求结果尚未确认，请刷新查看测试记录后再操作。');
    const request = pending || {id:crypto.randomUUID().replaceAll('-', ''), fingerprint};
    sessionStorage.setItem(storage, json(request));
    let record;
    try {
      record = await api('/api/testing/simple/runs', {...body, request_id:request.id});
      sessionStorage.removeItem(storage);
    } catch (error) {
      if (error.status && error.status < 500) sessionStorage.removeItem(storage);
      throw error;
    }
    while (record.status === 'running') {
      if (progress) progress(record);
      await new Promise(resolve => setTimeout(resolve, 800));
      record = await api('/api/testing/runs/' + record.id + '?' + queryParams());
    }
    boot.history = [record, ...boot.history.filter(row => row.id !== record.id)]; renderHistory();
    return record;
  }
  async function query() {
    if (busy || !schema?.can_query) return;
    busy = true; loadRecord = null; refresh(); notice('');
    $('result').replaceChildren(element('p', '正在查询前 5 条数据…'));
    try { showResult(await requestAction('query', {}, showResult)); }
    catch (error) { $('result').replaceChildren(element('p', error.message)); throw error; }
    finally { busy = false; refresh(); }
  }
  function renderFields() {
    inputs.clear(); $('commonFields').replaceChildren(); $('otherFields').replaceChildren(); $('fieldSearch').value = '';
    const original = responseValue(loadRecord);
    for (const field of schema.editable_fields) {
      if (!(field.name in original)) continue;
      const value = original[field.name], type = field.schema.value_type || field.schema.type;
      const label = element('label', names[field.name] || field.name); label.dataset.field = field.name.toLowerCase();
      let input;
      if (type === 'System.Boolean' || field.schema.kind === 'enum') {
        input = element('select');
        if (value == null) { const blank = element('option', '未设置'); blank.value = ''; blank.disabled = true; input.append(blank); }
        for (const item of field.schema.values || ['true', 'false']) {
          const option = element('option', item === 'true' ? '是' : item === 'false' ? '否' : item); option.value = item; input.append(option);
        }
      } else { input = element(field.name === 'Description' ? 'textarea' : 'input'); if (input.tagName === 'TEXTAREA') input.rows = 3; }
      input.value = value == null ? '' : String(value); input.setAttribute('aria-label', field.name);
      const row = {field, input, original:value, dirty:false};
      input.oninput = () => { row.dirty = input.value !== (value == null ? '' : String(value)); refresh(); };
      inputs.set(field.name, row); label.append(input);
      (['Description', 'ExtName'].includes(field.name) || /^Ex/.test(field.name) ? $('commonFields') : $('otherFields')).append(label);
    }
    $('otherFieldsDetails').hidden = !$('otherFields').children.length;
    $('otherFieldsDetails').open = !$('commonFields').children.length;
  }
  async function editRow(record, index) {
    if (busy || !schema?.can_edit || record.spec.service !== schema.service) return;
    busy = true; loadRecord = null; inputs.clear(); refresh();
    $('commonFields').replaceChildren(); $('otherFields').replaceChildren(); $('otherFieldsDetails').hidden = true;
    $('editTitle').textContent = '编辑' + title(record.spec.service); $('editIdentity').textContent = '';
    $('editStatus').textContent = '正在读取记录…'; $('editDialog').showModal();
    try {
      const result = await requestAction('load', {source_run_id:record.id, row_index:index});
      if (result.status !== 'passed') throw new Error(result.result?.error || result.result?.message || '读取记录失败');
      loadRecord = result;
      const ref = result.spec.data.ObjectToChange;
      $('editIdentity').textContent = ref.Name + (ref.Revision ? ' · 版本 ' + ref.Revision : '');
      $('editStatus').textContent = boot.write_target_confirmed ? '修改需要调整的字段，保存后自动回读核对。' : '当前系统尚未允许修改业务数据。';
      renderFields();
    } catch (error) { $('editStatus').textContent = error.message; }
    finally { busy = false; refresh(); }
  }
  function changes() {
    const data = {};
    for (const [name, row] of inputs) {
      if (!row.dirty) continue;
      const type = row.field.schema.value_type || row.field.schema.type, text = row.input.value;
      if (type === 'System.Boolean') data[name] = text === 'true';
      else if (/System\.(Int16|Int32|Int64|Double|Single|Decimal)$/.test(type)) {
        const value = Number(text);
        if (!text.trim() || !Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) throw new Error(name + ' 请输入有效数值');
        data[name] = value;
      } else data[name] = text;
    }
    return data;
  }
  async function save() {
    if (busy || !loadRecord) return;
    let data;
    try { data = changes(); }
    catch (error) { $('editStatus').textContent = error.message; return; }
    if (!Object.keys(data).length) return;
    busy = true; refresh(); $('editStatus').textContent = '正在保存并回读核对…';
    let saved = false;
    try {
      const record = await requestAction('update', {source_run_id:loadRecord.id, changes:data});
      if (record.status !== 'passed') {
        const uncertain = record.result?.mutation_may_have_occurred || record.result?.primary_response?.outcome === 'success' || record.result?.response?.outcome === 'success';
        throw new Error((record.result?.error || record.result?.message || '保存后的字段核对未通过') + (uncertain ? '；请先重新查询核对，避免重复保存。' : ''));
      }
      saved = true; loadRecord = null; $('editDialog').close(); notice('修改已保存，回读核对通过。');
    } catch (error) {
      loadRecord = null;
      $('editStatus').textContent = error.message + ' 请关闭编辑后重新查询核对。';
    }
    finally { busy = false; refresh(); }
    if (saved) { await query(); notice('修改已保存，回读核对通过。'); }
  }
  function handle(action) { return () => Promise.resolve().then(action).catch(error => notice(error.message, true)); }
  $('objectSearch').oninput = renderObjects; $('objectSelect').onchange = handle(selectObject);
  $('queryButton').onclick = handle(query);
  $('editForm').onsubmit = event => { event.preventDefault(); handle(save)(); };
  $('fieldSearch').oninput = () => {
    const search = $('fieldSearch').value.trim().toLowerCase();
    for (const label of $('otherFields').children) label.hidden = !!search && !label.dataset.field.includes(search) && !label.textContent.includes(search);
  };
  for (const id of ['closeEditor', 'cancelEdit']) $(id).onclick = () => { if (!busy) $('editDialog').close(); };
  $('editDialog').oncancel = event => { if (busy) event.preventDefault(); };
  $('historyToggle').onclick = () => {
    $('historyPanel').hidden = !$('historyPanel').hidden;
    $('historyToggle').setAttribute('aria-expanded', String(!$('historyPanel').hidden));
  };
  $('wcfNav').classList.add('active'); $('wcfNav').setAttribute('aria-current', 'page');
  handle(async () => {
    user = (await api('/config')).username; $('usernameLabel').textContent = user;
    boot = await api('/api/testing/simple/bootstrap?' + queryParams()); $('connection').textContent = '默认配置已载入';
    renderObjects(); renderHistory();
    $('objectSelect').value = boot.services.includes('ProductMaint') ? 'ProductMaint' : boot.services[0] || '';
    await selectObject();
    const pending = JSON.parse(sessionStorage.getItem(storage) || 'null');
    if (pending && boot.history.some(row => row.request_id === pending.id)) sessionStorage.removeItem(storage);
    const running = boot.history.find(row => row.status === 'running');
    if (running) {
      busy = true; refresh(); let record = running;
      try {
        while (record.status === 'running') {
          showResult(record); await new Promise(resolve => setTimeout(resolve, 800));
          record = await api('/api/testing/runs/' + record.id + '?' + queryParams());
        }
        showResult(record);
      } finally { busy = false; refresh(); }
    }
  })();
})();
