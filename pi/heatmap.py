#!/usr/bin/env python3
"""The history heat map: every aircraft path the map has shown, summed,
overall and by the wind at Dulles at the time.

Owner's requests, 2026-09-27: a heat map over the history of the database
where "the resulting lines shouldn't get broader, just brighter, and with
the absolute size of the planes weighting the brightness as well. So an
approach that has 100 private planes would have the same brightness as 10
commercial planes (in the broadest sense possible)"; then, "I want to be
able to type a degree or use a pointer to move the wind position. So we can
see what the heat map looks like when the wind is blowing at any one
direction."

  - A fixed grid of cells about 110 m square over the map's area. Each
    aircraft's path is drawn through the cells it crosses, and each cell
    counts an aircraft once per hour however many positions it sent there,
    so a slow aircraft or a 2-second log does not brighten a cell more than
    a fast one. Lines keep one cell's width; more traffic only adds weight.
  - Weight by size, ICAO wake turbulence category (aircraft_types.json
    "wtc"): light (up to ~7 t: private planes, most helicopters) 1; medium
    and heavy (airliners, regional and business jets, widebodies) 10. An
    unknown type counts 1: most are private planes.
  - The same positions as the live map: within 50 miles of Dulles, at or
    below 15,000 ft, departures dropped. Frozen hour files (h/) are read
    back; hours from before the first one kept are built once from the log.
  - Wind: KIAD's report nearest the middle of each hour (the direction it
    blows FROM, to 10 degrees, as reports give it), kept in state.json as it
    arrives. Each hour also goes into that direction's grid, or "calm"
    (calm or variable). An hour waits for its wind; one still without it
    WIND_WAIT_S after it ended goes into the overall grid only.
  - The grids are kept on the drive (heat/, float32) and grow forever,
    beyond the 30 days of hour files. The images are rewritten at most once
    a day, and only the wind grids that got new hours, so the upload is
    small.

Images: 8-bit greyscale PNG, one pixel per cell, north up; 0 = nothing,
else round(255 * log(1 + w) / log(1 + max)), max per image in heat.json.
heat.png is all winds; heat/<dir>.png (000..350) and heat/calm.png by wind.
"""
import array, json, math, os, struct, time, zlib

# The area: the map's 50-mile circle around Dulles (publish.CENTER, RADIUS_NM)
LAT_MIN, LAT_MAX = 38.215, 39.675
LON_MIN, LON_MAX = -78.392, -76.520
DLAT = 0.001            # ~111 m
DLON = 0.00128          # ~111 m at this latitude
ROWS = round((LAT_MAX - LAT_MIN) / DLAT)
COLS = round((LON_MAX - LON_MIN) / DLON)
WEIGHT = {"L": 1.0, "M": 10.0, "H": 10.0, "J": 10.0}
UNKNOWN_WEIGHT = 1.0
MAX_GAP_S = 30           # a longer silence breaks the line (no drawing across it)
REPUBLISH_S = 20 * 3600  # images at most about once a day
WIND_WAIT_S = 30 * 3600  # an hour waits this long for its wind report
WIND_KEYS = [f"{d:03d}" for d in range(0, 360, 10)] + ["calm"]


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
    """points: (t, hex, lat, lon, type) for one hour -> ({cell: weight}, aircraft).
    Each aircraft adds its weight once to every cell its path crosses."""
    by_hex = {}
    for t, hexid, lat, lon, actype in points:
        by_hex.setdefault(hexid, []).append((t, lat, lon, actype))
    out = {}
    for pts in by_hex.values():
        pts.sort()
        cells, prev, actype = set(), None, ""
        for t, lat, lon, ty in pts:
            actype = ty or actype
            if prev and t - prev[0] <= MAX_GAP_S:
                cells |= line_cells((prev[1], prev[2]), (lat, lon))
            else:
                cells |= line_cells((lat, lon), (lat, lon))
            prev = (t, lat, lon)
        w = weight(actype)
        for k in cells:
            out[k] = out.get(k, 0.0) + w
    return out, len(by_hex)


