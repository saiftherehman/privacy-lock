"""Tests for the shared rules in common/privacy_lock_core.py.

Run from the repository root:  python3 -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
import privacy_lock_core as core  # noqa: E402

# A cheap hash keeps the tests fast; the real one is checked separately.
FAST_HASH = staticmethod(lambda pin, salt: (salt.hex() + ":" + pin))


class FakePlatform(core.Platform):
    computer = "PC"

    def __init__(self, folder):
        self.pin_file = os.path.join(folder, "pin.json")
        self.state_file = os.path.join(folder, "state.json")
        self.boot_notice = os.path.join(folder, "boot-notice.json")
        self.was_blocked = os.path.join(folder, "was-blocked.json")
        self.attempts_file = os.path.join(folder, "attempts.json")
        self.pins = []
        self.blocked = set()
        self.problems = []
        self.password_ok = True
        self.password_asked = 0
        self.login_user = None
        self.uninstalled = False
        self.interactive = False

    def write_private(self, path, text):
        with open(path, "w") as f:
            f.write(text)

    write_public = write_private

    def block(self, device):
        self.blocked.add(device)

    def unblock(self, device):
        self.blocked.discard(device)

    def real_user(self):
        return "alice"

    def security_problems(self, user):
        return list(self.problems)

    def confirm_account_password(self):
        self.password_asked += 1
        if not self.password_ok:
            raise core.Fail("Computer password not accepted.")

    def read_pin(self, prompt):
        return self.pins.pop(0)

    def boot_id(self):
        return "boot-2"

    def local_login_user(self):
        return self.login_user

    def uninstall(self):
        self.uninstalled = True


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(core.Engine, "hash_pin", FAST_HASH)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = FakePlatform(self.tmp.name)
        self.e = core.Engine(self.p)
        self.p.pins = ["1234", "1234"]
        self.e.cmd_setup()

    def run_cmd(self, *args, pins=()):
        self.p.pins = list(pins)
        try:
            return self.e.run(list(args))
        except core.Fail as f:
            return f.code

    def state(self):
        return self.e.load_state()

    def read(self, path, default=None):
        return self.e.load_json(path, default)

    # -- PIN

    def test_setup_twice_refused(self):
        self.assertEqual(self.run_cmd("setup", pins=["5555", "5555"]), core.EXIT_ERROR)

    def test_short_or_mismatched_new_pin(self):
        self.assertEqual(self.run_cmd("change-pin", pins=["1234", "12", "12"]), core.EXIT_ERROR)
        self.assertEqual(self.run_cmd("change-pin", pins=["1234", "5678", "5679"]), core.EXIT_ERROR)
        self.assertEqual(self.run_cmd("check-pin", pins=["1234"]), 0)

    def test_change_pin(self):
        self.assertEqual(self.run_cmd("change-pin", pins=["1234", "5678", "5678"]), 0)
        self.assertEqual(self.run_cmd("check-pin", pins=["1234"]), core.EXIT_WRONG_PIN)
        self.assertEqual(self.run_cmd("check-pin", pins=["5678"]), 0)

    def test_lockout_after_five_wrong(self):
        for _ in range(4):
            self.assertEqual(self.run_cmd("check-pin", pins=["0000"]), core.EXIT_WRONG_PIN)
        self.assertEqual(self.run_cmd("check-pin", pins=["0000"]), core.EXIT_LOCKED_OUT)
        # The right PIN is refused during the lockout, without being read.
        self.assertEqual(self.run_cmd("check-pin", pins=[]), core.EXIT_LOCKED_OUT)
        # After the hour it works again and the counter starts over.
        tries = self.read(self.p.attempts_file)
        tries["locked_until"] = time.time() - 1
        self.p.write_private(self.p.attempts_file, json.dumps(tries))
        self.assertEqual(self.run_cmd("check-pin", pins=["0000"]), core.EXIT_WRONG_PIN)
        self.assertEqual(self.read(self.p.attempts_file)["fails"], 1)
        self.assertEqual(self.run_cmd("check-pin", pins=["1234"]), 0)
        self.assertEqual(self.read(self.p.attempts_file)["fails"], 0)

    def test_real_hash_is_scrypt(self):
        salt = bytes(16)
        expected = __import__("hashlib").scrypt(b"1234", salt=salt, n=2**15, r=8, p=1,
                                                maxmem=64 * 1024 * 1024).hex()
        mock.patch.stopall()
        self.assertEqual(core.Engine.hash_pin("1234", salt), expected)

    # -- blocking

    def test_block_needs_pin_then_password(self):
        self.assertEqual(self.run_cmd("camera", "off", pins=["0000"]), core.EXIT_WRONG_PIN)
        self.assertEqual(self.p.password_asked, 1)  # only the one from setup
        self.assertEqual(self.p.blocked, set())
        self.p.password_ok = False
        self.assertEqual(self.run_cmd("camera", "off", pins=["1234"]), core.EXIT_ERROR)
        self.assertEqual(self.p.blocked, set())
        self.p.password_ok = True
        self.assertEqual(self.run_cmd("camera", "off", pins=["1234"]), 0)
        self.assertEqual(self.p.blocked, {"camera"})
        self.assertEqual(self.state()["camera"]["mode"], "pin")
        self.assertEqual(self.read(self.p.was_blocked), {"camera": "pin"})

    def test_block_during_lockout_needs_only_password(self):
        for _ in range(5):
            self.run_cmd("check-pin", pins=["0000"])
        self.assertEqual(self.run_cmd("all", "off", pins=[]), 0)
        self.assertEqual(self.p.blocked, {"camera", "mic"})

    def test_restart_block_never_downgraded(self):
        self.run_cmd("mic", "off", "--restart-only", pins=["1234"])
        self.run_cmd("mic", "off", pins=["1234"])
        self.assertEqual(self.state()["mic"]["mode"], "restart")
        self.run_cmd("camera", "off", pins=["1234"])
        self.run_cmd("camera", "off", "--restart-only", pins=["1234"])
        self.assertEqual(self.state()["camera"]["mode"], "restart")

    def test_unblock(self):
        self.run_cmd("all", "off", pins=["1234"])
        self.assertEqual(self.run_cmd("camera", "on", pins=["0000"]), core.EXIT_WRONG_PIN)
        self.assertEqual(self.p.blocked, {"camera", "mic"})
        self.assertEqual(self.run_cmd("camera", "on", pins=["1234"]), 0)
        self.assertEqual(self.p.blocked, {"mic"})
        self.assertEqual(self.read(self.p.was_blocked), {"mic": "pin"})
        self.assertEqual(self.run_cmd("mic", "on", pins=["1234"]), 0)
        self.assertFalse(os.path.exists(self.p.was_blocked))

    def test_restart_only_refuses_pin(self):
        self.run_cmd("camera", "off", "--restart-only", pins=["1234"])
        self.assertEqual(self.run_cmd("camera", "on", pins=["1234"]), core.EXIT_RESTART_ONLY)
        self.assertEqual(self.p.blocked, {"camera"})

    def test_unprotected_computer(self):
        self.p.problems = ["Automatic login is turned on."]
        self.assertEqual(self.run_cmd("camera", "off", pins=["1234"]), 0)  # blocking still works
        self.assertEqual(self.run_cmd("camera", "on", pins=["1234"]), core.EXIT_ERROR)
        self.assertEqual(self.run_cmd("change-pin", pins=["1234", "5678", "5678"]), core.EXIT_ERROR)
        self.assertEqual(self.run_cmd("remove", pins=["1234"]), core.EXIT_ERROR)
        self.assertEqual(self.p.blocked, {"camera"})

    # -- restart

    def boot(self):
        """Simulate a restart: blocks and RAM state are gone, then boot runs."""
        self.p.blocked = set()
        for path in (self.p.state_file, self.p.boot_notice):
            if os.path.exists(path):
                os.remove(path)
        self.e.cmd_boot()

    def test_pin_block_survives_restart(self):
        self.run_cmd("all", "off", pins=["1234"])
        self.boot()
        self.assertEqual(self.p.blocked, {"camera", "mic"})
        self.assertEqual({d: e["mode"] for d, e in self.state().items()},
                         {"camera": "pin", "mic": "pin"})
        self.p.login_user = "alice"
        self.e.cmd_wait_login()  # nothing to wait for
        self.assertEqual(self.p.blocked, {"camera", "mic"})
        self.assertFalse(os.path.exists(self.p.boot_notice))

    def test_restart_block_lifted_at_login_only(self):
        self.run_cmd("camera", "off", "--restart-only", pins=["1234"])
        self.run_cmd("mic", "off", pins=["1234"])
        self.boot()
        self.assertEqual(self.p.blocked, {"camera", "mic"})
        self.assertEqual(self.state()["camera"]["mode"], "login")
        # Down again before anyone logged in: still kept.
        self.boot()
        self.assertEqual(self.state()["camera"]["mode"], "login")
        self.p.login_user = "alice"
        self.e.cmd_wait_login()
        self.assertEqual(self.p.blocked, {"mic"})
        self.assertEqual(self.read(self.p.boot_notice),
                         {"unblocked": ["camera"], "boot_id": "boot-2"})
        self.assertEqual(self.read(self.p.was_blocked), {"mic": "pin"})

    def test_restart_during_lockout_lifts_pin_blocks(self):
        self.run_cmd("all", "off", pins=["1234"])
        for _ in range(5):
            self.run_cmd("check-pin", pins=["0000"])
        self.boot()
        self.assertFalse(os.path.exists(self.p.attempts_file))
        self.assertEqual({e["mode"] for e in self.state().values()}, {"login"})
        self.p.login_user = "alice"
        self.e.cmd_wait_login()
        self.assertEqual(self.p.blocked, set())
        self.assertEqual(self.read(self.p.boot_notice)["unblocked"], ["camera", "mic"])
        self.assertFalse(os.path.exists(self.p.was_blocked))

    def test_restart_after_expired_lockout_keeps_pin_blocks(self):
        self.run_cmd("camera", "off", pins=["1234"])
        self.p.write_private(self.p.attempts_file,
                             json.dumps({"fails": 5, "locked_until": time.time() - 1}))
        self.boot()
        self.assertEqual(self.state()["camera"]["mode"], "pin")

    def test_old_list_format(self):
        self.p.write_private(self.p.was_blocked, json.dumps(["camera", "bogus"]))
        self.boot()
        self.assertEqual(self.state(), {"camera": mock.ANY})
        self.assertEqual(self.state()["camera"]["mode"], "pin")

    def test_boot_without_blocks(self):
        self.boot()
        self.assertEqual(self.state(), {})
        self.assertEqual(self.p.blocked, set())

    # -- Forgot PIN and Turn off

    def test_forgot_pin_refused_while_blocked(self):
        self.run_cmd("mic", "off", pins=["1234"])
        asked = self.p.password_asked
        self.assertEqual(self.run_cmd("reset-pin", pins=["9999", "9999"]), core.EXIT_ERROR)
        self.assertEqual(self.p.password_asked, asked)
        self.assertEqual(self.run_cmd("check-pin", pins=["1234"]), 0)

    def test_forgot_pin_while_unblocked(self):
        for _ in range(3):
            self.run_cmd("check-pin", pins=["0000"])
        self.assertEqual(self.run_cmd("reset-pin", pins=["9999", "9999"]), 0)
        self.assertFalse(os.path.exists(self.p.attempts_file))
        self.assertEqual(self.run_cmd("check-pin", pins=["1234"]), core.EXIT_WRONG_PIN)
        self.assertEqual(self.run_cmd("check-pin", pins=["9999"]), 0)

    def test_remove(self):
        self.run_cmd("camera", "off", "--restart-only", pins=["1234"])
        self.assertEqual(self.run_cmd("remove", pins=["1234"]), core.EXIT_RESTART_ONLY)
        self.boot()
        self.p.login_user = "alice"
        self.e.cmd_wait_login()
        self.run_cmd("mic", "off", pins=["1234"])
        self.assertEqual(self.run_cmd("remove", pins=["0000"]), core.EXIT_WRONG_PIN)
        self.assertFalse(self.p.uninstalled)
        self.assertEqual(self.run_cmd("remove", pins=["1234"]), 0)
        self.assertTrue(self.p.uninstalled)
        self.assertEqual(self.p.blocked, set())

    def test_unknown_commands(self):
        for args in (["camera"], ["camera", "off", "--bogus"], ["mic", "on", "x"], ["setup", "x"]):
            with self.assertRaises(ValueError):
                self.e.run(args)


if __name__ == "__main__":
    unittest.main()
