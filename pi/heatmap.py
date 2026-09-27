#!/usr/bin/env python3
"""The history heat map: every aircraft path near Dulles, summed, by the
wind at the time, by airport zone, by aircraft size, arrivals and take-offs.

Owner's requests, 2026-09-27, in order:
  - "a heatmap over the history of the database ... the resulting lines
    shouldn't get broader, just brighter, and with the absolute size of the
    planes weighting the brightness as well. So an approach that has 100
    private planes would have the same brightness as 10 commercial planes
    (in the broadest sense possible)."
  - "type a degree or use a pointer to move the wind position. So we can see
    what the heat map looks like when the wind is blowing at any one
    direction."
  - "give me just airports to select."
  - plane size in two colours, "orange and blue", and "departures added or
    selectable".

How it is built:
  - A fixed grid of cells about 110 m square over the map's area. Each
    aircraft's path is drawn through the cells it crosses, and each cell
    counts an aircraft once per hour however many positions it sent there,
    so lines keep one cell's width; more traffic only adds weight.
  - Weight by size, ICAO wake turbulence category (aircraft_types.json
    "wtc"): small = light (up to ~7 t: private planes, most helicopters) 1;
    big = medium and heavy (airliners, regional and business jets,
    widebodies) 10. An unknown type counts as small.
  - Positions within 50 miles of Dulles at or below 15,000 ft, from the log
    (publish.heat_points_for_hour). "Arrivals" are exactly the live map's
    positions (take-offs dropped by its rules); "take-offs" are the airborne
    positions those rules drop (departure climbs and fast climb-outs).
  - Wind: KIAD's report nearest the middle of each hour (the direction it
    blows FROM, to 10 degrees), kept in state.json as it arrives, or "calm"
    (calm or variable). An hour waits for its wind; one still without it
    WIND_WAIT_S after it ended counts under all winds only.
  - Airport zones: the live map's (publish.classify_airport; 0-9, 7 = en
    route), of the position that first reached the cell.
  - Every combination is its own grid, so any selection adds up exactly:
    heat/g/<scope>/<wind>/<class>.f32, scope "all" or a zone id, wind "all"
    or a wind key, class a/d (arrival/take-off) + s/b (small/big).
  - The grids live on the drive, grow forever, and are edited in place
    (memory-mapped, one at a time). Images are rewritten at most once a day,
    only those whose grids changed. A rebuild (a new version of this file's
    layout) runs MAX_HOURS_PER_RUN hours per publish so the live map is never
    held up; the old images stay until the new ones are complete.

Images: heat/v2/<scope>/<wind>/<a|d>.png, RGB, one pixel per cell, north up:
red = small aircraft, green = big aircraft (blue unused), each
round(255 * ln(1 + w) / ln(1 + max)) with its max in heat.json.
"""
import itertools, json, math, mmap, os, shutil, struct, time, zlib

VERSION = 2
# The area: the map's 50-mile circle around Dulles (publish.CENTER, RADIUS_NM)
LAT_MIN, LAT_MAX = 38.215, 39.675
LON_MIN, LON_MAX = -78.392, -76.520
DLAT = 0.001            # ~111 m
DLON = 0.00128          # ~111 m at this latitude
ROWS = round((LAT_MAX - LAT_MIN) / DLAT)
COLS = round((LON_MAX - LON_MIN) / DLON)
WEIGHT = {"L": 1.0, "M": 10.0, "H": 10.0, "J": 10.0}
UNKNOWN_WEIGHT = 1.0
MAX_GAP_S = 30            # a longer silence breaks the line (no drawing across it)
REPUBLISH_S = 20 * 3600   # images at most about once a day
WIND_WAIT_S = 30 * 3600   # an hour waits this long for its wind report
SETTLE_S = 600            # an hour is added this long after it ends
MAX_HOURS_PER_RUN = 12    # a rebuild is spread over runs
CLASSES = ("as", "ab", "ds", "db")


def _wtc_table():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "aircraft_types.json")) as f:
            return json.load(f).get("wtc", {})
    except (OSError, ValueError):
        return {}


