#!/usr/bin/env python3
"""FB-RCS remote -> desktop, Kodi, or Chromium, depending on what's running.

event6 (touch/stylus device): swipes and taps -> navigation
event7 (consumer control):    volume keys

Desktop (no Kodi, no Chromium): arrows/Enter keyboard navigation,
                                Back = Escape, x2 = app menu, x3 = launch Kodi
Kodi:                           normal Kodi navigation, OK = play/pause in fullscreen video
Chromium:                       arrows/Enter (D-pad extension / spatial nav),
                                Back = Escape, x2 = page back, x3 = close Chromium
"""
import json, os, pwd, socket, subprocess, threading, time
import evdev
from evdev import ecodes as E, UInput

# ---------- settings ----------
NAME = "FB-RCS"
SWIPE_MIN = 400       # movement needed to count as a swipe (range 0-4095)
BACK_Y = 3000         # taps lower than this on screen = Back
KODI = ("127.0.0.1", 9090)
KODI_CMD = ["kodi"]                    # how to start Kodi
KODI_ENV = {"KODI_AE_SINK": "ALSA"}    # extra environment for Kodi (ALSA audio)
BROWSER_MODE = "keys" # default for sites not listed below
MOUSE_SITES = []      # sites that should use the cursor instead
STEP = 80             # cursor pixels per arrow press in browser mouse mode
BACK_WINDOW = 0.8     # seconds between Back presses to count as a double/triple
CHROMIUM_NAMES = ("chromium", "chromium-browse")
KODI_NAMES = ("kodi", "kodi.bin", "kodi-x11", "kodi-wayland", "kodi-gbm")
CHROMIUM = "chromium|chromium-browse"
USER = "lights"       # desktop user
VOL_STEP = "5%"

mouse = UInput({E.EV_KEY: [E.BTN_LEFT], E.EV_REL: [E.REL_X, E.REL_Y]},
               name="fb-remote-mouse")
keys = UInput({E.EV_KEY: [E.KEY_LEFTALT, E.KEY_LEFTMETA, E.KEY_UP, E.KEY_DOWN,
                          E.KEY_LEFT, E.KEY_RIGHT, E.KEY_ENTER, E.KEY_ESC]},
              name="fb-remote-keys")
last_back = 0.0
back_count = 0
last_launch = 0.0

# ---------- virtual mouse / keyboard ----------
def move(dx, dy):
    mouse.write(E.EV_REL, E.REL_X, dx)
    mouse.write(E.EV_REL, E.REL_Y, dy)
    mouse.syn()

def click():
    for v in (1, 0):
        mouse.write(E.EV_KEY, E.BTN_LEFT, v)
        mouse.syn()

def combo(*codes):
    for c in codes:
        keys.write(E.EV_KEY, c, 1)
    keys.syn()
    for c in reversed(codes):
        keys.write(E.EV_KEY, c, 0)
    keys.syn()

def count_back():
    """Returns 1, 2, 3... for quick successive Back presses."""
    global last_back, back_count
    now = time.monotonic()
    back_count = back_count + 1 if now - last_back < BACK_WINDOW else 1
    last_back = now
    return back_count

# ---------- process detection ----------
def live_process(names):
    """Command line of a live (non-zombie) process with one of these names,
    preferring a main process over helpers. None if not running."""
    found = None
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                data = f.read()
        except OSError:
            continue
        name = data[data.find("(") + 1:data.rfind(")")]
        state = data[data.rfind(")") + 2]
        if name not in names or state == "Z":
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            cmd = ""
        if "--type=" not in cmd:
            return cmd
        if found is None:
            found = cmd
    return found

def chromium_running():
    return live_process(CHROMIUM_NAMES) is not None

def kodi_running():
    return live_process(KODI_NAMES) is not None

def browser_mode():
    cmd = live_process(CHROMIUM_NAMES) or ""
    return "mouse" if any(s in cmd for s in MOUSE_SITES) else BROWSER_MODE

