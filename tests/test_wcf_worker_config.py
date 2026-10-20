"""Keep the official exposure rules when creating an isolated WCF worker."""
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell configuration helper')
SCRIPT = Path(__file__).resolve().parents[1] / 'designer' / 'wcf_worker.ps1'


def configure(tmp_path):
    def quote(path):
        return "'" + str(path).replace("'", "''") + "'"
    output = tmp_path / 'worker.exe.config'
    probe = tmp_path / 'configure.ps1'
    probe.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile({quote(SCRIPT)},[ref]$null,[ref]$null)\n"
        "$fn=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'New-DesignerWcfWorkerConfig'},$true)\n"
        ". ([scriptblock]::Create($fn.Extent.Text))\n"
        f"New-DesignerWcfWorkerConfig {quote(tmp_path)} {quote(output)}\n",
        encoding='utf-8-sig',
    )
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    run = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-File', str(probe)],
                         capture_output=True, timeout=15)
    return run, output


@pytest.mark.parametrize('app_settings', ['', '<appSettings><add key="LogLevel" value="2" /></appSettings>'])
def test_preserves_native_exposure_rules_without_changing_installation(tmp_path, app_settings):
    profile = tmp_path / 'WCFBuilderLauncher.exe.config'
    profile.write_text('<configuration>' + app_settings + '''<WCFGenerator>
      <AllServices><add Name="Service" ExposedType="BaseAndExposed"><add Name="ProductMaint" ExposedType="Exposed">
      <Methods><add Name="Load" ExposedType="Exposed"/><add Name="InternalMethod" ExposedType="NonExposed"/></Methods>
      </add></add></AllServices><ServicesSettings IsGenerateAll="true"/>
      </WCFGenerator><startup><supportedRuntime version="v4.0"/></startup></configuration>''')
    original = profile.read_bytes()
    run, output = configure(tmp_path)
    assert run.returncode == 0, run.stderr.decode(errors='replace')
    source, copied = ET.fromstring(original), ET.parse(output).getroot()
    assert ET.canonicalize(ET.tostring(source.find('WCFGenerator'), encoding='unicode'), strip_text=True) == ET.canonicalize(
        ET.tostring(copied.find('WCFGenerator'), encoding='unicode'), strip_text=True)
    assert copied.find('appSettings/add[@key="LogLevel"]').get('value') == '0'
    assert copied.find('startup/supportedRuntime').get('version') == 'v4.0'
    assert profile.read_bytes() == original


@pytest.mark.parametrize('content', [None, '<configuration><WCFGenerator><AllServices/></WCFGenerator></configuration>'])
def test_missing_native_rules_fail_before_generation(tmp_path, content):
    if content:
        (tmp_path / 'WCFBuilderLauncher.exe.config').write_text(content)
    run, output = configure(tmp_path)
    assert run.returncode != 0
    assert not output.exists()
