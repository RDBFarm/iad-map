#!/usr/bin/env python3
"""Publish the last 24 hours of recorded positions to GitHub.

Run every 15 minutes by iad-map-publish.timer. Reads the collector's log
(flights.db), keeps what the map shows -- positions within 50 statute miles
of Dulles at or below 15,000 ft -- and writes, on the `live-data` branch of RDBFarm/iad-map:

  live.json        what live.html reads first: the window, counts, KIAD
                   weather, the list of hour files with a hash of each, and
                   the finished days kept
  h/<day>/<hour>.json   one file per UTC hour of positions (t/ the same for
                   whole tracks), a folder per UTC day
  d/<date>.json    one per finished local (Eastern) day: its hour files,
                   weather and events, written once just after midnight

Finished hours are kept for KEEP_DAYS days (owner, 2026-09-26: a longer
timeline, "as long as the file sizes don't change"). They are already
frozen and pushed once, so keeping them costs no upload; the per-day folders
keep each push's folder listings as short as with 24 hours. Kept files stay
on disk and are never read back into memory (30 days is ~1.5 GB).

The branch is kept to a single commit, replaced and force-pushed each run,
so the repository does not grow. Two things keep each push down to what
changed (the current hour, the index), and both were missing until
2026-09-26, when every push carried the whole ~18 MiB tree:

  - A finished hour's file is frozen: built once, SETTLE_S after the hour
    ends, then read back from disk on every later run (FINAL_LIST names the
    frozen ones). Rebuilt each run, old hours changed: for one, an aircraft
    last heard low that reappears hours later climbing makes the departure
    rule drop points from the old hour.
  - Git only skips files the remote already has if the remote's commit is a
    parent of what is pushed. An amended single commit has no parent, so git
    sent everything. Each push therefore also sends, to `live-data-prev`, a
    commit with the same files whose parent is the current `live-data` tip;
    that makes the old tip's files count as already there.

live.html fetches from raw.githubusercontent.com, so GitHub Pages is not
rebuilt.

  python3 publish.py            build and push
  python3 publish.py --no-push  build only, and print a summary
"""
import calendar, datetime, hashlib, json, math, os, re, sqlite3, subprocess, sys, time, urllib.request
from zoneinfo import ZoneInfo

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
PREV_BRANCH = "live-data-prev"   # carries the parent link; see the docstring
SETTLE_S = 600   # an hour's file is final this long after the hour ends
# the hour files already built final, one name per line; kept inside .git so
# it is never pushed
FINAL_LIST = os.path.join(WORK_DIR, ".git", "iad-map-final-hours")
WX_URL = os.environ.get(
    "IADMAP_WX_URL",
    "https://aviationweather.gov/api/data/metar?ids=KIAD&format=json&hours=25")
SPAN_S = 24 * 3600
KEEP_DAYS = 30              # finished local days kept for the map's day picker
LOCAL = ZoneInfo("America/New_York")
# a full repack once a day; otherwise only unreachable loose objects are pruned
GC_STAMP = os.path.join(WORK_DIR, ".git", "iad-map-last-gc")

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
# A rise only counts as part of a climb when the two thinned points are at
# most this far apart (owner, 09-26). Without it, an aircraft last heard low
# that reappeared hours later higher up read as one climb, and its earlier
# arrival points were dropped.
CLIMB_MAX_GAP_S = 60


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
                while (j < len(pts) and pts[j][1] > pts[j - 1][1] + CLIMB_MIN_FT
                       and pts[j][0] - pts[j - 1][0] <= CLIMB_MAX_GAP_S):
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


def fetch_weather(start, hours=None):
    """KIAD METARs from aviationweather.gov. Any failure gives no weather.
    `hours` widens the look-back (for a finished day's file)."""
    url = re.sub(r"hours=\d+", f"hours={int(hours)}", WX_URL) if hours else WX_URL
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "rdbf-iad-map"})
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


def read_final():
    """Files built after their hour was settled, still on disk. Hour files in
    the flat layout used until 2026-09-26 (h/<YYYYMMDDHH>.json) are moved
    into their day folder: same bytes, so nothing is uploaded again."""
    try:
        with open(FINAL_LIST) as f:
            names = f.read().split()
    except OSError:
        return set()
    out = set()
    for n in names:
        p = tracks.parse_name(n)
        if p and "/" not in n[2:]:
            new = tracks.hour_name(*p)
            old_path, new_path = os.path.join(WORK_DIR, n), os.path.join(WORK_DIR, new)
            if os.path.exists(old_path):
                os.makedirs(os.path.dirname(new_path), exist_ok=True)
                os.replace(old_path, new_path)
            n = new
        if os.path.exists(os.path.join(WORK_DIR, n)):
            out.add(n)
    return out


def local_day(epoch):
    return datetime.datetime.fromtimestamp(epoch, LOCAL).date()


def day_bounds(date):
    """Start and end (epoch) of a local calendar day; 23 or 25 h at DST."""
    a = datetime.datetime.combine(date, datetime.time(), LOCAL)
    b = datetime.datetime.combine(date + datetime.timedelta(days=1), datetime.time(), LOCAL)
    return int(a.timestamp()), int(b.timestamp())


