#!/usr/bin/env python3
"""Publish the last 24 hours of recorded positions to GitHub.

Run every 15 minutes by iad-map-publish.timer. Reads the collector's hourly
files, builds live_24h.json in the shape live.html expects, and force-pushes
it as the only commit on the `live-data` branch of RDBFarm/iad-map. Each push
replaces the last, so the repository does not grow. live.html fetches the
file from raw.githubusercontent.com; GitHub Pages is not rebuilt.

  python3 publish.py            build and push
  python3 publish.py --no-push  build only, and print a summary
"""
import json, math, os, shutil, subprocess, sys, time, urllib.request

POINTS_DIR = os.environ.get("IADMAP_POINTS_DIR", "/var/lib/iad-map/points")
WORK_DIR = os.environ.get("IADMAP_PUBLISH_DIR", "/var/lib/iad-map/publish")
DEPLOY_KEY = os.environ.get("IADMAP_DEPLOY_KEY", "/var/lib/iad-map/deploy_key")
REMOTE = os.environ.get("IADMAP_REMOTE", "git@github.com:RDBFarm/iad-map.git")
BRANCH = "live-data"
WX_URL = os.environ.get(
    "IADMAP_WX_URL",
    "https://aviationweather.gov/api/data/metar?ids=KIAD&format=json&hours=25")
SPAN_S = 24 * 3600

# Airport ids as live.html numbers them; 7 is en-route.
# A position is labelled with the nearest airport when it is within that
# airport's radius and below its ceiling. These thresholds are this script's
# own rule, chosen to resemble the historical map; they are not an FAA
# definition of an arrival.
AIRPORTS = [  # (id, lat, lon, radius_nm, ceiling_ft)
    (0, 38.9444, -77.4558, 15, 10000),  # KIAD
    (1, 38.8521, -77.0377, 15, 10000),  # KDCA
    (2, 39.1754, -76.6683, 15, 10000),  # KBWI
    (3, 39.0779, -77.5578, 5, 3000),    # KJYO
    (4, 39.1683, -77.1660, 5, 3000),    # KGAI
    (5, 38.7214, -77.5153, 5, 3000),    # KHEF
    (6, 38.5997, -77.4542, 5, 3000),    # KRMN
    (8, 38.8108, -76.8674, 12, 8000),   # KADW
    (9, 38.5036, -77.3050, 5, 3000),    # KNYG
]
MAX_ALT_FT = 15000


def dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def airport_for(lat, lon, alt):
    best, best_d = 7, None
    for ap, alat, alon, radius, ceiling in AIRPORTS:
        d = dist_nm(lat, lon, alat, alon)
        if d <= radius and alt < ceiling and (best_d is None or d < best_d):
            best, best_d = ap, d
    return best


def load_points(start, end):
    rows = []
    if not os.path.isdir(POINTS_DIR):
        return rows
    first = time.strftime("%Y%m%d%H", time.gmtime(start))
    for name in sorted(os.listdir(POINTS_DIR)):
        if not name.endswith(".jsonl") or name[:-6] < first:
            continue
        with open(os.path.join(POINTS_DIR, name)) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue  # a line cut short by a power loss
                if start <= r[0] <= end:
                    rows.append(r)
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


def build(now):
    end = int(now)
    start = end - SPAN_S
    raw = load_points(start, end)
    raw.sort(key=lambda r: r[0])
    pts, aircraft, mlat_ac, adsb_ac, mlat_pts = [], set(), set(), set(), 0
    for t, hexid, lat, lon, alt, gs, track, vrate, flight, actype, mlat in raw:
        if alt > MAX_ALT_FT:
            continue
        aircraft.add(hexid)
        if mlat:
            mlat_pts += 1
            mlat_ac.add(hexid)
        else:
            adsb_ac.add(hexid)
        pts.append([
            lat, lon, round(max(0.0, min(1.0, 1 - alt / MAX_ALT_FT)), 3),
            round((t - start) / 60, 2), airport_for(lat, lon, alt),
            flight or hexid.upper(), actype, gs, alt, track, vrate,
        ])
    return {
        "meta": {
            "start": start, "end": end, "built": end,
            "span_min": SPAN_S // 60,
            "positions": len(pts), "aircraft": len(aircraft),
            "mlat_positions": mlat_pts,
            "aircraft_mlat_only": len(mlat_ac - adsb_ac),
            "receiver": "Red Devil Bison Farm, Poolesville MD",
        },
        "wx": fetch_weather(start),
        "pts": pts,
    }


BRANCH_README = """# live-data

Written by the ADS-B receiver at Red Devil Bison Farm every 15 minutes and
force-pushed, so this branch only ever holds one commit: the latest 24 hours.
Do not edit it; the next push replaces it. The code that writes it is in
`pi/` on `main`, and `live.html` on `main` is the page that reads it.
"""


def push(payload):
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    os.makedirs(WORK_DIR)
    with open(os.path.join(WORK_DIR, "live_24h.json"), "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    with open(os.path.join(WORK_DIR, "README.md"), "w") as f:
        f.write(BRANCH_README)
    env = dict(os.environ, GIT_SSH_COMMAND=(
        f"ssh -i {DEPLOY_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={os.path.dirname(DEPLOY_KEY)}/known_hosts"))
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(payload["meta"]["end"]))
    for cmd in (
        ["git", "init", "-q"],
        ["git", "symbolic-ref", "HEAD", f"refs/heads/{BRANCH}"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=RDBF ADS-B receiver", "-c", "user.email=adsb-receiver@localhost",
         "commit", "-q", "-m", f"Rolling 24 hours to {stamp}"],
        ["git", "push", "-q", "--force", REMOTE, f"HEAD:{BRANCH}"],
    ):
        subprocess.run(cmd, cwd=WORK_DIR, env=env, check=True, timeout=300)


def main():
    payload = build(time.time())
    m = payload["meta"]
    print(f"publish: {m['positions']} positions, {m['aircraft']} aircraft, "
          f"{m['mlat_positions']} MLAT positions, {m['aircraft_mlat_only']} aircraft seen only by MLAT, "
          f"{len(payload['wx'])} weather reports", flush=True)
    if "--no-push" in sys.argv:
        return
    push(payload)
    print("publish: pushed to", BRANCH, flush=True)


if __name__ == "__main__":
    main()
