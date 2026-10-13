"""Physical acceptance follows vendor DDL, including defaults and column usage."""
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import fromstring

import pytest
import config
from designer import acceptance, column_contract, publication, vendor


def contract(clause='Code nvarchar(255) NULL', **extra):
    return column_contract.parse_definition({'table': 'ExTable', 'name': clause.split()[0],
        'sql_definition': clause, 'nullable': True, **extra})


@pytest.mark.parametrize('definition,row,passed', [
    ('ID char(16) NULL', ('char', 16, None, None, 'YES'), True),
    ('ID char(16) NULL', ('char', 32, None, None, 'YES'), False),
    ('Code nvarchar(255) NULL', ('nvarchar', 255, None, None, 'YES'), True),
    ('Code nvarchar(255) NULL', ('nvarchar', 256, None, None, 'YES'), False),
    ('Code nvarchar(24) NULL', ('varchar', 24, None, None, 'YES'), False),
    ('Code nvarchar(24) NULL', ('nvarchar', 24, None, None, 'NO'), False),
    ('Text nvarchar(max) NULL', ('nvarchar', -1, None, None, 'YES'), True),
    ('Text nvarchar(max) NULL', ('nvarchar', 255, None, None, 'YES'), False),
    ('Value decimal(18,2) NULL', ('decimal', None, 18, 2, 'YES'), True),
    ('Value decimal(18,2) NULL', ('decimal', None, 18, 3, 'YES'), False),
    ('Flag bit NULL', ('int', None, 10, 0, 'YES'), False),
    ('Created datetime NULL', ('datetime', None, None, None, 'YES'), True),
    ('Data varbinary(max) NULL', ('varbinary', -1, None, None, 'YES'), True),
    ('Code nvarchar(255) NULL', None, False),
])
def test_exact_physical_contract_rejects_actual_mismatches(definition, row, passed):
    assert column_contract.compare(contract(definition), row)['passed'] is passed


@pytest.mark.parametrize('clause', ['Code nvarchar(0) NULL', 'Code varchar NULL',
    'Code mystery NULL', 'Code datetime2(7) NULL', 'Code decimal NULL'])
def test_unaccepted_generator_syntax_never_becomes_success(clause):
    with pytest.raises(ValueError):
        contract(clause)


def xml_table(columns='<DBColumnDefinition Name="Code" Action="Update"/>', table='ExTable'):
    return fromstring('<InSiteMetaData><Header><Version>1.0</Version></Header><Import>'
        f'<DBTableDefinitions><DBTableDefinition Name="{table}"><Columns>{columns}</Columns>'
        '</DBTableDefinition></DBTableDefinitions></Import></InSiteMetaData>')


def test_partial_xml_requests_values_from_compiled_mdb_and_skips_deleted_columns():
    root = xml_table('<DBColumnDefinition Name="Code" Action="Update"/>'
                     '<DBColumnDefinition Name="Gone" Action="Delete"/>')
    assert column_contract.requested_columns(root) == [{'name': 'ExTable', 'columns': ['Code']}]
    with pytest.raises(ValueError, match='表名'):
        column_contract.requested_columns(xml_table(table="ExTable' OR '1'='1"))


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    mdb = tmp_path / 'compiled.mdb'; mdb.write_bytes(b'compiled')
    dll = tmp_path / 'CIMS.DBUpdate.dll'; dll.write_bytes(b'vetted engine')
    monkeypatch.setattr(vendor, 'assembly', lambda: tmp_path / 'Camstar.Metadata.dll')
    monkeypatch.setattr(column_contract, 'SUPPORTED_ENGINES', {vendor.digest(dll): 'tested'})
    return mdb, dll


def bridge_result(args, columns, returncode=0):
    file = Path(args[args.index('-ResultFile') + 1])
    file.write_text(json.dumps({'ok': True, 'result': {'columns': columns}}), encoding='utf-8')
    return SimpleNamespace(returncode=returncode)


def test_resolver_preserves_vendor_default_and_component_evidence(backend, monkeypatch):
    mdb, dll = backend
    monkeypatch.setattr(column_contract.subprocess, 'run',
        lambda args, **kwargs: bridge_result(args, [contract()]))
    result = column_contract.resolve(mdb, xml_table())
    assert result['columns'][0]['expected_length'] == 255
    assert result['assembly_sha256'] == vendor.digest(dll)
    assert result['compiled_sha256'] == vendor.digest(mdb)
    assert mdb.read_bytes() == b'compiled'


def test_unknown_engine_is_rejected_before_bridge(backend, monkeypatch):
    mdb, _ = backend
    monkeypatch.setattr(column_contract, 'SUPPORTED_ENGINES', {})
    monkeypatch.setattr(column_contract.subprocess, 'run', lambda *a, **k: pytest.fail('must not run'))
    with pytest.raises(ValueError, match='版本尚未验收'):
        column_contract.resolve(mdb, xml_table())


@pytest.mark.parametrize('columns', [[], [contract(), contract()]])
def test_incomplete_or_duplicate_contract_cannot_pass(backend, monkeypatch, columns):
    monkeypatch.setattr(column_contract.subprocess, 'run',
        lambda args, **kwargs: bridge_result(args, columns))
    with pytest.raises(ValueError, match='不完整或重复'):
        column_contract.resolve(backend[0], xml_table())


