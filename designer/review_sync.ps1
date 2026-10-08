param([string]$ServerMdb,[string]$ExpectedSha256,[string]$ResultFile)
$ErrorActionPreference='Stop'
$taskCredential=[pscredential]::new($env:DESIGNER_WINDOWS_USER,(ConvertTo-SecureString $env:DESIGNER_WINDOWS_PASSWORD -AsPlainText -Force))
try {
 New-PSDrive -Name DesignerSync -PSProvider FileSystem -Root $env:DESIGNER_SERVER_SHARE -Credential $taskCredential | Out-Null
 $taskPath='DesignerSync:\'+$ServerMdb.Substring(3)
 $taskHash=(Get-FileHash -LiteralPath $taskPath -Algorithm SHA256).Hash.ToLowerInvariant()
 @{status='file_checked';unchanged=($taskHash -eq $ExpectedSha256);server_mdb=$ServerMdb;
   copied_sha256=$taskHash;designer_open_verified=$false} | ConvertTo-Json |
   Set-Content -LiteralPath $ResultFile -Encoding UTF8
} finally {if(Get-PSDrive DesignerSync -ErrorAction SilentlyContinue) {Remove-PSDrive DesignerSync}}
