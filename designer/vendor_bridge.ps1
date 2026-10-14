param([string]$AssemblyPath,[string]$RequestFile,[string]$ResultFile,[switch]$ReadWorker)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Reflection.Assembly]::LoadFrom($AssemblyPath) | Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'VendorBridge.cs') -ReferencedAssemblies @($AssemblyPath,'System.Data','System.Core','System.Xml','System.Xml.Linq','System.Windows.Forms','System.Web.Extensions')
if ($ReadWorker) { [DesignerVendorBridge]::Serve() }
else { [DesignerVendorBridge]::Run($RequestFile,$ResultFile) }
