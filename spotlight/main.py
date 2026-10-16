"""StreamPI app tiles for Kodi, grouped into categories.

Paths (point skin widgets at these):
  plugin://plugin.video.streampi.apps/                         category folders
  plugin://plugin.video.streampi.apps/?action=list&cat=all     every app
  plugin://plugin.video.streampi.apps/?action=list&cat=sports  one category
  plugin://plugin.video.streampi.apps/?action=spotlight&feed=home|sports|live|streaming
                                                               Spotlight content

Browser apps launch through systemd (streampi-app@<service>), which stops Kodi
while Chromium runs and brings it back afterwards, crash or not. Entries in
apps.json with a "kodi" path (like YouTube) open that Kodi add-on instead.

Categories, order and taglines live in resources/apps.json. A service added to
/etc/streampi/apps.conf but not to apps.json still shows up under Streaming.

Rows learn: every launch is logged to usage.json in the add-on's data folder,
and each row puts the apps you open most, and most recently, first. Apps never
opened keep their apps.json order at the end. Delete usage.json to reset.
"""

import json
import os
import subprocess
import sys
import time
import urllib.parse

import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

ADDON = xbmcaddon.Addon()
HANDLE = int(sys.argv[1])
BASE = sys.argv[0]
LAUNCHER = "/usr/local/bin/streampi-app"
RES = xbmcvfs.translatePath(ADDON.getAddonInfo("path")) + "/resources/"
DATA = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
USAGE = os.path.join(DATA, "usage.json")

# A launch counts half as much after this many days, so a new habit overtakes
# an old one within a couple of weeks.
HALF_LIFE_DAYS = 7
KEEP_LAUNCHES = 50          # per app; older ones barely count anyway


