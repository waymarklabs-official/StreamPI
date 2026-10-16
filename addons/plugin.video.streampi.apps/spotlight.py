"""StreamPI Spotlight feeds: the "top shelf" content above the app rows.

Feeds (plugin://plugin.video.streampi.apps/?action=spotlight&feed=<name>):
  home       your teams + big games, mixed with what's popular on your services
  sports     favorites, playoff games and close games (NFL, college, MLB)
  live       games happening right now (or later today if nothing is on)
  streaming  popular right now on Netflix, Prime Video and Paramount+

Sources: ESPN's public scoreboard JSON (no key) and TMDB "where to watch"
(needs a free API key). Everything is cached so the Pi isn't hitting the
network on every focus change, and a stale cache is used if the network drops.

Kodi reads with offline=True: whatever is cached is shown at once, however
old, and `python3 spotlight.py --refresh` is started in the background to
fetch anything stale for next time. On a slow link (ship, hotel) waiting on
ESPN held the home screen up for 7+ seconds after every app.

This module has no Kodi imports so it can be tested from a normal shell:
    python3 spotlight.py home
"""

import fcntl
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
TMDB = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/"

LEAGUES = {
    #  key                 sport       espn league         label              extra query
    "nfl":              ("football", "nfl",              "NFL",              {}),
    "college-football": ("football", "college-football", "College Football", {"groups": "80", "limit": "300"}),
    "mlb":              ("baseball", "mlb",              "MLB",              {}),
}

DEFAULT_CONFIG = {
    "tmdb_key": "",
    "region": "US",
    "favorites": ["Miami Dolphins", "Tennessee Volunteers", "Chicago Cubs"],
    "leagues": ["nfl", "college-football", "mlb"],
    # TMDB watch-provider ids per app in your launcher
    "providers": {"netflix": "8", "prime": "9", "paramount": "531|2303|2616"},
    # Which app a broadcast opens in. Anything unlisted opens YouTube TV.
    "broadcast_apps": {
        "Prime Video": "prime", "ESPN": "espn", "ESPN2": "espn", "ESPNU": "espn",
        "ESPN+": "espn", "ESPN Deportes": "espn", "Paramount+": "paramount",
        "Netflix": "netflix",
    },
    "default_sports_app": "youtubetv",
}

APP_NAMES = {"netflix": "Netflix", "prime": "Prime Video", "paramount": "Paramount+",
             "youtubetv": "YouTube TV", "espn": "ESPN", "spotify": "Spotify"}

SCORES_TTL = 120               # seconds; keeps live scores current
TMDB_TTL = 6 * 3600


# ---------------------------------------------------------------- plumbing --

