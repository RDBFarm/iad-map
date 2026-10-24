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

The wide map (update_wide; owner, 2026-10-13: "a much larger area and
include higher altitudes"): the same lines and weights over everything the
receiver hears, every height, in a box 200 miles each way from Dulles (98% of
the receiver's aircraft-time is within 200 miles, 99.9% within 230; every
aircraft heard Oct 7-14, the farthest 275 miles), with cells
about 445 m square so the image has as many pixels as the near one. No wind,
airport or take-off split: one grid per size, one image,
heat/wide/all.png, with heat-wide.json. Its sums live in their own folder
and its rebuild runs under its own time limit, so it never holds up the
near map or the live map.
"""
import itertools, json, math, mmap, os, shutil, struct, time, zlib

VERSION = 2
# The area: the 50-mile circle around Dulles (publish.CENTER, HEAT_RADIUS_NM; the
# live map itself reaches 75 miles since 2026-10-18)
LAT_MIN, LAT_MAX = 38.215, 39.675
LON_MIN, LON_MAX = -78.392, -76.520
DLAT = 0.001            # ~111 m
DLON = 0.00128          # ~111 m at this latitude
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


class Area:
    """A grid of cells over a lat/lon box; row 0 is the north edge."""
    def __init__(self, lat_min, lat_max, lon_min, lon_max, dlat, dlon):
        self.lat_min, self.lat_max, self.lon_min, self.lon_max = lat_min, lat_max, lon_min, lon_max
        self.dlat, self.dlon = dlat, dlon
        self.rows = round((lat_max - lat_min) / dlat)
        self.cols = round((lon_max - lon_min) / dlon)

    def bounds(self):
        return [[self.lat_min, self.lon_min], [self.lat_max, self.lon_max]]


NEAR = Area(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, DLAT, DLON)
# The wide map: 200 statute miles (173.8 nm) north, south, east and west of
# Dulles (38.9444, -77.4558); cells ~445 m, four times the near map's.
WIDE = Area(36.044, 41.844, -81.180, -73.731, 0.004, 0.00512)
WIDE_VERSION = 1
WIDE_BUDGET_S = 120       # at most this long adding hours per run (the Pi is a 4B)


def cell(lat, lon, area=NEAR):
    """Fractional (row, col) of a position; row 0 is the north edge."""
    return (area.lat_max - lat) / area.dlat, (lon - area.lon_min) / area.dlon


def line_cells(a, b, area=NEAR):
    """Cells a straight segment passes through (sampled at least twice per cell)."""
    (r1, c1), (r2, c2) = cell(*a, area), cell(*b, area)
    n = max(1, int(math.ceil(2 * max(abs(r2 - r1), abs(c2 - c1)))))
    rows, cols = area.rows, area.cols
    out = set()
    for i in range(n + 1):
        f = i / n
        r, c = int(r1 + (r2 - r1) * f), int(c1 + (c2 - c1) * f)
        if 0 <= r < rows and 0 <= c < cols:
            out.add(r * cols + c)
    return out


def path_cells(pts, area=NEAR):
    """One aircraft's positions (t, lat, lon, type, tag), in time order ->
    ({cell: tag of the position that first reached it}, type). The path is
    drawn as straight segments between positions at most MAX_GAP_S apart."""
    cells, prev, actype = {}, None, ""
    for t, lat, lon, ty, tag in pts:
        actype = ty or actype
        seg = line_cells((prev[1], prev[2]) if prev and t - prev[0] <= MAX_GAP_S else (lat, lon), (lat, lon), area)
        for k in seg:
            cells.setdefault(k, tag)
        prev = (t, lat, lon)
    return cells, actype


def aircraft_cells(points, area=NEAR):
    """points: (t, hex, lat, lon, type, tag) in any order -> path_cells()
    for each aircraft."""
    by_hex = {}
    for t, hexid, lat, lon, actype, tag in points:
        by_hex.setdefault(hexid, []).append((t, lat, lon, actype, tag))
    for pts in by_hex.values():
        pts.sort()
        yield path_cells(pts, area)


def hour_cells(points):
    """points: (t, hex, lat, lon, type, zone, movement 'a'|'d') for one hour
    -> ({(zone, class): {cell: weight}}, aircraft). Each aircraft adds its
    weight once to every cell its path crosses; the cell goes to the zone and
    movement of the position that first reached it, the class to the
    aircraft's size, so all the parts add up to the total."""
    out, n = {}, 0
    for cells, actype in aircraft_cells((t, h, lat, lon, ty, (zone, mov))
                                        for t, h, lat, lon, ty, zone, mov in points):
        n += 1
        w = weight(actype)
        size = "b" if w > 1 else "s"
        for k, (zone, mov) in cells.items():
            d = out.setdefault((zone, mov + size), {})
            d[k] = d.get(k, 0.0) + w
    return out, n


def wide_hour_cells(points):
    """points: (t, hex, lat, lon, type) for one hour, sorted by aircraft and
    then time, so they can be read a row at a time (about 160 aircraft are in
    the box at once, ~300,000 positions an hour) -> ({'s'|'b': {cell:
    weight}}, aircraft), on the wide grid, the same rules as hour_cells."""
    out, n = {}, 0
    for _, rows in itertools.groupby(points, key=lambda p: p[1]):
        cells, actype = path_cells(((t, lat, lon, ty, None) for t, _, lat, lon, ty in rows), WIDE)
        n += 1
        w = weight(actype)
        d = out.setdefault("b" if w > 1 else "s", {})
        for k in cells:
            d[k] = d.get(k, 0.0) + w
    return out, n


def wind_key(wind):
    """'calm' or a direction (degrees, from) -> its wind key."""
    if wind == "calm":
        return "calm"
    return f"{int(round(float(wind) / 10.0)) % 36 * 10:03d}"


def winds_by_hour(wx, distance=False):
    """{hour: 'calm' or degrees} from publish.fetch_weather() reports: for
    each hour, the report nearest its middle, within an hour of it. With
    `distance`, {hour: (seconds from the hour's middle, wind)}."""
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
    if distance:
        return {h: (d, v) for h, (d, v) in best.items() if v is not None}
    return {h: v for h, (d, v) in best.items() if v is not None}


def take_winds(st, wx):
    """Record the wind for each hour of these reports in the state. An hour
    not yet in a grid takes the report nearest its middle seen so far
    (`wind_d` keeps how near): until 2026-10-24 the first report seen was
    kept (`setdefault`), and since the publisher runs every 15 minutes, the
    :52 report of the hour BEFORE (38 min from the middle) was always seen
    before the hour's own :52 report (22 min) and kept, so every hour went
    into the wind grid of the hour before it. Hours already in a grid keep
    the wind they were counted under."""
    wind, wind_d = st.s["wind"], st.s["wind_d"]
    for h, (d, v) in winds_by_hour(wx, distance=True).items():
        k = str(h)
        if h in st.done:
            wind.setdefault(k, v)
        elif k not in wind or d < wind_d.get(k, 3601):
            wind[k], wind_d[k] = v, d


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
    def __init__(self, path, area=NEAR):
        self.path = path
        size = 4 * area.rows * area.cols
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


def add_to(path, cells, area=NEAR):
    g = Grid(path, area)
    try:
        g.add(cells)
    finally:
        g.close()


def write_image(out, paths, area=NEAR):
    """The RGB image of two grids (red the first, green the second; a
    missing grid is empty). Returns each one's largest weight."""
    chans, tops = [], []
    for path in paths:
        if os.path.exists(path):
            g = Grid(path, area)
            b, top = log_bytes(g.acc)
            g.close()
        else:
            b, top = bytearray(area.rows * area.cols), 0.0
        chans.append(b)
        tops.append(round(top, 1))
    rgb = bytearray(3 * area.rows * area.cols)
    rgb[0::3], rgb[1::3] = chans
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out + ".tmp", "wb") as f:
        f.write(png(area.rows, area.cols, rgb, 3))
    os.replace(out + ".tmp", out)
    return tops


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
        for k, v in (("hours", []), ("aircraft_hours", 0), ("published", 0), ("wind", {}), ("wind_d", {}),
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
    take_winds(st, wx)
    if db_start is None:
        st.save()
        return False
    last = int(now) - SETTLE_S - 3600
    last -= last % 3600
    todo = [h for h in range(db_start - db_start % 3600, last + 1, 3600) if h not in st.done]
    if wx_fetch and todo and not st.s.get("wind_backfilled") and any(str(h) not in wind for h in todo):
        oldest = min(h for h in todo if str(h) not in wind)
        take_winds(st, wx_fetch(min(168, math.ceil((now - oldest) / 3600) + 2)))
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
        tops = write_image(os.path.join(work_dir, "heat", "v2", scope, wk, mov + ".png"),
                           [st.grid(scope, wk, mov + size) for size in "sb"])
        st.s["images"][img] = {"max": tops, "v": int(now)}
    st.s["dirty"] = []
    meta = {
        "version": VERSION,
        "bounds": NEAR.bounds(), "rows": NEAR.rows, "cols": NEAR.cols, "cell_m": 111,
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


def update_wide(work_dir, db_hours, now, state_dir, db_start=None, force=False, budget_s=WIDE_BUDGET_S):
    """The wide map: add settled hours of the log not added yet, oldest
    first, for at most budget_s (and MAX_HOURS_PER_RUN hours) per call.
    db_hours(hour) -> (t, hex, lat, lon, type), every airborne position in
    the WIDE box. Writes heat/wide/all.png and heat-wide.json into work_dir
    when nothing is left to add and a day has passed since the last time.
    Returns True if it wrote them."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "state.json")
    try:
        with open(path) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    if s.get("version") != WIDE_VERSION:
        for name in ("s.f32", "b.f32"):
            if os.path.exists(os.path.join(state_dir, name)):
                os.remove(os.path.join(state_dir, name))
        s = {"version": WIDE_VERSION}
    for k, v in (("hours", []), ("aircraft_hours", 0), ("published", 0), ("dirty", False), ("max", [0, 0])):
        s.setdefault(k, v)
    done = set(s["hours"])

    def save():
        s["hours"] = sorted(done)
        with open(path + ".tmp", "w") as f:
            json.dump(s, f)
        os.replace(path + ".tmp", path)

    if db_start is None:
        return False
    last = int(now) - SETTLE_S - 3600
    last -= last % 3600
    todo = [h for h in range(db_start - db_start % 3600, last + 1, 3600) if h not in done]
    started = time.monotonic()
    for h in todo[:MAX_HOURS_PER_RUN]:
        if time.monotonic() - started > budget_s:
            break
        parts, n = wide_hour_cells(db_hours(h))
        for size, cells in parts.items():
            add_to(os.path.join(state_dir, size + ".f32"), cells, WIDE)
        s["aircraft_hours"] += n
        s["dirty"] = True
        done.add(h)
        save()                                 # after each hour: a crash loses at most the hour in hand
    meta_path = os.path.join(work_dir, "heat-wide.json")
    try:
        with open(meta_path) as f:
            old_version = json.load(f).get("version")
    except (OSError, ValueError):
        old_version = None
    due = force or old_version != WIDE_VERSION or now - s["published"] >= REPUBLISH_S
    if any(h not in done for h in todo) or not due or not done:
        return False
    if s["dirty"] or not os.path.exists(os.path.join(work_dir, "heat", "wide", "all.png")):
        s["max"] = write_image(os.path.join(work_dir, "heat", "wide", "all.png"),
                               [os.path.join(state_dir, size + ".f32") for size in "sb"], WIDE)
        s["v"] = int(now)
    meta = {
        "version": WIDE_VERSION,
        "bounds": WIDE.bounds(), "rows": WIDE.rows, "cols": WIDE.cols, "cell_m": 445,
        "first_hour": min(done), "last_hour": max(done),
        "hours": len(done), "aircraft_hours": s["aircraft_hours"],
        "images": {"all": {"max": s["max"], "v": s.get("v", int(now))}},
        "weights": {"small": WEIGHT["L"], "big": WEIGHT["M"], "unknown": UNKNOWN_WEIGHT},
        "note": "Image heat/wide/all.png: every airborne position the receiver logged, every height, "
                "in a box 200 miles each way from Dulles. Red channel small aircraft, green big; "
                "pixel = round(255 ln(1+w) / ln(1+max)), max per channel in images.all.max.",
        "built": int(now),
    }
    with open(meta_path + ".tmp", "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    os.replace(meta_path + ".tmp", meta_path)
    s["published"] = int(now)
    s["dirty"] = False
    save()
    return True
