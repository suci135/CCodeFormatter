<#
.SYNOPSIS
    Review, commit, and upload the current repository to GitHub.

.EXAMPLE
    .\scripts\publish_github.ps1 -Message "Fix while formatting"

.EXAMPLE
    .\scripts\publish_github.ps1 -Message "Release 0.2.0" -Yes
#>
[CmdletBinding()]
param(
    [string]$Message,

    [string]$Remote = "origin",

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

Invoke-Git rev-parse --is-inside-work-tree | Out-Null
if (-not $Branch) {
    $Branch = (& git branch --show-current).Trim()
}
if (-not $Branch) {
    throw "No current branch is checked out."
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
Invoke-Git push $Remote "HEAD:$Branch"
Write-Host "Uploaded successfully: $Remote/$Branch" -ForegroundColor Green
