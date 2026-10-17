"""StreamPI travel location: keeps the weather pointed at where you are.

Every 2 hours, and within a minute of the trip choice changing, resolves the
trip in /var/lib/streampi/travel.json (see resources/lib/where.py) to a
Gismeteo city and makes it the weather location. Choosing Home puts the
weather back exactly as it was. The time zone is never touched: it stays on
the place the trip started from.
"""

import json
import os
import sys
import time

import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
sys.path.insert(0, os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo("path")),
                                "resources", "lib"))
import where  # noqa: E402

DATA = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
STATE = os.path.join(DATA, "state.json")
REFRESH = 2 * 3600        # ships move ~20 knots: 2 h is plenty for weather
RETRY = 10 * 60           # after a network failure


def log(msg):
    xbmc.log(f"[service.streampi.location] {msg}", xbmc.LOGINFO)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(DATA, exist_ok=True)
    with open(STATE + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(STATE + ".tmp", STATE)


def weather_addon():
    try:
        return xbmcaddon.Addon("weather.gismeteo")
    except RuntimeError:
        return None


def use_slot_one():
    """Show weather slot 1 and fetch it now (Kodi otherwise keeps showing the
    old place until its next scheduled update)."""
    xbmc.executeJSONRPC(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "Settings.SetSettingValue",
        "params": {"setting": "weather.currentlocation", "value": 1}}))
    xbmc.executebuiltin("Weather.Refresh")


def check_screen(state):
    """If the weather on screen isn't the city we set, refresh it."""
    city = state.get("city")
    shown = xbmc.getInfoLabel("Weather.Location")
    if city and shown and shown != city["name"]:
        log(f"screen shows {shown!r}, expected {city['name']!r}: refreshing")
        xbmc.executebuiltin("Weather.Refresh")


def apply_city(state, city):
    w = weather_addon()
    if not w:
        log("weather.gismeteo not installed; nothing to point")
        return
    if "before" not in state:            # remember the home setup once
        state["before"] = {"CurrentLocation": w.getSettingBool("CurrentLocation"),
                           "Location1": w.getSetting("Location1"),
                           "Location1ID": w.getSetting("Location1ID")}
    label = city["name"] + (f", {city['country']}" if city.get("country") else "")
    if (w.getSetting("Location1ID") != city["id"] or
            w.getSettingBool("CurrentLocation")):
        w.setSettingBool("CurrentLocation", False)
        w.setSetting("Location1", label)
        w.setSetting("Location1ID", city["id"])
        use_slot_one()
        log(f"weather now {label} (id {city['id']}, at {city.get('position')})")
    state["city"] = city


def restore_home(state):
    before = state.pop("before", None)
    state.pop("city", None)
    w = weather_addon()
    if not before or not w:
        return
    w.setSettingBool("CurrentLocation", before["CurrentLocation"])
    w.setSetting("Location1", before["Location1"])
    w.setSetting("Location1ID", before["Location1ID"])
    use_slot_one()
    log("home: weather restored to how it was before travelling")


def update(state):
    """One pass. Returns seconds until the next scheduled pass."""
    travel = where.load_travel()
    state["mode"] = travel.get("mode", "home")
    if state["mode"] == "home":
        restore_home(state)
        return REFRESH
    try:
        city = where.resolve(travel, state.setdefault("pages", {}))
    except NotImplementedError as exc:
        log(str(exc))
        return REFRESH
    except Exception as exc:               # network, site change: keep last city
        log(f"lookup failed, keeping {state.get('city', {}).get('name')}: {exc}")
        return RETRY
    if city:
        apply_city(state, city)
    return REFRESH


def travel_mtime():
    try:
        return os.path.getmtime(where.TRAVEL_FILE)
    except OSError:
        return 0


def main():
    monitor = xbmc.Monitor()
    if monitor.waitForAbort(15):           # let Kodi and the network settle
        return
    state = load_state()
    seen = None
    next_run = 0
    while not monitor.abortRequested():
        mtime = travel_mtime()
        if mtime != seen or time.time() >= next_run:
            seen = mtime
            try:
                next_run = time.time() + update(state)
                save_state(state)
                if monitor.waitForAbort(20):      # give the refresh a moment
                    break
                if state.get("mode", "home") != "home":
                    check_screen(state)
            except Exception as exc:       # never let the service die
                log(f"update failed: {exc}")
                next_run = time.time() + RETRY
        if monitor.waitForAbort(60):
            break


main()
