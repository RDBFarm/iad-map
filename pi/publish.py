#!/usr/bin/env python3
"""Publish the last 24 hours of recorded positions to GitHub.

Run every 15 minutes by iad-map-publish.timer. Reads the collector's log
(flights.db), keeps what the map shows -- positions within 50 statute miles
of Dulles at or below 15,000 ft -- and writes, on the `live-data` branch of RDBFarm/iad-map:

  live.json        what live.html reads first: the window, counts, KIAD
                   weather, and the list of hour files with a hash of each
  h/<hour>.json    one file per UTC hour of positions

The branch is kept to a single commit that each run amends and force-pushes,
so the repository does not grow. A finished hour's file comes out byte-for-
byte the same on every run, so after its first upload git never sends it
again: each push carries only the current hour and the index. live.html
fetches from raw.githubusercontent.com, so GitHub Pages is not rebuilt.

  python3 publish.py            build and push
  python3 publish.py --no-push  build only, and print a summary
"""
import hashlib, json, math, os, sqlite3, subprocess, sys, time, urllib.request

import aircraft_lookup
import farm
import proximity
import tracks

DB_PATH = os.environ.get("IADMAP_DB", "/mnt/flightdata/iad-map/flights.db")
WORK_DIR = os.environ.get("IADMAP_PUBLISH_DIR", "/mnt/flightdata/iad-map/publish")
EVENTS_DIR = os.environ.get("IADMAP_EVENTS_DIR", "/mnt/flightdata/iad-map/events")
DEPLOY_KEY = os.environ.get("IADMAP_DEPLOY_KEY", "/var/lib/iad-map/deploy_key")
REMOTE = os.environ.get("IADMAP_REMOTE", "git@github.com:RDBFarm/iad-map.git")
BRANCH = "live-data"
WX_URL = os.environ.get(
    "IADMAP_WX_URL",
    "https://aviationweather.gov/api/data/metar?ids=KIAD&format=json&hours=25")
SPAN_S = 24 * 3600

# ── Classification: copied unchanged from render_github.py (the script that
# builds the May 1 map, as of the copy last modified 2026-09-03), so the live
# map labels and filters points the same way. Changes since copying are
# marked with their date; open issues are listed in the README.
# BEGIN copied from render_github.py
AIRPORTS = {
    "KIAD": (38.9444, -77.4558, "Dulles"),
    "KDCA": (38.8521, -77.0377, "Reagan Natl"),
    "KBWI": (39.1754, -76.6683, "BWI"),
    "KJYO": (39.0779, -77.5578, "Leesburg"),
    "KGAI": (39.1683, -77.1660, "Gaithersburg"),
    "KHEF": (38.7214, -77.5153, "Manassas"),
    "KRMN": (38.5997, -77.4542, "Stafford"),
    "KADW": (38.8108, -76.8674, "Andrews AFB"),
    "KNYG": (38.5036, -77.3050, "Quantico MCAF"),
}

# Runway headings (both directions) for each airport
# Used to score alignment between aircraft track and runway centerline
AIRPORT_RUNWAYS = {
    "KIAD": [19, 199, 120, 300],   # 01/19, 12/30
    "KDCA": [19, 199, 150, 330],   # 01/19, 15/33
    "KBWI": [100, 280, 150, 330],  # 10/28, 15/33
    "KJYO": [170, 350],            # 17/35
    "KGAI": [140, 320],            # 14/32
    "KHEF": [160, 340],            # 16/34
    "KRMN": [150, 330],            # 15/33
    "KADW": [10, 190],             # 01/19 (parallel runways same heading)
    "KNYG": [20, 200],             # 02/20
}

# GA category codes — these get proximity-only classification
GA_CATEGORIES = {"A1", "A2", "B1"}

