#!/usr/bin/env python3
"""Full recorded tracks, for drawing one aircraft's whole path on the map.

The map's own points stop at 15,000 ft and 50 miles from Dulles. When the
owner selects a flight, the map instead draws its whole recorded path, at
every altitude and range, from these files (owner's request, 2026-09-24).

One file per UTC hour, t/<YYYYMMDDHH>.json:
  {"start": hour_epoch, "ac": {hex: [flight, type, [[sec, lat, lon, alt], ...]]}}

To keep uploads small each track is simplified for drawing: a point is kept
at least every 60 s, whenever the track turns 3 degrees or the altitude
changes 200 ft since the last kept point, on either side of any gap over
30 s, and at each end. The drawn line matches the full data; the full log
in flights.db keeps every point. A finished hour's file is built once and
reused, so each push only carries the current hour's.
"""
import json, os, sqlite3, time

KEEP_EVERY_S = 60
TURN_DEG = 3
CLIMB_FT = 200
GAP_S = 30
FINISHED_AFTER_S = 180   # an hour is final this long after it ends (commit lag)


def _turn(a, b):
    if a is None or b is None:
        return 0
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def simplify(pts):
    """pts: time-ordered [t, lat, lon, alt, track]. Returns the kept ones."""
    if len(pts) <= 2:
        return pts
    kept = [pts[0]]
    for i in range(1, len(pts)):
        p, prev, last = pts[i], pts[i - 1], kept[-1]
        if p[0] - prev[0] > GAP_S and prev is not last:
            kept.append(prev)            # the point before a gap
            last = prev
        if (i == len(pts) - 1 or p[0] - last[0] >= KEEP_EVERY_S or p[0] - prev[0] > GAP_S
                or abs((p[3] or 0) - (last[3] or 0)) >= CLIMB_FT or _turn(p[4], last[4]) >= TURN_DEG):
            kept.append(p)
    return kept


def hour_file(db, hour_start, type_for=None):
    rows = {}
    seen = set()
    for t, hexid, lat, lon, alt, ground, track, flight, actype in db.execute("""
            SELECT pos_t, hex, lat, lon, alt_baro, on_ground, track, flight, type FROM positions
            WHERE t BETWEEN ? AND ? AND pos_t >= ? AND pos_t < ? AND lat IS NOT NULL
            ORDER BY pos_t""", (hour_start - 5, hour_start + 3630, hour_start, hour_start + 3600)):
        if (hexid, t) in seen:
            continue
        seen.add((hexid, t))
        r = rows.setdefault(hexid, {"flight": "", "type": "", "pts": []})
        if flight:
            r["flight"] = flight
        if actype:
            r["type"] = actype
        r["pts"].append([t, round(lat, 5), round(lon, 5), 0 if ground else alt, track])
    ac = {}
    for hexid in sorted(rows):
        r = rows[hexid]
        actype = r["type"] or (type_for(hexid) if type_for else "") or ""
        ac[hexid] = [r["flight"], actype,
                     [[int(p[0] - hour_start), p[1], p[2], p[3]] for p in simplify(r["pts"])]]
    return {"start": hour_start, "ac": ac}


def build(db_path, work_dir, start, end, type_for=None):
    """{"t/<hour>.json": bytes} for every hour touching [start, end]. Finished
    hours already in work_dir are reused as they are."""
    files = {}
    first = start - start % 3600
    db = None
    for hour in range(first, end + 1, 3600):
        name = "t/" + time.strftime("%Y%m%d%H", time.gmtime(hour)) + ".json"
        path = os.path.join(work_dir, name)
        if hour + 3600 + FINISHED_AFTER_S < end and os.path.exists(path):
            with open(path, "rb") as f:
                files[name] = f.read()
            continue
        if db is None:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60)
        body = hour_file(db, hour, type_for)
        if body["ac"]:
            files[name] = json.dumps(body, separators=(",", ":")).encode()
    if db is not None:
        db.close()
    return files
