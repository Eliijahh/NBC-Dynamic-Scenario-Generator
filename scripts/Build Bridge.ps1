param([string]$GamePath = 'R:\SteamLibrary\steamapps\common\Nebulous')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$managedPath = Join-Path $GamePath 'Nebulous_Data\Managed'
if (-not (Test-Path -LiteralPath (Join-Path $managedPath 'Nebulous.dll'))) { throw 'Nebulous.dll was not found.' }
$sdkLine = & dotnet --list-sdks | Select-Object -Last 1
if (-not $sdkLine) { throw 'Install the .NET SDK to build the game bridge.' }
$sdkVersion = $sdkLine.Split(' ')[0]
$sdkRoot = $sdkLine.Substring($sdkLine.IndexOf('[') + 1).TrimEnd(']')
$compilerPath = Join-Path $sdkRoot "$sdkVersion\Roslyn\bincore\csc.dll"
$outputPath = Join-Path $projectRoot 'build\NBCBridge'
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
$compilerArguments = @('-nologo', '-target:library', '-nostdlib+', '-langversion:latest', '-optimize+', "-out:$outputPath\NBCBridge.dll")
$referenceNames = @('mscorlib.dll', 'System.dll', 'System.Core.dll', 'System.Xml.dll', 'Nebulous.dll', 'UnityEngine.dll', 'UnityEngine.CoreModule.dll', 'UnityEngine.SceneManagementModule.dll', 'UnityEngine.AssetBundleModule.dll', 'UnityEngine.IMGUIModule.dll', 'Mirror.dll', 'Facepunch.Steamworks.Win64.dll', 'UnityEngine.UI.dll', 'Unity.TextMeshPro.dll')
foreach ($referenceName in $referenceNames) {
    $referencePath = Join-Path $managedPath $referenceName
    if (Test-Path -LiteralPath $referencePath) { $compilerArguments += "-r:$referencePath" }
}
$compilerArguments += Join-Path $projectRoot 'bridge\NBCBridge.cs'
$compilerArguments += Join-Path $projectRoot 'bridge\RefitBridge.cs'
$compilerArguments += Join-Path $projectRoot 'bridge\FittingCatalog.cs'
$compilerArguments += Join-Path $projectRoot 'bridge\LifecycleBridge.cs'
$compilerArguments += "-r:$(Join-Path $projectRoot 'bridge\lib\0Harmony.dll')"
& dotnet $compilerPath @compilerArguments
if ($LASTEXITCODE -ne 0) { throw 'Bridge compilation failed.' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'bridge\ModInfo.xml') -Destination $outputPath -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'bridge\lib\0Harmony.dll') -Destination $outputPath -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'bridge\lib\Harmony-LICENSE.txt') -Destination $outputPath -Force
Write-Output "Bridge built at $outputPath"
