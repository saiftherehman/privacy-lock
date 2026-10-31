"""
privacy_lock_engine (macOS) - the macOS side of Camera & Mic Lock.

Runs inside privacy_lock_daemon.py as root. The rules (PIN, lockout, block
modes, restart) are in common/privacy_lock_core.py.

IMPORTANT LIMIT: macOS does not let any third-party tool switch the camera or
microphone off system-wide while System Integrity Protection is on. So on a
Mac a block means "stopped within about half a second", not "cannot be
opened":

  camera: while blocked, the daemon checks twice a second whether any camera
          is running; if so it ends the system camera services (VDCAssistant,
          AppleCameraAssistant, UVCAssistant, appleh13camerad, cameracaptured),
          which stops every app's video, and tells the user.
  mic:    while blocked, every input device is muted with its volume at 0
          (re-applied if anything changes it), and on macOS 14 and newer any
          process that starts recording is ended.
"""

import json
import os
import plistlib
import pwd
import shutil
import signal
import subprocess
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "common")]
import privacy_lock_core as core  # noqa: E402

PRIVATE_DIR = "/Library/Application Support/privacy-lock/data"   # root only (0700)
PUBLIC_DIR = "/var/run/privacy-lock"                            # emptied at every start
INSTALL_DIR = "/Library/Application Support/privacy-lock"
SOCKET = "/var/run/privacy-lock.sock"
DAEMON_LABEL = "io.github.saiftherehman.privacylock.daemon"
DAEMON_PLIST = f"/Library/LaunchDaemons/{DAEMON_LABEL}.plist"
AGENT_PLIST = "/Library/LaunchAgents/io.github.saiftherehman.privacylock.menu.plist"

VOLUMES_FILE = os.path.join(PRIVATE_DIR, "mic-volumes.json")
BOOT_FILE = os.path.join(PUBLIC_DIR, "boot.json")
PIN_MARKER = os.path.join(PUBLIC_DIR, "pin-set")
STOPPED_FILE = os.path.join(PUBLIC_DIR, "stopped.json")   # when an app was last stopped

CAMERA_SERVICES = ["VDCAssistant", "AppleCameraAssistant", "UVCAssistant",
                   "appleh13camerad", "cameracaptured"]
# Never ended for recording: the audio system itself.
KEEP_RUNNING = {"coreaudiod", "audiomxd"}


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", "")


def write_file(path, text, mode):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def boot_id():
    """Seconds of the kernel's boot time: the same for the whole time the Mac is on."""
    out = run(["sysctl", "-n", "kern.boottime"]).stdout  # { sec = 1730360000, usec = 1 } ...
    return out.split("sec =", 1)[1].split(",", 1)[0].strip() if "sec =" in out else ""


def console_user():
    """The user at the Mac's own screen, or None at the login window."""
    try:
        uid = os.stat("/dev/console").st_uid
    except OSError:
        return None
    if uid == 0:
        return None
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None


def password_matches(user, password):
    """Ask Open Directory whether this is the user's password (never stored)."""
    from OpenDirectory import ODNode, ODSession, kODNodeTypeAuthentication, kODRecordTypeUsers
    node, err = ODNode.nodeWithSession_type_error_(ODSession.defaultSession(),
                                                   kODNodeTypeAuthentication, None)
    if node is None:
        return False
    record, err = node.recordWithRecordType_name_attributes_error_(kODRecordTypeUsers, user,
                                                                   None, None)
    if record is None:
        return False
    ok, err = record.verifyPassword_error_(password, None)
    return bool(ok)


# ---------------------------------------------------------------- blocking

def stop_camera():
    """End the camera services; every app's video stops. launchd starts them
    again on demand, and the daemon ends them again while blocked."""
    for name in CAMERA_SERVICES:
        run(["killall", "-9", name])


def stop_recorders(seen):
    """End processes recording now (macOS 14+). `seen` holds PIDs already asked
    to stop: they get SIGKILL. Returns the names that were stopped."""
    from privacy_lock_audio import recording_pids
    stopped = []
    for pid in recording_pids():
        if pid in (os.getpid(), 0, 1):
            continue
        name = run(["ps", "-p", str(pid), "-o", "comm="]).stdout.strip().rsplit("/", 1)[-1]
        if not name or name in KEEP_RUNNING:
            continue
        try:
            os.kill(pid, signal.SIGKILL if pid in seen else signal.SIGTERM)
            seen.add(pid)
            stopped.append(name)
        except ProcessLookupError:
            seen.discard(pid)
    return stopped


