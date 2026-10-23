#!/usr/bin/env python3
"""Build farm-model.json, the numbers behind busy.html's forecast for the farm.

Owner (2026-10-23): "an air traffic forecast for the farm ... expected plane
activity based on the expected winds, an hourly as well as blocks of time in
the coming 7 days".

Measured from the t/ hour files on live-data (the last 30 days) and KIAD's
hourly wind reports (METARs, from the Iowa Environmental Mesonet archive):
  - which way Dulles landed each hour: the direction its arrivals flew in the
    2.5 minutes before they first got below 1,500 ft (busy.py's landings);
    south if 80% of an hour's landings went south, north if 80% went north;
  - how many aircraft came within NEAR_MI of the farm centre below BELOW_FT
    (closest point of each aircraft's path in each hour);
  - those per Dulles landing, by direction ("share"); the ones at or above
    BELOW_FT per hour ("above_per_hour", about the same either way); and the chance of
    landing south against the wind along the runways (wind speed x cos of the
    angle between where it blows from and the south landing direction).
The page multiplies Dulles's usual landings for each hour (busy.json) by
p * share[S] + (1 - p) * share[N], p from the forecast wind.

    git fetch --depth 1 origin +live-data:refs/remotes/origin/live-data
    python3 tools/make_farm_model.py origin/live-data
"""
import calendar, collections, csv, datetime, io, json, math, os, statistics, subprocess, sys, urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pi"))
import busy, farm, tracks  # noqa: E402

NEAR_MI = 2.0
BELOW_FT = 6000
U_EDGES = [0, 2, 4]          # knots along the runways; bins u<0, 0-2, 2-4, 4+
LOCAL = ZoneInfo("America/New_York")
IEM = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=IAD&data=drct&data=sknt"
       "&year1={a.year}&month1={a.month}&day1={a.day}&year2={b.year}&month2={b.month}&day2={b.day}"
       "&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&report_type=3&report_type=4")

REF = sys.argv[1] if len(sys.argv) > 1 else "origin/live-data"
git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT, check=True).stdout
names = {n for n in git("ls-tree", "-r", "--name-only", REF).split() if n.startswith("t/") and tracks.parse_name(n)}
hours = sorted(tracks.parse_name(n)[1] for n in names)[:-1]     # the newest hour isn't settled
KX = 69.17 * math.cos(math.radians(farm.FARM[0]))                 # statute miles per degree
KY = 69.05


def bearing(a, b):
    p1, p2, dl = math.radians(a[0]), math.radians(b[0]), math.radians(b[1] - a[1])
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                   math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360


