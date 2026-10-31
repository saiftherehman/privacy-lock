# Linux implementation

Implements [design.md](design.md). Reference platform: Ubuntu 26.04, GNOME 50
on Wayland, PipeWire, systemd 259.

## Requirements

| Need | Ubuntu / Debian | Fedora | Arch |
|---|---|---|---|
| Python 3 + GObject | `python3-gi` | `python3-gobject` | `python-gobject` |
| GTK 3 | `gir1.2-gtk-3.0` | `gtk3` | `gtk3` |
| App indicator | `gir1.2-ayatanaappindicator3-0.1` | `libayatana-appindicator-gtk3` | `libayatana-appindicator` |
| Notifications | `gir1.2-notify-0.7`, `libnotify-bin` | `libnotify` | `libnotify` |
| ACL tools | `acl` | `acl` | `acl` |
| ALSA mixer | `alsa-utils` | `alsa-utils` | `alsa-utils` |
| polkit (`pkexec`) | `pkexec` | `polkit` | `polkit` |

Also: udev, kmod, `sudo`, and either systemd (system and user instances) or
OpenRC with elogind. `install.sh` checks for all of these and prints the
install command for Ubuntu / Debian, Fedora, Arch and openSUSE if something
is missing.

GNOME has no tray by default: the *AppIndicator and KStatusNotifierItem
Support* extension is needed (pre-enabled on Ubuntu). KDE Plasma, XFCE,
Cinnamon and MATE show the icon natively.

## Files

| Source (`linux/`) | Installed as | Runs as | Purpose |
|---|---|---|---|
| `privacy-lock` | `/usr/local/sbin/privacy-lock` (+ link in `/usr/local/bin`) | root | Engine: Linux blocking, account checks, password check, removal |
| `../common/privacy_lock_core.py` | `/usr/local/lib/privacy-lock/` | root | The shared rules (PIN, lockout, modes, restart), same file as on Windows and macOS |
| `privacy-lock-helper` | `/usr/local/libexec/` | root via pkexec | Indicator → block / unblock only |
| `privacy-lock-pin-helper` | `/usr/local/libexec/` | root via pkexec | Indicator → setup / change-pin / reset-pin / remove only |
| `privacy-lock-pin-check` | `/usr/local/libexec/` | root via pkexec | Indicator step 1: checks the PIN only (no password), before step 2 asks for it |
| `io.github.saiftherehman.privacylock.policy` | `/usr/share/polkit-1/actions/` | | polkit rules for the helpers |
| `privacy-lock-tray` | `/usr/local/bin/` | user | Indicator, dialogs, notifications (no privileges) |
| `privacy-lock-tray.service` | `/etc/systemd/user/` | user service | Starts indicator at login, `Restart=always`, enabled `--global` |
| `privacy-lock-tray.desktop` | `/usr/share/applications/` | | App-launcher entry; on OpenRC also `/etc/xdg/autostart/` with `--supervise` |
| `privacy-lock-boot.service` | `/etc/systemd/system/` | root, once per boot | Re-applies last session's blocks until login |
| `privacy-lock-login.service` | `/etc/systemd/system/` | root, until first login | Lifts those blocks at the first local login, writes the notice |
| `privacy-lock-boot.openrc`, `privacy-lock-login.openrc` | `/etc/init.d/` | root | The same two services for OpenRC (runlevels `boot` and `default`) |
| `install.sh` | | root | Checks dependencies, installs, enables services (systemd or OpenRC), runs `check`, restarts the indicator |
| `uninstall.sh` | | root | Calls `privacy-lock remove` |

## How the camera is blocked

1. `/run/modprobe.d/privacy-lock-camera.conf`: `install uvcvideo /bin/false`
   so the webcam driver cannot be loaded.
2. udev rule `/run/udev/rules.d/72-privacy-lock-camera.rules`: every
   `video4linux` node (`/dev/video*`, `/dev/v4l-subdev*`) and `media` node
   (`/dev/media*`) gets mode `0000`, owner root, and loses the
   `uaccess` tag. Ordering matters: `70-uaccess.rules` adds the tag, our `72-`
   rule removes it, and `73-seat-late.rules` would otherwise grant the
   logged-in user an ACL.
3. udev is reloaded **before** the nodes are hardened (an event processed
   with the old rules would re-add the user ACL), then each node gets
   `setfacl -b`, `chown root:root`, `chmod 0000`.
4. Open handles are closed: PipeWire / WirePlumber are restarted per user;
   other holders get SIGTERM, then SIGKILL.
5. `uvcvideo` is unloaded, retried up to 5× (PipeWire may re-grab the device
   while restarting). The nodes disappear.

Unblock: remove both files, `modprobe uvcvideo`, reload udev, restart
PipeWire.

## How the microphone is blocked

The mic shares its sound card with the speakers, so the driver stays loaded.

1. udev rule `/run/udev/rules.d/72-privacy-lock-mic.rules`: every ALSA
   capture node `/dev/snd/pcmC*D*c` → mode `0000`, root, no `uaccess`.
   Playback nodes are untouched.
2. Same node hardening as the camera, before and after the udev reload.
3. Hardware capture switch off via `amixer` (`Capture`, `Mic`, `Internal
   Mic`, `Headset Mic`), which also lights the mute LED on many laptops.
