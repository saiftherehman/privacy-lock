# Development

## Status (2026-10-31)

| Platform | State |
|---|---|
| Linux | Feature-complete per [design.md](design.md). Tested on Ubuntu 26.04 GNOME Wayland (checklist below). Since the last test: the rules moved into the shared core, plus support for other distributions, desktops, PulseAudio, OpenRC and MIPI cameras. Needs a reinstall and a re-run of the checklist. |
| Windows | Written ([windows.md](windows.md)), lint-clean, shared parts unit-tested. Not yet run on Windows. |
| macOS | Written ([macos.md](macos.md)), best effort only (see the limit there), lint-clean, shared parts unit-tested. Not yet run on a Mac. |

## Layout

```text
common/   the rules (privacy_lock_core.py) and what Windows and macOS share:
          request handling, menu logic, command line
linux/    engine, GTK indicator, polkit helpers, systemd and OpenRC services
windows/  service (SYSTEM), notification-area menu, installer
macos/    LaunchDaemon (root), menu-bar app, CoreAudio/CoreMediaIO bindings, installer
tests/    unit tests for common/ (python3 -m unittest discover tests)
```

Every platform engine is a `Platform` subclass that blocks devices, checks
the account and the password, and reads PINs; `privacy_lock_core.Engine`
does everything else. Change a rule in `design.md` and `privacy_lock_core.py`,
and all three platforms follow.

Next steps, in order:

1. Linux: finish the restart and Forgot PIN tests below with the installed
   version, then reinstall (`sudo ./linux/install.sh`) and re-run the
   checklist on the new code.
2. Windows: install and run the Windows checklist.
3. macOS: install and run the macOS checklist.
4. Linux on other distributions (Fedora, Arch, KDE, OpenRC) in VMs.
5. Publish on GitHub (choose a license first).

## Automated tests

```bash
python3 -m unittest discover -b tests
```

`tests/test_core.py` runs every rule (PIN, lockout, modes, restart, login,
Forgot PIN, Turn off) against a fake platform in a temporary folder.
`tests/test_shared.py` covers the Windows / macOS request handling (allow-list,
bad input) and the menu flows (PIN before password, lockout, cancelling).

## Linux test checklist

Run after every change, on a real machine, in a real terminal (`sudo` needs a
TTY). ✅ = passed on Ubuntu 26.04, 2026-10-31.

**Install**
- ✅ `sudo ./install.sh` prints `OK: this computer is password protected.`
- ✅ Indicator appears in the top bar; status notification shows right away.
- ✅ Upgrading from the first version moves old `/etc` blocks to `/run`.

**Blocking**
- ✅ `camera off`: `uvcvideo` unloaded, `/dev/video*` gone, `modprobe uvcvideo`
  as user → `Operation not permitted`, PipeWire lists no camera.
- ✅ `mic off`: `arecord -D hw:0,0` → `Permission denied`, PipeWire lists no
  source, `/dev/snd/pcmC0D0c` is `c---------` with no ACL.
- ✅ `privacy-lock status` reports leaks if any node is still accessible.

**Unblocking**
- ✅ PIN unblock from the indicator: camera opens, mic records, PipeWire lists
  both, capture switch back on.
- ✅ Restart-only block refuses the PIN (exit 5), unit-tested.
- ✅ 5 wrong PINs → 1-hour lockout; right PIN refused during it; works again
  after it, unit-tested and done by hand.
- ✅ Restart after lockout, while blocked (2026-10-31): camera + mic blocked
  (`pin` mode), lockout active. After the restart, `privacy-lock-boot` logged
  `Kept blocked until login: camera, mic` at 12:30:43, the GDM greeter started
  at 12:30:47, the user logged in at 12:31:00.75 and `privacy-lock-login`
  logged `Unblocked after login of uid 1000: camera, mic` at 12:31:01.97.
  Afterwards both reported ON, camera gave a frame, mic recorded, and
  `attempts.json` was gone (lockout cleared). To check it, compare
  `journalctl -b -u privacy-lock-boot -u privacy-lock-login -o short-precise`
  with `journalctl -b -u systemd-logind | grep "New session"`. (A text console
  can't be used to check the login screen: logging in there counts as a login
  and lifts the block.)
  - ✅ Block/unblock from the menu accepts the right PIN after the restart.
  - ✅ "Camera and Microphone unblocked" notification shown once after login.