def closest(body):
    """{hex: (miles, altitude ft)} at each aircraft's closest point to the farm in one hour file."""
    out = {}
    for hexid, (_, _, pts) in body["ac"].items():
        best, prev = None, None
        for sec, lat, lon, alt in pts:
            x, y = (lon - farm.FARM[1]) * KX, (lat - farm.FARM[0]) * KY
            if prev and sec - prev[0] <= 120:            # along the straight line between points
                _, x0, y0, a0 = prev
                dx, dy = x - x0, y - y0
                f = max(0.0, min(1.0, -(x0 * dx + y0 * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
                cand = (math.hypot(x0 + f * dx, y0 + f * dy), None if a0 is None or alt is None else a0 + f * (alt - a0))
            else:
                cand = (math.hypot(x, y), alt)
            if best is None or cand[0] < best[0]:
                best = cand
            prev = (sec, x, y, alt)
        if best:
            out[hexid] = best
    return out


cache = {}


def load(h):
    if h not in cache:
        n = tracks.hour_name("t", h)
        cache[h] = json.loads(git("show", f"{REF}:{n}")) if n in names else None
    return cache[h]


per_hour = {}
for h in hours:
    bodies = [b for b in (load(h - 3600), load(h), load(h + 3600)) if b]
    paths = busy.paths_from_files(bodies)
    ways, landings = [], 0
    for t, kind, hexid in busy.movements(paths):
        if kind != "a" or not h <= t < h + 3600:
            continue
        landings += 1
        seg = sorted(p for p in paths[hexid] if p[3] is not None and t - 150 <= p[0] <= t)
        if len(seg) >= 2 and busy.nm(seg[0][1:3], seg[-1][1:3]) > 0.3:
            b = bearing(seg[0][1:3], seg[-1][1:3])
            ways.append(("N" if b >= 320 or b < 60 else "S" if 140 <= b < 240 else None, b))
    c = collections.Counter(w for w, _ in ways)
    flow = None
    if c["N"] + c["S"] >= 3:
        flow = "N" if c["N"] >= 0.8 * (c["N"] + c["S"]) else "S" if c["S"] >= 0.8 * (c["N"] + c["S"]) else "mixed"
    cl = [(d, alt) for d, alt in closest(load(h)).values() if d <= NEAR_MI and alt is not None]
    near = sum(1 for d, alt in cl if alt < BELOW_FT)
    per_hour[h] = {"flow": flow, "landings": landings, "near": near, "above": len(cl) - near,
                   "south_bearings": [b for w, b in ways if w == "S"]}
    for old in [k for k in cache if k < h - 3600]:
        del cache[old]

# KIAD winds: the report nearest the middle of each hour, within the hour
a = datetime.datetime.fromtimestamp(hours[0] - 86400, datetime.timezone.utc)
b = datetime.datetime.fromtimestamp(hours[-1] + 86400, datetime.timezone.utc)
req = urllib.request.Request(IEM.format(a=a, b=b), headers={"User-Agent": "iad-map tools/make_farm_model.py"})
reports = []
for r in csv.DictReader(io.StringIO(urllib.request.urlopen(req, timeout=60).read().decode())):
    try:
        reports.append((calendar.timegm(datetime.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").timetuple()),
                        float(r["drct"]), float(r["sknt"])))
    except ValueError:
        pass
reports.sort()


def wind(h):
    best = min(reports, key=lambda r: abs(r[0] - h - 1800), default=None)
    return best[1:] if best and abs(best[0] - h - 1800) <= 1800 else None


axis = round(statistics.median(b for v in per_hour.values() for b in v["south_bearings"]))


def along(d, s):
    return 0.0 if s < 0.5 else s * math.cos(math.radians(d - axis))


def u_bin(u):
    return sum(u >= e for e in U_EDGES)


share, above = {}, {}
for f in ("S", "N"):
    hs = [v for v in per_hour.values() if v["flow"] == f]
    share[f] = round(sum(v["near"] for v in hs) / max(1, sum(v["landings"] for v in hs)), 3)
    above[f] = round(sum(v["above"] for v in hs) / max(1, len(hs)), 1)
tally = [collections.Counter() for _ in range(len(U_EDGES) + 1)]
for h, v in per_hour.items():
    w = wind(h)
    if w and v["flow"] in ("S", "N"):
        tally[u_bin(along(*w))][v["flow"]] += 1
p_south = []
for i, c in enumerate(tally):
    n = c["S"] + c["N"]
    p_south.append({"from_kt": U_EDGES[i - 1] if i else None, "to_kt": U_EDGES[i] if i < len(U_EDGES) else None,
                    "p": round(c["S"] / n, 2) if n else None, "hours": n})

# the same rule on the same weeks, with the winds that blew (not forecasts)
usual = collections.defaultdict(list)
for h, v in per_hour.items():
    lt = datetime.datetime.fromtimestamp(h, LOCAL)
    usual[(lt.weekday(), lt.hour)].append(v["landings"])
pred, blocks = {}, collections.defaultdict(lambda: [0.0, 0, 0])
for h, v in per_hour.items():
    w = wind(h)
    if not w:
        continue
    lt = datetime.datetime.fromtimestamp(h, LOCAL)
    p = p_south[u_bin(along(*w))]["p"] or 0
    pred[h] = statistics.mean(usual[(lt.weekday(), lt.hour)]) * (p * share["S"] + (1 - p) * share["N"])
    k = blocks[(lt.date(), lt.hour // 6)]
    k[0] += pred[h]; k[1] += v["near"]; k[2] += 1


def corr(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))


full = [k for k in blocks.values() if k[2] == 6]
quiet = [k[1] for k in full if k[0] < 10]
busy_ = [k[1] for k in full if k[0] >= 40]
fmt = lambda t: datetime.datetime.fromtimestamp(t, LOCAL).strftime("%Y-%m-%d")
flows = collections.Counter(v["flow"] or "unclear" for v in per_hour.values())
model = {
    "version": 1, "near_mi": NEAR_MI, "below_ft": BELOW_FT, "axis_deg": axis,
    "share": share, "p_south": p_south, "above_per_hour": above,
    "hours": {"counted": len(per_hour), "south": flows["S"], "north": flows["N"], "mixed": flows["mixed"],
              "unclear": flows["unclear"], "with_wind": len(pred)},
    "first": fmt(hours[0]), "last": fmt(hours[-1]),
    "check": {"note": "the rule on the same weeks with the winds that blew, not forecasts",
              "hourly_corr": round(corr([pred[h] for h in pred], [per_hour[h]["near"] for h in pred]), 2),
              "block_corr": round(corr([k[0] for k in full], [k[1] for k in full]), 2), "blocks": len(full),
              "quiet_blocks": {"n": len(quiet), "median": statistics.median(quiet), "max": max(quiet)} if quiet else None,
              "busy_blocks": {"n": len(busy_), "median": statistics.median(busy_), "min": min(busy_)} if busy_ else None},
    "source": "tools/make_farm_model.py: the farm receiver's recorded paths (live-data t/ files) and KIAD "
              "METARs from the Iowa Environmental Mesonet",
}
with open(os.path.join(ROOT, "farm-model.json"), "w") as f:
    json.dump(model, f, indent=1)
    f.write("\n")
print(json.dumps(model, indent=1))
