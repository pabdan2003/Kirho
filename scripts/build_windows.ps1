param(
    [string]$Python = "py",
    [string]$ISCC = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Version = ([regex]::Match((Get-Content "pyproject.toml" -Raw), '(?m)^version = "([^"]+)"$')).Groups[1].Value
if (-not $Version) {
    throw "Could not determine the application version."
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name Kirho `
    --icon assets\kirho.ico `
    --add-data "i18n;i18n" `
    --add-data "themes;themes" `
    main.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

if (-not $ISCC) {
    $ISCC = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
}
if (-not $ISCC -or -not (Test-Path $ISCC)) {
    throw "Inno Setup 6+ is required. Install it, then ensure ISCC.exe is on PATH or pass -ISCC <path>."
}

& $ISCC "/DAppVersion=$Version" "packaging\windows\Kirho.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed."
}
