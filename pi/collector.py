#!/usr/bin/env python3
"""Log everything this receiver hears, to SQLite on the USB drive.

Every 2 seconds (IADMAP_INTERVAL_S), reads readsb's current picture,
/run/readsb/aircraft.json, and stores one row for every aircraft that has sent
a message since the previous read -- all ranges and altitudes, with or without
a position (Mode S-only aircraft have none). Nothing is filtered or deleted
here; choosing what to show is the publisher's job, and how long to keep the
log has not been decided.

Rows are buffered and committed every 30 seconds, so the drive sees a
handful of writes a minute rather than one per read. The database lives on
/mnt/flightdata, never the SD card: the collector refuses to start if that
drive isn't mounted.

Table `positions`, one row per aircraft per read:
  t          epoch seconds of the aircraft's last message
  hex        ICAO address        flight, type, category, squawk
  lat, lon   NULL if no position; pos_t = epoch seconds of that position
             (an aircraft can send other messages without a new position)
  alt_baro   feet (pressure altitude; can be negative on the ground)
  on_ground  1 when readsb reports "ground"
  alt_geom, gs, track, baro_rate, geom_rate, rssi
  mlat       1 if the position came from MLAT
  emergency  readsb's ADS-B emergency status when not "none" (general, nordo, ...)
  acas_ra    a TCAS resolution advisory the aircraft broadcast, as readsb's
             JSON, if this readsb reports one (not yet confirmed on the Pi)
"""
import json, os, sqlite3, time

AIRCRAFT_JSON = os.environ.get("IADMAP_AIRCRAFT_JSON", "/run/readsb/aircraft.json")
DRIVE = os.environ.get("IADMAP_DRIVE", "/mnt/flightdata")
DB_PATH = os.environ.get("IADMAP_DB", os.path.join(DRIVE, "iad-map", "flights.db"))
INTERVAL_S = float(os.environ.get("IADMAP_INTERVAL_S", "2"))
COMMIT_EVERY_S = 30

COLUMNS = ["t", "hex", "flight", "type", "category", "squawk", "lat", "lon", "pos_t",
           "alt_baro", "on_ground", "alt_geom", "gs", "track", "baro_rate", "geom_rate",
           "rssi", "mlat", "emergency", "acas_ra"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
  t REAL NOT NULL, hex TEXT NOT NULL, flight TEXT, type TEXT, category TEXT, squawk TEXT,
  lat REAL, lon REAL, pos_t REAL, alt_baro INTEGER, on_ground INTEGER,
  alt_geom INTEGER, gs REAL, track REAL, baro_rate INTEGER, geom_rate INTEGER,
  rssi REAL, mlat INTEGER, emergency TEXT, acas_ra TEXT);
CREATE INDEX IF NOT EXISTS positions_t ON positions (t);
"""


def open_db(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")  # the publisher can read while this writes
    db.executescript(SCHEMA)
    return db


def rows_from(data, last_msg):
    """Rows for aircraft with a message newer than the one last recorded.
    last_msg maps hex -> time of the last recorded message, and is updated."""
    now = data.get("now", time.time())
    out = []
    for ac in data.get("aircraft", []):
        t = round(now - ac.get("seen", 1e9), 1)
        if t <= last_msg.get(ac.get("hex"), 0):
            continue  # nothing new from this aircraft since the last read
        last_msg[ac.get("hex")] = t
        alt = ac.get("alt_baro")
        has_pos = ac.get("lat") is not None and ac.get("lon") is not None
        out.append((
            t, ac.get("hex", ""), (ac.get("flight") or "").strip() or None,
            ac.get("t"), ac.get("category"), ac.get("squawk"),
            ac.get("lat") if has_pos else None, ac.get("lon") if has_pos else None,
            round(now - ac.get("seen_pos", 0), 1) if has_pos else None,
            alt if isinstance(alt, (int, float)) else None, 1 if alt == "ground" else 0,
            ac.get("alt_geom"), ac.get("gs"), ac.get("track"),
            ac.get("baro_rate"), ac.get("geom_rate"), ac.get("rssi"),
            1 if has_pos and "lat" in (ac.get("mlat") or []) else 0,
            ac.get("emergency") if ac.get("emergency") not in (None, "none") else None,
            json.dumps(ac["acas_ra"], separators=(",", ":")) if ac.get("acas_ra") else None,
        ))
    return out


def main():
    if DB_PATH.startswith(DRIVE) and not os.path.ismount(DRIVE):
        raise SystemExit(f"collector: {DRIVE} is not mounted; not writing to the SD card instead")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = open_db(DB_PATH)
    insert = f"INSERT INTO positions ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})"
    buffer, last_commit, last_msg = [], time.time(), {}
    while True:
        started = time.time()
        try:
            with open(AIRCRAFT_JSON) as f:
                buffer.extend(rows_from(json.load(f), last_msg))
        except (OSError, ValueError) as e:
            print("collector: skipped a reading:", e, flush=True)
        if started - last_commit >= COMMIT_EVERY_S and buffer:
            db.executemany(insert, buffer)
            db.commit()
            buffer, last_commit = [], started
            cutoff = started - 600
            last_msg = {h: t for h, t in last_msg.items() if t > cutoff}
        time.sleep(max(0.2, INTERVAL_S - (time.time() - started)))


if __name__ == "__main__":
    main()
