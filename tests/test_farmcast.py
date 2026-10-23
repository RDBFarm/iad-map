"""The farm forecast on the Pi (pi/farmcast.py): its numbers from the made-up
log, the winds, one forecast kept each morning, and the record by days ahead."""
import datetime, json, os, shutil, tempfile, unittest

import fixture as F
import farmcast as C
import publish as P

TZ = C.LOCAL


def at(y, mo, d, h=0, mi=0):
    return int(datetime.datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp())


def wx_for(hours, deg=180, kt=10):
    return [{"epoch": h + 1800, "drct": deg, "sknt": kt, "compass": "S"} for h in hours]


class Fetches:
    """Stands in for the network: Open-Meteo answers with `om`, the archive with `iem`."""
    def __init__(self, om=None, iem=None):
        self.om, self.iem, self.calls = om, iem, []

    def __call__(self, url, timeout=30):
        self.calls.append(url)
        body = self.om if "open-meteo" in url else self.iem
        if body is None:
            raise OSError("no network in the tests")
        return body


def ready_state(state_dir, start, days=15):
    """A state that has counted `days` days before `start`: south flow with wind from the south
    in the afternoons (8 of 10 landings over the farm), north otherwise (none)."""
    hours, wind = {}, {}
    for h in range(start - days * 86400, start, 3600):
        south = 12 <= C.local(h).hour < 18
        hours[str(h)] = ["S" if south else "N", 10, 8 if south else 0, 2]
        wind[str(h)] = [180 if south else 0, 10]
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "state.json"), "w") as f:
        json.dump({"version": C.VERSION, "hours": hours, "wind": wind, "forecasts": {}, "no_wind": []}, f)


def busy_json(path, first, days=28, per_quarter=1):
    """busy.json with `days` whole days from `first`, `per_quarter` landings every 15 minutes."""
    ds = [(first + datetime.timedelta(days=i)).isoformat() for i in range(days)]
    with open(path, "w") as f:
        json.dump({"complete_days": ds, "days": {d: {"a": [per_quarter] * 96, "d": [0] * 96} for d in ds}}, f)


