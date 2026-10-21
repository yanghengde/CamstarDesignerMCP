"""Exercise real WCF entry points; mock only SDK, SQL Agent and file transport."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

import config
from designer import services, vendor, progress_actions, wcf_contract, progress
from tools import designer_design
from tests.test_designer_progress import workflow, ready_for_publish


def expected(owner='Workflow', name='ExtwfStatus'):
    return {'owner': owner, 'name': name, 'present': True, 'data_type': 'Integer',
            'field_type': '[Integer]', 'precision': 0, 'scale': 0, 'is_list': False}


def output(contract='WorkflowChanges', name='ExtwfStatus', data_type='Integer'):
    clr = wcf_contract.CLR_TYPES[data_type]
    schema = {name: {'type': f'Primitive<{clr}>', 'value_type': clr, 'is_list': False}}
    return {'status': 'services_generated', 'data_contract_count': 2592, 'service_count': 798,
            'type_checks': [{'name': 'Camstar.WCF.ObjectStack.' + contract, 'properties': [name], 'field_schemas': schema}],
            'server_type_checks': [{'name': 'Camstar.WCF.Service.' + contract, 'properties': [name], 'field_schemas': schema}]}


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    source = tmp_path / 'compiled.mdb'; source.write_bytes(b'compiled metadata')
    state = SimpleNamespace(source=source, payload=output(), stages=[], statements=[],
                            omit_server=False, change_source=False)
    monkeypatch.setattr(services, 'worker_environment', lambda: {})
    def transfer(folder, mode, env):
        if mode == 'settings':
            (folder / 'server_settings.json').write_text(json.dumps({'server_connection': 'localhost:443'}))
        elif mode == 'stage':
            state.stages.append(json.loads((folder / 'request.json').read_text()))
            if state.change_source: source.write_bytes(b'drifted')
        elif mode == 'fetch':
            client = folder / 'client'; client.mkdir()
            (client / 'Camstar.WCFClient.dll').write_bytes(b'client')
            if not state.omit_server:
                server = folder / 'server' / 'Bin'; server.mkdir(parents=True)
                (server / 'Camstar.WCFService.dll').write_bytes(b'server')
            (folder / 'result.json').write_text(json.dumps({'ok': True, 'result': state.payload}))
    monkeypatch.setattr(services, 'transfer', transfer)
    class Cursor:
        def execute(self, sql, *args):
            state.statements.append(sql)
            return self
        def fetchone(self): return (1,)
    @contextmanager
    def connection(*args, **kwargs):
        yield SimpleNamespace(cursor=lambda: Cursor())
    monkeypatch.setattr(services, 'target_connection', connection)
    return state


def test_compiled_sdk_schema_reaches_actual_public_tool_and_worker(backend, monkeypatch):
    rows = [expected(), expected('WorkflowChanges')]
    monkeypatch.setattr(vendor, 'run', lambda *args: {'complete': True, 'fields': rows})
    compiled = progress_actions.compiled_field_contracts(str(backend.source), vendor.digest(backend.source),
        [{'owner': row['owner'], 'name': row['name'], 'present': True} for row in rows])
    result = asyncio.run(designer_design.generate_designer_wcf_package(
        str(backend.source), vendor.digest(backend.source), ['Workflow', 'WorkflowChanges'], expected_fields=compiled))
    assert json.loads(backend.stages[0]['expected_fields']) == rows
    assert result['status'] == 'services_generated'
    assert result['source_unchanged'] is True and not result['partial_package']
    assert any('sp_delete_job' in sql for sql in backend.statements)


def test_legacy_three_property_expectation_remains_supported(backend):
    row = {'owner': 'Workflow', 'name': 'ExtwfStatus', 'present': True}
    result = services.generate_wcf(str(backend.source), vendor.digest(backend.source), ['Workflow'], expected_fields=[row])
    assert result['status'] == 'services_generated'
    assert json.loads(backend.stages[0]['expected_fields']) == [row]


def test_partial_generation_stays_partial_and_cannot_complete_full_acceptance(backend):
    # Scope comes from the caller even if a worker returns a different flag.
    backend.payload['partial_package'] = False
    result = services.generate_wcf(str(backend.source), vendor.digest(backend.source), ['Workflow'],
                                  service_names=['WorkflowMaint'], expected_fields=[expected()])
    assert result['partial_package'] is True
    with pytest.raises(ValueError, match='全量'):
        progress_actions.verify_wcf_fields(result, {'field_expectations': [expected()]}, ['Workflow'])


@pytest.mark.parametrize('change, message', [
    ({'is_list': 'false'}, 'is_list'), ({'precision': True}, 'precision'),
    ({'scale': -1}, 'scale'), ({'data_type': ''}, 'data_type'),
    ({'present': 1}, 'present'), ({'unexpected': True}, '未知属性'),
])
def test_invalid_extended_schema_stops_before_remote_work(backend, change, message):
    with pytest.raises(ValueError, match=message):
        services.generate_wcf(str(backend.source), vendor.digest(backend.source), ['Workflow'],
                              expected_fields=[{**expected(), **change}])
    assert not backend.stages and not backend.statements


@pytest.mark.parametrize('values', [[{}], [None], [1], ['Workflow', 'workflow']])
def test_malformed_names_return_validation_error_before_remote_work(backend, values):
    with pytest.raises(ValueError):
        services.generate_wcf(str(backend.source), vendor.digest(backend.source), values)
    assert not backend.stages and not backend.statements


def test_duplicate_or_conflicting_expectations_cannot_overwrite_each_other(backend):
    for row in (expected(), {**expected(), 'present': False}):
        with pytest.raises(ValueError, match='重复'):
            services.generate_wcf(str(backend.source), vendor.digest(backend.source), ['Workflow'], expected_fields=[expected(), row])
    assert not backend.stages and not backend.statements


@pytest.mark.parametrize('bad', ['wrong_owner', 'duplicate', 'incomplete_shape', 'missing_field'])
def test_sdk_response_is_checked_for_identity_and_complete_schema(backend, monkeypatch, bad):
    row = expected()
    returned = ([{**row, 'owner': 'Other'}] if bad == 'wrong_owner' else [row, row] if bad == 'duplicate' else
                [{'owner': row['owner'], 'name': row['name'], 'present': True}] if bad == 'incomplete_shape' else
                [{**row, 'present': False}])
    monkeypatch.setattr(vendor, 'run', lambda *args: {'complete': True, 'fields': returned})
    with pytest.raises(ValueError):
        progress_actions.compiled_field_contracts(str(backend.source), vendor.digest(backend.source), [row])
    assert not backend.stages


@pytest.mark.parametrize('failure', ['missing_dll', 'zero_count', 'changed_source', 'missing_server_field', 'conflicting_contract'])
def test_public_tool_rejects_success_flag_with_incomplete_evidence(backend, failure):
    if failure == 'missing_dll': backend.omit_server = True
    if failure == 'zero_count': backend.payload['service_count'] = 0
    if failure == 'changed_source': backend.change_source = True
    if failure == 'missing_server_field': backend.payload['server_type_checks'][0]['properties'] = []
    if failure == 'conflicting_contract':
        backend.payload['type_checks'].append({**backend.payload['type_checks'][0], 'properties': []})
    with pytest.raises(ValueError, match='WCF'):
        services.generate_wcf(str(backend.source), vendor.digest(backend.source), ['Workflow'], expected_fields=[expected()])
    receipt, = Path(config.DESIGNER_ROOT).glob('artifacts/*/generation_result.json')
    assert json.loads(receipt.read_text(encoding='utf-8'))['status'] == 'generation_failed'
    assert any('sp_delete_job' in sql for sql in backend.statements)


def test_tool_schema_exposes_optional_sdk_properties_and_rejects_typos():
    adapter = TypeAdapter(wcf_contract.ExpectedField)
    schema = adapter.json_schema()
    assert {'data_type', 'field_type', 'is_list', 'precision', 'scale'}.issubset(schema['properties'])
    assert set(schema['required']) == {'owner', 'name', 'present'}
    assert adapter.validate_python(expected()) == expected()
    with pytest.raises(ValidationError): adapter.validate_python({**expected(), 'scalee': 0})


def test_verifying_existing_vendor_names_does_not_apply_new_design_reserved_words():
    assert wcf_contract.fields([{'owner': 'ExA', 'name': 'Old', 'present': False}])
    assert wcf_contract.names(['System', 'Old']) == ['System', 'Old']


def test_page_workflow_uses_real_compile_contract_and_generation_functions(backend, workflow, monkeypatch):
    from tools import designer
    sid, folder = workflow
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(folder.parent.parent))
    ready_for_publish(sid, folder, monkeypatch)
    progress.start_action('user', sid, folder.name, 'confirm_publish')
    async def check(path): return {'intact': True}
    monkeypatch.setattr(designer, 'check_designer_package', check)
    monkeypatch.setattr(progress.review, 'sync_file', lambda *args: {'unchanged': True})
    dll = folder / 'metadata.dll'; dll.write_bytes(b'test SDK')
    monkeypatch.setattr(vendor, 'assembly', lambda: dll)
    row = {**expected('ExSample', 'Code'), 'data_type': 'String', 'field_type': 'Name', 'precision': 40}
    backend.payload = output('ExSampleChanges', 'Code', 'String')
    def sdk(output_folder, request):
        if request['mode'] == 'compile':
            file = output_folder / 'compiled.mdb'; file.write_bytes(b'compiled metadata')
            return {'compiled_mdb': str(file)}
        assert request['mode'] == 'field_contracts'
        return {'complete': True, 'fields': [row]}
    monkeypatch.setattr(vendor, 'run', sdk)
    monkeypatch.setattr(vendor, 'query', lambda *args, **kwargs: {'total': 1, 'records': [{'name': 'Code'}]})
    async def execute():
        progress.start_action('user', sid, folder.name, 'wcf', {'verify_types': ['ExSample']})
        await asyncio.gather(*list(progress._tasks))
        state = progress.snapshot('user', sid)
        assert state['stages'][8]['status'] == 'done', state['last_job']
        assert state['stages'][9]['status'] == 'pending'
        assert state['wcf']['verified_fields'] == [row]
        assert state['wcf']['verification_version'] == 2
        assert progress_actions.wcf_archive(state['wcf']).is_file()
    asyncio.run(execute())
    assert json.loads(backend.stages[0]['expected_fields']) == [row]
