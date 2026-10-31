"""
privacy_lock_menu - the tray menu logic shared by Windows and macOS.

The Linux menu (linux/privacy-lock-tray) is GTK and talks to polkit; this
module is the same behavior for the platforms where the menu talks to a
privileged background process instead. Each platform gives it:

  ui      dialogs and notifications (see the methods used below)
  client  request(args, pins, password) -> (exit code, stdout, stderr)

All flows run on a worker thread; the ui methods must be safe to call from it.
"""

import json
import threading

import privacy_lock_core as core
from privacy_lock_core import DEVICES, MIN_PIN_LEN, NAMES, targets_label


def device_line(device, state):
    entry = state.get(device)
    if entry is None:
        return f"{NAMES[device]}: ON"
    return {"restart": f"{NAMES[device]}: BLOCKED (until restart)",
            "login": f"{NAMES[device]}: BLOCKED (until login)"}.get(
        entry["mode"], f"{NAMES[device]}: BLOCKED (PIN to unlock)")


def menu_entries(state, pin_set, computer):
    """The menu, top to bottom: (label, action or None, devices). None as a
    whole entry is a separator; action None is a greyed-out line.
    Deliberately no "Quit": the menu must always stay."""
    items = [(device_line(d, state), None, None) for d in DEVICES]
    items.append(None)
    if not pin_set:
        items.append(("Set up PIN…", "setup", None))
        return items
    open_devs = [d for d in DEVICES if d not in state]
    pin_devs = [d for d in DEVICES if state.get(d, {}).get("mode") == "pin"]
    for d in open_devs:
        items.append((f"Block {NAMES[d].lower()}…", "block", [d]))
    if len(open_devs) == 2:
        items.append(("Block camera and microphone…", "block", open_devs))
    if open_devs and pin_devs:
        items.append(None)
    for d in pin_devs:
        items.append((f"Unblock {NAMES[d].lower()}…", "unblock", [d]))
    if len(pin_devs) == 2:
        items.append(("Unblock camera and microphone…", "unblock", pin_devs))
    if any(e["mode"] == "restart" for e in state.values()):
        items.append((f"Restart the {computer} to unblock the rest", None, None))
    items.append(None)
    items.append(("Change PIN…", "change-pin", None))
    items.append(("Forgot PIN…", "forgot-pin", None))
    items.append(("Turn off Camera & Mic Lock…", "remove", None))
    return items


