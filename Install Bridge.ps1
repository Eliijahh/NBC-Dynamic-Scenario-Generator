param([string]$GamePath = 'R:\SteamLibrary\steamapps\common\Nebulous')
$ErrorActionPreference = 'Stop'
if (Get-Process Nebulous -ErrorAction SilentlyContinue) { throw 'Close NEBULOUS before installing or updating the bridge.' }
& (Join-Path $PSScriptRoot 'scripts\Build Bridge.ps1') -GamePath $GamePath
$installPath = Join-Path $GamePath 'Mods\NBCBridge'
New-Item -ItemType Directory -Force -Path $installPath | Out-Null
foreach ($fileName in @('NBCBridge.dll', 'ModInfo.xml')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "build\NBCBridge\$fileName") -Destination $installPath -Force
}
Write-Output "Installed bridge at $installPath"
Write-Output 'Launch NEBULOUS, enable NBC Campaign Bridge in Mods, then return to the main menu.'
