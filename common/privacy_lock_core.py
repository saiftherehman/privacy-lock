"""
privacy_lock_core - the platform-independent part of Camera & Mic Lock.

Implements every rule of docs/design.md once: the PIN (hash, check, wrong-PIN
lockout), the two block modes, what a restart does, Forgot PIN and Turn off.
Each platform engine (linux/privacy-lock, windows/privacy_lock_engine.py,
macos/privacy_lock_engine.py) supplies a Platform object that does the actual
blocking, reads PINs and checks the computer password, then calls main().
"""

import hashlib
import hmac
import json
import os
import secrets
import sys
import time

DEVICES = ("camera", "mic")
NAMES = {"camera": "Camera", "mic": "Microphone"}

MIN_PIN_LEN = 4
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 3600

# Exit codes, so the menu can tell failures apart.
EXIT_ERROR = 1
EXIT_WRONG_PIN = 3
EXIT_LOCKED_OUT = 4
EXIT_RESTART_ONLY = 5

COMMANDS_USAGE = """\
  privacy-lock setup                     set the PIN (first run)
  privacy-lock change-pin                change the PIN (needs the old PIN)
  privacy-lock reset-pin                 forgot the PIN: set a new one with only
                                         the computer password (only while
                                         nothing is blocked)
  privacy-lock check                     check that the computer is password protected
  privacy-lock remove                    turn the lock off and uninstall it
                                         (needs the computer password and the PIN)
  privacy-lock <camera|mic|all> off      block (unlock with the PIN)
  privacy-lock <camera|mic|all> off --restart-only
                                         block (unlock ONLY by restarting)
  privacy-lock <camera|mic|all> on       unblock (asks for the PIN)
  privacy-lock status [--json]           show the current state"""


class Fail(Exception):
    """A command failed: message for the user plus an exit code."""

    def __init__(self, msg, code=EXIT_ERROR):
        super().__init__(msg)
        self.code = code


def targets_label(devices):
    return " and ".join(NAMES[d] for d in devices)


def parse_devices(word):
    if word == "all":
        return list(DEVICES)
    if word in ("camera", "cam"):
        return ["camera"]
    if word in ("mic", "microphone"):
        return ["mic"]
    return None


def forgot_pin_blocked(computer):
    return ("Forgot PIN can only be used while nothing is blocked.\n"
            f"To unblock without the PIN: enter 5 wrong PINs, then restart the {computer} within\n"
            "the hour and log in.")


def not_protected_warning(problems, computer, fix_hint):
    return ("WARNING: this computer is not password protected.\n"
            + "".join(f"  - {p}\n" for p in problems)
            + "\nCamera & Mic Lock uses your computer password as a backup check that you are\n"
              f"the real owner. Without it, anyone at this {computer} could change the PIN or\n"
              "unblock the camera and microphone.\n\n"
            + fix_hint + " Until then you can block the camera and microphone, but you\n"
              "cannot set up or change the PIN or unblock with the PIN.")


def restart_notice(devices, computer):
    """Title and text of the notification after a restart lifted blocks."""
    verb = "are" if len(devices) > 1 else "is"
    return (f"{targets_label(devices)} unblocked",
            f"The {computer} was restarted, so your {targets_label(devices).lower()} "
            f"{verb} unblocked and available to apps again.")