def file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:12]


def kept_files(final, end):
    """Frozen files old enough to have left the 24 h window but inside
    KEEP_DAYS: kept on disk and in the branch as they are. Returns
    ({name: None}, the oldest local date kept)."""
    oldest = local_day(end) - datetime.timedelta(days=KEEP_DAYS)
    keep_from = day_bounds(oldest)[0]
    kept = {}
    for n in final:
        p = tracks.parse_name(n)
        if p and p[1] >= keep_from:
            kept[n] = None
        elif n.startswith("d/"):
            try:
                if datetime.date.fromisoformat(n[2:12]) >= oldest:
                    kept[n] = None
            except ValueError:
                pass
    return kept, oldest


def day_file(date, names, now, final_names):
    """The index of one finished local day, from its hour files on disk."""
    start, end = day_bounds(date)
    chunks, trk, positions, aircraft, first = [], [], 0, set(), None
    for n in sorted(names):
        kind, hour = tracks.parse_name(n)
        if not (start <= hour < end):
            continue
        path = os.path.join(WORK_DIR, n)
        entry = {"name": n, "hash": file_hash(path)}
        if kind == "h":
            chunks.append(entry)
            with open(path, "rb") as f:
                body = json.load(f)
            positions += len(body["pts"])
            aircraft.update(a[0] for a in body["ac"])
            first = hour if first is None else min(first, hour)
        else:
            trk.append(entry)
    ev = {k: [e for e in proximity.recent(EVENTS_DIR, k + ".jsonl", start)
              if start <= e.get("t", e.get("cpa_t", 0)) < end]
          for k in ("emergencies", "close_approaches", "tcas")}
    passes = [p for p in proximity.recent(EVENTS_DIR, "farm_passes.jsonl", start) if p.get("t", 0) < end]
    wx = [w for w in fetch_weather(start, hours=math.ceil((now - start) / 3600) + 1) if w["epoch"] < end]
    index = {
        "meta": {"start": start, "end": end, "built": end, "span_min": (end - start) // 60,
                 "positions": positions, "aircraft": len(aircraft), "date": date.isoformat(),
                 # the day the archive began (or the Pi was off) may start late
                 "first_hour": first},
        "wx": wx,
        "events": ev,
        "farm": {"center": list(farm.FARM), "radius_nm": farm.FARM_RADIUS_NM,
                 "ground_ft": farm.ground_ft(*farm.FARM)[0], "ground_basis": farm.ground_ft(*farm.FARM)[1],
                 "passes": passes},
        "chunks": chunks,
        "tracks": trk,
    }
    index["farm"]["summary"] = farm.summary(passes)
    enrich(index)
    return json.dumps(index, separators=(",", ":")).encode()


def build(now, wx=None, final=frozenset()):
    """Returns (index, {filename: bytes}, names of the files now final).
    Files named in `final` are read back from disk, not rebuilt."""
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
    files, chunks, now_final = {}, [], set()
    for hour_start in sorted(by_hour):
        name = tracks.hour_name("h", hour_start)
        if name in final:
            with open(os.path.join(WORK_DIR, name), "rb") as f:
                body = f.read()
        else:
            body = json.dumps(hour_chunk(by_hour[hour_start], hour_start),
                              separators=(",", ":")).encode()
        if hour_start + 3600 + SETTLE_S <= end:
            now_final.add(name)
        files[name] = body
        chunks.append({"name": name, "hash": hashlib.sha1(body).hexdigest()[:12]})
    # Whole recorded tracks, every altitude and range, for drawing a selected
    # flight's full path (tracks.py). Failure here never stops the map.
    track_chunks = []
    try:
        if os.path.exists(DB_PATH):
            tfiles, tfinal = tracks.build(DB_PATH, WORK_DIR, start, end,
                                          aircraft_lookup.type_for, final, SETTLE_S)
            now_final |= tfinal
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
    return index, files, now_final & set(files)


BRANCH_README = """# live-data

Written by the ADS-B receiver at Red Devil Bison Farm every 15 minutes and
force-pushed, so this branch only ever holds one commit: the latest 24 hours.
`live-data-prev` holds the same files on top of the previous push, only so
that git uploads just what changed. Do not edit either; the next push
replaces them. The code that writes it is in
`pi/` on `main`, and `live.html` on `main` is the page that reads it.
"""


def git(*args, env=None):
    return subprocess.run(["git", *args], cwd=WORK_DIR, env=env, check=True,
                          timeout=300, capture_output=True, text=True).stdout


def write_files(files):
    """Write the files that are held in memory (None = kept on disk as is)."""
    for name, body in files.items():
        if body is None:
            continue
        path = os.path.join(WORK_DIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "rb") as f:
                if f.read() == body:
                    continue
        except OSError:
            pass
        with open(path, "wb") as f:
            f.write(body)