class Spotlight:
    def __init__(self, data_dir, config=None, fetch=None, now=None, offline=False):
        self.dir = data_dir
        self.offline = offline      # never wait on the network if cache exists
        self.stale = False          # offline read skipped something; refresh it
        self.cache = os.path.join(data_dir, "cache")
        self.art = os.path.join(data_dir, "art")
        os.makedirs(self.cache, exist_ok=True)
        os.makedirs(self.art, exist_ok=True)
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(config if config is not None else self._load_config())
        self._fetch = fetch or _http_json
        self.now = now or datetime.now(timezone.utc)

    def _load_config(self):
        path = os.path.join(self.dir, "spotlight.json")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _cached(self, key, ttl, url, headers=None):
        """Fetch JSON with a file cache. Falls back to stale data on failure."""
        path = os.path.join(self.cache, hashlib.sha1(key.encode()).hexdigest() + ".json")
        have = os.path.exists(path)
        fresh = have and time.time() - os.path.getmtime(path) < ttl
        if not fresh and have and self.offline:
            self.stale = True       # show the old copy now, refresh later
        elif not fresh:
            try:
                data = self._fetch(url, headers or {})
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                return data
            except Exception:          # network, HTTP or JSON error
                pass
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------ feeds --

    def feed(self, name):
        if name == "sports":
            return self.sports()
        if name == "live":
            games = self.sports()
            live = [g for g in games if g["state"] == "in"]
            if live:
                return live
            today = self.now + timedelta(hours=18)
            return [g for g in games if g["state"] == "pre" and g["start"] < today]
        if name == "streaming":
            return self.streaming()
        # home: the games that matter most, then what's popular on your services
        games = [g for g in self.sports() if g["rank"] >= 60][:4]
        shows = self.streaming()
        return (games + shows)[:12]

    # ---------------------------------------------------------- sports --

    def sports(self):
        games = []
        for key in self.cfg["leagues"]:
            if key not in LEAGUES:
                continue
            sport, league, label, extra = LEAGUES[key]
            url = ESPN.format(sport=sport, league=league)
            if extra:
                url += "?" + urllib.parse.urlencode(extra)
            data = self._cached("espn:" + key, SCORES_TTL, url)
            if not data:
                continue
            for ev in data.get("events", []):
                game = self._game(ev, sport, label)
                if game:
                    games.append(game)
        games.sort(key=lambda g: (-g["rank"], g["start"]))
        return games

    def _game(self, ev, sport, label):
        try:
            comp = ev["competitions"][0]
            status = comp.get("status") or ev["status"]
            state = status["type"]["state"]                      # pre / in / post
            teams = {c["homeAway"]: c for c in comp["competitors"]}
            home, away = teams["home"], teams["away"]
            start = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        except (KeyError, IndexError, ValueError):
            return None

        favs = set(self.cfg["favorites"])
        fav = home["team"].get("displayName") in favs or away["team"].get("displayName") in favs

        notes = " ".join(n.get("headline", "") for n in comp.get("notes", []))
        season_type = (ev.get("season") or {}).get("type")
        if label == "College Football":
            playoff = "Playoff" in notes or "CFP" in notes
        else:
            playoff = season_type == 3

        close = False
        if state == "in":
            try:
                diff = abs(int(home.get("score", 0)) - int(away.get("score", 0)))
            except ValueError:
                diff = 99
            period = status.get("period", 0)
            if sport == "football":
                close = diff <= 8 and period >= 4
            else:
                close = diff <= 2 and period >= 7

        # Only games you'd care about make the shelf.
        if fav:
            if state == "post" and self.now - start > timedelta(hours=30):
                return None
            if state == "pre" and start - self.now > timedelta(days=7):
                return None
        elif not (playoff or close):
            return None
        elif state == "post":
            return None

        # Order: your team live > any close/playoff game live > your team
        # starting soon > your team later this week > your team's last result.
        soon = state == "pre" and start - self.now < timedelta(hours=6)
        if state == "in":
            rank = 200 if fav else 100 + (40 if close else 0) + (30 if playoff else 0)
        elif state == "pre":
            rank = (150 if soon else 80) if fav else (40 if soon else 20) + (30 if playoff else 0)
        else:
            rank = 65                  # only favorites' finals get this far

        networks = [n for b in comp.get("broadcasts", []) for n in b.get("names", [])]
        app = self._app_for(networks)
        a, h = away["team"], home["team"]
        a_name = a.get("shortDisplayName") or a.get("displayName", "Away")
        h_name = h.get("shortDisplayName") or h.get("displayName", "Home")

        if state == "pre":
            title = f"{a_name} at {h_name}"
        else:
            title = f"{a_name} {away.get('score', '')}  ·  {h_name} {home.get('score', '')}"

        detail = status["type"].get("shortDetail", "")
        tags = []
        if fav:
            tags.append("Your team")
        if playoff:
            tags.append(notes.strip() or "Playoffs")
        if close:
            tags.append("Close game")
        if state == "in":
            tags.insert(0, "LIVE")
        tagline = " · ".join(tags + [detail] + ([networks[0]] if networks else []))

        records = []
        for c in (away, home):
            rec = (c.get("records") or [{}])[0].get("summary")
            if rec:
                records.append(f"{c['team'].get('displayName')} ({rec})")
        plot_bits = [" vs ".join(records)] if records else []
        situation = (comp.get("situation") or {}).get("downDistanceText")
        if state == "in" and situation:
            plot_bits.append(situation)
        plot_bits.append(f"Watch on {', '.join(networks) or APP_NAMES.get(app, app)}"
                         f" — opens {APP_NAMES.get(app, app)}.")

        fanart = self._matchup_art(ev.get("id", title), a, h)
        return {
            "kind": "game", "state": state, "rank": rank, "start": start,
            "title": title, "tagline": tagline, "plot": "\n".join(plot_bits),
            "genre": label, "app": app,
            "art": {"fanart": fanart, "landscape": fanart, "thumb": fanart,
                    "icon": h.get("logo", "")},
        }

    def _app_for(self, networks):
        apps = self.cfg["broadcast_apps"]
        for n in networks:
            if n in apps:
                return apps[n]
        return self.cfg["default_sports_app"]

    def _matchup_art(self, ev_id, away, home):
        """1920x1080 split in the two teams' colors with their logos.
        Scores aren't drawn in, so each matchup is rendered only once."""
        out = os.path.join(self.art, f"game-{ev_id}.jpg")
        if os.path.exists(out):
            return out
        if self.offline:            # drawing needs logo downloads: do it later
            self.stale = True
            return home.get("logo", "")
        try:
            from PIL import Image, ImageDraw, ImageFilter
        except ImportError:
            return home.get("logo", "")

        W, H = 1920, 1080
        img = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)
        ca = _dim(_hex(away.get("color"), (40, 40, 60)))
        ch = _dim(_hex(home.get("color"), (60, 40, 40)))
        draw.polygon([(0, 0), (W * 0.58, 0), (W * 0.42, H), (0, H)], fill=ca)
        draw.polygon([(W * 0.58, 0), (W, 0), (W, H), (W * 0.42, H)], fill=ch)

        for team, cx in ((away, W * 0.25), (home, W * 0.75)):
            logo = self._logo(team)
            if logo is None:
                continue
            logo.thumbnail((560, 560), Image.LANCZOS)
            shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
            shadow.putalpha(logo.split()[3].point(lambda v: int(v * 0.45)))
            shadow = shadow.filter(ImageFilter.GaussianBlur(18))
            x, y = int(cx - logo.width / 2), int(H * 0.42 - logo.height / 2)
            img.paste(shadow, (x + 12, y + 16), shadow)
            img.paste(logo, (x, y), logo)

        # Darken the lower third so Spotlight text stays readable.
        fade = Image.new("L", (1, H))
        for y in range(H):
            fade.putpixel((0, y), int(max(0, (y - H * 0.45) / (H * 0.55)) * 200))
        black = Image.new("RGB", (W, H), (0, 0, 0))
        img = Image.composite(black, img, fade.resize((W, H)))
        img.save(out, quality=88)
        return out

    def _logo(self, team):
        url = team.get("logo")
        if not url:
            return None
        from PIL import Image
        path = os.path.join(self.art, f"logo-{team.get('id', hashlib.sha1(url.encode()).hexdigest()[:8])}.png")
        if not os.path.exists(path):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "StreamPI"})
                with urllib.request.urlopen(req, timeout=8) as r, open(path, "wb") as fh:
                    fh.write(r.read())
            except Exception:
                return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            return None

    # ------------------------------------------------------- streaming --

    def streaming(self):
        key = (self.cfg.get("tmdb_key") or "").strip()
        if not key:
            return []
        if key.startswith("eyJ"):                       # v4 read access token
            auth, headers = {}, {"Authorization": "Bearer " + key}
        else:                                           # v3 API key
            auth, headers = {"api_key": key}, {}

        per_app = []
        for app, provider_ids in self.cfg["providers"].items():
            picks = []
            for media in ("tv", "movie"):
                q = dict(auth, watch_region=self.cfg["region"],
                         with_watch_providers=provider_ids,
                         with_watch_monetization_types="flatrate",
                         sort_by="popularity.desc", language="en-US")
                url = f"{TMDB}/discover/{media}?" + urllib.parse.urlencode(q)
                data = self._cached(f"tmdb:{app}:{media}", TMDB_TTL, url, headers)
                for r in (data or {}).get("results", [])[:3]:
                    if r.get("backdrop_path"):
                        picks.append(self._show(r, media, app, auth, headers))
            # alternate series / movie within each app
            tv = [p for p in picks if p["genre"] == "Series"]
            mv = [p for p in picks if p["genre"] == "Movie"]
            per_app.append([x for pair in zip(tv, mv) for x in pair] + tv[len(mv):] + mv[len(tv):])

        # round-robin across apps so one service doesn't own the shelf
        out = []
        for i in range(max((len(p) for p in per_app), default=0)):
            for p in per_app:
                if i < len(p):
                    out.append(p[i])
        return out

    def _show(self, r, media, app, auth, headers):
        title = r.get("name") or r.get("title") or ""
        date = r.get("first_air_date") or r.get("release_date") or ""
        app_name = APP_NAMES.get(app, app)
        kind = "Series" if media == "tv" else "Movie"
        logo = ""
        imgs = self._cached(f"tmdb:logo:{media}:{r['id']}", 7 * 86400,
                            f"{TMDB}/{media}/{r['id']}/images?" + urllib.parse.urlencode(
                                dict(auth, include_image_language="en,null")), headers)
        logos = (imgs or {}).get("logos") or []
        if logos:
            logo = TMDB_IMG + "w500" + logos[0]["file_path"]
        backdrop = TMDB_IMG + "w1280" + r["backdrop_path"]
        score = r.get("vote_average")
        tagline = " · ".join(filter(None, [
            f"Popular on {app_name}", kind, date[:4],
            f"★ {score:.1f}" if score else ""]))
        return {
            "kind": "show", "state": "", "rank": 0, "start": self.now,
            "title": title, "tagline": tagline, "plot": r.get("overview", ""),
            "genre": kind, "app": app,
            "art": {"fanart": backdrop, "landscape": backdrop, "thumb": backdrop,
                    "poster": (TMDB_IMG + "w500" + r["poster_path"]) if r.get("poster_path") else "",
                    "clearlogo": logo},
        }


# ------------------------------------------------------------- helpers --

def _http_json(url, headers):
    req = urllib.request.Request(url, headers=dict({"User-Agent": "StreamPI",
                                                    "Accept": "application/json"}, **headers))
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def _hex(value, default):
    try:
        v = (value or "").lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def _dim(rgb, factor=0.7):
    return tuple(int(c * factor) for c in rgb)


FEEDS = ("home", "sports", "live", "streaming")


def refresh(data_dir):
    """Fetch everything stale and draw missing art. One run at a time."""
    try:
        lock = open(os.path.join(data_dir, ".refresh.lock"), "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                      # another refresh is already running
    sp = Spotlight(data_dir)
    for name in FEEDS:
        try:
            sp.feed(name)
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    data_dir = os.path.expanduser("~/.kodi/userdata/addon_data/plugin.video.streampi.apps")
    if sys.argv[1:2] == ["--refresh"]:
        refresh(sys.argv[2] if len(sys.argv) > 2 else data_dir)
        sys.exit(0)
    for item in Spotlight(data_dir).feed(sys.argv[1] if len(sys.argv) > 1 else "home"):
        print(f"{item['title']:<40} {item['tagline']}  -> {item['app']}")
