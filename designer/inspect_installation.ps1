param([string]$ResultFile)
$ErrorActionPreference='Stop'
$taskCredential=[pscredential]::new($env:DESIGNER_WINDOWS_USER,(ConvertTo-SecureString $env:DESIGNER_WINDOWS_PASSWORD -AsPlainText -Force))
function Get-DesignerBinary([string]$taskPath) {
 $taskRemote='DesignerInstall:\'+$taskPath.Substring(3)
 $taskExists=Test-Path -LiteralPath $taskRemote -PathType Leaf
 $taskVersion=$null
 if($taskExists) {$taskVersion=(Get-Item -LiteralPath $taskRemote).VersionInfo.FileVersion}
 return @{path=$taskPath;exists=$taskExists;file_version=$taskVersion}
}
try {
 New-PSDrive -Name DesignerInstall -PSProvider FileSystem -Root $env:DESIGNER_SERVER_SHARE -Credential $taskCredential | Out-Null
 $taskUi=Get-DesignerBinary $env:DESIGNER_UI_EXE
 $taskImport=Get-DesignerBinary $env:DESIGNER_SERVER_IMPORT_EXE
 $taskSettings=@{}
 $taskConfig='DesignerInstall:\'+$env:DESIGNER_UI_EXE.Substring(3)+'.Config'
 if(Test-Path -LiteralPath $taskConfig) {
  [xml]$taskXml=Get-Content -LiteralPath $taskConfig -Raw
  foreach($taskNode in $taskXml.configuration.appSettings.add) {
   if($taskNode.key -in @('MDBPath','SiteMDBPath','Username')) {$taskSettings[[string]$taskNode.key]=[string]$taskNode.value}
  }
 }
 $taskFolder=Split-Path $env:DESIGNER_SERVER_IMPORT_EXE -Parent
 $taskComponents=@()
 foreach($taskName in @('InSiteImport.dll','InSite.dll','InSiteCompare.dll','InSiteUtilities.dll')) {
  $taskComponents+=Get-DesignerBinary (Join-Path $taskFolder $taskName)
 }
 @{read_only=$true;server_share=$env:DESIGNER_SERVER_SHARE;designer_ui=$taskUi;xml_import_executable=$taskImport;
   designer_settings=$taskSettings;native_components=$taskComponents;xml_import_verified=$false;
   note='Installed binaries do not establish successful XML import; round-trip acceptance is still required.'} |
   ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ResultFile -Encoding UTF8
} finally {if(Get-PSDrive DesignerInstall -ErrorAction SilentlyContinue) {Remove-PSDrive DesignerInstall}}