4. The sound server is restarted so it drops its already-open handle:
   PipeWire (`pipewire`, `pipewire-pulse`, `wireplumber` user units), or
   `pulseaudio.service`, or, without user units (OpenRC, or started by the
   desktop), the processes are ended and come back by socket activation, the
   session or PulseAudio autospawn.

Unblock: remove the rule, reload udev, copy owner / group / mode back from the
card's playback node (a `change` event restores the ACL but not the mode),
capture switch on, restart the sound server.

## Restart

Every block file is under `/run` (RAM, emptied at boot). Each block also
writes `/var/lib/privacy-lock/was-blocked.json` (device → mode). At boot,
before the display manager and gettys start, `privacy-lock-boot.service`
(`privacy-lock boot`) re-applies those blocks: `pin` blocks keep mode `pin`
(they survive the restart), unless `/var/lib/privacy-lock/attempts.json`
shows an active lockout; then that file is deleted and they get mode `login`
like `restart` blocks. `privacy-lock-login.service`
(`privacy-lock wait-login`) then polls logind (or elogind) every second; the first session
with class `user`, a seat, `Remote=no` and UID ≥ 1000 (logind creates it only
after PAM accepted the password; the login screen is a `greeter` session) lifts
the `login` blocks (journal: `Unblocked after login of <user>: camera, mic`),
updates `was-blocked.json` and writes
`/run/privacy-lock/boot-notice.json` (with the kernel `boot_id`). The indicator shows the notice
once per boot and records the `boot_id` in
`~/.local/state/privacy-lock/notified-boot`.

## State files

| Path | Mode | Contents |
|---|---|---|
| `/etc/privacy-lock/pin.json` | `0600` root (dir `0755`) | scrypt hash + salt (n=2^15, r=8, p=1) |
| `/run/privacy-lock/state.json` | `0644` | Blocked devices + mode, read by the indicator |
| `/var/lib/privacy-lock/attempts.json` | `0600` | Wrong-PIN count, lockout end (survives restart) |
| `/run/privacy-lock/boot-notice.json` | `0644` | Devices a restart unblocked + `boot_id` |
| `/var/lib/privacy-lock/was-blocked.json` | `0600` | Currently blocked devices → mode (survives restart) |
| `$XDG_RUNTIME_DIR/privacy-lock-login-notified` | user | Login status shown this session |

## Password checks

- **Is a password required?** `/etc/shadow` field for the user must start with
  `$` (only the format is looked at); GDM (`/etc/gdm3/custom.conf`,
  `/etc/gdm/custom.conf`), LightDM (`autologin-user=`) and SDDM
  (`[Autologin] User=`) must not auto-login; `sudo -l -U <user>` must not show
  `NOPASSWD`.
- **Proof the owner is present:** in a terminal, `runuser -u <user> -- su <user>
  -c true` (PAM asks for the user's own password); from the indicator, polkit action
  `io.github.saiftherehman.privacylock.pin` with `auth_self`, asked every time.

## polkit

| Action | Program | `allow_active` |
|---|---|---|
| `io.github.saiftherehman.privacylock.checkpin` | `privacy-lock-pin-check` | `yes` (limited by the wrong-PIN lockout) |
| `io.github.saiftherehman.privacylock.toggle` | `privacy-lock-helper` | `auth_self` (PIN rechecked) |
| `io.github.saiftherehman.privacylock.pin` | `privacy-lock-pin-helper` | `auth_self` |

The engine enforces the split too: `--gui` may only block / unblock;
`--gui-pin` may only run `setup`, `change-pin`, `reset-pin`, `remove`.

## Distribution support

| Area | Status | Notes |
|---|---|---|
| Ubuntu 26.04 GNOME Wayland | ✅ tested | Reference platform |
| Other Ubuntu flavours, Debian, Mint, Pop!_OS | 🔶 expected to work | Same paths and packages |
| Fedora | 🔶 untested | GDM config at `/etc/gdm/custom.conf` (handled). Falls back to `AppIndicator3` if Ayatana is missing. Check that SELinux allows the `/run/modprobe.d` writes and the pkexec helpers. |
| Arch / Manjaro, openSUSE | 🔶 untested | `install.sh` lists the packages. openSUSE's polkit defaults may be stricter. |
| KDE Plasma, XFCE, LXQt, Cinnamon, MATE | 🔶 untested | Tray works natively. Screen-unlock notice listens for `ActiveChanged` from every screensaver interface (`org.gnome`, `org.freedesktop`, `org.cinnamon`, `org.mate`). |
| PulseAudio instead of PipeWire | 🔶 untested | Restarts `pulseaudio.service`, or ends the process for autospawn. |
| OpenRC + elogind (Gentoo, Artix, Alpine) | 🔶 untested | `install.sh` installs the init scripts in `/etc/init.d` and the menu as an autostart entry that restarts itself (`--supervise`). |
| runit, s6, other init systems (Void, ...) | ❌ not supported | `install.sh` stops with a message. The engine itself works; only the two boot services are missing. |
| MIPI / IPU6 cameras (many 2022+ Intel laptops) | 🔶 untested | Their `/dev/video*`, `/dev/media*` and `/dev/v4l-subdev*` nodes are blocked like any camera. Only `uvcvideo` is unloaded; the IPU driver stays loaded. |
| Bluetooth headset mics | ❌ out of scope | Routed through BlueZ / PipeWire, not ALSA capture nodes. |
