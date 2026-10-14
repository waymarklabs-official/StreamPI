"""Delete cached YouTube videos once they have been watched past the threshold.

Why sampling rather than just reading the final position: Kodi tears the player
down before onPlayBackStopped fires, so getTime() and getTotalTime() both raise
by the time the callback runs. The only reliable way to know how far someone got
is to sample while playback is live and keep the high-water mark.

We track the *furthest* point reached, not the last one. Someone who watches to
90% and then skips back to re-hear a line has still watched the video, and the
space should come back.
"""

import json
import os
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")

CACHE_DIR = "/var/lib/streampi/youtube"
STATE_FILE = os.path.join(CACHE_DIR, ".state.json")
SAMPLE_SECONDS = 5

# Positions in the first few seconds are noise -- Kodi reports 0 while the
# demuxer spins up, and a resumed video jumps straight to its bookmark.
MIN_SAMPLE_TIME = 3.0


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {msg}", level)


def threshold():
    try:
        return float(ADDON.getSettingInt("threshold"))
    except (ValueError, TypeError):
        return 75.0


def notify(title, message, icon=xbmcgui.NOTIFICATION_INFO, ms=4000):
    if ADDON.getSettingBool("notify"):
        xbmcgui.Dialog().notification(title, message, icon, ms)


def read_state():
    """Shared with the streampi-ytcache sweeper. Always re-read before writing;
    the timer may have rewritten it since we last looked."""
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(data):
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        os.replace(tmp, STATE_FILE)   # atomic, so the sweeper never sees a half file
        return True
    except OSError as exc:
        log(f"could not write state: {exc}", xbmc.LOGERROR)
        return False


def video_id_for(path):
    """Cached files are named <id>.<ext>; anything else is not ours."""
    if not path:
        return None
    try:
        if not os.path.realpath(path).startswith(os.path.realpath(CACHE_DIR)):
            return None
    except OSError:
        return None
    name = os.path.basename(path)
    vid = name.rsplit(".", 1)[0]
    return vid if len(vid) == 11 else None


def purge(vid, percent):
    """Remove every artifact for this id and drop it from shared state."""
    removed, freed = [], 0
    try:
        entries = os.listdir(CACHE_DIR)
    except OSError as exc:
        log(f"cannot list cache dir: {exc}", xbmc.LOGERROR)
        return

    for name in entries:
        if not name.startswith(vid + "."):
            continue
        path = os.path.join(CACHE_DIR, name)
        try:
            freed += os.path.getsize(path)
            os.remove(path)
            removed.append(name)
        except OSError as exc:
            log(f"could not remove {name}: {exc}", xbmc.LOGWARNING)

    state = read_state()
    title = state.get(vid, {}).get("title", vid)
    state.pop(vid, None)
    write_state(state)

    if removed:
        mb = freed / 1024 ** 2
        log(f"deleted {vid} after {percent:.0f}% watched, freed {mb:.0f} MB")
        notify("Space reclaimed", f"{title[:40]} — {mb:.0f} MB freed")
        # Drop the now-missing file from the library so it does not linger as a
        # dead entry in the YouTube Cache source.
        xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "VideoLibrary.Clean",
            "params": {"showdialogs": False},
        }))


class Tracker(xbmc.Player):
    """Keeps the high-water mark for whatever is currently playing."""

    def __init__(self):
        super().__init__()
        self.vid = None
        self.max_percent = 0.0

    def _start(self):
        self.vid = None
        self.max_percent = 0.0
        try:
            path = self.getPlayingFile()
        except RuntimeError:
            return
        vid = video_id_for(path)
        if vid:
            self.vid = vid
            state = read_state()
            # Resume from whatever was already recorded; a video watched to 60%
            # in an earlier session plus 20% now should still trip the threshold.
            self.max_percent = float(state.get(vid, {}).get("max_percent", 0.0))
            log(f"tracking {vid} (already at {self.max_percent:.0f}%)")

    def sample(self):
        if not self.vid:
            return
        try:
            if not self.isPlayingVideo():
                return
            now = self.getTime()
            total = self.getTotalTime()
        except RuntimeError:
            return
        if total <= 0 or now < MIN_SAMPLE_TIME:
            return
        pct = min(100.0, (now / total) * 100.0)
        if pct > self.max_percent:
            self.max_percent = pct

    def _finish(self, ended):
        vid, pct = self.vid, self.max_percent
        self.vid, self.max_percent = None, 0.0
        if not vid:
            return
        if ended:
            # Playback ran to the end; nothing more to watch regardless of what
            # the last sample caught.
            pct = 100.0

        state = read_state()
        entry = state.setdefault(vid, {"added": time.time(), "title": vid})
        entry["max_percent"] = max(float(entry.get("max_percent", 0.0)), pct)
        entry["last_played"] = time.time()
        write_state(state)

        if entry["max_percent"] >= threshold():
            purge(vid, entry["max_percent"])
        else:
            log(f"{vid} at {entry['max_percent']:.0f}%, keeping "
                f"(threshold {threshold():.0f}%)")

    # --- Kodi callbacks -------------------------------------------------
    def onAVStarted(self):
        self._start()

    def onPlayBackStopped(self):
        self._finish(ended=False)

    def onPlayBackEnded(self):
        self._finish(ended=True)

    def onPlayBackError(self):
        self._finish(ended=False)


def main():
    if not os.path.isdir(CACHE_DIR):
        log(f"cache dir {CACHE_DIR} missing; service idle", xbmc.LOGWARNING)

    monitor = xbmc.Monitor()
    player = Tracker()
    log(f"started, deleting at {threshold():.0f}% watched")

    while not monitor.abortRequested():
        player.sample()
        if monitor.waitForAbort(SAMPLE_SECONDS):
            break

    # Kodi is shutting down mid-playback: bank what we have so the position is
    # not lost, and let the systemd sweeper act on it later.
    if player.vid:
        player._finish(ended=False)
    log("stopped")


if __name__ == "__main__":
    main()
