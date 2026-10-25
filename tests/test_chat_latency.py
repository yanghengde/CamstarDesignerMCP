"""Focused CLF reads reduce round trips without losing execution semantics."""
from copy import deepcopy
import json

import pytest

import config
from agent import chat_context
from designer import clf, vendor
from tests.test_clf_workspace import raw_clf
from tests.test_field_catalog import catalog


def test_clf_analysis_deduplicates_reads_and_preserves_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    source = tmp_path / 'source.mdb'; source.write_bytes(b'original')
    before = vendor.digest(source)
    record = raw_clf()
    record['functions'][0]['parameters'].append({'Name': 'Error', 'ValueExpression': 'LabelId::BadValue'})
    batches = []
    def batch(mdb, queries):
        batches.append(deepcopy(queries))
        if len(batches) == 1:
            return {'results': [{'result': record}, {'result': {'where_used': [], 'truncated': True}}]}
        return {'results': [{'query': q, 'result': {'status': 'failed', 'error': 'missing definition'}
                           if q['kind'] == 'function_parameter' else {'name': q.get('name')}} for q in queries]}
    monkeypatch.setattr(vendor, 'query_batch', batch)
    result = clf.analysis_context(str(source), 'ExLogic', before)
    assert len(batches) == 2
    assert batches[1] == [{'mode': 'get', 'kind': 'function', 'name': 'Check'},
                          {'mode': 'list', 'kind': 'function_parameter', 'owner': 'Check', 'limit': 1000},
                          {'mode': 'get', 'kind': 'label', 'name': 'BadValue'}]
    assert [c['call_id'] for c in result['calls']] == [1, 2]
    assert result['calls'][0]['active'] is False
    assert result['references']['truncated']
    assert result['analysis']['definitions'][1]['result']['status'] == 'failed'
    assert result['analysis']['nested_clfs_expanded'] is False
    assert vendor.digest(source) == before


def test_clf_analysis_rejects_version_change_between_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    source = tmp_path / 'source.mdb'; source.write_bytes(b'original')
    def batch(mdb, queries):
        if queries[0]['kind'] == 'clf':
            return {'results': [{'result': raw_clf()}, {'result': {'where_used': []}}]}
        source.write_bytes(b'changed')
        return {'results': []}
    monkeypatch.setattr(vendor, 'query_batch', batch)
    with pytest.raises(ValueError, match='已更新'):
        clf.analysis_context(str(source), 'ExLogic')


def test_large_clf_context_keeps_every_call_parameter_and_reference():
    data = {'format': 'designer_clf_v1', 'name': 'ExLogic', 'calls': [
        {'call_id': i, 'sequence': i, 'active': i != 9, 'function': 'Check',
         'parameters': [{'name': 'Condition', 'value': 'ImportantExpression' * 30}]} for i in range(25)],
        'declared_parameters': [{'name': 'Input'}],
        'references': {'available': True, 'truncated': True, 'rows': [{'CDOName': 'ProductMaint'}]}}
    message = {'role': 'tool', 'tool_call_id': 'clf', 'name': 'get_designer_clf', 'content': str(data)}
    result = json.loads(chat_context.compact_tool(message, 0)['content'])
    assert result == data


def test_clf_object_context_omits_bulk_attributes_but_keeps_field_names_and_original():
    fields = catalog(300)
    data = {'name': 'ShopFloor', 'fields': [{'noise': 'x' * 5000}] * 300, 'field_catalog': fields}
    message = {'role': 'tool', 'tool_call_id': 'batch', 'name': 'query_designer_batch', 'content': str({
        'results': [{'query': {'mode': 'get', 'kind': 'cdo', 'name': 'ShopFloor'}, 'result': data}]})}
    original = deepcopy(message)
    focused = chat_context.compact_tool(message, 0, scope='clf')
    result = json.loads(focused['content'])['results'][0]['result']
    assert result['field_names'] == [row[0] for row in fields['rows']]
    assert result['field_names_complete'] and result['field_properties_omitted']
    assert result['evidence_id'] == 'batch'
    assert len(focused['content']) < 10000
    assert message == original
    normal = json.loads(chat_context.compact_tool(message, 0)['content'])['results'][0]['result']
    assert normal['field_catalog']['rows'] == fields['rows']