# Known airline ICAO prefixes that serve IAD area airports
# Maps prefix → most likely airport
AIRLINE_AIRPORT = {
    # IAD mainline & regionals
    "UAL":"KIAD","UCA":"KIAD","SKW":"KIAD","ASA":"KIAD","AAL":"KIAD",
    "DAL":"KIAD","SWA":"KIAD","JBU":"KIAD","FFT":"KIAD","VRD":"KIAD",
    "AWI":"KIAD","ENY":"KIAD","RPA":"KIAD","PDT":"KIAD","GJS":"KIAD",
    "MXY":"KIAD","JIA":"KIAD","ACJ":"KIAD",
    # DCA mainline
    "EGF":"KDCA","TCF":"KDCA",
    # BWI focus carriers
    # (a two-letter "WN" entry was removed 2026-09-24: prefixes are compared
    # as three letters, so it could never match)
    # Cargo
    "FDX":"KIAD","UPS":"KIAD","ABX":"KIAD",
    # Military IAD/region
    "RCH":"KIAD","SAM":"KIAD","PAT":"KIAD","CAF":"KIAD",
    "VMC":"KIAD","CFC":"KIAD",
    # Andrews AFB military
    "CNV":"KADW","EGL":"KADW",  # two-letter VV, VM removed 2026-09-24 (never matched)
    "CPT":"KADW","TRF":"KADW","OSI":"KADW",
    # Quantico / Marine One
    "HMX":"KNYG",  # two-letter MX removed 2026-09-24 (never matched)
}

# These airports have NO commercial or airline service — GA only
# Any flight with an airline callsign is NEVER assigned to these airports
GA_ONLY_AIRPORTS = {"KJYO", "KGAI", "KHEF", "KRMN"}

def is_airline_callsign(flight):
    """Return True if callsign looks like an airline (3 letters + digits), not an N-number."""
    if not flight or len(flight) < 4:
        return False
    prefix = flight[:3].upper()
    # N-numbers start with N followed by digits — those are GA/private
    if flight[0].upper() == "N" and flight[1].isdigit():
        return False
    # Airline callsigns are 3 alpha chars followed by digits
    return prefix.isalpha() and flight[3].isdigit()

def angle_diff(a, b):
    """Smallest difference between two headings in degrees (0-180)."""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d

def classify_airport(lat, lon, alt, flight="", category="", track=0):
    """
    Multi-factor airport classification:
    1. High altitude → enroute
    2. Airline prefix known → use that airport (if also nearby/low)
    3. Geometry: score each airport by distance + runway alignment
    4. GA fallback: nearest airport within radius
    """
    R = 3958.8

    def dist_mi(alat, alon):
        phi1, phi2 = math.radians(lat), math.radians(alat)
        dphi = math.radians(alat - lat)
        dlam = math.radians(alon - lon)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    # High altitude = definitely enroute
    if alt > 10000:
        return "enroute"

    # Build distance map for all airports
    distances = {code: dist_mi(info[0], info[1]) for code, info in AIRPORTS.items()}

    # Must be within 20 miles of SOME airport to classify
    min_dist = min(distances.values())
    if min_dist > 20:
        return "enroute"

    # Extract airline prefix (first 3 chars of callsign if not N-number)
    is_ga = (category in GA_CATEGORIES or
             (flight and flight[0].upper() == "N" and len(flight) > 1 and flight[1].isdigit()) or
             not flight)

    # For GA: simple nearest airport within 6 miles under 3000ft
    if is_ga:
        if alt > 3000:
            return "enroute"
        best = min(distances, key=distances.get)
        return best if distances[best] <= 6 else "enroute"

    # HARD RULE: airline callsigns can NEVER be classified as GA-only airports
    # KJYO, KGAI, KHEF, KRMN have zero scheduled commercial service
    airline = is_airline_callsign(flight)

    # For commercial: check airline prefix hint first
    prefix = flight[:3].upper() if len(flight) >= 3 else ""
    hint = AIRLINE_AIRPORT.get(prefix)

    # Score each airport: lower is better
    # Score = distance_weight + alignment_penalty
    best_ap, best_score = "enroute", 999

    for code, (alat, alon, _) in AIRPORTS.items():
        d = distances[code]
        if d > 20:
            continue
        # Hard rule: airline flights cannot land at GA-only airports
        if airline and code in GA_ONLY_AIRPORTS:
            continue

        # Distance score: 0 at airport, increases with distance
        dist_score = d * 2.0

        # Runway alignment score: how well does track match any runway heading?
        if track and code in AIRPORT_RUNWAYS:
            min_align = min(angle_diff(track, rwy) for rwy in AIRPORT_RUNWAYS[code])
            # 0° alignment = 0 penalty, 90° = 20 penalty — stronger signal
            align_score = (min_align / 90.0) * 20.0
        else:
            align_score = 10.0  # neutral if no track data

        # Airline hint bonus: strongly prefer the hinted airport
        # Must be strong enough to override proximity to smaller airports
        hint_bonus = -20.0 if (hint == code) else 0.0

        # Altitude modifier: lower alt near airport = stronger signal
        alt_factor = max(0.5, 1.0 - (alt / 10000))

        score = (dist_score + align_score + hint_bonus) * alt_factor

        if score < best_score:
            best_score = score
            best_ap = code

    return best_ap if best_score < 15 else "enroute"