def write_tree(index, files):
    """Everything in `files` on disk, everything else under h/ t/ d/ gone."""
    for sub in ("h", "t", "d"):
        top = os.path.join(WORK_DIR, sub)
        os.makedirs(top, exist_ok=True)
        for dirpath, dirnames, filenames in os.walk(top, topdown=False):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                if os.path.relpath(full, WORK_DIR) not in files:
                    os.remove(full)
            if dirpath != top and not os.listdir(dirpath):
                os.rmdir(dirpath)
    write_files(files)
    with open(os.path.join(WORK_DIR, "live.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    with open(os.path.join(WORK_DIR, "README.md"), "w") as f:
        f.write(BRANCH_README)


def push(index, files, final):
    os.makedirs(WORK_DIR, exist_ok=True)
    if not os.path.isdir(os.path.join(WORK_DIR, ".git")):
        git("init", "-q")
        git("symbolic-ref", "HEAD", f"refs/heads/{BRANCH}")
    write_tree(index, files)
    with open(FINAL_LIST + ".tmp", "w") as f:
        f.write("".join(n + "\n" for n in sorted(final)))
    os.replace(FINAL_LIST + ".tmp", FINAL_LIST)
    env = dict(os.environ, GIT_SSH_COMMAND=(
        f"ssh -i {DEPLOY_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={os.path.dirname(DEPLOY_KEY)}/known_hosts"))
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(index["meta"]["end"]))
    # The last commit pushed to live-data (the local branch moves only after
    # a push succeeds, so it is what GitHub holds).
    prev = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/heads/{BRANCH}^{{commit}}"],
                          cwd=WORK_DIR, capture_output=True, text=True).stdout.strip()
    git("add", "-A")
    tree = git("write-tree").strip()
    ident = ["-c", "user.name=RDBF ADS-B receiver", "-c", "user.email=adsb-receiver@localhost"]
    msg = f"Rolling 24 hours to {stamp}"
    commit = git(*ident, "commit-tree", tree, "-m", msg).strip()
    # Same files, with the previous push as parent: git then sends only the
    # files that commit lacks. Without a parent link it sends every file.
    carrier = git(*ident, "commit-tree", tree, *(["-p", prev] if prev else []),
                  "-m", msg + " (on top of the previous push)").strip()
    git("push", "-q", "--force", REMOTE, f"{commit}:refs/heads/{BRANCH}",
        f"{carrier}:refs/heads/{PREV_BRANCH}", env=env)
    git("update-ref", f"refs/heads/{BRANCH}", commit)
    git("update-ref", f"refs/heads/{PREV_BRANCH}", carrier)
    git("reflog", "expire", "--expire=now", "--all")
    # Unreachable loose objects (old live.json, the current hour's earlier
    # versions) every run; a full repack, which rewrites every kept file's
    # objects, only once a day.
    git("prune", "--expire=now")
    try:
        last_gc = os.path.getmtime(GC_STAMP)
    except OSError:
        last_gc = 0
    if time.time() - last_gc > 20 * 3600:
        git("gc", "-q", "--prune=now")
        with open(GC_STAMP, "w"):
            pass


def archive(index, files, final, end):
    """Add the kept days to what is published: frozen files older than the
    24 h window (kept on disk, not read), a d/<date>.json for each finished
    local day not written yet, and the list of days in live.json. Returns
    the names final after this run."""
    write_files(files)                       # day files read this run's hours from disk
    kept, oldest = kept_files(final, end)
    for n in kept:
        files.setdefault(n, None)
    final = set(final) | set(kept)
    hour_names = [n for n in files if tracks.parse_name(n)]
    date = oldest
    while date < local_day(end):
        name = f"d/{date.isoformat()}.json"
        day_end = day_bounds(date)[1]
        if name not in files and day_end + SETTLE_S <= end:
            day_hours = [n for n in hour_names if n in final
                         and day_bounds(date)[0] <= tracks.parse_name(n)[1] < day_end]
            if any(n.startswith("h/") for n in day_hours):
                try:
                    files[name] = day_file(date, day_hours, end, final)
                    final.add(name)
                except Exception as e:     # never let this stop the map
                    print(f"publish: day file {name} failed:", e, flush=True)
        date += datetime.timedelta(days=1)
    days = []
    for n in sorted(f for f in files if f.startswith("d/")):
        body = files[n]
        h = hashlib.sha1(body).hexdigest()[:12] if body is not None else file_hash(os.path.join(WORK_DIR, n))
        days.append({"date": n[2:12], "hash": h})
    index["days"] = days
    return final & set(files)


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
    frozen = read_final()
    index, files, final = build(time.time(), wx, frozen)
    index["farm"]["summary"] = farm.summary(index["farm"]["passes"])
    enrich(index)
    final = archive(index, files, final | (frozen - set(files)), index["meta"]["end"])
    m = index["meta"]
    print(f"publish: {m['positions']} positions, {m['aircraft']} aircraft, "
          f"{m['mlat_positions']} MLAT positions, {m['aircraft_mlat_only']} aircraft seen only by MLAT, "
          f"{len(index['wx'])} weather reports, {len(files)} files, {len(index['days'])} days kept", flush=True)
    if "--no-push" in sys.argv:
        return
    push(index, files, final)
    print("publish: pushed to", BRANCH, flush=True)


if __name__ == "__main__":
    main()
