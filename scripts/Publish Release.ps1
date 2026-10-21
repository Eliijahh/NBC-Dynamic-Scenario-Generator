param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [string]$Title = "",
    [string]$NotesFile = "",
    [string]$GamePath = 'R:\SteamLibrary\steamapps\common\Nebulous',
    [switch]$SkipBuild,
    [switch]$AllowDirty,
    [switch]$DryRun
)
# Builds the portable release, checksums it, tags the current commit and creates
# a GitHub Release with the executable attached. Requires the GitHub CLI:
#   winget install --id GitHub.cli
#   gh auth login
$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $projectRoot 'build\release'
$exe = Join-Path $releaseDir 'NBC-Campaign-Command.exe'
$checksum = "$exe.sha256"

function Invoke-Native([string]$Exe, [string[]]$Arguments, [switch]$AllowFailure) {
    if ($DryRun) {
        Write-Output ("[dry-run] " + $Exe + " " + ($Arguments -join ' '))
        return
    }
    & $Exe @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "Command failed ($code): $Exe $($Arguments -join ' ')"
    }
}

if ($Tag -notmatch '^v\d+\.\d+\.\d+([-.].+)?$') {
    throw "Tag '$Tag' should look like v0.1.0."
}
if (-not $Title) { $Title = "NBC Campaign Command $Tag" }
if (-not $NotesFile) {
    $candidate = Join-Path $projectRoot 'docs\RELEASE-NOTES.md'
    if (Test-Path -LiteralPath $candidate) { $NotesFile = $candidate }
}

# The tag must match the source that built the exe, so require a clean tree.
$dirty = (& git -C $projectRoot status --porcelain) -join "`n"
if ($dirty -and -not $AllowDirty) {
    throw "The working tree has uncommitted changes. Commit them first, or pass -AllowDirty.`n$dirty"
}

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $DryRun) {
    if (-not $gh) {
        throw 'GitHub CLI (gh) is not installed. Run: winget install --id GitHub.cli'
    }
    Invoke-Native 'gh' @('auth', 'status')
}

if (-not $SkipBuild) {
    # Call the build script with named parameters: array splatting is positional,
    # so "@('-GamePath', $path)" would pass '-GamePath' as the path value.
    if ($DryRun) {
        Write-Output "[dry-run] $(Join-Path $PSScriptRoot 'Build Release.ps1') -GamePath $GamePath"
    } else {
        & (Join-Path $PSScriptRoot 'Build Release.ps1') -GamePath $GamePath
    }
}
if (-not $DryRun -and -not (Test-Path -LiteralPath $exe)) {
    throw "Release executable not found at $exe"
}

if ($DryRun) {
    Write-Output "[dry-run] would write SHA-256 to $checksum"
} else {
    $hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  NBC-Campaign-Command.exe" | Set-Content -LiteralPath $checksum -Encoding ascii
    Write-Output "SHA-256: $hash"
}

$existing = (& git -C $projectRoot rev-parse -q --verify "refs/tags/$Tag") 2>$null
if ($existing) {
    Write-Output "Tag $Tag already exists locally."
} else {
    Invoke-Native 'git' @('-C', $projectRoot, 'tag', '-a', $Tag, '-m', $Title)
}
Invoke-Native 'git' @('-C', $projectRoot, 'push', 'origin', $Tag)

$arguments = @('release', 'create', $Tag, $exe, $checksum, '--title', $Title)
if ($NotesFile) {
    $arguments += @('--notes-file', $NotesFile)
} else {
    $arguments += '--generate-notes'
}
Invoke-Native 'gh' $arguments

if ($DryRun) {
    Write-Output '[dry-run] no changes were made.'
} else {
    Write-Output "Release $Tag published. Check the repository's Releases page."
}
