"""Designer design contracts, source drift, and actual vendor round-trip checks."""
import asyncio
import json
import os
from pathlib import Path

import pytest
import config
from designer import vendor
from designer.publication import odbc_value, sql_identifier, redact
from designer.metadata import definition_index
import xml.etree.ElementTree as ET
from tools import designer_design
from tools.designer import check_designer_package


@pytest.mark.parametrize("operations", [
    [], [{"action":"unknown"}],
    [{"action":"create_cdo","name":"ExTest"}],
    [{"action":"create_field_type","name":"ExText","data_type":"String","max_length":True}],
    [{"action":"create_field_type","name":"ExText","data_type":"String","max_length":0}],
    [{"action":"add_field","name":"ExText","owner":"Product","field_type":"Description","persistent":"false"}],
    [{"action":"patch","kind":"cdo","name":"Product","changes":{"Description":"changed"},"expected":{}}],
])
def test_invalid_design_is_rejected_before_backend(operations):
    with pytest.raises(ValueError):
        vendor.validate_operations(operations)


def test_source_drift_rejected_before_copy_or_backend(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"DESIGNER_ROOT",str(tmp_path))
    (tmp_path/"source.mdb").write_bytes(b"new data")
    with pytest.raises(ValueError,match="SHA256"):
        vendor.design("source.mdb","0"*64,"200",[{"action":"create_cdo","name":"ExTest","parent":"Product"}])
    assert not (tmp_path/"artifacts").exists()


def test_cdo_workflow_uses_private_string_type_and_persistent_field(monkeypatch):
    captured={}
    def fake_design(mdb,digest,workspace,operations):
        captured["operations"]=operations
        return {"status":"saved_to_test_copy_and_exported"}
    monkeypatch.setattr(vendor,"design",fake_design)
    asyncio.run(designer_design.generate_designer_cdo_package("source.mdb","digest","ExProduct","Product",
                [{"name":"ExDescription","data_type":"String","max_length":200}]))
    assert captured["operations"]==[
        {"action":"create_cdo","name":"ExProduct","parent":"Product","description":""},
        {"action":"create_field_type","name":"ExDescription200","data_type":"String","max_length":200},
        {"action":"add_field","name":"ExDescription","owner":"ExProduct","field_type":"ExDescription200","persistent":True,"is_list":False,"description":""},
    ]


def test_existing_type_cannot_silently_change_every_reference():
    with pytest.raises(ValueError,match="现有field_type"):
        asyncio.run(designer_design.generate_designer_cdo_package("unused","unused","ExProduct","Product",
                    [{"name":"ExDescription","field_type":"Description","max_length":200}]))


def test_odbc_configuration_escapes_delimiters():
    assert odbc_value("a};DATABASE=other") == "{a}};DATABASE=other}"
    with pytest.raises(ValueError):
        odbc_value("bad\nvalue")


def test_official_clf_before_after_lists_allow_repeated_function_calls():
    xml=ET.fromstring('''<InSiteMetaData><Import><CLFDefinition Name="Example"><CLFFunctions>
    <BaseCLFFunctions><CLFFunction Name="Call"><Attributes><Sequence>1</Sequence></Attributes></CLFFunction></BaseCLFFunctions>
    <NewCLFFunctions><CLFFunction Name="Call"><Attributes><Sequence>1</Sequence></Attributes></CLFFunction>
    <CLFFunction Name="Call"><Attributes><Sequence>2</Sequence></Attributes></CLFFunction></NewCLFFunctions>
    </CLFFunctions></CLFDefinition></Import></InSiteMetaData>''')
    assert len(definition_index(xml))==4
    duplicate=xml.find('Import/CLFDefinition/CLFFunctions/NewCLFFunctions/CLFFunction')
    from copy import deepcopy
    xml.find('Import/CLFDefinition/CLFFunctions/NewCLFFunctions').append(deepcopy(duplicate))
    with pytest.raises(ValueError,match="重复"):
        definition_index(xml)


def test_both_database_credentials_are_redacted_without_empty_replacement(monkeypatch):
    monkeypatch.setattr(config,"DESIGNER_DB_PASSWORD","admin-secret")
    monkeypatch.setattr(config,"DESIGNER_UPDATE_DB_PASSWORD","update-secret")
    assert redact("admin-secret update-secret") == "[REDACTED] [REDACTED]"
    monkeypatch.setattr(config,"DESIGNER_DB_PASSWORD","")
    monkeypatch.setattr(config,"DESIGNER_UPDATE_DB_PASSWORD","")
    assert redact("ordinary error") == "ordinary error"


def test_sql_identifier_escapes_delimiters_and_rejects_controls():
    assert sql_identifier("a]b")=="[a]]b]"
    with pytest.raises(ValueError): sql_identifier("bad\x00name")


def test_publish_refuses_unconfirmed_target_before_any_backend(monkeypatch):
    from designer.publication import publish_database
    monkeypatch.setattr(config,"DESIGNER_TEST_TARGET_CONFIRMED",False)
    with pytest.raises(ValueError,match="目标未确认"):
        publish_database("unused","unused","unused","unused")


def test_service_generation_rejects_changed_input_without_remote_calls(tmp_path,monkeypatch):
    from designer.services import generate_wcf
    monkeypatch.setattr(config,"DESIGNER_ROOT",str(tmp_path))
    (tmp_path/"compiled.mdb").write_bytes(b"changed")
    with pytest.raises(ValueError,match="SHA256"):
        generate_wcf("compiled.mdb","0"*64,["ExProduct"])
    assert not (tmp_path/"artifacts").exists()


