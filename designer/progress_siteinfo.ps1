param([string]$ServerFile, [string]$LocalFile)
$ErrorActionPreference = 'Stop'
$taskSecure = ConvertTo-SecureString $env:DESIGNER_WINDOWS_PASSWORD -AsPlainText -Force
$taskCredential = New-Object System.Management.Automation.PSCredential($env:DESIGNER_WINDOWS_USER, $taskSecure)
try {
    New-PSDrive -Name DesignerProgress -PSProvider FileSystem -Root $env:DESIGNER_SERVER_SHARE -Credential $taskCredential | Out-Null
    $taskSource = 'DesignerProgress:\' + $ServerFile.Substring(3)
    $taskBefore = (Get-FileHash -LiteralPath $taskSource -Algorithm SHA256).Hash
    Copy-Item -LiteralPath $taskSource -Destination $LocalFile
    if ((Get-FileHash -LiteralPath $LocalFile -Algorithm SHA256).Hash -ne $taskBefore -or
        (Get-FileHash -LiteralPath $taskSource -Algorithm SHA256).Hash -ne $taskBefore) {
        throw 'SiteInfo changed during copy'
    }
} finally {
    Remove-PSDrive -Name DesignerProgress -ErrorAction SilentlyContinue
}
