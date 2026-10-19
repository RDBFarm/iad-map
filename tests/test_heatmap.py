"""The history heat maps (near: 50 miles, under 15,000 ft; wide: 200 miles,
every height) on the made-up log."""
import json, os, shutil, tempfile, unittest

import fixture as F
import heatmap as H
import publish as P


def cell_of(area, lat, lon):
    r, c = H.cell(lat, lon, area)
    return int(r) * area.cols + int(c)


def grid_values(path, area, cells):
    g = H.Grid(path, area)
    try:
        return [g.acc[k] for k in cells]
    finally:
        g.close()


class HeatMaps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="iadmap-heat-")
        cls.work = os.path.join(cls.tmp, "work")
        os.makedirs(cls.work)
        cls.near_state, cls.wide_state = os.path.join(cls.tmp, "near"), os.path.join(cls.tmp, "wide")
        start = P.log_start()
        for _ in range(3):                        # a few runs, as the Pi would over successive publishes
            H.update(cls.work, P.heat_points_for_hour, F.NOW + 31 * 3600, cls.near_state, db_start=start)
            H.update_wide(cls.work, P.wide_points_for_hour, F.NOW, cls.wide_state, db_start=start)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_both_maps_published(self):
        with open(os.path.join(self.work, "heat.json")) as f:
            near = json.load(f)
        with open(os.path.join(self.work, "heat-wide.json")) as f:
            wide = json.load(f)
        self.assertEqual(near["version"], H.VERSION)
        self.assertEqual(wide["version"], H.WIDE_VERSION)
        self.assertTrue(os.path.exists(os.path.join(self.work, "heat", "v2", "all", "all", "a.png")))
        self.assertTrue(os.path.exists(os.path.join(self.work, "heat", "wide", "all.png")))
        self.assertGreater(wide["aircraft_hours"], 0)

    def test_near_map_stops_at_50_miles(self):
        # the far airliner's path where it is inside the near grid's box but beyond 50 miles
        k = cell_of(H.NEAR, F.FAR[1][0], F.FAR[1][1] + 0.03)
        for cls in H.CLASSES:
            path = os.path.join(self.near_state, "g", "all", "all", cls + ".f32")
            if os.path.exists(path):
                self.assertEqual(grid_values(path, H.NEAR, [k]), [0.0], cls)

    def test_wide_map_has_the_high_overflight(self):
        k = cell_of(H.WIDE, 41.0, -78.0)
        total = sum(grid_values(os.path.join(self.wide_state, s + ".f32"), H.WIDE, [k])[0]
                    for s in "sb" if os.path.exists(os.path.join(self.wide_state, s + ".f32")))
        self.assertGreater(total, 0)

    def test_once_per_cell_per_aircraft_hour(self):
        # a path that doubles back over itself lights each cell once
        pts = [(F.T0 + 2 * i, "x1", 39.0, -77.4 + 0.0005 * (i if i < 50 else 100 - i), "B738", 0, "a")
               for i in range(100)]
        parts, n = H.hour_cells(pts)
        self.assertEqual(n, 1)
        self.assertTrue(all(w == H.WEIGHT["M"] for cells in parts.values() for w in cells.values()))

    def test_parts_add_up(self):
        pts = P.heat_points_for_hour(F.T0)
        parts, _ = H.hour_cells(pts)
        by_cell = {}
        for cells in parts.values():
            for k, w in cells.items():
                by_cell[k] = by_cell.get(k, 0) + w
        lit = set().union(*(set(c) for c in parts.values()))
        self.assertEqual(set(by_cell), lit)

    def test_same_log_same_images(self):
        other = tempfile.mkdtemp(prefix="iadmap-heat2-")
        try:
            work = os.path.join(other, "work")
            os.makedirs(work)
            start = P.log_start()
            H.update_wide(work, P.wide_points_for_hour, F.NOW, os.path.join(other, "wide"), db_start=start)
            with open(os.path.join(self.work, "heat", "wide", "all.png"), "rb") as f1, \
                    open(os.path.join(work, "heat", "wide", "all.png"), "rb") as f2:
                self.assertEqual(f1.read(), f2.read())
        finally:
            shutil.rmtree(other, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