# ---------- desktop session helpers ----------
def session_env():
    pw = pwd.getpwnam(USER)
    run = f"/run/user/{pw.pw_uid}"
    env = {"HOME": pw.pw_dir, "USER": USER, "XDG_RUNTIME_DIR": run,
           "DBUS_SESSION_BUS_ADDRESS": f"unix:path={run}/bus",
           "DISPLAY": ":0", "PATH": "/usr/local/bin:/usr/bin:/bin"}
    try:
        socks = sorted(f for f in os.listdir(run)
                       if f.startswith("wayland-") and not f.endswith(".lock"))
        if socks:
            env["WAYLAND_DISPLAY"] = socks[0]
    except OSError:
        pass
    return env

def as_user(cmd, wait=True):
    env = [f"{k}={v}" for k, v in session_env().items()]
    full = ["runuser", "-u", USER, "--", "env", *env, *cmd]
    if wait:
        return subprocess.run(full, capture_output=True).returncode == 0
    subprocess.Popen(full, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True

def launch_kodi():
    global last_launch
    if time.monotonic() - last_launch < 20:   # don't double-launch while it starts
        return
    last_launch = time.monotonic()
    extra = [f"{k}={v}" for k, v in KODI_ENV.items()]
    as_user(["env", *extra, *KODI_CMD], wait=False)

# ---------- system volume ----------
def system_volume(up):
    sign = "+" if up else "-"
    if not as_user(["wpctl", "set-volume", "-l", "1.0",
                    "@DEFAULT_AUDIO_SINK@", VOL_STEP + sign]):
        as_user(["pactl", "set-sink-volume", "@DEFAULT_SINK@", sign + VOL_STEP])

def system_mute():
    if not as_user(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"]):
        as_user(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])

# ---------- Kodi ----------
def kodi(method, params=None):
    req = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params:
        req["params"] = params
    try:
        with socket.create_connection(KODI, timeout=2) as s:
            s.sendall(json.dumps(req).encode())
            buf, dec = "", json.JSONDecoder()
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    return None
                buf += chunk.decode(errors="ignore")
                while buf.strip():
                    buf = buf.lstrip()
                    try:
                        obj, end = dec.raw_decode(buf)
                    except ValueError:
                        break
                    buf = buf[end:]
                    if obj.get("id") == 1:
                        return obj.get("result")
    except OSError as err:
        print(f"Kodi not reachable: {err}", flush=True)
        return None

def in_fullscreen_video():
    r = kodi("GUI.GetProperties", {"properties": ["currentwindow"]})
    return bool(r) and r["currentwindow"]["id"] == 12005

# ---------- button handlers ----------
KEYMAP = {"Input.Up": E.KEY_UP, "Input.Down": E.KEY_DOWN,
          "Input.Left": E.KEY_LEFT, "Input.Right": E.KEY_RIGHT,
          "Input.Select": E.KEY_ENTER}

def cursor(action, step):
    if action == "Input.Up":
        move(0, -step)
    elif action == "Input.Down":
        move(0, step)
    elif action == "Input.Left":
        move(-step, 0)
    elif action == "Input.Right":
        move(step, 0)
    elif action == "Input.Select":
        click()

def handle_browser(action):
    global back_count
    mode = browser_mode()
    if action == "Input.Back":
        n = count_back()
        if n == 1:
            combo(E.KEY_ESC)
            action = "escape"
        elif n == 2:
            combo(E.KEY_LEFTALT, E.KEY_LEFT)
            action = "page back"
        else:
            subprocess.run(["pkill", "-x", CHROMIUM])
            action = "close chromium"
            back_count = 0
    elif mode == "keys" and action in KEYMAP:
        combo(KEYMAP[action])
    else:
        cursor(action, STEP)
    print(f"[browser:{mode}] {action}", flush=True)

def handle_kodi(action):
    if action == "Input.Select" and in_fullscreen_video():
        action = "playpause"
        kodi("Input.ExecuteAction", {"action": "playpause"})
    else:
        kodi(action)
    print(f"[kodi] {action}", flush=True)

def handle_desktop(action):
    global back_count
    if action == "Input.Back":
        n = count_back()
        if n == 1:
            combo(E.KEY_ESC)
            action = "escape"
        elif n == 2:
            combo(E.KEY_LEFTMETA)
            action = "app menu"
        else:
            launch_kodi()
            action = "launch kodi"
            back_count = 0
    elif action in KEYMAP:
        combo(KEYMAP[action])
    print(f"[desktop] {action}", flush=True)

def park_cursor():
    move(10000, 10000)   # shove the pointer into the bottom-right corner

def dispatch(action):
    if chromium_running():
        if browser_mode() == "keys":
            park_cursor()
        handle_browser(action)
    elif kodi_running():
        handle_kodi(action)          # Kodi hides its own pointer
    else:
        handle_desktop(action)
# ---------- volume keys (event7, not grabbed) ----------
def find_consumer():
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if d.name.startswith(NAME) and \
           E.KEY_VOLUMEUP in d.capabilities().get(E.EV_KEY, []):
            return d
        d.close()
    return None

def volume_loop():
    while True:
        dev = find_consumer()
        if dev is None:
            time.sleep(2)
            continue
        print(f"Volume keys on {dev.path}", flush=True)
        try:
            for e in dev.read_loop():
                # value 1 = press, 2 = held (repeats, so holding ramps volume)
                if e.type != E.EV_KEY or e.value not in (1, 2):
                    continue
                use_kodi = kodi_running() and not chromium_running()
                if e.code == E.KEY_VOLUMEUP:
                    if use_kodi:
                        kodi("Application.SetVolume", {"volume": "increment"})
                    else:
                        system_volume(True)
                elif e.code == E.KEY_VOLUMEDOWN:
                    if use_kodi:
                        kodi("Application.SetVolume", {"volume": "decrement"})
                    else:
                        system_volume(False)
                elif e.code == E.KEY_MUTE and e.value == 1:
                    if use_kodi:
                        kodi("Application.SetMute", {"mute": "toggle"})
                    else:
                        system_mute()
        except OSError:
            print("Volume device disconnected, waiting...", flush=True)
            time.sleep(1)

# ---------- touch device (event6, grabbed) ----------
def find_device():
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        abs_codes = [c for c, _ in d.capabilities().get(E.EV_ABS, [])]
        if d.name == NAME and E.ABS_X in abs_codes:
            return d
        d.close()
    return None

def classify(pts):
    (sx, sy), (ex, ey) = pts[0], pts[-1]
    dx, dy = ex - sx, ey - sy
    if max(abs(dx), abs(dy)) >= SWIPE_MIN:
        if abs(dy) > abs(dx):
            return "Input.Up" if dy > 0 else "Input.Down"
        return "Input.Right" if dx < 0 else "Input.Left"
    return "Input.Back" if sy >= BACK_Y else "Input.Select"

def run(dev):
    dev.grab()   # hide the fake touches from everything else
    print(f"Listening on {dev.path} ({dev.name})", flush=True)
    x = y = None
    touching, pts = False, []
    for e in dev.read_loop():
        if e.type == E.EV_ABS:
            if e.code == E.ABS_X:
                x = e.value
            elif e.code == E.ABS_Y:
                y = e.value
        elif e.type == E.EV_KEY and e.code == E.BTN_TOUCH:
            if e.value == 1:
                touching, pts = True, []
            else:
                touching = False
                if x is not None:
                    pts.append((x, y))
                if pts:
                    dispatch(classify(pts))
        elif e.type == E.EV_SYN and touching and x is not None:
            pts.append((x, y))

# ---------- main ----------
threading.Thread(target=volume_loop, daemon=True).start()

while True:
    dev = find_device()
    if dev is None:
        time.sleep(2)
        continue
    try:
        run(dev)
    except OSError:
        print("Remote disconnected, waiting...", flush=True)
        time.sleep(1)
