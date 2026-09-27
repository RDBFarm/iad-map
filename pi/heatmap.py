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
  - Airports (owner, 2026-09-27: "give me just airports to select"): the
    live map's airport zones (publish.classify_airport; 0-9, 7 = en route).
    Each aircraft's cells in an hour are also split by the zone of the
    position that reached them first, into heat/ap/<id>/all and
    heat/ap/<id>/<wind>, so airports combine exactly with the wind: the
    zones add up to the total.
  - The grids are kept on the drive (heat/, float32) and grow forever,
    beyond the 30 days of hour files. The images are rewritten at most once
    a day, and only the wind grids that got new hours, so the upload is
    small.

Images: 8-bit greyscale PNG, one pixel per cell, north up; 0 = nothing,
else round(255 * log(1 + w) / log(1 + max)), max per image in heat.json.
heat.png is all winds; heat/<dir>.png (000..350) and heat/calm.png by wind;
heat/ap/<id>/all.png and heat/ap/<id>/<wind>.png by airport zone.
"""
import array, itertools, json, math, mmap, os, struct, time, zlib

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


def hour_cells_split(points):
    """points: (t, hex, lat, lon, type, airport) for one hour ->
    ({cell: weight}, {airport: {cell: weight}}, aircraft). Each aircraft adds
    its weight once to every cell its path crosses; the cell goes to the
    airport zone of the position that first reached it, so the zones add up
    to the total."""
    by_hex = {}
    for t, hexid, lat, lon, actype, ap in points:
        by_hex.setdefault(hexid, []).append((t, lat, lon, actype, ap))
    total, by_ap = {}, {}
    for pts in by_hex.values():
        pts.sort()
        cells, prev, actype = {}, None, ""
        for t, lat, lon, ty, ap in pts:
            actype = ty or actype
            seg = line_cells((prev[1], prev[2]) if prev and t - prev[0] <= MAX_GAP_S else (lat, lon), (lat, lon))
            for k in seg:
                cells.setdefault(k, ap)
            prev = (t, lat, lon)
        w = weight(actype)
        for k, ap in cells.items():
            total[k] = total.get(k, 0.0) + w
            d = by_ap.setdefault(ap, {})
            d[k] = d.get(k, 0.0) + w
    return total, by_ap, len(by_hex)


def hour_cells(points):
    """({cell: weight}, aircraft) for one hour; points as hour_cells_split's."""
    total, _, n = hour_cells_split(points)
    return total, n


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
    return [(start + p[3], ac[p[5]][0], p[0], p[1], ac[p[5]][2], p[4]) for p in body["pts"]]


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
    data = bytes(data)
    raw = b"".join(b"\x00" + data[r * cols:(r + 1) * cols] for r in range(rows))
    def chunk(kind, body):
        c = struct.pack(">I", len(body)) + kind + body
        return c + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", cols, rows, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def grid_image(acc):
    """(PNG bytes, largest weight) of a grid, log-scaled. Only lit cells are
    visited (most wind and airport grids are sparse)."""
    lit = list(itertools.compress(range(len(acc)), acc))
    top = max((acc[i] for i in lit), default=0.0)
    data = bytearray(len(acc))
    if top > 0:
        scale = 255 / math.log1p(top)
        for i in lit:
            v = acc[i]
            if v > 0:
                data[i] = max(1, min(255, round(math.log1p(v) * scale)))
    return png_grey(ROWS, COLS, data), top


