"""Exercise configuration guards without invoking Update DB or writing SQL."""
import json
import os
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows .NET Framework bridge')
ROOT = Path(__file__).resolve().parents[1]


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_probe(tmp_path, body):
    script = tmp_path / 'probe.ps1'
    # Use the production reference list, so a missing dependency fails here.
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$ast=[Management.Automation.Language.Parser]::ParseFile({ps_quote(ROOT / 'designer/publication_bridge.ps1')},[ref]$null,[ref]$null)\n"
        "$assignment=$ast.Find({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$taskReferences' -and $n.Operator -eq 'Equals'},$true)\n"
        "$references=@($assignment.Right.FindAll({param($n) $n -is [Management.Automation.Language.StringConstantExpressionAst]},$true) | ForEach-Object {$_.Value})\n"
        + body,
        encoding='utf-8-sig',
    )
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-File', str(script)],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result.stdout.decode('utf-8-sig').strip()


@pytest.fixture(scope='module')
def guard_results(tmp_path_factory):
    folder = tmp_path_factory.mktemp('runtime-configuration')
    harness = folder / 'Probe.cs'
    harness.write_text('''using System;
using System.Collections.Generic;
using System.Data;

public static class RuntimeConfigurationProbe {
 static DataSet Source() {
  var source = new DataSet();
  foreach(string name in RuntimeConfigurationSnapshot.TableNames) {
   var table = new DataTable(name);
   table.Columns.Add("TName", typeof(string));
   table.Columns.Add("TValue", typeof(string));
   table.Rows.Add("setting", "preserve-me");
   source.Tables.Add(table);
  }
  source.Tables["InsiteSiteInfo"].Rows.Add("LicenseServer", "example:29000");
  source.Tables["InsiteSiteInfo"].Rows.Add("LicenseFileType", "test");
  return source;
 }
 static bool Reject(Action<DataSet> change) {
  var source = Source();
  change(source);
  try { new RuntimeConfigurationSnapshot(source); return false; }
  catch(InvalidOperationException) { return true; }
 }
 public static Dictionary<string,bool> Run() {
  var result = new Dictionary<string,bool>();
  result["missing_table"] = Reject(s => s.Tables.Remove("DBDataSourceNames"));
  result["empty_site_configuration"] = Reject(s => s.Tables["InsiteSiteInfo"].Rows.Clear());
  result["empty_system_configuration"] = Reject(s => s.Tables["SystemInfo"].Rows.Clear());
  result["missing_license_server"] = Reject(s => s.Tables["InsiteSiteInfo"].Rows.RemoveAt(1));
  result["blank_license_file_type"] = Reject(s => s.Tables["InsiteSiteInfo"].Rows[2]["TValue"] = " ");
  var source = Source();
  var snapshot = new RuntimeConfigurationSnapshot(source);
  source.Tables["SystemInfo"].Rows.Clear();
  result["snapshot_is_independent"] = snapshot.RowCounts()["SystemInfo"] == 1;
  var original = Source().Tables["InsiteSiteInfo"];
  var reordered = original.Clone();
  for(int i=original.Rows.Count-1; i>=0; i--) reordered.ImportRow(original.Rows[i]);
  reordered.Columns["TValue"].SetOrdinal(0);
  result["fingerprint_ignores_row_and_column_order"] = RuntimeConfigurationSnapshot.Fingerprint(original) == RuntimeConfigurationSnapshot.Fingerprint(reordered);
  reordered.Rows[0]["TValue"] = "changed";
  result["fingerprint_detects_value_changes"] = RuntimeConfigurationSnapshot.Fingerprint(original) != RuntimeConfigurationSnapshot.Fingerprint(reordered);
  reordered.Rows.RemoveAt(0);
  result["fingerprint_detects_deleted_rows"] = RuntimeConfigurationSnapshot.Fingerprint(original) != RuntimeConfigurationSnapshot.Fingerprint(reordered);
  return result;
 }
}
''', encoding='utf-8')
    output = run_probe(folder,
        f"Add-Type -Path @({ps_quote(ROOT / 'designer/RuntimeConfiguration.cs')},{ps_quote(harness)}) -ReferencedAssemblies $references\n"
        "[RuntimeConfigurationProbe]::Run() | ConvertTo-Json -Compress\n")
    return json.loads(output)


@pytest.mark.parametrize('check', [
    'missing_table', 'empty_site_configuration', 'empty_system_configuration',
    'missing_license_server', 'blank_license_file_type', 'snapshot_is_independent',
    'fingerprint_ignores_row_and_column_order', 'fingerprint_detects_value_changes',
    'fingerprint_detects_deleted_rows',
])
def test_configuration_guard(guard_results, check):
    assert guard_results[check]


def test_publication_bridge_compiles_with_installed_components(tmp_path):
    from designer import publication
    try:
        directory = publication.publication_assembly_directory()
    except ValueError as error:
        pytest.skip(str(error))
    # Compile both real C# files. Never call DesignerPublicationBridge.Run.
    body = ''
    for name in ('Camstar.Data.dll', 'OECAdmin.dll', 'CIMS.DBUpdate.dll'):
        path = ps_quote(directory / name)
        body += f"[Reflection.Assembly]::LoadFrom({path}) | Out-Null\n$references+={path}\n"
    body += (
        f"Add-Type -Path @({ps_quote(ROOT / 'designer/RuntimeConfiguration.cs')},{ps_quote(ROOT / 'designer/PublicationBridge.cs')}) -ReferencedAssemblies $references\n"
        "'compiled_without_running_update'\n"
    )
    assert run_probe(tmp_path, body) == 'compiled_without_running_update'
