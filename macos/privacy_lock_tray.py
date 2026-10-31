"""
privacy_lock_tray (macOS) - the lock icon in the menu bar.

Runs as the logged-in user, without privileges. A LaunchAgent starts it at
every login and starts it again if it is closed (KeepAlive). Menu logic and
dialog order are shared with Windows (common/privacy_lock_menu.py); the
dialogs are standard macOS ones, shown with osascript.
"""

import json
import os
import subprocess
import sys
import threading
import time

import pystray
from PIL import Image, ImageDraw

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "common")]
import privacy_lock_core as core  # noqa: E402
import privacy_lock_menu as menu  # noqa: E402
from privacy_lock_client import Client  # noqa: E402

PUBLIC_DIR = "/var/run/privacy-lock"
STATE_FILE = os.path.join(PUBLIC_DIR, "state.json")
BOOT_NOTICE = os.path.join(PUBLIC_DIR, "boot-notice.json")
BOOT_FILE = os.path.join(PUBLIC_DIR, "boot.json")
PIN_MARKER = os.path.join(PUBLIC_DIR, "pin-set")
STOPPED_FILE = os.path.join(PUBLIC_DIR, "stopped.json")
STATE_HOME = os.path.expanduser("~/Library/Application Support/privacy-lock")
NOTIFIED_FILE = os.path.join(STATE_HOME, "notified-boot")
LOGIN_MARKER = os.path.join(STATE_HOME, "notified-login")
COMPUTER = "Mac"


# ---------------------------------------------------------------- icons