class FarmForecast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, files, cls.final = P.build(F.NOW)
        P.write_files(files)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="iadmap-farmcast-")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hours_counted_from_the_made_up_log(self):
        hours = [F.T0 + 3600 * i for i in range(F.HOURS)]
        net = Fetches()
        self.assertTrue(C.update(F.WORK, F.NOW + 3 * 3600, os.path.join(self.tmp, "fc"), self.final, wx_for(hours), net))
        with open(os.path.join(self.tmp, "fc", "state.json")) as f:
            st = json.load(f)
        # each hour: landing south (the arrivals come down the lane past the farm), 3 landings,
        # 4 aircraft within 2 miles below 6,000 ft (those 3 and the Cessna at 1,100 ft), none above
        self.assertEqual(st["hours"], {str(h): ["S", 3, 4, 0] for h in hours})
        self.assertEqual(st["wind"], {str(h): [180, 10] for h in hours})
        with open(os.path.join(F.WORK, "farm-model.json")) as f:
            m = json.load(f)
        self.assertEqual(m["share"]["S"], round(12 / 9, 3))
        self.assertFalse(m["ready"])
        self.assertEqual(net.calls, [], "winds were in the reports; not ready, so no forecast fetched")
        # nothing new: nothing written
        self.assertFalse(C.update(F.WORK, F.NOW + 3 * 3600, os.path.join(self.tmp, "fc"), self.final, wx_for(hours), net))

    def test_winds_from_the_archive_once(self):
        hours = [F.T0 + 3600 * i for i in range(F.HOURS)]
        row = lambda h, d: datetime.datetime.fromtimestamp(h + 1800, datetime.timezone.utc).strftime(f"IAD,%Y-%m-%d %H:%M,{d},7.00")
        net = Fetches(iem="station,valid,drct,sknt\n" + "\n".join([row(hours[0], "170.00"), row(hours[1], "M")]) + "\n")
        state = os.path.join(self.tmp, "fc")
        C.update(F.WORK, F.NOW + 9 * 3600, state, self.final, [], net)
        C.update(F.WORK, F.NOW + 10 * 3600, state, self.final, [], net)
        with open(os.path.join(state, "state.json")) as f:
            st = json.load(f)
        self.assertEqual(st["wind"], {str(hours[0]): [170.0, 7.0], str(hours[1]): [None, 7.0]})
        self.assertEqual(st["no_wind"], [hours[2]], "the archive had nothing for it")
        self.assertEqual(len(net.calls), 1, "each hour looked up once")

    def test_winds(self):
        h = at(2026, 10, 20, 14)
        reports = [(h + 1800 + 1500, 200, 8), (h + 1800 - 600, 190, 6), (h + 3600 + 1800 + 2400, 10, 5)]
        self.assertEqual(C.winds_for([h, h + 3600], reports), {h: [190, 6]}, "nearest within 30 minutes")
        self.assertEqual(C.reports_from_wx([{"epoch": h, "drct": 0, "sknt": 4, "compass": "VRB"}]), [(h, None, 4)])
        self.assertEqual(C.along(None, 6), 0.0, "variable counts as calm")
        self.assertAlmostEqual(C.along(C.AXIS_DEG, 7), 7.0)
        self.assertLess(C.along(0, 7), 0)
        iem = "station,valid,drct,sknt\nIAD,2026-10-20 18:52,170.00,9.00\nIAD,2026-10-20 19:52,M,4.00\nIAD,2026-10-20 20:52,M,M\n"
        self.assertEqual(C.reports_from_iem(iem), [(at(2026, 10, 20, 14, 52), 170.0, 9.0), (at(2026, 10, 20, 15, 52), None, 4.0)])

    def test_numbers(self):
        records = {at(2026, 10, 20, i): ["S" if i < 10 else "N", 10, 8 if i < 10 else 0, 3] for i in range(20)}
        winds = {h: [180 if r[0] == "S" else 0, 10] for h, r in records.items()}
        m = C.model(records, winds, "test")
        self.assertEqual(m["share"], {"S": 0.8, "N": 0.0})
        self.assertEqual(m["above_per_hour"], {"S": 3.0, "N": 3.0})
        self.assertEqual([b["p"] for b in m["p_south"]], [0.0, None, None, 1.0])
        self.assertEqual(m["hours"]["south"], 10)

    def test_one_forecast_kept_each_morning(self):
        start = at(2026, 10, 20)
        state = os.path.join(self.tmp, "fc")
        ready_state(state, start)
        busy_json(os.path.join(self.work, "busy.json"), datetime.date(2026, 9, 20))   # 4 landings an hour
        times = [start + 3600 * i for i in range(7 * 24)]
        net = Fetches(om=json.dumps({"hourly": {"time": times, "wind_speed_10m": [10] * len(times),
                                                 "wind_direction_10m": [180] * len(times)}}))
        C.update(self.work, at(2026, 10, 20, 5, 50), state, [], [], net)
        self.assertFalse([u for u in net.calls if "open-meteo" in u], "not before 6 AM")
        C.update(self.work, at(2026, 10, 20, 6, 10), state, [], [], net)
        C.update(self.work, at(2026, 10, 20, 9, 10), state, [], [], net)
        self.assertEqual(len([u for u in net.calls if "open-meteo" in u]), 1, "once a day")
        with open(os.path.join(state, "state.json")) as f:
            kept = json.load(f)["forecasts"]["2026-10-20"]
        hours = {int(h): v for h, v in kept["hours"].items()}
        self.assertEqual(min(hours), at(2026, 10, 20, 6), "from the hour under way")
        self.assertEqual(len(hours), 7 * 24 - 6)
        with open(os.path.join(self.work, "farm-model.json")) as f:
            m = json.load(f)
        self.assertTrue(m["ready"])
        self.assertAlmostEqual(hours[at(2026, 10, 21, 15)][2], 4 * m["share"]["S"], places=3)   # south wind: p = 1
        self.assertEqual(m["score"]["forecasts"], 1)

    def test_record_by_days_ahead(self):
        issued = datetime.date(2026, 10, 20)
        hours = range(at(2026, 10, 20, 6), at(2026, 10, 23), 3600)
        forecasts = {issued.isoformat(): {"issued": at(2026, 10, 20, 6, 5), "hours": {str(h): [180, 10, 2.0] for h in hours}}}
        records = {h: ["S", 3, 3, 0] for h in hours if h != at(2026, 10, 22, 20)}    # one hour not counted
        s = C.score(forecasts, records, at(2026, 10, 23, 9))
        by = {x["lead"]: x for x in s["by_lead"]}
        self.assertEqual({k: v["blocks"] for k, v in by.items()}, {0: 3, 1: 4, 2: 3})
        self.assertEqual(by[1]["median_err"], 6.0)               # 12 forecast, 18 came
        self.assertEqual((by[1]["median_forecast"], by[1]["median_actual"]), (12.0, 18))
        self.assertEqual(C.score(forecasts, records, at(2026, 12, 1))["forecasts"], 0, "only the last 28 days")

    def test_blocks_on_clock_change_days(self):
        self.assertEqual(len(C.block_hours(datetime.date(2026, 10, 20), 0)), 6)
        self.assertEqual(len(C.block_hours(datetime.date(2026, 11, 1), 0)), 7)
        self.assertEqual(len(C.block_hours(datetime.date(2027, 3, 14), 0)), 5)
        self.assertEqual(len(C.block_hours(datetime.date(2026, 10, 20), 3)), 6)

    def test_usual_landings_like_the_page(self):
        path = os.path.join(self.tmp, "busy.json")
        busy_json(path, datetime.date(2026, 10, 5), days=3, per_quarter=2)       # Mon-Wed only
        with open(path) as f:
            u = C.usual_landings(json.load(f))
        self.assertEqual(u[0][0], 8.0)
        self.assertEqual(u[5][0], 8.0, "a weekday with no whole day takes every day's average")
        self.assertIsNone(C.usual_landings({"complete_days": [], "days": {}}))


if __name__ == "__main__":
    unittest.main()
