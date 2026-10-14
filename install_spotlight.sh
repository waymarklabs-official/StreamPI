#!/usr/bin/env bash
# Installs StreamPI Spotlight: live games for your teams + what's popular on
# your services, shown in the big banner above the app rows.
#
# Put main.py, spotlight.py and apply_home.sh in your home folder, then:
#   bash install_spotlight.sh              (asks for your TMDB key)
#   bash install_spotlight.sh <TMDB_KEY>   (or pass it directly)
set -euo pipefail

ADDON=~/.kodi/addons/plugin.video.streampi.apps
DATA=~/.kodi/userdata/addon_data/plugin.video.streampi.apps
CONF=$DATA/spotlight.json

for f in main.py spotlight.py apply_home.sh; do
    [[ -f ~/$f ]] || { echo "Missing ~/$f — copy it to the Pi first"; exit 1; }
done
python3 -c "import PIL" 2>/dev/null || sudo apt-get install -y python3-pil

mkdir -p "$DATA"
if [[ ! -f "$CONF" ]]; then
    key="${1:-}"
    if [[ -z "$key" ]]; then
        read -rp "TMDB API key (or read access token), blank to skip: " key
    fi
    python3 - "$CONF" "$key" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2].strip()
cfg = {
    "tmdb_key": key,
    "region": "US",
    "favorites": ["Miami Dolphins", "Tennessee Volunteers", "Chicago Cubs"],
    "leagues": ["nfl", "college-football", "mlb"],
}
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
PY
    chmod 600 "$CONF"
    echo "Wrote $CONF (edit it to change teams or the key)"
else
    echo "Keeping existing $CONF"
fi

cp ~/main.py ~/spotlight.py "$ADDON"/
echo "Installed add-on code"

echo
echo "Spotlight preview:"
python3 "$ADDON/spotlight.py" home || echo "(preview failed — check network/key)"
echo

bash ~/apply_home.sh