def test_restore_rejects_wrong_receipt_hash_before_connection(tmp_path,monkeypatch):
    from designer.publication import restore_test_backup
    monkeypatch.setattr(config,"DESIGNER_ROOT",str(tmp_path))
    monkeypatch.setattr(config,"DESIGNER_TEST_TARGET_CONFIRMED",True)
    (tmp_path/"receipt.json").write_text('{}')
    with pytest.raises(ValueError,match="SHA256"):
        restore_test_backup("receipt.json","0"*64)


def test_restore_baseline_requires_matching_database_and_intact_mdb(tmp_path,monkeypatch):
    from designer.publication import reconcile_restored_baseline
    monkeypatch.setattr(config,"DESIGNER_ROOT",str(tmp_path))
    monkeypatch.setattr(config,"DESIGNER_DB_SERVER","test-host")
    monkeypatch.setattr(config,"DESIGNER_DB_NAME","test-db")
    mdb=tmp_path/"saved.mdb";mdb.write_bytes(b"saved design")
    baseline={"target":{"server":"test-host","database":"test-db"},"mdb_file":str(mdb),
              "sha256":vendor.digest(mdb),"metadata_fingerprint":"before"}
    assert reconcile_restored_baseline({"published_baseline":baseline},"before")["status"]=="verified"
    assert reconcile_restored_baseline({"published_baseline":baseline},"after")["status"]=="requires_baseline_reconciliation"
    mdb.write_bytes(b"changed")
    assert reconcile_restored_baseline({"published_baseline":baseline},"before")["status"]=="requires_baseline_reconciliation"
    assert reconcile_restored_baseline({},"before")["status"]=="requires_baseline_reconciliation"


def test_installation_inspection_rejects_missing_credentials_before_remote_call(tmp_path,monkeypatch):
    from designer.installation import inspect_installation
    monkeypatch.setattr(config,"DESIGNER_ROOT",str(tmp_path))
    monkeypatch.setattr(config,"DESIGNER_WINDOWS_PASSWORD","")
    with pytest.raises(ValueError,match="Windows凭据"):
        inspect_installation()
    assert not (tmp_path/"artifacts").exists()


def test_installation_inspection_rejects_escaping_server_path(tmp_path,monkeypatch):
    from designer.installation import inspect_installation
    monkeypatch.setattr(config,"DESIGNER_ROOT",str(tmp_path))
    values={"DESIGNER_DB_SERVER":"test-host","DESIGNER_SERVER_SHARE":r"\\test-host\C",
            "DESIGNER_WINDOWS_USER":"test-user","DESIGNER_WINDOWS_PASSWORD":"test-secret",
            "DESIGNER_UI_EXE":r"C:\tools\..\Designer.exe","DESIGNER_SERVER_IMPORT_EXE":r"C:\tools\Import.exe"}
    for key,value in values.items():monkeypatch.setattr(config,key,value)
    with pytest.raises(ValueError,match="绝对路径"):
        inspect_installation()
    assert not (tmp_path/"artifacts").exists()


@pytest.mark.skipif(not os.getenv("DESIGNER_TEST_VENDOR"),reason="set DESIGNER_TEST_VENDOR=1 for actual vendor bridge")
def test_real_cdo_field_type_column_and_same_batch_patch(monkeypatch):
    from designer.mdb import connect
    from designer.catalog import rows
    source=Path(os.environ["DESIGNER_TEST_MDB"]).resolve()
    before=vendor.digest(source)
    assert not str(source).startswith("\\\\")
    ops=[{"action":"create_cdo","name":"ExDesignerTest","parent":"Product"},
         {"action":"create_field_type","name":"ExDesignerTestText","data_type":"String","max_length":200},
         {"action":"add_field","name":"ExDesignerText","owner":"ExDesignerTest","field_type":"ExDesignerTestText","persistent":True},
         {"action":"patch","kind":"field_type","name":"ExDesignerTestText","changes":{"Description":"200 characters"},"expected":{"Description":""}}]
    try:
        result=vendor.design(str(source),before,"",ops)
        assert result["status"]=="saved_to_test_copy_and_exported"
        assert result["execution"]["reloaded_definitions"]
        with connect(Path(result["files"]["modified.mdb"])) as c:
            field=rows(c.cursor().execute("SELECT * FROM CDOFields WHERE FieldName=?","ExDesignerText"))[0]
            column=rows(c.cursor().execute("SELECT * FROM DBColumns WHERE DBColumnID=?",field["DBColumnID"]))[0]
            field_type=rows(c.cursor().execute("SELECT * FROM FieldDefinitions WHERE FieldDefName=?","ExDesignerTestText"))[0]
            assert not field["IsNonpersistent"]
            assert column["PrecisionValue"]==field_type["PrecisionValue"]==200
            assert field_type["Description"]=="200 characters"
        assert asyncio.run(check_designer_package(result["files"]["manifest.json"]))["intact"]
        from designer.metadata import load_xml
        xml,_=load_xml(Path(result["files"]["changes.xml"]))
        assert xml.findtext("Import/FieldDefinitions/FieldDefinition/Attributes/Precision")=="200"
        assert xml.findtext("Import/CDODefinitions/CDODefinition/Attributes/ParentCDO/Name")=="Product"
    finally:
        assert vendor.digest(source)==before
