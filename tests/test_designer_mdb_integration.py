"""Opt-in real Access adapter checks on a local test snapshot, never the server."""

from hashlib import sha256
import os
from pathlib import Path

import pytest

from designer import catalog, mdb


@pytest.fixture
def snapshot():
    name = os.getenv("DESIGNER_TEST_MDB")
    if not name:
        pytest.skip("set DESIGNER_TEST_MDB to a local InSite MDB test snapshot")
    path = Path(name).resolve()
    if str(path).startswith("\\\\"):
        pytest.fail("integration checks require a local snapshot, not a server share")
    assert path.is_file()
    digest = sha256(path.read_bytes()).hexdigest()
    yield path
    assert sha256(path.read_bytes()).hexdigest() == digest


def test_real_schema_and_raw_cdo_lookup(snapshot):
    schema = mdb.schema(snapshot)
    table_names = {t["name"] for t in schema["tables"]}
    assert {"CDODefinition", "CDOFields", "Workspace"}.issubset(table_names)
    result = catalog.list_cdos(snapshot, "Container", 0, 5)
    assert result["total_versions"] > 0
    assert all("container" in c["CDOName"].casefold() for c in result["cdos"])


def test_real_container_fields_and_type_references(snapshot):
    result = catalog.get_cdo(snapshot, "Container", 0, 5)
    assert result["definitions"]
    assert result["total_field_versions"] >= len(result["fields"]) > 0
    assert result["workspace_resolution"] == "raw_versions_not_merged"
    assert all(f["data_type_name"] for f in result["fields"])
    assert all(f["field_type_versions"] for f in result["fields"])


def test_real_cdo_name_is_a_parameter_not_sql(snapshot):
    with pytest.raises(ValueError, match="找不到 CDO"):
        catalog.get_cdo(snapshot, "Container' OR 1=1 --", 0, 5)
