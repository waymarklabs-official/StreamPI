# StreamPI

Raspberry Pi 4 streaming box: Kodi 21 (Arctic Fuse 2) with streaming services
launched in Chromium, driven by an FB-RCS touch remote.

| Folder | What it is | Installed to |
|---|---|---|
| `install.sh`, `preflight.sh` | Base installer (run `sudo ./preflight.sh` first) | — |
| `bin/` | `streampi-app` launcher, audio setup, netgate, ytcache | `/usr/local/bin/` |
| `portal/` | `streampi-portal` | `/usr/local/bin/` |
| `systemd/` | Units (`@USER@`/`@UID@` filled in by `install.sh`) | `/etc/systemd/system/` |
| `config/` | `apps.conf`, `ytcache.conf`, ALSA, polkit, Kodi settings/sources | `/etc/streampi/`, `/etc/`, `~/.kodi/userdata/` |
| `addons/` | `plugin.video.streampi.apps` (app tiles + spotlight), `service.streampi.ytcache` | `~/.kodi/addons/` |
| `remote/` | `fb_remote.py` + `fb-remote.service`, `remote_probe.py` (touch-pad probe) | service runs `~/fb_remote.py` |
| `chromium/remote-dpad/` | Chromium extension: d-pad focus, hidden cursor/scrollbars | — |
| `appliance/` | `install_appliance.sh` — boot straight to Kodi (`--undo` to revert) | — |
| `spotlight/` | Spotlight-enabled `main.py`, home layout (`apply_home.sh`), installer | — |
| `tools/` | `make_tiles.py` — regenerates tile art from `~/tile-src/` | — |

Not in the repo (secrets / per-box): `/etc/streampi/youtube-cookies.txt`,
`~/.kodi/userdata/addon_data/plugin.video.streampi.apps/spotlight.json` (TMDB key).

## Useful commands

```
journalctl -u fb-remote -f          # what the remote is doing
streampi-app --check                 # chromium / widevine / cage sanity
grep -iE "streampi|Traceback" ~/.kodi/temp/kodi.log | tail
```
