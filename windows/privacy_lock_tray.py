"""
privacy_lock_tray (Windows) - the lock icon in the notification area.

Runs as the signed-in user, without privileges. Started at every sign-in
(HKLM Run key, with --supervise so it comes back if it is closed). Menu logic
and dialog order are shared with macOS (common/privacy_lock_menu.py).
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import pystray
import win32api
import win32event
import winerror
from PIL import Image, ImageDraw

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "common")]
import privacy_lock_core as core  # noqa: E402
import privacy_lock_menu as menu  # noqa: E402
from privacy_lock_client import Client  # noqa: E402

DATA_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "privacy-lock")
PUBLIC_DIR = os.path.join(DATA_DIR, "public")
STATE_FILE = os.path.join(PUBLIC_DIR, "state.json")
BOOT_NOTICE = os.path.join(PUBLIC_DIR, "boot-notice.json")
BOOT_FILE = os.path.join(PUBLIC_DIR, "boot.json")
PIN_MARKER = os.path.join(PUBLIC_DIR, "pin-set")
NOTIFIED_FILE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                             "privacy-lock", "notified-boot")
COMPUTER = "PC"


# ---------------------------------------------------------------- icons

def draw_icon(camera_blocked, mic_blocked):
    """Open lock, crossed-out camera, crossed-out mic, or closed lock."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white, red = (255, 255, 255, 255), (220, 50, 50, 255)
    if camera_blocked == mic_blocked:
        d.rounded_rectangle((12, 28, 52, 58), 6, fill=red if camera_blocked else white)
        if camera_blocked:
            d.arc((20, 6, 44, 40), 180, 360, fill=red, width=6)
        else:
            d.arc((20, 2, 44, 36), 180, 330, fill=white, width=6)
        return img
    if camera_blocked:
        d.rounded_rectangle((6, 20, 44, 50), 5, fill=white)
        d.polygon([(44, 30), (58, 22), (58, 48), (44, 40)], fill=white)
    else:
        d.rounded_rectangle((22, 6, 42, 38), 10, fill=white)
        d.arc((14, 20, 50, 48), 0, 180, fill=white, width=4)
        d.line((32, 48, 32, 58), fill=white, width=4)
    d.line((8, 8, 56, 56), fill=red, width=7)
    return img


ICONS = {(c, m): draw_icon(c, m) for c in (False, True) for m in (False, True)}


# ---------------------------------------------------------------- password dialog

class CREDUI_INFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hwndParent", wt.HWND),
                ("pszMessageText", wt.LPCWSTR), ("pszCaptionText", wt.LPCWSTR),
                ("hbmBanner", wt.HBITMAP)]


CREDUIWIN_GENERIC = 0x1
CREDUIWIN_IN_CRED_ONLY = 0x20
CRED_PACK_GENERIC_CREDENTIALS = 0x4
ERROR_CANCELLED = 1223


def windows_password_prompt(reason):
    """The Windows Security dialog, filled in with the signed-in user.
    Returns the password, None if cancelled. Raises OSError if unavailable."""
    credui = ctypes.WinDLL("credui")
    ole32 = ctypes.WinDLL("ole32")
    user = os.environ.get("USERNAME", "")

    in_size = wt.DWORD(0)
    credui.CredPackAuthenticationBufferW(CRED_PACK_GENERIC_CREDENTIALS, user, "", None,
                                         ctypes.byref(in_size))
    in_buf = (ctypes.c_byte * in_size.value)()
    if not credui.CredPackAuthenticationBufferW(CRED_PACK_GENERIC_CREDENTIALS, user, "",
                                                in_buf, ctypes.byref(in_size)):
        raise OSError("CredPackAuthenticationBuffer failed")

    info = CREDUI_INFO(ctypes.sizeof(CREDUI_INFO), None,
                       f"Enter your Windows password to {reason}.", "Camera & Mic Lock", None)
    package = wt.ULONG(0)
    out_buf, out_size, save = ctypes.c_void_p(), wt.ULONG(0), wt.BOOL(False)
    err = credui.CredUIPromptForWindowsCredentialsW(
        ctypes.byref(info), 0, ctypes.byref(package), in_buf, in_size,
        ctypes.byref(out_buf), ctypes.byref(out_size), ctypes.byref(save),
        CREDUIWIN_GENERIC | CREDUIWIN_IN_CRED_ONLY)
    if err == ERROR_CANCELLED:
        return None
    if err != 0:
        raise OSError(f"CredUIPromptForWindowsCredentials: {err}")
    try:
        name, domain, pw = (ctypes.create_unicode_buffer(514) for _ in range(3))
        n1, n2, n3 = wt.DWORD(514), wt.DWORD(514), wt.DWORD(514)
        if not credui.CredUnPackAuthenticationBufferW(
                CRED_PACK_GENERIC_CREDENTIALS, out_buf, out_size, name, ctypes.byref(n1),
                domain, ctypes.byref(n2), pw, ctypes.byref(n3)):
            raise OSError("CredUnPackAuthenticationBuffer failed")
        password = pw.value
        ctypes.memset(pw, 0, ctypes.sizeof(pw))
        return password
    finally:
        ctypes.memset(out_buf, 0, out_size.value)
        ole32.CoTaskMemFree(out_buf)


