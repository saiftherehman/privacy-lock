<#
Turns Camera & Mic Lock off and removes it. Asks for your PIN and your Windows
password, exactly like "Turn off Camera & Mic Lock" in the menu.
#>
$cmd = Join-Path $env:ProgramFiles "privacy-lock\privacy-lock.cmd"
if (Test-Path $cmd) {
    & $cmd remove
    exit $LASTEXITCODE
}
Write-Host "Camera & Mic Lock is not installed."
