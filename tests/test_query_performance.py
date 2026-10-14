"""Performance changes preserve pagination, source validation and lookup evidence."""
import json

import pytest

from agent import chat_context, chat_intent
from designer import vendor
from tests.test_program_optimizations import isolated
from tests.test_designer_creation_preflight import installed_sdk


def test_adjacent_pages_share_bounded_vendor_read(isolated, monkeypatch):
    root, _ = isolated
    source = root / 'source.mdb'
    source.write_bytes(b'v1')
    calls = []
    records = [{'name': f'Object{i:04}'} for i in range(750)]

    def run(folder, request):
        calls.append(request)
        start = request['offset']
        return {'total': len(records), 'records': records[start:start + request['limit']]}

    monkeypatch.setattr(vendor, 'run', run)
    for offset in (0, 20, 40, 280):
        result = vendor.query(str(source), 'list', 'cdo', offset=offset)
        assert result['records'] == records[offset:offset + 20]
        assert result['total'] == 750 and result['has_more']
    assert len(calls) == 1 and calls[0]['limit'] == 300
    last = vendor.query(str(source), 'list', 'cdo', offset=740)
    assert last['records'] == records[740:] and not last['has_more']
    assert len(calls) == 2
    crossing = vendor.query(str(source), 'list', 'cdo', offset=290, limit=30)
    assert crossing['records'] == records[290:320]
    source.write_bytes(b'v2')
    vendor.query(str(source), 'list', 'cdo')
    assert len(calls) == 4


def test_list_context_preserves_all_names_and_marks_partial_properties():
    data = {'total': 300, 'offset': 0, 'has_more': False, 'source_sha256': 'abc',
            'records': [{'name': f'Object{i}', 'ParentCDO': {'name': 'Product'},
                         'unused_detail': 'x' * 2000} for i in range(300)]}
    message = {'role': 'tool', 'name': 'list_designer_entities', 'tool_call_id': 'list', 'content': str(data)}
    result = json.loads(chat_context.compact_tool(message, 0)['content'])
    assert len(result['records']) == 300
    assert result['records'][-1]['name'] == 'Object299'
    assert result['records'][-1]['ParentCDO']['name'] == 'Product'
    assert result['record_properties_partial'] and result['evidence_id'] == 'list'
    assert result['source_sha256'] == 'abc' and not result['has_more']
    assert message['content'] == str(data)


@pytest.mark.parametrize('text', ['展示Product字段', '查询Product类型', '展示Product下的有哪些继承模型'])
def test_explicit_lookup_selects_small_tool_set(text):
    assert chat_intent.metadata_lookup(text)
    assert all(chat_intent.read_only_tool(name) for name in chat_intent.METADATA_LOOKUP_TOOLS)


@pytest.mark.parametrize('text', ['确认', '查询字段然后创建对象', '查看类型后改为Integer',
                                    '帮我生成Product设计', '为什么保存失败', '继续'])
def test_design_and_ambiguous_followups_keep_full_tools(text):
    assert not chat_intent.metadata_lookup(text)


def test_installed_inheritance_matches_vendor_parent(installed_sdk):
    result = vendor.query(str(installed_sdk), 'inheritance', 'cdo', 'Product')
    assert result['complete'] and result['total'] == len(result['records'])
    assert result['parent'] == 'RevisionedObject'
    assert all(row['name'] != 'Product' and row['depth'] >= 1 for row in result['records'])
    for row in result['records'][:2]:
        detail = vendor.query(str(installed_sdk), 'get', 'cdo', row['name'])
        assert detail['ParentCDO']['name'] == row['parent']
    assert vendor.query(str(installed_sdk), 'inheritance', 'cdo', 'Product') == result

