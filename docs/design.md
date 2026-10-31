# Design: behavior every platform must implement

This is the platform-independent specification. The Linux, Windows and macOS
versions must all behave as described here, so a user moving between systems
sees the same tool. Platform-specific details live in
[linux.md](linux.md), [windows.md](windows.md) and [macos.md](macos.md).

## 1. Devices

Two independently lockable devices:

| Id | Name | Covers |
|---|---|---|
| `camera` | Camera | Every video-capture device, built-in and external (USB), including ones connected after the block. |
| `mic` | Microphone | Every audio-capture device, built-in and external. Audio **output** must keep working. |

`all` means both.

## 2. Blocking

- Blocking needs the same two-step check as unblocking: first the PIN, then
  the computer password (asked every time, never remembered). A wrong PIN
  ends the attempt at once; the password is only asked after a right PIN.
  Changing the PIN and turning the lock off use the same order. Exception: while the
  wrong-PIN lockout (§4) is active, blocking needs only the computer password,
  so the devices can always be blocked.
- The user chooses an **unblock mode** each time:
  - `pin`: *With my PIN*
  - `restart`: *Only by restarting the computer*
- Blocking an already-blocked device again may upgrade `pin` → `restart`, but
  never downgrades `restart` → `pin`.
- When a block starts, every program currently using the device loses it
  (stream stopped or program closed). After that, no non-administrator
  program can open the device.
- The block must be enforced by something running with administrator / root
  rights, so a normal program cannot undo it.

## 3. Unblocking

| Way | Allowed when |
|---|---|
| Computer password + PIN | Block mode is `pin`, no wrong-PIN lockout is active, and the computer is password protected (§6). |
| Restart | Block mode is `restart`, or the wrong-PIN lockout is active at boot (§5). A `pin` block otherwise stays on across restarts. |
| Turn off (§8) | Same as PIN, plus the computer password. |

## 4. PIN

