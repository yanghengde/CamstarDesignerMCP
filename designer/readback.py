"""Review saved MDB changes with the vendor comparer, without compiling."""
from pathlib import Path
import shutil
import time
from datetime import datetime, timezone

from designer import read_cache, review, vendor, working
from designer.files import artifact_dir, source_path
from designer.metadata import load_xml


KINDS = {'CDODefinition': '对象', 'CDOFieldDefinition': '字段', 'FieldDefinition': '字段类型',
         'CLFDefinition': 'CLF', 'CLFFunction': '函数调用', 'QueryDefinition': '查询',
         'DBTableDefinition': '数据表', 'DBColumnDefinition': '数据库列',
         'DBIndexDefinition': '索引', 'CDOMapDefinition': '对象映射',
         'CDOFieldMapDefinition': '字段映射', 'LabelDefinition': '标签'}
PROPERTY_LABELS = {'FieldDef': '字段类型', 'IsListType': '列表字段', 'FieldDescription': '说明',
                   'AllowClientToRead': '允许客户端读取', 'AllowClientToUpdate': '允许客户端修改',
                   'DBColumn': '存储列', 'DefaultValue': '默认值', 'Required': '必填规则',
                   'Precision': '长度/精度', 'PrecisionValue': '长度/精度', 'Scale': '小数位',
                   'ParentCDO': '父对象', 'CDOFieldName': '字段名', 'SQLType': '数据库类型'}


def _value(node):
    """Preserve reference names, type attributes, nested lists and empty values."""
    if node is None:
        return None
    text = node.text or ''
    if not len(node) and not node.attrib:
        return text
    result = {'attributes': dict(node.attrib)} if node.attrib else {}
    if text.strip():
        result['value'] = text
    if len(node):
        result['children'] = [{'property': child.tag, 'value': _value(child)} for child in node]
    return result


def diff_rows(root):
    """Parse the actual SDK diff; empty Import containers are not changes."""
    result = []

    def visit(node, owner='', path=''):
        name, action = node.get('Name'), node.get('Action', 'Import')
        context = f'{path}/{node.tag}' + (f':{name}' if name is not None else '')
        if name is not None:
            properties = list(node.findall('Attributes/*'))
            # Query text, CLF call lists and other payloads can live outside Attributes.
            properties += [child for child in node if child.tag != 'Attributes'
                           and not any('Name' in item.attrib for item in child.iter())]
            values = []
            for prop in properties:
                old, new = prop.find('ExpectedValue'), prop.find('NewValue')
                values.append({'property': prop.tag, 'label': PROPERTY_LABELS.get(prop.tag, prop.tag),
                               'before_known': action in {'Create', 'Delete'} or old is not None,
                               'before': _value(prop if action == 'Delete' and old is None else old),
                               'after': None if action == 'Delete' else _value(new if new is not None else prop)})
            if node.text and node.text.strip():
                values.append({'property': '#text', 'label': '内容', 'before_known': action in {'Create', 'Delete'},
                               'before': node.text if action == 'Delete' else None,
                               'after': None if action == 'Delete' else node.text})
            if values or action in {'Create', 'Delete', 'Rename'}:
                result.append({'kind': node.tag, 'kind_label': KINDS.get(node.tag, node.tag),
                               'owner': owner, 'name': name, 'path': context,
                               'action': action, 'label': {'Create': '新增', 'Delete': '删除', 'Rename': '重命名'}.get(action, '修改'),
                               'properties': values})
            if node.tag in {'CDODefinition', 'DBTableDefinition', 'CDOMapDefinition', 'CLFDefinition', 'QueryDefinition'}:
                owner = name
        for child in node:
            if child.tag != 'Attributes':
                visit(child, owner, context)

    visit(root.find('Import'))
    return result


def read_changes(package, manifests):
    """Compare this cycle's baseline to the current saved work file on copies."""
    started = time.perf_counter()
    path, manifest = review.package(package['manifest_file'])
    working.check_current(manifest)
    source = source_path(package['mdb_file'], '.mdb')
    checksum = package['sha256']
    if manifest['artifacts']['modified.mdb'] != checksum or vendor.digest(source) != checksum:
        raise ValueError('工作 MDB 已变化，请刷新后重新读取')
    if not manifests or Path(manifests[0]).resolve() != path:
        raise ValueError('本轮设计来源不完整，请刷新后重新读取')
    verified = [review.package(item)[1] for item in manifests]
    baseline = source_path(verified[-1]['source_file'], '.mdb')
    base_hash = verified[-1]['source_sha256']

    def unchanged():
        if vendor.digest(source) != checksum or vendor.digest(baseline) != base_hash:
            raise ValueError('MDB 在读取期间发生变化，请刷新后重新读取')

    unchanged()
    # Reading a stale work copy must never claim to reflect saved Designer edits.
    # Do this on cache hits too; unsaved edits are outside the saved-file contract.
    def check_designer():
        if package.get('review') and not review.sync_file(str(path)).get('unchanged'):
            raise ValueError('Designer 已保存新的改动，请先点击“同步 Designer 修改”，再读取 MDB 变更')
    check_designer()
    key = read_cache.identity(source, checksum, vendor.assembly(),
                              {'mode': 'design_readback_v1', 'reader_sha256': vendor.digest(Path(__file__)),
                               'baseline': str(baseline), 'baseline_sha256': base_hash})
    result = read_cache.load(key)
    cached = result is not None
    if result is None:
        folder = artifact_dir()
        before, after = folder / 'before.mdb', folder / 'after.mdb'
        try:
            shutil.copyfile(baseline, before)
            shutil.copyfile(source, after)
            if vendor.digest(before) != base_hash or vendor.digest(after) != checksum:
                raise ValueError('MDB 在复制期间发生变化，请重试')
            exported = vendor.export(folder, before, after)
            root, xml_hash = load_xml(Path(exported['xml_file']))
            unchanged()
            check_designer()
            result = {'format': 'designer_mdb_changes_v1', 'status': 'mdb_changes_read',
                      'source_sha256': checksum, 'baseline_sha256': base_hash,
                      'rows': diff_rows(root), 'comparison_engine': exported['comparison_engine'],
                      'xml_file': exported['xml_file'], 'xml_sha256': xml_hash,
                      'read_at': datetime.now(timezone.utc).isoformat(),
                      'comparison_seconds': round(time.perf_counter() - started, 3),
                      'read_only': True, 'compiled': False}
            read_cache.save(key, result)
        finally:
            before.unlink(missing_ok=True)
            after.unlink(missing_ok=True)
    unchanged()
    from designer.operations import manifest_operations
    associated = {(op.get('owner', '').casefold(), op.get('name', '').casefold())
                  for item in verified for op in manifest_operations(item) if op.get('automatic_maintenance')}
    rows = [{**row, 'automatic_maintenance': row['kind'] == 'CDOFieldDefinition'
             and (row['owner'].casefold(), row['name'].casefold()) in associated} for row in result['rows']]
    receipt = {**result, 'rows': rows, 'package_id': package['id'], 'source_file': str(source),
               'baseline_file': str(baseline), 'cache_hit': cached,
               'duration_seconds': round(time.perf_counter() - started, 3)}
    return receipt
