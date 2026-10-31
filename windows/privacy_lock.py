"""privacy-lock (Windows) - the command line. See common/privacy_lock_cli.py."""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "common")]
import privacy_lock_cli  # noqa: E402
from privacy_lock_client import Client  # noqa: E402

if __name__ == "__main__":
    sys.exit(privacy_lock_cli.main(Client(), sys.argv[1:]))