# Ground vehicle category codes — never include these
GROUND_VEHICLE_CATEGORIES = {"C1", "C2", "C3", "C4", "C5"}

def is_arrival(alt, gs, baro_rate, track, lat, lon, category, flight=""):
    """
    Return True if this point looks like an inbound aircraft rather than a departure.
    Keeps: descending aircraft, landing rollout, go-arounds, holds, flybys.
    Drops: departures, ground vehicles, parked/taxiing aircraft.
    """
    # Drop ground vehicles entirely
    if category in GROUND_VEHICLE_CATEGORIES:
        return False
    # Ground level handling — only keep landing rollout (gs > 40 kts)
    if alt < 200:
        return gs > 40
    # Low but airborne — keep (final approach, flare)
    if alt < 2000:
        return True
    # GA departure check — slower thresholds than commercial
    if category in GA_CATEGORIES:
        # If climbing fast enough and heading away from all runways = departure
        if gs > 70 and alt > 500 and alt < 4000:
            if baro_rate > 300:  # definitely climbing
                best_align = min(
                    angle_diff(track, rwy)
                    for runways in AIRPORT_RUNWAYS.values()
                    for rwy in runways
                )
                if best_align > 60:
                    return False
        return True
    # If we have baro_rate data: keep descending or level, drop strong climbers
    if baro_rate != 0:
        # Climbing fast at jet speed = departure
        if baro_rate > 500 and gs > 180 and alt > 500:
            return False
        # Descending = arrival
        if baro_rate < -200:
            return True
        # Level or gentle climb (hold, go-around, flyby) = keep
        return True
    # No baro_rate — use heading + speed to catch obvious departures
    # A fast airline flight with heading that doesn't align with ANY airport runway
    # in the region is almost certainly a departure climbing out
    if gs > 200 and alt > 2000 and alt < 8000 and is_airline_callsign(flight):
        # Check if track aligns within 60° of any runway at any of our airports
        best_align = 180
        for code, runways in AIRPORT_RUNWAYS.items():
            for rwy in runways:
                diff = angle_diff(track, rwy)
                if diff < best_align:
                    best_align = diff
        # If heading doesn't match any runway within 60° = likely departure
        if best_align > 60:
            return False
    # Slow or aligned with a runway = keep
    return True
# END copied from render_github.py

# Departure detection, same rule as render_github.py: per aircraft, in time
# order, a run of 3+ consecutive rises of more than 100 ft starting below
# 500 ft is a departure; its points above 200 ft are dropped.
# The rule was written for the historical data, where an aircraft's points
# are about 10 s apart. At 2 s a normal climb gains under 100 ft per step and
# the rule would miss it, so it is applied to each aircraft's points thinned
# to >= 10 s apart, and every logged point inside a flagged climb is dropped.
# Only the rule's input is thinned; the map keeps every point it doesn't drop.
CLIMB_STEPS = 3
CLIMB_MIN_FT = 100
CLIMB_SPACING_S = 10


