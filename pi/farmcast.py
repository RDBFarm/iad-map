#!/usr/bin/env python3
"""The farm forecast's numbers, kept up to date on the Pi, and a record of how
its forecasts have done. For busy.html.

Owner (2026-10-23): "an air traffic forecast for the farm ... expected plane
activity based on the expected winds", then, asked whether it would improve
as data came in, "yes, build both": the numbers recomputed from every hour the
Pi has seen, and each morning's forecast scored once the day is over.

Per settled hour, from the t/ hour files and their neighbours (like busy.py):
  - which way Dulles landed: the direction its landings (busy.movements) flew
    in the 2.5 minutes before they first got below 1,500 ft; south or north
    when 80% of the hour's landings agree, "M" (mixed) otherwise, None with
    fewer than 3;
  - its landings; the aircraft within NEAR_MI of the farm centre below
    BELOW_FT at their closest point in the hour ("near"), and at or above it;
  - KIAD's wind: the METAR nearest the middle of the hour (publish's reports,
    or the Iowa Environmental Mesonet archive, once, for an hour they missed).
model() turns those into farm-model.json: near per landing for each way of
landing, and the chance of landing south against the wind along the runways.
tools/make_farm_model.py builds the repository's copy (busy.html's fallback)
with the same functions.

Each morning (the first run after MORNING_HOUR Eastern) the Pi fetches
Open-Meteo's 7-day hourly wind for Dulles, the forecast busy.html shows, and
keeps what the page would have said for every hour (predict(), the twin of
buildForecast() in busy.html; tests/test_pages.py holds them equal). Once a
6-hour block is over and counted it is scored against the planes that came,
by how many days ahead it was forecast.
"""
import bisect, calendar, collections, csv, datetime, io, json, math, os, statistics, time, urllib.request
from zoneinfo import ZoneInfo

import busy
import farm
import tracks

NEAR_MI = 2.0
BELOW_FT = 6000
AXIS_DEG = 181            # Dulles's south landing direction, measured 09-25 - 10-23 (tools/make_farm_model.py)
U_EDGES = [0, 2, 4]       # knots of wind along the runways from the south; bins u<0, 0-2, 2-4, 4+
LOCAL = ZoneInfo("America/New_York")
MORNING_HOUR = 6          # the day's forecast is kept on the first run after this, Eastern
KEEP_FORECAST_DAYS = 35
SCORE_DAYS = 28
ARCHIVE_AFTER_S = 6 * 3600   # an hour publish's reports missed is looked up in the archive after this
BUDGET_S = 60
READY_HOURS = 14 * 24     # busy.html uses the Pi's numbers, and the Pi keeps forecasts, from this many hours on
VERSION = 1
OM_URL = ("https://api.open-meteo.com/v1/forecast?latitude=38.9445&longitude=-77.4558"
          "&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=kn&timezone=America%2FNew_York"
          "&timeformat=unixtime&forecast_days=7")
IEM_URL = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=IAD&data=drct&data=sknt"
           "&year1={a.year}&month1={a.month}&day1={a.day}&year2={b.year}&month2={b.month}&day2={b.day}"
           "&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&report_type=3&report_type=4")
KX = 69.17 * math.cos(math.radians(farm.FARM[0]))      # statute miles per degree
KY = 69.05


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "rdbf-iad-map farmcast"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def bearing(a, b):
    p1, p2, dl = math.radians(a[0]), math.radians(b[0]), math.radians(b[1] - a[1])
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                   math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360


def closest(body):
    """{hex: (miles, altitude ft)} at each aircraft's closest point to the farm in one hour file,
    along the straight line between points up to 2 minutes apart."""
    out = {}
    for hexid, (_, _, pts) in body["ac"].items():
        best, prev = None, None
        for sec, lat, lon, alt in pts:
            x, y = (lon - farm.FARM[1]) * KX, (lat - farm.FARM[0]) * KY
            if prev and sec - prev[0] <= 120:
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