def add_hour(acc, points):
    """Adds one hour to a grid. Returns aircraft counted."""
    cells, n = hour_cells(points)
    for k, w in cells.items():
        acc[k] += w
    return n


def points_from_hour_file(path):
    with open(path) as f:
        body = json.load(f)
    ac, start = body["ac"], body["start"]
    return [(start + p[3], ac[p[5]][0], p[0], p[1], ac[p[5]][2]) for p in body["pts"]]


def wind_key(wind):
    """'calm' or a direction (degrees, from) -> the grid it goes in."""
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


def png_grey(rows, cols, data):
    """8-bit greyscale PNG from a bytes-like of rows*cols values."""
    raw = bytearray()
    for r in range(rows):
        raw.append(1)                      # "Sub" filter: better compression on sparse rows
        prev = 0
        for v in data[r * cols:(r + 1) * cols]:
            raw.append((v - prev) & 0xFF)
            prev = v
    def chunk(kind, body):
        c = struct.pack(">I", len(body)) + kind + body
        return c + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", cols, rows, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def grid_image(acc):
    """(PNG bytes, largest weight) of a grid, log-scaled."""
    top = max(acc)
    scale = 255 / math.log1p(top) if top > 0 else 0
    data = bytes(0 if v <= 0 else max(1, min(255, round(math.log1p(v) * scale))) for v in acc)
    return png_grey(ROWS, COLS, data), top


class Grid:
    def __init__(self, path):
        self.path = path
        self.acc = array.array("f")
        try:
            with open(path, "rb") as f:
                self.acc.frombytes(f.read())
        except OSError:
            pass
        if len(self.acc) != ROWS * COLS:
            self.acc = array.array("f", bytes(4 * ROWS * COLS))

    def add(self, cells):
        for k, w in cells.items():
            self.acc[k] += w

    def save(self):
        with open(self.path + ".tmp", "wb") as f:
            f.write(self.acc.tobytes())
        os.replace(self.path + ".tmp", self.path)


class State:
    def __init__(self, state_dir):
        self.dir = state_dir
        os.makedirs(os.path.join(state_dir, "wind"), exist_ok=True)
        self.path = os.path.join(state_dir, "state.json")
        try:
            with open(self.path) as f:
                self.s = json.load(f)
        except (OSError, ValueError):
            self.s = {}
        if self.s.get("hours") and not os.path.exists(os.path.join(state_dir, "acc.f32")):
            self.s = {}                        # the grids are gone: start again
        self.s.setdefault("hours", [])
        self.s.setdefault("aircraft_hours", 0)
        self.s.setdefault("published", 0)
        self.s.setdefault("wind", {})          # hour -> "calm" or degrees
        self.s.setdefault("bins", {})          # key -> {"hours", "aircraft_hours", "v"}
        self.s.setdefault("dirty", [])         # wind grids changed since the last images
        self.s.setdefault("no_wind_hours", 0)
        self.done = set(self.s["hours"])

    def grid_path(self, key):
        return os.path.join(self.dir, "acc.f32") if key is None else os.path.join(self.dir, "wind", key + ".f32")

    def save(self):
        self.s["hours"] = sorted(self.done)
        with open(self.path + ".tmp", "w") as f:
            json.dump(self.s, f)
        os.replace(self.path + ".tmp", self.path)


