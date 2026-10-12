"""Opt-in configured-model checks. Generate arguments/text; never execute tools."""
import asyncio
import json
import os

import pytest

pytestmark = pytest.mark.skipif(os.getenv('CHAT_TEST_MODEL') != '1', reason='set CHAT_TEST_MODEL=1 to use the configured model')


def test_configured_model_keeps_fields_when_renaming_a_table_design():
    from agent.chat_context import build_messages
    from agent.llm_client import oai_client
    from agent.prompts import SYSTEM_PROMPT, CHAT_QUALITY_RULE
    from config import LLM_MODEL
    from designer.vendor import validate_operations
    from tools import mcp

    original = {'mdb_file': 'test-only.mdb', 'expected_sha256': 'a' * 64, 'workspace': '200', 'operations': [
        {'action': 'create_cdo', 'name': 'AssetTrace_6381', 'parent': 'NamedDataObject', 'create_table': True, 'table_name': 'AssetTrace_6381'},
        {'action': 'create_field_type', 'name': 'ExEmailText', 'data_type': 'String', 'max_length': 200},
        {'action': 'add_field', 'owner': 'AssetTrace_6381', 'name': 'OwnerEmail', 'field_type': 'ExEmailText', 'persistent': True},
    ]}
    task = {'goal': '创建客户 CDO 和对应物理表，OwnerEmail String 长度200，持久化；工作区200。',
            'requires_table': True, 'required_object_name': 'AssetTrace_6382',
            'proposed_plan': [{'tool': 'generate_designer_design_package', 'arguments': original}]}
    messages = build_messages([
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'system', 'content': '这是隔离参数评估，所有真实执行接口均未连接。工具调用在本测试中只收集拟定参数，运行器不会执行任何工具。来源、工作区和父对象已通过假只读回执核对，无命名冲突。本轮请通过工具参数提案生成预览，继续遵守后端预览与确认门槛。'},
        {'role': 'user', 'content': 'AssetTrace_6381换成6382，其他字段和属性不变，请生成下一版设计预览。'},
    ], task=task, extra_system=[CHAT_QUALITY_RULE])

    async def run():
        tool = next(tool for tool in await mcp.list_tools() if tool.name == 'generate_designer_design_package')
        async with oai_client.with_options(timeout=35, max_retries=0) as client:
            response = await asyncio.wait_for(client.chat.completions.create(
                model=LLM_MODEL, messages=messages,
                tools=[{'type': 'function', 'function': {'name': tool.name, 'description': tool.description, 'parameters': tool.parameters}}],
                tool_choice='auto', max_tokens=4000, temperature=0,
            ), timeout=40)
        message = response.choices[0].message
        assert message.tool_calls, '模型未提交预览参数：' + (message.content or '')[:600]
        proposal = json.loads(message.tool_calls[0].function.arguments)
        operations = proposal['operations']
        validate_operations(operations)
        cdo = next(op for op in operations if op['action'] == 'create_cdo')
        field = next(op for op in operations if op['action'] == 'add_field')
        field_type = next(op for op in operations if op['action'] == 'create_field_type')
        assert cdo['name'] == cdo['table_name'] == 'AssetTrace_6382' and cdo['create_table'] is True
        assert field['owner'] == 'AssetTrace_6382' and field['name'] == 'OwnerEmail' and field['persistent'] is True
        assert field_type['max_length'] == 200 and field_type['data_type'] == 'String'
        assert proposal['expected_sha256'] == original['expected_sha256'] and str(proposal['workspace']) == '200'
    asyncio.run(run())
