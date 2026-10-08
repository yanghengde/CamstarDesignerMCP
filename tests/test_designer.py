"""Offline contracts: actual artifact contents, conflicts, path isolation, XML and export."""

import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from contextlib import contextmanager
import xml.etree.ElementTree as ET

import pytest

import config
from designer.files import source_path
from designer import mdb, catalog
from designer.metadata import build_field_draft, cdo_nodes, fields, identifier, load_xml, serialize
from tools import designer as tools

FIXTURE = Path(__file__).resolve().parents[1] / "examples/designer/demo_metadata.xml"


@pytest.fixture
def metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DESIGNER_ROOT", str(tmp_path))
    path = tmp_path / "baseline.xml"
    path.write_bytes(FIXTURE.read_bytes())
    return path


def run(coro):
    return asyncio.run(coro)


def generate(path, **kwargs):
    params = dict(
        xml_file=str(path), expected_sha256=sha256(path.read_bytes()).hexdigest(),
        target_cdo="DemoContainer", template_cdo="DemoContainer", template_field="ExistingText",
        field_name="ExternalLotNumber", workspace="customer", description="批次号 <外部> & 来源",
    )
    params.update(kwargs)
    return run(tools.generate_designer_field_package(**params))


def test_package_has_correct_field_and_preserves_source(metadata):
    original = metadata.read_bytes()
    result = generate(metadata)
    root, _ = load_xml(Path(result["files"]["changes.xml"]))
    cdo = cdo_nodes(root)[0]
    assert cdo.get("Name") == "DemoContainer"
    assert cdo.get("Action") == "Import"
    field = fields(cdo)[0]
    assert field.get("Name") == "ExternalLotNumber"
    assert field.get("Action") == "Create"
    assert field.findtext("Attributes/FieldDef/Name") == "String"
    assert field.findtext("Attributes/FieldDescription") == "批次号 <外部> & 来源"
    assert root.findtext("Header/Defaults/ExpectedValues/ActionIfDifferent") == "Stop"
    assert metadata.read_bytes() == original
    assert result["ready_for_publish"] is False
    manifest = json.loads(Path(result["files"]["manifest.json"]).read_text(encoding="utf-8"))
    assert manifest["workspace_applied_to_xml"] is False
    assert run(tools.check_designer_package(result["files"]["manifest.json"]))["intact"] is True


def test_stale_source_hash_is_rejected_without_output(metadata):
    with pytest.raises(ValueError, match="SHA256"):
        generate(metadata, expected_sha256="0" * 64)
    assert not (metadata.parent / "artifacts").exists()


def test_invalid_xml_description_is_rejected_without_output(metadata):
    with pytest.raises(ValueError, match="XML 1.0"):
        generate(metadata, description="bad\x00description")
    assert not (metadata.parent / "artifacts").exists()


def test_source_drift_and_package_tampering_are_reported(metadata):
    result = generate(metadata)
    metadata.write_bytes(metadata.read_bytes().replace(b"ExistingText", b"ChangedText"))
    check = run(tools.check_designer_package(result["files"]["manifest.json"]))
    assert check["intact"] is False
    assert check["checks"]["source_unchanged"] is False
    output = Path(result["files"]["changes.xml"])
    output.write_bytes(output.read_bytes().replace(b"ExternalLotNumber", b"OtherField"))
    check = run(tools.check_designer_package(result["files"]["manifest.json"]))
    assert check["checks"]["changes.xml"] is False


@pytest.mark.parametrize("name", ["existingtext", "select", "a-b", "中文", "1Field"])
def test_invalid_or_duplicate_field_names_are_rejected(metadata, name):
    with pytest.raises(ValueError):
        generate(metadata, field_name=name)


@pytest.mark.parametrize("attr", ["<IsPersistent>True</IsPersistent>", "<IsList>True</IsList>", "<DBColumn><Name>ExternalLot</Name></DBColumn>", "<UnknownReference><Name>Other</Name></UnknownReference>"])
def test_complex_or_persistent_templates_are_rejected(metadata, attr):
    data = metadata.read_text(encoding="utf-8")
    data = data.replace("<IsPersistent>False</IsPersistent>", "").replace("<IsList>False</IsList>", "")
    metadata.write_text(data.replace("</Attributes>\n          </CDOFieldDefinition>", attr + "</Attributes>\n          </CDOFieldDefinition>"), encoding="utf-8")
    with pytest.raises(ValueError):
        generate(metadata)


