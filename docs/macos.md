# macOS implementation

Implements [design.md](design.md) for macOS 12 and newer, as far as macOS
allows.

Status: **written, not yet run on a real Mac.** The test checklist in
[development.md](development.md#macos-test-checklist) has to pass first.

## The limit, said plainly

macOS does not let a third-party tool switch the camera or microphone off
system-wide while System Integrity Protection is on. Kernel extensions that
did this no longer load, and the privacy database (TCC) cannot be changed by
apps. So on a Mac a block **cannot stop an app from opening the device**. What
it does instead:

- **Camera:** twice a second the background process checks whether any
  camera is running (CoreMediaIO, `DeviceIsRunningSomewhere`). If one is, it
  ends the system camera services (`VDCAssistant`, `AppleCameraAssistant`,
  `UVCAssistant`, `appleh13camerad`, `cameracaptured`). Every app's video
  stops; the services come back on demand and are ended again as long as the
  block lasts. An app may still get a fraction of a second of video.
- **Microphone:** every input device is muted with its volume at 0, re-applied
  if anything changes it. On macOS 14 and newer, any process that starts
  recording is ended too (CoreAudio's process list). The volumes from before
  are put back when the mic is unblocked.
- The menu shows a notification whenever an app was stopped.

The menu, the installer and this page all say this, so nobody mistakes it
for the hard block Linux and Windows give.

## Requirements

- macOS 12 or newer.
- Apple's Python 3 (`/usr/bin/python3`; macOS offers to install the Command
  Line Tools the first time). Homebrew or python.org Python is refused,
  because the user can change it without the admin password.
- The installer adds `pystray`, `pillow` and PyObjC (Cocoa, Quartz,
  OpenDirectory) to its own virtual environment.

## Install

```bash
cd macos
sudo ./install.sh
```

Then click the lock icon in the menu bar and choose **Set up PIN…**, or run
`privacy-lock setup`.

## Files

| Source (`macos/`) | Installed as | Runs as | Purpose |
|---|---|---|---|
| `privacy_lock_daemon.py` | `/Library/Application Support/privacy-lock/` | root, LaunchDaemon `io.github.saiftherehman.privacylock.daemon` (KeepAlive) | Boot and login handling, watch loop, socket |
| `privacy_lock_engine.py` | same | root (inside the daemon) | macOS side: blocking, account checks, password check, removal |
| `privacy_lock_audio.py` | same | root | CoreAudio / CoreMediaIO through ctypes |
| `../common/privacy_lock_*.py` | same | | Shared rules, request handling, menu logic, command line |
| `privacy_lock_tray.py` | same | the user, LaunchAgent `io.github.saiftherehman.privacylock.menu` (KeepAlive) | Menu-bar icon, dialogs (osascript), notifications |
| `privacy_lock_client.py` | same | the user | Socket client |
| `privacy_lock.py` | `/usr/local/bin/privacy-lock` (wrapper) | the user | Command line |
| `*.plist` | `/Library/LaunchDaemons/`, `/Library/LaunchAgents/` | | launchd jobs |
| `install.sh`, `uninstall.sh` | | root / user | Install; turn off |

Private data in `/Library/Application Support/privacy-lock/data/` (root,
`0700`): `pin.json`, `attempts.json`, `was-blocked.json`, `mic-volumes.json`.
Public, in `/var/run/privacy-lock/` (emptied at every start): `state.json`,
`boot-notice.json`, `boot.json`, `pin-set`, `stopped.json`. Log:
`/Library/Logs/privacy-lock.log`.

## Restart

The daemon knows a real start from its own restart by the kernel boot time
(`sysctl kern.boottime`), and only then runs the shared boot step. Blocks
are re-applied as soon as the daemon starts, which is before the login
window. `login` blocks are lifted when `/dev/console` belongs to a user,
which only happens after that user logged in with their password at the
Mac's own screen (or unlocked FileVault at startup).

## The menu and the daemon

The menu talks to the daemon over `/var/run/privacy-lock.sock`. Only root can
create files in `/var/run`, and the client checks that the socket belongs to
root before sending anything. The daemon learns who is asking from the
socket itself (`getpeereid`) and only accepts requests from the user at the
Mac's own screen; `status` is open to everyone.

Order, as on the other platforms: PIN first (`check-pin`), and only a right
PIN leads to the password dialog. The password goes to the daemon once, is
checked with Open Directory, and is then dropped.

## Password checks

- **Is a password required?** Open Directory accepts an empty password for
  the account; automatic login (`autoLoginUser` in
  `/Library/Preferences/com.apple.loginwindow.plist`, or `/etc/kcpassword`);
  `sudo -l` shows `NOPASSWD`.

## Uninstall

**Turn off Camera & Mic Lock…** in the menu, or `./uninstall.sh` (runs
`privacy-lock remove`). Needs the PIN and the Mac password. Puts the input
volumes back, removes the menu, the data, the command, and finally the
daemon and the program folder.

## Open points

- Whether CoreMediaIO lists cameras to a LaunchDaemon with no GUI session on
  every macOS version; if not, the camera watch needs to move into the menu
  (user session).
- The exact names of the camera services differ between macOS versions and
  between Intel and Apple silicon; the list may need adding to.
- pystray menu updates from a background thread on macOS.
- A configuration profile (PPPC) that denies Camera and Microphone to
  chosen apps would be stronger, but profiles can only name specific apps.
- Code signing and notarization for publishing.
