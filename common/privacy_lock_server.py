"""
privacy_lock_server - one request from the menu or the command line, as
handled by the privileged background process on Windows and macOS.

A request is one line of JSON:
    {"args": ["camera", "off"], "pins": ["1234"], "password": "..."}
The reply is one line of JSON:
    {"code": 0, "out": "Camera: OFF (unlock: PIN)", "err": ""}

Who is asking is never taken from the request: the background process finds
it out from the connection itself and passes it in as `user`.
"""

import contextlib
import io
import json

import privacy_lock_core as core

MAX_REQUEST = 64 * 1024


def allowed(args):
    """Commands a person may ask for. Boot and login handling are internal."""
    if args in (["status"], ["status", "--json"], ["check"], ["check-pin"], ["setup"],
                ["change-pin"], ["reset-pin"], ["remove"]):
        return True
    if len(args) >= 2 and core.parse_devices(args[0]):
        return args[1:] in (["off"], ["off", "--restart-only"], ["on"])
    return False


def parse(line):
    """The request as a dict, or None if it is not a valid request."""
    try:
        req = json.loads(line)
    except ValueError:
        return None
    if not isinstance(req, dict):
        return None
    args, pins, password = req.get("args"), req.get("pins", []), req.get("password")
    if not (isinstance(args, list) and all(isinstance(a, str) for a in args)):
        return None
    if not (isinstance(pins, list) and all(isinstance(p, str) for p in pins) and len(pins) <= 4):
        return None
    if password is not None and not isinstance(password, str):
        return None
    return {"args": args, "pins": pins, "password": password}


def handle(line, user, make_platform, lock):
    """Run one request for `user`. make_platform(user, pins, password) builds
    the platform object; `lock` keeps requests and background work apart."""
    req = parse(line)
    if req is None or not allowed(req["args"]):
        return {"code": core.EXIT_ERROR, "out": "", "err": "Not allowed."}
    platform = make_platform(user, req["pins"], req["password"])
    req["password"] = None
    out, err = io.StringIO(), io.StringIO()
    with lock, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = core.Engine(platform).run(req["args"])
        except core.Fail as f:
            print(f, file=err)
            code = f.code
        except Exception as e:  # keep the background process alive
            print(f"Failed: {e}", file=err)
            code = core.EXIT_ERROR
    return {"code": code, "out": out.getvalue().strip(), "err": err.getvalue().strip()}


def encode(obj):
    return (json.dumps(obj) + "\n").encode()
