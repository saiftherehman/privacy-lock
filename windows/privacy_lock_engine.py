"""
privacy_lock_engine (Windows) - the Windows side of Camera & Mic Lock.

Runs inside privacy_lock_service.py as SYSTEM. The rules (PIN, lockout, block
modes, restart) are in common/privacy_lock_core.py; this file blocks devices,
checks the account and verifies the computer password.

  camera: every present device in the Camera and Image classes is disabled
          (Disable-PnpDevice), and camera access is set to Deny for the whole
          device (CapabilityAccessManager ConsentStore). Cameras plugged in
          later are disabled by the service within a few seconds.
  mic:    every audio capture endpoint (SWD\\MMDEVAPI\\{0.0.1...}) is disabled and
          microphone access is set to Deny. Speakers are left alone.

Disabling a device ends every stream on it, so apps lose access at once. The
devices stay disabled across a restart until the service decides otherwise.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
import uuid

import pywintypes
import win32api
import win32con
import win32net
import win32security
import win32ts
import winreg

# The shared rules: installed next to this file, or in the source tree.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "common")]
import privacy_lock_core as core  # noqa: E402

DATA_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "privacy-lock")
PUBLIC_DIR = os.path.join(DATA_DIR, "public")          # Users: read only (set by install.ps1)
INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))

DISABLED_FILE = os.path.join(DATA_DIR, "disabled-devices.json")  # what we turned off
BOOT_FILE = os.path.join(PUBLIC_DIR, "boot.json")
PIN_MARKER = os.path.join(PUBLIC_DIR, "pin-set")

CONSENT_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
CONSENT_NAME = {"camera": "webcam", "mic": "microphone"}

TASK_NAME = r"\privacy-lock\privacy-lock-service"
RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "privacy-lock-tray"

CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
ERROR_ACCOUNT_RESTRICTION = 1327


# ---------------------------------------------------------------- helpers

def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def powershell(script):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def ps_quote(text):
    return "'" + text.replace("'", "''") + "'"


# Capture endpoints are SWD\MMDEVAPI\{0.0.1.*}; playback ones {0.0.0.*}.
CLASSES = {"camera": ("Camera", "Image"), "mic": ("AudioEndpoint",)}
CAPTURE_PREFIX = "SWD\\MMDEVAPI\\{0.0.1."
CM_PROB_DISABLED = 22


def enabled_devices(device):
    """Instance ids of present, enabled devices of this kind (WMI, no PowerShell,
    so the service can check every few seconds)."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
    query = ("SELECT DeviceID, ConfigManagerErrorCode FROM Win32_PnPEntity WHERE "
             + " OR ".join(f"PNPClass='{c}'" for c in CLASSES[device]))
    ids = []
    for dev in wmi.ExecQuery(query):
        if dev.ConfigManagerErrorCode == CM_PROB_DISABLED:
            continue
        if device == "mic" and not dev.DeviceID.upper().startswith(CAPTURE_PREFIX):
            continue
        ids.append(dev.DeviceID)
    return ids


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def set_consent(device, allow):
    """The system-wide privacy switch (Settings > Privacy > Camera / Microphone)."""
    key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE,
                             CONSENT_KEY + "\\" + CONSENT_NAME[device], 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
    with key:
        winreg.SetValueEx(key, "Value", 0, winreg.REG_SZ, "Allow" if allow else "Deny")


def reg_value(root, path, name):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def split_user(user):
    """'DOMAIN\\name' -> ('DOMAIN', 'name'); a bare name is a local account."""
    domain, _, name = user.rpartition("\\")
    return (domain or "."), name


def logon(user, password):
    """Ask Windows whether this is the account's password. Returns the error code or 0."""
    domain, name = split_user(user)
    try:
        handle = win32security.LogonUser(name, domain, password,
                                         win32con.LOGON32_LOGON_INTERACTIVE,
                                         win32con.LOGON32_PROVIDER_DEFAULT)
        handle.Close()
        return 0
    except pywintypes.error as e:
        return e.winerror


def boot_time():
    """When Windows started, from the uptime counter (stable to a second or two)."""
    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000.0


