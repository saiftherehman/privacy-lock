#Requires -RunAsAdministrator
<#
Installs Camera & Mic Lock for Windows 10 / 11.

Needs Python 3.10 or newer installed "for all users" (python.org installer,
"Install for all users" ticked), so it lives under Program Files where normal
programs cannot change it. Run from an administrator PowerShell:

    Set-ExecutionPolicy -Scope Process Bypass; .\install.ps1
#>
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$dir = Join-Path $env:ProgramFiles "privacy-lock"
$data = Join-Path $env:ProgramData "privacy-lock"
$taskPath = "\privacy-lock\"
$taskName = "privacy-lock-service"

# ---- Python, installed where only administrators can change it
$python = $null
foreach ($cand in @("py -3", "python")) {
    try {
        $exe = & cmd /c "$cand -c ""import sys; print(sys.base_prefix + '|' + sys.executable)"" 2>nul"
        if ($LASTEXITCODE -eq 0 -and $exe) { $python = $exe.Trim(); break }
    } catch {}
}
if (-not $python) {
    Write-Host "Python 3 was not found. Install it from python.org with 'Install for all users' ticked, then run this again."
    exit 1
}
$basePrefix, $pythonExe = $python.Split("|")
if (-not $basePrefix.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) {
    Write-Host "Python is installed in $basePrefix, which your user account can change."
    Write-Host "A program running as you could then change Camera & Mic Lock too. Install Python"
    Write-Host "from python.org with 'Install for all users' ticked (it goes to Program Files), then run this again."
    exit 1
}

# ---- stop an older version
Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue |
    Stop-ScheduledTask -ErrorAction SilentlyContinue
$pattern = '*privacy' + '_lock_*'
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like $pattern -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# ---- files
New-Item -ItemType Directory -Force $dir | Out-Null
Copy-Item (Join-Path $src "*.py"), (Join-Path $src "privacy-lock.cmd") $dir -Force
Copy-Item (Join-Path $src "..\common\*.py") $dir -Force
if (-not (Test-Path (Join-Path $dir "venv\Scripts\python.exe"))) {
    & $pythonExe -m venv (Join-Path $dir "venv")
}
$venvPy = Join-Path $dir "venv\Scripts\python.exe"
$venvPyw = Join-Path $dir "venv\Scripts\pythonw.exe"
& $venvPy -m pip install --quiet --disable-pip-version-check --upgrade pywin32 pystray pillow
if ($LASTEXITCODE -ne 0) { Write-Host "Installing the Python packages failed."; exit 1 }

# ---- data folders: private (SYSTEM + Administrators) and public (everyone may read)
New-Item -ItemType Directory -Force $data, (Join-Path $data "public") | Out-Null
icacls $data /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
icacls (Join-Path $data "public") /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" `
    "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" | Out-Null

# ---- the service: SYSTEM, at every start, restarted if it stops
$action = New-ScheduledTaskAction -Execute $venvPyw -Argument "`"$dir\privacy_lock_service.py`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskPath $taskPath -TaskName $taskName

# ---- the menu: for everyone, at every sign-in
Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" "privacy-lock-tray" `
    "`"$venvPyw`" `"$dir\privacy_lock_tray.py`" --supervise"

# ---- the command line
$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if (($machinePath -split ";") -notcontains $dir) {
    [Environment]::SetEnvironmentVariable("Path", "$machinePath;$dir", "Machine")
}

# ---- check, and start the menu for the person signed in (not as administrator)
for ($i = 0; $i -lt 20 -and -not (Test-Path "\\.\pipe\privacy-lock"); $i++) { Start-Sleep -Milliseconds 500 }
Write-Host "Installed."
& (Join-Path $dir "privacy-lock.cmd") check
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: fix the above before setting up the PIN (Settings > Accounts > Sign-in options)."
}
$signedIn = (Get-CimInstance Win32_ComputerSystem).UserName
if ($signedIn) {
    $once = "privacy-lock-start-menu"
    Register-ScheduledTask -TaskPath $taskPath -TaskName $once -Force `
        -Action (New-ScheduledTaskAction -Execute $venvPyw -Argument "`"$dir\privacy_lock_tray.py`" --supervise") `
        -Principal (New-ScheduledTaskPrincipal -UserId $signedIn -LogonType Interactive -RunLevel Limited) | Out-Null
    Start-ScheduledTask -TaskPath $taskPath -TaskName $once
    Start-Sleep -Seconds 2
    Unregister-ScheduledTask -TaskPath $taskPath -TaskName $once -Confirm:$false
}
if (-not (Test-Path (Join-Path $data "public\pin-set"))) {
    Write-Host "Next: set your PIN from the lock icon in the notification area, or run: privacy-lock setup"
}
Write-Host "The lock icon is in the notification area (you may need to drag it out of the ^ overflow)."
