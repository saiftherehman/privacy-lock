#!/bin/sh
# Installs Camera & Mic Lock for macOS (root-owned, so user programs can't modify it).
#
# Needs Apple's Python 3 (/usr/bin/python3; macOS offers to install the Command
# Line Tools the first time it is run). A Python that the user can change
# (Homebrew, python.org) is refused: a program running as the user could then
# change the lock too.
set -e
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
cd "$(dirname "$0")"

DIR="/Library/Application Support/privacy-lock"
PY=/usr/bin/python3

if ! "$PY" -c 'import sys; assert sys.version_info >= (3, 9)' 2>/dev/null; then
    echo "Apple's Python 3 is missing. Run: xcode-select --install   then run this again."
    exit 1
fi
PREFIX=$("$PY" -c 'import sys; print(sys.base_prefix)')
if [ -n "$(find "$PREFIX" -maxdepth 0 \( -perm -g+w -o -perm -o+w \) -print)" ] \
        || [ "$(stat -f %u "$PREFIX")" != 0 ]; then
    echo "Python at $PREFIX can be changed without the admin password. Refusing to install."
    exit 1
fi

# ---- stop an older version
launchctl bootout system/io.github.saiftherehman.privacylock.daemon 2>/dev/null || true
CONSOLE_UID=$(stat -f %u /dev/console)
[ "$CONSOLE_UID" != 0 ] && launchctl bootout "gui/$CONSOLE_UID/io.github.saiftherehman.privacylock.menu" 2>/dev/null || true

# ---- files
mkdir -p "$DIR"
chown root:wheel "$DIR"
chmod 755 "$DIR"
install -o root -g wheel -m 0644 privacy_lock_*.py ../common/privacy_lock_*.py "$DIR/"
[ -x "$DIR/venv/bin/python3" ] || "$PY" -m venv "$DIR/venv"
"$DIR/venv/bin/python3" -m pip install --quiet --disable-pip-version-check --upgrade \
    pystray pillow pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz \
    pyobjc-framework-OpenDirectory
chown -R root:wheel "$DIR"
chmod -R go-w "$DIR"
mkdir -p "$DIR/data"
chmod 700 "$DIR/data"

cat > /usr/local/bin/privacy-lock.tmp <<CMD
#!/bin/sh
exec "$DIR/venv/bin/python3" "$DIR/privacy_lock.py" "\$@"
CMD
mkdir -p /usr/local/bin
mv /usr/local/bin/privacy-lock.tmp /usr/local/bin/privacy-lock
chown root:wheel /usr/local/bin/privacy-lock
chmod 755 /usr/local/bin/privacy-lock

install -o root -g wheel -m 0644 io.github.saiftherehman.privacylock.daemon.plist /Library/LaunchDaemons/
install -o root -g wheel -m 0644 io.github.saiftherehman.privacylock.menu.plist /Library/LaunchAgents/

# ---- start
launchctl bootstrap system /Library/LaunchDaemons/io.github.saiftherehman.privacylock.daemon.plist
for i in 1 2 3 4 5 6 7 8 9 10; do [ -S /var/run/privacy-lock.sock ] && break; sleep 1; done
[ "$CONSOLE_UID" != 0 ] && launchctl bootstrap "gui/$CONSOLE_UID" /Library/LaunchAgents/io.github.saiftherehman.privacylock.menu.plist || true

echo "Installed."
if [ -n "$SUDO_USER" ]; then
    sudo -u "$SUDO_USER" /usr/local/bin/privacy-lock check \
        || echo "WARNING: fix the above before setting up the PIN (System Settings > Users & Groups)."
fi
[ -f /var/run/privacy-lock/pin-set ] || echo "Next: set your PIN from the lock icon in the menu bar, or run: privacy-lock setup"
echo
echo "Note: on a Mac, a block stops any app using the camera or microphone within"
echo "about half a second and keeps the microphone muted. macOS does not allow a"
echo "tool like this to make the devices disappear completely."
