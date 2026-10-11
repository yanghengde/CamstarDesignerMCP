"""Verify changed metadata rows against SQL, including updates and deletions."""
from pathlib import Path
from decimal import Decimal

from designer import vendor
from designer.files import artifact_dir
from designer.mdb import connect

# Names are from the installed metadata schema, not business object tables.
TABLE_KEYS = {
    'CDODefinition': ('CDODefID',), 'CDOFields': ('FieldID',), 'FieldDefinitions': ('FieldDefID',),
    'CLFDefinition': ('CLFID',), 'CLFFunctions': ('CLFFunctionID',),
    'CLFFunParm': ('CLFFunctionParmValueID',), 'CLFFunParmVal': ('CLFFunctionParmValueID', 'SequenceNumber'),
    'FunParmDef': ('FunctionParmID',), 'FunctionDefinition': ('FunctionID',),
    'QueryDef': ('QueryDefID',), 'QueryText': ('QueryTextID',), 'QueryParms': ('QueryParmID',),
    'DBTableDefinition': ('DBTableID',), 'DBColumns': ('DBColumnID',),
    'DBIndexDefinition': ('DBIndexID',), 'DBIndexEntries': ('DBIndexEntryID',),
    'CDOMapDefinition': ('CDOMapID',), 'CDOFieldMapDefinition': ('CDOFieldMapID',),
    'CLFEventMap': ('CLFEventMapID',), 'Labels': ('LabelID',), 'CDOLabels': ('CDOLabelID',),
}
IGNORED = {'workspacecode', 'inheritmask', 'inheritedid', 'changecount'}


def scalar(value):
    if isinstance(value, Decimal): return int(value) if value == value.to_integral_value() else float(value)
    return value


def read_metadata(path):
    result = {}
    with connect(Path(path)) as conn:
        cursor = conn.cursor()
        for table in TABLE_KEYS:
            cursor.execute(f'SELECT * FROM [{table}]')
            columns = [item[0].casefold() for item in cursor.description]
            result[table] = [dict(zip(columns, (scalar(value) for value in row))) for row in cursor.fetchall()]
    return result


def compiled_metadata(path):
    import shutil
    folder = artifact_dir()
    before = vendor.digest(Path(path))
    private = folder / 'input.mdb'
    shutil.copyfile(path, private)
    if vendor.digest(private) != before: raise ValueError('验收复制期间设计文件发生变化')
    result = vendor.run(folder, {'mode': 'compile', 'mdb': str(private)})
    if vendor.digest(Path(path)) != before: raise ValueError('验收编译期间设计文件发生变化')
    return read_metadata(result['compiled_mdb'])


def changes(before, after):
    result = []
    for table, keys in TABLE_KEYS.items():
        key_names = tuple(key.casefold() for key in keys)
        def index(rows):
            found = {}
            for row in rows:
                identity = tuple(row[name] for name in key_names)
                if identity in found: raise ValueError(f'编译后的 {table} 有重复定义：{identity}')
                found[identity] = row
            return found
        old, new = index(before.get(table, [])), index(after.get(table, []))
        for identity in sorted(set(old) | set(new), key=repr):
            prior, desired = old.get(identity), new.get(identity)
            if desired is None:
                result.append({'table': table, 'keys': dict(zip(keys, identity)), 'deleted': True, 'expected': {}})
            elif prior is None:
                values = {key: value for key, value in desired.items() if key not in IGNORED}
                result.append({'table': table, 'keys': dict(zip(keys, identity)), 'deleted': False, 'expected': values})
            else:
                values = {key: value for key, value in desired.items() if key not in IGNORED and prior.get(key) != value}
                if values: result.append({'table': table, 'keys': dict(zip(keys, identity)), 'deleted': False, 'expected': values})
    return result


def verify_changes(cursor, schema, planned):
    from designer.publication import sql_identifier
    available = {row[0].casefold(): row[0] for row in cursor.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=? AND TABLE_TYPE='BASE TABLE'", schema).fetchall()}
    checks = []
    for item in planned:
        table = available.get(item['table'].casefold())
        check = {'kind': 'metadata', 'table': item['table'], 'keys': item['keys'], 'deleted': item['deleted']}
        if not table:
            checks.append({**check, 'passed': False, 'reason': '目标缺少元数据表'}); continue
        where = ' AND '.join(f'{sql_identifier(key)}=?' for key in item['keys'])
        cursor.execute(f'SELECT * FROM {sql_identifier(schema)}.{sql_identifier(table)} WHERE {where}', *item['keys'].values())
        columns = [column[0].casefold() for column in cursor.description]
        rows = cursor.fetchall()
        if item['deleted']:
            checks.append({**check, 'passed': not rows, 'remaining': len(rows)}); continue
        if len(rows) != 1:
            checks.append({**check, 'passed': False, 'reason': '定义不存在或不唯一'}); continue
        actual = dict(zip(columns, (scalar(value) for value in rows[0])))
        def equal(expected, observed):
            return expected == observed or expected in (None, '') and observed in (None, '')
        mismatches = {key: {'expected': value, 'actual': actual.get(key)} for key, value in item['expected'].items()
                      if key not in actual or not equal(value, actual[key])}
        checks.append({**check, 'passed': not mismatches, 'mismatches': mismatches})
    return checks


def verify_manifest(manifest, cursor, schema):
    base = compiled_metadata(manifest['source_file'])
    from designer.files import source_path
    # The immutable manifest snapshot, not a later working version.
    current = source_path(manifest['_modified_file'], '.mdb')
    desired = compiled_metadata(current)
    return verify_changes(cursor, schema, changes(base, desired))
