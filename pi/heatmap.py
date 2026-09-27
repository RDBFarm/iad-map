#!/usr/bin/env python3
"""The history heat map: every aircraft path the map has shown, summed.

Owner's request (2026-09-27): a heat map over the history of the database
where "the resulting lines shouldn't get broader, just brighter, and with
the absolute size of the planes weighting the brightness as well. So an
approach that has 100 private planes would have the same brightness as 10
commercial planes (in the broadest sense possible)."

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
  - The sum is kept on the drive (heat/acc.f32 and the hours already added)
    and grows forever, beyond the 30 days of hour files. heat.png and
    heat.json are written for the map at most once a day, so the upload is
    about a megabyte a day.

heat.png: 8-bit greyscale, one pixel per cell, north up; 0 = nothing, else
round(255 * log(1 + w) / log(1 + max)). heat.json gives the bounds, the
largest cell's weight and what is covered.
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
MAX_GAP_S = 30          # a longer silence breaks the line (no drawing across it)
REPUBLISH_S = 20 * 3600  # heat.png at most about once a day


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


def add_hour(acc, points):
    """points: (t, hex, lat, lon, type) for one hour. Adds each aircraft's
    weight once to every cell its path crosses. Returns aircraft counted."""
    by_hex = {}
    for t, hexid, lat, lon, actype in points:
        by_hex.setdefault(hexid, []).append((t, lat, lon, actype))
    for hexid, pts in by_hex.items():
        pts.sort()
        cells = set()
        prev = None
        actype = ""
        for t, lat, lon, ty in pts:
            actype = ty or actype
            if prev and t - prev[0] <= MAX_GAP_S:
                cells |= line_cells((prev[1], prev[2]), (lat, lon))
            else:
                cells |= line_cells((lat, lon), (lat, lon))
            prev = (t, lat, lon)
        w = weight(actype)
        for k in cells:
            acc[k] += w
    return len(by_hex)


def points_from_hour_file(path):
    with open(path) as f:
        body = json.load(f)
    ac, start = body["ac"], body["start"]
    return [(start + p[3], ac[p[5]][0], p[0], p[1], ac[p[5]][2]) for p in body["pts"]]


def png_grey(rows, cols, data):
    """8-bit greyscale PNG from a bytes-like of rows*cols values."""
    raw = bytearray()
    for r in range(rows):
        raw.append(1)                      # "Sub" filter: better compression on sparse rows
        row = data[r * cols:(r + 1) * cols]
        prev = 0
        for v in row:
            raw.append((v - prev) & 0xFF)
            prev = v
    def chunk(kind, body):
        c = struct.pack(">I", len(body)) + kind + body
        return c + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", cols, rows, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


class Heat:
    def __init__(self, state_dir):
        self.dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self.acc_path = os.path.join(state_dir, "acc.f32")
        self.state_path = os.path.join(state_dir, "state.json")
        self.acc = array.array("f")
        try:
            with open(self.acc_path, "rb") as f:
                self.acc.frombytes(f.read())
        except OSError:
            pass
        if len(self.acc) != ROWS * COLS:
            self.acc = array.array("f", bytes(4 * ROWS * COLS))
            self.state = {"hours": [], "aircraft_hours": 0, "published": 0}
        else:
            try:
                with open(self.state_path) as f:
                    self.state = json.load(f)
            except (OSError, ValueError):
                self.state = {"hours": [], "aircraft_hours": 0, "published": 0}
        self.done = set(self.state["hours"])

    def add(self, hour, points):
        self.state["aircraft_hours"] += add_hour(self.acc, points)
        self.done.add(hour)

    def save(self):
        tmp = self.acc_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self.acc.tobytes())
        os.replace(tmp, self.acc_path)
        self.state["hours"] = sorted(self.done)
        with open(self.state_path + ".tmp", "w") as f:
            json.dump(self.state, f)
        os.replace(self.state_path + ".tmp", self.state_path)

    def image(self):
        top = max(self.acc) if self.done else 0.0
        scale = 255 / math.log1p(top) if top > 0 else 0
        data = bytes(0 if v <= 0 else max(1, min(255, round(math.log1p(v) * scale))) for v in self.acc)
        meta = {
            "bounds": [[LAT_MIN, LON_MIN], [LAT_MAX, LON_MAX]], "rows": ROWS, "cols": COLS,
            "cell_m": 111, "max_weight": round(top, 1),
            "first_hour": min(self.done) if self.done else None,
            "last_hour": max(self.done) if self.done else None,
            "hours": len(self.done), "aircraft_hours": self.state["aircraft_hours"],
            "weights": {"light": WEIGHT["L"], "medium": WEIGHT["M"], "heavy": WEIGHT["H"],
                        "unknown": UNKNOWN_WEIGHT},
            "scale": "log: pixel = round(255 * ln(1 + w) / ln(1 + max_weight))",
        }
        return png_grey(ROWS, COLS, data), meta


def update(work_dir, final_names, parse_name, hour_name, db_hours, now, state_dir,
           db_start=None, force=False):
    """Add every settled hour not added yet: from its frozen h/ file, or, for
    hours before the first file kept, from the log (db_hours(hour) -> points).
    Writes heat.png and heat.json into work_dir when a day has passed since
    the last time (or they are missing). Returns True if they were written."""
    heat = Heat(state_dir)
    added = 0
    finals = sorted(parse_name(n)[1] for n in final_names
                    if parse_name(n) and parse_name(n)[0] == "h")
    first_file = finals[0] if finals else None
    if db_start is not None and first_file is not None:
        hour = db_start - db_start % 3600
        while hour < first_file:
            if hour not in heat.done:
                heat.add(hour, db_hours(hour))
                added += 1
            hour += 3600
    for hour in finals:
        if hour not in heat.done:
            heat.add(hour, points_from_hour_file(os.path.join(work_dir, hour_name("h", hour))))
            added += 1
    if added:
        heat.save()
    png_path = os.path.join(work_dir, "heat.png")
    due = force or not os.path.exists(png_path) or now - heat.state.get("published", 0) >= REPUBLISH_S
    if not (due and heat.done):
        return False
    png, meta = heat.image()
    meta["built"] = int(now)
    with open(png_path + ".tmp", "wb") as f:
        f.write(png)
    os.replace(png_path + ".tmp", png_path)
    with open(os.path.join(work_dir, "heat.json"), "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    heat.state["published"] = int(now)
    heat.save()
    return True
