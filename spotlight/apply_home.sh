#!/usr/bin/env bash
# StreamPI home layout for Arctic Fuse 2.
#
# Writes the side menu so each entry has real widget rows and a Spotlight,
# instead of opening a folder when clicked. Backs up the current layout first.
#
#   bash apply_home.sh            apply the layout
#   bash apply_home.sh --undo     restore the most recent backup
set -euo pipefail

NODES=~/.kodi/userdata/addon_data/script.skinvariables/nodes/skin.arctic.fuse.2
BACKUPS=~/streampi-home-backups

if [[ "${1:-}" == "--undo" ]]; then
    # Only the dated layout backups; other StreamPI backups share the folder.
    last=$(ls -1d "$BACKUPS"/20[0-9][0-9]-*/ 2>/dev/null | tail -1)
    [[ -n "$last" ]] || { echo "No backup found in $BACKUPS"; exit 1; }
    sudo systemctl stop kodi
    cp "$last"*.json "$NODES"/
    sed -i '/id="script-skinvariables-generator-hash"/d' \
        ~/.kodi/userdata/addon_data/skin.arctic.fuse.2/settings.xml
    sudo systemctl start kodi
    echo "Restored layout from $last"
    exit 0
fi

stamp=$(date +%F-%H%M%S)
mkdir -p "$BACKUPS/$stamp"
cp "$NODES"/*.json "$BACKUPS/$stamp"/
echo "Backed up current layout to $BACKUPS/$stamp"

# Kodi rewrites these files on exit, so it must be stopped while we write.
# If it isn't running (an app has the screen), leave it that way: starting it
# would close the app. It picks up the new layout when it comes back.
kodi_was_up=false
systemctl is-active --quiet kodi && kodi_was_up=true
sudo systemctl stop kodi

python3 - "$NODES" <<'PY'
import json, os, sys, uuid

nodes = sys.argv[1]
APPS = "plugin://plugin.video.streampi.apps/?action=list&cat="
ICONS = "special://skin/extras/icons/"

def guid():
    return "guid-" + uuid.uuid4().hex[:8]

def row(label, cat, style="Landscape"):
    return {"label": label, "path": APPS + cat, "widget_style": style,
            "target": "videos", "guid": guid()}

SPOT = "plugin://plugin.video.streampi.apps/?action=spotlight&feed="

def menu_item(label, icon, rows, spotlight_path):
    return {
        "label": label,
        "icon": ICONS + icon,
        # Clicking the menu entry just stays on Home; rows do the work.
        "path": "ActivateWindow(Home)",
        "target": "",
        "widgets": rows,
        "guid": guid(),
        "spotlight_label": label,
        "spotlight_path": spotlight_path,
        "spotlight_icon": ICONS + icon,
        "spotlight_target": "videos",
    }

sidemenu = [
    menu_item("Home", "house.png", [
        row("All Apps", "all"),
        row("Live TV", "live"),
        row("Sports", "sports"),
        row("Streaming", "streaming"),
        row("Music", "music"),
        row("System", "system"),
    ], SPOT + "home"),
    menu_item("Live TV", "tv.png",          [row("Live TV", "live")],          SPOT + "live"),
    menu_item("Sports", "football.png",     [row("Sports", "sports")],         SPOT + "sports"),
    menu_item("Streaming", "film.png",      [row("Streaming", "streaming")],   SPOT + "streaming"),
    menu_item("Music", "stream-music.png",  [row("Music", "music")],           APPS + "music"),
]

with open(os.path.join(nodes, "skinvariables-shortcut-sidemenu.json"), "w") as fh:
    json.dump(sidemenu, fh, indent=4)

# Global widgets repeat on every menu entry, which would double up the rows.
with open(os.path.join(nodes, "skinvariables-shortcut-sidemenu-globalwidgets.json"), "w") as fh:
    json.dump([], fh)

# The two leftover custom tab lists are no longer referenced by the menu.
for name in os.listdir(nodes):
    if name.startswith("skinvariables-shortcut-sidemenu-customnode-"):
        os.remove(os.path.join(nodes, name))

print("Wrote new side menu: Home (with a System row), Live TV, Sports, Streaming, Music")
PY

# Arctic Fuse builds the home screen from these files only when its stored
# fingerprint changes, and the fingerprint doesn't cover them. Dropping it
# makes the skin rebuild on the next start (takes ~12 s, then reloads).
sed -i '/id="script-skinvariables-generator-hash"/d' \
    ~/.kodi/userdata/addon_data/skin.arctic.fuse.2/settings.xml

$kodi_was_up && sudo systemctl start kodi
echo "Done. Undo any time with:  bash apply_home.sh --undo"
