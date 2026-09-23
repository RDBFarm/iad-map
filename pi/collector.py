#!/usr/bin/env python3
"""Record aircraft positions heard by this receiver.

Every 2 seconds, reads the receiver's current picture (readsb writes it to
/run/readsb/aircraft.json) and appends each fresh position inside the map's
area to an hourly file under /var/lib/iad-map/points/. Files older than
26 hours are deleted, so the disk holds about a day of history.

Each line is one JSON array:
  [epoch_s, hex, lat, lon, alt_ft, gs_kt, track_deg, vrate_fpm, flight, type, mlat]
alt_ft is 0 for aircraft on the ground; missing numbers are null.
"""
import json, math, os, time

AIRCRAFT_JSON = os.environ.get("IADMAP_AIRCRAFT_JSON", "/run/readsb/aircraft.json")
OUT_DIR = os.environ.get("IADMAP_POINTS_DIR", "/var/lib/iad-map/points")
INTERVAL_S = float(os.environ.get("IADMAP_INTERVAL_S", "2"))
KEEP_HOURS = 26

# The map's area: 50 statute miles around Dulles, at or below 15,000 ft.
CENTER = (38.9444, -77.4558)
RADIUS_NM = 43.45
MAX_ALT_FT = 15000
MAX_POS_AGE_S = 10  # ignore positions the receiver hasn't refreshed recently


def dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def num(v, nd=0):
    if isinstance(v, (int, float)):
        return round(v, nd) if nd else int(round(v))
    return None


def snapshot(last_seen):
    with open(AIRCRAFT_JSON) as f:
        data = json.load(f)
    now = data.get("now", time.time())
    rows = []
    for ac in data.get("aircraft", []):
        lat, lon = ac.get("lat"), ac.get("lon")
        if lat is None or lon is None or ac.get("seen_pos", 99) > MAX_POS_AGE_S:
            continue
        alt = ac.get("alt_baro", ac.get("alt_geom"))
        alt = 0 if alt == "ground" else num(alt)
        if alt is None or alt > MAX_ALT_FT:
            continue
        if dist_nm(lat, lon, *CENTER) > RADIUS_NM:
            continue
        hexid = ac.get("hex", "")
        t = int(now - ac.get("seen_pos", 0))
        key = (round(lat, 5), round(lon, 5), alt)
        if last_seen.get(hexid) == key:
            continue  # same position as last time: nothing new
        last_seen[hexid] = key
        rows.append([
            t, hexid, round(lat, 5), round(lon, 5), alt,
            num(ac.get("gs")), num(ac.get("track")),
            num(ac.get("baro_rate", ac.get("geom_rate"))),
            (ac.get("flight") or "").strip(), ac.get("t") or "",
            1 if "lat" in (ac.get("mlat") or []) else 0,
        ])
    return now, rows


def prune():
    cutoff = time.strftime("%Y%m%d%H", time.gmtime(time.time() - KEEP_HOURS * 3600))
    for name in os.listdir(OUT_DIR):
        if name.endswith(".jsonl") and name[:-6] < cutoff:
            os.remove(os.path.join(OUT_DIR, name))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    last_seen, last_prune = {}, 0
    while True:
        started = time.time()
        try:
            now, rows = snapshot(last_seen)
            if rows:
                name = time.strftime("%Y%m%d%H", time.gmtime(now)) + ".jsonl"
                with open(os.path.join(OUT_DIR, name), "a") as f:
                    for r in rows:
                        f.write(json.dumps(r, separators=(",", ":")) + "\n")
            if len(last_seen) > 5000:
                last_seen.clear()
            if started - last_prune > 600:
                prune()
                last_prune = started
        except (OSError, ValueError) as e:
            print("collector: skipped a reading:", e, flush=True)
        time.sleep(max(0.2, INTERVAL_S - (time.time() - started)))


if __name__ == "__main__":
    main()
