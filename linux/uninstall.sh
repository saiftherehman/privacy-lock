#!/bin/sh
# Turns Camera & Mic Lock off and removes it. Asks for the computer password
# and the PIN, exactly like "Turn off Camera & Mic Lock" in the top-bar menu.
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
if [ -x /usr/local/sbin/privacy-lock ]; then
    exec /usr/local/sbin/privacy-lock remove
fi
echo "Camera & Mic Lock is not installed."
