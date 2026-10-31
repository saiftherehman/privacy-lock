"""
privacy_lock_client (macOS) - talks to privacy_lock_daemon.py over its socket.

Before anything is sent (PINs, the computer password), the client checks
that the socket belongs to root; only root can create files in /var/run.
"""

import json
import os
import socket
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "common")]
import privacy_lock_core as core  # noqa: E402

SOCKET = "/var/run/privacy-lock.sock"
NOT_RUNNING = ("The Camera & Mic Lock background service is not running. Restart the Mac, "
               "or reinstall it with install.sh.")


class Client:
    def request(self, args, pins, password):
        """Send one request. Returns (exit code, stdout, stderr)."""
        try:
            if os.stat(SOCKET).st_uid != 0:
                return core.EXIT_ERROR, "", "Refused: the lock's socket is not owned by root."
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(300)
                s.connect(SOCKET)
                s.sendall((json.dumps({"args": args, "pins": pins, "password": password})
                           + "\n").encode())
                data = b""
                while not data.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            reply = json.loads(data)
            return reply["code"], reply["out"], reply["err"]
        except (OSError, ValueError, KeyError):
            return core.EXIT_ERROR, "", NOT_RUNNING
