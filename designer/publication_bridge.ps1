param([string]$AssemblyDirectory,[string]$RequestFile,[string]$ResultFile)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskReferences=@('System.Data','System.Core','System.ServiceModel','System.Web.Extensions')
foreach($taskName in @('Camstar.Data.dll','OECAdmin.dll','CIMS.DBUpdate.dll')) {
 $taskPath=Join-Path $AssemblyDirectory $taskName
 [Reflection.Assembly]::LoadFrom($taskPath) | Out-Null
 $taskReferences+=$taskPath
}
Add-Type -Path (Join-Path $PSScriptRoot 'PublicationBridge.cs') -ReferencedAssemblies $taskReferences
[DesignerPublicationBridge]::Run($RequestFile,$ResultFile)