def weight(actype, _wtc={}):
    if not _wtc:
        _wtc.update(_wtc_table() or {"": ""})
    return WEIGHT.get(_wtc.get(actype or ""), UNKNOWN_WEIGHT)


def cell(lat, lon):
    """Fractional (row, col) of a position; row 0 is the north edge."""
    return (LAT_MAX - lat) / DLAT, (lon - LON_MIN) / DLON


def line_cells(a, b):
    """Cells a straight segment passes through (sampled at least twice per cell)."""
    (r1, c1), (r2, c2) = cell(*a), cell(*b)
    n = max(1, int(math.ceil(2 * max(abs(r2 - r1), abs(c2 - c1)))))
    out = set()
    for i in range(n + 1):
        f = i / n
        r, c = int(r1 + (r2 - r1) * f), int(c1 + (c2 - c1) * f)
        if 0 <= r < ROWS and 0 <= c < COLS:
            out.add(r * COLS + c)
    return out


def hour_cells(points):
    """points: (t, hex, lat, lon, type, zone, movement 'a'|'d') for one hour
    -> ({(zone, class): {cell: weight}}, aircraft). Each aircraft adds its
    weight once to every cell its path crosses; the cell goes to the zone and
    movement of the position that first reached it, the class to the
    aircraft's size, so all the parts add up to the total."""
    by_hex = {}
    for t, hexid, lat, lon, actype, zone, mov in points:
        by_hex.setdefault(hexid, []).append((t, lat, lon, actype, zone, mov))
    out = {}
    for pts in by_hex.values():
        pts.sort()
        cells, prev, actype = {}, None, ""
        for t, lat, lon, ty, zone, mov in pts:
            actype = ty or actype
            seg = line_cells((prev[1], prev[2]) if prev and t - prev[0] <= MAX_GAP_S else (lat, lon), (lat, lon))
            for k in seg:
                cells.setdefault(k, (zone, mov))
            prev = (t, lat, lon)
        w = weight(actype)
        size = "b" if w > 1 else "s"
        for k, (zone, mov) in cells.items():
            d = out.setdefault((zone, mov + size), {})
            d[k] = d.get(k, 0.0) + w
    return out, len(by_hex)


def wind_key(wind):
    """'calm' or a direction (degrees, from) -> its wind key."""
    if wind == "calm":
        return "calm"
    return f"{int(round(float(wind) / 10.0)) % 36 * 10:03d}"


def winds_by_hour(wx):
    """{hour: 'calm' or degrees} from publish.fetch_weather() reports: for
    each hour, the report nearest its middle, within an hour of it."""
    best = {}
    for w in wx or []:
        e = w.get("epoch")
        if not e:
            continue
        for hour in (e - e % 3600, e - e % 3600 - 3600, e - e % 3600 + 3600):
            d = abs(e - (hour + 1800))
            if d <= 3600 and (hour not in best or d < best[hour][0]):
                calm = w.get("compass") == "VRB" or not w.get("sknt")
                best[hour] = (d, "calm" if calm else w.get("drct"))
    return {h: v for h, (d, v) in best.items() if v is not None}


def _chunk(kind, body):
    c = struct.pack(">I", len(body)) + kind + body
    return c + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(rows, cols, data, channels):
    """8-bit PNG, greyscale (1 channel) or RGB (3), from rows*cols*channels bytes."""
    data, w = bytes(data), cols * channels
    raw = b"".join(b"\x00" + data[r * w:(r + 1) * w] for r in range(rows))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", cols, rows, 8, 0 if channels == 1 else 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def log_bytes(acc):
    """(bytearray of log-scaled 1..255 per lit cell, largest weight). Only
    lit cells are visited (most grids are sparse)."""
    lit = list(itertools.compress(range(len(acc)), acc))
    top = max((acc[i] for i in lit), default=0.0)
    out = bytearray(len(acc))
    if top > 0:
        scale = 255 / math.log1p(top)
        for i in lit:
            v = acc[i]
            if v > 0:
                out[i] = max(1, min(255, round(math.log1p(v) * scale)))
    return out, top


