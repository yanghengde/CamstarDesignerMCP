param([string]$LocalMdb,[string]$ExpectedSha256,[string]$ResultFile,[ValidateSet('true','false')][string]$Activate)
$ErrorActionPreference='Stop'
$taskCredential=[pscredential]::new($env:DESIGNER_WINDOWS_USER,(ConvertTo-SecureString $env:DESIGNER_WINDOWS_PASSWORD -AsPlainText -Force))
try {
 New-PSDrive -Name DesignerReview -PSProvider FileSystem -Root $env:DESIGNER_SERVER_SHARE -Credential $taskCredential | Out-Null
 $taskConfigPath='DesignerReview:\'+$env:DESIGNER_UI_EXE.Substring(3)+'.Config'
 $taskConfigHash=(Get-FileHash -LiteralPath $taskConfigPath -Algorithm SHA256).Hash
 [xml]$taskConfig=Get-Content -LiteralPath $taskConfigPath -Raw
 $taskMdbNode=$taskConfig.configuration.appSettings.add | Where-Object key -eq 'MDBPath'
 $taskSiteNode=$taskConfig.configuration.appSettings.add | Where-Object key -eq 'SiteMDBPath'
 if(-not $taskMdbNode -or -not $taskSiteNode) {throw 'Designer config lacks MDBPath/SiteMDBPath'}
 $taskSitePath=[string]$taskSiteNode.value
 if($taskSitePath -notmatch '^C:\\' -or $taskSitePath -match '\.\.|[\r\n]') {throw 'SiteInfo must be an existing C drive file'}
 $taskId=[guid]::NewGuid().ToString('N')
 $taskRelative='Temp\DesignerMCP\Review_'+$taskId
 $taskRemote='DesignerReview:\'+$taskRelative
 New-Item -ItemType Directory -Path $taskRemote | Out-Null
 Copy-Item -LiteralPath $LocalMdb -Destination ($taskRemote+'\InSite.mdb')
 if((Get-FileHash -LiteralPath ($taskRemote+'\InSite.mdb') -Algorithm SHA256).Hash -ne $ExpectedSha256) {throw 'Copied MDB checksum mismatch'}
 Copy-Item -LiteralPath ('DesignerReview:\'+$taskSitePath.Substring(3)) -Destination ($taskRemote+'\SiteInfo.mdb')
 $taskPreviousMdb=[string]$taskMdbNode.value
 $taskPreviousSite=[string]$taskSiteNode.value
 $taskBackup=$null
 $taskActivated=$false
 $taskActivationError=$null
 if($Activate -eq 'true') {
  if((Get-FileHash -LiteralPath $taskConfigPath -Algorithm SHA256).Hash -ne $taskConfigHash) {throw 'Designer config changed during transfer; retry'}
  $taskBackup=$taskRemote+'\Designer.Net.UI.exe.Config.backup'
  Copy-Item -LiteralPath $taskConfigPath -Destination $taskBackup
  $taskMdbNode.value='C:\'+$taskRelative+'\InSite.mdb'
  $taskSiteNode.value='C:\'+$taskRelative+'\SiteInfo.mdb'
  try {
   $taskConfig.Save($taskConfigPath)
   [xml]$taskCheck=Get-Content -LiteralPath $taskConfigPath -Raw
   if(($taskCheck.configuration.appSettings.add | Where-Object key -eq 'MDBPath').value -ne $taskMdbNode.value) {throw 'Designer config verification failed'}
   $taskActivated=$true
  } catch {
   $taskActivationError='Designer startup config could not be changed; select the copied MDB and SiteInfo manually.'
   if((Get-FileHash -LiteralPath $taskConfigPath -Algorithm SHA256).Hash -ne $taskConfigHash) {
    Copy-Item -LiteralPath $taskBackup -Destination $taskConfigPath -Force
   }
  }
 }
 @{status= $(if($taskActivated) {'selected_for_next_designer_launch'} else {'copied_for_designer'});
   activated=$taskActivated;restart_required=$taskActivated;activation_error=$taskActivationError;
   server_mdb=('C:\'+$taskRelative+'\InSite.mdb');server_siteinfo=('C:\'+$taskRelative+'\SiteInfo.mdb');
   share_mdb=($env:DESIGNER_SERVER_SHARE.TrimEnd('\')+'\'+$taskRelative+'\InSite.mdb');
   copied_sha256=$ExpectedSha256;previous_mdb=$taskPreviousMdb;previous_siteinfo=$taskPreviousSite;
   config_backup= $(if($taskBackup) {'C:\'+$taskRelative+'\Designer.Net.UI.exe.Config.backup'} else {$null});
   database_published=$false} | ConvertTo-Json | Set-Content -LiteralPath $ResultFile -Encoding UTF8
} finally {if(Get-PSDrive DesignerReview -ErrorAction SilentlyContinue) {Remove-PSDrive DesignerReview}}
