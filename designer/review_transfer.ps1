param([string]$LocalMdb,[string]$ExpectedSha256,[string]$ResultFile,[ValidateSet('true','false')][string]$Activate,
      [string]$ProjectId='', [string]$BackupsDirectory='')
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
 if($ProjectId) {
  if($ProjectId -notmatch '^[0-9a-f]{32}$') {throw 'Invalid working project id'}
  $taskRelative='DesignerWorkspace\'+$ProjectId
 }
 $taskRemote='DesignerReview:\'+$taskRelative
 New-Item -ItemType Directory -Path $taskRemote -Force | Out-Null
 $taskStaging=$taskRemote+'\InSite.'+$taskId+'.tmp'
 try {
  Copy-Item -LiteralPath $LocalMdb -Destination $taskStaging
  if((Get-FileHash -LiteralPath $taskStaging -Algorithm SHA256).Hash -ne $ExpectedSha256) {throw 'Copied MDB checksum mismatch'}
  Move-Item -LiteralPath $taskStaging -Destination ($taskRemote+'\InSite.mdb') -Force
 } finally {if(Test-Path -LiteralPath $taskStaging) {Remove-Item -LiteralPath $taskStaging}}
 if(-not(Test-Path -LiteralPath ($taskRemote+'\SiteInfo.mdb'))) {
  Copy-Item -LiteralPath ('DesignerReview:\'+$taskSitePath.Substring(3)) -Destination ($taskRemote+'\SiteInfo.mdb')
 }
 if($ProjectId -and $BackupsDirectory -and (Test-Path -LiteralPath $BackupsDirectory)) {
  $taskBackupRoot=$taskRemote+'\backups'
  New-Item -ItemType Directory -Path $taskBackupRoot -Force | Out-Null
  $taskSavedIds=@()
  foreach($taskVersion in (Get-ChildItem -LiteralPath $BackupsDirectory -Directory)) {
   if($taskVersion.Name -notmatch '^[0-9a-f]{32}$' -or -not(Test-Path -LiteralPath (Join-Path $taskVersion.FullName 'backup.json'))) {continue}
   $taskSavedIds += $taskVersion.Name
   $taskVersionRemote=Join-Path $taskBackupRoot $taskVersion.Name
   New-Item -ItemType Directory -Path $taskVersionRemote -Force | Out-Null
   $taskVersionRecord=Get-Content -LiteralPath (Join-Path $taskVersion.FullName 'backup.json') -Raw | ConvertFrom-Json
   foreach($taskVersionFile in @('InSite.mdb','SiteInfo.mdb','backup.json')) {
    $taskLocalVersion=Join-Path $taskVersion.FullName $taskVersionFile
    if(Test-Path -LiteralPath $taskLocalVersion) {Copy-Item -LiteralPath $taskLocalVersion -Destination (Join-Path $taskVersionRemote $taskVersionFile) -Force}
   }
   if((Get-FileHash -LiteralPath (Join-Path $taskVersionRemote 'InSite.mdb') -Algorithm SHA256).Hash -ne $taskVersionRecord.sha256) {throw 'Remote backup MDB checksum mismatch'}
   if($taskVersionRecord.siteinfo_sha256 -and (Get-FileHash -LiteralPath (Join-Path $taskVersionRemote 'SiteInfo.mdb') -Algorithm SHA256).Hash -ne $taskVersionRecord.siteinfo_sha256) {throw 'Remote backup SiteInfo checksum mismatch'}
  }
  foreach($taskOldVersion in (Get-ChildItem -LiteralPath $taskBackupRoot -Directory)) {
   if($taskOldVersion.Name -match '^[0-9a-f]{32}$' -and $taskOldVersion.Name -notin $taskSavedIds) {
    $taskDeletePath=(Resolve-Path -LiteralPath $taskOldVersion.FullName).ProviderPath
    $taskAllowedRoot=(Resolve-Path -LiteralPath $taskBackupRoot).ProviderPath.TrimEnd('\')+'\'
    if(-not $taskDeletePath.StartsWith($taskAllowedRoot,[StringComparison]::OrdinalIgnoreCase)) {throw 'Backup path escapes working project'}
    Remove-Item -LiteralPath $taskDeletePath -Recurse -Force
   }
  }
 }
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