class Grid:
    """A float32 grid file, edited in place (memory-mapped)."""
    def __init__(self, path):
        self.path = path
        size = 4 * ROWS * COLS
        if not os.path.exists(path) or os.path.getsize(path) != size:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.truncate(size)               # a sparse file: no disk used until written
        self.f = open(path, "r+b")
        self.mm = mmap.mmap(self.f.fileno(), size)
        self.acc = memoryview(self.mm).cast("f")

    def add(self, cells):
        acc = self.acc
        for k, w in cells.items():
            acc[k] += w

    def close(self):
        self.mm.flush()
        self.acc.release()
        self.mm.close()
        self.f.close()


def add_to(path, cells):
    g = Grid(path)
    try:
        g.add(cells)
    finally:
        g.close()


class State:
    def __init__(self, state_dir):
        self.dir = state_dir
        self.path = os.path.join(state_dir, "state.json")
        os.makedirs(state_dir, exist_ok=True)
        try:
            with open(self.path) as f:
                self.s = json.load(f)
        except (OSError, ValueError):
            self.s = {}
        if self.s.get("version") != VERSION:
            # a new layout: rebuild every hour from the log, keeping the winds
            winds = self.s.get("wind", {})
            for old in ("acc.f32", "wind", "ap", "g"):
                p = os.path.join(state_dir, old)
                if os.path.isdir(p):
                    shutil.rmtree(p)
                elif os.path.exists(p):
                    os.remove(p)
            self.s = {"version": VERSION, "wind": winds, "wind_backfilled": bool(winds)}
        for k, v in (("hours", []), ("aircraft_hours", 0), ("published", 0), ("wind", {}),
                     ("hours_by_wind", {}), ("images", {}), ("dirty", []), ("no_wind_hours", 0)):
            self.s.setdefault(k, v)
        self.done = set(self.s["hours"])

    def grid(self, scope, wind, cls):
        return os.path.join(self.dir, "g", str(scope), wind, cls + ".f32")

    def save(self):
        self.s["hours"] = sorted(self.done)
        with open(self.path + ".tmp", "w") as f:
            json.dump(self.s, f)
        os.replace(self.path + ".tmp", self.path)