def update(work_dir, final_names, parse_name, hour_name, db_hours, now, state_dir,
           db_start=None, wx=None, wx_fetch=None, force=False):
    """Add every settled hour not added yet, overall and in its wind grid:
    from its frozen h/ file, or, for hours before the first file kept, from
    the log (db_hours(hour) -> points). wx: this run's weather reports;
    wx_fetch(hours) -> older ones, used once for hours past this run's.
    Writes heat.png, heat/<key>.png and heat.json into work_dir when a day
    has passed since the last time (or they are missing). Returns True if
    it wrote them."""
    st = State(state_dir)
    wind = st.s["wind"]
    for h, v in winds_by_hour(wx).items():
        wind.setdefault(str(h), v)
    finals = sorted(parse_name(n)[1] for n in final_names
                    if parse_name(n) and parse_name(n)[0] == "h")
    first_file = finals[0] if finals else None
    todo = []
    if db_start is not None and first_file is not None:
        hour = db_start - db_start % 3600
        while hour < first_file:
            todo.append((hour, None))
            hour += 3600
    todo += [(h, os.path.join(work_dir, hour_name("h", h))) for h in finals]
    todo = [(h, p) for h, p in todo if h not in st.done]
    # hours past what this run's weather covers: fetch older reports once
    if wx_fetch and any(str(h) not in wind for h, _ in todo) and not st.s.get("wind_backfilled"):
        oldest = min(h for h, _ in todo if str(h) not in wind)
        for h, v in winds_by_hour(wx_fetch(min(168, math.ceil((now - oldest) / 3600) + 2))).items():
            wind.setdefault(str(h), v)
        st.s["wind_backfilled"] = True
    ready = []
    for h, p in todo:
        w = wind.get(str(h))
        if w is None and now - (h + 3600) < WIND_WAIT_S:
            continue                           # its wind may still come
        ready.append((h, p, None if w is None else wind_key(w)))
    if ready:
        total = Grid(st.grid_path(None))
        by_key = {}
        for h, p, key in ready:
            by_key.setdefault(key, []).append((h, p))
        for key, hours in by_key.items():
            g = Grid(st.grid_path(key)) if key else None
            b = st.s["bins"].setdefault(key, {"hours": 0, "aircraft_hours": 0, "v": 0}) if key else None
            for h, p in hours:
                cells, n = hour_cells(points_from_hour_file(p) if p else db_hours(h))
                total.add(cells)
                st.s["aircraft_hours"] += n
                if g:
                    g.add(cells)
                    b["hours"] += 1
                    b["aircraft_hours"] += n
                else:
                    st.s["no_wind_hours"] += 1
                st.done.add(h)
            if g:
                g.save()
                if key not in st.s["dirty"]:
                    st.s["dirty"].append(key)
        total.save()
    # a year of hourly winds is ~9,000 entries; keep them all (they are small)
    st.save()
    png_path = os.path.join(work_dir, "heat.png")
    due = force or not os.path.exists(png_path) or now - st.s["published"] >= REPUBLISH_S
    if not (due and st.done):
        return False
    os.makedirs(os.path.join(work_dir, "heat"), exist_ok=True)
    png, top = grid_image(Grid(st.grid_path(None)).acc)
    with open(png_path + ".tmp", "wb") as f:
        f.write(png)
    os.replace(png_path + ".tmp", png_path)
    for key in st.s["dirty"]:
        bpng, btop = grid_image(Grid(st.grid_path(key)).acc)
        path = os.path.join(work_dir, "heat", key + ".png")
        with open(path + ".tmp", "wb") as f:
            f.write(bpng)
        os.replace(path + ".tmp", path)
        st.s["bins"][key].update(max_weight=round(btop, 1), v=int(now))
    st.s["dirty"] = []
    meta = {
        "bounds": [[LAT_MIN, LON_MIN], [LAT_MAX, LON_MAX]], "rows": ROWS, "cols": COLS,
        "cell_m": 111, "max_weight": round(top, 1),
        "first_hour": min(st.done), "last_hour": max(st.done),
        "hours": len(st.done), "aircraft_hours": st.s["aircraft_hours"],
        "no_wind_hours": st.s["no_wind_hours"],
        "weights": {"light": WEIGHT["L"], "medium": WEIGHT["M"], "heavy": WEIGHT["H"],
                    "unknown": UNKNOWN_WEIGHT},
        "scale": "log: pixel = round(255 * ln(1 + w) / ln(1 + max_weight))",
        "wind": {k: {"hours": b["hours"], "aircraft_hours": b["aircraft_hours"],
                     "max_weight": b.get("max_weight", 0), "v": b["v"]}
                 for k, b in sorted(st.s["bins"].items())},
        "wind_note": "KIAD report nearest the middle of each hour; direction the wind blows FROM, "
                     "to 10 degrees; 'calm' is calm or variable. Image: heat/<key>.png.",
        "built": int(now),
    }
    with open(os.path.join(work_dir, "heat.json"), "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    st.s["published"] = int(now)
    st.save()
    return True
