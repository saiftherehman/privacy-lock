# Camera & Mic Lock (privacy-lock)

[![tests](https://github.com/saiftherehman/privacy-lock/actions/workflows/tests.yml/badge.svg)](https://github.com/saiftherehman/privacy-lock/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A PIN-protected kill switch for your webcam and microphone.

When the camera or microphone is blocked, **no program can use it**: not your
browser, not a video-call app, not a background process. Getting access back
needs your PIN, typed by hand. A block can also be set to last only until
the next restart. (On a Mac the lock can only stop programs within moments of
them starting the camera or mic; macOS allows nothing stronger.)

| Platform | Status |
|---|---|
| Linux, Ubuntu / GNOME | ✅ Available, tested on Ubuntu 26.04 (GNOME 50, Wayland) |
| Linux, other distributions and desktops | 🔶 Supported, not yet tested: Debian, Fedora, Arch, openSUSE; GNOME, KDE, XFCE, Cinnamon, MATE; PipeWire or PulseAudio; systemd or OpenRC. See [docs/linux.md](docs/linux.md#distribution-support) |
| Windows 10 / 11 | 🔶 Written, not yet tested on Windows: [docs/windows.md](docs/windows.md) |
| macOS 12+ | 🔶 Written, not yet tested on a Mac. **Weaker than on Linux and Windows:** macOS only lets it stop apps within moments, not make the devices disappear. See [docs/macos.md](docs/macos.md) |

---

## Features

Every platform version provides the same behavior (full rules in
[docs/design.md](docs/design.md)):

- **A lock icon** in the top bar / system tray / menu bar to block and unblock
  the camera and microphone. It starts at every login, has no "Quit", and
  comes back by itself if closed.
- **Two kinds of block**, chosen each time you block:
  - *With my PIN*: stays on across restarts until you enter the PIN.
  - *Only by restarting the computer*: even the PIN cannot lift it.
- **A restart unblocks "restart only" blocks, after you log in.** Until the
  computer password is entered at the login screen the blocks stay on; a
  notification tells you they were lifted.
- **Status notification** at every login and screen unlock.
- **Wrong-PIN protection:** after 5 wrong PINs, PIN unlocking is disabled for
  1 hour. During that hour a restart unblocks everything (after you log
  in).
- **Your computer password is the backup check.** Setting up the PIN,
  changing it and turning the lock off also ask for your computer (login /
  screen-lock) password. The tool never reads or stores that password.
- **Refuses to run on an unprotected computer.** If the account has no
  password or automatic login is on, it warns you and will not set up, change
  the PIN, or unblock.
- Also blocks USB cameras and microphones plugged in later.

## Quick start

Get the code:

```bash
git clone https://github.com/saiftherehman/privacy-lock.git
cd privacy-lock
```

**Linux**

```bash
cd linux
sudo ./install.sh
```

If something is missing, the installer says which packages to install.
Details: [docs/linux.md](docs/linux.md).

**Windows** (Python from python.org, installed *for all users*), in an
administrator PowerShell:

```powershell
cd windows
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

Details: [docs/windows.md](docs/windows.md).

**macOS** (Apple's Python: run `xcode-select --install` once if needed):

```bash
cd macos
sudo ./install.sh
```

Details: [docs/macos.md](docs/macos.md).

Each installer should print `OK: this computer is password protected.` If it
prints a warning, fix that first: set a password for your account and turn
**off** automatic login.

Then click the **lock icon** (top bar, notification area or menu bar) and
choose **Set up PIN…**, or run `privacy-lock setup`. It asks for your new PIN
(at least 4 digits) twice and your computer password.

## Daily use

Click the **lock icon**:

| Menu item | What it does |
|---|---|
| **Block camera… / microphone… / camera and microphone…** | Asks how it may be unblocked later (PIN, or restart only), then your PIN, then your computer password, then blocks. |
| **Unblock …** | PIN, then computer password. Only shown for PIN-type blocks. |
| **Change PIN…** | Current PIN and new PIN twice → computer password. |
| **Forgot PIN…** | New PIN twice → computer password, no old PIN. Only while nothing is blocked; otherwise it explains how to get back in. |
| **Turn off Camera & Mic Lock…** | Computer password + PIN. Unblocks everything, removes the PIN and uninstalls. |

The icon shows the state: open lock (nothing blocked), crossed-out camera,
crossed-out microphone, or closed lock (both blocked).

If the icon is ever missing, search your apps for **Camera & Mic Lock**.

### Command line

```text
sudo privacy-lock setup                      set the PIN (first run)
sudo privacy-lock change-pin                 change the PIN (needs the old PIN)
sudo privacy-lock reset-pin                  forgot the PIN: new one, nothing may be blocked
sudo privacy-lock all off                    block camera and mic (PIN to unblock)
sudo privacy-lock camera off --restart-only  block the camera until restart
sudo privacy-lock mic on                     unblock the mic (computer password + PIN)
privacy-lock status                          show the current state
sudo privacy-lock check                      is this computer password protected?
sudo privacy-lock remove                     turn off and uninstall
```

`camera`, `mic` and `all` work with every `on` / `off` command. On Windows
and macOS leave out `sudo`: the command talks to the lock's own background
service.

## Uninstall

Use **Turn off Camera & Mic Lock…** in the menu, or the uninstaller
(`sudo ./linux/uninstall.sh`, `windows\uninstall.ps1`, `./macos/uninstall.sh`).
All need your computer password and your PIN. Turning off is refused while a
"restart only" block or the wrong-PIN lockout is active: restart first.

## Forgot the PIN?

1. If something is blocked: enter 5 wrong PINs to start the 1-hour lockout,
   then restart within that hour. After you log in, the camera and
   microphone are unblocked.
2. Choose **Forgot PIN…** in the menu (or run `sudo privacy-lock reset-pin`),
   enter a new PIN twice, then your computer password.

Forgot PIN only works while nothing is blocked, so it can never be used to
unblock the camera or microphone.

## What it protects against, and what it doesn't

**Protects against:** any normal program (malware, browser tabs, apps,
extensions) using the camera or microphone, and anyone at the keyboard who
knows neither the PIN nor the computer password.

**Does not protect against:**

- Anyone who can **restart** the computer after 5 wrong PINs, or with a
  "restart only" block. By design, that restart unblocks. The password at
  the login screen keeps them out afterwards, which is why a password and no
  automatic login are required.
- Programs running as **administrator / root**, or anyone who knows the
  administrator password: they can undo any block.
- **Bluetooth headset microphones** (on Linux). Turn Bluetooth off when you
  need full privacy.
- On a **Mac**, an app can open the camera or microphone for a moment
  before it is stopped (see [docs/macos.md](docs/macos.md)).
- On **Windows**, *Shut down* with Fast Startup does not count as a
  restart; use *Restart* to lift a "restart only" block.

## Documentation

| File | Contents |
|---|---|
| [docs/design.md](docs/design.md) | Behavior every platform must implement; security model |
| [docs/linux.md](docs/linux.md) | Linux implementation, files, distribution support |
| [docs/windows.md](docs/windows.md) | Windows implementation |
| [docs/macos.md](docs/macos.md) | macOS implementation and its limits |
| [docs/development.md](docs/development.md) | Project status, test checklist, known gaps, conventions |
| [docs/security-review.md](docs/security-review.md) | Linux security review and its minor points |

## Project layout

```text
privacy-lock/
├── README.md
├── LICENSE      MIT license
├── SECURITY.md  how to report security problems
├── .github/     automatic test run on GitHub
├── docs/        design and per-platform documentation
├── common/      the shared rules and code used by every version
├── linux/       Linux version
├── windows/     Windows version
├── macos/       macOS version
└── tests/       unit tests (python3 -m unittest discover tests)
```

## Security

Found a way around the lock? Please report it privately, see
[SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © 2026 [saiftherehman](https://github.com/saiftherehman)
