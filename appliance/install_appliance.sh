#!/usr/bin/env bash
# StreamPI appliance mode: boot straight into Kodi, no desktop.
#
# Wires up the handover the StreamPI files were designed for:
#   kodi.service  <-- Conflicts= -->  streampi-app@<service>.service
# Picking an app stops Kodi and runs Chromium inside cage on the TV; exiting
# (or a crash) brings Kodi back via ExecStopPost.
#
#   sudo bash install_appliance.sh          install, then reboot
#   sudo bash install_appliance.sh --undo   back to the desktop, then reboot
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo bash $0 ${1:-}"; exit 1; }

USER_NAME=lights
UID_NUM=$(id -u "$USER_NAME")
LAUNCHER=/usr/local/bin/streampi-app
ADDON_MAIN=/home/$USER_NAME/.kodi/addons/plugin.video.streampi.apps/main.py
UNIT=/etc/systemd/system/kodi.service
SUDOERS=/etc/sudoers.d/streampi
BACKUP=/home/$USER_NAME/streampi-appliance-backup

if [[ "${1:-}" == "--undo" ]]; then
    [[ -d "$BACKUP" ]] || { echo "No backup at $BACKUP"; exit 1; }
    systemctl disable kodi.service 2>/dev/null || true
    rm -f "$UNIT" "$SUDOERS"
    cp "$BACKUP/streampi-app" "$LAUNCHER"
    cp "$BACKUP/main.py" "$ADDON_MAIN"
    chown "$USER_NAME:$USER_NAME" "$ADDON_MAIN"
    systemctl daemon-reload
    raspi-config nonint do_boot_behaviour B4      # desktop, auto-login
    echo "Restored desktop mode. Reboot to apply:  sudo reboot"
    exit 0
fi

# 0. Preflight -------------------------------------------------------------
command -v cage >/dev/null || { echo "Installing cage..."; apt-get install -y cage; }
[[ -f "$LAUNCHER" && -f "$ADDON_MAIN" ]] || { echo "StreamPI files not found"; exit 1; }

# 1. Backup (only the first time, so --undo always returns to the original) -
if [[ ! -d "$BACKUP" ]]; then
    mkdir -p "$BACKUP"
    cp "$LAUNCHER" "$ADDON_MAIN" "$BACKUP"/
    chown -R "$USER_NAME:$USER_NAME" "$BACKUP"
    echo "Backed up launcher and add-on to $BACKUP"
fi

# 2. kodi.service — the unit everything else already expects ---------------
cat > "$UNIT" <<EOF
[Unit]
Description=Kodi (StreamPI)
After=network-online.target systemd-user-sessions.service
Wants=network-online.target
# Kodi owns tty1 and the display; no login prompt underneath it.
Conflicts=getty@tty1.service
After=getty@tty1.service

[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
SupplementaryGroups=video render input audio tty
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
StandardInput=tty
StandardOutput=journal
StandardError=journal
Environment=HOME=/home/$USER_NAME
Environment=XDG_RUNTIME_DIR=/run/user/$UID_NUM
# Standalone on the bare display, ALSA audio (HDMI-0 is pinned in asound.conf).
ExecStart=/usr/bin/kodi --standalone --windowing=gbm --audio-backend=alsa
# Exit from Kodi's menu brings it straight back, like a real box.
# (Being stopped for an app does not count as exiting, so no restart loop.)
Restart=always
RestartSec=2
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
echo "Wrote $UNIT"

# 3. Run Chromium inside cage, as the launcher's own comments intend --------
if ! grep -q 'exec cage' "$LAUNCHER"; then
    sed -i 's|^\(\s*\)exec "\$bin" "\${flags\[@\]}"|\1exec cage -s -- "$bin" "${flags[@]}"|' "$LAUNCHER"
fi
grep -q 'exec cage -s --' "$LAUNCHER" || { echo "Could not patch $LAUNCHER"; exit 1; }
echo "Launcher now runs Chromium inside cage"

# 4. Let Kodi (running as $USER_NAME) start app units, and nothing else ------
cat > "$SUDOERS.tmp" <<EOF
# StreamPI: Kodi may start the app units that take over the display.
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block streampi-app@*.service
EOF
visudo -cf "$SUDOERS.tmp" >/dev/null
chmod 440 "$SUDOERS.tmp"
mv "$SUDOERS.tmp" "$SUDOERS"
echo "Added $SUDOERS"

# 5. Add-on launches through systemd instead of directly --------------------
python3 - "$ADDON_MAIN" <<'PY'
import sys
path = sys.argv[1]
src = open(path, encoding="utf-8").read()
old = "subprocess.Popen([LAUNCHER, svc],"
new = ('subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", "start", '
       '"--no-block", f"streampi-app@{svc}.service"],')
if new not in src:
    if old not in src:
        sys.exit("Could not find the launch call in main.py")
    open(path, "w", encoding="utf-8").write(src.replace(old, new))
print("Add-on now launches apps through systemd")
PY

# 6. Boot to console, Kodi takes the screen --------------------------------
raspi-config nonint do_boot_behaviour B1      # console, no auto-login
systemctl daemon-reload
systemctl enable kodi.service
echo
echo "Done. Reboot to switch over:  sudo reboot"
echo "Undo any time:                sudo bash $0 --undo"
