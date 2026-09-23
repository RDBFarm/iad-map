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
import hashlib, json, math, os, re, sqlite3, subprocess, sys, time, urllib.request

DB_PATH = os.environ.get("IADMAP_DB", "/mnt/flightdata/iad-map/flights.db")
WORK_DIR = os.environ.get("IADMAP_PUBLISH_DIR", "/mnt/flightdata/iad-map/publish")
DEPLOY_KEY = os.environ.get("IADMAP_DEPLOY_KEY", "/var/lib/iad-map/deploy_key")
REMOTE = os.environ.get("IADMAP_REMOTE", "git@github.com:RDBFarm/iad-map.git")
BRANCH = "live-data"
WX_URL = os.environ.get(
    "IADMAP_WX_URL",
    "https://aviationweather.gov/api/data/metar?ids=KIAD&format=json&hours=25")
SPAN_S = 24 * 3600

# Airport ids as live.html numbers them; 7 is en-route.
# A position is labelled with the nearest airport when it is within that
# airport's radius and below its ceiling, except that an airline callsign is
# never given a GA-only field. The radii and ceilings are this script's own
# rule, chosen to resemble the historical map; they are not the classifier in
# render_github.py, which isn't in this repository.
AIRPORTS = [  # (id, lat, lon, radius_nm, ceiling_ft, ga_only)
    (0, 38.9444, -77.4558, 15, 10000, False),  # KIAD
    (1, 38.8521, -77.0377, 15, 10000, False),  # KDCA
    (2, 39.1754, -76.6683, 15, 10000, False),  # KBWI
    (3, 39.0779, -77.5578, 5, 3000, True),     # KJYO
    (4, 39.1683, -77.1660, 5, 3000, True),     # KGAI
    (5, 38.7214, -77.5153, 5, 3000, True),     # KHEF
    (6, 38.5997, -77.4542, 5, 3000, True),     # KRMN
    (8, 38.8108, -76.8674, 12, 8000, False),   # KADW
    (9, 38.5036, -77.3050, 5, 3000, False),    # KNYG
]
# Three letters then a digit: the ICAO airline-callsign shape (UAL123, JIA5675).
# Some military callsigns share it (PAT13); they are kept off GA-only fields too.
AIRLINE_CALLSIGN = re.compile(r"^[A-Z]{3}[0-9]")
CENTER = (38.9444, -77.4558)
RADIUS_NM = 43.45  # 50 statute miles
MAX_ALT_FT = 15000


def dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def airport_for(lat, lon, alt, flight):
    airline = bool(AIRLINE_CALLSIGN.match(flight or ""))
    best, best_d = 7, None
    for ap, alat, alon, radius, ceiling, ga_only in AIRPORTS:
        if ga_only and airline:
            continue
        d = dist_nm(lat, lon, alat, alon)
        if d <= radius and alt < ceiling and (best_d is None or d < best_d):
            best, best_d = ap, d
    return best


def load_points(start, end):
    """Map positions from the log, as
    [t, hex, lat, lon, alt, gs, track, vrate, flight, type, mlat], one per
    distinct position (an aircraft's other messages repeat its last one)."""
    if not os.path.exists(DB_PATH):
        return []
    lat_pad = RADIUS_NM / 60
    lon_pad = lat_pad / math.cos(math.radians(CENTER[0]))
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=60)
    cur = db.execute("""
        SELECT pos_t, hex, lat, lon, CASE WHEN on_ground = 1 THEN 0 ELSE alt_baro END,
               gs, track, COALESCE(baro_rate, geom_rate), flight, type, mlat
        FROM positions
        WHERE t BETWEEN ? AND ? AND pos_t BETWEEN ? AND ?
          AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
          AND (on_ground = 1 OR alt_baro <= ?)""",
        (start, end + 60, start, end,
         CENTER[0] - lat_pad, CENTER[0] + lat_pad, CENTER[1] - lon_pad, CENTER[1] + lon_pad,
         MAX_ALT_FT))
    rows, seen = [], set()
    for t, hexid, lat, lon, alt, gs, track, vrate, flight, actype, mlat in cur:
        key = (hexid, t)
        if key in seen or dist_nm(lat, lon, *CENTER) > RADIUS_NM:
            continue
        seen.add(key)
        rows.append([int(t), hexid, round(lat, 5), round(lon, 5), int(alt),
                     None if gs is None else round(gs), None if track is None else round(track),
                     vrate, flight or "", actype or "", mlat])
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
    `ac` as [hex, flight, type]; each point is
    [lat, lon, alt_ft, seconds_into_hour, airport_id, ac_index, gs, track, vrate]."""
    ac, ac_index, pts = [], {}, []
    for t, hexid, lat, lon, alt, gs, track, vrate, flight, actype, mlat in rows:
        key = (hexid, flight, actype)
        if key not in ac_index:
            ac_index[key] = len(ac)
            ac.append([hexid, flight, actype])
        pts.append([lat, lon, alt, t - hour_start, airport_for(lat, lon, alt, flight),
                    ac_index[key], gs, track, vrate])
    return {"start": hour_start, "ac": ac, "pts": pts}


def build(now):
    """Returns (index, {filename: bytes})."""
    end = int(now)
    start = end - SPAN_S
    first_hour = start - start % 3600
    raw = [r for r in load_points(first_hour, end) if r[4] <= MAX_ALT_FT]
    raw.sort(key=lambda r: (r[0], r[1]))
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
    window = [r for r in raw if r[0] >= start]
    mlat_ac = {r[1] for r in window if r[10]}
    adsb_ac = {r[1] for r in window if not r[10]}
    index = {
        "meta": {
            "start": start, "end": end, "built": end,
            "span_min": SPAN_S // 60,
            "positions": len(window), "aircraft": len(mlat_ac | adsb_ac),
            "mlat_positions": sum(1 for r in window if r[10]),
            "aircraft_mlat_only": len(mlat_ac - adsb_ac),
            "receiver": "Red Devil Bison Farm, Poolesville MD",
        },
        "wx": fetch_weather(start),
        "chunks": chunks,
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
    os.makedirs(os.path.join(WORK_DIR, "h"), exist_ok=True)
    for name in os.listdir(os.path.join(WORK_DIR, "h")):
        if "h/" + name not in files:
            os.remove(os.path.join(WORK_DIR, "h", name))
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


def main():
    index, files = build(time.time())
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
