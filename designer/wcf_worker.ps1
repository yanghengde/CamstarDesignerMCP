param([string]$RequestFile)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8

function New-DesignerWcfWorkerConfig([string]$GeneratorDirectory,[string]$Destination) {
 $taskProfile=Join-Path $GeneratorDirectory 'WCFBuilderLauncher.exe.config'
 if(-not(Test-Path -LiteralPath $taskProfile -PathType Leaf)) {throw 'Missing official WCFBuilderLauncher.exe.config generation profile'}
 [xml]$taskConfig=Get-Content -LiteralPath $taskProfile -Raw
 if(-not $taskConfig.SelectSingleNode('/configuration/WCFGenerator/AllServices/add')) {
  throw 'Official WCF generation profile lacks AllServices exposure rules'
 }
 # The official profile controls inherited method exposure even when all
 # services are generated. Dropping it exposes every inherited method again.
 $taskAppSettings=$taskConfig.SelectSingleNode('/configuration/appSettings')
 if(-not $taskAppSettings) {
  $taskAppSettings=$taskConfig.CreateElement('appSettings')
  $taskConfig.configuration.AppendChild($taskAppSettings) | Out-Null
 }
 $taskLog=$taskAppSettings.SelectSingleNode('add[@key="LogLevel"]')
 if(-not $taskLog) {
  $taskLog=$taskConfig.CreateElement('add');$taskLog.SetAttribute('key','LogLevel')
  $taskAppSettings.AppendChild($taskLog) | Out-Null
 }
 $taskLog.SetAttribute('value','0')
 $taskConfig.Save($Destination)
}

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
# Preserve the installed generator's service/method rules in a private copy;
# scope its log level to this worker without editing installed configuration.
New-DesignerWcfWorkerConfig $taskDllFolder ($taskExe+'.config')
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
