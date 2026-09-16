param(
    [switch]$OneDir,
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment Python was not found: $python"
}

$pyinstallerInstalled = & $python -c "import importlib.util; print('1' if importlib.util.find_spec('PyInstaller') else '0')"
if ($pyinstallerInstalled -ne "1") {
    Write-Host "Installing PyInstaller..."
    & $python -m pip install pyinstaller
}

$pyglassInstalled = & $python -c "import importlib.util; print('1' if importlib.util.find_spec('pyglass') else '0')"
if ($pyglassInstalled -ne "1") {
    Write-Host "Installing pyglass-qt for refractive glass..."
    & $python -m pip install "pyglass-qt>=0.3.0"
}

if ($OneDir -and $OneFile) {
    throw "OneDir and OneFile cannot be used together."
}
$useOneDir = $OneDir
$mode = if ($useOneDir) { "--onedir" } else { "--onefile" }
$runtimeTempArgs = if ($useOneDir) { @() } else { @("--runtime-tmpdir", "%LOCALAPPDATA%\\Temp") }
$assetData = "$(Join-Path $projectRoot 'assets');assets"
$iconPath = Join-Path $projectRoot "assets\icons\app-icon.ico"
$runtimeHook = Join-Path $projectRoot "scripts\pyinstaller_runtime_hook.py"
$icuPath = Join-Path $env:WINDIR "System32\icuuc.dll"

if (-not (Test-Path -LiteralPath $icuPath)) {
    throw "Windows ICU runtime was not found: $icuPath"
}

& $python (Join-Path $projectRoot "scripts\create_icon.py")
if ($LASTEXITCODE -ne 0) {
    throw "Icon generation failed."
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        $mode `
        @runtimeTempArgs `
        --name CCodeFormatter `
        --paths (Join-Path $projectRoot "src") `
        --collect-all PyQt6 `
        --collect-all numpy `
        --collect-all pyglass `
        --add-data $assetData `
        --add-binary "$icuPath;." `
        --runtime-hook $runtimeHook `
        --icon $iconPath `
        (Join-Path $projectRoot "main.py")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }
} finally {
    Pop-Location
}

$output = if ($useOneDir) {
    Join-Path $projectRoot "dist\CCodeFormatter\CCodeFormatter.exe"
} else {
    Join-Path $projectRoot "dist\CCodeFormatter.exe"
}

Write-Host "Build complete: $output"
