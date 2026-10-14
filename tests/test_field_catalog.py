"""Complete field catalogs reach the model and UI without repeated MDB loads."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from agent import chat_context, memory, langgraph_runtime as runtime
from agent.chat_evidence import evidence_scope, read
from designer import vendor, progress_store
from tests.test_program_optimizations import isolated
from tests.test_designer_creation_preflight import installed_sdk


def catalog(count=300):
    return {'format': 'designer_field_catalog_v1', 'owner': 'Product', 'complete': True,
            'total_fields': count, 'source_file': 'source.mdb', 'source_sha256': 'a' * 64,
            'read_only': True, 'workspace_resolution': 'vendor_metadata_model',
            'columns': ['name', 'field_type', 'data_type', 'precision', 'scale',
                        'persistent', 'is_list', 'origin', 'description'],
            'rows': [[f'Field{i:03}', 'Description', 'String', 80, 0, True, False,
                      'RevisionedObject', f'字段说明{i}'] for i in range(count)]}


@pytest.mark.parametrize('tool', ['get_designer_fields', 'get_designer_entity'])
def test_model_receives_every_field_and_type_without_generic_samples(tool):
    fields = catalog()
    data = fields if tool == 'get_designer_fields' else {
        'name': 'Product', 'fields': [{'large_property': 'x' * 5000}] * 300, 'field_catalog': fields}
    message = {'role': 'tool', 'tool_call_id': 'fields', 'name': tool, 'content': str(data)}
    compact = json.loads(chat_context.compact_tool(message, 1)['content'])
    result = compact if tool == 'get_designer_fields' else compact['field_catalog']
    assert result['rows'] == fields['rows']
    assert result['total_fields'] == 300 and result['complete']
    assert message['content'] == str(data)


def test_explicit_evidence_pages_are_not_truncated_again():
    messages = [{'role': 'tool', 'tool_call_id': 'original', 'content': 'x' * 15000}]
    with evidence_scope(messages):
        page = read('original', limit=12000)
    message = {'role': 'tool', 'tool_call_id': 'page', 'name': 'get_chat_evidence', 'content': str(page)}
    result = json.loads(chat_context.compact_tool(message, 1)['content'])
    assert len(result['content']) == 12000
    assert result['next_offset'] == 12000


def test_field_pages_and_search_share_one_load_but_recheck_source_and_sdk(isolated, monkeypatch):
    root, dll = isolated
    source = root / 'source.mdb'; source.write_bytes(b'v1')
    calls = []

    def run(folder, request):
        calls.append(request)
        return {'total': 300, 'records': [{'name': f'Field{i:03}'} for i in range(300)]}

    monkeypatch.setattr(vendor, 'run', run)
    first = vendor.query(str(source), 'list', 'field', owner='Product', limit=32)
    second = vendor.query(str(source), 'list', 'field', owner='Product', offset=32, limit=300)
    found = vendor.query(str(source), 'list', 'field', owner='Product', search='field299')
    assert len(first['records']) == 32 and len(second['records']) == 268
    assert found['total'] == 1 and found['records'][0]['name'] == 'Field299'
    assert len(calls) == 1 and calls[0]['search'] == '' and calls[0]['offset'] == 0
    source.write_bytes(b'v2')
    vendor.query(str(source), 'list', 'field', owner='Product')
    dll.write_bytes(b'sdk2')
    vendor.query(str(source), 'list', 'field', owner='Product')
    assert len(calls) == 3


def test_field_catalog_reuses_complete_object_detail(isolated, monkeypatch):
    root, _ = isolated
    source = root / 'source.mdb'; source.write_bytes(b'v1')
    calls = []

    def run(folder, request):
        calls.append(request)
        return {'name': 'Product', 'field_catalog': catalog()}

    monkeypatch.setattr(vendor, 'run', run)
    vendor.query(str(source), 'get', 'cdo', 'Product')
    fields = vendor.query(str(source), 'fields', 'cdo', 'Product')
    assert fields['rows'] == catalog()['rows'] and len(calls) == 1
    assert fields['source_sha256'] == vendor.digest(source)


def test_table_event_and_recovered_history_keep_all_rows(isolated, monkeypatch):
    events = []
    fields = catalog()
    monkeypatch.setattr(runtime, 'get_stream_writer', lambda: events.append)
    monkeypatch.setattr(runtime, 'record_perf', lambda *args: None)
    monkeypatch.setattr(runtime, 'record_tool_outcome', lambda **kwargs: None)

    async def query(**kwargs):
        return fields

    monkeypatch.setattr(runtime, 'get_tool_func', lambda name: query)
    sid = memory.create_session('user')
    call = {'id': 'all-fields', 'function': {'name': 'get_designer_fields',
            'arguments': json.dumps({'mdb_file': 'source.mdb', 'cdo_name': 'Product'})}}
    state = {'username': 'user', 'session_id': sid, 'messages': [], 'pending_tool_calls': [call]}
    result = asyncio.run(runtime._execute_tool_node(state))
    assert result['messages'][-1]['field_catalog'] == fields
    assert next(event for event in events if event['type'] == 'field_catalog')['result'] == fields
    model = json.loads(chat_context.compact_tool(result['messages'][-1], 1)['content'])
    assert model['displayed_as_table'] is True and len(model['rows']) == 300
    recovered = []
    progress_store.recover_chat_results('user', sid, recovered)
    assert recovered[-1]['field_catalog'] == fields


def test_browser_renders_all_rows_filters_locally_and_deduplicates():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for browser regression')
    source = (Path(__file__).parents[1] / 'static/index.html').read_text(encoding='utf-8')
    render = source.split('        function showFieldCatalog(id, result) {', 1)[1].split(
        '        function showRequirement(', 1)[0]
    data = catalog()
    data['rows'][-1][-1] = '<img src=x onerror=alert(1)>'
    script = r'''
const assert = require('node:assert/strict');
class Element {
    constructor() {this.children=[];this.dataset={};this.style={};this.events={};}
    appendChild(child) {this.children.push(child);}
    setAttribute() {}
    addEventListener(name, callback) {this.events[name]=callback;}
    set innerHTML(value) {throw new Error('Untrusted cells must use textContent');}
}
const document={createElement:()=>new Element()}, messagesDiv=new Element();
messagesDiv.querySelectorAll=()=>messagesDiv.children;
const createBubble=()=>{const node=new Element();messagesDiv.appendChild(node);return node;};
const scrollToBottom=()=>{};
''' + 'function showFieldCatalog(id, result) {' + render + '\nconst data=' + json.dumps(data) + r''';
showFieldCatalog('fields',data); showFieldCatalog('fields',data);
assert.equal(messagesDiv.children.length,1);
const bubble=messagesDiv.children[0], filter=bubble.children[1];
const rows=bubble.children[2].children[0].children[1].children;
assert.equal(rows.length,300);
assert.equal(rows[299].children[9].textContent,'<img src=x onerror=alert(1)>');
filter.value='Field299';filter.events.input();
assert.equal(rows.filter(row=>!row.hidden).length,1);
filter.value='';filter.events.input();
assert.equal(rows.filter(row=>!row.hidden).length,300);
'''
    result = subprocess.run([node, '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_installed_sdk_catalog_matches_complete_product_definition(installed_sdk):
    fields = vendor.query(str(installed_sdk), 'fields', 'cdo', 'Product')
    detail = vendor.query(str(installed_sdk), 'get', 'cdo', 'Product')
    assert fields['complete'] is True
    assert len(fields['rows']) == fields['total_fields'] == detail['total_fields'] > 100
    assert {row[0] for row in fields['rows']} == {row['name'] for row in detail['fields']}
    assert all(row[1] and row[2] for row in fields['rows'])
    assert detail['field_catalog']['rows'] == fields['rows']
