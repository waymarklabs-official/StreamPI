"""Where is the box? Trip choice -> a Gismeteo weather city.

No Kodi imports, so it can be tested from a shell:
    python3 where.py            # resolve the saved trip choice
    python3 where.py cruise "Valiant Lady" "Virgin Voyages"

Network-derived "automatic" location is useless when travelling: on the ship
it reported Madrid (Gismeteo's guess from the satellite ISP) or Key West.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as etree

TRAVEL_FILE = "/var/lib/streampi/travel.json"
GISMETEO = "https://services.gismeteo.net/inform-service/inf_chrome/cities/"
CRUISEMAPPER = "https://www.cruisemapper.com"
ADSB = "https://api.adsb.lol/v2/callsign/"      # free, open ADS-B feed

# Flight numbers use the airline's IATA code (DL1234); aircraft broadcast the
# ICAO code (DAL1234). Common airlines; a 3-letter code is used as typed.
AIRLINES = {
    "AA": "AAL", "DL": "DAL", "UA": "UAL", "WN": "SWA", "B6": "JBU", "AS": "ASA",
    "NK": "NKS", "F9": "FFT", "G4": "AAY", "HA": "HAL", "SY": "SCX", "MX": "MXY",
    "AC": "ACA", "WS": "WJA", "PD": "POE", "AM": "AMX", "Y4": "VOI", "CM": "CMP",
    "AV": "AVA", "LA": "LAN", "BA": "BAW", "VS": "VIR", "LH": "DLH", "AF": "AFR",
    "KL": "KLM", "IB": "IBE", "EI": "EIN", "LX": "SWR", "OS": "AUA", "SK": "SAS",
    "AY": "FIN", "TP": "TAP", "AZ": "ITY", "FR": "RYR", "U2": "EZY", "W6": "WZZ",
    "EK": "UAE", "QR": "QTR", "EY": "ETD", "TK": "THY", "QF": "QFA", "NZ": "ANZ",
    "SQ": "SIA", "CX": "CPA", "JL": "JAL", "NH": "ANA", "KE": "KAL",
}
UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# CruiseMapper's own pages for each line; ship pages are found by name there.
LINES = {
    "Virgin Voyages": "Virgin-Voyages-109",
    "Royal Caribbean": "Royal-Caribbean-1",
    "Carnival": "Carnival-Cruise-Line-9",
    "Norwegian": "Norwegian-Cruise-Line-10",
    "MSC": "MSC-Cruises-13",
    "Celebrity": "Celebrity-Cruises-5",
    "Princess": "Princess-Cruises-3",
    "Disney": "Disney-Cruise-Line-12",
    "Holland America": "Holland-America-Line-11",
}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load_travel(path=TRAVEL_FILE):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"mode": "home"}


# ------------------------------------------------------------- cruise --

def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def ship_page(ship, line, cache=None):
    """URL of the ship's CruiseMapper page, remembered in `cache` (a dict)."""
    key = f"{line}|{ship}"
    if cache is not None and key in cache:
        return cache[key]
    want = _slug(ship)
    pages = [LINES[line]] if line in LINES else list(LINES.values())
    for page in pages:
        try:
            html = _get(f"{CRUISEMAPPER}/cruise-lines/{page}")
        except Exception:
            continue
        for path in set(re.findall(r'href="https://www\.cruisemapper\.com/ships/([A-Za-z0-9-]+-\d+)"', html)):
            if _slug(path.rsplit("-", 1)[0]) == want:
                url = f"{CRUISEMAPPER}/ships/{path}"
                if cache is not None:
                    cache[key] = url
                return url
    return None


def ship_position(url):
    """(lat, lon) from the ship page's current-position map."""
    m = re.search(r'"shipCurrentPositionMap":\{"lat":(-?[\d.]+),"lon":(-?[\d.]+)', _get(url))
    return (float(m.group(1)), float(m.group(2))) if m else None


# -------------------------------------------------------------- plane --

def callsign(flight):
    """'DL 1234' / 'dl1234' -> 'DAL1234'; 'DAL1234' stays as is."""
    f = re.sub(r"\s+", "", flight).upper()
    m = re.fullmatch(r"([A-Z]{3})(\d{1,4}[A-Z]?)", f)
    if m:
        return f
    m = re.fullmatch(r"([A-Z0-9]{2})(\d{1,4}[A-Z]?)", f)
    if not m:
        raise LookupError(f"can't read flight number {flight!r}")
    code = AIRLINES.get(m.group(1))
    if not code:
        raise LookupError(f"unknown airline {m.group(1)!r}: type the 3-letter "
                          f"ICAO code instead (e.g. DAL1234)")
    return code + m.group(2).lstrip("0")


def flight_position(flight):
    """(lat, lon) of the aircraft in the air now. LookupError before take-off,
    after landing, or out of receiver range (mid-ocean)."""
    cs = callsign(flight)
    data = json.loads(_get(ADSB + cs))
    for ac in data.get("ac") or []:
        if ac.get("lat") is not None and ac.get("lon") is not None:
            return float(ac["lat"]), float(ac["lon"])
    raise LookupError(f"{cs} not being tracked right now")


# ----------------------------------------------------------- gismeteo --

def _cities(params):
    url = GISMETEO + "?" + urllib.parse.urlencode(dict(params, lang="en"))
    root = etree.fromstring(_get(url).encode())
    return [{"name": c.attrib["n"], "id": c.attrib["id"],
             "country": c.attrib.get("country_name", "")} for c in root]


def nearest_city(lat, lon):
    found = _cities({"lat": lat, "lng": lon, "count": 1, "nocache": 1})
    return found[0] if found else None


def city_named(name):
    found = _cities({"startsWith": name.split(",")[0].strip()})
    if not found:
        return None
    want = name.lower()
    # "Lisbon, Portugal": prefer a match on the country too
    for c in found:
        if c["country"] and c["country"].lower() in want:
            return c
    return found[0]


# ---------------------------------------------------------------- main --

def resolve(travel, cache=None):
    """Weather city for the trip choice, or None for home/automatic.
    Raises on network trouble so the caller can keep the last good city."""
    mode = travel.get("mode", "home")
    if mode == "cruise":
        url = ship_page(travel.get("ship", ""), travel.get("line", ""), cache)
        if not url:
            raise LookupError(f"ship {travel.get('ship')!r} not found on CruiseMapper")
        pos = ship_position(url)
        if not pos:
            raise LookupError(f"no position on {url}")
        city = nearest_city(*pos)
        if city:
            city["position"] = pos
        return city
    if mode == "hotel":
        return city_named(travel.get("city", ""))
    if mode == "plane":
        pos = flight_position(travel.get("flight", ""))
        city = nearest_city(*pos)
        if city:
            city["position"] = pos
        return city
    return None


if __name__ == "__main__":
    if len(sys.argv) > 2:
        t = {"mode": sys.argv[1]}
        if t["mode"] == "cruise":
            t.update(ship=sys.argv[2], line=sys.argv[3] if len(sys.argv) > 3 else "")
        elif t["mode"] == "hotel":
            t["city"] = sys.argv[2]
        elif t["mode"] == "plane":
            t["flight"] = sys.argv[2]
    else:
        t = load_travel()
    print(t, "->", resolve(t))
