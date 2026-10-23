"""Busy times (pi/busy.py): the landings and take-offs at Dulles counted per
quarter-hour on the made-up log, each hour once, and whole days on the days
the clocks change."""
import json, os, shutil, tempfile, unittest

import fixture as F
import busy as B
import publish as P


class BusyTimes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="iadmap-busy-")
        cls.state = os.path.join(cls.tmp, "busy")
        _, files, cls.final = P.build(F.NOW)
        P.write_files(files)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def counts(self):
        with open(os.path.join(F.WORK, "busy.json")) as f:
            d = json.load(f)
        return {k: (sum(v["a"]), sum(v["d"])) for k, v in d["days"].items()}, d

    def test_each_hour_counted_once_when_it_has_settled(self):
        # the log's last hour waits for the hour after it (an approach can run on into it)
        self.assertTrue(B.update(F.WORK, F.NOW, self.state, self.final))
        first, _ = self.counts()
        self.assertEqual(list(first.values()), [(6, 6)])
        # three hours on, it is counted without it; the first two are not counted again
        self.assertTrue(B.update(F.WORK, F.NOW + 3 * 3600, self.state, self.final))
        self.assertFalse(B.update(F.WORK, F.NOW + 4 * 3600, self.state, self.final))
        every, d = self.counts()
        # nine arrivals and nine departures in the made-up log; the Cessna at
        # 1,100 ft over the farm, the overflights and the ground vehicle are not movements
        self.assertEqual(list(every.values()), [(9, 9)])
        day = next(iter(d["days"].values()))
        self.assertEqual((len(day["a"]), len(day["d"])), (96, 96))
        self.assertEqual(d["complete_days"], [], "three hours is not a whole day")
        self.assertEqual((d["version"], d["tz"], d["slot_min"]), (B.VERSION, "America/New_York", 15))

    def test_low_without_a_climb_or_a_descent_is_not_a_movement(self):
        t = F.T0
        near = (B.IAD[0] + 0.02, B.IAD[1])
        paths = {
            "pass": [(t + 60 * i, near[0], near[1], 1200) for i in range(10)],          # low, never high
            "high": [(t + 60 * i, near[0], near[1], 3000) for i in range(10)],          # never low
            "land": [(t, near[0] + 0.1, near[1], 4000), (t + 300, near[0], near[1], 900)],
            "late": [(t, near[0] + 0.1, near[1], 4000), (t + 2400, near[0], near[1], 900)],  # unheard 40 min
        }
        self.assertEqual(B.movements(paths), [(t + 300, "a", "land")])

    def test_whole_days_where_the_clocks_change(self):
        self.assertEqual(B.complete_days({"2026-10-13": 24, "2026-10-14": 23}), ["2026-10-13"])
        self.assertEqual(B.complete_days({"2026-11-01": 24}), [], "the day the clocks go back has 25 hours")
        self.assertEqual(B.complete_days({"2026-11-01": 25}), ["2026-11-01"])
        self.assertEqual(B.complete_days({"2027-03-14": 23}), ["2027-03-14"], "the day they go forward has 23")

    def test_quarter_hours_are_eastern_time(self):
        self.assertEqual(B.slot(F.T0), ("2026-10-13", 8 * 4))   # 12:00 UTC is 8 AM EDT
        # 1:30 AM happens twice on 2026-11-01; both are the same quarter of the same day
        self.assertEqual(B.slot(1793511000), B.slot(1793511000 + 3600))
        self.assertEqual(B.slot(1793511000), ("2026-11-01", 6))


if __name__ == "__main__":
    unittest.main()