def draw_icon(camera_blocked, mic_blocked):
    """Open lock, crossed-out camera, crossed-out mic, or closed lock (black,
    like the other menu-bar icons)."""
    img = Image.new("RGBA", (44, 44), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = (0, 0, 0, 255)
    if camera_blocked == mic_blocked:
        d.rounded_rectangle((8, 20, 36, 40), 4, fill=ink)
        if camera_blocked:
            d.arc((13, 4, 31, 28), 180, 360, fill=ink, width=4)
        else:
            d.arc((13, 1, 31, 25), 180, 330, fill=ink, width=4)
        return img
    if camera_blocked:
        d.rounded_rectangle((4, 14, 30, 34), 4, fill=ink)
        d.polygon([(30, 21), (40, 15), (40, 33), (30, 27)], fill=ink)
    else:
        d.rounded_rectangle((15, 4, 29, 26), 7, fill=ink)
        d.arc((9, 13, 35, 33), 0, 180, fill=ink, width=3)
        d.line((22, 33, 22, 40), fill=ink, width=3)
    d.line((5, 5, 39, 39), fill=(0, 0, 0, 0), width=7)
    d.line((5, 5, 39, 39), fill=ink, width=3)
    return img


ICONS = {(c, m): draw_icon(c, m) for c in (False, True) for m in (False, True)}


# ---------------------------------------------------------------- dialogs

def osascript(lines, *args):
    """Run AppleScript with the texts passed as arguments (never pasted into
    the script). Returns stdout, or None if the user cancelled."""
    cmd = ["osascript", "-e", "on run argv"]
    for line in lines:
        cmd += ["-e", line]
    cmd += ["-e", "end run", *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


class MacUI:
    def __init__(self, tray):
        self.tray = tray

    def ask_text(self, title, label, caution=False):
        return osascript([
            'display dialog (item 2 of argv) with title (item 1 of argv) default answer "" '
            'with hidden answer buttons {"Cancel", "OK"} default button "OK" '
            f'with icon {"caution" if caution else "note"}',
            "return text returned of result"], title, label)

    def ask_pins(self, title, labels):
        pins = []
        for label in labels:
            value = self.ask_text(title, label)
            if not value:
                return None
            pins.append(value)
        return pins

    def ask_block_mode(self, title, question, restart_label):
        button = osascript([
            'display dialog (item 2 of argv) with title (item 1 of argv) '
            'buttons {"Cancel", item 3 of argv, "With my PIN"} default button "With my PIN" '
            'cancel button "Cancel"',
            "return button returned of result"], title, question, restart_label)
        if button is None:
            return None
        return "pin" if button == "With my PIN" else "restart"

    def ask_password(self, reason):
        return self.ask_text("Camera & Mic Lock",
                             f"Enter your Mac login password to {reason}:", caution=True)

    def info(self, title, text):
        osascript(['display dialog (item 2 of argv) with title (item 1 of argv) '
                   'buttons {"OK"} default button "OK"'], title, text)

    def error(self, text):
        osascript(['display alert "Camera & Mic Lock" message (item 1 of argv) as critical'], text)

    def confirm(self, title, text):
        return osascript([
            'display alert (item 1 of argv) message (item 2 of argv) as warning '
            'buttons {"Cancel", "Turn off"} default button "Cancel" cancel button "Cancel"',
            "return button returned of result"], title, text) == "Turn off"

    def notify(self, title, body):
        osascript(["display notification (item 2 of argv) with title (item 1 of argv)"],
                  title, body or "")

    def refresh(self):
        self.tray.refresh(force=True)

    def quit(self):
        self.tray.icon.stop()
        os._exit(0)


# ---------------------------------------------------------------- tray

def screen_locked():
    try:
        import Quartz
        info = Quartz.CGSessionCopyCurrentDictionary() or {}
        return bool(info.get("CGSSessionScreenIsLocked", False))
    except Exception:
        return False


def login_id():
    """Changes with every login: the PID of this user's loginwindow."""
    r = subprocess.run(["pgrep", "-x", "-u", str(os.getuid()), "loginwindow"],
                       capture_output=True, text=True)
    return r.stdout.strip()


class Tray:
    def __init__(self):
        self.state, self.pin_set = None, None
        self.ui = MacUI(self)
        self.flows = menu.Flows(self.ui, Client(), COMPUTER)
        self.icon = pystray.Icon("privacy-lock", ICONS[(False, False)], "Camera & Mic Lock",
                                 menu=pystray.Menu(self.menu_items))
        self.was_locked = False
        self.stopped_seen = menu.read_json(STOPPED_FILE, {})

    def menu_items(self):
        busy = self.flows.busy.locked()
        for entry in menu.menu_entries(self.state or {}, self.pin_set, COMPUTER):
            if entry is None:
                yield pystray.Menu.SEPARATOR
                continue
            label, action, devices = entry
            yield pystray.MenuItem(label, self.handler(action, devices),
                                   enabled=action is not None and not busy)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("On a Mac, a block stops apps within moments; "
                               "it cannot make the device disappear.", None, enabled=False)

    def handler(self, action, devices):
        def run():
            if action:
                self.flows.start(action, devices)
        return run

    def refresh(self, force=False):
        state = menu.read_json(STATE_FILE, {})
        pin_set = os.path.exists(PIN_MARKER)
        if force or state != self.state or pin_set != self.pin_set:
            self.state, self.pin_set = state, pin_set
            self.flows.state = state
            self.icon.icon = ICONS[("camera" in state, "mic" in state)]
            self.icon.update_menu()

    def show_boot_notice(self):
        notice = menu.read_json(BOOT_NOTICE, None)
        boot = menu.read_json(BOOT_FILE, {}).get("id")
        if not notice or not boot or notice.get("boot_id") != boot:
            return False
        if menu.read_json(NOTIFIED_FILE, None) == boot:
            return False
        self.save(NOTIFIED_FILE, boot)
        devices = [d for d in notice.get("unblocked", []) if d in core.NAMES]
        if not devices:
            return False
        self.ui.notify(*core.restart_notice(devices, COMPUTER))
        return True

    @staticmethod
    def save(path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(value, f)

    def show_stopped(self):
        """Tell the user when the daemon stopped an app using a blocked device."""
        stopped = menu.read_json(STOPPED_FILE, {})
        for device, event in stopped.items():
            if device in core.NAMES and event != self.stopped_seen.get(device):
                self.ui.notify(f"{core.NAMES[device]} is blocked",
                               f"An app tried to use the {core.NAMES[device].lower()} "
                               f"and was stopped ({event.get('what', '')}).")
        self.stopped_seen = stopped

    def watch(self):
        time.sleep(2)  # let the icon appear first
        this_login = login_id()
        if menu.read_json(LOGIN_MARKER, None) != this_login:
            self.save(LOGIN_MARKER, this_login)
            if not self.show_boot_notice():
                self.ui.notify(*menu.status_notice(menu.read_json(STATE_FILE, {})))
        while True:
            self.refresh()
            self.show_boot_notice()
            self.show_stopped()
            locked = screen_locked()
            if self.was_locked and not locked:
                self.ui.notify(*menu.status_notice(menu.read_json(STATE_FILE, {})))
            self.was_locked = locked
            time.sleep(2)

    def run(self):
        self.refresh(force=True)
        threading.Thread(target=self.watch, daemon=True).start()
        self.icon.run()  # must be the main thread on macOS


if __name__ == "__main__":
    Tray().run()
