#!/usr/bin/env python3
"""Log everything this receiver hears, to SQLite on the USB drive.

Every 2 seconds (IADMAP_INTERVAL_S), reads readsb's current picture,
/run/readsb/aircraft.json, and stores one row for every aircraft that has sent
a message since the previous read -- all ranges and altitudes, with or without
a position (Mode S-only aircraft have none). Nothing is filtered here;
choosing what to show is the publisher's job.

Retention (owner's decision, 2026-09-24): keep everything until the drive
is nearly full. When free space on the drive falls below PRUNE_BELOW_GB, the
oldest rows are deleted, a day at a time, until PRUNE_TO_GB is free. The
database is created with incremental auto-vacuum so deleted space goes back
to the drive without needing a full VACUUM, which would need as much free
space as the database itself. The event logs in events/ are never pruned.

Rows are buffered and committed every 30 seconds, so the drive sees a
handful of writes a minute rather than one per read. The database lives on
/mnt/flightdata, never the SD card: the collector refuses to start if that
drive isn't mounted.

Table `positions`, one row per aircraft per read:
  t          epoch seconds of the aircraft's last message
  hex        ICAO address        flight, type, category, squawk
             (type from readsb, else looked up by address: aircraft_lookup.py)
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
import json, os, signal, sqlite3, time

import aircraft_lookup

AIRCRAFT_JSON = os.environ.get("IADMAP_AIRCRAFT_JSON", "/run/readsb/aircraft.json")
DRIVE = os.environ.get("IADMAP_DRIVE", "/mnt/flightdata")
DB_PATH = os.environ.get("IADMAP_DB", os.path.join(DRIVE, "iad-map", "flights.db"))
INTERVAL_S = float(os.environ.get("IADMAP_INTERVAL_S", "2"))
COMMIT_EVERY_S = 30
PRUNE_BELOW_GB = float(os.environ.get("IADMAP_PRUNE_BELOW_GB", "10"))
PRUNE_TO_GB = float(os.environ.get("IADMAP_PRUNE_TO_GB", "15"))
PRUNE_CHECK_S = 600

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
    # Must be set before the first table exists; a no-op on an existing database.
    db.execute("PRAGMA auto_vacuum=INCREMENTAL")
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
            ac.get("t") or aircraft_lookup.type_for(ac.get("hex")),
            ac.get("category"), ac.get("squawk"),
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


def free_gb(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e9


def checkpoint(db):
    """Give deleted pages back to the drive. Returns True if the whole WAL
    could be written back; False while a reader (a publish query) holds it."""
    # incremental_vacuum frees one page per step and Python's execute()
    # stops after the first, so run it through executescript(), which
    # steps it to the end. The file shrinks at the checkpoint afterwards.
    db.executescript("PRAGMA incremental_vacuum;")
    busy = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    return not busy


def prune(db, path, free=free_gb):
    """Delete the oldest HOUR of rows when free space is short. Returns
    (rows deleted, whether deleting more now would help).

    One hour per call, so the collector's loop is never held long (a day
    is ~2 million rows); main() calls again at the next commit while space
    is still short. Until 2026-10-24 this looped a day at a time until
    PRUNE_TO_GB was free, with nothing checking that a deletion gave space
    back -- and it does not while a publish query holds the WAL (the pages
    wait there and free space FALLS), nor when something else is filling
    the drive. With free space stuck at 9 GB one call deleted the whole
    log. Now a deletion that did not raise free space stops the pruning
    until the next check (PRUNE_CHECK_S), when the checkpoint is tried
    again before anything else is deleted."""
    before = free(path)
    if before >= PRUNE_BELOW_GB:
        return 0, False
    checkpoint(db)                  # pages from an earlier deletion may still be waiting
    held = free(path)
    if held >= PRUNE_TO_GB:
        return 0, False
    oldest = db.execute("SELECT MIN(t) FROM positions").fetchone()[0]
    if oldest is None:
        return 0, False
    n = db.execute("DELETE FROM positions WHERE t < ?", (oldest + 3600,)).rowcount
    db.commit()
    flushed = checkpoint(db)
    after = free(path)
    helped = flushed and after > held
    print(f"collector: drive low, deleted {n} rows from the oldest hour "
          f"({time.strftime('%Y-%m-%d %H:00', time.gmtime(oldest))} UTC); {after:.2f} GB free"
          + ("" if helped else "; that gave no space back (a reader holds the log, or the drive "
                               "is filling elsewhere), so no more is deleted for now"), flush=True)
    return n, helped


def main():
    if DB_PATH.startswith(DRIVE) and not os.path.ismount(DRIVE):
        raise SystemExit(f"collector: {DRIVE} is not mounted; not writing to the SD card instead")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = open_db(DB_PATH)
    insert = f"INSERT INTO positions ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})"
    buffer, last_commit, last_msg, prune_at = [], time.time(), {}, time.time() + PRUNE_CHECK_S
    # systemd stops the service with SIGTERM (every installer re-run, every
    # reboot); without this the rows buffered since the last commit -- up to
    # 30 s of readings -- died with the process. The handler only sets a
    # flag: an exception raised between executemany() and commit() would
    # insert the buffer twice.
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    while not stopping:
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
        if started >= prune_at:
            prune_at = started + PRUNE_CHECK_S
            try:
                n, more = prune(db, os.path.dirname(DB_PATH))
                if more:
                    prune_at = started + COMMIT_EVERY_S      # the next hour, after the next commit
            except sqlite3.Error as e:
                print("collector: prune failed:", e, flush=True)
        time.sleep(max(0.2, INTERVAL_S - (time.time() - started)))
    if buffer:
        db.executemany(insert, buffer)
        db.commit()
        print(f"collector: stopping; {len(buffer)} buffered rows saved", flush=True)


if __name__ == "__main__":
    main()
