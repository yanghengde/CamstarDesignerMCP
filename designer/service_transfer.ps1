param([string]$LocalFolder,[ValidateSet('stage','fetch','settings')][string]$Mode)
$ErrorActionPreference='Stop'
$taskCredential=[pscredential]::new($env:DESIGNER_WINDOWS_USER,(ConvertTo-SecureString $env:DESIGNER_WINDOWS_PASSWORD -AsPlainText -Force))
try {
 New-PSDrive -Name DesignerTransfer -PSProvider FileSystem -Root $env:DESIGNER_SERVER_SHARE -Credential $taskCredential | Out-Null
 $taskRemote='DesignerTransfer:\Temp\DesignerMCP\'+(Split-Path $LocalFolder -Leaf)
 if($Mode -eq 'settings') {
  $taskConfigPath='DesignerTransfer:\'+$env:DESIGNER_WCF_DIRECTORY.Substring(3)+'\Web.config'
  [xml]$taskConfig=Get-Content -LiteralPath $taskConfigPath -Raw
  $taskConnection=$taskConfig.configuration.connectionStrings.add | Where-Object name -eq 'Server'
  if(-not $taskConnection -or $taskConnection.connectionString -match '(?i)password|pwd=') {throw 'Missing or unsupported Camstar Server connection setting'}
  $taskApplications=@()
  $taskServiceDirectory='DesignerTransfer:\'+$env:DESIGNER_WCF_DIRECTORY.Substring(3)
  $taskSampleServices=@(Get-ChildItem -LiteralPath $taskServiceDirectory -Filter '*Product*.svc' -File | Select-Object -ExpandProperty Name)
  $taskGeneratorAddress=$null
  $taskGeneratorConfig='DesignerTransfer:\'+$env:DESIGNER_SERVER_WCF_GENERATOR.Substring(3)+'\app.config'
  if(Test-Path -LiteralPath $taskGeneratorConfig) {
   [xml]$taskGeneratorXml=Get-Content -LiteralPath $taskGeneratorConfig -Raw
   $taskAddressNode=$taskGeneratorXml.SelectSingleNode("//*[local-name()='Address' or local-name()='address']")
   if($taskAddressNode) {$taskGeneratorAddress=$taskAddressNode.InnerText}
  }
  $taskIisPath='DesignerTransfer:\Windows\System32\inetsrv\config\applicationHost.config'
  $taskIisError=$null
  try {
  if(Test-Path -LiteralPath $taskIisPath) {
   [xml]$taskIis=Get-Content -LiteralPath $taskIisPath -Raw
   foreach($taskSite in $taskIis.configuration.'system.applicationHost'.sites.site) {
    foreach($taskApplication in $taskSite.application) {
     foreach($taskVirtualDirectory in $taskApplication.virtualDirectory) {
      if(([string]$taskVirtualDirectory.physicalPath).TrimEnd('\') -eq $env:DESIGNER_WCF_DIRECTORY.TrimEnd('\')) {
       $taskApplications+=@{site=[string]$taskSite.name;path=[string]$taskApplication.path;app_pool=[string]$taskApplication.applicationPool;bindings=@($taskSite.bindings.binding | ForEach-Object {@{protocol=[string]$_.protocol;binding=[string]$_.bindingInformation}})}
      }
     }
    }
   }
  }
  } catch {$taskIisError=$_.Exception.Message}
  @{server_connection=[string]$taskConnection.connectionString;sample_services=$taskSampleServices;generator_address=$taskGeneratorAddress;iis_applications=$taskApplications;iis_inspection_error=$taskIisError} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $LocalFolder 'server_settings.json') -Encoding UTF8
 } elseif($Mode -eq 'stage') {
  New-Item -ItemType Directory -Path $taskRemote -Force | Out-Null
  foreach($taskName in @('request.json','compiled.mdb','WcfWorker.cs','WorkerProcess.cs','wcf_worker.ps1')) {
   Copy-Item -LiteralPath (Join-Path $LocalFolder $taskName) -Destination ($taskRemote+'\'+$taskName)
  }
 } else {
  foreach($taskName in @('result.json','worker.log','progress.json','generator.log')) {
   if(Test-Path -LiteralPath ($taskRemote+'\'+$taskName)) {Copy-Item -LiteralPath ($taskRemote+'\'+$taskName) -Destination (Join-Path $LocalFolder $taskName)}
  }
  foreach($taskName in @('client','server')) {
   if(Test-Path -LiteralPath ($taskRemote+'\'+$taskName)) {Copy-Item -LiteralPath ($taskRemote+'\'+$taskName) -Destination $LocalFolder -Recurse}
  }
 }
} finally { if(Get-PSDrive DesignerTransfer -ErrorAction SilentlyContinue) {Remove-PSDrive DesignerTransfer} }