# ---------------------------------------------------------------- dialogs (Tk)

class TkUI:
    """Dialogs on the Tk thread. The menu flows run on a worker thread and call
    these; each call is handed to the Tk thread and waited for."""

    def __init__(self, tray):
        self.tray = tray
        self.root = tk.Tk()
        self.root.withdraw()
        self.tasks = queue.Queue()
        self.root.after(100, self.pump)

    def pump(self):
        while not self.tasks.empty():
            fn, box, done = self.tasks.get()
            try:
                box.append(fn())
            except Exception as e:
                box.append(e)
            done.set()
        self.root.after(100, self.pump)

    def on_tk(self, fn):
        box, done = [], threading.Event()
        self.tasks.put((fn, box, done))
        done.wait()
        if isinstance(box[0], Exception):
            raise box[0]
        return box[0]

    def dialog(self, title):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        return win, frame

    def run_dialog(self, win, first=None):
        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_width()) // 2
        y = (win.winfo_screenheight() - win.winfo_height()) // 3
        win.geometry(f"+{x}+{y}")
        win.lift()
        win.focus_force()
        if first is not None:
            first.focus_set()
        win.grab_set()
        self.root.wait_window(win)

    # -- used by privacy_lock_menu.Flows
    def ask_pins(self, title, labels):
        def show():
            win, frame = self.dialog(title)
            entries, result = [], []
            for label in labels:
                ttk.Label(frame, text=label).pack(anchor="w", pady=(4, 2))
                e = ttk.Entry(frame, show="•", width=28)
                e.pack(fill="x")
                entries.append(e)

            def ok(_event=None):
                result.extend(e.get() for e in entries)
                win.destroy()

            buttons = ttk.Frame(frame)
            buttons.pack(fill="x", pady=(12, 0))
            ttk.Button(buttons, text="OK", command=ok).pack(side="right")
            ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=6)
            win.bind("<Return>", ok)
            win.bind("<Escape>", lambda _e: win.destroy())
            self.run_dialog(win, entries[0])
            return result if result and all(result) else None
        return self.on_tk(show)

    def ask_block_mode(self, title, question, restart_label):
        def show():
            win, frame = self.dialog(title)
            ttk.Label(frame, text=question).pack(anchor="w", pady=(0, 8))
            mode = tk.StringVar(value="pin")
            ttk.Radiobutton(frame, text="With my PIN", variable=mode, value="pin").pack(anchor="w")
            ttk.Radiobutton(frame, text=restart_label, variable=mode,
                            value="restart").pack(anchor="w")
            result = []

            def ok(_event=None):
                result.append(mode.get())
                win.destroy()

            buttons = ttk.Frame(frame)
            buttons.pack(fill="x", pady=(12, 0))
            ttk.Button(buttons, text="Block", command=ok).pack(side="right")
            ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=6)
            win.bind("<Escape>", lambda _e: win.destroy())
            self.run_dialog(win)
            return result[0] if result else None
        return self.on_tk(show)

    def ask_password(self, reason):
        try:
            return windows_password_prompt(reason)
        except OSError:
            pins = self.ask_pins("Camera & Mic Lock",
                                 [f"Enter your Windows password to {reason}:"])
            return pins[0] if pins else None

    def info(self, title, text):
        self.on_tk(lambda: messagebox.showinfo(title, text, parent=self.root))

    def error(self, text):
        self.on_tk(lambda: messagebox.showerror("Camera & Mic Lock", text, parent=self.root))

    def confirm(self, title, text):
        return self.on_tk(lambda: messagebox.askokcancel(title, text, icon="warning",
                                                         parent=self.root))

    def notify(self, title, body):
        self.tray.notify(title, body)

    def refresh(self):
        self.tray.refresh(force=True)

    def quit(self):
        self.tray.quit()


