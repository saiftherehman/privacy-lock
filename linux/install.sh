#!/bin/sh
# Installs privacy-lock system-wide (root-owned, so user programs can't modify it).
# Works with systemd, and with OpenRC + elogind.
set -e
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
cd "$(dirname "$0")"

# ---- dependencies
missing=""
need_cmd() { command -v "$1" >/dev/null 2>&1 || missing="$missing $2"; }
need_cmd python3 python3
need_cmd setfacl acl
need_cmd amixer alsa-utils
need_cmd pkexec polkit
need_cmd notify-send libnotify
need_cmd loginctl "systemd-or-elogind"
need_cmd udevadm udev
python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Notify", "0.7")' \
    2>/dev/null || missing="$missing python-gobject+gtk3+libnotify"
python3 - 2>/dev/null <<'PY' || missing="$missing appindicator"
import gi
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
except ValueError:
    gi.require_version("AppIndicator3", "0.1")
PY
if [ -n "$missing" ]; then
    echo "Missing:$missing"
    echo "Install them first, for example:"
    echo "  Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 libnotify-bin acl alsa-utils pkexec"
    echo "  Fedora:        sudo dnf install python3-gobject gtk3 libayatana-appindicator-gtk3 libnotify acl alsa-utils polkit"
    echo "  Arch:          sudo pacman -S python-gobject gtk3 libayatana-appindicator libnotify acl alsa-utils polkit"
    echo "  openSUSE:      sudo zypper install python3-gobject-Gdk typelib-1_0-AyatanaAppIndicator3-0_1 libnotify-tools acl alsa-utils polkit"
    exit 1
fi

if [ -d /run/systemd/system ]; then
    init=systemd
elif command -v openrc-run >/dev/null 2>&1 || [ -x /sbin/openrc-run ]; then
    init=openrc
else
    echo "This init system is not supported yet (only systemd and OpenRC)."
    exit 1
fi

# ---- files
install -o root -g root -m 0755 privacy-lock        -D /usr/local/sbin/privacy-lock
install -o root -g root -m 0644 ../common/privacy_lock_core.py -D /usr/local/lib/privacy-lock/privacy_lock_core.py
install -o root -g root -m 0755 privacy-lock-helper -D /usr/local/libexec/privacy-lock-helper
install -o root -g root -m 0755 privacy-lock-pin-helper /usr/local/libexec/privacy-lock-pin-helper
install -o root -g root -m 0755 privacy-lock-pin-check /usr/local/libexec/privacy-lock-pin-check
install -o root -g root -m 0755 privacy-lock-tray   -D /usr/local/bin/privacy-lock-tray
ln -sf /usr/local/sbin/privacy-lock /usr/local/bin/privacy-lock

install -o root -g root -m 0644 io.github.saiftherehman.privacylock.policy -D /usr/share/polkit-1/actions/io.github.saiftherehman.privacylock.policy
install -o root -g root -m 0644 privacy-lock-tray.desktop    -D /usr/share/applications/privacy-lock-tray.desktop

if [ "$init" = systemd ]; then
    install -o root -g root -m 0644 privacy-lock-boot.service    /etc/systemd/system/
    install -o root -g root -m 0644 privacy-lock-login.service   /etc/systemd/system/
    install -o root -g root -m 0644 privacy-lock-tray.service    -D /etc/systemd/user/privacy-lock-tray.service
    rm -f /etc/xdg/autostart/privacy-lock-tray.desktop  # the user service starts the menu
    systemctl daemon-reload
    systemctl enable privacy-lock-boot.service privacy-lock-login.service >/dev/null 2>&1
    systemctl --global enable privacy-lock-tray.service >/dev/null 2>&1
else
    install -o root -g root -m 0755 privacy-lock-boot.openrc  /etc/init.d/privacy-lock-boot
    install -o root -g root -m 0755 privacy-lock-login.openrc /etc/init.d/privacy-lock-login
    rc-update add privacy-lock-boot boot >/dev/null
    rc-update add privacy-lock-login default >/dev/null
    # No user services: the desktop starts the menu, which restarts itself.
    sed 's|^Exec=.*|Exec=/usr/local/bin/privacy-lock-tray --supervise|' privacy-lock-tray.desktop \
        > /etc/xdg/autostart/privacy-lock-tray.desktop
    chmod 0644 /etc/xdg/autostart/privacy-lock-tray.desktop
fi

# Move blocks made by an older version (kept across restarts) to the new scheme.
[ -d /etc/privacy-lock ] && chmod 755 /etc/privacy-lock
/usr/local/sbin/privacy-lock migrate

echo "Installed."
/usr/local/sbin/privacy-lock check || echo "WARNING: fix the above before setting up the PIN (Settings > System > Users)."
[ -f /etc/privacy-lock/pin.json ] || echo "Next: set your PIN from the lock icon in the top bar, or run: sudo privacy-lock setup"
# Start (or restart with the new version) the menu for the person installing.
if [ -n "$SUDO_USER" ]; then
    pkill -u "$SUDO_USER" -f /usr/local/bin/privacy-lock-tray || true
    if [ "$init" = systemd ]; then
        systemctl --user --machine="$SUDO_USER@" daemon-reload || true
        systemctl --user --machine="$SUDO_USER@" restart privacy-lock-tray.service || true
    else
        echo "Log out and back in to start the lock icon."
    fi
fi
echo "The lock icon is in the top bar. It starts at every login and comes back by itself if closed."
