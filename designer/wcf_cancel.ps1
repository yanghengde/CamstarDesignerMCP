param([string]$TaskFolder)
$ErrorActionPreference='Stop'
$taskFull=[IO.Path]::GetFullPath($TaskFolder).TrimEnd('\')
$taskParent=[IO.Path]::GetDirectoryName($taskFull)
$taskId=[IO.Path]::GetFileName($taskFull)
if($taskParent -ne 'C:\Temp\DesignerMCP' -or $taskId -notmatch '^[0-9a-f]{32}$') {throw 'Invalid generation workspace'}
$taskExecutable=Join-Path $taskFull 'WcfWorker.exe'
foreach($taskProcess in Get-Process -Name WcfWorker -ErrorAction SilentlyContinue) {
 if($taskProcess.MainModule.FileName -eq $taskExecutable) {
  Stop-Process -Id $taskProcess.Id -ErrorAction Stop
  Write-Output ('Stopped isolated WCF worker '+$taskProcess.Id)
 }
}
