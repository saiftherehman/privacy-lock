"""
privacy_lock_client (Windows) - talks to privacy_lock_service.py over a named pipe.

Before anything is sent (PINs, the computer password), the client checks
that the pipe really belongs to the service: its owner must be SYSTEM or the
Administrators group, which a normal program cannot fake.
"""

import json
import os
import sys

import pywintypes
import win32file
import win32pipe
import win32security

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "common")]
import privacy_lock_core as core  # noqa: E402

PIPE_NAME = r"\\.\pipe\privacy-lock"

# Read, plus write data only: the pipe's permissions allow no more than that.
PIPE_ACCESS = 0x0012008B
TRUSTED_OWNERS = ("S-1-5-18", "S-1-5-32-544")  # SYSTEM, Administrators

NOT_RUNNING = ("The Camera & Mic Lock service is not running. Restart the PC, or reinstall "
               "it with install.ps1.")


class Client:
    def request(self, args, pins, password):
        """Send one request. Returns (exit code, stdout, stderr)."""
        try:
            handle = self.connect()
        except pywintypes.error:
            return core.EXIT_ERROR, "", NOT_RUNNING
        try:
            owner = win32security.GetSecurityInfo(
                handle, win32security.SE_KERNEL_OBJECT,
                win32security.OWNER_SECURITY_INFORMATION).GetSecurityDescriptorOwner()
            if win32security.ConvertSidToStringSid(owner) not in TRUSTED_OWNERS:
                return core.EXIT_ERROR, "", "Refused: the lock's connection is not owned by the system."
            line = json.dumps({"args": args, "pins": pins, "password": password}) + "\n"
            win32file.WriteFile(handle, line.encode())
            data = b""
            while not data.endswith(b"\n"):
                _hr, chunk = win32file.ReadFile(handle, 65536)
                if not chunk:
                    break
                data += chunk
            reply = json.loads(data)
            return reply["code"], reply["out"], reply["err"]
        except (pywintypes.error, ValueError, KeyError):
            return core.EXIT_ERROR, "", NOT_RUNNING
        finally:
            win32file.CloseHandle(handle)

    @staticmethod
    def connect():
        for _ in range(20):
            try:
                return win32file.CreateFile(PIPE_NAME, PIPE_ACCESS, 0, None,
                                            win32file.OPEN_EXISTING, 0, None)
            except pywintypes.error as e:
                if e.winerror != 231:  # ERROR_PIPE_BUSY: wait for a free instance
                    raise
                win32pipe.WaitNamedPipe(PIPE_NAME, 3000)
        raise pywintypes.error(231, "CreateFile", "busy")
