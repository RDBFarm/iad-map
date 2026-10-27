"""publish.py on the made-up log: what the live map gets, and that a settled
hour is built once and never changes."""
import json, os, unittest

import fixture as F
import publish as P


class Publish(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index, cls.files, cls.final = P.build(F.NOW)

    def test_hours_built_and_settled(self):
        hours = sorted(n for n in self.files if n.startswith("h/"))
        self.assertEqual(len(hours), F.HOURS)
        self.assertEqual(set(hours), {n for n in self.final if n.startswith("h/")})

    def test_live_map_reaches_75_miles_and_the_history_map_stays_at_50(self):
        far = [r for r in P.load_points(F.T0, F.T0 + F.HOURS * 3600) if r[8].startswith("AAL")]
        self.assertTrue(far, "the 57-72 mile airliner should be on the live map")
        self.assertTrue(all(50 < F.miles(F.IAD, (r[2], r[3])) <= 75 for r in far))
        heat = [p for h in range(F.T0, F.T0 + F.HOURS * 3600, 3600) for p in P.heat_points_for_hour(h)]
        self.assertTrue(heat)
        self.assertFalse([p for p in heat if p[1].startswith("a4")], "the history map keeps 50 miles")
        self.assertTrue(all(F.miles(F.IAD, (p[2], p[3])) <= 50.01 for p in heat))
        in_files = lambda h: any(h.encode() in b for n, b in self.files.items() if n.startswith("h/"))
        self.assertTrue(in_files("a40000"), "the far airliner is in the live map's hour files")
        self.assertFalse(in_files("a50000"), "the 35,000 ft overflight is not")
        self.assertFalse(in_files("a60000"), "nor the ground vehicle")

    def test_nothing_above_15000_ft_or_on_the_ground(self):
        pts = P.load_points(F.T0, F.T0 + F.HOURS * 3600)
        self.assertFalse([r for r in pts if r[1].startswith("a5")], "the 35,000 ft overflight is not on the map")
        mapped = {r[1] for r in pts if r[4] <= P.MAX_ALT_FT}
        self.assertTrue({h for h in mapped if h.startswith("a1")}, "arrivals are on the map")

    def test_a_garbled_altitude_is_unknown_not_low(self):
        # BP02 (10-26): a T-38 at 28,000 ft read -1,000 ft for a few seconds and showed on
        # the live map as a "military fighter"; below tracks.MIN_ALT_FT the height is unknown
        in_files = lambda prefix: any(b"ae63f3" in b for n, b in self.files.items() if n.startswith(prefix))
        self.assertFalse(in_files("h/"), "not on the live map")
        self.assertFalse(in_files("o/"), "nor among the take-offs")
        self.assertFalse([r for r in P.load_points(F.T0, F.T0 + F.HOURS * 3600) if r[1] == "ae63f3"])
        self.assertFalse([p for h in range(F.T0, F.T0 + F.HOURS * 3600, 3600) for p in P.heat_points_for_hour(h)
                          if p[1] == "ae63f3"], "nor the history heat map")
        track = [json.loads(b)["ac"].get("ae63f3") for n, b in self.files.items() if n.startswith("t/")]
        pts = [p for t in track if t for p in t[2]]
        self.assertTrue(pts, "its whole path is still recorded")
        self.assertTrue(all(p[3] is None or p[3] >= 28000 for p in pts), "with the garbled stretch's height unknown")
        self.assertIn(None, [p[3] for p in pts])

    def test_a_settled_hour_is_read_back_unchanged(self):
        P.write_files(self.files)
        index2, files2, _ = P.build(F.NOW + 900, final=self.final)
        for name in self.final:
            if name in files2:
                self.assertEqual(files2[name], self.files[name], name)

    def test_rebuilding_gives_the_same_bytes(self):
        _, again, _ = P.build(F.NOW)
        for name in self.files:
            self.assertEqual(again[name], self.files[name], name)

    def test_a_failing_git_command_logs_why(self):
        import contextlib, io, subprocess
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(subprocess.CalledProcessError):
            P.git("rev-parse", "--verify", "no-such-ref^{commit}")
        self.assertIn("publish: git rev-parse failed (exit", out.getvalue())
        self.assertRegex(out.getvalue(), r"fatal|Needed a single revision|not a git repository")

    def test_installed_version_is_reported(self):
        self.assertIsNone(P.installed_version())       # no VERSION file in the repo
        self.assertIn("positions", self.index["meta"])

    def test_take_offs_have_their_own_hour_files(self):
        # o/ holds the airborne positions the arrival rules drop (owner, 10-24:
        # a toggle on the live map); the fixture's DAL departures climb out of Dulles
        deps = sorted(n for n in self.files if n.startswith("o/"))
        self.assertEqual(len(deps), F.HOURS)
        self.assertEqual([c["name"] for c in self.index["deps"]], deps)
        self.assertEqual(set(deps), {n for n in self.final if n.startswith("o/")}, "settled like the h/ files")
        in_files = lambda prefix, kind: any(prefix.encode() in self.files[n] for n in self.files if n.startswith(kind + "/"))
        self.assertTrue(in_files("a20000", "o"), "a departure's climb is in the take-off files")
        self.assertFalse(in_files("a10000", "o"), "an arrival is not")
        self.assertTrue(in_files("a10000", "h"))
        self.assertFalse(in_files("a60000", "o"), "nor the ground vehicle")
        self.assertGreater(self.index["meta"]["takeoff_positions"], 0)

    def test_the_over_the_farm_figure_is_the_forecasts_rule(self):
        # one rule for the map and the forecast (owner, 10-24): within farmcast.NEAR_MI
        # below farmcast.BELOW_FT per hour file; the Cessna crosses the farm at 1,100 ft
        import farmcast as C
        hours = [P.tracks.parse_name(n)[1] for n in self.files if n.startswith("t/")]
        near = P.near_summary(hours, lambda h: P.read_track_body(P.tracks.hour_name("t", h), self.files))
        self.assertEqual(near["hours"], F.HOURS)
        self.assertEqual((near["radius_mi"], near["below_ft"], near["props"]), (C.NEAR_MI, C.BELOW_FT, "included"))
        direct = sum(sum(1 for d, alt in C.closest(json.loads(self.files[n])).values() if d <= C.NEAR_MI and alt < C.BELOW_FT)
                     for n in self.files if n.startswith("t/"))
        self.assertEqual(near["aircraft"], direct)
        self.assertGreaterEqual(near["aircraft"], F.HOURS, "the Cessna, every hour")
        # farmcast's own count for an hour is used when it has one
        rec = {hours[0]: ["S", 3, 7, 1]}
        self.assertEqual(P.near_summary(hours, lambda h: P.read_track_body(P.tracks.hour_name("t", h), self.files), rec)["aircraft"],
                         direct - sum(1 for d, alt in C.closest(json.loads(self.files[P.tracks.hour_name("t", hours[0])])).values()
                                      if d <= C.NEAR_MI and alt < C.BELOW_FT) + 7)
        # a finished day's file carries the figure and its take-off files
        P.write_files(self.files)
        real = P.fetch_weather
        try:
            P.fetch_weather = lambda start, hours=None, on_fail=[]: []
            day = json.loads(P.day_file(P.local_day(F.T0), list(self.files), F.T0 + 86400, set(self.final), need_wx=False))
        finally:
            P.fetch_weather = real
        self.assertEqual(day["farm"]["near"]["aircraft"], direct)
        self.assertEqual(len(day["deps"]), F.HOURS)

    def test_a_jet_on_final_tracks_true_degrees_and_is_dulles(self):
        # 8 statute miles out at 2,500 ft tracking 181° true, what Dulles's
        # southbound landings measure. The runway table held the runway
        # numbers read as degrees (19/199), and the 18° alignment penalty
        # scored this point "en route".
        lat = F.IAD[0] + 8 / 69.0
        self.assertEqual(P.classify_airport(lat, F.IAD[1], 2500, "XYZ123", "A3", 181), "KIAD")
        self.assertEqual(P.classify_airport(lat, F.IAD[1], 2500, "XYZ123", "A3", 1), "KIAD")

    def test_a_day_file_waits_for_its_weather(self):
        # the fixture's local day is over; its file should carry the day's weather
        end = F.T0 + 86400
        name = f"d/{P.local_day(F.T0).isoformat()}.json"
        report = {"epoch": F.T0 + 1800, "drct": 350, "sknt": 8, "compass": "N", "altim_hpa": 1012.6}
        real = P.fetch_weather
        try:
            P.fetch_weather = lambda start, hours=None, on_fail=[]: on_fail     # the fetch fails
            files = dict(self.files)
            P.archive({}, files, set(self.final), end)
            self.assertNotIn(name, files, "no day file without its weather")
            P.fetch_weather = lambda start, hours=None, on_fail=[]: [report]  # the next run: it works
            final = P.archive({}, files, set(self.final), end)
            self.assertIn(name, final)
            self.assertEqual(json.loads(files[name])["wx"], [report])
            # a day already on disk with no weather (2026-10-21) is built again while recent
            P.write_files({name: json.dumps({**json.loads(files[name]), "wx": []}).encode()})
            files[name] = None
            P.archive({}, files, final, end)
            self.assertEqual(json.loads(files[name])["wx"], [report])
            # and after WX_RETRY_DAYS it goes out without, rather than never
            P.fetch_weather = lambda start, hours=None, on_fail=[]: on_fail
            files = dict(self.files)
            P.archive({}, files, set(self.final), end + P.WX_RETRY_DAYS * 86400 + 3600)
            self.assertIn(name, files)
            self.assertEqual(json.loads(files[name])["wx"], [])
        finally:
            P.fetch_weather = real
            try:
                os.remove(os.path.join(P.WORK_DIR, name))
            except OSError:
                pass

    def test_an_empty_settled_file_is_not_final(self):
        # a power cut during a write can leave a frozen hour's file empty;
        # it was then read back and pushed empty for 30 days
        P.write_files(self.files)
        name = sorted(n for n in self.final if n.startswith("h/"))[0]
        os.makedirs(os.path.dirname(P.FINAL_LIST), exist_ok=True)
        with open(P.FINAL_LIST, "w") as f:
            f.write("".join(n + "\n" for n in sorted(self.final)))
        try:
            self.assertEqual(P.read_final(), set(self.final))
            open(os.path.join(P.WORK_DIR, name), "wb").close()
            self.assertEqual(P.read_final(), set(self.final) - {name})
            self.assertFalse([f for f in os.listdir(os.path.dirname(os.path.join(P.WORK_DIR, name)))
                              if f.endswith(".tmp")], "files are written whole, through a .tmp")
        finally:
            os.remove(P.FINAL_LIST)
            P.write_files({name: self.files[name]})


if __name__ == "__main__":
    unittest.main()