# ---------------------------------------------------------------- platform

class MacPlatform(core.Platform):
    computer = "Mac"
    fix_hint = ("Fix it first: System Settings > Users & Groups: set a password and set\n"
                "\"Automatically log in as\" to Off.")

    pin_file = os.path.join(PRIVATE_DIR, "pin.json")
    was_blocked = os.path.join(PRIVATE_DIR, "was-blocked.json")
    attempts_file = os.path.join(PRIVATE_DIR, "attempts.json")
    state_file = os.path.join(PUBLIC_DIR, "state.json")
    boot_notice = os.path.join(PUBLIC_DIR, "boot-notice.json")

    interactive = False

    def __init__(self, user=None, pins=(), password=None):
        self.user = user
        self.pins = list(pins)
        self.password = password

    # -- files
    def write_private(self, path, text):
        os.makedirs(PRIVATE_DIR, mode=0o700, exist_ok=True)
        write_file(path, text, 0o600)
        if path == self.pin_file:
            self.write_public(PIN_MARKER, "")

    def write_public(self, path, text):
        os.makedirs(PUBLIC_DIR, exist_ok=True)
        os.chmod(PUBLIC_DIR, 0o755)
        write_file(path, text, 0o644)

    # -- devices: the daemon's watch loop does the continuous part
    def block(self, device):
        if device == "camera":
            stop_camera()
        else:
            from privacy_lock_audio import silence_inputs
            saved = load_json(VOLUMES_FILE, {})
            for dev, before in silence_inputs().items():
                saved.setdefault(str(dev), before)  # keep the first, real values
            write_file(VOLUMES_FILE, json.dumps(saved), 0o600)
            stop_recorders(set())

    def unblock(self, device):
        if device == "mic":
            from privacy_lock_audio import restore_inputs
            restore_inputs(load_json(VOLUMES_FILE, {}))
            self.remove(VOLUMES_FILE)

    def leaks(self, device):
        from privacy_lock_audio import camera_running, inputs_silenced, mic_running
        if device == "camera":
            return ["a camera is running right now"] if camera_running() else []
        found = [] if inputs_silenced() else ["an input is not muted"]
        if mic_running():
            found.append("a microphone is running right now")
        return found

    # -- people and passwords
    def real_user(self):
        return self.user or console_user() or "root"

    def security_problems(self, user):
        problems = []
        if user != "root":
            try:
                if password_matches(user, ""):
                    problems.append(f"The account '{user}' has no password.")
            except Exception:
                pass
            if "NOPASSWD" in run(["sudo", "-l", "-U", user]).stdout:
                problems.append(f"'{user}' can run admin (sudo) commands without a password.")
        try:
            with open("/Library/Preferences/com.apple.loginwindow.plist", "rb") as f:
                auto = plistlib.load(f).get("autoLoginUser")
        except (OSError, plistlib.InvalidFileException):
            auto = None
        if auto or os.path.exists("/etc/kcpassword"):
            problems.append("Automatic login is turned on, so the Mac starts without "
                            "asking for a password.")
        return problems

    def confirm_account_password(self):
        password, self.password = self.password, None  # used once, then forgotten
        if not password or not password_matches(self.real_user(), password):
            raise core.Fail("Your computer password was not accepted.")

    def read_pin(self, prompt):
        if not self.pins:
            raise core.Fail("The PIN must be typed by hand in the menu or in the terminal.")
        return self.pins.pop(0)

    # -- restart
    def boot_id(self):
        return boot_id()

    def local_login_user(self):
        return console_user()

    # -- turn off
    def uninstall(self):
        user = console_user()
        if user:
            run(["launchctl", "bootout", f"gui/{pwd.getpwnam(user).pw_uid}/io.github.saiftherehman.privacylock.menu"])
        run(["pkill", "-f", "privacy_lock_tray.py"])
        for path in (AGENT_PLIST, SOCKET):
            self.remove(path)
        shutil.rmtree(PUBLIC_DIR, ignore_errors=True)
        shutil.rmtree(PRIVATE_DIR, ignore_errors=True)
        run(["rm", "-f", "/usr/local/bin/privacy-lock"])
        # The daemon cannot remove itself while it runs: a moment later,
        # unload it and delete the program files (AbandonProcessGroup in the
        # plist lets this outlive the daemon).
        subprocess.Popen(
            ["/bin/sh", "-c",
             f"sleep 3; rm -f '{DAEMON_PLIST}'; launchctl bootout system/{DAEMON_LABEL}; "
             f"rm -rf '{INSTALL_DIR}'"],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