def status_notice(state):
    """Title and text of the status notification (login and screen unlock)."""
    blocked = [d for d in DEVICES if d in state]
    unblocked = [d for d in DEVICES if d not in state]
    if not blocked:
        return ("Camera and microphone are unblocked",
                "Apps can use them. Block them from the lock icon.")
    lines = []
    for d in blocked:
        how = {"restart": "until restart", "login": "until login"}.get(
            state[d]["mode"], "PIN to unblock")
        lines.append(f"{NAMES[d]}: BLOCKED ({how})")
    lines += [f"{NAMES[d]}: unblocked" for d in unblocked]
    title = ("Camera and microphone are blocked" if not unblocked
             else f"{targets_label(blocked)} is blocked")
    return title, "\n".join(lines)


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Flows:
    """What each menu item does. Mirrors linux/privacy-lock-tray: the PIN is
    checked first, and only a right PIN leads to the computer-password prompt."""

    def __init__(self, ui, client, computer):
        self.ui = ui
        self.client = client
        self.computer = computer
        self.busy = threading.Lock()
        self.state = {}

    def start(self, action, devices=None):
        """Run a menu action on a worker thread; ignored while another runs."""
        if not self.busy.acquire(blocking=False):
            return

        def work():
            try:
                getattr(self, "do_" + action.replace("-", "_"))(*([devices] if devices else []))
            except Exception as e:  # never let a flow kill the menu
                self.ui.error(f"Something went wrong: {e}")
            finally:
                self.busy.release()
                self.ui.refresh()

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- steps

    def check_pin(self, pin, lockout_ok=False):
        """Step 1. True to go on to the password; shows the error otherwise."""
        code, _out, err = self.client.request(["check-pin"], [pin], None)
        if code == 0 or (code == core.EXIT_LOCKED_OUT and lockout_ok):
            return True
        self.ui.error(err or f"Failed (exit code {code}).")
        return False

    def finish(self, args, pins, success_title, reason):
        """Step 2: the computer password, then the real command."""
        password = self.ui.ask_password(reason)
        if password is None:
            return False
        code, out, err = self.client.request(args, pins, password)
        if code == 0:
            if success_title:
                self.ui.notify(success_title, out)
            return True
        self.ui.error(err or out or f"Failed (exit code {code}).")
        return False

    def ask_new_pin(self, title, first_fields):
        pins = self.ui.ask_pins(title, first_fields + [
            f"New PIN (digits, at least {MIN_PIN_LEN}):", "Repeat new PIN:"])
        if pins is None:
            return None
        new, repeat = pins[-2:]
        if not new.isdigit() or len(new) < MIN_PIN_LEN:
            self.ui.error(f"The PIN must be at least {MIN_PIN_LEN} digits.")
            return None
        if new != repeat:
            self.ui.error("The new PINs do not match.")
            return None
        return pins

    # ---------------------------------------------------------------- actions

    def do_block(self, devices):
        label = targets_label(devices).lower()
        mode = self.ui.ask_block_mode(f"Block {label}",
                                      f"How should the {label} be unblocked later?",
                                      f"Only by restarting the {self.computer}")
        if mode is None:
            return
        pins = self.ui.ask_pins(f"Block {label}", ["Enter your PIN:"])
        if pins is None or not self.check_pin(pins[0], lockout_ok=True):
            return
        target = "all" if len(devices) == 2 else devices[0]
        args = [target, "off"] + (["--restart-only"] if mode == "restart" else [])
        self.finish(args, pins, f"{targets_label(devices)} blocked", f"block the {label}")

    def do_unblock(self, devices):
        label = targets_label(devices).lower()
        pins = self.ui.ask_pins(f"Unblock {label}", ["Enter your PIN:"])
        if pins is None or not self.check_pin(pins[0]):
            return
        target = "all" if len(devices) == 2 else devices[0]
        self.finish([target, "on"], pins, f"{targets_label(devices)} unblocked",
                    f"unblock the {label}")

    def do_setup(self):
        pins = self.ask_new_pin("Set up PIN", [])
        if pins is not None:
            self.finish(["setup"], pins, "PIN set up", "set up your PIN")

    def do_change_pin(self):
        pins = self.ask_new_pin("Change PIN", ["Current PIN:"])
        if pins is not None and self.check_pin(pins[0]):
            self.finish(["change-pin"], pins, "PIN changed", "change your PIN")

    def do_forgot_pin(self):
        if self.state:
            # The engine refuses too; this only explains the way back in.
            self.ui.info("Forgot PIN",
                         "Forgot PIN can only be used while nothing is blocked.\n\n"
                         "To unblock without the PIN: enter 5 wrong PINs, then restart the "
                         f"{self.computer} within the hour and log in. Then use Forgot PIN "
                         "to set a new one.")
            return
        pins = self.ask_new_pin("Forgot PIN", [])
        if pins is not None:
            # No old PIN: the computer password is the only check.
            self.finish(["reset-pin"], pins, "New PIN set", "set a new PIN")

    def do_remove(self):
        if not self.ui.confirm(
                "Turn off Camera & Mic Lock?",
                "This removes your PIN, unblocks the camera and microphone, and uninstalls "
                "the lock. You will need your computer password and your PIN."):
            return
        pins = self.ui.ask_pins("Turn off Camera & Mic Lock", ["Enter your PIN:"])
        if pins is None or not self.check_pin(pins[0]):
            return
        if self.finish(["remove"], pins, None, "turn off Camera & Mic Lock"):
            self.ui.notify("Camera & Mic Lock turned off",
                           "The PIN was removed and the camera and microphone are unblocked.")
            self.ui.quit()
