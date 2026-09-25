#!/usr/bin/env python3
"""Aircraft passing over the farm. Used by publish.py and alerts.py.

"Over the farm" means over the farm's land: an aircraft whose path -- one
position to the next, or a single position -- crosses the parcel boundary
(18020 Edwards Ferry Rd, 213 acres, Maryland iMAP), airborne, at any
altitude. Owner's decision, 2026-09-25 ("just give me ones that actually fly
over my airspace"); it replaced a 1 nm radius around the parcel centre.

Heights are height above the farm's ground: the reported pressure altitude,
corrected to sea level with KIAD's current altimeter setting (from the METAR),
minus the ground elevation at the nearest point measured across the parcel
(farm_ground.json, from the USGS elevation service at install; the notes'
~380 ft if that failed). ADS-B altitude comes in 25-100 ft steps and the
correction is approximate, so treat these as +/- 100 ft or so.

Propeller aeroplanes are excluded from the figure, the highlights and the
alerts, at the owner's request (2026-09-24). An aircraft is a prop plane when
its ICAO type is a fixed-wing type (landplane, seaplane, amphibian or
gyrocopter) with piston, turboprop or electric engines, per
aircraft_types.json. Helicopters and jets stay in. An aircraft whose type is
unknown stays in the figure and the map highlights (marked "type unknown")
but is not pushed to the phone -- the owner's call, 2026-09-24.

update() works through flights.db in 15-minute slices, like proximity.py,
and appends one line per pass to events/farm_passes.jsonl -- props included,
marked, so the log keeps everything.
"""
import json, math, os, sqlite3, time

import aircraft_lookup

FARM = (39.1506, -77.4612)   # centre of the parcel polygon
FARM_RADIUS_NM = 1.0         # kept for old log lines and the alert wording
# The parcel boundary as (lat, lon), from Maryland iMAP (the outline on the map).
PARCEL = [[39.151795, -77.453878], [39.152533, -77.455843], [39.155998, -77.465539], [39.155417, -77.465647], [39.153679, -77.466727], [39.152486, -77.46807], [39.15129, -77.469338], [39.151066, -77.469576], [39.150332, -77.470355], [39.150306, -77.470348], [39.147259, -77.462724], [39.144868, -77.456731], [39.146311, -77.455808], [39.146396, -77.457437], [39.149188, -77.457301], [39.149074, -77.455086], [39.151288, -77.455272], [39.151104, -77.454039], [39.151795, -77.453878]]
GROUND_FILE = os.environ.get("IADMAP_FARM_GROUND", "/mnt/flightdata/iad-map/farm_ground.json")
FALLBACK_GROUND_FT = 380     # "Poolesville is roughly 380 ft" (owner's notes); used only if USGS failed
LOW_ALERT_AGL_FT = 1100      # jets: owner's 1,500 ft reported, less ~400 ft of ground
HELI_ALERT_AGL_FT = 150      # helicopters: owner's 550 ft reported, less ~400 ft
HIGHLIGHT_AGL_FT = 1600      # map highlight: owner's 2,000 ft reported, less ~400 ft
PREV_MAX_S = 30              # join two positions into a path segment only if this close in time
PASS_GAP_S = 120
SLICE_S = 900
COMMIT_LAG_S = 90

_TYPES = None
_NAMES = {}


def aircraft_types():
    global _TYPES, _NAMES
    if _TYPES is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "aircraft_types.json")) as f:
                data = json.load(f)
            _TYPES, _NAMES = data["types"], data.get("names", {})
        except (OSError, ValueError, KeyError):
            _TYPES = {}
    return _TYPES


def type_name(actype):
    """Manufacturer and model for an ICAO type code, e.g. 'CESSNA 172 Skyhawk'."""
    aircraft_types()
    return _NAMES.get((actype or "").upper())


def is_helicopter(actype):
    """True for a helicopter type (ICAO description starting with H)."""
    desc = aircraft_types().get((actype or "").upper())
    return bool(desc) and desc[0] == "H"


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


