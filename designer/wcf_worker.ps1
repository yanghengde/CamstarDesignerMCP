param([string]$RequestFile)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$taskRequest=Get-Content -LiteralPath $RequestFile -Raw | ConvertFrom-Json
$taskDllFolder=$taskRequest.generator_directory
Set-Location -LiteralPath $taskDllFolder
$env:OPCORE_SITEINFO_PATH=$taskRequest.siteinfo_mdb
$taskReferences=@('System.Core','System.Data','System.Configuration','System.ServiceModel','System.Web.Extensions')
foreach($taskName in @('Camstar.WCFClientBase.dll','Camstar.WCFServiceBase.dll','Camstar.Exceptions.dll','Camstar.Utility.dll','Camstar.WCFGenerator.dll','Camstar.Util.dll')) {
 $taskFile=Join-Path $taskDllFolder $taskName
 [Reflection.Assembly]::LoadFrom($taskFile) | Out-Null
 $taskReferences+=$taskFile
}
$taskExe=Join-Path $PSScriptRoot 'WcfWorker.exe'
Add-Type -Path (Join-Path $PSScriptRoot 'WcfWorker.cs') -ReferencedAssemblies $taskReferences -OutputAssembly $taskExe -OutputType ConsoleApplication
# A private executable config scopes vendor LogLevel to this worker. Installed
# registry and machine-wide application settings remain unchanged.
'<configuration><appSettings><add key="LogLevel" value="0" /></appSettings><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8" /></startup></configuration>' | Set-Content -LiteralPath ($taskExe+'.config') -Encoding UTF8
foreach($taskName in @('Camstar.WCFClientBase.dll','Camstar.WCFServiceBase.dll','Camstar.Exceptions.dll','Camstar.Utility.dll','Camstar.WCFGenerator.dll','Camstar.Util.dll')) {
 Copy-Item -LiteralPath (Join-Path $taskDllFolder $taskName) -Destination (Join-Path $PSScriptRoot $taskName)
}
foreach($taskName in @('app.config','Global.asax','ApplicationInsights.config')) {
 Copy-Item -LiteralPath (Join-Path $taskDllFolder $taskName) -Destination (Join-Path $PSScriptRoot $taskName)
}
Add-Type -Path (Join-Path $PSScriptRoot 'WorkerProcess.cs')
$taskSeconds=1800
if($taskRequest.timeout_seconds) {$taskSeconds=[int]$taskRequest.timeout_seconds}
$taskExit=[DesignerWorkerProcess]::Run($taskExe,$RequestFile,(Join-Path $PSScriptRoot 'result.json'),$taskSeconds)
exit $taskExit
