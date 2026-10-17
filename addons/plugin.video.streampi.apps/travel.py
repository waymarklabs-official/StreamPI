"""Travel Mode tile: choose Home / Cruise / Hotel / Plane with the remote.

Writes the same /var/lib/streampi/travel.json as the WiFi setup page; the
service.streampi.location add-on picks it up within a minute and points the
weather at the ship, hotel city or (later) flight. The clock is never changed.
"""

import json
import os
import time

import xbmcgui

TRAVEL_FILE = "/var/lib/streampi/travel.json"
LINES = ["Virgin Voyages", "Royal Caribbean", "Carnival", "Norwegian", "MSC",
         "Celebrity", "Princess", "Disney", "Holland America", "Other"]
SHIPS = {"Virgin Voyages": ["Scarlet Lady", "Valiant Lady", "Resilient Lady",
                            "Brilliant Lady"]}
MODES = [("home", "Home", "Weather uses your normal location"),
         ("cruise", "Cruise", "Weather follows your ship"),
         ("hotel", "Hotel", "Weather for the city you're staying in"),
         ("plane", "Plane", "Weather follows your flight")]


def load():
    try:
        with open(TRAVEL_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"mode": "home"}


def describe(t=None):
    t = t or load()
    mode = t.get("mode", "home")
    if mode == "cruise":
        return f"Cruise: {t.get('ship', '?')}"
    if mode == "hotel":
        return f"Hotel: {t.get('city', '?')}"
    if mode == "plane":
        return f"Plane: {t.get('flight', '?')}"
    return "Home"


def save(t):
    t["set"] = int(time.time())
    tmp = TRAVEL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(t, fh, indent=1)
    os.replace(tmp, TRAVEL_FILE)


def _pick(heading, labels, current=None):
    pre = labels.index(current) if current in labels else -1
    return xbmcgui.Dialog().select(heading, labels, preselect=pre)


def _type(heading, default=""):
    text = xbmcgui.Dialog().input(heading, defaultt=default)
    return text.strip()


def choose():
    """The dialogs. Back/cancel at any step leaves everything unchanged."""
    old = load()
    items = []
    for key, label, hint in MODES:
        li = xbmcgui.ListItem(label, hint)
        items.append(li)
    i = xbmcgui.Dialog().select(f"Travel Mode (now: {describe(old)})", items,
                                useDetails=True,
                                preselect=[m[0] for m in MODES].index(old.get("mode", "home")))
    if i < 0:
        return
    mode = MODES[i][0]
    new = {"mode": mode}

    if mode == "cruise":
        j = _pick("Cruise line", LINES, old.get("line"))
        if j < 0:
            return
        new["line"] = LINES[j]
        ships = SHIPS.get(new["line"], [])
        if ships:
            k = _pick("Ship", ships + ["Other ship..."], old.get("ship"))
            if k < 0:
                return
            new["ship"] = ships[k] if k < len(ships) else _type("Ship name")
        else:
            new["ship"] = _type("Ship name", old.get("ship", ""))
        if not new["ship"]:
            return
    elif mode == "hotel":
        new["city"] = _type("City (e.g. Lisbon, Portugal)", old.get("city", ""))
        if not new["city"]:
            return
    elif mode == "plane":
        new["flight"] = _type("Flight number (e.g. DL1234)",
                              old.get("flight", "")).replace(" ", "").upper()
        if not new["flight"]:
            return

    try:
        save(new)
    except OSError as exc:
        xbmcgui.Dialog().ok("Travel Mode", f"Could not save: {exc}")
        return
    xbmcgui.Dialog().notification("Travel Mode", f"{describe(new)} - weather updates within a minute",
                                  xbmcgui.NOTIFICATION_INFO, 5000)
