"""Use the configured complete Update DB installation without mixing DLLs."""
import pytest

import config
from designer import publication


def component_set(directory):
    directory.mkdir()
    for name in ('Camstar.Data.dll', 'OECAdmin.dll', 'CIMS.DBUpdate.dll'):
        (directory / name).write_bytes(b'test')
    return directory


def test_publication_components_can_be_separate_from_metadata(tmp_path, monkeypatch):
    metadata = tmp_path / 'Designer2'
    metadata.mkdir()
    dll = metadata / 'Camstar.Metadata.dll'
    dll.write_bytes(b'metadata')
    admin = component_set(tmp_path / 'InSite Administration')
    monkeypatch.setattr(config, 'DESIGNER_METADATA_ASSEMBLY', str(dll))
    monkeypatch.setattr(config, 'DESIGNER_PUBLICATION_ASSEMBLY_DIRECTORY', str(admin))
    assert publication.publication_assembly_directory() == admin
    assert config.DESIGNER_METADATA_ASSEMBLY == str(dll)


def test_unconfigured_publication_directory_keeps_legacy_layout(tmp_path, monkeypatch):
    metadata = component_set(tmp_path / 'combined')
    dll = metadata / 'Camstar.Metadata.dll'
    dll.write_bytes(b'metadata')
    monkeypatch.setattr(config, 'DESIGNER_METADATA_ASSEMBLY', str(dll))
    monkeypatch.setattr(config, 'DESIGNER_PUBLICATION_ASSEMBLY_DIRECTORY', '')
    assert publication.publication_assembly_directory() == metadata


def test_missing_component_names_the_configured_directory(tmp_path, monkeypatch):
    admin = component_set(tmp_path / 'admin')
    (admin / 'OECAdmin.dll').unlink()
    monkeypatch.setattr(config, 'DESIGNER_PUBLICATION_ASSEMBLY_DIRECTORY', str(admin))
    with pytest.raises(ValueError, match='OECAdmin.dll') as error:
        publication.publication_assembly_directory()
    assert str(admin) in str(error.value)
    assert 'DESIGNER_PUBLICATION_ASSEMBLY_DIRECTORY' in str(error.value)