- Digits only, at least 4.
- Stored only as a salted, slow hash (scrypt or the platform's equivalent),
  readable only by administrator / root. Compared in constant time.
- Must be **typed by a person**: in a terminal it is read from the terminal
  device, never from a pipe; in the GUI from a password-style field.
- **Wrong-PIN lockout:** after **5** wrong PINs in a row, PIN unlocking is
  refused (even with the right PIN) for **1 hour**. During the lockout only a
  restart unblocks. When the hour has passed, the counter resets. A correct
  PIN resets the counter. The counter survives a restart; a restart during
  the lockout clears it. This is also the way back in after forgetting the
  PIN: 5 wrong PINs, then restart within the hour.
- **Forgot PIN:** a new PIN can be set with only the computer password
  (no old PIN), but **only while nothing is blocked**, so it can never be
  used to unblock anything. It also resets the wrong-PIN counter. While
  something is blocked, it is refused with: `Forgot PIN can only be used
  while nothing is blocked. To unblock without the PIN: enter 5 wrong PINs,
  then restart the computer within the hour and log in.`
- Messages: `Wrong PIN. N attempt(s) left before PIN unlock is disabled for
  1 hour.` / `PIN unlock is disabled for another N min. Restart the computer
  to unblock now.`

## 5. Restart behavior

- At boot every block from before the restart is re-applied before the
  login screen appears.
- A `pin` block stays a `pin` block: it survives any number of restarts and
  only the PIN lifts it, unless the wrong-PIN lockout is still active at
  boot, in which case it is treated like a `restart` block.
- A `restart` block (and a `pin` block during a lockout) is lifted, but
  **only once someone logs in** with the computer password. Until that login
  (login screen, before the password is entered) it stays on. A login at the
  computer's own screen or console counts; remote (SSH) sessions do not. If
  the computer goes down again before a login, it is kept for the next boot
  too.
- If a restart lifted blocks, the user sees a notification after logging
  in: **"Camera and Microphone unblocked: The
  computer was restarted, so your camera and microphone are unblocked and
  available to apps again."** Shown once per boot.

## 6. Computer-password requirement

The computer's own login password is the backup proof that the real owner is
present. The tool **never stores or compares the password itself**; it
only checks that one is required.

The computer counts as **not password protected** if any of:

- the user's account has no password (or password login is disabled);
- automatic login is turned on;
- administrator commands can run without a password (e.g. `NOPASSWD` sudo).

While not protected: blocking still works; **setup, change PIN, unblock and
turn off are refused** with this warning:

> WARNING: this computer is not password protected. … Camera & Mic Lock uses
> your computer password as a backup check that you are the real owner.
> Without it, anyone at this computer could change the PIN or unblock the
> camera and microphone. Fix it first: set a password and turn off automatic
> login.

The installer runs the check and shows the same warning.

## 7. Actions that ask for the computer password

| Action | Computer password | PIN |
|---|---|---|
| Block | ✔ | ✔ (not during the wrong-PIN lockout, §2) |
| Unblock | ✔ | ✔ |
| Set up PIN | ✔ (every time, never remembered) | new ×2 |
| Change PIN | ✔ | old, then new ×2 |
| Forgot PIN (nothing blocked) | ✔ | new ×2 |
| Turn off | ✔ | ✔ |

The operating system checks the password, never the tool:

- **Linux:** polkit (menu) or PAM through `su` (terminal) asks for it, so
  the tool never sees it at all.
- **Windows and macOS:** the menu asks for it (the Windows Security dialog on
  Windows, a standard macOS dialog on a Mac) and hands it once, over a local
  connection only the system can own, to the privileged service, which asks
  the system whether it is right (`LogonUser` / Open Directory) and then
  forgets it. It is never written to disk or to a log.

## 8. Turning off

"Turn off Camera & Mic Lock" = unblock everything, delete the PIN and all
state, uninstall every component, and notify the user. Refused while a
`restart` block or a wrong-PIN lockout is active ("Restart first").

## 9. User interface

- **Indicator** in the top bar / system tray / menu bar. Icon reflects state:
  nothing blocked · camera blocked · mic blocked · both blocked.
- **No Quit item.** The indicator starts at every login and is restarted
  automatically if it exits. It can also be found by name in the app launcher.
- **Menu**, top to bottom:
  1. Status lines (not clickable): `Camera: ON` / `Camera: BLOCKED (PIN to
     unlock)` / `Camera: BLOCKED (until restart)` / `Camera: BLOCKED (until
     login)`, same for Microphone.
  2. If no PIN is set: only **Set up PIN…**.
  3. Otherwise: **Block …** items for unblocked devices (each device, and
     both); **Unblock …** items for `pin`-mode blocks; a note "Restart the
     computer to unblock the rest" if any `restart` block exists;
     **Change PIN…**; **Forgot PIN…**; **Turn off Camera & Mic Lock…**.
- **Forgot PIN dialog:** while something is blocked, it only explains the
  way back in (the refusal text in §4); otherwise new PIN ×2, then the
  computer password.
- **Block dialog:** "How should the … be unblocked later?" with the two modes.
- **Notifications:**
  - after blocking / unblocking / PIN change;
  - at **every login** and **every screen unlock**: current status
    ("Camera and microphone are blocked", with each device's mode, or "…are
    unblocked");
  - after a restart lifted blocks (§5), instead of the login status that time;
  - after turning off.
- The indicator itself has no privileges; it asks a privileged component to
  act.

## 10. Command line

Where the platform has a natural command line, it provides:

```text
privacy-lock setup | change-pin | reset-pin | check | remove
privacy-lock <camera|mic|all> off [--restart-only]
privacy-lock <camera|mic|all> on
privacy-lock status [--json]
```

Exit codes: `0` ok · `1` error · `3` wrong PIN · `4` lockout active ·
`5` restart-only block.

## 11. Security model

**In scope:** normal (non-administrator) programs, including malware running
as the user; a person at the keyboard who knows neither the PIN nor the
computer password.

**Out of scope (documented, not defended):**

- Administrator / root code, or a person who knows the administrator password.
- Restarting the computer during a wrong-PIN lockout, or with a `restart`
  block (unblocks by design; the login password protects what comes
  after).
- Hardware attacks, firmware, kernel exploits.
- Devices the operating system does not expose as capture devices (e.g.
  Bluetooth headset mics on Linux, which bypass ALSA).

**Known trade-off:** the indicator checks the PIN before asking for the
computer password, and that first check needs no password, so a program
running as the user could submit PIN guesses through it. Unblocking still
needs the computer password as well, and 5 wrong guesses trigger the 1-hour
lockout (which a program could also trigger on purpose to annoy the user, or
to make a restart lift a PIN block, which is still behind the login password).