- ✅ Restart during the lockout, new version (PIN blocks survive restarts).
  Tested 2026-10-31: camera + mic blocked (`pin`), lockout active until ~13:49,
  `was-blocked.json` in the previous version's list format. Restarted 12:51:
  boot log `Kept blocked until login: camera, mic` at 12:51:43, user login
  12:52:56, `Unblocked after login of uid 1000: camera, mic` at 12:52:58;
  notification and devices fine, right PIN accepted again afterwards.
- ⏳ Restart while PIN-blocked, **no** lockout, shared-core version.
  Prepared 2026-10-31 13:27: shared-core version installed with
  `sudo ./linux/install.sh` (printed `OK: this computer is password
  protected.`; engine, `privacy_lock_core.py` and tray match the source;
  boot and login services enabled; tray restarted without errors). Camera +
  mic blocked in `pin` mode since 12:54 (blocked by the previous version, so
  this also tests that the new version reads its state). No wrong PINs, no
  lockout. Restart, then after login check:
  - `journalctl -b -u privacy-lock-boot -u privacy-lock-login -o short-precise`
    shows `Kept blocked (PIN to unblock): camera, mic` before the login and
    no `Unblocked after login` line.
  - `privacy-lock status` → both `OFF (unlock: PIN)`.
  - No "unblocked" notification; the login status notice says both blocked.
  - The PIN from the menu unblocks both; camera gives a frame, mic records.
  The previous version is backed up in `~/Scripts/privacy-lock-backup-2026-10-31`
  (`sudo ~/Scripts/privacy-lock-backup-2026-10-31/linux/install.sh` to go back).
  Checked after the 13:56 boot: boot service logged `Kept blocked (PIN to
  unblock): camera, mic` at 13:56:01, before the login; the login service
  logged nothing (no `Unblocked after login`). `privacy-lock status` shows
  both `OFF (unlock: PIN)`. `uvcvideo` not loaded, no `/dev/video*`;
  `/dev/snd/pcmC0D0c` is mode 0000 with no ACLs and the capture switch is off.
  Still to confirm by hand: no "unblocked" notification at login, and the PIN
  from the menu unblocks both.
  Logic unit-tested 2026-10-31 (pin / lockout active / lockout expired /
  restart-only / mixed / old file format / counter migration).
- ⬜ Restart with a restart-only block: lifted after login, as before.

**Indicator**
- ✅ No Quit item; killing it → back within ~2 s (`Restart=always`).
- ✅ Status notification at login and at screen unlock.
- ✅ Change PIN from the menu (old PIN + new PIN twice → PIN checked → password
  dialog).
- ⬜ Forgot PIN while something is blocked: explanation shown, no password
  dialog; `sudo privacy-lock reset-pin` refused the same way.
- ⬜ Forgot PIN with nothing blocked: new PIN twice → password dialog → the
  new PIN works, the old one is refused. Engine logic unit-tested 2026-10-31.
  Done from the menu 2026-10-31 12:53 and the new PIN was accepted at 12:54;
  still to check: the old PIN is refused.
- ⬜ Wrong PIN in any menu action: error shown, **no** password dialog.
- ⬜ Turn off from the menu (do it last, it uninstalls).

**Password checks**
- ✅ Autologin detection for GDM, LightDM, SDDM, ignoring commented lines,
  unit-tested.
- ⬜ On an account without a password: setup / unblock / change refused with
  the warning; blocking still works.

**After the shared-core update (reinstall first)**
- ⬜ `privacy-lock status` and `--json` unchanged; menu looks the same.
- ⬜ Block / unblock camera and mic from the menu and the terminal.
- ⬜ Restart with a PIN block and with a restart-only block; journal now says
  `Unblocked after login of <user>: ...` (user name instead of uid).
- ⬜ Wrong PIN, lockout, Forgot PIN, Change PIN behave as before.

**Other distributions and desktops (VMs)**
- ⬜ Fedora GNOME, Arch KDE, Debian XFCE: install, block, unblock, restart.
- ⬜ KDE / XFCE / Cinnamon: status notice after screen unlock.
- ⬜ PulseAudio system: mic block drops a running recording.
- ⬜ Gentoo or Artix with OpenRC + elogind: boot service runs before the
  display manager; restart-only block lifted at login; menu comes back after
  being killed.
- ⬜ Laptop with an IPU6 camera: no app can open it while blocked.

## Windows test checklist

Not run yet. On a Windows 10 and a Windows 11 machine, Python from python.org
for all users:

