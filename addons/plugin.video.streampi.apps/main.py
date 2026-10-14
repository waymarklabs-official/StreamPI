"""StreamPI app tiles for Kodi, grouped into categories.

Paths (point skin widgets at these):
  plugin://plugin.video.streampi.apps/                         category folders
  plugin://plugin.video.streampi.apps/?action=list&cat=all     every app
  plugin://plugin.video.streampi.apps/?action=list&cat=sports  one category

Browser apps launch through systemd (streampi-app@<service>), which stops Kodi
while Chromium runs and brings it back afterwards, crash or not. Entries in
apps.json with a "kodi" path (like YouTube) open that Kodi add-on instead.

Categories, order and taglines live in resources/apps.json. A service added to
/etc/streampi/apps.conf but not to apps.json still shows up under Streaming.
"""

import json
import subprocess
import sys
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

    xbmcplugin.endOfDirectory(HANDLE)


def launch(svc):
    try:
        subprocess.Popen([LAUNCHER, svc], stdout=subprocess.DEVNULL,
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
    else:
        list_categories(catalog)


if __name__ == "__main__":
    main()