def test_process_failure_is_rejected_even_after_writing_result(backend, monkeypatch):
    monkeypatch.setattr(column_contract.subprocess, 'run',
        lambda args, **kwargs: bridge_result(args, [contract()], returncode=1))
    with pytest.raises(ValueError, match='读取失败'):
        column_contract.resolve(backend[0], xml_table())


@pytest.mark.parametrize('changed', [0, 1])
def test_mdb_or_engine_drift_cannot_pass(backend, monkeypatch, changed):
    def run(args, **kwargs):
        backend[changed].write_bytes(b'changed during read')
        return bridge_result(args, [contract()])
    monkeypatch.setattr(column_contract.subprocess, 'run', run)
    with pytest.raises(ValueError, match='已变化'):
        column_contract.resolve(backend[0], xml_table())


@pytest.mark.parametrize('actual_length,expected_status', [(255, 'verified'), (100, 'verification_failed')])
def test_final_verification_uses_vendor_default_even_with_incomplete_diff(backend, monkeypatch, actual_length, expected_status):
    mdb, _ = backend
    folder = mdb.parent
    (folder/'changes.xml').write_text('<InSiteMetaData><Header><Version>1.0</Version></Header><Import>'
        '<DBTableDefinitions><DBTableDefinition Name="ExTable"><Columns>'
        '<DBColumnDefinition Name="Code" Action="Update"><Attributes><Precision>0</Precision></Attributes></DBColumnDefinition>'
        '</Columns></DBTableDefinition></DBTableDefinitions></Import></InSiteMetaData>')
    monkeypatch.setattr(publication, 'verified_manifest', lambda file: (folder/'manifest.json', {}))
    monkeypatch.setattr(column_contract, 'resolve', lambda *args: {'columns': [contract()]})
    monkeypatch.setattr(acceptance, 'verify_manifest', lambda *args: [])
    monkeypatch.setattr(publication, 'target_schema', lambda cursor: 'dbo')
    class Cursor:
        def execute(self, sql, *args):
            assert 'INFORMATION_SCHEMA.COLUMNS' in sql
            assert args == ('dbo', 'ExTable', 'Code')
            return self
        def fetchone(self): return ('nvarchar', actual_length, None, None, 'YES')
    @contextmanager
    def connection(): yield SimpleNamespace(cursor=lambda: Cursor())
    monkeypatch.setattr(publication, 'target_connection', connection)
    result = publication.verify_published_design('manifest.json', compiled_mdb=str(mdb))
    assert result['status'] == expected_status
    assert result['checks'][0]['expected_precision'] == '0'
    assert result['checks'][0]['expected_length'] == 255


def test_operational_issues_do_not_claim_runtime_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_DB_PASSWORD', 'secret')
    file = tmp_path/'update.log'
    file.write_text('ERROR: Update to SiteState table failed: duplicate key\n'
                    'WARNING: SendMessage: connection failed secret\nResult of update operation: True')
    result = publication.update_diagnostics(file)
    assert result['site_state_status'] == 'failed'
    assert result['runtime_recycle_status'] == 'failed'
    assert result['runtime_service_verified'] is False
    assert 'secret' not in json.dumps(result)
    file.write_text('Result of update operation: True')
    assert publication.update_diagnostics(file)['runtime_recycle_status'] == 'not_verified'


def test_unaccepted_column_api_stops_publication_before_target_access(backend, monkeypatch):
    from designer import working
    mdb, _ = backend
    folder = mdb.parent
    manifest = folder/'manifest.json'; manifest.write_text('{}')
    (folder/'modified.mdb').write_bytes(b'design')
    (folder/'changes.xml').write_text('<InSiteMetaData><Header><Version>1.0</Version></Header><Import/></InSiteMetaData>')
    monkeypatch.setattr(config, 'DESIGNER_TEST_TARGET_CONFIRMED', True)
    monkeypatch.setattr(config, 'DESIGNER_REQUIRE_DATABASE_BACKUP', False)
    monkeypatch.setattr(publication, 'verified_manifest', lambda *args: (manifest, {}))
    monkeypatch.setattr(working, 'check_current', lambda *args: None)
    def compile(output, request):
        file = output/'compiled.mdb'; file.write_bytes(b'compiled')
        return {'compiled_mdb': str(file)}
    monkeypatch.setattr(vendor, 'run', compile)
    def unsupported(*args): raise ValueError('Camstar 列生成 API 版本尚未验收')
    monkeypatch.setattr(column_contract, 'resolve', unsupported)
    monkeypatch.setattr(publication, 'target_connection', lambda *args, **kwargs: pytest.fail('target must not be accessed'))
    with pytest.raises(ValueError, match='版本尚未验收'):
        publication.publish_database(str(manifest), vendor.digest(manifest), siteinfo_mdb=str(mdb))
    report = json.loads(next(folder.rglob('publish_result.json')).read_text(encoding='utf-8'))
    assert report['database_modified'] is False
    assert report['database_update_status'] == 'not_started'
