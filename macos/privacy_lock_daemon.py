"""
privacy_lock_daemon (macOS) - the part of Camera & Mic Lock that runs as root.

A LaunchDaemon (KeepAlive). It:

  * once per Mac start, re-applies the blocks from before the restart and
    lifts the restart-lifted ones when someone logs in at the Mac's own screen;
  * keeps blocked devices stopped (see privacy_lock_engine.py for how far that
    goes on macOS);
  * answers the menu and the command line on the socket
    /var/run/privacy-lock.sock (the person at the Mac's own screen only).
"""

import ctypes
import json
import os
import socket
import sys
import threading
import time

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "common")]
import privacy_lock_audio as audio  # noqa: E402
import privacy_lock_core as core  # noqa: E402
import privacy_lock_engine as mac  # noqa: E402
import privacy_lock_server as server  # noqa: E402

WATCH_EVERY = 0.5
lock = threading.RLock()
_libc = ctypes.CDLL(None, use_errno=True)


def log(msg):
    # The real stdout (launchd sends it to the log file), never a reply being captured.
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, file=sys.__stdout__, flush=True)


def engine(user=None, pins=(), password=None):
    return core.Engine(mac.MacPlatform(user, pins, password))


def logged(fn, *args):
    import contextlib
    import io
    out = io.StringIO()
    with lock, contextlib.redirect_stdout(out):
        try:
            fn(*args)
        except Exception as e:
            print(f"failed: {e}")
    for line in out.getvalue().splitlines():
        log(line)


# ---------------------------------------------------------------- restart

def at_start():
    os.makedirs(mac.PUBLIC_DIR, exist_ok=True)
    os.chmod(mac.PUBLIC_DIR, 0o755)
    e = engine()
    if os.path.exists(e.p.pin_file):
        e.p.write_public(mac.PIN_MARKER, "")
    current = mac.boot_id()
    if mac.load_json(mac.BOOT_FILE, {}).get("id") != current:
        e.p.write_public(mac.BOOT_FILE, json.dumps({"id": current}))
        log("Mac started")
        logged(e.cmd_boot)
    else:
        log("daemon restarted (same Mac start)")


def wait_for_login():
    e = engine()
    while True:
        with lock:
            if not e.waiting_for_login():
                return
            user = e.p.local_login_user()
        if user:
            logged(e.lift_after_login, user)
            return
        time.sleep(1)


# ---------------------------------------------------------------- keep stopped

def note_stopped(device, what):
    stopped = mac.load_json(mac.STOPPED_FILE, {})
    stopped[device] = {"time": time.time(), "what": what}
    mac.write_file(mac.STOPPED_FILE, json.dumps(stopped), 0o644)
    log(f"stopped {device}: {what}")


def watch():
    seen = set()
    while True:
        time.sleep(WATCH_EVERY)
        try:
            with lock:
                state = engine().load_state()
                if "camera" in state and audio.camera_running():
                    mac.stop_camera()
                    note_stopped("camera", "camera services")
                if "mic" in state:
                    if not audio.inputs_silenced():
                        audio.silence_inputs()
                    stopped = mac.stop_recorders(seen)
                    if stopped:
                        note_stopped("mic", ", ".join(sorted(set(stopped))))
                else:
                    seen.clear()
        except Exception as ex:
            log(f"watch: {ex}")


# ---------------------------------------------------------------- socket

def peer_user(conn):
    uid, gid = ctypes.c_uint32(), ctypes.c_uint32()
    if _libc.getpeereid(conn.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        return None, None
    import pwd
    try:
        return uid.value, pwd.getpwuid(uid.value).pw_name
    except KeyError:
        return uid.value, None


def serve_one(conn):
    with conn:
        conn.settimeout(300)
        data = b""
        try:
            while not data.endswith(b"\n") and len(data) < server.MAX_REQUEST:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            uid, user = peer_user(conn)
            line = data.decode(errors="replace")
            req = server.parse(line)
            at_screen = user is not None and (user == mac.console_user() or uid == 0)
            if not at_screen and not (req and req["args"][:1] == ["status"]):
                reply = {"code": core.EXIT_ERROR, "out": "",
                         "err": "Only available to the person logged in at this Mac's own screen."}
            else:
                reply = server.handle(line, user if uid != 0 else mac.console_user(),
                                      mac.MacPlatform, lock)
                if req:
                    log(f"{user}: {' '.join(req['args'])} -> {reply['code']}")
            conn.sendall(server.encode(reply))
        except OSError as e:
            log(f"socket: {e}")


def serve():
    try:
        os.remove(mac.SOCKET)
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(mac.SOCKET)
    os.chmod(mac.SOCKET, 0o666)  # anyone may connect; who they are is checked per request
    sock.listen(8)
    while True:
        conn, _ = sock.accept()
        threading.Thread(target=serve_one, args=(conn,), daemon=True).start()


def main():
    if os.geteuid() != 0:
        sys.exit("The privacy-lock daemon must run as root (it is started by launchd).")
    at_start()
    threading.Thread(target=wait_for_login, daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    serve()


if __name__ == "__main__":
    main()
