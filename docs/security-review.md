# Security review (Linux, 14 Nov 2026)

Review of the installed Linux version: everything that runs as root or
decides who may do what (`privacy-lock`, the three pkexec helpers, the
polkit policy, the services, `privacy_lock_core.py`, the tray's pkexec
calls).

## Result

No way found for another program or a remote attacker to get access to the
machine, or to unblock the camera or microphone, through this program.

- No network listener and no network use.
- No shell commands built from text (`subprocess` with argument lists only).
- All installed files are owned by root and not writable by the user.
- PIN stored as a salted scrypt hash in `/etc/privacy-lock/pin.json`
  (root-only, `0600`), compared with `hmac.compare_digest`.
- 5 wrong PINs → PIN unlock disabled for 1 hour.
- Block / unblock / PIN changes need the computer password (polkit
  `auth_self`, never remembered) and the PIN.
- Each pkexec helper accepts only a fixed list of commands; the engine
  refuses tray flags that do not match the command.
- The engine refuses to run when the account has no password, sudo has a
  `NOPASSWD` rule, or automatic login is on.

## Minor points (not ways in)

1. **The PIN check needs no password.** `privacy-lock-pin-check` is allowed
   without a password on purpose, so a wrong PIN is refused before the
   password prompt. Side effect: a program running as the user could use up
   the 5 tries and disable PIN unlock for an hour. A restart clears it.
   Nuisance, not access.
2. **The source folder is user-writable.** The cloned repository you
   install from can be changed by anything running as the user. Malware
   could edit it and wait for the next `sudo` install. Before reinstalling,
   check for unexpected changes with `git status` and `git diff` in the
   clone.
3. **Bluetooth headset microphones are not blocked.** They go through BlueZ
   and PipeWire, not ALSA capture nodes. Turn Bluetooth off when full
   privacy is needed.
4. **The PIN can be as short as 4 digits** (`MIN_PIN_LEN`). Use 6 or more
   (tray → Change PIN).

## Change made during the review

Locking the mic also silenced the speakers: PipeWire hides the whole sound
card, speakers included, while any of its `/dev/snd/pcmC*D*` nodes is
inaccessible. Locking now removes the capture nodes (`pcmC*D*c`) instead of
only setting them to mode `0000`; unlocking recreates them from
`/sys/class/sound/pcmC*D*c/dev`. See [linux.md](linux.md), "How the
microphone is blocked".

Tested on Ubuntu 26.04 with PipeWire 1.6.2: locked, the
mic node is gone, there is no audio input and the speakers keep playing;
unlocked, the node is back with the same owner, mode and session ACL as
before, and recording works.
