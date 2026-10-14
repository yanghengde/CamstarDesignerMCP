"""A pending batch must not partially save before a stale second MDB call."""
import json
from pathlib import Path

import pytest

import config
from designer import working
from designer.design_contract import validate_calls


def cdo_call(source, name='ExOne', checksum='a' * 64):
    return {'function': {'name': 'generate_designer_cdo_package', 'arguments': json.dumps({
        'mdb_file': str(source), 'expected_sha256': checksum, 'cdo_name': name,
        'parent_cdo': 'NamedDataObject', 'fields': [{'name': 'Code', 'field_type': 'Name'}]})}}


def generic_call(source):
    return {'function': {'name': 'generate_designer_design_package', 'arguments': json.dumps({
        'mdb_file': str(source), 'expected_sha256': 'a' * 64,
        'operations': [{'action': 'create_cdo', 'name': 'ExTwo', 'parent': 'NamedDataObject'}]})}}


def test_same_mdb_cannot_be_split_across_specialized_and_generic_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    source = tmp_path / 'source.mdb'
    source.write_bytes(b'unchanged source')
    with pytest.raises(ValueError, match='合并为一个 generate_designer_design_package'):
        validate_calls([cdo_call(source), generic_call(source)])
    assert source.read_bytes() == b'unchanged source'


def test_duplicate_mock_paths_are_normalized_before_any_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    with pytest.raises(ValueError, match='同一个工作 MDB'):
        validate_calls([cdo_call('source.mdb'), cdo_call(tmp_path / 'nested' / '..' / 'source.mdb', 'ExTwo')])
    assert not list(tmp_path.iterdir())


def test_existing_legacy_source_alias_resolves_to_current_working_mdb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    seed = tmp_path / 'seed.mdb'
    seed.write_bytes(b'original')
    project_id, _ = working.identity(seed)
    folder = working.project_dir(project_id)
    folder.mkdir(parents=True)
    current = folder / 'InSite.mdb'
    current.write_bytes(b'latest')
    working.write_json(folder / 'state.json', {'id': project_id, 'latest_manifest': ''})
    with pytest.raises(ValueError, match='同一个工作 MDB'):
        validate_calls([cdo_call(seed), generic_call(current)])


def test_different_mdb_targets_and_reads_can_share_one_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    validate_calls([cdo_call('first.mdb'), generic_call('second.mdb'),
                    {'function': {'name': 'list_designer_entities', 'arguments': '{}'}}])


def test_invalid_second_call_is_reported_before_cross_call_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    invalid = cdo_call('source.mdb', 'ExTwo')
    args = json.loads(invalid['function']['arguments'])
    args['unknown_option'] = True
    invalid['function']['arguments'] = json.dumps(args)
    with pytest.raises(ValueError, match='参数不完整或包含未知参数'):
        validate_calls([cdo_call('source.mdb'), invalid])


def test_source_permission_errors_are_not_treated_as_absent_mock_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    original_stat = Path.stat
    def stat(path, *args, **kwargs):
        if path.name == 'locked.mdb':
            raise PermissionError('source is protected')
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)
    with pytest.raises(PermissionError, match='source is protected'):
        validate_calls([cdo_call('locked.mdb'), generic_call('second.mdb')])


def test_single_call_does_not_require_a_source_during_argument_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    monkeypatch.setattr(working, 'resolve', lambda value: pytest.fail('single-call file lookup is unnecessary'))
    validate_calls([cdo_call('source.mdb')])