def departure_points(rows):
    """Set of (hex, t) for points in a departure climb. rows are time-sorted."""
    by_hex = {}
    for r in rows:
        by_hex.setdefault(r[1], []).append((r[0], r[4]))
    flagged = set()
    for hexid, all_pts in by_hex.items():
        pts = []
        for t, alt in all_pts:
            if not pts or t - pts[-1][0] >= CLIMB_SPACING_S:
                pts.append((t, alt))
        i = 0
        while i < len(pts):
            if pts[i][1] < 500:
                climb_count = 0
                j = i + 1
                while j < len(pts) and pts[j][1] > pts[j - 1][1] + CLIMB_MIN_FT:
                    climb_count += 1
                    j += 1
                if climb_count >= CLIMB_STEPS:
                    # up to the next thinned point, or the end if the climb ran out the data
                    t0 = pts[i][0]
                    t_end = pts[j][0] if j < len(pts) else float("inf")
                    for t, alt in all_pts:
                        if t0 <= t < t_end and alt > 200:
                            flagged.add((hexid, t))
                i = j if j > i else i + 1
            else:
                i += 1
    return flagged


# live.html's airport ids
AIRPORT_IDS = {"KIAD": 0, "KDCA": 1, "KBWI": 2, "KJYO": 3, "KGAI": 4, "KHEF": 5,
               "KRMN": 6, "enroute": 7, "KADW": 8, "KNYG": 9}
CENTER = (38.9444, -77.4558)
RADIUS_NM = 43.45  # 50 statute miles
MAX_ALT_FT = 15000


def dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def load_points(start, end):
    """Map positions from the log, as
    [t, hex, lat, lon, alt, gs, track, vrate, flight, type, mlat, category], one per
    distinct position (an aircraft's other messages repeat its last one)."""
    if not os.path.exists(DB_PATH):
        return []
    lat_pad = RADIUS_NM / 60
    lon_pad = lat_pad / math.cos(math.radians(CENTER[0]))
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=60)
    cur = db.execute("""
        SELECT pos_t, hex, lat, lon, CASE WHEN on_ground = 1 THEN 0 ELSE alt_baro END,
               gs, track, COALESCE(baro_rate, geom_rate), flight, type, mlat, category
        FROM positions
        WHERE t BETWEEN ? AND ? AND pos_t BETWEEN ? AND ?
          AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
          AND (on_ground = 1 OR alt_baro <= ?)""",
        (start, end + 60, start, end,
         CENTER[0] - lat_pad, CENTER[0] + lat_pad, CENTER[1] - lon_pad, CENTER[1] + lon_pad,
         MAX_ALT_FT))
    rows, seen = [], set()
    for t, hexid, lat, lon, alt, gs, track, vrate, flight, actype, mlat, category in cur:
        key = (hexid, t)
        if key in seen or dist_nm(lat, lon, *CENTER) > RADIUS_NM:
            continue
        seen.add(key)
        rows.append([int(t), hexid, round(lat, 5), round(lon, 5), int(alt),
                     None if gs is None else round(gs), None if track is None else round(track),
                     vrate, flight or "", actype or aircraft_lookup.type_for(hexid) or "",
                     mlat, category or ""])
    db.close()
    return rows


