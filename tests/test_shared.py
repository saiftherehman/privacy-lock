"""Tests for the request handling and menu logic shared by Windows and macOS."""

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
sys.path.insert(0, os.path.dirname(__file__))
import privacy_lock_core as core  # noqa: E402
import privacy_lock_menu as menu  # noqa: E402
import privacy_lock_server as server  # noqa: E402
from test_core import FAST_HASH, FakePlatform  # noqa: E402


class ServerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(core.Engine, "hash_pin", FAST_HASH)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = tmp.name
        self.lock = threading.RLock()
        self.seen = []

    def make_platform(self, user, pins, password):
        p = FakePlatform(self.folder)
        p.pins = list(pins)
        p.password_ok = password == "secret"
        self.seen.append((user, password))
        return p

    def send(self, args, pins=(), password=None, user="alice"):
        line = json.dumps({"args": args, "pins": list(pins), "password": password})
        return server.handle(line, user, self.make_platform, self.lock)

    def test_flow(self):
        self.assertEqual(self.send(["setup"], ["1234", "1234"], "wrong")["code"], core.EXIT_ERROR)
        self.assertEqual(self.send(["setup"], ["1234", "1234"], "secret")["code"], 0)
        r = self.send(["camera", "off"], ["1234"], "secret")
        self.assertEqual(r, {"code": 0, "out": "Camera: OFF (unlock: PIN)", "err": ""})
        r = self.send(["check-pin"], ["9999"])
        self.assertEqual(r["code"], core.EXIT_WRONG_PIN)
        self.assertIn("4 attempt(s) left", r["err"])
        st = json.loads(self.send(["status", "--json"])["out"])
        self.assertTrue(st["camera"]["blocked"])
        self.assertEqual(self.seen[-1][0], "alice")

    def test_refused_requests(self):
        for line in ("not json", "[]", json.dumps({"args": "status"}),
                     json.dumps({"args": ["boot"]}), json.dumps({"args": ["wait-login"]}),
                     json.dumps({"args": ["migrate"]}),
                     json.dumps({"args": ["status"], "pins": [1]}),
                     json.dumps({"args": ["status"], "pins": ["1"] * 5}),
                     json.dumps({"args": ["camera", "off", "--bogus"]}),
                     json.dumps({"args": ["status"], "password": 5})):
            r = server.handle(line, "alice", self.make_platform, self.lock)
            self.assertEqual(r["code"], core.EXIT_ERROR, line)
            self.assertEqual(r["err"], "Not allowed.")

    def test_crash_is_reported(self):
        def broken(user, pins, password):
            p = self.make_platform(user, pins, password)
            p.security_problems = mock.Mock(side_effect=RuntimeError("boom"))
            return p
        r = server.handle(json.dumps({"args": ["check"]}), "alice", broken, self.lock)
        self.assertEqual(r["code"], core.EXIT_ERROR)
        self.assertIn("boom", r["err"])


class FakeUI:
    def __init__(self, answers):
        self.answers = list(answers)
        self.log = []

    def next(self, kind, *args):
        self.log.append((kind,) + args)
        return self.answers.pop(0)

    def ask_pins(self, title, labels):
        return self.next("pins", title, len(labels))

    def ask_block_mode(self, title, question, restart_label):
        return self.next("mode", restart_label)

    def ask_password(self, reason):
        return self.next("password", reason)

    def confirm(self, title, text):
        return self.next("confirm")

    def info(self, title, text):
        self.log.append(("info", title))

    def error(self, text):
        self.log.append(("error", text))

    def notify(self, title, body):
        self.log.append(("notify", title))

    def refresh(self):
        pass

    def quit(self):
        self.log.append(("quit",))


class FakeClient:
    def __init__(self, replies):
        self.replies = dict(replies)
        self.calls = []

    def request(self, args, pins, password):
        self.calls.append((args, pins, password))
        return self.replies.get(args[0], (0, "", ""))