**Install**
- ⬜ `install.ps1` refuses a per-user Python; with an all-users Python it
  prints `OK: this computer is password protected.`
- ⬜ Task `\privacy-lock\privacy-lock-service` runs as SYSTEM; the lock icon
  appears and the status notification shows.
- ⬜ `privacy-lock status` works from a normal (not admin) terminal.

**Blocking**
- ⬜ Block camera: Camera app and a browser lose the picture at once;
  Device Manager shows the camera disabled; Settings > Privacy > Camera is
  off and cannot be turned on without admin rights.
- ⬜ Block mic: Voice Recorder stops; Sound settings show no input; speakers
  still play.
- ⬜ USB webcam or headset plugged in while blocked: disabled within ~5 s.
- ⬜ A device disabled by hand before blocking stays disabled after unblocking.

**Unblocking and PIN**
- ⬜ PIN, then the Windows Security dialog; wrong password refused; wrong PIN
  never shows the dialog.
- ⬜ 5 wrong PINs → lockout; blocking still works with only the password.
- ⬜ Forgot PIN refused while blocked, works while not.

**Restart**
- ⬜ PIN block survives Restart; restart-only block stays at the sign-in
  screen and is lifted after signing in; notification shown once.
- ⬜ Restart-only block after *Shut down* with Fast Startup stays (documented).
- ⬜ Killing the service: back within a minute, and it does not count as a
  restart (restart-only block stays).
- ⬜ Remote Desktop sign-in does not lift a restart-only block.

**Menu**
- ⬜ Killing the menu: back within a few seconds; only one icon per session.
- ⬜ Status notification after unlocking the screen.
- ⬜ Turn off: everything enabled again, task, `Run` entry, `PATH` entry and
  both folders gone (do it last).

**Password checks**
- ⬜ Account without a password, `AutoAdminLogon=1`, Sysinternals Autologon,
  UAC set to never notify: each one reported by `privacy-lock check`.

## macOS test checklist

Not run yet. On an Apple silicon and an Intel Mac, with `/usr/bin/python3`:

- ⬜ `sudo ./install.sh` refuses Homebrew Python; installs, prints the check
  and the limit note; icon in the menu bar.
- ⬜ Block camera: FaceTime / Photo Booth / a browser lose the picture within
  a second, each time they try again; a notification says an app was stopped.
  Check `/Library/Logs/privacy-lock.log` for which services were ended.
- ⬜ Block mic: inputs muted at volume 0 in Sound settings and kept there;
  on macOS 14+ a recording app is ended; unblocking puts the volume back.
- ⬜ PIN before password, lockout, Forgot PIN, Change PIN as on Linux.
- ⬜ Restart: PIN block stays; restart-only block lifted only after logging
  in; notification once.
- ⬜ Killing the menu: launchd brings it back.
- ⬜ Automatic login and an account without password are reported by
  `privacy-lock check`.
- ⬜ Turn off removes everything, including the daemon.



## Known gaps

- Linux: the `NOPASSWD` check flags *any* `NOPASSWD` rule, even a harmless
  one for a single command.
- Linux: `libayatana-appindicator` prints a deprecation warning; consider
  `libayatana-appindicator-glib` or a StatusNotifierItem over D-Bus.
- Linux: no runit / s6 services.
- Windows and macOS: never run yet (see the checklists and the open points
  in [windows.md](windows.md) and [macos.md](macos.md)).
- macOS: a block stops apps quickly but cannot keep them from opening the
  device; this is a macOS limit.

## Conventions

- Each platform lives in its own top-level folder (`linux/`, `windows/`,
  `macos/`) and must implement [design.md](design.md) exactly; behavior
  changes go into `design.md` first, then into `common/privacy_lock_core.py`
  with a test.
- No em dashes in code, comments or docs.
- User-facing wording (menu items, notifications, warnings) is shared across
  platforms: copy it from `design.md`, or better, use the strings in
  `common/`.
- Privileged code stays small; the indicator never runs with privileges.
- Commit messages and documentation describe the change only.

## Useful commands (Linux)

```bash
privacy-lock status --json                       # state as JSON
systemctl --user status privacy-lock-tray        # indicator service
journalctl --user -u privacy-lock-tray -b        # indicator log
systemctl status privacy-lock-boot               # boot service
pkaction --action-id io.github.saiftherehman.privacylock.pin --verbose
ls -l /dev/video* /dev/snd/pcm*c; getfacl /dev/snd/pcmC0D0c
```
