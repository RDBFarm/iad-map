#!/usr/bin/env python3
"""Aircraft passing over the farm. Used by publish.py and alerts.py.

"Over the farm" is within FARM_RADIUS_NM of the centre of the farm parcel
(18020 Edwards Ferry Rd), at any altitude, airborne. Altitudes are pressure
altitudes as the aircraft report them -- roughly feet above sea level, not
above the farm's ground.

Propeller aeroplanes are excluded from the figure, the highlights and the
alerts, at the owner's request (2026-09-24). An aircraft is a prop plane when
its ICAO type is a fixed-wing type (landplane, seaplane, amphibian or
gyrocopter) with piston, turboprop or electric engines, per
aircraft_types.json. Helicopters and jets stay in. An aircraft whose type is
unknown stays in: a missed alert costs more than an extra one.

update() works through flights.db in 15-minute slices, like proximity.py,
and appends one line per pass to events/farm_passes.jsonl -- props included,
marked, so the log keeps everything.
"""
import json, math, os, sqlite3, time

FARM = (39.1506, -77.4612)   # centre of the parcel polygon
FARM_RADIUS_NM = 1.0
PASS_GAP_S = 120
SLICE_S = 900
COMMIT_LAG_S = 90

_TYPES = None


def aircraft_types():
    global _TYPES
    if _TYPES is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "aircraft_types.json")) as f:
                _TYPES = json.load(f)["types"]
        except (OSError, ValueError, KeyError):
            _TYPES = {}
    return _TYPES


def is_prop(actype):
    """True for a propeller aeroplane, False for anything else known, None if unknown."""
    desc = aircraft_types().get((actype or "").upper())
    if not desc or len(desc) != 3:
        return None
    return desc[0] in "LSAG" and desc[2] in "PTE"


def dist_nm(lat, lon):
    p1, p2 = math.radians(FARM[0]), math.radians(lat)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon - FARM[1]) / 2) ** 2)
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def update(db_path, events_dir, now=None):
    """Log passes from new slices. Returns the number of passes logged."""
    now = time.time() if now is None else now
    os.makedirs(events_dir, exist_ok=True)
    state_path = os.path.join(events_dir, "farm_state.json")
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {"done_until": int(now - SLICE_S), "open": {}}
    lat_pad = FARM_RADIUS_NM / 60
    lon_pad = lat_pad / math.cos(math.radians(FARM[0]))
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60)
    logged, stop = 0, int(now - COMMIT_LAG_S)
    while state["done_until"] < stop:
        a = state["done_until"]
        b = min(a + SLICE_S, stop)
        for t, hexid, lat, lon, alt, gs, flight, actype, mlat in db.execute("""
                SELECT pos_t, hex, lat, lon, alt_baro, gs, flight, type, mlat FROM positions
                WHERE t BETWEEN ? AND ? AND pos_t >= ? AND pos_t < ?
                  AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
                  AND on_ground = 0 AND alt_baro IS NOT NULL
                ORDER BY pos_t""",
                (a - 5, b + 30, a, b, FARM[0] - lat_pad, FARM[0] + lat_pad,
                 FARM[1] - lon_pad, FARM[1] + lon_pad)):
            d = dist_nm(lat, lon)
            if d > FARM_RADIUS_NM:
                continue
            p = state["open"].get(hexid)
            if p is None or t - p["end"] > PASS_GAP_S:
                if p is not None:
                    logged += _write(events_dir, p)
                p = state["open"][hexid] = {"hex": hexid, "start": int(t), "end": int(t), "t": int(t),
                                            "dist_nm": 99, "min_alt": alt}
            p["end"] = int(t)
            p["min_alt"] = min(p["min_alt"], alt)
            if flight:
                p["flight"] = flight
            if actype:
                p["type"] = actype
            if d < p["dist_nm"]:
                p.update(t=int(t), dist_nm=round(d, 2), alt=alt, gs=gs, lat=lat, lon=lon, mlat=mlat)
        for hexid, p in list(state["open"].items()):
            if p["end"] < b - PASS_GAP_S:
                logged += _write(events_dir, p)
                del state["open"][hexid]
        state["done_until"] = b
        with open(state_path + ".tmp", "w") as f:
            json.dump(state, f)
        os.replace(state_path + ".tmp", state_path)
    db.close()
    return logged


def _write(events_dir, p):
    p = dict(p)
    p["prop"] = is_prop(p.get("type"))
    with open(os.path.join(events_dir, "farm_passes.jsonl"), "a") as f:
        f.write(json.dumps(p, separators=(",", ":")) + "\n")
    return 1


def summary(passes):
    """The over-the-farm figure: passes that aren't prop planes."""
    kept = sorted(p["alt"] for p in passes if p.get("prop") is not True)
    if not kept:
        return {"aircraft": 0}
    return {"aircraft": len(kept), "median_alt": kept[len(kept) // 2], "lowest_alt": kept[0],
            "under_2000": sum(1 for a in kept if a < 2000),
            "props_excluded": sum(1 for p in passes if p.get("prop") is True)}