def test_inherited_field_conflict_is_rejected(metadata):
    root, _ = load_xml(metadata)
    cdo = cdo_nodes(root)[0]
    parent = deepcopy(cdo)
    parent.set("Name", "BaseContainer")
    fields(parent)[0].set("Name", "ExternalLotNumber")
    root.find("Import/CDODefinitions").append(parent)
    ET.SubElement(ET.SubElement(cdo.find("Attributes"), "ParentCDO"), "Name").text = "BaseContainer"
    metadata.write_bytes(serialize(root))
    with pytest.raises(ValueError, match="父 CDO"):
        generate(metadata)


def test_missing_parent_is_recorded_as_unresolved(metadata):
    root, _ = load_xml(metadata)
    ET.SubElement(ET.SubElement(cdo_nodes(root)[0].find("Attributes"), "ParentCDO"), "Name").text = "MissingParent"
    metadata.write_bytes(serialize(root))
    assert generate(metadata)["plan"]["unresolved_parent"] == "MissingParent"


def test_direct_field_layout_and_expected_values(metadata):
    root, _ = load_xml(metadata)
    cdo = cdo_nodes(root)[0]
    field = fields(cdo)[0]
    cdo.remove(cdo.find("CDOFieldDefinitions"))
    cdo.append(field)
    desc = field.find("Attributes/FieldDescription")
    desc.text = None
    ET.SubElement(desc, "NewValue").text = "new description"
    ET.SubElement(desc, "ExpectedValue").text = "old description"
    draft, _ = build_field_draft(root, "DemoContainer", "DemoContainer", "ExistingText", "NewText", None)
    generated = cdo_nodes(draft)[0].find("CDOFieldDefinition")
    assert generated is not None
    assert generated.findtext("Attributes/FieldDescription") == "new description"
    assert not list(generated.iter("ExpectedValue"))


@pytest.mark.parametrize("payload", [
    b'<!DOCTYPE InSiteMetaData [<!ENTITY e "boom">]><InSiteMetaData>&e;</InSiteMetaData>',
    b'<wrong/>', b'<InSiteMetaData><Header><Version>2.0</Version></Header><Import/></InSiteMetaData>',
])
def test_invalid_and_unsafe_xml_is_rejected(metadata, payload):
    metadata.write_bytes(payload)
    with pytest.raises(ValueError):
        run(tools.validate_designer_xml(str(metadata)))


def test_case_insensitive_duplicate_definition_is_rejected(metadata):
    root, _ = load_xml(metadata)
    cdo = cdo_nodes(root)[0]
    duplicate = deepcopy(fields(cdo)[0])
    duplicate.set("Name", "existingtext")
    cdo.find("CDOFieldDefinitions").append(duplicate)
    metadata.write_bytes(serialize(root))
    with pytest.raises(ValueError, match="重复"):
        run(tools.validate_designer_xml(str(metadata)))


def test_deep_xml_is_rejected(metadata):
    metadata.write_text('<InSiteMetaData><Header><Version>1.0</Version></Header><Import>' + '<X>' * 140 + '</X>' * 140 + '</Import></InSiteMetaData>', encoding="utf-8")
    with pytest.raises(ValueError, match="嵌套深度"):
        load_xml(metadata)


def test_path_escape_and_wrong_extension_are_rejected(metadata):
    outside = metadata.parent.parent / "outside.xml"
    outside.write_bytes(FIXTURE.read_bytes())
    with pytest.raises(ValueError):
        source_path(str(outside), ".xml")
    with pytest.raises(ValueError):
        source_path("../outside.xml", ".xml")
    with pytest.raises(ValueError):
        source_path(".env", ".xml")


def test_compare_ignores_format_and_action_but_finds_attribute_change(metadata):
    root, _ = load_xml(metadata)
    cdo_nodes(root)[0].set("Action", "Create")
    other = metadata.parent / "other.xml"
    other.write_bytes(serialize(root))
    assert run(tools.compare_designer_xml(str(metadata), str(other)))["counts"] == {"added": 0, "missing": 0, "changed": 0}
    fields(cdo_nodes(root)[0])[0].find("Attributes/FieldDescription").text = "changed"
    other.write_bytes(serialize(root))
    diff = run(tools.compare_designer_xml(str(metadata), str(other)))
    assert diff["counts"] == {"added": 0, "missing": 0, "changed": 1}
    assert "CDOFieldDefinition:existingtext" in diff["changed"][0]


