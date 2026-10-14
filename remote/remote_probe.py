import evdev
from evdev import ecodes as E

dev = evdev.InputDevice('/dev/input/event6')
print(dev.name)
for code, info in dev.capabilities().get(E.EV_ABS, []):
    print(f"  {E.ABS[code]}: min={info.min} max={info.max}")
print("Press buttons (Ctrl+C to quit)...\n")

x = y = None
touching = False
pts, t0 = [], 0

for e in dev.read_loop():
    if e.type == E.EV_ABS:
        if e.code == E.ABS_X: x = e.value
        elif e.code == E.ABS_Y: y = e.value
    elif e.type == E.EV_KEY and e.code == E.BTN_TOUCH:
        if e.value == 1:
            touching, pts, t0 = True, [], e.timestamp()
        else:
            touching = False
            if x is not None: pts.append((x, y))
            if pts:
                (sx, sy), (ex, ey) = pts[0], pts[-1]
                print(f"start=({sx},{sy}) end=({ex},{ey}) "
                      f"dx={ex-sx:+} dy={ey-sy:+} "
                      f"points={len(pts)} {e.timestamp()-t0:.2f}s")
            else:
                print("touch with no coordinates")
    elif e.type == E.EV_SYN and touching and x is not None:
        pts.append((x, y))
