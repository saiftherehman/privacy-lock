"""
privacy_lock_service (Windows) - the part of Camera & Mic Lock that runs as SYSTEM.

Started by the Task Scheduler at every Windows start (install.ps1 registers
it, restarts it if it stops). It:

  * once per Windows start, re-applies the blocks from before the restart and
    lifts the restart-lifted ones at the first sign-in at this PC's own screen;
  * keeps blocked devices blocked: a camera or mic plugged in later is
    disabled within a few seconds;
  * answers the menu and the command line over the named pipe
    \\\\.\\pipe\\privacy-lock (people signed in at this PC only).
"""

import logging
import logging.handlers
import os
import sys
import threading
import time

import pywintypes
import win32api
import win32file
import win32pipe
import win32security
import win32ts

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "common")]
import privacy_lock_core as core  # noqa: E402
import privacy_lock_engine as win  # noqa: E402
import privacy_lock_server as server  # noqa: E402
from privacy_lock_client import PIPE_NAME  # noqa: E402

FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
ERROR_PIPE_CONNECTED = 535

# SYSTEM and Administrators: full. Signed-in users: read and write data only,
# so they cannot create a second copy of the pipe and pose as the service.
PIPE_SDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x12008b;;;IU)"

ENFORCE_EVERY = 5   # seconds between checks for newly plugged-in devices
lock = threading.RLock()
log = logging.getLogger("privacy-lock")


def setup_log():
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(win.DATA_DIR, "service.log"), maxBytes=256 * 1024, backupCount=1)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def engine(user=None, pins=(), password=None):
    return core.Engine(win.WindowsPlatform(user, pins, password))


def logged(fn, *args):
    """Run an engine step under the lock and write what it printed to the log."""
    import contextlib
    import io
    out = io.StringIO()
    with lock, contextlib.redirect_stdout(out):
        try:
            fn(*args)
        except Exception as e:
            log.exception("failed: %s", e)
    for line in out.getvalue().splitlines():
        log.info(line)


# ---------------------------------------------------------------- restart

def at_start():
    if win.is_new_boot():
        win.record_boot()
        log.info("Windows started")
        logged(engine().cmd_boot)
    else:
        log.info("service restarted (same Windows start)")


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


# ---------------------------------------------------------------- keep blocked

def enforce():
    """Disable cameras / mics that appeared while blocked (plugged in later,
    or re-enabled by an administrator)."""
    while True:
        time.sleep(ENFORCE_EVERY)
        try:
            e = engine()
            with lock:
                state = e.load_state()
                for device, entry in state.items():
                    if entry.get("mode") and win.enabled_devices(device):
                        log.info("re-blocking %s (new or re-enabled device)", device)
                        e.p.block(device)
        except Exception as ex:
            log.exception("enforce: %s", ex)


# ---------------------------------------------------------------- pipe

def client_identity(handle):
    """(user, session id) of whoever is on the other end of the pipe."""
    pid = win32pipe.GetNamedPipeClientProcessId(handle)
    session = win32ts.ProcessIdToSessionId(pid)
    win32pipe.ImpersonateNamedPipeClient(handle)
    try:
        user = win32api.GetUserNameEx(win32api.NameSamCompatible)
    finally:
        win32security.RevertToSelf()
    return user, session


def read_line(handle):
    data = b""
    while not data.endswith(b"\n") and len(data) < server.MAX_REQUEST:
        _hr, chunk = win32file.ReadFile(handle, 4096)
        if not chunk:
            break
        data += chunk
    return data.decode(errors="replace")


def serve_one(handle):
    try:
        line = read_line(handle)  # must be read before impersonating
        user, session = client_identity(handle)
        req = server.parse(line)
        if session != win.console_session() and not (req and req["args"][:1] == ["status"]):
            reply = {"code": core.EXIT_ERROR, "out": "",
                     "err": "Only available to the person signed in at this PC's own screen."}
        else:
            reply = server.handle(line, user, win.WindowsPlatform, lock)
            if req:
                log.info("%s: %s -> %s", user, " ".join(req["args"]), reply["code"])
        win32file.WriteFile(handle, server.encode(reply))
        win32file.FlushFileBuffers(handle)
    except pywintypes.error as e:
        log.info("pipe: %s", e)
    finally:
        try:
            win32pipe.DisconnectNamedPipe(handle)
        except pywintypes.error:
            pass
        win32file.CloseHandle(handle)


def serve():
    sa = pywintypes.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        PIPE_SDDL, win32security.SDDL_REVISION_1)
    first = True
    while True:
        handle = win32pipe.CreateNamedPipe(
            PIPE_NAME,
            win32pipe.PIPE_ACCESS_DUPLEX | (FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0),
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT
            | PIPE_REJECT_REMOTE_CLIENTS,
            win32pipe.PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, sa)
        first = False
        try:
            win32pipe.ConnectNamedPipe(handle, None)
        except pywintypes.error as e:
            if e.winerror != ERROR_PIPE_CONNECTED:
                win32file.CloseHandle(handle)
                continue
        threading.Thread(target=serve_one, args=(handle,), daemon=True).start()


def main():
    os.makedirs(win.PUBLIC_DIR, exist_ok=True)
    setup_log()
    at_start()
    threading.Thread(target=wait_for_login, daemon=True).start()
    threading.Thread(target=enforce, daemon=True).start()
    serve()


if __name__ == "__main__":
    main()