class MenuTest(unittest.TestCase):
    def run_flow(self, action, devices, answers, replies=(), state=None):
        ui, client = FakeUI(answers), FakeClient(replies)
        flows = menu.Flows(ui, client, "Mac")
        flows.state = state or {}
        getattr(flows, "do_" + action.replace("-", "_"))(*([devices] if devices else []))
        return ui, client

    def test_block_checks_pin_before_password(self):
        ui, client = self.run_flow("block", ["camera"], ["restart", ["1234"], "pw"])
        self.assertEqual(client.calls, [(["check-pin"], ["1234"], None),
                                        (["camera", "off", "--restart-only"], ["1234"], "pw")])
        self.assertEqual(ui.log[0], ("mode", "Only by restarting the Mac"))
        self.assertEqual(ui.log[-1], ("notify", "Camera blocked"))

    def test_wrong_pin_never_asks_password(self):
        ui, client = self.run_flow("unblock", ["camera", "mic"], [["0000"]],
                                   {"check-pin": (core.EXIT_WRONG_PIN, "", "Wrong PIN.")})
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("password", [entry[0] for entry in ui.log])
        self.assertEqual(ui.log[-1], ("error", "Wrong PIN."))

    def test_block_during_lockout_goes_on(self):
        ui, client = self.run_flow("block", ["mic"], ["pin", ["0000"], "pw"],
                                   {"check-pin": (core.EXIT_LOCKED_OUT, "", "locked")})
        self.assertEqual(client.calls[-1], (["mic", "off"], ["0000"], "pw"))

    def test_unblock_during_lockout_stops(self):
        ui, client = self.run_flow("unblock", ["mic"], [["1234"]],
                                   {"check-pin": (core.EXIT_LOCKED_OUT, "", "locked")})
        self.assertEqual(len(client.calls), 1)

    def test_new_pin_validation(self):
        ui, client = self.run_flow("setup", None, [["12", "12"]])
        self.assertEqual(client.calls, [])
        ui, client = self.run_flow("setup", None, [["1234", "1235"]])
        self.assertEqual(ui.log[-1], ("error", "The new PINs do not match."))
        ui, client = self.run_flow("change-pin", None, [["1111", "2222", "2222"], "pw"])
        self.assertEqual(client.calls, [(["check-pin"], ["1111"], None),
                                        (["change-pin"], ["1111", "2222", "2222"], "pw")])

    def test_forgot_pin(self):
        ui, client = self.run_flow("forgot-pin", None, [], state={"mic": {"mode": "pin"}})
        self.assertEqual(client.calls, [])
        self.assertEqual(ui.log, [("info", "Forgot PIN")])
        ui, client = self.run_flow("forgot-pin", None, [["5555", "5555"], "pw"])
        self.assertEqual(client.calls, [(["reset-pin"], ["5555", "5555"], "pw")])

    def test_cancel_password(self):
        ui, client = self.run_flow("unblock", ["camera"], [["1234"], None])
        self.assertEqual(len(client.calls), 1)

    def test_remove(self):
        ui, client = self.run_flow("remove", None, [False])
        self.assertEqual(client.calls, [])
        ui, client = self.run_flow("remove", None, [True, ["1234"], "pw"])
        self.assertEqual(client.calls[-1], (["remove"], ["1234"], "pw"))
        self.assertEqual(ui.log[-1], ("quit",))

    def test_menu_entries(self):
        labels = lambda items: [e[0] if e else "-" for e in items]  # noqa: E731
        self.assertEqual(labels(menu.menu_entries({}, False, "PC")),
                         ["Camera: ON", "Microphone: ON", "-", "Set up PIN…"])
        items = menu.menu_entries({"camera": {"mode": "pin"}, "mic": {"mode": "restart"}},
                                  True, "PC")
        self.assertEqual(labels(items), [
            "Camera: BLOCKED (PIN to unlock)", "Microphone: BLOCKED (until restart)", "-",
            "Unblock camera…", "Restart the PC to unblock the rest", "-",
            "Change PIN…", "Forgot PIN…", "Turn off Camera & Mic Lock…"])
        self.assertIn("Block camera and microphone…", labels(menu.menu_entries({}, True, "PC")))

    def test_status_notice(self):
        self.assertEqual(menu.status_notice({})[0], "Camera and microphone are unblocked")
        title, body = menu.status_notice({"mic": {"mode": "login"}})
        self.assertEqual(title, "Microphone is blocked")
        self.assertEqual(body, "Microphone: BLOCKED (until login)\nCamera: unblocked")


if __name__ == "__main__":
    unittest.main()
