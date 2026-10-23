#!/usr/bin/env python3
"""When Dulles is busiest: every landing and take-off at KIAD, counted per
15 minutes of Eastern time, for busy.html.

Owner (2026-10-22): "when the busiest times are for dulles air traffic ...
segregate it by day as well", then "make the chart and put it on another page
on the website".

From the full recorded paths (the t/ hour files, pi/tracks.py), which keep every
altitude and range. On a path, a stretch below LOW_FT within NEAR_NM of the
Dulles reference point is a movement:
  - a landing, at the first such point, if the path was at or above HIGH_FT in
    the 20 minutes before (it came down to the airport);
  - a take-off, at the last such point, if it is at or above HIGH_FT within
    the 15 minutes after (it climbed away).
A path is split where it goes unheard for more than 30 minutes. On 27 days
(09-26 to 10-22) this found about 450 landings and 440 take-offs a day, 91%
with airline callsigns; not checked against official counts.

Each settled hour is counted once, from its t/ file and its neighbours (an
approach can start in the hour before), and added to state.json; busy.json
(published) holds the counts per local day, a = landings, d = take-offs, 96
quarter-hours each. The t/ files are kept for 30 days, so the first run fills
in the last 30 days and the counts grow from there.
"""
import datetime, json, math, os, time
from zoneinfo import ZoneInfo

import tracks

IAD = (38.9445, -77.4558)
NEAR_NM = 5.0
LOW_FT = 1500
HIGH_FT = 2500
BEFORE_S = 1200
AFTER_S = 900
GAP_S = 1800
LOCAL = ZoneInfo("America/New_York")
VERSION = 1
BUDGET_S = 60             # at most this long per run (the first run has 30 days to count)


def nm(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(x))


def movements(paths):
    """paths: {hex: [(t, lat, lon, alt), ...]} -> [(t, 'a'|'d', hex)], by time."""
    out = []
    for hexid, pts in paths.items():
        pts = sorted(p for p in pts if p[3] is not None)
        segs, seg = [], []
        for p in pts:
            if seg and p[0] - seg[-1][0] > GAP_S:
                segs.append(seg)
                seg = []
            seg.append(p)
        if seg:
            segs.append(seg)
        for seg in segs:
            low = [p for p in seg if p[3] <= LOW_FT and nm(p[1:3], IAD) <= NEAR_NM]
            if not low:
                continue
            t0, t1 = low[0][0], low[-1][0]
            if any(p[3] >= HIGH_FT and t0 - BEFORE_S <= p[0] < t0 for p in seg):
                out.append((t0, "a", hexid))
            if any(p[3] >= HIGH_FT and t1 < p[0] <= t1 + AFTER_S for p in seg):
                out.append((t1, "d", hexid))
    return sorted(out)


def paths_from_files(bodies):
    """Parsed t/ hour files -> {hex: [(t, lat, lon, alt), ...]}."""
    paths = {}
    for d in bodies:
        for hexid, (_, _, pts) in d["ac"].items():
            paths.setdefault(hexid, []).extend((d["start"] + p[0], p[1], p[2], p[3]) for p in pts)
    return paths


def slot(t):
    """(local date 'YYYY-MM-DD', quarter-hour 0-95) of an epoch time."""
    d = datetime.datetime.fromtimestamp(t, LOCAL)
    return d.date().isoformat(), (d.hour * 60 + d.minute) // 15


def add(days, moves, start, end):
    """Count the movements in [start, end) into days {date: {'a': [96], 'd': [96]}}."""
    n = 0
    for t, kind, _ in moves:
        if start <= t < end:
            day, q = slot(t)
            days.setdefault(day, {"a": [0] * 96, "d": [0] * 96})[kind][q] += 1
            n += 1
    return n


def count_hour(h, load, st):
    """Count hour h (epoch start) into st, from load(hour) -> parsed t/ file or None."""
    bodies = [b for b in (load(h - 3600), load(h), load(h + 3600)) if b]
    add(st["days"], movements(paths_from_files(bodies)), h, h + 3600)
    day = slot(h + 1800)[0]
    st["hours_per_day"][day] = st["hours_per_day"].get(day, 0) + 1


def update(work_dir, now, state_dir, final, budget_s=BUDGET_S):
    """Count every settled hour not counted yet whose t/ file is in work_dir;
    write busy.json when anything changed. final: names of the frozen hour
    files. Returns True if busy.json was written."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "state.json")
    try:
        with open(path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    if st.get("version") != VERSION:
        st = {"version": VERSION, "hours": [], "days": {}, "hours_per_day": {}}
    done = set(st["hours"])
    hours = sorted(p[1] for p in map(tracks.parse_name, final) if p and p[0] == "t")
    have = set(hours)
    started, changed = time.monotonic(), False
    cache = {}

    def load(h):
        if h not in cache:
            try:
                with open(os.path.join(work_dir, tracks.hour_name("t", h))) as f:
                    cache[h] = json.load(f)
            except (OSError, ValueError):
                cache[h] = None
        return cache[h]

    for h in hours:
        if h in done:
            continue
        if h + 3600 not in have and now < h + 3 * 3600:
            continue                       # the hour after isn't final yet: an approach may run into it
        if time.monotonic() - started > budget_s:
            break
        count_hour(h, load, st)
        done.add(h)
        changed = True
        for old in [k for k in cache if k < h - 3600]:
            del cache[old]
    st["hours"] = sorted(done)
    with open(path + ".tmp", "w") as f:
        json.dump(st, f)
    os.replace(path + ".tmp", path)
    out = os.path.join(work_dir, "busy.json")
    if not changed and os.path.exists(out):
        return False
    write(out, st["days"], st["hours_per_day"], now)
    return True


def complete_days(hours_per_day):
    """Local days with every hour counted (23 or 25 on clock-change days)."""
    out = []
    for day, n in hours_per_day.items():
        d = datetime.date.fromisoformat(day)
        a = datetime.datetime(d.year, d.month, d.day, tzinfo=LOCAL)
        b = a + datetime.timedelta(days=1)
        if n >= round((b.timestamp() - a.timestamp()) / 3600):
            out.append(day)
    return sorted(out)


def write(out, days, hours_per_day, built, source="the farm receiver's recorded paths (pi/busy.py)"):
    meta = {"version": VERSION, "tz": "America/New_York", "slot_min": 15,
            "complete_days": complete_days(hours_per_day),
            "days": {k: days[k] for k in sorted(days)},
            "rule": f"landing: a path below {LOW_FT} ft within {NEAR_NM:g} nm of Dulles that was at or above "
                    f"{HIGH_FT} ft in the {BEFORE_S // 60} minutes before; take-off: the same, climbing to "
                    f"{HIGH_FT} ft within {AFTER_S // 60} minutes after",
            "source": source, "built": int(built)}
    with open(out + ".tmp", "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    os.replace(out + ".tmp", out)
