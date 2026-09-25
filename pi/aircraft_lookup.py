#!/usr/bin/env python3
"""Aircraft type by ICAO address, for when readsb doesn't supply one.

readsb on the ADS-B Exchange image leaves the type field ("t") empty -- all
258 aircraft in a check on 2026-09-24 had none -- so prop planes couldn't be
told from jets. This looks the type up in tar1090's aircraft database
(wiedehopf/tar1090-db, csv branch: hex;registration;type;...), kept as a
small SQLite table on the USB drive.

  python3 aircraft_lookup.py --build   download the database and (re)build
                                       the table; run by the installer and
                                       monthly by iad-map-aircraft-db.timer
  type_for(hex)                        the type code, or None if unknown
  reg_for(hex)                         the registration, or None
"""
import gzip, json, os, re, sqlite3, sys, time, urllib.request

CSV_URL = "https://raw.githubusercontent.com/wiedehopf/tar1090-db/csv/aircraft.csv.gz"
DB_PATH = os.environ.get("IADMAP_AIRCRAFT_DB", "/mnt/flightdata/iad-map/aircraft.db")

_db = None
_cache = {}


def type_for(hexid):
    """ICAO type code for an ICAO address, or None. Cached per address."""
    return _row(hexid)[1]


_OPERATORS = None


def operator_for(callsign):
    """(name, radio callsign) for an airline-style callsign such as RPA5604,
    from operators.json (tar1090-db); None if not one or not listed."""
    global _OPERATORS
    m = re.match(r"^([A-Z]{3})[0-9]", (callsign or "").strip().upper())
    if not m:
        return None
    if _OPERATORS is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "operators.json")) as f:
                _OPERATORS = json.load(f)["operators"]
        except (OSError, ValueError, KeyError):
            _OPERATORS = {}
    op = _OPERATORS.get(m.group(1))
    return (op[0], op[2]) if op else None


def reg_for(hexid):
    """Registration for an ICAO address, or None."""
    return _row(hexid)[0]


def _row(hexid):
    """(registration, type) for an ICAO address; (None, None) if unknown."""
    global _db
    if not hexid or hexid.startswith("~"):
        return (None, None)  # "~" marks a non-ICAO address (TIS-B etc.): no real airframe to look up
    hexid = hexid.lower()
    if hexid in _cache:
        return _cache[hexid]
    if len(_cache) > 20000:
        _cache.clear()
    try:
        if _db is None:
            if not os.path.exists(DB_PATH):
                return (None, None)
            _db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        row = _db.execute("SELECT reg, type FROM aircraft WHERE hex = ?", (hexid,)).fetchone()
        r = (row[0] or None, row[1] or None) if row else (None, None)
    except sqlite3.Error:
        _db = None
        return (None, None)
    _cache[hexid] = r
    return r


def build():
    tmp_gz, tmp_db = DB_PATH + ".csv.gz.tmp", DB_PATH + ".tmp"
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": "rdbf-iad-map"})
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp_gz, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    db = sqlite3.connect(tmp_db)
    db.execute("CREATE TABLE aircraft (hex TEXT PRIMARY KEY, reg TEXT, type TEXT) WITHOUT ROWID")
    n = 0
    with gzip.open(tmp_gz, "rt", encoding="utf-8", errors="replace") as f:
        rows = []
        for line in f:
            p = line.rstrip("\n").split(";")
            if len(p) < 3 or not p[0]:
                continue
            rows.append((p[0].lower(), p[1] or None, p[2] or None))
            if len(rows) >= 50000:
                db.executemany("INSERT OR REPLACE INTO aircraft VALUES (?,?,?)", rows)
                n += len(rows)
                rows = []
        db.executemany("INSERT OR REPLACE INTO aircraft VALUES (?,?,?)", rows)
        n += len(rows)
    db.commit()
    typed = db.execute("SELECT COUNT(*) FROM aircraft WHERE type IS NOT NULL").fetchone()[0]
    db.close()
    os.replace(tmp_db, DB_PATH)
    os.remove(tmp_gz)
    print(f"aircraft_lookup: {n} aircraft, {typed} with a type, in {DB_PATH}", flush=True)


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    else:
        for h in sys.argv[1:]:
            print(h, type_for(h))