COVER = {"SKC": 0, "CLR": 0, "CAVOK": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "OVX": 4}
SKY = ["Clear", "Few clouds", "Scattered clouds", "Broken clouds", "Overcast"]
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def fetch_weather(start):
    """KIAD METARs from aviationweather.gov. Any failure gives no weather."""
    try:
        req = urllib.request.Request(WX_URL, headers={"User-Agent": "rdbf-iad-map"})
        with urllib.request.urlopen(req, timeout=20) as r:
            obs = json.load(r)
    except Exception as e:
        print("publish: no weather this time:", e, flush=True)
        return []
    out = []
    for o in obs if isinstance(obs, list) else []:
        try:
            t = o.get("obsTime")
            if not isinstance(t, (int, float)) or t < start:
                continue
            temp = o.get("temp")
            wdir, wspd = o.get("wdir"), o.get("wspd") or 0
            vrb = not isinstance(wdir, (int, float))
            clouds = [COVER.get(c.get("cover"), 0) for c in (o.get("clouds") or [])]
            vis = o.get("visib")
            if isinstance(vis, str):
                vis = float(vis.rstrip("+")) if vis.rstrip("+").replace(".", "", 1).isdigit() else None
            out.append({
                "t": round((t - start) / 60, 1),
                "epoch": int(t),
                "altim_hpa": o.get("altim") if isinstance(o.get("altim"), (int, float)) else None,
                "tmpf": round(temp * 9 / 5 + 32) if isinstance(temp, (int, float)) else None,
                "drct": 0 if vrb else int(wdir),
                "sknt": int(wspd),
                "gust": o.get("wgst"),
                "compass": "VRB" if vrb or wspd == 0 else COMPASS[int((wdir % 360) / 22.5 + 0.5) % 16],
                "vsby": vis,
                "sky": SKY[max(clouds)] if clouds else "Clear",
                "wx": o.get("wxString") or "",
            })
        except Exception:
            continue
    out.sort(key=lambda w: w["t"])
    return out


def hour_chunk(rows, hour_start):
    """One hour's positions. Aircraft identity is stored once per hour in
    `ac` as [hex, flight, type, ga]; each point is
    [lat, lon, alt_ft, seconds_into_hour, airport_id, ac_index, gs, track, vrate]."""
    ac, ac_index, pts = [], {}, []
    for t, hexid, lat, lon, alt, gs, track, vrate, flight, actype, mlat, category in rows:
        # GA: GA category, an N-number (N then a digit), or no callsign -- the
        # same test classify_airport() uses. render_github.py counted any
        # callsign starting with "N", which caught Spirit (NKS); fixed
        # 2026-09-24. live.html skips its own filter for GA points.
        ga = 1 if (category in GA_CATEGORIES or
                   (flight and flight[0].upper() == "N" and len(flight) > 1 and flight[1].isdigit()) or
                   not flight) else 0
        key = (hexid, flight, actype, ga)
        if key not in ac_index:
            ac_index[key] = len(ac)
            ac.append([hexid, flight, actype, ga])
        ap = classify_airport(lat, lon, alt, flight, category, track or 0)
        pts.append([lat, lon, alt, t - hour_start, AIRPORT_IDS.get(ap, 7),
                    ac_index[key], gs, track, vrate])
    return {"start": hour_start, "ac": ac, "pts": pts}


def altim_lookup(wx):
    """altim_at(t): KIAD's altimeter setting (hPa) in force at time t, i.e.
    from the latest report at or before it (within 3 hours), else None."""
    obs = sorted((w["epoch"], w["altim_hpa"]) for w in wx if w.get("altim_hpa"))
    def altim_at(t):
        best = None
        for e, a in obs:
            if e <= t + 60:
                best = (e, a)
        return best[1] if best and t - best[0] < 3 * 3600 else None
    return altim_at


