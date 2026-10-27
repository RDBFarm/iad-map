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
with airline callsigns.

Checked against the FAA's daily tower count for IAD (ATADS), Sept 26-30: 93.6%
of its operations. Most of the rest are lost before 1,500 ft, so from VERSION 2
(owner, 10-27: "Include them") a path stretch with no movement by the rule above
also counts when it was plainly on a Dulles runway's line (recovered()):
  - a landing, at its last point, if last heard within RECOVER_NM of Dulles at
    or below RECOVER_FT, descending and closing on the airport;
  - a take-off, at its first point, if first heard there climbing away;
in both cases within LINE_NM sideways of a runway's extended line, flying along
it (ALONG_DEG), and more than OTHER_NM from Leesburg and Manassas, whose own
traffic otherwise passes (all 68 extra "landings" on 9-30 without that test were
Leesburg's). Over Sept 26-30 that added 59 landings and 28 take-offs: 95.4%.

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
VERSION = 2               # 2: lost landings and take-offs recovered from the runway lines (10-27)
RECOVER_NM = 12.0
RECOVER_FT = 5000
LINE_NM = 2.0
ALONG_DEG = 15
OTHER_NM = 3.0
RUNWAYS = [1, 181, 110.6, 290.6]                          # Dulles, true (publish.AIRPORT_RUNWAYS)
OTHER_AIRPORTS = [(39.0779, -77.5578), (38.7214, -77.5153)]   # Leesburg, Manassas (publish.AIRPORTS)
BUDGET_S = 60             # at most this long per run (the first run has 30 days to count)


def nm(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(x))


def bearing(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                   math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360


def turn(a, b):
    return abs((a - b + 180) % 360 - 180)


def on_runway_line(p, track):
    """Within LINE_NM sideways of a Dulles runway's extended line, flying along it, and clear of Leesburg and Manassas."""
    if min(nm(p[1:3], o) for o in OTHER_AIRPORTS) <= OTHER_NM:
        return False
    d, b = nm(p[1:3], IAD), bearing(IAD, p[1:3])
    return any(turn(track, r) <= ALONG_DEG and abs(d * math.sin(math.radians(turn(b, (r + 180) % 360)))) <= LINE_NM
               for r in RUNWAYS)


def recovered(seg):
    """A path stretch with no movement by the main rule: [(t, 'a'|'d')] for a landing lost before
    LOW_FT or a take-off first heard after it, on a Dulles runway's line (module docstring)."""
    out = []
    last = seg[-1]
    prev = next((q for q in reversed(seg[:-1]) if last[0] - q[0] >= 60), None)
    if prev and 0 < last[3] <= RECOVER_FT and last[3] < prev[3] and nm(last[1:3], IAD) <= RECOVER_NM \
            and nm(last[1:3], IAD) < nm(prev[1:3], IAD):
        track = bearing(prev[1:3], last[1:3])
        if turn(track, bearing(last[1:3], IAD)) <= 30 and on_runway_line(last, track):
            out.append((last[0], "a"))
    first = seg[0]
    nxt = next((q for q in seg[1:] if q[0] - first[0] >= 60), None)
    if nxt and 0 < first[3] <= RECOVER_FT and nxt[3] > first[3] and nm(first[1:3], IAD) <= RECOVER_NM \
            and nm(nxt[1:3], IAD) > nm(first[1:3], IAD):
        track = bearing(first[1:3], nxt[1:3])
        if turn(track, bearing(IAD, first[1:3])) <= 30 and on_runway_line(first, track):
            out.append((first[0], "d"))
    return out


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
            found = []
            low = [p for p in seg if p[3] <= LOW_FT and nm(p[1:3], IAD) <= NEAR_NM]
            if low:
                t0, t1 = low[0][0], low[-1][0]
                if any(p[3] >= HIGH_FT and t0 - BEFORE_S <= p[0] < t0 for p in seg):
                    found.append((t0, "a"))
                if any(p[3] >= HIGH_FT and t1 < p[0] <= t1 + AFTER_S for p in seg):
                    found.append((t1, "d"))
            out.extend((t, k, hexid) for t, k in found or recovered(seg))
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
    hours = sorted(p[1] for p in map(tracks.parse_name, final) if p and p[0] == "t")
    if st.get("version") != VERSION:
        st = migrate(st, hours)
    done = set(st["hours"])
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

    behind = False
    for h in hours:
        if h in done:
            continue
        if h + 3600 not in have and now < h + 3 * 3600:
            continue                       # the hour after isn't final yet: an approach may run into it
        if time.monotonic() - started > budget_s:
            behind = True
            break
        count_hour(h, load, st)
        done.add(h)
        changed = True
        for old in [k for k in cache if k < h - 3600]:
            del cache[old]
    st["hours"] = sorted(done)
    if not behind:
        st.pop("recounting", None)
    with open(path + ".tmp", "w") as f:
        json.dump(st, f)
    os.replace(path + ".tmp", path)
    out = os.path.join(work_dir, "busy.json")
    if st.get("recounting") and os.path.exists(out):
        return False                       # a new rule's recount: the page keeps the old counts until it is done
    if not changed and os.path.exists(out):
        return False
    write(out, st["days"], st["hours_per_day"], now, earlier_rule_days=st.get("earlier_rule_days"))
    return True


def migrate(old, hours):
    """State for a new VERSION: every local day from the first one whose t/ files are all still
    kept is counted again under the new rule; a day before it (its first hours already gone past
    the 30-day window) keeps its earlier count, listed in earlier_rule_days."""
    st = {"version": VERSION, "hours": [], "days": {}, "hours_per_day": {}, "earlier_rule_days": []}
    if not old.get("days") and not old.get("hours_per_day"):
        return st
    st["recounting"] = True
    for day in sorted(set(old.get("days", {})) | set(old.get("hours_per_day", {}))):
        if hours and day_start(day) >= hours[0]:
            continue                       # all its hours are kept: counted again
        if day in old.get("days", {}):
            st["days"][day] = old["days"][day]
        if day in old.get("hours_per_day", {}):
            st["hours_per_day"][day] = old["hours_per_day"][day]
        st["earlier_rule_days"].append(day)
    keep = set(st["earlier_rule_days"])
    st["hours"] = [h for h in hours if slot(h + 1800)[0] in keep]     # counted already, under the earlier rule
    return st


def day_start(day):
    d = datetime.date.fromisoformat(day)
    return datetime.datetime(d.year, d.month, d.day, tzinfo=LOCAL).timestamp()


def day_hours(day):
    d = datetime.date.fromisoformat(day) + datetime.timedelta(days=1)
    return round((datetime.datetime(d.year, d.month, d.day, tzinfo=LOCAL).timestamp() - day_start(day)) / 3600)


def complete_days(hours_per_day):
    """Local days with every hour counted (23 or 25 on clock-change days)."""
    return sorted(day for day, n in hours_per_day.items() if n >= day_hours(day))


def write(out, days, hours_per_day, built, source="the farm receiver's recorded paths (pi/busy.py)",
          earlier_rule_days=None):
    meta = {"version": VERSION, "tz": "America/New_York", "slot_min": 15,
            "complete_days": complete_days(hours_per_day),
            "days": {k: days[k] for k in sorted(days)},
            "rule": f"landing: a path below {LOW_FT} ft within {NEAR_NM:g} nm of Dulles that was at or above "
                    f"{HIGH_FT} ft in the {BEFORE_S // 60} minutes before; take-off: the same, climbing to "
                    f"{HIGH_FT} ft within {AFTER_S // 60} minutes after; plus, from version 2, one last heard "
                    f"descending (or first heard climbing) below {RECOVER_FT} ft within {RECOVER_NM:g} nm on a "
                    f"Dulles runway's line",
            "earlier_rule_days": sorted(earlier_rule_days or []),
            "source": source, "built": int(built)}
    with open(out + ".tmp", "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    os.replace(out + ".tmp", out)