# ---------------------------------------------------------------- tray

def screen_locked():
    """The input desktop is not ours while the lock screen shows."""
    user32 = ctypes.WinDLL("user32")
    desk = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desk:
        return True
    user32.CloseDesktop(desk)
    return False


class Tray:
    def __init__(self, first_start):
        self.state, self.pin_set = None, None
        self.ui = TkUI(self)
        self.flows = menu.Flows(self.ui, Client(), COMPUTER)
        self.icon = pystray.Icon("privacy-lock", ICONS[(False, False)], "Camera & Mic Lock",
                                 menu=pystray.Menu(self.menu_items))
        self.first_start = first_start
        self.was_locked = False

    def menu_items(self):
        busy = self.flows.busy.locked()
        for entry in menu.menu_entries(self.state or {}, self.pin_set, COMPUTER):
            if entry is None:
                yield pystray.Menu.SEPARATOR
                continue
            label, action, devices = entry
            yield pystray.MenuItem(label, self.handler(action, devices),
                                   enabled=action is not None and not busy)

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

    def notify(self, title, body):
        try:
            self.icon.notify(body or " ", title)
        except Exception:
            pass

    def show_boot_notice(self):
        """Once per Windows start, if it lifted blocks. Returns True if shown."""
        notice = menu.read_json(BOOT_NOTICE, None)
        boot = menu.read_json(BOOT_FILE, {}).get("id")
        if not notice or not boot or notice.get("boot_id") != boot:
            return False
        if menu.read_json(NOTIFIED_FILE, None) == boot:
            return False
        os.makedirs(os.path.dirname(NOTIFIED_FILE), exist_ok=True)
        with open(NOTIFIED_FILE, "w") as f:
            json.dump(boot, f)
        devices = [d for d in notice.get("unblocked", []) if d in core.NAMES]
        if not devices:
            return False
        self.notify(*core.restart_notice(devices, COMPUTER))
        return True

    def watch(self):
        """Every 2 s: state changes, the restart notice, screen unlock."""
        if self.first_start:
            time.sleep(2)  # let the icon appear first
            if not self.show_boot_notice():
                self.notify(*menu.status_notice(menu.read_json(STATE_FILE, {})))
        while True:
            self.refresh()
            self.show_boot_notice()
            locked = screen_locked()
            if self.was_locked and not locked:
                self.notify(*menu.status_notice(menu.read_json(STATE_FILE, {})))
            self.was_locked = locked
            time.sleep(2)

    def quit(self):
        self.icon.stop()
        os._exit(0)

    def run(self):
        self.refresh(force=True)
        self.icon.run_detached()
        threading.Thread(target=self.watch, daemon=True).start()
        self.ui.root.mainloop()


def supervise():
    """Start the menu again whenever it exits (like Restart=always on Linux).
    The first start is the sign-in; later ones skip the status notice."""
    flag = []
    while True:
        started = time.monotonic()
        subprocess.run([sys.executable, os.path.abspath(__file__)] + flag)
        flag = ["--restarted"]
        if not os.path.exists(os.path.join(_here, "privacy_lock_engine.py")):
            return  # uninstalled
        time.sleep(2 if time.monotonic() - started > 10 else 30)


def main():
    if sys.argv[1:] == ["--supervise"]:
        supervise()
        return
    # Only one menu per sign-in session.
    mutex = win32event.CreateMutex(None, False, "Local\\privacy-lock-tray")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return
    Tray(first_start="--restarted" not in sys.argv).run()
    del mutex


if __name__ == "__main__":
    main()
