"""Explicit SQL TLS settings apply to every Designer connection."""
from types import SimpleNamespace

import pytest
import pyodbc

import config
from designer.publication import target_connection


@pytest.mark.parametrize('encrypt,trust', [(True, False), (True, True), (False, False)])
@pytest.mark.parametrize('update', [False, True])
def test_sql_tls_settings_and_credentials(monkeypatch, encrypt, trust, update):
    for name, value in {
        'DESIGNER_DB_SERVER': 'localhost', 'DESIGNER_DB_NAME': 'TestDB',
        'DESIGNER_DB_USER': 'reader', 'DESIGNER_DB_PASSWORD': 'read;secret}',
        'DESIGNER_UPDATE_DB_USER': 'updater', 'DESIGNER_UPDATE_DB_PASSWORD': 'update;secret}',
        'DESIGNER_DB_ENCRYPT': encrypt, 'DESIGNER_DB_TRUST_SERVER_CERTIFICATE': trust,
    }.items():
        monkeypatch.setattr(config, name, value)
    closed = []
    connection = SimpleNamespace(close=lambda: closed.append(True))
    calls = []
    monkeypatch.setattr(pyodbc, 'drivers', lambda: ['SQL Server', 'ODBC Driver 18 for SQL Server'])
    monkeypatch.setattr(pyodbc, 'connect', lambda value, **kwargs: calls.append((value, kwargs)) or connection)
    with target_connection('msdb', update=update) as actual:
        assert actual is connection
        assert not closed
    value, options = calls[0]
    assert 'DRIVER={ODBC Driver 18 for SQL Server}' in value
    assert 'SERVER={localhost};DATABASE={msdb}' in value
    assert f"Encrypt={{{'yes' if encrypt else 'no'}}}" in value
    assert f"TrustServerCertificate={{{'yes' if trust else 'no'}}}" in value
    assert ('UID={updater};PWD={update;secret}}}' if update else 'UID={reader};PWD={read;secret}}}') in value
    assert options == {'timeout': 8, 'autocommit': True}
    assert closed == [True]