# ── Geometry: is it over the land? ─────────────────────────────────────────
def inside(lat, lon):
    """Point in the parcel polygon (ray casting)."""
    c = False
    for (a1, o1), (a2, o2) in zip(PARCEL, PARCEL[1:] + PARCEL[:1]):
        if (a1 > lat) != (a2 > lat) and lon < (o2 - o1) * (lat - a1) / (a2 - a1) + o1:
            c = not c
    return c


def _cross(p, q, r, s):
    def o(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (v > 0) - (v < 0)
    return o(p, q, r) != o(p, q, s) and o(r, s, p) != o(r, s, q)


def crosses(lat1, lon1, lat2, lon2):
    """True if the straight path between two positions touches the parcel."""
    if inside(lat1, lon1) or inside(lat2, lon2):
        return True
    return any(_cross((lat1, lon1), (lat2, lon2), a, b) for a, b in zip(PARCEL, PARCEL[1:] + PARCEL[:1]))


# ── Height above the farm's ground ─────────────────────────────────────────
_GROUND = None


def ground_points():
    global _GROUND
    if _GROUND is None:
        try:
            with open(GROUND_FILE) as f:
                _GROUND = json.load(f)["points"]
        except (OSError, ValueError, KeyError):
            _GROUND = []
    return _GROUND


def ground_ft(lat, lon):
    """(elevation in ft, basis) at the nearest measured point on the parcel."""
    pts = ground_points()
    if not pts:
        return FALLBACK_GROUND_FT, "approximate (notes)"
    best = min(pts, key=lambda p: (p[0] - lat) ** 2 + ((p[1] - lon) * 0.776) ** 2)
    return best[2], "USGS"


def height_agl(alt_baro, lat, lon, altim_hpa=None):
    """Height above the farm's ground, from reported pressure altitude.
    altim_hpa: KIAD altimeter setting; without it no pressure correction."""
    if not isinstance(alt_baro, (int, float)):
        return None
    msl = alt_baro + ((altim_hpa * 0.0295300 - 29.92) * 1000 if altim_hpa else 0)
    return int(round(msl - ground_ft(lat, lon)[0]))


def build_ground(path=GROUND_FILE, n=7):
    """Ask the USGS elevation service (EPQS) for the ground height at an
    n x n grid over the parcel (points inside it) and its centre."""
    import urllib.request
    lats = [p[0] for p in PARCEL]
    lons = [p[1] for p in PARCEL]
    pts = [FARM]
    for i in range(n):
        for j in range(n):
            la = min(lats) + (max(lats) - min(lats)) * (i + 0.5) / n
            lo = min(lons) + (max(lons) - min(lons)) * (j + 0.5) / n
            if inside(la, lo):
                pts.append((la, lo))
    out = []
    for la, lo in pts:
        url = f"https://epqs.nationalmap.gov/v1/json?x={lo:.6f}&y={la:.6f}&units=Feet&wkid=4326"
        req = urllib.request.Request(url, headers={"User-Agent": "rdbf-iad-map"})
        with urllib.request.urlopen(req, timeout=30) as r:
            v = float(json.load(r)["value"])
        if -500 < v < 5000:
            out.append([round(la, 6), round(lo, 6), round(v)])
    if not out:
        raise RuntimeError("no elevations returned")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"source": "USGS EPQS (epqs.nationalmap.gov), feet above sea level",
                   "fetched": time.strftime("%Y-%m-%d", time.gmtime()), "points": out}, f)
    el = [p[2] for p in out]
    print(f"farm ground: {len(out)} points, {min(el)}-{max(el)} ft, mean {sum(el)/len(el):.0f} ft", flush=True)


