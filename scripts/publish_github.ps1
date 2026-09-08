<#
.SYNOPSIS
    Review, commit, and upload the current repository to GitHub.

.EXAMPLE
    .\scripts\publish_github.ps1 -Message "Fix while formatting"

.EXAMPLE
    .\scripts\publish_github.ps1 -Message "Release 0.2.0" -Yes

.EXAMPLE
    .\scripts\publish_github.ps1 -Message "Initial upload" -RemoteUrl "https://github.com/user/repository.git"
#>
[CmdletBinding()]
param(
    [string]$Message,

    [string]$Remote = "origin",

    [string]$RemoteUrl = "",

    [string]$Branch = "",

    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments)] [string[]]$Arguments)
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed."
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is not installed or is not available in PATH."
}

if ([string]::IsNullOrWhiteSpace($Message)) {
    $Message = Read-Host "Commit message"
}
if ([string]::IsNullOrWhiteSpace($Message)) {
    throw "A commit message is required; no files were staged or uploaded."
}

$insideRepository = & git rev-parse --is-inside-work-tree 2>$null
if ($LASTEXITCODE -ne 0 -or $insideRepository.Trim() -ne "true") {
    Write-Host "Initializing a new Git repository..." -ForegroundColor Cyan
    Invoke-Git init
}

if (-not $Branch) {
    $Branch = (& git branch --show-current).Trim()
}
if (-not $Branch) {
    $Branch = "main"
    Invoke-Git branch -M $Branch
}

$remoteOutput = & git remote get-url $Remote 2>$null
$existingRemoteUrl = if ($LASTEXITCODE -eq 0) { "$remoteOutput".Trim() } else { "" }
if (-not $existingRemoteUrl) {
    if ([string]::IsNullOrWhiteSpace($RemoteUrl)) {
        $RemoteUrl = Read-Host "GitHub repository URL"
    }
    if ([string]::IsNullOrWhiteSpace($RemoteUrl)) {
        throw "A GitHub repository URL is required; no files were staged or uploaded."
    }
    Invoke-Git remote add $Remote $RemoteUrl
    $existingRemoteUrl = $RemoteUrl
}
elseif (-not [string]::IsNullOrWhiteSpace($RemoteUrl) -and $RemoteUrl -ne $existingRemoteUrl) {
    throw "Remote '$Remote' already points to '$existingRemoteUrl'. Change it with git remote set-url only after verifying the target repository."
}

Invoke-Git diff --check
Invoke-Git add --all

$stagedFiles = @(& git diff --cached --name-only)
if (-not $stagedFiles) {
    Write-Host "No changes to upload."
    exit 0
}

$sensitivePaths = $stagedFiles | Where-Object {
    $_ -match '(^|/)(\.env(?:\..*)?|.*\.(pem|key|pfx|p12))$'
}
if ($sensitivePaths) {
    Invoke-Git restore --staged -- $sensitivePaths
    throw "Potential secret files were removed from the staging area. Review them before publishing."
}

Write-Host ""
Write-Host "Files to publish to ${Remote}/${Branch}:" -ForegroundColor Cyan
& git diff --cached --stat
& git diff --cached --name-status

if (-not $Yes) {
    $confirmation = Read-Host "Commit and push these changes? [y/N]"
    if ($confirmation -notmatch '^(?i:y|yes)$') {
        Write-Host "Cancelled. Changes remain staged for review."
        exit 0
    }
}

Invoke-Git commit -m $Message
Invoke-Git push --set-upstream $Remote "HEAD:$Branch"
Write-Host "Uploaded successfully: $existingRemoteUrl ($Branch)" -ForegroundColor Green