def test_registry_contains_only_designer_tools_in_clean_process():
    code = "import asyncio,json; from tools import mcp; print(json.dumps([t.name for t in asyncio.run(mcp.list_tools())]))"
    names = json.loads(subprocess.check_output([sys.executable, "-c", code], text=True))
    assert set(names) == {
        "get_designer_environment", "inspect_designer_mdb", "read_designer_mdb_table",
        "list_designer_mdb_cdos", "get_designer_mdb_cdo", "list_designer_cdos",
        "get_designer_cdo", "validate_designer_xml", "compare_designer_xml",
        "generate_designer_field_package", "check_designer_package",
        "export_designer_metadata_diff", "get_mcp_server_status",
        "get_designer_capabilities", "get_designer_catalog", "list_designer_entities",
        "get_designer_entity", "get_designer_entity_schema", "analyze_designer_where_used",
        "generate_designer_design_package", "generate_designer_cdo_package", "export_designer_vendor_diff",
        "inspect_designer_database", "prepare_designer_publish_plan",
        "compile_designer_mdb",
        "backup_designer_test_database", "publish_designer_test_database",
        "restore_designer_test_database", "verify_designer_published_design",
        "generate_designer_wcf_package",
        "inspect_designer_installation",
    }


def test_mdb_connect_enforces_readonly(monkeypatch, tmp_path):
    pyodbc = pytest.importorskip("pyodbc")
    calls = []
    connection = SimpleNamespace(close=lambda: calls.append("closed"))
    monkeypatch.setattr(mdb, "access_drivers", lambda: ["Microsoft Access Driver (*.mdb)"])
    monkeypatch.setattr(pyodbc, "connect", lambda *a, **kw: (calls.append((a, kw)) or connection))
    with mdb.connect(tmp_path / "test.mdb"):
        pass
    assert "READONLY=1" in calls[0][0][0]
    assert calls[-1] == "closed"


def test_mdb_table_rejects_sql_injection_before_execute(monkeypatch, tmp_path):
    @contextmanager
    def fake_connect(path):
        yield object()
    monkeypatch.setattr(mdb, "connect", fake_connect)
    monkeypatch.setattr(mdb, "tables", lambda conn: ["CDODefinition"])
    with pytest.raises(ValueError, match="表名"):
        mdb.read_table(tmp_path / "test.mdb", "CDODefinition]; DROP TABLE X", 10)


def test_mdb_catalog_refuses_unverified_schema(monkeypatch):
    monkeypatch.setattr(catalog, "tables", lambda conn: ["OtherTable"])
    with pytest.raises(ValueError, match="已验证的 Designer 表"):
        catalog.check_schema(object())


@pytest.mark.parametrize("offset,limit", [(-1, 20), (0, 101), (True, 20)])
def test_mdb_catalog_rejects_unbounded_pagination(offset, limit):
    with pytest.raises(ValueError):
        catalog.bounds(offset, limit)


def test_official_export_uses_input_copies_and_exact_documented_args(metadata, monkeypatch):
    base, modified = metadata.parent / "base.mdb", metadata.parent / "modified.mdb"
    base.write_bytes(b"base")
    modified.write_bytes(b"modified")
    exe = metadata.parent / "MetadataExport.exe"
    exe.write_bytes(b"placeholder")
    monkeypatch.setattr(config, "DESIGNER_METADATA_EXPORT_EXE", str(exe))

    def fake_run(args, **kwargs):
        assert args[1:3] == ["-a", "-b"]
        assert "shell" not in kwargs
        a, b = Path(args[3]), Path(args[5])
        assert a != base and b != modified
        assert a.read_bytes() == b"base" and b.read_bytes() == b"modified"
        Path(args[7]).write_bytes(FIXTURE.read_bytes())
        Path(args[9]).write_text("report", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = run(tools.export_designer_metadata_diff(str(base), str(modified)))
    assert result["status"] == "exported"
    assert base.read_bytes() == b"base" and modified.read_bytes() == b"modified"


def test_export_without_config_fails_before_creating_artifacts(metadata, monkeypatch):
    base, modified = metadata.parent / "base.mdb", metadata.parent / "modified.mdb"
    base.write_bytes(b"base")
    modified.write_bytes(b"modified")
    monkeypatch.setattr(config, "DESIGNER_METADATA_EXPORT_EXE", "")
    with pytest.raises(ValueError, match="尚未配置"):
        run(tools.export_designer_metadata_diff(str(base), str(modified)))
    assert not (metadata.parent / "artifacts").exists()
