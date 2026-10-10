param(
    [switch]$InstallOnly
)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot

try {
    if (-not (Test-Path -LiteralPath 'requirements.txt' -PathType Leaf)) {
        throw '缺少 requirements.txt，请使用完整的项目目录。'
    }

    $projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
        Write-Host '正在创建 Python 虚拟环境...'
        $pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonLauncher) {
            & $pythonLauncher.Source -3 -m venv .venv
        } elseif ($pythonCommand) {
            & $pythonCommand.Source -m venv .venv
        } else {
            throw '未找到 Python，请先安装 Python 3.10 或以上版本，再运行此命令。'
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
            throw '创建虚拟环境失败，请检查 Python 安装。'
        }
    }

    & $projectPython -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw '项目虚拟环境需要可用的 Python 3.10 或以上版本。'
    }

    Write-Host '正在检查项目依赖，自动安装缺少或版本不匹配的依赖...'
    & $projectPython -m pip --disable-pip-version-check install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw '依赖安装失败，请检查网络及上方错误信息，修复后重新运行。'
    }

    if (-not (Test-Path -LiteralPath '.env' -PathType Leaf)) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
        Write-Host '已创建 .env，请填写模型接口与 Designer 环境配置。'
    }

    if ($InstallOnly) {
        Write-Host '依赖准备完成。'
    } else {
        Write-Host '正在启动设计服务，按 Ctrl+C 停止。'
        & $projectPython main.py
        if ($LASTEXITCODE -ne 0) {
            throw '服务启动失败，请查看上方错误；若端口已占用，请先停止原服务。'
        }
    }
} finally {
    Pop-Location
}
