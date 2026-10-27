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

    def test_lost_before_the_runway_counts_only_on_a_dulles_runway_line(self):
        # from 10-27 ("Include them"): 94% of the FAA's count was found; most of the rest are
        # lost before 1,500 ft, or first heard after it, on a Dulles runway's line
        t, (la, lo) = F.T0, B.IAD
        west = lo - 0.015                       # the west runway, ~0.7 nm off the reference point
        paths = {
            # landing south, last heard at 2,000 ft 4 nm out on the line
            "lost": [(t, la + 0.15, west, 4000), (t + 90, la + 0.11, west, 3000), (t + 180, la + 0.07, west, 2000)],
            # take-off south, first heard at 2,000 ft 2.4 nm out, climbing away along the line
            "late": [(t, la - 0.04, west, 2000), (t + 90, la - 0.08, west, 3200), (t + 180, la - 0.12, west, 4500)],
            # descending towards Dulles but across the runway lines, 5 nm east
            "across": [(t, la + 0.02, lo + 0.25, 4000), (t + 90, la + 0.02, lo + 0.15, 3000),
                       (t + 180, la + 0.02, lo + 0.10, 2000)],
            # landing at Leesburg, 9 nm north-west, on its runway 17 (the 9-30 pitfall)
            "leesburg": [(t, 39.13, -77.575, 1500), (t + 90, 39.10, -77.563, 900), (t + 180, 39.085, -77.559, 400)],
            # a landing the main rule finds is not counted twice
            "found": [(t, la + 0.15, west, 4000), (t + 90, la + 0.07, west, 2000), (t + 180, la + 0.03, west, 900)],
        }
        self.assertEqual(B.movements(paths), [(t, "d", "late"), (t + 180, "a", "found"), (t + 180, "a", "lost")])

    def test_a_new_rule_recounts_the_kept_days_and_keeps_the_rest(self):
        work = os.path.join(self.tmp, "work-v")
        shutil.copytree(F.WORK, work)
        state = os.path.join(self.tmp, "busy-v")
        os.makedirs(state)
        # the local day of the log: its early hours named as kept (no files, nothing in them), so the
        # day starts with the first kept hour, as on the Pi where whole local days leave the window
        final = set(self.final) | {f"t/20261013/{h:02d}.json" for h in range(4, 12)}
        earlier = {"a": [1] * 96, "d": [2] * 96}
        with open(os.path.join(state, "state.json"), "w") as f:
            json.dump({"version": B.VERSION - 1, "hours": [], "days": {"2026-10-10": earlier, "2026-10-13": earlier},
                       "hours_per_day": {"2026-10-10": 24, "2026-10-13": 3}}, f)
        with open(os.path.join(work, "busy.json"), "w") as f:
            f.write("previous")
        # the recount runs out of time: the page keeps the counts it had
        self.assertFalse(B.update(work, F.NOW + 3 * 3600, state, final, budget_s=-1))
        with open(os.path.join(work, "busy.json")) as f:
            self.assertEqual(f.read(), "previous")
        self.assertTrue(B.update(work, F.NOW + 3 * 3600, state, final))
        with open(os.path.join(work, "busy.json")) as f:
            d = json.load(f)
        self.assertEqual(d["earlier_rule_days"], ["2026-10-10"])
        self.assertEqual(d["days"]["2026-10-10"], earlier, "a day past the window keeps its count")
        self.assertEqual((sum(d["days"]["2026-10-13"]["a"]), sum(d["days"]["2026-10-13"]["d"])), (9, 9),
                         "a kept day is counted again, from nothing")
        self.assertEqual(d["version"], B.VERSION)

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