def hour_record(h, load, south_degs=None):
    """[flow, landings, near, above] for hour h, from load(hour) -> parsed t/ file or None.
    south_degs, if a list, gets the direction of each landing that went south."""
    bodies = [b for b in (load(h - 3600), load(h), load(h + 3600)) if b]
    paths = busy.paths_from_files(bodies)
    ways, landings = collections.Counter(), 0
    for t, kind, hexid in busy.movements(paths):
        if kind != "a" or not h <= t < h + 3600:
            continue
        landings += 1
        seg = sorted(p for p in paths[hexid] if p[3] is not None and t - 150 <= p[0] <= t)
        if len(seg) >= 2 and busy.nm(seg[0][1:3], seg[-1][1:3]) > 0.3:
            b = bearing(seg[0][1:3], seg[-1][1:3])
            w = "N" if b >= 320 or b < 60 else "S" if 140 <= b < 240 else None
            ways[w] += 1
            if w == "S" and south_degs is not None:
                south_degs.append(b)
    n, s = ways["N"], ways["S"]
    flow = None if n + s < 3 else "N" if n >= 0.8 * (n + s) else "S" if s >= 0.8 * (n + s) else "M"
    body = load(h)
    cl = [(d, alt) for d, alt in (closest(body).values() if body else []) if d <= NEAR_MI and alt is not None]
    near = sum(1 for _, alt in cl if alt < BELOW_FT)
    return [flow, landings, near, len(cl) - near]


# ---- winds

def reports_from_wx(wx):
    """publish.fetch_weather() reports -> [(epoch, degrees or None if variable, knots)]."""
    return [(w["epoch"], None if w.get("compass") == "VRB" else w.get("drct"), w.get("sknt") or 0)
            for w in wx or [] if w.get("epoch")]


def reports_from_iem(text):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            t = calendar.timegm(datetime.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").timetuple())
            kt = float(r["sknt"])
        except (KeyError, ValueError):
            continue
        try:
            d = float(r["drct"])
        except ValueError:
            d = None                 # variable
        out.append((t, d, kt))
    return out


def iem_reports(start, end, fetch=get):
    a = datetime.datetime.fromtimestamp(start - 86400, datetime.timezone.utc)
    b = datetime.datetime.fromtimestamp(end + 86400, datetime.timezone.utc)
    return reports_from_iem(fetch(IEM_URL.format(a=a, b=b), 60))


def winds_for(hours, reports):
    """{hour: [degrees or None, knots]} from the report nearest each hour's middle, within 30 minutes."""
    reports = sorted(reports)
    times = [r[0] for r in reports]
    out = {}
    for h in hours:
        mid = h + 1800
        i = bisect.bisect_left(times, mid)
        near = [reports[j] for j in (i - 1, i) if 0 <= j < len(reports)]
        best = min(near, key=lambda r: abs(r[0] - mid), default=None)
        if best and abs(best[0] - mid) <= 1800:
            out[h] = [best[1], best[2]]
    return out


def along(deg, kt):
    """Knots of wind blowing up the runways from the south; calm or variable is 0."""
    return 0.0 if deg is None or kt < 0.5 else kt * math.cos(math.radians(deg - AXIS_DEG))


def u_bin(u):
    return sum(u >= e for e in U_EDGES)


# ---- the numbers

def local(t):
    return datetime.datetime.fromtimestamp(t, LOCAL)


def corr(xs, ys):
    if len(xs) < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sx * sy), 2) if sx and sy else None


def model(records, winds, source):
    """records {hour: [flow, landings, near, above]}, winds {hour: [degrees, knots]} -> farm-model dict."""
    hs = sorted(records)
    share, above = {}, {}
    for f in ("S", "N"):
        sel = [records[h] for h in hs if records[h][0] == f]
        share[f] = round(sum(r[2] for r in sel) / max(1, sum(r[1] for r in sel)), 3)
        above[f] = round(sum(r[3] for r in sel) / max(1, len(sel)), 1)
    tally = [collections.Counter() for _ in range(len(U_EDGES) + 1)]
    for h in hs:
        if h in winds and records[h][0] in ("S", "N"):
            tally[u_bin(along(*winds[h]))][records[h][0]] += 1
    p_south = []
    for i, c in enumerate(tally):
        n = c["S"] + c["N"]
        p_south.append({"from_kt": U_EDGES[i - 1] if i else None, "to_kt": U_EDGES[i] if i < len(U_EDGES) else None,
                        "p": round(c["S"] / n, 2) if n else None, "hours": n})
    flows = collections.Counter(records[h][0] or "unclear" for h in hs)
    return {
        "version": 1, "near_mi": NEAR_MI, "below_ft": BELOW_FT, "axis_deg": AXIS_DEG,
        "share": share, "p_south": p_south, "above_per_hour": above,
        "hours": {"counted": len(hs), "south": flows["S"], "north": flows["N"], "mixed": flows["M"],
                  "unclear": flows["unclear"], "with_wind": sum(1 for h in hs if h in winds)},
        "ready": len(hs) >= READY_HOURS,
        "first": local(hs[0]).date().isoformat() if hs else None,
        "last": local(hs[-1]).date().isoformat() if hs else None,
        "check": check(records, winds, share, p_south), "source": source,
    }


