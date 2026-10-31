#!/bin/sh
# Turns Camera & Mic Lock off and removes it. Asks for your PIN and your Mac
# password, exactly like "Turn off Camera & Mic Lock" in the menu.
if [ -x /usr/local/bin/privacy-lock ]; then
    exec /usr/local/bin/privacy-lock remove
fi
echo "Camera & Mic Lock is not installed."
