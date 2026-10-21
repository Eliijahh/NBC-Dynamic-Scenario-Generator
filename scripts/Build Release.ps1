param(
    [string]$GamePath = 'R:\SteamLibrary\steamapps\common\Nebulous',
    [switch]$SkipBridge
)
# Native tools (PyInstaller, pip) write progress to stderr; keep PowerShell from
# turning that into a terminating error and check exit codes explicitly instead.
$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $projectRoot '.tools'
$venv = Join-Path $tools 'venv-build'
$releaseDir = Join-Path $projectRoot 'build\release'
$bridgeDir = Join-Path $projectRoot 'build\NBCBridge'

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

if (-not $SkipBridge) {
    Invoke-Native (Join-Path $PSScriptRoot 'Build Bridge.ps1') @('-GamePath', $GamePath)
}
if (-not (Test-Path -LiteralPath (Join-Path $bridgeDir 'NBCBridge.dll'))) {
    throw 'The bridge has not been built. Run Build Bridge.ps1 first.'
}

if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
    Write-Output "Creating build virtual environment at $venv"
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    Invoke-Native 'py' @('-m', 'venv', $venv)
}
$python = Join-Path $venv 'Scripts\python.exe'
Invoke-Native $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '--upgrade', 'pip')
Invoke-Native $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', 'pyinstaller')

$arguments = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',
    '--name', 'NBC-Campaign-Command',
    '--distpath', $releaseDir,
    '--workpath', (Join-Path $projectRoot 'build\pyinstaller'),
    '--specpath', (Join-Path $projectRoot 'build'),
    '--add-data', "$bridgeDir;bridge",
    '--add-data', ((Join-Path $projectRoot 'prices.json') + ';.'),
    (Join-Path $projectRoot 'app.py')
)
Invoke-Native $python $arguments

$exe = Join-Path $releaseDir 'NBC-Campaign-Command.exe'
$size = [math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 1)
Write-Output "Release built: $exe ($size MB)"
Write-Output 'The exe bundles the bridge, so users do not need the .NET SDK.'
