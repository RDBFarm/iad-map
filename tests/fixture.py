"""A small, made-up receiver log for the tests, and the environment the Pi
programs read their paths from. Imported first by every test module, so the
programs under pi/ see a temporary drive, never /mnt/flightdata.

Three hours at the collector's 2 s cadence, built from straight legs:
  - south-flow arrivals down the west lane past the farm into Dulles
  - departures climbing out of Dulles to the north-west
  - a Cessna crossing the farm's land low
  - an airliner 57-72 statute miles out at 8,000 ft (inside the live map's 75
    miles, outside the history map's 50)
  - a jet crossing 150 miles north at 35,000 ft (the wide map only)
  - a ground vehicle at Dulles (shown nowhere)
  - a T-38 at 28,000 ft 40 miles west whose altitude reads -1,000 ft for 20 s
    (BP02 on 10-26: a garbled reading put it on the live map as a "fighter";
    tracks.MIN_ALT_FT keeps it off everything under 15,000 ft)
"""
import math, os, sqlite3, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI = os.path.join(ROOT, "pi")
T0 = 1791892800                  # 2026-10-13 12:00 UTC, an hour boundary
HOURS = 3
NOW = T0 + HOURS * 3600 + 11 * 60   # every hour of the log settled (SETTLE_S is 10 min)
IAD = (38.9444, -77.4558)
FARM = (39.1506, -77.4612)
WEST_LANE_LON = FARM[1] - 0.5 / (60 * math.cos(math.radians(FARM[0])))
FAR = [(39.44, -76.25), (39.44, -76.60)]      # 57-72 statute miles ENE of Dulles

_TMP = tempfile.mkdtemp(prefix="iadmap-test-")
DRIVE = os.path.join(_TMP, "drive")
DB = os.path.join(DRIVE, "iad-map", "flights.db")
WORK = os.path.join(DRIVE, "iad-map", "publish")
os.makedirs(WORK, exist_ok=True)
os.environ.update(IADMAP_DB=DB, IADMAP_PUBLISH_DIR=WORK, IADMAP_DRIVE=DRIVE,
                  IADMAP_EVENTS_DIR=os.path.join(DRIVE, "iad-map", "events"),
                  IADMAP_AIRCRAFT_DB=os.path.join(DRIVE, "iad-map", "aircraft.db"),
                  IADMAP_FARM_GROUND=os.path.join(DRIVE, "iad-map", "farm_ground.json"))
if PI not in sys.path:
    sys.path.insert(0, PI)


def miles(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(x))


def leg_rows(hexid, flight, actype, start, legs, category="A3", ground=False):
    """legs: [(lat, lon, alt_ft, seconds from start)], flown straight between them, a row every 2 s."""
    rows = []
    for (la1, lo1, a1, t1), (la2, lo2, a2, t2) in zip(legs, legs[1:]):
        dist_nm = miles((la1, lo1), (la2, lo2)) / 1.15078
        gs = dist_nm / (t2 - t1) * 3600
        trk = (math.degrees(math.atan2((lo2 - lo1) * math.cos(math.radians(la1)), la2 - la1)) + 360) % 360
        rate = round((a2 - a1) / (t2 - t1) * 60)
        for s in range(t1, t2, 2):
            f = (s - t1) / (t2 - t1)
            t = start + s
            if not T0 <= t < T0 + HOURS * 3600:
                continue
            alt = round(a1 + (a2 - a1) * f)
            rows.append((t, hexid, flight, actype, category, "1200" if actype == "C172" else "2345",
                         la1 + (la2 - la1) * f, lo1 + (lo2 - lo1) * f, t, alt, 1 if ground else 0,
                         alt, round(gs, 1), round(trk, 1), rate, rate, -20.0, 0, "none", None))
    return rows


def make_db(path=DB):
    sys.path.insert(0, PI)
    import collector
    if os.path.exists(path):
        os.remove(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(collector.SCHEMA)
    rows = []
    for k in range(HOURS * 3):                     # every 20 minutes
        s = k * 1200
        rows += leg_rows(f"a1{k:04x}", f"UAL{100 + k}", "B739", T0 + s - 300, [
            (FARM[0] + 40 / 60, WEST_LANE_LON, 11000, 0), (FARM[0], WEST_LANE_LON, 3500, 540),
            (IAD[0] + 0.02, WEST_LANE_LON, 400, 800), (IAD[0] + 0.015, WEST_LANE_LON, 320, 812)])
        rows += leg_rows(f"a2{k:04x}", f"DAL{200 + k}", "A321", T0 + s + 600, [
            (IAD[0], IAD[1], 400, 0), (IAD[0] + 0.25, IAD[1] - 0.45, 15000, 600),
            (IAD[0] + 0.6, IAD[1] - 1.1, 24000, 900)])
    for k in range(HOURS):
        s = k * 3600
        rows += leg_rows(f"a3{k:04x}", f"N12{k}AB", "C172", T0 + s + 120, [
            (FARM[0], FARM[1] - 0.25, 1100, 0), (FARM[0], FARM[1] + 0.25, 1100, 330)], category="A1")
        rows += leg_rows(f"a4{k:04x}", f"AAL{300 + k}", "B738", T0 + s + 900, [
            (FAR[0][0], FAR[0][1], 8000, 0), (FAR[1][0], FAR[1][1], 8000, 300)])
        rows += leg_rows(f"a6{k:04x}", "", "", T0 + s, [
            (IAD[0], IAD[1], 0, 0), (IAD[0] + 0.01, IAD[1], 0, 1200)], category="C1", ground=True)
    for k in range(HOURS * 2):                     # every 30 minutes
        rows += leg_rows(f"a5{k:04x}", f"JBU{400 + k}", "A320", T0 + k * 1800, [
            (41.0, -79.5, 35000, 0), (41.0, -76.0, 35000, 1800)])
    garbled = (IAD[0] - 0.1, IAD[1] - 0.8)        # ~40 statute miles west of Dulles
    rows += leg_rows("ae63f3", "BP02", "T38", T0 + 1200, [
        (garbled[0] - 0.3, garbled[1] + 0.3, 28000, 0), (garbled[0], garbled[1], 28000, 150)], category="A6")
    rows += leg_rows("ae63f3", "BP02", "T38", T0 + 1350, [
        (garbled[0], garbled[1], -1000, 0), (garbled[0] + 0.04, garbled[1] - 0.04, -1000, 20)], category="A6")
    rows += leg_rows("ae63f3", "BP02", "T38", T0 + 1370, [
        (garbled[0] + 0.04, garbled[1] - 0.04, 28000, 0), (garbled[0] + 0.3, garbled[1] - 0.3, 28000, 150)],
        category="A6")
    db.executemany(f"INSERT INTO positions ({','.join(collector.COLUMNS)}) "
                   f"VALUES ({','.join('?' * len(collector.COLUMNS))})", rows)
    db.commit()
    db.close()
    return len(rows)


ROWS = make_db()
