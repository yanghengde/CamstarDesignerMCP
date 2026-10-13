"""Resolve physical columns with the installed, vetted Camstar Update DB engine."""
import json
import os
from pathlib import Path
import re
import subprocess

import config
from designer import vendor
from designer.files import artifact_dir, source_path

# Internal API: accept only an installation actually checked against vendor DDL.
# New binaries require adapter acceptance, never a guessed default-length fallback.
SUPPORTED_ENGINES = {
    '3784cba5c3cd3de154738ca6bbfe7b93a10cc358129302d540fe6581ef10a10c': '10.6.0.0',
}
STRING_TYPES = {'char', 'nchar', 'varchar', 'nvarchar'}


def requested_columns(root):
    result = []
    for table in root.findall('Import/DBTableDefinitions/DBTableDefinition'):
        if table.get('Action') == 'Delete':
            continue
        names = [column.get('Name', '') for column in table.findall('Columns/DBColumnDefinition')
                 if column.get('Action') != 'Delete']
        if names:
            name = table.get('Name', '')
            # Vendor LoadFromMDB interpolates its table name into an Access query.
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name):
                raise ValueError('厂商列核验不支持该表名：' + name)
            result.append({'name': name, 'columns': names})
    return result


def parse_definition(column):
    """Parse generated SQL; this describes syntax, not Camstar mapping rules."""
    name = column['name']
    clause = column['sql_definition']
    if not clause.startswith(name + ' '):
        raise ValueError('厂商列定义名称不匹配：' + name)
    match = re.match(r'([a-z]+)(?:\((max|\d+)(?:\s*,\s*(\d+))?\))?(?=\s|$)',
                     clause[len(name) + 1:], re.IGNORECASE)
    if not match:
        raise ValueError('无法解析厂商 SQL 列定义：' + name)
    sql_type, size, scale = match.groups()
    sql_type = sql_type.casefold()
    expected = {**column, 'expected_type': sql_type, 'expected_nullable': column['nullable']}
    if sql_type in STRING_TYPES | {'binary', 'varbinary'}:
        if size is None or scale is not None:
            raise ValueError('厂商字符串或二进制列缺少有效长度：' + name)
        expected['expected_length'] = -1 if size.casefold() == 'max' else int(size)
        if expected['expected_length'] == 0:
            raise ValueError('厂商生成了零长度物理列：' + name)
    elif sql_type in {'decimal', 'numeric'}:
        if size is None or not size.isdecimal() or scale is None:
            raise ValueError('厂商数值列缺少精度或小数位：' + name)
        expected.update(expected_numeric_precision=int(size), expected_numeric_scale=int(scale))
    elif sql_type not in {'bit', 'int', 'float', 'datetime'} or size is not None:
        raise ValueError('尚未验收的厂商 SQL 类型：' + sql_type)
    return expected


def resolve(compiled_mdb, root):
    tables = requested_columns(root)
    if not tables:
        return {'method': 'no_changed_physical_columns', 'columns': []}
    mdb = source_path(str(compiled_mdb), '.mdb')
    dll = vendor.assembly().parent / 'CIMS.DBUpdate.dll'
    if not dll.is_file():
        raise ValueError('缺少厂商列定义生成组件：CIMS.DBUpdate.dll')
    dll_hash = vendor.digest(dll)
    if dll_hash not in SUPPORTED_ENGINES:
        raise ValueError('Camstar 列生成 API 版本尚未验收，停止核验；组件 SHA256：' + dll_hash)
    mdb_hash = vendor.digest(mdb)
    folder = artifact_dir()
    request_file, result_file = folder / 'request.json', folder / 'column_contract.json'
    request_file.write_text(json.dumps({'compiled_mdb': str(mdb), 'tables': tables}), encoding='utf-8')
    shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    args = [str(shell), '-NoProfile', '-NonInteractive', '-File',
            str(Path(__file__).with_name('column_contract_bridge.ps1')),
            '-AssemblyDirectory', str(dll.parent), '-RequestFile', str(request_file), '-ResultFile', str(result_file)]
    with (folder / 'bridge.log').open('wb') as log:
        completed = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=config.DESIGNER_BRIDGE_TIMEOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if completed.returncode or not result_file.is_file():
        raise ValueError('厂商列定义读取失败；日志：' + str(folder / 'bridge.log'))
    payload = json.loads(result_file.read_text(encoding='utf-8-sig'))
    if not payload.get('ok'):
        from designer.publication import redact
        raise ValueError('厂商列定义读取失败：' + redact(payload.get('error', 'unknown'))[:2000])
    if vendor.digest(mdb) != mdb_hash or vendor.digest(dll) != dll_hash:
        raise ValueError('读取列定义期间 MDB 或厂商组件已变化')
    result = payload['result']
    result['columns'] = [parse_definition(column) for column in result['columns']]
    requested = {(table['name'].casefold(), name.casefold()) for table in tables for name in table['columns']}
    returned = [(column['table'].casefold(), column['name'].casefold()) for column in result['columns']]
    if len(returned) != len(set(returned)) or set(returned) != requested:
        raise ValueError('厂商列定义返回不完整或重复，停止核验')
    result.update(compiled_mdb=str(mdb), compiled_sha256=mdb_hash, assembly_sha256=dll_hash,
                  adapter_version=SUPPORTED_ENGINES[dll_hash], result_file=str(result_file))
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def compare(expected, row):
    actual = {'actual_type': row[0], 'actual_length': row[1], 'actual_numeric_precision': row[2],
              'actual_numeric_scale': row[3], 'actual_nullable': row[4] == 'YES'} if row else {}
    pairs = [('expected_type', 'actual_type'), ('expected_nullable', 'actual_nullable'),
             ('expected_length', 'actual_length'), ('expected_numeric_precision', 'actual_numeric_precision'),
             ('expected_numeric_scale', 'actual_numeric_scale')]
    mismatches = {wanted: {'expected': expected[wanted], 'actual': actual.get(observed)}
                  for wanted, observed in pairs if wanted in expected and expected[wanted] != actual.get(observed)}
    return {**expected, **actual, 'passed': bool(row) and not mismatches, 'mismatches': mismatches}
