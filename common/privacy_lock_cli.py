"""
privacy_lock_cli - the `privacy-lock` command on Windows and macOS.

Same commands and order as on Linux: PINs are typed by hand (getpass reads the
console / terminal, never a pipe), the PIN is checked first, and only a right
PIN leads to the computer-password prompt. Everything is then sent to the
privileged background process, which checks it all again.
"""

import getpass
import sys

import privacy_lock_core as core
from privacy_lock_core import MIN_PIN_LEN


def ask(prompt):
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit("Cancelled.")


def ask_new_pin():
    while True:
        pin = ask(f"New PIN (digits, min {MIN_PIN_LEN}): ")
        if not pin.isdigit() or len(pin) < MIN_PIN_LEN:
            print(f"PIN must be at least {MIN_PIN_LEN} digits. Try again.")
        elif ask("Repeat PIN: ") != pin:
            print("PINs do not match. Try again.")
        else:
            return [pin, pin]


def show(code, out, err):
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


def main(client, args):
    if not args or args[0] in ("-h", "--help", "help"):
        print(core.COMMANDS_USAGE)
        return 0
    cmd = args[0]
    if cmd in ("status", "check"):
        return show(*client.request(args, [], None))

    pins, check_first, lockout_ok = [], None, False
    devices = core.parse_devices(cmd)
    if devices and args[1:2] == ["off"]:
        print("PIN required.")
        pins = [ask("PIN: ")]
        check_first, lockout_ok = pins[0], True
    elif devices and args[1:] == ["on"]:
        print("PIN required.")
        pins = [ask("PIN: ")]
        check_first = pins[0]
    elif cmd == "change-pin":
        print("Current PIN required.")
        pins = [ask("PIN: ")]
        check_first = pins[0]
    elif cmd == "remove":
        print("PIN required.")
        pins = [ask("PIN: ")]
        check_first = pins[0]
    elif cmd not in ("setup", "reset-pin"):
        print(core.COMMANDS_USAGE)
        return 2

    if check_first is not None:
        code, out, err = client.request(["check-pin"], [check_first], None)
        if code != 0 and not (code == core.EXIT_LOCKED_OUT and lockout_ok):
            return show(code, out, err)
    if cmd in ("setup", "reset-pin", "change-pin"):
        pins += ask_new_pin()
    print("Confirm it's you: enter your computer login password.")
    password = ask("Computer password: ")
    return show(*client.request(args, pins, password))