def build(now, wx=None):
    """Returns (index, {filename: bytes})."""
    end = int(now)
    start = end - SPAN_S
    first_hour = start - start % 3600
    logged = [r for r in load_points(first_hour, end) if r[4] <= MAX_ALT_FT]
    logged.sort(key=lambda r: (r[0], r[1]))
    departures = departure_points(logged)
    # Same filters as render_github.py: drop departure climbs, then anything
    # is_arrival() rejects (ground vehicles, taxiing, fast climb-outs).
    raw = [r for r in logged
           if (r[1], r[0]) not in departures
           and is_arrival(r[4], r[5] or 0, r[7] or 0, r[6] or 0, r[2], r[3], r[11], r[8])]
    by_hour = {}
    for r in raw:
        by_hour.setdefault(r[0] - r[0] % 3600, []).append(r)
    files, chunks = {}, []
    for hour_start in sorted(by_hour):
        name = "h/" + time.strftime("%Y%m%d%H", time.gmtime(hour_start)) + ".json"
        body = json.dumps(hour_chunk(by_hour[hour_start], hour_start),
                          separators=(",", ":")).encode()
        files[name] = body
        chunks.append({"name": name, "hash": hashlib.sha1(body).hexdigest()[:12]})
    # Whole recorded tracks, every altitude and range, for drawing a selected
    # flight's full path (tracks.py). Failure here never stops the map.
    track_chunks = []
    try:
        if os.path.exists(DB_PATH):
            tfiles = tracks.build(DB_PATH, WORK_DIR, start, end, aircraft_lookup.type_for)
            for name in sorted(tfiles):
                files[name] = tfiles[name]
                track_chunks.append({"name": name, "hash": hashlib.sha1(tfiles[name]).hexdigest()[:12]})
    except Exception as e:
        print("publish: track files failed:", e, flush=True)
    window = [r for r in raw if r[0] >= start]
    heard = [r for r in logged if r[0] >= start]
    mlat_ac = {r[1] for r in heard if r[10]}
    adsb_ac = {r[1] for r in heard if not r[10]}
    index = {
        "meta": {
            "start": start, "end": end, "built": end,
            "span_min": SPAN_S // 60,
            "positions": len(window), "aircraft": len({r[1] for r in window}),
            "positions_in_area": len(heard),
            "departure_points_dropped": sum(1 for r in heard if (r[1], r[0]) in departures),
            "mlat_positions": sum(1 for r in heard if r[10]),
            "aircraft_mlat_only": len(mlat_ac - adsb_ac),
            "receiver": "Red Devil Bison Farm, Poolesville MD",
        },
        "wx": wx if wx is not None else fetch_weather(start),
        # Events at any range, for highlighting: emergencies from alerts.py,
        # close approaches and TCAS advisories from proximity.py.
        "events": {
            "emergencies": proximity.recent(EVENTS_DIR, "emergencies.jsonl", start),
            "close_approaches": proximity.recent(EVENTS_DIR, "close_approaches.jsonl", start),
            "tcas": proximity.recent(EVENTS_DIR, "tcas.jsonl", start),
        },
        # Aircraft over the farm (farm.py): every pass in the window, props
        # included and marked; the summary leaves props out.
        "farm": {
            "center": list(farm.FARM), "radius_nm": farm.FARM_RADIUS_NM,
            # for the map's Near-the-farm search: average ground height, basis
            "ground_ft": farm.ground_ft(*farm.FARM)[0], "ground_basis": farm.ground_ft(*farm.FARM)[1],
            "passes": proximity.recent(EVENTS_DIR, "farm_passes.jsonl", start),
        },
        "chunks": chunks,
        "tracks": track_chunks,
    }
    return index, files


BRANCH_README = """# live-data

Written by the ADS-B receiver at Red Devil Bison Farm every 15 minutes and
force-pushed, so this branch only ever holds one commit: the latest 24 hours.
Do not edit it; the next push replaces it. The code that writes it is in
`pi/` on `main`, and `live.html` on `main` is the page that reads it.
"""


def git(*args, env=None):
    return subprocess.run(["git", *args], cwd=WORK_DIR, env=env, check=True,
                          timeout=300, capture_output=True, text=True).stdout