def check(records, winds, share, p_south):
    """The rule on the same hours with the winds that blew (in-sample, not forecasts)."""
    usual = collections.defaultdict(list)
    for h, r in records.items():
        lt = local(h)
        usual[(lt.weekday(), lt.hour)].append(r[1])
    pred, blocks = {}, collections.defaultdict(lambda: [0.0, 0, 0])
    for h, r in records.items():
        if h not in winds:
            continue
        lt = local(h)
        p = p_south[u_bin(along(*winds[h]))]["p"] or 0
        pred[h] = statistics.mean(usual[(lt.weekday(), lt.hour)]) * (p * share["S"] + (1 - p) * share["N"])
        k = blocks[(lt.date(), lt.hour // 6)]
        k[0] += pred[h]; k[1] += r[2]; k[2] += 1
    full = [k for k in blocks.values() if k[2] == 6]
    quiet = [k[1] for k in full if k[0] < 10]
    busy_ = [k[1] for k in full if k[0] >= 40]
    return {"note": "the rule on the same weeks with the winds that blew, not forecasts",
            "hourly_corr": corr([pred[h] for h in pred], [records[h][2] for h in pred]),
            "block_corr": corr([k[0] for k in full], [k[1] for k in full]), "blocks": len(full),
            "quiet_blocks": {"n": len(quiet), "median": statistics.median(quiet), "max": max(quiet)} if quiet else None,
            "busy_blocks": {"n": len(busy_), "median": statistics.median(busy_), "min": min(busy_)} if busy_ else None}


# ---- the forecast: the twin of busy.html's usualLandings(), pSouth() and buildForecast()

def usual_landings(busy_data):
    """Dulles's average landings per local hour by weekday (Mon = 0) over the last 28 whole days of
    busy.json; a weekday with none takes every day's average. None without any whole day."""
    sums, n = [[0.0] * 24 for _ in range(7)], [0] * 7
    for d in busy_data["complete_days"][-28:]:
        c = busy_data["days"].get(d)
        if not c:
            continue
        w = datetime.date.fromisoformat(d).weekday()
        n[w] += 1
        for q in range(96):
            sums[w][q >> 2] += c["a"][q]
    total = sum(n)
    if not total:
        return None
    every = [sum(s[h] for s in sums) / total for h in range(24)]
    return [[x / n[w] for x in s] if n[w] else every for w, s in enumerate(sums)]


def p_south(m, deg, kt):
    u = along(deg, kt)
    b = next((x for x in m["p_south"] if (x["from_kt"] is None or u >= x["from_kt"])
              and (x["to_kt"] is None or u < x["to_kt"])), None)
    return b["p"] if b and b["p"] is not None else 0.5


def predict(m, busy_data, om, now):
    """{hour: [degrees, knots, planes expected]} for every forecast hour not yet over."""
    usual = usual_landings(busy_data)
    hr, out = om["hourly"], {}
    if not usual:
        return out
    for t, deg, kt in zip(hr["time"], hr["wind_direction_10m"], hr["wind_speed_10m"]):
        if t + 3600 <= now or deg is None or kt is None:
            continue
        lt, p = local(t), p_south(m, deg, kt)
        out[t] = [deg, kt, usual[lt.weekday()][lt.hour] * (p * m["share"]["S"] + (1 - p) * m["share"]["N"])]
    return out


def block_hours(day, b):
    """Epoch hours in 6-hour block b (0-3) of a local date: 5 to 7 of them on clock-change days."""
    start = datetime.datetime(day.year, day.month, day.day, b * 6, tzinfo=LOCAL)
    end = (datetime.datetime(day.year, day.month, day.day, b * 6 + 6, tzinfo=LOCAL) if b < 3 else
           datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(), LOCAL))
    return list(range(int(start.timestamp()), int(end.timestamp()), 3600))


def score(forecasts, records, now, days=SCORE_DAYS):
    """How the kept forecasts did, by days ahead: each whole 6-hour block that is over and counted,
    planes forecast against planes that came within NEAR_MI below BELOW_FT."""
    since = local(now).date() - datetime.timedelta(days=days)
    pairs = collections.defaultdict(list)
    kept = 0
    for issued, f in forecasts.items():
        issued_day = datetime.date.fromisoformat(issued)
        if issued_day < since:
            continue
        kept += 1
        hours = {int(h): v for h, v in f["hours"].items()}
        for day, b in {(local(h).date(), local(h).hour // 6) for h in hours}:
            want = block_hours(day, b)
            if all(h in hours and h in records for h in want):
                pairs[(day - issued_day).days].append((sum(hours[h][2] for h in want), sum(records[h][2] for h in want)))
    return {"days": days, "forecasts": kept, "by_lead": [
        {"lead": lead, "blocks": len(pairs[lead]),
         "median_err": round(statistics.median(abs(p - a) for p, a in pairs[lead]), 1),
         "median_actual": statistics.median(a for _, a in pairs[lead]),
         "median_forecast": round(statistics.median(p for p, _ in pairs[lead]), 1)}
        for lead in sorted(pairs)]}


# ---- on the Pi, from publish.py

def update(work_dir, now, state_dir, final, wx, fetch=get, budget_s=BUDGET_S):
    """Count new settled hours, fill in their winds, keep today's forecast once it is morning,
    and write farm-model.json (with the forecasts' record) when it changed. Returns True if written."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "state.json")
    try:
        with open(path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    if st.get("version") != VERSION:
        st = {"version": VERSION, "hours": {}, "wind": {}, "forecasts": {}, "no_wind": []}
    records = {int(h): r for h, r in st["hours"].items()}
    winds = {int(h): w for h, w in st["wind"].items()}

    hours = sorted(p[1] for p in map(tracks.parse_name, final) if p and p[0] == "t")
    have, started, cache = set(hours), time.monotonic(), {}

    def load(h):
        if h not in cache:
            try:
                with open(os.path.join(work_dir, tracks.hour_name("t", h))) as f:
                    cache[h] = json.load(f)
            except (OSError, ValueError):
                cache[h] = None
        return cache[h]

    for h in hours:
        if h in records or (h + 3600 not in have and now < h + 3 * 3600):
            continue
        if time.monotonic() - started > budget_s:
            break
        records[h] = hour_record(h, load)
        for old in [k for k in cache if k < h - 3600]:
            del cache[old]

    winds.update({h: w for h, w in winds_for([h for h in records if h not in winds], reports_from_wx(wx)).items()})
    # each hour still without a wind is looked up in the archive once; one it has no report for stays without
    none = set(st.get("no_wind", []))
    missing = [h for h in records if h not in winds and h not in none and h < now - ARCHIVE_AFTER_S]
    if missing:
        try:
            got = winds_for(missing, iem_reports(min(missing), max(missing), fetch))
            winds.update(got)
            none |= set(missing) - set(got)
        except Exception as e:
            print("farmcast: no archive winds this time:", e, flush=True)
    st["no_wind"] = sorted(none)

    m = model(records, winds, "pi/farmcast.py on the farm's receiver: its recorded paths and KIAD METARs")
    lt = local(now)
    today = lt.date().isoformat()
    if lt.hour >= MORNING_HOUR and today not in st["forecasts"] and m["ready"]:
        try:
            with open(os.path.join(work_dir, "busy.json")) as f:
                busy_data = json.load(f)
            hrs = predict(m, busy_data, json.loads(fetch(OM_URL, 30)), now)
            if hrs:
                st["forecasts"][today] = {"issued": int(now), "hours": {str(h): [d, k, round(e, 3)] for h, (d, k, e) in hrs.items()}}
        except Exception as e:
            print("farmcast: no forecast kept this time:", e, flush=True)
    oldest = (lt.date() - datetime.timedelta(days=KEEP_FORECAST_DAYS)).isoformat()
    st["forecasts"] = {d: f for d, f in st["forecasts"].items() if d >= oldest}
    m["score"] = score(st["forecasts"], records, now)

    st["hours"] = {str(h): r for h, r in sorted(records.items())}
    st["wind"] = {str(h): w for h, w in sorted(winds.items())}
    with open(path + ".tmp", "w") as f:
        json.dump(st, f, separators=(",", ":"))
    os.replace(path + ".tmp", path)
    if not records:
        return False
    out, text = os.path.join(work_dir, "farm-model.json"), json.dumps(m, indent=1) + "\n"
    try:
        with open(out) as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    with open(out + ".tmp", "w") as f:
        f.write(text)
    os.replace(out + ".tmp", out)
    return True