class Grid:
    """A float32 grid file, edited in place (memory-mapped): with ~400 grids
    the Pi only holds the pages an hour touches, not 8.5 MB per grid."""
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        size = 4 * ROWS * COLS
        if not os.path.exists(path) or os.path.getsize(path) != size:
            with open(path, "wb") as f:
                f.truncate(size)               # a sparse file: no disk used until written
        self.f = open(path, "r+b")
        self.mm = mmap.mmap(self.f.fileno(), size)
        self.acc = memoryview(self.mm).cast("f")

    def add(self, cells):
        acc = self.acc
        for k, w in cells.items():
            acc[k] += w

    def save(self):
        self.mm.flush()

    def close(self):
        self.acc.release()
        self.mm.close()
        self.f.close()


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
        self.s.setdefault("ap_hours", [])      # hours already split by airport zone
        self.s.setdefault("ap_bins", {})       # "id/key" -> {"hours", "aircraft_hours", "v"}
        self.s.setdefault("ap_dirty", [])
        self.done = set(self.s["hours"])
        self.ap_done = set(self.s["ap_hours"])

    def grid_path(self, key):
        return os.path.join(self.dir, "acc.f32") if key is None else os.path.join(self.dir, "wind", key + ".f32")

    def ap_path(self, apkey):
        return os.path.join(self.dir, "ap", apkey + ".f32")

    def save(self):
        self.s["hours"] = sorted(self.done)
        self.s["ap_hours"] = sorted(self.ap_done)
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
        ready.append((h, p, None if w is None else wind_key(w), True))
    # hours added before airports existed: split them by airport zone once
    sources = dict(todo)
    sources.update({h: os.path.join(work_dir, hour_name("h", h)) for h in finals})
    for h in sorted(st.done - st.ap_done):
        p = sources.get(h)
        if p is None and first_file is not None and h < first_file and db_start is not None and h >= db_start - db_start % 3600:
            p = ""                             # before the first hour file: from the log
        if p is None or (p and not os.path.exists(p)):
            st.ap_done.add(h)                  # its hour file is gone (older than the files kept)
            st.s["ap_missing_hours"] = st.s.get("ap_missing_hours", 0) + 1
            continue
        w = wind.get(str(h))
        ready.append((h, p or None, None if w is None else wind_key(w), False))
    grids = {}
    def grid(path):
        if path not in grids:
            grids[path] = Grid(path)
        return grids[path]
    def count(bins, key, n):
        b = bins.setdefault(key, {"hours": 0, "aircraft_hours": 0, "v": 0})
        b["hours"] += 1
        b["aircraft_hours"] += n
    try:
        for h, p, key, new in ready:
            cells, by_ap, n = hour_cells_split(points_from_hour_file(p) if p else db_hours(h))
            if new:
                grid(st.grid_path(None)).add(cells)
                st.s["aircraft_hours"] += n
                if key:
                    grid(st.grid_path(key)).add(cells)
                    count(st.s["bins"], key, n)
                    if key not in st.s["dirty"]:
                        st.s["dirty"].append(key)
                else:
                    st.s["no_wind_hours"] += 1
                st.done.add(h)
            for ap, apcells in by_ap.items():
                for k in (["all", key] if key else ["all"]):
                    apkey = f"{ap}/{k}"
                    grid(st.ap_path(apkey)).add(apcells)
                    count(st.s["ap_bins"], apkey, 0)   # hours per zone; aircraft not split
                    if apkey not in st.s["ap_dirty"]:
                        st.s["ap_dirty"].append(apkey)
            st.ap_done.add(h)
    finally:
        for g in grids.values():
            g.save()
            g.close()
    # a year of hourly winds is ~9,000 entries; keep them all (they are small)
    st.save()
    png_path = os.path.join(work_dir, "heat.png")
    due = force or not os.path.exists(png_path) or now - st.s["published"] >= REPUBLISH_S
    if not (due and st.done):
        return False
    os.makedirs(os.path.join(work_dir, "heat"), exist_ok=True)
    g = Grid(st.grid_path(None))
    png, top = grid_image(g.acc)
    g.close()
    with open(png_path + ".tmp", "wb") as f:
        f.write(png)
    os.replace(png_path + ".tmp", png_path)
    def write_image(grid_file, png_rel, info):
        g = Grid(grid_file)
        bpng, btop = grid_image(g.acc)
        g.close()
        path = os.path.join(work_dir, "heat", png_rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as f:
            f.write(bpng)
        os.replace(path + ".tmp", path)
        info.update(max_weight=round(btop, 1), v=int(now))
    for key in st.s["dirty"]:
        write_image(st.grid_path(key), key + ".png", st.s["bins"][key])
    st.s["dirty"] = []
    for apkey in st.s["ap_dirty"]:
        write_image(st.ap_path(apkey), "ap/" + apkey + ".png", st.s["ap_bins"][apkey])
    st.s["ap_dirty"] = []
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
        "airports": {apkey: {"hours": b["hours"], "max_weight": b.get("max_weight", 0), "v": b["v"]}
                     for apkey, b in sorted(st.s["ap_bins"].items())},
        "airports_note": "Airport zones as on the live map (0 KIAD, 1 KDCA, 2 KBWI, 3 KJYO, 4 KGAI, 5 KHEF, "
                         "6 KRMN, 7 en route, 8 KADW, 9 KNYG); '<id>/all' all winds, '<id>/<wind>' by wind. "
                         "Image: heat/ap/<id>/<key>.png. The zones add up to the total.",
        "built": int(now),
    }
    with open(os.path.join(work_dir, "heat.json"), "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    st.s["published"] = int(now)
    st.save()
    return True