def update(work_dir, db_hours, now, state_dir, db_start=None, wx=None, wx_fetch=None, force=False):
    """Add every settled hour of the log not added yet (at most
    MAX_HOURS_PER_RUN per call), into every grid it belongs to.
    db_hours(hour) -> points as hour_cells() takes them. wx: this run's
    weather reports; wx_fetch(hours) -> older ones, used once. Writes the
    images and heat.json into work_dir when there is nothing left to add and
    a day has passed since the last time (or they are from an older layout).
    Returns True if it wrote them."""
    st = State(state_dir)
    wind = st.s["wind"]
    for h, v in winds_by_hour(wx).items():
        wind.setdefault(str(h), v)
    if db_start is None:
        st.save()
        return False
    last = int(now) - SETTLE_S - 3600
    last -= last % 3600
    todo = [h for h in range(db_start - db_start % 3600, last + 1, 3600) if h not in st.done]
    if wx_fetch and todo and not st.s.get("wind_backfilled") and any(str(h) not in wind for h in todo):
        oldest = min(h for h in todo if str(h) not in wind)
        for h, v in winds_by_hour(wx_fetch(min(168, math.ceil((now - oldest) / 3600) + 2))).items():
            wind.setdefault(str(h), v)
        st.s["wind_backfilled"] = True
    ready = []
    for h in todo:
        w = wind.get(str(h))
        if w is None and now - (h + 3600) < WIND_WAIT_S:
            continue                           # its wind may still come
        ready.append((h, None if w is None else wind_key(w)))
    backlog = len(ready) > MAX_HOURS_PER_RUN
    for h, key in ready[:MAX_HOURS_PER_RUN]:
        parts, n = hour_cells(db_hours(h))
        winds = ["all", key] if key else ["all"]
        overall = {}
        for (zone, cls), cells in parts.items():
            o = overall.setdefault(cls, {})
            for k, w in cells.items():
                o[k] = o.get(k, 0.0) + w
            for wk in winds:
                add_to(st.grid(zone, wk, cls), cells)
                img = f"{zone}/{wk}/{cls[0]}"
                if img not in st.s["dirty"]:
                    st.s["dirty"].append(img)
        for cls, cells in overall.items():
            for wk in winds:
                add_to(st.grid("all", wk, cls), cells)
                img = f"all/{wk}/{cls[0]}"
                if img not in st.s["dirty"]:
                    st.s["dirty"].append(img)
        st.s["aircraft_hours"] += n
        if key:
            st.s["hours_by_wind"][key] = st.s["hours_by_wind"].get(key, 0) + 1
        else:
            st.s["no_wind_hours"] += 1
        st.done.add(h)
        st.save()                              # after each hour: a crash loses at most the hour in hand
    meta_path = os.path.join(work_dir, "heat.json")
    try:
        with open(meta_path) as f:
            old_version = json.load(f).get("version")
    except (OSError, ValueError):
        old_version = None
    due = force or old_version != VERSION or now - st.s["published"] >= REPUBLISH_S
    if backlog or not due or not st.done:
        return False
    if old_version != VERSION:
        # the first images of this layout replace the old ones
        if os.path.exists(os.path.join(work_dir, "heat.png")):
            os.remove(os.path.join(work_dir, "heat.png"))
        if os.path.isdir(os.path.join(work_dir, "heat")):
            shutil.rmtree(os.path.join(work_dir, "heat"))
    for img in st.s["dirty"]:
        scope, wk, mov = img.split("/")
        chans, tops = [], []
        for size in "sb":
            path = st.grid(scope, wk, mov + size)
            if os.path.exists(path):
                g = Grid(path)
                b, top = log_bytes(g.acc)
                g.close()
            else:
                b, top = bytearray(ROWS * COLS), 0.0
            chans.append(b)
            tops.append(round(top, 1))
        rgb = bytearray(3 * ROWS * COLS)
        rgb[0::3], rgb[1::3] = chans
        out = os.path.join(work_dir, "heat", "v2", scope, wk, mov + ".png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + ".tmp", "wb") as f:
            f.write(png(ROWS, COLS, rgb, 3))
        os.replace(out + ".tmp", out)
        st.s["images"][img] = {"max": tops, "v": int(now)}
    st.s["dirty"] = []
    meta = {
        "version": VERSION,
        "bounds": [[LAT_MIN, LON_MIN], [LAT_MAX, LON_MAX]], "rows": ROWS, "cols": COLS, "cell_m": 111,
        "first_hour": min(st.done), "last_hour": max(st.done),
        "hours": len(st.done), "aircraft_hours": st.s["aircraft_hours"],
        "no_wind_hours": st.s["no_wind_hours"],
        "wind": {k: {"hours": n} for k, n in sorted(st.s["hours_by_wind"].items())},
        "images": st.s["images"],
        "weights": {"small": WEIGHT["L"], "big": WEIGHT["M"], "unknown": UNKNOWN_WEIGHT},
        "note": "Image heat/v2/<scope>/<wind>/<a|d>.png: scope 'all' or airport zone (0 KIAD, 1 KDCA, "
                "2 KBWI, 3 KJYO, 4 KGAI, 5 KHEF, 6 KRMN, 7 en route, 8 KADW, 9 KNYG); wind 'all', "
                "000..350 (from) or calm; a = arrivals (the live map's positions), d = take-offs. "
                "Red channel small aircraft, green big; pixel = round(255 ln(1+w) / ln(1+max)), "
                "max per channel in images[key].max.",
        "built": int(now),
    }
    with open(meta_path + ".tmp", "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    os.replace(meta_path + ".tmp", meta_path)
    st.s["published"] = int(now)
    st.save()
    return True