def write_tree(index, files):
    for sub in ("h", "t"):
        os.makedirs(os.path.join(WORK_DIR, sub), exist_ok=True)
        for name in os.listdir(os.path.join(WORK_DIR, sub)):
            if f"{sub}/{name}" not in files:
                os.remove(os.path.join(WORK_DIR, sub, name))
    for name, body in files.items():
        path = os.path.join(WORK_DIR, name)
        try:
            with open(path, "rb") as f:
                if f.read() == body:
                    continue
        except OSError:
            pass
        with open(path, "wb") as f:
            f.write(body)
    with open(os.path.join(WORK_DIR, "live.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    with open(os.path.join(WORK_DIR, "README.md"), "w") as f:
        f.write(BRANCH_README)


def push(index, files):
    os.makedirs(WORK_DIR, exist_ok=True)
    if not os.path.isdir(os.path.join(WORK_DIR, ".git")):
        git("init", "-q")
        git("symbolic-ref", "HEAD", f"refs/heads/{BRANCH}")
    write_tree(index, files)
    env = dict(os.environ, GIT_SSH_COMMAND=(
        f"ssh -i {DEPLOY_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={os.path.dirname(DEPLOY_KEY)}/known_hosts"))
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(index["meta"]["end"]))
    has_commit = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=WORK_DIR,
                                capture_output=True).returncode == 0
    git("add", "-A")
    git("-c", "user.name=RDBF ADS-B receiver", "-c", "user.email=adsb-receiver@localhost",
        "commit", "-q", *(["--amend"] if has_commit else []), "-m", f"Rolling 24 hours to {stamp}")
    # The previous commit is still in the local object store, so git sees the
    # remote already has every unchanged hour file and sends only the new ones.
    git("push", "-q", "--force", REMOTE, f"HEAD:{BRANCH}", env=env)
    git("reflog", "expire", "--expire=now", "--all")
    git("gc", "-q", "--prune=now")


def enrich(index):
    """Give every aircraft named in an event its registration, type and the
    type's full name, for the map's detail card. Looked up at publish time,
    so events logged before a lookup existed get them too."""
    def one(x):
        h = x.get("hex")
        x["type"] = x.get("type") or aircraft_lookup.type_for(h) or ""
        x["reg"] = x.get("reg") or aircraft_lookup.reg_for(h)
        x["type_name"] = farm.type_name(x["type"])
        op = aircraft_lookup.operator_for(x.get("flight"))
        if op:
            x["operator"], x["operator_radio"] = op
        x["prop"] = farm.is_prop(x["type"]) if x.get("prop") is None else x["prop"]
    ev = index["events"]
    for e in ev["emergencies"] + ev["tcas"] + index["farm"]["passes"]:
        one(e)
    for e in ev["close_approaches"]:
        one(e["a"])
        one(e["b"])


def main():
    wx = fetch_weather(int(time.time()) - SPAN_S)
    if os.path.exists(DB_PATH):
        try:
            n = proximity.update(DB_PATH, EVENTS_DIR)
            print(f"publish: {n} close approaches logged", flush=True)
        except Exception as e:  # never let this stop the map
            print("publish: close-approach check failed:", e, flush=True)
        try:
            n = farm.update(DB_PATH, EVENTS_DIR, altim_at=altim_lookup(wx))
            print(f"publish: {n} passes over the farm logged", flush=True)
        except Exception as e:  # never let this stop the map
            print("publish: farm-pass check failed:", e, flush=True)
    index, files = build(time.time(), wx)
    index["farm"]["summary"] = farm.summary(index["farm"]["passes"])
    enrich(index)
    m = index["meta"]
    print(f"publish: {m['positions']} positions, {m['aircraft']} aircraft, "
          f"{m['mlat_positions']} MLAT positions, {m['aircraft_mlat_only']} aircraft seen only by MLAT, "
          f"{len(index['wx'])} weather reports, {len(files)} hour files", flush=True)
    if "--no-push" in sys.argv:
        return
    push(index, files)
    print("publish: pushed to", BRANCH, flush=True)


if __name__ == "__main__":
    main()