def is_new_boot():
    """True once per Windows start. The service also starts again after a crash
    or an update; that must not count as a restart."""
    old = load_json(BOOT_FILE, {})
    now = boot_time()
    return abs(old.get("boot_time", 0) - now) > 120


def record_boot():
    write_file(BOOT_FILE, json.dumps({"boot_time": boot_time(), "id": uuid.uuid4().hex}))


# ---------------------------------------------------------------- sessions

def console_session():
    return win32ts.WTSGetActiveConsoleSessionId()


def session_user(session):
    try:
        name = win32ts.WTSQuerySessionInformation(None, session, win32ts.WTSUserName)
        domain = win32ts.WTSQuerySessionInformation(None, session, win32ts.WTSDomainName)
    except pywintypes.error:
        return None
    return f"{domain}\\{name}" if name else None


def session_locked(session):
    """The lock screen (LogonUI.exe) is showing in this session. Windows can sign
    a user in by itself after an update and lock the session at once; that is
    not a login with the password, so it must not count."""
    for proc in win32ts.WTSEnumerateProcesses(None, 1):  # (session, pid, name, sid)
        if proc[0] == session and proc[2].lower() == "logonui.exe":
            return True
    return False


# ---------------------------------------------------------------- platform

class WindowsPlatform(core.Platform):
    computer = "PC"
    fix_hint = ("Fix it first: Settings > Accounts > Sign-in options > set a password, and turn\n"
                "off automatic sign-in (netplwiz, Autologon).")

    pin_file = os.path.join(DATA_DIR, "pin.json")
    was_blocked = os.path.join(DATA_DIR, "was-blocked.json")
    attempts_file = os.path.join(DATA_DIR, "attempts.json")
    state_file = os.path.join(PUBLIC_DIR, "state.json")
    boot_notice = os.path.join(PUBLIC_DIR, "boot-notice.json")

    interactive = False

    def __init__(self, user=None, pins=(), password=None):
        # One request from the menu or the command line: the person asking (as
        # seen by the service, never as claimed by them), their PINs, password.
        self.user = user
        self.pins = list(pins)
        self.password = password

    # -- files (the folders' permissions are set by install.ps1)
    def write_private(self, path, text):
        write_file(path, text)
        if path == self.pin_file:
            write_file(PIN_MARKER, "")

    def write_public(self, path, text):
        write_file(path, text)

    # -- devices
    def block(self, device):
        set_consent(device, allow=False)
        ids = enabled_devices(device)
        if ids:
            powershell("$ErrorActionPreference='SilentlyContinue'; " + "; ".join(
                f"Disable-PnpDevice -InstanceId {ps_quote(i)} -Confirm:$false" for i in ids))
        disabled = load_json(DISABLED_FILE, {})
        disabled[device] = sorted(set(disabled.get(device, [])) | set(ids))
        write_file(DISABLED_FILE, json.dumps(disabled))

    def unblock(self, device):
        disabled = load_json(DISABLED_FILE, {})
        ids = disabled.pop(device, [])
        if ids:
            # Only what we disabled: a device the owner turned off stays off.
            powershell("$ErrorActionPreference='SilentlyContinue'; " + "; ".join(
                f"Enable-PnpDevice -InstanceId {ps_quote(i)} -Confirm:$false" for i in ids))
        write_file(DISABLED_FILE, json.dumps(disabled))
        set_consent(device, allow=True)

    def leaks(self, device):
        found = [f"device {i}" for i in enabled_devices(device)]
        consent = reg_value(winreg.HKEY_LOCAL_MACHINE,
                            CONSENT_KEY + "\\" + CONSENT_NAME[device], "Value")
        if consent != "Deny":
            found.append(f"{CONSENT_NAME[device]} access is not set to Deny")
        return found

    # -- people and passwords
    def real_user(self):
        return self.user or session_user(console_session()) or win32api.GetUserNameEx(2)

    def security_problems(self, user):
        problems = []
        domain, name = split_user(user)
        err = logon(user, "")
        if err in (0, ERROR_ACCOUNT_RESTRICTION):
            # Logging on with an empty password worked, or Windows only refused
            # because blank passwords are limited to the console.
            problems.append(f"The account '{name}' has no password.")
        winlogon = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
        if str(reg_value(winreg.HKEY_LOCAL_MACHINE, winlogon, "AutoAdminLogon")) == "1" \
                or reg_value(winreg.HKEY_LOCAL_MACHINE, winlogon, "DefaultPassword") is not None \
                or self.lsa_autologon_secret():
            problems.append("Automatic sign-in is turned on, so the PC starts without "
                            "asking for a password.")
        if self.is_admin(name) and self.uac_silent():
            problems.append(f"'{name}' can run admin programs without being asked "
                            "(User Account Control is off or set to never notify).")
        return problems

    @staticmethod
    def lsa_autologon_secret():
        """Sysinternals Autologon keeps the password as an LSA secret."""
        try:
            policy = win32security.LsaOpenPolicy(None, win32security.POLICY_GET_PRIVATE_INFORMATION)
        except pywintypes.error:
            return False
        try:
            return bool(win32security.LsaRetrievePrivateData(policy, "DefaultPassword"))
        except pywintypes.error:
            return False
        finally:
            win32security.LsaClose(policy)

    @staticmethod
    def is_admin(name):
        try:
            groups = win32net.NetUserGetLocalGroups(None, name)
        except pywintypes.error:
            return False
        admins = win32security.LookupAccountSid(
            None, win32security.ConvertStringSidToSid("S-1-5-32-544"))[0]
        return admins in groups

    @staticmethod
    def uac_silent():
        policies = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        lua = reg_value(winreg.HKEY_LOCAL_MACHINE, policies, "EnableLUA")
        prompt = reg_value(winreg.HKEY_LOCAL_MACHINE, policies, "ConsentPromptBehaviorAdmin")
        return lua == 0 or prompt == 0

    def confirm_account_password(self):
        password, self.password = self.password, None  # used once, then forgotten
        if not password or logon(self.real_user(), password) != 0:
            raise core.Fail("Your computer password was not accepted.")

    def read_pin(self, prompt):
        if not self.pins:
            raise core.Fail("The PIN must be typed by hand in the menu or in the terminal.")
        return self.pins.pop(0)

    # -- restart
    def boot_id(self):
        return load_json(BOOT_FILE, {}).get("id", "")

    def local_login_user(self):
        session = console_session()
        if session == 0xFFFFFFFF:
            return None
        user = session_user(session)
        if not user or session_locked(session):
            return None
        return user

    # -- turn off
    def uninstall(self):
        for device in core.DEVICES:
            try:
                set_consent(device, allow=True)
            except OSError:
                pass
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except OSError:
            pass
        # The menus are told by the remove reply and close themselves. The
        # service, its task and the files go a moment later, once this reply
        # is sent (the running Python cannot delete itself).
        script = (
            "Start-Sleep -Seconds 5; "
            f"Unregister-ScheduledTask -TaskName {ps_quote(TASK_NAME.rsplit(chr(92), 1)[1])} "
            f"-TaskPath {ps_quote(TASK_NAME.rsplit(chr(92), 1)[0] + chr(92))} -Confirm:$false; "
            "$pattern = '*privacy' + '_lock_*'; "
            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like $pattern "
            "-and $_.ProcessId -ne $PID } "
            "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
            "$path = [Environment]::GetEnvironmentVariable('Path', 'Machine'); "
            "[Environment]::SetEnvironmentVariable('Path', (($path -split ';') | Where-Object "
            f"{{ $_ -and $_ -ne {ps_quote(INSTALL_DIR)} }}) -join ';', 'Machine'); "
            f"Remove-Item -Recurse -Force {ps_quote(DATA_DIR)}; "
            f"Remove-Item -Recurse -Force {ps_quote(INSTALL_DIR)}")
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive",
               "-ExecutionPolicy", "Bypass", "-Command", script]
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            # Leave the Task Scheduler's job, so stopping the service does not
            # stop the cleanup too.
            subprocess.Popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
        except OSError:
            subprocess.Popen(cmd, creationflags=flags, close_fds=True)
