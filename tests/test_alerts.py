"""Sighting alerts: worth stepping outside for, or not (owner, 2026-10-18),
the watched flight on its day, and texts that fit one SMS."""
import datetime, json, math, os, unittest
from zoneinfo import ZoneInfo

import fixture as F
import alerts as A
import farm

A.current_visibility = lambda: None            # no weather fetch in tests
A.aircraft_lookup.reg_for = lambda h: "N12345"
LIMIT = A.SIGHT_MI
EAST = 60 * math.cos(math.radians(farm.FARM[0]))   # minutes of longitude per nm


def at(nm_north=0.0, nm_east=0.0, **kw):
    ac = {"hex": "adfdf9", "lat": farm.FARM[0] + nm_north / 60, "lon": farm.FARM[1] + nm_east / EAST}
    ac.update(kw)
    return ac


class Sightings(unittest.TestCase):
    def test_heading_this_way_alerts_early_with_the_closest_point(self):
        p = A.sight_path(at(-25, 3, gs=300, track=0, baro_rate=0), 6000, LIMIT)
        self.assertIsNone(A.sight_verdict(p, LIMIT))
        in_s, slant, horiz, brg, elev, agl = p["closest"]
        self.assertGreater(in_s, 120)
        self.assertLess(horiz, 4)
        self.assertTrue(60 <= brg <= 120, brg)             # passes to the east

    def test_heading_away_at_the_edge_does_not(self):
        p = A.sight_path(at(-10, 0, gs=300, track=180, baro_rate=1500), 8000, LIMIT)
        self.assertEqual(A.sight_verdict(p, LIMIT), "heading away")

    def test_low_on_the_horizon_does_not(self):
        p = A.sight_path(at(-10, 0, gs=0), 1500, LIMIT)
        self.assertEqual(A.sight_verdict(p, LIMIT), "too low")

    def test_far_and_passing_by_does_not(self):
        p = A.sight_path(at(-30, 20, gs=300, track=0), 30000, LIMIT)
        self.assertEqual(A.sight_verdict(p, LIMIT), "not coming within sight")

    def test_text_is_one_plain_sms(self):
        p = A.sight_path(at(-25, 3, gs=300, track=0, baro_rate=0), 6000, LIMIT)
        who = ("Air Force One", "92-9000", "Boeing VC-25A", None)
        ev = A.sighting_event(at(-25, 3, gs=300, track=0, alt_baro=6400), F.NOW, p, 6000, who, LIMIT)
        title, body = A.compose_sighting(ev)
        line = A.text_line(title)
        self.assertLessEqual(len(line), A.TEXT_MAX)
        self.assertTrue(line.isascii())
        self.assertIn("closest in about", line)


class Loop(unittest.TestCase):
    """watch() itself over scripted receiver readings, one every 2 s."""

    def run_reads(self, reads):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="iadmap-alerts-")
        path = os.path.join(tmp, "aircraft.json")
        queued, it = [], iter(reads)

        def write_next(*_):
            try:
                snap = next(it)
            except StopIteration:
                raise KeyboardInterrupt            # out of readings: stop the loop
            with open(path, "w") as f:
                json.dump(snap, f)

        saved = (A.AIRCRAFT_JSON, A.queue_alert, A.push_pending, A.append_event, A.time.sleep, A.current_altim)
        A.AIRCRAFT_JSON, A.queue_alert = path, lambda ev, test=False: queued.append(ev)
        A.push_pending, A.append_event, A.time.sleep = (lambda: True), (lambda ev, name="": None), write_next
        A.current_altim = lambda: None
        try:
            write_next()
            with self.assertRaises(KeyboardInterrupt):
                A.watch()
        finally:
            (A.AIRCRAFT_JSON, A.queue_alert, A.push_pending, A.append_event, A.time.sleep, A.current_altim) = saved
        return queued

    def track(self, hexid, start_nm_north, nm_east, track, gs, alt, n, flight=""):
        """n readings 2 s apart of an aircraft flying straight from start_nm_north (south of the farm when negative)."""
        reads = []
        for i in range(n):
            d = gs * 2 * i / 3600
            north = start_nm_north + d * math.cos(math.radians(track))
            east = nm_east + d * math.sin(math.radians(track))
            reads.append({"now": F.NOW + 2 * i, "aircraft": [dict(at(north, east), hex=hexid, flight=flight, alt_baro=alt,
                                                                gs=gs, track=track, seen=0, seen_pos=0)]})
        return reads

    def test_air_force_one_coming_by_alerts_once(self):
        queued = self.run_reads(self.track("adfdf9", -25, 3, 0, 300, 6400, 200))
        self.assertEqual(len(queued), 1)
        self.assertGreater(queued[0]["closest"]["in_s"], 60)

    def test_air_force_one_leaving_does_not(self):
        queued = self.run_reads(self.track("adfdf9", -10, 0, 180, 300, 9000, 120))
        self.assertEqual(queued, [])

    def test_an_ordinary_airliner_does_not(self):
        queued = self.run_reads(self.track("a12345", -25, 3, 0, 300, 6400, 200, flight="DAL123"))
        self.assertEqual(queued, [])


class WatchedFlight(unittest.TestCase):
    def setUp(self):
        self.flights = A.load_watch_flights()
        z = ZoneInfo("America/New_York")
        self.day = datetime.datetime(2026, 11, 21, 17, 0, tzinfo=z).timestamp()
        self.other = datetime.datetime(2026, 11, 22, 17, 0, tzinfo=z).timestamp()

    def test_file_is_valid(self):
        with open(A.WATCH_FLIGHTS_JSON) as f:
            d = json.load(f)
        for callsign, e in d["flights"].items():
            self.assertEqual(callsign, callsign.strip().upper())
            for day in e.get("dates", []):
                datetime.date.fromisoformat(day)

    def test_ua2853_only_on_its_day(self):
        ac = {"hex": "abc123", "flight": "UAL2853 ", "t": "B739"}
        self.assertIsNotNone(A.watched(ac, {}, self.flights, self.day))
        self.assertIsNone(A.watched(ac, {}, self.flights, self.other))

    def test_west_lane_arrival_alerts_minutes_ahead(self):
        lane = -0.5                                 # 0.5 nm west of the farm, southbound
        p = A.sight_path(at(20, lane, gs=220, track=180, baro_rate=-800), 7500, LIMIT)
        self.assertIsNone(A.sight_verdict(p, LIMIT))
        in_s, slant, horiz, brg, elev, agl = p["closest"]
        self.assertGreater(in_s, 180)
        self.assertLess(horiz, 1.5)
        self.assertGreater(elev, 30)


if __name__ == "__main__":
    unittest.main()
