# Windows implementation

Implements [design.md](design.md) for Windows 10 and 11.

Status: **written, not yet run on a real Windows machine.** Everything below
is what the code does; the test checklist in
[development.md](development.md#windows-test-checklist) has to pass before
it can be called working.

## Requirements

- Windows 10 or 11.
- Python 3.10 or newer from python.org, installed **for all users** (so it
  lives under `C:\Program Files`, where only administrators can change it).
  The installer refuses a per-user Python: a program running as you could
  change it, and with it the lock.
- The installer adds `pywin32`, `pystray` and `pillow` to its own virtual
  environment.

## Install

In an administrator PowerShell:

```powershell
cd windows
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

It should print `OK: this computer is password protected.` Then click the
lock icon in the notification area (it may be hidden under the `^`
overflow) and choose **Set up PIN…**, or run `privacy-lock setup`.

## Files

| Source (`windows/`) | Installed as | Runs as | Purpose |
|---|---|---|---|
| `privacy_lock_service.py` | `C:\Program Files\privacy-lock\` | SYSTEM, Task Scheduler "at startup", restarted if it stops | Boot and login handling, keeps devices blocked, answers the named pipe |
| `privacy_lock_engine.py` | same | SYSTEM (inside the service) | Windows side: blocking, account checks, password check, removal |
| `../common/privacy_lock_core.py` | same | | The shared rules (same file as on Linux and macOS) |
| `../common/privacy_lock_server.py` | same | | Request format and allow-list |
| `privacy_lock_tray.py` | same | the signed-in user (HKLM `Run` key, `--supervise`) | Notification-area icon, dialogs, notifications |
| `../common/privacy_lock_menu.py` | same | | Menu entries and dialog order, shared with macOS |
| `privacy_lock_client.py` | same | the user | Named-pipe client |
| `privacy_lock.py`, `privacy-lock.cmd` | same (folder added to `PATH`) | the user | Command line |
| `install.ps1`, `uninstall.ps1` | | administrator / user | Install; turn off |

Data in `C:\ProgramData\privacy-lock\` (SYSTEM and Administrators only):
`pin.json`, `attempts.json`, `was-blocked.json`, `disabled-devices.json`,
`service.log`. Readable by everyone, in `public\`: `state.json`,
`boot-notice.json`, `boot.json`, `pin-set`.

## How the camera is blocked

1. Camera access is set to **Deny** for the whole device:
   `HKLM\...\CapabilityAccessManager\ConsentStore\webcam\Value`. This is the
   switch in Settings > Privacy & security > Camera.
2. Every present, enabled device in the `Camera` and `Image` classes is
   disabled (`Disable-PnpDevice`). Disabling ends any running stream, so apps
   lose the camera at once. The ids are kept in `disabled-devices.json`, so
   unblocking turns back on only what the lock turned off.
3. Every 5 seconds the service looks (WMI, `Win32_PnPEntity`) for cameras
   that are enabled while blocked, such as one plugged in later, and disables
   them.

## How the microphone is blocked

The same, with microphone access set to Deny and the audio **capture**
endpoints disabled (`AudioEndpoint` devices whose id starts with
`SWD\MMDEVAPI\{0.0.1.`). Playback endpoints (`{0.0.0.`) are left alone, so
sound keeps working.

## Restart

Disabled devices stay disabled while Windows is off, so nothing is open
before the service starts. The service tells a real Windows start from its
own restart by the boot time (uptime counter), and only on a real start
runs the shared boot step: `pin` blocks stay, `restart` blocks and blocks
during a wrong-PIN lockout become `login` blocks.

They are lifted when someone has signed in at the PC's own screen: the
console session has a user and the lock screen (`LogonUI.exe`) is not
showing in it. That second check matters because Windows can sign a user in
by itself after an update and lock the session at once; that is not a
sign-in with the password. Remote Desktop sessions never count.

**Shut down vs. restart:** with Fast Startup on (the Windows default),
*Shut down* hibernates the system part of Windows, so the service does not
start again and restart-only blocks stay. Use **Restart** to lift them.

## The menu and the service

The menu has no privileges. It talks to the service over the named pipe
`\\.\pipe\privacy-lock`:

- The pipe's permissions let signed-in users read and write data but not
  create another copy of the pipe. It is created at startup, before anyone
  can sign in, with `FILE_FLAG_FIRST_PIPE_INSTANCE`.
- Before sending a PIN or a password, the client checks that the pipe's
  owner is SYSTEM or Administrators.
- The service finds out who is asking by impersonating the client, never
  from the request, and only accepts requests from the console session (the
  person at this PC's own screen). `status` is open to everyone.

Order, as on Linux: the PIN is checked first (`check-pin`, no password).
Only a right PIN leads to the **Windows Security** password dialog. The
password goes to the service once, is checked with `LogonUser`, and is
then dropped.

## Password checks

- **Is a password required?** `LogonUser` with an empty password: success,
  or error 1327 (blank passwords are limited to the console), means no
  password. Automatic sign-in: `Winlogon\AutoAdminLogon = 1`, a
  `DefaultPassword` value, or the Sysinternals Autologon LSA secret.
  Administrator without prompts: the user is in Administrators and UAC is
  off (`EnableLUA = 0`) or set to never notify
  (`ConsentPromptBehaviorAdmin = 0`).
- **Windows Hello:** the dialog asks for the account password, not the
  Hello PIN. People who only use Hello need to know their Microsoft account
  or local password.

## Uninstall

**Turn off Camera & Mic Lock…** in the menu, or `uninstall.ps1` (runs
`privacy-lock remove`). Both need the PIN and the Windows password. The
service turns the devices and privacy switches back on, deletes the PIN and
the data, removes the scheduled task, the `Run` entry, the `PATH` entry and
the program folder.

## Open points

- Disabling capture endpoints with `Disable-PnpDevice` needs confirming on
  real hardware, including USB headsets and webcams with built-in mics.
- A device in use may report that a restart is needed to disable it;
  `privacy-lock status` then lists it as still accessible.
- Group Policy or Intune may already manage the camera privacy switch.
- Code signing and a proper installer (MSI) for publishing.