class Platform:
    """What each operating system provides. Paths are absolute file paths."""

    computer = "computer"          # "PC" / "Mac", used in messages
    fix_hint = "Fix it first: set a password and turn off automatic login."

    pin_file = state_file = boot_notice = was_blocked = attempts_file = None

    # -- files
    def write_private(self, path, text):
        """Write a file only the administrator / root can read."""
        raise NotImplementedError

    def write_public(self, path, text):
        """Write a file everyone can read but only the administrator can change."""
        raise NotImplementedError

    def remove(self, path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    # -- devices
    def block(self, device):
        raise NotImplementedError

    def unblock(self, device):
        raise NotImplementedError

    def leaks(self, device):
        """Descriptions of ways the blocked device is still reachable."""
        return []

    def after_change(self, extra_users=()):
        """Called after devices were blocked or unblocked (e.g. restart audio)."""

    # -- people and passwords
    def real_user(self):
        raise NotImplementedError

    def security_problems(self, user):
        raise NotImplementedError

    def confirm_account_password(self):
        """Raise Fail unless the user's own computer password was given."""
        raise NotImplementedError

    def read_pin(self, prompt):
        raise NotImplementedError

    def say(self, text):
        """A progress line for a person at a terminal (not for the menu)."""

    # -- restart
    def boot_id(self):
        raise NotImplementedError

    def local_login_user(self):
        """Someone logged in at this computer's own screen (not remotely), or None."""
        raise NotImplementedError

    # -- turn off
    def uninstall(self):
        raise NotImplementedError

    def notify_all(self, title, body):
        pass


class Engine:
    def __init__(self, platform):
        self.p = platform

    # ------------------------------------------------------------ files

    @staticmethod
    def load_json(path, default):
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def load_state(self):
        return self.load_json(self.p.state_file, {})

    def save_state(self, state):
        self.p.write_public(self.p.state_file, json.dumps(state))
        # Remember across a restart which devices were blocked, and how.
        if state:
            self.p.write_private(self.p.was_blocked,
                                 json.dumps({d: e["mode"] for d, e in sorted(state.items())}))
        else:
            self.p.remove(self.p.was_blocked)

    def pin_set(self):
        return os.path.exists(self.p.pin_file)

    # ------------------------------------------------------------ password protection

    def require_password_protection(self):
        problems = self.p.security_problems(self.p.real_user())
        if problems:
            raise Fail(not_protected_warning(problems, self.p.computer, self.p.fix_hint))

    # ------------------------------------------------------------ PIN

    @staticmethod
    def hash_pin(pin, salt):
        return hashlib.scrypt(pin.encode(), salt=salt, n=2**15, r=8, p=1,
                              maxmem=64 * 1024 * 1024).hex()

    def set_new_pin(self):
        while True:
            pin = self.p.read_pin(f"New PIN (digits, min {MIN_PIN_LEN}): ")
            if not pin.isdigit() or len(pin) < MIN_PIN_LEN:
                msg = f"PIN must be at least {MIN_PIN_LEN} digits."
            elif self.p.read_pin("Repeat PIN: ") != pin:
                msg = "PINs do not match."
            else:
                break
            if not getattr(self.p, "interactive", False):
                raise Fail(msg)  # the menu sends each PIN once; no retry loop
            self.p.say(msg + " Try again.")
        salt = secrets.token_bytes(16)
        self.p.write_private(self.p.pin_file,
                             json.dumps({"salt": salt.hex(), "hash": self.hash_pin(pin, salt)}))
        print("PIN saved.")

    def pin_locked_out(self):
        return self.load_json(self.p.attempts_file, {"locked_until": 0})["locked_until"] > time.time()

    def save_attempts(self, tries):
        self.p.write_private(self.p.attempts_file, json.dumps(tries))

    def verify_pin(self):
        stored = self.load_json(self.p.pin_file, None)
        if not stored:
            raise Fail("No PIN set yet. Run: privacy-lock setup")
        tries = self.load_json(self.p.attempts_file, {"fails": 0, "locked_until": 0})

        wait = tries["locked_until"] - time.time()
        if wait > 0:
            raise Fail(f"Too many wrong PINs. PIN unlock is disabled for another "
                       f"{int(wait // 60) + 1} min.\nRestart the {self.p.computer} to unblock now.",
                       EXIT_LOCKED_OUT)
        if tries["locked_until"]:
            tries = {"fails": 0, "locked_until": 0}  # lockout expired

        pin = self.p.read_pin("PIN: ")
        ok = hmac.compare_digest(self.hash_pin(pin, bytes.fromhex(stored["salt"])), stored["hash"])
        if ok:
            self.save_attempts({"fails": 0, "locked_until": 0})
            return

        tries["fails"] += 1
        if tries["fails"] >= MAX_ATTEMPTS:
            tries["locked_until"] = time.time() + LOCKOUT_SECONDS
            self.save_attempts(tries)
            raise Fail(f"Wrong PIN. That was attempt {MAX_ATTEMPTS} of {MAX_ATTEMPTS}: "
                       f"PIN unlock is disabled for 1 hour.\nRestart the {self.p.computer} "
                       f"to unblock now.", EXIT_LOCKED_OUT)
        self.save_attempts(tries)
        left = MAX_ATTEMPTS - tries["fails"]
        raise Fail(f"Wrong PIN. {left} attempt(s) left before PIN unlock is disabled for 1 hour.",
                   EXIT_WRONG_PIN)

    # ------------------------------------------------------------ commands

    def cmd_setup(self):
        if self.pin_set():
            raise Fail("PIN already set. Use: privacy-lock change-pin")
        self.require_password_protection()
        self.p.confirm_account_password()
        self.set_new_pin()

    def cmd_change_pin(self):
        if not self.pin_set():
            raise Fail("No PIN set yet. Run: privacy-lock setup")
        self.require_password_protection()
        self.p.say("Current PIN required.")
        self.verify_pin()
        self.p.confirm_account_password()
        self.set_new_pin()

    def cmd_reset_pin(self):
        """Forgot the PIN: set a new one with only the computer password. Refused
        while anything is blocked, so it can never be used to unblock."""
        if not self.pin_set():
            raise Fail("No PIN set yet. Run: privacy-lock setup")
        if self.load_state():
            raise Fail(forgot_pin_blocked(self.p.computer))
        self.require_password_protection()
        self.p.confirm_account_password()
        self.set_new_pin()
        self.p.remove(self.p.attempts_file)  # a fresh PIN starts with a fresh counter

    def cmd_block(self, devices, restart_only):
        if not self.pin_set():
            raise Fail("Set a PIN first: privacy-lock setup")
        # Never let a wrong-PIN lockout stop anyone from blocking.
        if not self.pin_locked_out():
            self.p.say("PIN required.")
            self.verify_pin()
        self.p.confirm_account_password()
        self.block(devices, restart_only)

    def block(self, devices, restart_only):
        state = self.load_state()
        mode = "restart" if restart_only else "pin"
        for d in devices:
            self.p.block(d)
            # A restart-only block is never weakened to a PIN block.
            if state.get(d, {}).get("mode") != "restart":
                state[d] = {"mode": mode, "since": int(time.time())}
        self.save_state(state)
        self.p.after_change()
        for d in devices:
            how = "restart only" if state[d]["mode"] == "restart" else "PIN"
            print(f"{NAMES[d]}: OFF (unlock: {how})")

    def cmd_unblock(self, devices):
        if not self.pin_set():
            raise Fail("Set a PIN first: privacy-lock setup")
        state = self.load_state()
        devices = [d for d in devices if d in state]
        if not devices:
            print("Already unblocked.")
            return
        restart_only = [d for d in devices if state[d]["mode"] == "restart"]
        if restart_only:
            raise Fail(f"{targets_label(restart_only)} can only be unblocked by restarting "
                       f"the {self.p.computer}.", EXIT_RESTART_ONLY)
        self.require_password_protection()
        self.p.say("PIN required.")
        self.verify_pin()
        self.p.confirm_account_password()
        for d in devices:
            self.p.unblock(d)
            del state[d]
        self.save_state(state)
        self.p.after_change()
        for d in devices:
            print(f"{NAMES[d]}: ON")

    def status(self):
        state = self.load_state()
        out = {}
        for d in DEVICES:
            entry = state.get(d)
            out[d] = {
                "blocked": entry is not None,
                "mode": entry["mode"] if entry else None,
                "leaky": self.p.leaks(d) if entry else [],
            }
        return out

    def cmd_status(self, as_json):
        st = self.status()
        if as_json:
            st["pin_set"] = self.pin_set()
            print(json.dumps(st))
            return
        for d in DEVICES:
            s = st[d]
            label = f"{NAMES[d] + ':':12}"
            if not s["blocked"]:
                print(f"{label}ON")
            elif s["leaky"]:
                print(f"{label}OFF, but still accessible: {' '.join(s['leaky'])} "
                      f"- run the 'off' command again")
            else:
                how = {"restart": "restart only", "login": "login"}.get(s["mode"], "PIN")
                print(f"{label}OFF (unlock: {how})")
        if not self.pin_set():
            print("PIN not set yet. Run: privacy-lock setup")

    def cmd_check(self):
        problems = self.p.security_problems(self.p.real_user())
        print("\n".join(problems) if problems else "OK: this computer is password protected.")
        return 1 if problems else 0

    def cmd_check_pin(self):
        """Only checks the PIN (same counter and lockout); the real command checks it again."""
        self.verify_pin()

    def cmd_boot(self):
        """Once per boot, before anyone can log in: re-apply the last session's
        blocks. PIN blocks stay as they are unless the wrong-PIN lockout is active;
        the others get mode "login" and are lifted when someone logs in."""
        locked_out = self.pin_locked_out()
        if locked_out:
            self.p.remove(self.p.attempts_file)  # a restart clears the lockout
        was = self.load_json(self.p.was_blocked, {})
        if isinstance(was, list):  # written by the first Linux versions: PIN blocks
            was = {d: "pin" for d in was}
        was = {d: m for d, m in was.items() if d in DEVICES}
        if not was:
            self.p.remove(self.p.was_blocked)
            self.p.write_public(self.p.state_file, "{}")
            return
        state = {}
        for d, mode in was.items():
            self.p.block(d)
            mode = "pin" if mode == "pin" and not locked_out else "login"
            state[d] = {"mode": mode, "since": int(time.time())}
        self.save_state(state)  # also keeps was_blocked, in case it goes down before a login
        for mode, label in (("pin", "Kept blocked (PIN to unblock)"),
                            ("login", "Kept blocked until login")):
            devs = [d for d in DEVICES if state.get(d, {}).get("mode") == mode]
            if devs:
                print(f"{label}: {', '.join(devs)}")

    def waiting_for_login(self):
        return any(e["mode"] == "login" for e in self.load_state().values())

    def lift_after_login(self, user):
        """Lift the "login" blocks; called once someone logged in locally."""
        state = self.load_state()
        lifted = [d for d in DEVICES if state.get(d, {}).get("mode") == "login"]
        if not lifted:
            return []
        for d in lifted:
            self.p.unblock(d)
            del state[d]
        self.save_state(state)
        print(f"Unblocked after login of {user}: {', '.join(lifted)}")
        self.p.after_change(extra_users=[user])
        self.p.write_public(self.p.boot_notice,
                            json.dumps({"unblocked": lifted, "boot_id": self.p.boot_id()}))
        return lifted

    def cmd_wait_login(self, poll=1.0):
        """After boot: wait for a local login, then lift the "login" blocks."""
        while self.waiting_for_login():
            user = self.p.local_login_user()
            if user is None:
                time.sleep(poll)
                continue
            self.lift_after_login(user)
            return

    def cmd_remove(self):
        """Turn the whole setup off: needs the computer password AND the PIN."""
        state = self.load_state()
        if any(e["mode"] == "restart" for e in state.values()):
            raise Fail(f"A restart-only block is active. Restart the {self.p.computer} first, "
                       "then turn the lock off.", EXIT_RESTART_ONLY)
        self.require_password_protection()
        if self.pin_set():
            self.p.say("PIN required.")
            self.verify_pin()
        self.p.confirm_account_password()
        for d in state:
            self.p.unblock(d)
        if state:
            self.p.after_change()
        self.p.uninstall()
        self.p.notify_all("Camera & Mic Lock turned off",
                          "The PIN was removed and the camera and microphone are unblocked.")
        print("Camera & Mic Lock is turned off and removed. Camera and microphone are unblocked.")

    # ------------------------------------------------------------ dispatch

    def run(self, args):
        """Run one command (without the program name). Returns an exit code;
        raises Fail for errors. Unknown commands raise ValueError."""
        if not args:
            raise ValueError("no command")
        cmd, rest = args[0], args[1:]
        if cmd == "status":
            self.cmd_status("--json" in rest)
        elif cmd == "check":
            return self.cmd_check()
        elif cmd == "setup" and not rest:
            self.cmd_setup()
        elif cmd == "change-pin" and not rest:
            self.cmd_change_pin()
        elif cmd == "reset-pin" and not rest:
            self.cmd_reset_pin()
        elif cmd == "check-pin" and not rest:
            self.cmd_check_pin()
        elif cmd == "remove" and not rest:
            self.cmd_remove()
        elif cmd == "boot" and not rest:
            self.cmd_boot()
        elif cmd == "wait-login" and not rest:
            self.cmd_wait_login()
        elif parse_devices(cmd) and rest[:1] == ["off"] and rest[1:] in ([], ["--restart-only"]):
            self.cmd_block(parse_devices(cmd), restart_only=bool(rest[1:]))
        elif parse_devices(cmd) and rest == ["on"]:
            self.cmd_unblock(parse_devices(cmd))
        else:
            raise ValueError(f"unknown command: {' '.join(args)}")
        return 0


def usage(code=2):
    print(COMMANDS_USAGE)
    sys.exit(code)


def main(platform, args):
    """Run a command and exit with its code (the CLI entry point of every engine)."""
    if not args or args[0] in ("-h", "--help", "help"):
        usage(0)
    try:
        code = Engine(platform).run(args)
    except Fail as e:
        print(e, file=sys.stderr)
        sys.exit(e.code)
    except ValueError:
        usage()
    sys.exit(code)