def update(db_path, events_dir, now=None, altim_at=None):
    """Log passes over the land from new slices. altim_at(t) gives KIAD's
    altimeter setting (hPa) at time t, or None. Returns passes logged."""
    now = time.time() if now is None else now
    altim_at = altim_at or (lambda t: None)
    os.makedirs(events_dir, exist_ok=True)
    state_path = os.path.join(events_dir, "farm_state.json")
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault("done_until", int(now - SLICE_S))
    state.setdefault("open", {})
    state.setdefault("prev", {})
    lats = [p[0] for p in PARCEL]
    lons = [p[1] for p in PARCEL]
    pad = 0.02   # about a mile: positions just outside still form segments across it
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60)
    logged, stop = 0, int(now - COMMIT_LAG_S)
    while state["done_until"] < stop:
        a = state["done_until"]
        b = min(a + SLICE_S, stop)
        for t, hexid, lat, lon, alt, gs, track, flight, actype, mlat in db.execute("""
                SELECT pos_t, hex, lat, lon, alt_baro, gs, track, flight, type, mlat FROM positions
                WHERE t BETWEEN ? AND ? AND pos_t >= ? AND pos_t < ?
                  AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
                  AND on_ground = 0 AND alt_baro IS NOT NULL
                ORDER BY pos_t""",
                (a - 5, b + 30, a, b, min(lats) - pad, max(lats) + pad, min(lons) - pad, max(lons) + pad)):
            prev = state["prev"].get(hexid)
            state["prev"][hexid] = [t, lat, lon, alt]
            if prev and (t - prev[0] > PREV_MAX_S or t <= prev[0]):
                prev = None
            if not (inside(lat, lon) or (prev and crosses(prev[1], prev[2], lat, lon))):
                continue
            # the lower end of the segment stands for the crossing
            pt = (t, lat, lon, alt)
            if prev and prev[3] < alt:
                pt = tuple(prev)
            agl = height_agl(pt[3], pt[1], pt[2], altim_at(pt[0]))
            p = state["open"].get(hexid)
            if p is None or t - p["end"] > PASS_GAP_S:
                if p is not None:
                    logged += _write(events_dir, p)
                p = state["open"][hexid] = {"hex": hexid, "rule": "parcel", "start": int(pt[0]),
                                            "end": int(t), "t": int(pt[0]), "alt_agl": agl, "min_agl": agl,
                                            "alt": pt[3], "min_alt": pt[3], "lat": pt[1], "lon": pt[2],
                                            "gs": gs, "track": track, "mlat": mlat}
            p["end"] = int(t)
            if flight:
                p["flight"] = flight
            actype = actype or aircraft_lookup.type_for(hexid)
            if actype:
                p["type"] = actype
            if agl is not None and (p["min_agl"] is None or agl < p["min_agl"]):
                p.update(t=int(pt[0]), alt_agl=agl, min_agl=agl, alt=pt[3], lat=pt[1], lon=pt[2],
                         gs=gs, track=track, mlat=mlat)
            p["min_alt"] = min(p["min_alt"], pt[3])
            p["ground_ft"], p["ground_basis"] = ground_ft(p["lat"], p["lon"])
            p["altim_hpa"] = altim_at(p["t"])
        for hexid, p in list(state["open"].items()):
            if p["end"] < b - PASS_GAP_S:
                logged += _write(events_dir, p)
                del state["open"][hexid]
        state["prev"] = {h: v for h, v in state["prev"].items() if v[0] > b - PREV_MAX_S}
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
    """The over-the-farm figure: passes over the land (the parcel rule) that
    aren't prop planes, by height above the farm's ground."""
    kept = [p for p in passes if p.get("rule") == "parcel" and p.get("prop") is not True
            and p.get("alt_agl") is not None]
    hs = sorted(p["alt_agl"] for p in kept)
    props = sum(1 for p in passes if p.get("rule") == "parcel" and p.get("prop") is True)
    if not hs:
        return {"aircraft": 0, "props_excluded": props, "rule": "parcel"}
    return {"aircraft": len(hs), "median_agl": hs[len(hs) // 2], "lowest_agl": hs[0],
            "under_highlight": sum(1 for h in hs if h < HIGHLIGHT_AGL_FT),
            "highlight_agl": HIGHLIGHT_AGL_FT, "props_excluded": props, "rule": "parcel"}


if __name__ == "__main__":
    import sys
    if "--ground" in sys.argv:
        build_ground()
