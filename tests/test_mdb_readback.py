import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

import config
from designer import readback, review, vendor


XML = '''<InSiteMetaData><Header><Version>1.0</Version></Header><Import>
<CDODefinitions><CDODefinition Name="Product" Action="Import"><Attributes/>
<CDOFieldDefinitions><CDOFieldDefinition Name="ActualField" Action="Create"><Attributes>
<FieldDef Datatype="String">String40</FieldDef><IsListType>False</IsListType>
<DBColumn><Name>ActualField</Name><DBTableName>Product</DBTableName></DBColumn>
</Attributes></CDOFieldDefinition></CDOFieldDefinitions></CDODefinition></CDODefinitions>
</Import></InSiteMetaData>'''


@pytest.fixture
def package(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DESIGNER_ROOT', str(tmp_path))
    folder = tmp_path / 'artifacts' / ('a' * 32)
    folder.mkdir(parents=True)
    for name, content in [('baseline.mdb', b'before'), ('modified.mdb', b'after'), ('changes.xml', XML.encode())]:
        (folder / name).write_bytes(content)
    manifest = {'status': 'saved_to_test_copy_and_exported', 'workspace_applied_to_mdb': True,
                'workspace': '200', 'source_file': str(folder / 'baseline.mdb'),
                'source_sha256': vendor.digest(folder / 'baseline.mdb'),
                'operations': [{'action': 'add_field', 'owner': 'Product', 'name': 'RequestedField'}],
                'artifacts': {name: vendor.digest(folder / name) for name in ('modified.mdb', 'changes.xml')}}
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    dll = tmp_path / 'metadata.dll'
    dll.write_bytes(b'fake-sdk')
    monkeypatch.setattr(vendor, 'assembly', lambda: dll)
    return review.summary(str(folder / 'manifest.json'))


def fake_export(monkeypatch, callback=None):
    calls = []
    def export(folder, before, after):
        calls.append((before.read_bytes(), after.read_bytes()))
        if callback:
            callback()
        output = folder / 'actual.xml'
        output.write_text(XML, encoding='utf-8')
        return {'xml_file': str(output), 'comparison_engine': 'test-vendor-comparer'}
    monkeypatch.setattr(vendor, 'export', export)
    return calls


def test_actual_mdb_diff_is_distinct_from_requested_operations_and_cached(package, monkeypatch):
    calls = fake_export(monkeypatch)
    manifests = [package['manifest_file']]
    before = Path(package['mdb_file']).read_bytes()
    first = readback.read_changes(package, manifests)
    second = readback.read_changes(package, manifests)
    assert len(calls) == 1
    assert calls[0] == (b'before', b'after')
    assert [row['name'] for row in first['rows']] == ['ActualField']
    assert first['cache_hit'] is False and second['cache_hit'] is True
    assert first['read_only'] and not first['compiled']
    assert Path(package['mdb_file']).read_bytes() == before
    assert not list(Path(config.DESIGNER_ROOT).glob('artifacts/*/before.mdb'))
    assert not list(Path(config.DESIGNER_ROOT).glob('artifacts/*/after.mdb'))


def test_stale_or_changing_source_cannot_return_or_cache_success(package, monkeypatch):
    source = Path(package['mdb_file'])
    fake_export(monkeypatch, lambda: source.write_bytes(b'external change'))
    with pytest.raises(ValueError, match='读取期间发生变化'):
        readback.read_changes(package, [package['manifest_file']])
    assert not list(Path(config.DESIGNER_ROOT).glob('cache/metadata/*.json'))
    with pytest.raises(ValueError, match='SHA256'):
        readback.read_changes(package, [package['manifest_file']])


def test_cache_does_not_hide_new_saved_designer_changes(package, monkeypatch):
    calls = fake_export(monkeypatch)
    package['review'] = {'server_mdb': 'saved Designer file'}
    checks = []
    def sync(_):
        checks.append(True)
        return {'unchanged': len(checks) <= 2}
    monkeypatch.setattr(review, 'sync_file', sync)
    readback.read_changes(package, [package['manifest_file']])
    with pytest.raises(ValueError, match='同步 Designer 修改'):
        readback.read_changes(package, [package['manifest_file']])
    assert len(checks) == 3 and len(calls) == 1


def test_designer_changes_during_compare_are_rejected_before_cache(package, monkeypatch):
    fake_export(monkeypatch)
    package['review'] = {'server_mdb': 'saved Designer file'}
    checks = []
    def sync(_):
        checks.append(True)
        return {'unchanged': len(checks) == 1}
    monkeypatch.setattr(review, 'sync_file', sync)
    with pytest.raises(ValueError, match='同步 Designer 修改'):
        readback.read_changes(package, [package['manifest_file']])
    assert not list(Path(config.DESIGNER_ROOT).glob('cache/metadata/*.json'))


def test_new_mdb_version_uses_earliest_cycle_baseline_and_invalidates_cache(package, monkeypatch):
    calls = fake_export(monkeypatch)
    readback.read_changes(package, [package['manifest_file']])
    original = Path(package['manifest_file'])
    latest = original.parent.parent / ('b' * 32)
    latest.mkdir()
    manifest = json.loads(original.read_text(encoding='utf-8'))
    (latest / 'baseline.mdb').write_bytes(b'after')
    (latest / 'modified.mdb').write_bytes(b'second change')
    (latest / 'changes.xml').write_text(XML, encoding='utf-8')
    manifest.update(source_file=str(latest / 'baseline.mdb'), source_sha256=vendor.digest(latest / 'baseline.mdb'))
    manifest['artifacts'] = {name: vendor.digest(latest / name) for name in ('modified.mdb', 'changes.xml')}
    (latest / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    current = review.summary(str(latest / 'manifest.json'))
    result = readback.read_changes(current, [current['manifest_file'], str(original)])
    assert result['cache_hit'] is False
    assert calls == [(b'before', b'after'), (b'before', b'second change')]


def test_baseline_is_checked_before_any_vendor_read(package, monkeypatch):
    calls = fake_export(monkeypatch)
    (Path(package['manifest_file']).parent / 'baseline.mdb').write_bytes(b'changed baseline')
    with pytest.raises(ValueError, match='MDB'):
        readback.read_changes(package, [package['manifest_file']])
    assert not calls


def test_diff_preserves_deletes_renames_empty_values_and_unknown_definitions():
    root = ET.fromstring('''<InSiteMetaData><Import><CDODefinition Name="Product" Action="Import"><Attributes/>
      <CDOFieldDefinition Name="Old" Action="Delete"><Attributes><FieldDef>String40</FieldDef></Attributes></CDOFieldDefinition>
      <CDOFieldDefinition Name="Renamed" Action="Rename"><Attributes>
        <FieldDescription><ExpectedValue>old</ExpectedValue><NewValue/></FieldDescription>
        <AllowClientToUpdate><ExpectedValue>False</ExpectedValue><NewValue>True</NewValue></AllowClientToUpdate>
      </Attributes></CDOFieldDefinition>
      <FutureDefinition Name="Unknown">Preserve content<Attributes><Reference><Name>Keep me</Name></Reference></Attributes></FutureDefinition>
    </CDODefinition></Import></InSiteMetaData>''')
    rows = readback.diff_rows(root)
    assert len(rows) == 3  # Empty Import parent does not inflate the change count.
    deleted, renamed, unknown = rows
    assert deleted['action'] == 'Delete'
    assert deleted['properties'][0] == {'property': 'FieldDef', 'label': '字段类型', 'before_known': True,
                                       'before': 'String40', 'after': None}
    assert renamed['properties'][0]['before'] == 'old' and renamed['properties'][0]['after'] == ''
    assert renamed['properties'][1]['before'] == 'False' and renamed['properties'][1]['after'] == 'True'
    assert unknown['properties'][0]['after']['children'][0]['value'] == 'Keep me'
    assert unknown['properties'][1]['after'] == 'Preserve content'


def test_browser_shows_actual_data_as_text_and_keeps_before_after():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    source = (Path(__file__).parents[1] / 'static/progress.html').read_text(encoding='utf-8')
    functions = source.split('        function readbackValue(', 1)[1].split('        function summaryRow(', 1)[0]
    preview_render = 'let readback=' + source.split('            let readback=', 1)[1].split(
        '            if((stage.number===9', 1)[0]
    rows = readback.diff_rows(ET.fromstring(XML))
    rows[0]['name'] = '<img src=x onerror=alert(1)>'
    script = r'''
const assert=require('node:assert/strict');
class Element {
 constructor(){this.children=[];this.dataset={};}
 appendChild(child){this.children.push(child);return child;}
 append(...items){this.children.push(...items);}
 replaceChildren(){this.children=[];}
 createTHead(){return this.appendChild(new Element());}
 createTBody(){return this.appendChild(new Element());}
 insertRow(){return this.appendChild(new Element());}
 insertCell(){return this.appendChild(new Element());}
 set innerHTML(value){throw Error('MDB content must use textContent');}
}
const document={createElement:()=>new Element()}, button=()=>new Element();
function readbackValue(''' + functions + '\nconst rows=' + json.dumps(rows) + r''';
const root=new Element();renderReadback(root,{rows,read_at:'2026-10-21T01:00:00Z',duration_seconds:1.2,source_file:'actual.mdb',baseline_file:'before.mdb',source_sha256:'sha'});
const all=[];function walk(node){if(node.textContent)all.push(node.textContent);for(const child of node.children)walk(child);}walk(root);
assert.ok(all.includes('新增字段 · Product.<img src=x onerror=alert(1)>'));
assert.ok(all.includes('变更前')&&all.includes('变更后'));
assert.ok(all.some(text=>text.includes('String40')));
assert.ok(all.includes('actual.mdb')&&all.includes('before.mdb'));
let preview=null;
document.querySelector=()=>preview;
const actions={after:node=>{preview=node;}},stage={number:6,actions:[{id:'readback'}]};
const state={package:{id:'pkg'},mdb_readback:{rows,read_at:'2026-10-21T01:00:00Z',duration_seconds:1.2}};
function renderPreview(){
''' + preview_render + r'''
}
renderPreview();const first=preview;
const card=preview.children.find(child=>child.className==='mdb-change');
const detail=card.children.find(child=>child.children[0]?.textContent?.startsWith('查看'));
detail.open=true;renderPreview();
assert.equal(preview,first);assert.equal(detail.open,true);assert.ok(card.children.includes(detail));
'''
    result = subprocess.run([node, '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