def load_usage():
    try:
        with open(USAGE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def record_launch(app_id):
    usage = load_usage()
    usage[app_id] = (usage.get(app_id, []) + [int(time.time())])[-KEEP_LAUNCHES:]
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = USAGE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(usage, fh)
        os.replace(tmp, USAGE)
    except OSError as exc:                          # never block a launch
        xbmc_log(f"could not save usage: {exc}")


def by_habit(apps):
    """Most-used, most-recent first; ties and unused apps keep their order."""
    usage, now = load_usage(), time.time()

    def score(app):
        return sum(0.5 ** ((now - t) / 86400 / HALF_LIFE_DAYS)
                   for t in usage.get(app["id"], []))
    return sorted(apps, key=score, reverse=True)    # stable sort


def load_catalog():
    try:
        with open(RES + "apps.json", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"categories": [], "apps": {}}


def launcher_services():
    """{id: name} from `streampi-app --list`, in launcher order."""
    try:
        out = subprocess.run([LAUNCHER, "--list"], capture_output=True,
                             text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in out.splitlines()[1:]:
        svc, name = line[:12].strip(), line[13:27].strip()
        if svc:
            found[svc] = name or svc.title()
    return found


def all_apps(catalog):
    """Merge apps.json with the launcher list. apps.json order wins."""
    launched = launcher_services()
    apps = []
    for app_id, cfg in catalog["apps"].items():
        if "kodi" in cfg or app_id in launched:
            apps.append({"id": app_id,
                         "name": cfg.get("name") or launched.get(app_id, app_id),
                         **cfg})
    for app_id, name in launched.items():          # new, uncategorised apps
        if app_id not in catalog["apps"]:
            apps.append({"id": app_id, "name": name, "cats": ["streaming"]})
    return apps


def art(app_id):
    tile = RES + app_id + ".png"
    return {"icon": tile, "thumb": tile, "landscape": tile,
            "fanart": RES + app_id + "-fanart.jpg",
            "clearlogo": RES + app_id + "-clearlogo.png"}


def list_categories(catalog):
    xbmcplugin.setPluginCategory(HANDLE, "Streaming Apps")
    cats = [{"id": "all", "name": "All Apps"}] + catalog["categories"]
    for cat in cats:
        li = xbmcgui.ListItem(label=cat["name"])
        url = f"{BASE}?action=list&cat={cat['id']}"
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)
    xbmcplugin.endOfDirectory(HANDLE)


def list_apps(catalog, cat_id):
    names = {c["id"]: c["name"] for c in catalog["categories"]}
    apps = all_apps(catalog)
    if cat_id != "all":
        apps = [a for a in apps if cat_id in a.get("cats", [])]
    apps = by_habit(apps)

    xbmcplugin.setPluginCategory(HANDLE, names.get(cat_id, "All Apps"))
    xbmcplugin.setContent(HANDLE, "videos")

    for app in apps:
        li = xbmcgui.ListItem(label=app["name"])
        li.setArt(art(app["id"]))
        tag = li.getVideoInfoTag()
        tag.setTitle(app["name"])
        tag.setTagLine(app.get("tagline", ""))
        tag.setPlot(app.get("tagline", f"Open {app['name']}."))
        tag.setGenres([names.get(c, c) for c in app.get("cats", [])])
        li.setProperty("IsPlayable", "false")

        if "kodi" in app:
            xbmcplugin.addDirectoryItem(HANDLE, app["kodi"], li, isFolder=True)
        else:
            url = f"{BASE}?action=launch&service={urllib.parse.quote(app['id'])}"
            xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=False)

    # No disk cache, or the row would keep yesterday's order.
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def list_spotlight(catalog, feed):
    """Spotlight items: live games and popular shows. Selecting one opens the
    app that has it. Falls back to the app tiles so Spotlight is never empty."""
    try:
        addon_dir = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
        if addon_dir not in sys.path:
            sys.path.insert(0, addon_dir)
        from spotlight import Spotlight
        sp = Spotlight(DATA, offline=True)          # cached copy, instantly
        items = sp.feed(feed)
        if sp.stale:
            refresh_spotlight(addon_dir)
    except Exception as exc:                        # never break the home screen
        xbmc_log(f"spotlight feed '{feed}' failed: {exc}")
        items = []
    if not items:
        list_apps(catalog, "all")
        return

    xbmcplugin.setPluginCategory(HANDLE, "Spotlight")
    xbmcplugin.setContent(HANDLE, "videos")
    for it in items:
        li = xbmcgui.ListItem(label=it["title"])
        li.setArt({k: v for k, v in it["art"].items() if v})
        tag = li.getVideoInfoTag()
        tag.setTitle(it["title"])
        tag.setTagLine(it["tagline"])
        tag.setPlot(it["plot"] or it["tagline"])
        tag.setGenres([it["genre"]])
        li.setLabel2(it["tagline"])
        li.setProperty("IsPlayable", "false")
        url = f"{BASE}?action=launch&service={urllib.parse.quote(it['app'])}"
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=False)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def refresh_spotlight(addon_dir):
    """Update stale Spotlight data in the background, for the next visit.
    A separate process, so the home screen never waits on the network."""
    try:
        subprocess.Popen(["/usr/bin/python3", os.path.join(addon_dir, "spotlight.py"),
                          "--refresh", DATA],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as exc:
        xbmc_log(f"could not start spotlight refresh: {exc}")


def xbmc_log(msg):
    import xbmc
    xbmc.log(f"[plugin.video.streampi.apps] {msg}", xbmc.LOGWARNING)


def launch(svc):
    record_launch(svc)
    try:
        subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", "start", "--no-block", f"streampi-app@{svc}.service"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        xbmcgui.Dialog().ok("StreamPI", f"Could not start {svc}: {exc}")


def main():
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
    catalog = load_catalog()
    action = params.get("action")
    if action == "launch" and params.get("service"):
        launch(params["service"])
    elif action == "list":
        list_apps(catalog, params.get("cat", "all"))
    elif action == "spotlight":
        list_spotlight(catalog, params.get("feed", "home"))
    else:
        list_categories(catalog)


if __name__ == "__main__":
    main()
