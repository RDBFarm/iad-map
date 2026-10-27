"""The pages in a real browser on the made-up data: they load without
errors and show it. Needs Playwright with Chromium; skipped without it.

  PLAYWRIGHT_CHROMIUM=/path/to/chromium   use that browser binary
  LEAFLET_DIST=/path/to/leaflet/dist      serve Leaflet locally (no network)
"""
import datetime, functools, http.server, json, os, threading, time, unittest
from zoneinfo import ZoneInfo

import fixture as F
import busy as B
import farmcast as C
import heatmap as H
import publish as P

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def publish_fixture():
    """Write what the Pi would publish for the made-up log into F.WORK."""
    index, files, final = P.build(F.NOW)
    index["farm"]["summary"] = P.farm.summary(index["farm"]["passes"])
    P.write_tree(index, files)
    start = P.log_start()
    for _ in range(3):
        H.update(F.WORK, P.heat_points_for_hour, F.NOW + 31 * 3600, os.path.join(F.DRIVE, "pages-heat"), db_start=start)
        H.update_wide(F.WORK, P.wide_points_for_hour, F.NOW, os.path.join(F.DRIVE, "pages-wide"), db_start=start)
    B.update(F.WORK, F.NOW + 3 * 3600, os.path.join(F.DRIVE, "pages-busy"), final)
    C.update(F.WORK, F.NOW + 3 * 3600, os.path.join(F.DRIVE, "pages-farmcast"), final,
             [{"epoch": F.T0 + 1800 + 3600 * i, "drct": 180, "sknt": 10, "compass": "S"} for i in range(F.HOURS)],
             fetch=lambda url, timeout=30: (_ for _ in ()).throw(OSError("no network in the tests")))
    return index


class Quiet(http.server.SimpleHTTPRequestHandler):
    """A static server that answers cross-origin, as raw.githubusercontent.com does for the data."""
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, *a):
        pass


def serve(directory):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=directory))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


@unittest.skipIf(sync_playwright is None, "Playwright not installed")
class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = publish_fixture()
        cls.site, cls.site_url = serve(F.ROOT)
        cls.data, cls.data_url = serve(F.WORK)
        cls.pw = sync_playwright().start()
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM")
        cls.browser = cls.pw.chromium.launch(**({"executable_path": exe} if exe else {}), args=["--no-proxy-server"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.site.shutdown()
        cls.data.shutdown()

    def open(self, page_name, width=1280, height=800, hash_="", data=None, fulfil=None, abort=()):
        pg = self.browser.new_page(viewport={"width": width, "height": height})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        dist = os.environ.get("LEAFLET_DIST")

        def route(r):
            url = r.request.url
            if dist and "/leaflet.min." in url:
                js = url.split("?")[0].endswith(".js")
                return r.fulfill(path=os.path.join(dist, "leaflet.js" if js else "leaflet.css"),
                                 content_type="application/javascript" if js else "text/css")
            if any(part in url for part in abort):
                return r.abort()
            for part, body in (fulfil or {}).items():
                if part in url:
                    return r.fulfill(body=body, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})
            if url.startswith(("http://127.0.0.1", "https://cdnjs.cloudflare.com")):
                return r.continue_()
            return r.abort()       # map tiles, radar: not needed, and must not make a test flaky
        pg.route("**/*", route)
        sep = "&" if "?" in page_name else "?"
        pg.goto(f"{self.site_url}{page_name}{sep}data={data or self.data_url}{hash_}", wait_until="load")
        pg.wait_for_timeout(2500)
        return pg, errors

    def test_live_map_shows_the_data(self):
        for width in (1280, 390):
            pg, errors = self.open("live.html", width=width)
            self.assertEqual(errors, [], f"live.html at {width}px")
            if width > 640:                       # the subtitle and badge are hidden on a phone
                self.assertIn("75-mile radius", pg.inner_text("#hdr"))
                self.assertRegex(pg.inner_text("#badge-pos"), r"\d")
            self.assertLessEqual(pg.evaluate("document.documentElement.scrollWidth"), width)
            pg.close()

    def test_search_matches_stays_and_opens_the_card(self):
        pg, errors = self.open("live.html")
        rows = "document.querySelectorAll('#search-results .search-result').length"
        pg.fill("#search-input", "L10")             # inside a callsign only: no longer a match
        pg.wait_for_timeout(300)
        self.assertEqual(pg.evaluate(rows), 0)
        pg.fill("#search-input", "101")             # a flight number alone finds UAL101
        pg.wait_for_timeout(300)
        self.assertIn("UAL101", pg.inner_text("#search-results"))
        pg.fill("#search-input", "B739")
        pg.wait_for_timeout(300)
        n = pg.evaluate(rows)
        self.assertGreater(n, 1)
        self.assertIn(f"{n} flights by", pg.inner_text("#search-count"))
        pg.click("#search-results .search-result >> nth=1")
        pg.wait_for_timeout(500)
        self.assertFalse(pg.evaluate("document.getElementById('ev-card').hidden"), "a result opens its card")
        self.assertIn("UAL", pg.inner_text("#ev-body h4"))
        self.assertEqual(pg.evaluate(rows), n, "the results stay while the card is open")
        pg.click("#ev-close")
        pg.wait_for_timeout(300)
        self.assertEqual(pg.evaluate(rows), n, "and after it is closed")
        pg.click("#search-clear-btn")
        pg.wait_for_timeout(300)
        self.assertEqual(pg.evaluate(rows), 0)
        self.assertEqual(errors, [])
        pg.close()

    def test_events_training_jets_hidden_and_hide_button_on_top(self):
        pg, errors = self.open("live.html")
        pg.wait_for_function("Object.keys(TYPE_DESC).length > 0")
        js = """(() => {
          NOTABLE['ae0001'] = [['Training Wheels', 'Bicycle Made For Two'], 'Northrop T-38 Talon', 'USAF', '', ''];
          const ac = (hex, flight, type, lat) => ({hex, flight, type, lat, lon: -77.4, alt: 9000, gs: 300, track: 90});
          const close = (a, b) => ({a, b, cpa_t: META.end - 600, start: META.end - 610, end: META.end - 590, h_nm: 0.4, v_ft: 200, closing_kt: 120, limit_nm: 0.33, flags: []});
          const ev = {close_approaches: [close(ac('ae0001', 'RO01', 'T38', 39.0), ac('ae0002', 'RO02', 'T38', 39.001)),
                                         close(ac('a00001', 'UAL1', 'B738', 39.2), ac('a00002', 'DAL2', 'A321', 39.201))]};
          const r = eventItems(ev);
          openEventCard(r.items[0]);
          const body = document.getElementById('ev-body');
          return {training: r.training, shown: r.items.map(i => i.label),
                  afterTitle: body.querySelector('h4').nextElementSibling.querySelector('#ev-hide') !== null};
        })()"""
        r = pg.evaluate(js)
        self.assertEqual(r["training"], 1, "the T-38 pair is not shown")
        self.assertEqual(len(r["shown"]), 1)
        self.assertIn("UAL1", r["shown"][0])
        self.assertTrue(r["afterTitle"], "the hide button is right under the card's title")
        self.assertEqual(errors, [])
        pg.close()

    def test_public_page_has_no_farm(self):
        pg, errors = self.open("flights.html")
        self.assertEqual(errors, [])
        self.assertNotIn("RDBF receiver", pg.inner_text("#hdr"))
        self.assertEqual(pg.get_attribute("#btn-busy", "href"), "busy.html?public")
        pg.close()

    def test_history_near_and_wide(self):
        pg, errors = self.open("heat.html")
        self.assertEqual(errors, [])
        self.assertIn("aircraft-hours", pg.inner_text("#status"))
        # the same page buttons on every page (owner, 10-24)
        self.assertEqual(pg.get_attribute("#btn-busy", "href"), "busy.html")
        self.assertEqual(pg.get_attribute("#back", "href"), "live.html")
        lit = "(() => { let n = 0; for (let i = 0; i < SHOWN.s.length; i++) if (SHOWN.s[i] || SHOWN.b[i]) n++; return n; })()"
        self.assertGreater(pg.evaluate(lit), 0)
        pg.click("#btn-wide")
        pg.wait_for_timeout(2000)
        self.assertIn("200 miles", pg.inner_text("#wide-info"))
        self.assertGreater(pg.evaluate(lit), 0)
        self.assertEqual(pg.evaluate("document.querySelectorAll('.bc-icon').length"),
                         pg.evaluate("BEACONS.length"))
        self.assertEqual(errors, [])
        pg.close()

    def test_busy_times_from_the_pi(self):
        pg, errors = self.open("busy.html")
        self.assertEqual(errors, [])
        self.assertIn("Counted by the farm's receiver", pg.inner_text("#status"))
        self.assertIn("0 whole days", pg.inner_text("#status"))   # the made-up log is three hours
        self.assertEqual(pg.get_attribute("#btn-heat", "href"), "heat.html")   # the same page buttons on every page (10-24)
        self.assertEqual(pg.get_attribute("#back", "href"), "live.html")
        self.assertIn("no whole days yet", pg.inner_text("#tbl"))
        self.assertIn("Needs a whole day", pg.inner_text("#fc-note"))
        pg.close()

    def test_busy_times_snapshot_until_the_pi_has_published(self):
        for width in (1280, 390):
            pg, errors = self.open("busy.html", width=width, data=self.site_url + "no-such-branch/")
            self.assertEqual(errors, [], f"busy.html at {width}px")
            self.assertIn("snapshot", pg.inner_text("#status"))
            self.assertIn("27 whole days", pg.inner_text("#status"))
            self.assertIn("on", pg.get_attribute("[data-kind=a]", "class").split(), "opens on Landings")
            first = pg.inner_text("#tbl tr")
            self.assertIn("Every day", first)
            self.assertIn("3:30\u20134:30p", first)
            # the table's headers say what each figure counts ("In it", "Per day", "Days": owner, 10-27)
            # in his order: "Day, Arrivals, Busiest Hour, Arrivals, Busiest 15 minutes, Arrivals"
            head = [c.strip() for c in pg.inner_text("#tbl-head").split("\t")]
            self.assertEqual(head, ["Day", "Landings", "Busiest hour", "Landings", "Busiest 15 minutes, from", "Landings"])
            self.assertIn("each weekday over its own", pg.inner_text("#tbl-note"))
            if width > 640:   # equal columns, so a count isn't a page away from its time ("uneven on my iPad", 10-27)
                cols = pg.evaluate("[...document.querySelectorAll('#tbl tr:first-child td')].map(t => t.getBoundingClientRect().width)")
                self.assertLessEqual(max(cols) - min(cols), 1, cols)
            fits = "(w => w.scrollWidth <= w.clientWidth)(document.getElementById('tbl').closest('.tablewrap'))"
            for kind in ("d", "ad", "a"):
                pg.click(f"[data-kind={kind}]")
                self.assertTrue(pg.evaluate(fits), f"the table fits at {width}px ({kind})")
                if kind == "d":
                    self.assertEqual(pg.inner_text("#tbl-head").count("Take-offs"), 3)
            self.assertLessEqual(pg.evaluate("document.documentElement.scrollWidth"), width)
            # tapping a bar, then a time on Wednesday's row, gives its figures under both charts
            bw = pg.evaluate("document.getElementById('bars').clientWidth")
            pg.click("#bars", position={"x": 64 + 64.5 * (bw - 68) / 96, "y": 100})   # the grid's gutter (10-24)
            self.assertIn("Every day, 4:00\u20134:15 PM:", pg.inner_text("#bar-tip"))
            self.assertIn("landings on average", pg.inner_text("#bar-tip"))
            gw = pg.evaluate("document.getElementById('grid').clientWidth")
            pg.click("#grid", position={"x": 64 + 40.5 * (gw - 68) / 96, "y": 4 + 3 * 26 + 13})
            self.assertIn("Wednesdays, 10:00\u201310:15 AM:", pg.inner_text("#tip"))
            self.assertEqual(pg.inner_text("#tip"), pg.inner_text("#bar-tip"))
            self.assertEqual(pg.inner_text("#day-title"), "Wednesdays")
            pg.click("[data-kind=d]")
            self.assertNotEqual(pg.inner_text("#tbl tr"), first)
            self.assertIn("take-offs on average", pg.inner_text("#tip"))
            self.assertEqual(errors, [])
            pg.close()

    def test_farm_forecast_follows_the_wind(self):
        # a made-up week from local midnight today: 3 days of wind from the south, then from the north
        tz = ZoneInfo("America/New_York")
        start = int(datetime.datetime.combine(datetime.datetime.now(tz).date(), datetime.time(), tz).timestamp())
        times = [start + 3600 * i for i in range(7 * 24)]
        om = json.dumps({"hourly": {"time": times, "wind_speed_10m": [10] * len(times),
                                    "wind_direction_10m": [180 if i < 72 else 0 for i in range(len(times))]}})
        for width in (1280, 390):
            pg, errors = self.open("busy.html", width=width, data=self.site_url + "no-such-branch/",
                                   fulfil={"api.open-meteo.com": om})
            self.assertEqual(errors, [], f"busy.html at {width}px")
            self.assertEqual(pg.inner_text("#fc-note"), "Planes expected to fly within 2 miles of the farm during final approach.")
            rows = pg.query_selector_all("#fc-blocks tr")
            self.assertEqual(len(rows), 7)
            cell = lambda r, b: rows[r].query_selector_all("td")[b + 1].inner_text()
            south, north = cell(1, 2), cell(5, 2)          # tomorrow's afternoon, and five days on
            self.assertGreater(int(south), 40)      # just the number; the hour taps give the wind
            self.assertLess(int(north), 10)
            self.assertLessEqual(pg.evaluate("document.documentElement.scrollWidth"), width)
            rows[2].click()
            self.assertIn("planes", pg.inner_text("#fc-day-title"))
            fw = pg.evaluate("document.getElementById('fc-bars').clientWidth")
            pg.click("#fc-bars", position={"x": 64 + 15.5 * (fw - 68) / 24, "y": 80})
            self.assertRegex(pg.inner_text("#fc-bar-tip"), r"3p\u20134p: about \d+ planes \u00b7 wind from the S at 10 kt")
            self.assertEqual(pg.inner_text("#fc-record"), "", "no numbers from the Pi here")
            # the page's arithmetic and the Pi's twin (farmcast.predict), which scores its kept forecasts
            page = dict(pg.evaluate("FCDAYS.flatMap(d => d.hours.filter(Boolean).map(x => [x.t, x.exp]))"))
            with open(os.path.join(F.ROOT, "farm-model.json")) as f, open(os.path.join(F.ROOT, "busy-seed.json")) as g:
                pi = C.predict(json.load(f), json.load(g), json.loads(om), time.time())
            both = [t for t in page if t in pi and C.local(t).hour != 1]     # 1 AM happens twice when the clocks go back
            self.assertGreater(len(both), 100)
            for t in both:
                self.assertAlmostEqual(page[t], pi[t][2], places=9, msg=C.local(t).isoformat())
            self.assertEqual(errors, [])
            pg.close()

    def test_farm_forecast_uses_the_pi_once_ready(self):
        with open(os.path.join(F.ROOT, "farm-model.json")) as f:
            seed = json.load(f)
        pi = dict(seed, ready=True, share={"S": 0.5, "N": 0.01}, source="pi/farmcast.py",
                  score={"days": 28, "forecasts": 9, "by_lead": [
                      {"lead": 0, "blocks": 12, "median_err": 4.0, "median_actual": 15, "median_forecast": 13.0},
                      {"lead": 1, "blocks": 20, "median_err": 6.5, "median_actual": 14, "median_forecast": 12.0},
                      {"lead": 3, "blocks": 3, "median_err": 9.0, "median_actual": 14, "median_forecast": 10.0}]})
        om = json.dumps({"hourly": {"time": [int(time.time()) // 3600 * 3600 + 3600 * i for i in range(48)],
                                    "wind_speed_10m": [10] * 48, "wind_direction_10m": [180] * 48}})
        pg, errors = self.open("busy.html", data=self.site_url + "no-such-branch/",
                               fulfil={"farm-model.json?t=": json.dumps(pi), "api.open-meteo.com": om})
        self.assertEqual(errors, [])
        self.assertIn("50% of its arrivals", pg.evaluate("document.getElementById('fc-about').textContent"))
        self.assertEqual(pg.inner_text("#fc-record"), "How it has done (last 28 days): 6-hour blocks forecast later the "
                         "same day were off by a median of 4 planes; the next day 6.5. A typical block had 15.")
        pg.close()
        # the Pi's own numbers before they cover two weeks: the snapshot is used, and the page says so
        with open(os.path.join(F.WORK, "farm-model.json")) as f:      # the made-up log's: three hours
            mine = f.read()
        pg, errors = self.open("busy.html", data=self.site_url + "no-such-branch/",
                               fulfil={"farm-model.json?t=": mine, "api.open-meteo.com": om})
        self.assertEqual(errors, [])
        self.assertIn(f"{round(seed['share']['S'] * 100)}% of its arrivals", pg.evaluate("document.getElementById('fc-about').textContent"))
        self.assertIn("The Pi is counting its own numbers (3 of 336 hours so far)", pg.inner_text("#fc-record"))
        pg.close()

    BOX = """s => { const e = document.querySelector(s); if (!e || e.hidden) return null;
        const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }"""

    def assert_clear(self, pg, pairs, label):
        for a, b in pairs:
            ra, rb = pg.evaluate(self.BOX, a), pg.evaluate(self.BOX, b)
            if ra is None or rb is None:
                continue
            ox = min(ra[2], rb[2]) - max(ra[0], rb[0])
            oy = min(ra[3], rb[3]) - max(ra[1], rb[1])
            self.assertFalse(ox > 0 and oy > 0, f"{label}: {a} over {b} by {ox:.0f} x {oy:.0f} px")

    def test_panels_stay_clear_of_each_other(self):
        # At 390 px the legend and weather card sat 30 px inside the control bar and 114 px over
        # each other; at 1280 x 800 a long search list reached the legend (10-24).
        pairs = [("#leg", "#controls"), ("#wx", "#controls"), ("#ev-card", "#controls"), ("#leg", "#wx"),
                 ("#search-panel", "#ap-panel"), ("#search-panel", "#leg"), ("#search-panel", "#wx"),
                 ("#hdr", "#search-panel"), ("#hdr", "#ap-panel")]
        for width, height in ((390, 844), (1280, 800)):
            pg, errors = self.open("live.html", width=width, height=height)
            self.assert_clear(pg, pairs, f"{width} px")
            if width == 390:
                pg.click("#ap-panel h3")
                pg.wait_for_timeout(200)
                self.assert_clear(pg, pairs, f"{width} px, categories open")
                pg.click("#ap-panel h3")
            pg.fill("#search-input", "UAL")             # every airliner in the made-up log
            pg.wait_for_timeout(500)
            self.assert_clear(pg, pairs, f"{width} px, search results")
            pg.click("#search-results .search-result >> nth=0")
            pg.wait_for_timeout(800)
            self.assertFalse(pg.evaluate("document.getElementById('ev-card').hidden"))
            self.assert_clear(pg, pairs, f"{width} px, card open")
            # the header's subtitle can wrap (the "Pi may be offline" note): the panels follow it
            pg.evaluate("document.getElementById('updated').textContent = 'updated 9:30 PM — 5.8 h ago, the Pi may be offline, and more words to wrap the line'")
            pg.wait_for_timeout(300)
            self.assert_clear(pg, pairs, f"{width} px, wrapped header")
            self.assertEqual(errors, [])
            pg.close()

    def test_day_map_text_is_readable(self):
        # toggleMapStyle() recoloured what it knew; the panel headings and search rows stayed
        # light on the light panels (10-24)
        pg, errors = self.open("live.html")
        pg.fill("#search-input", "UAL")
        pg.wait_for_timeout(300)
        pg.click("#btn-mapstyle")
        pg.wait_for_timeout(500)
        for sel in ("#search-panel h3", "#events h3", ".sr-time", ".sr-type", ".sr-name", "#leg h3", ".leg-note", "#search-count"):
            rgb = pg.evaluate("s => getComputedStyle(document.querySelector(s)).color", sel)
            parts = [int(x) for x in rgb.strip("rgba()").split(",")[:3]]
            self.assertLess(max(parts), 120, f"{sel} is {rgb} on the day map's light panel")
        self.assertEqual(errors, [])
        pg.close()

    def test_a_missing_hour_file_still_draws_the_rest(self):
        # raw.githubusercontent can serve a new live.json before a brand-new hour file:
        # one failed fetch used to fail the whole first load (10-24)
        lost = self.index["chunks"][0]["name"]
        pg, errors = self.open("live.html", abort=(lost,))
        self.assertGreater(pg.evaluate("PTS.n"), 0)
        self.assertIn("1 hour missing, retrying at the next refresh", pg.inner_text("#updated"))
        self.assertEqual(errors, [])
        pg.close()

    def test_weather_card_before_the_first_report(self):
        pg, errors = self.open("live.html")
        text = pg.evaluate("""() => { WX = [{t: 600, tmpf: 50, sknt: 0, drct: 0, compass: 'N', vsby: 10, sky: 'Clear', wx: null, gust: null}];
            updateWeather(0); return [document.getElementById('wx-time').textContent, document.getElementById('wx-temp').textContent]; }""")
        self.assertTrue(text[0].startswith("first report"), text)      # not a report 10 minutes in the future
        self.assertEqual(text[1], "—")
        pg.close()

    def test_farm_summary_counts_passes_and_aircraft(self):
        # summary.aircraft counts passes: 51 "aircraft" on 10-18 were 51 passes by 49
        pg, errors = self.open("live.html")
        text = pg.evaluate("""() => { drawFarm({center: FARM_CENTER || [39.15, -77.46], passes: [
              {hex: 'a1', rule: 'parcel', alt_agl: 5000}, {hex: 'a1', rule: 'parcel', alt_agl: 7000},
              {hex: 'b2', rule: 'parcel', alt_agl: 9000}, {hex: 'c3', rule: 'parcel', alt_agl: 800, prop: true}],
            summary: {aircraft: 3, median_agl: 7000, lowest_agl: 5000, under_highlight: 0, highlight_agl: 1600, props_excluded: 1, rule: 'parcel'}});
            return document.getElementById('farm-summary').innerText; }""")
        self.assertIn("3 passes by 2 aircraft", text)
        self.assertEqual(errors, [])
        pg.close()

    def test_busy_times_labels_midnight_and_partial_blocks(self):
        tz = ZoneInfo("America/New_York")
        start = int(datetime.datetime.combine(datetime.datetime.now(tz).date(), datetime.time(), tz).timestamp())
        times = [start + 3600 * i for i in range(7 * 24)]
        om = json.dumps({"hourly": {"time": times, "wind_speed_10m": [10] * len(times), "wind_direction_10m": [180] * len(times)}})
        pg, errors = self.open("busy.html", data=self.site_url + "no-such-branch/", fulfil={"api.open-meteo.com": om})
        self.assertEqual(pg.evaluate("quarter(96)"), "12:00 AM")          # was "12:00 PM" (10-24)
        self.assertEqual(pg.evaluate("quarter(0)"), "12:00 AM")
        # today's first block has already started: its chip says from when it counts
        partial = pg.evaluate("FCDAYS[0].hours.findIndex(Boolean) % 6 !== 0")
        chip = pg.evaluate("(document.querySelector('#fc-blocks .chip small') || {}).textContent || ''")
        self.assertEqual(bool(chip), partial, chip)
        if partial:
            self.assertRegex(chip, r"^from \d+[ap]$")
        self.assertEqual(pg.get_attribute("[data-kind=a]", "aria-pressed"), "true")
        self.assertEqual(errors, [])
        pg.close()

    def test_take_offs_toggle_like_the_heat_map(self):
        # Arrivals (the map as before) / Take-offs (the o/ files) / Both (owner, 10-24)
        pg, errors = self.open("live.html")
        # [points loaded, of them take-offs, points the map shows over the whole day]
        count = "(() => { let n = 0; for (let i = 0; i < PTS.n; i++) n += PTS.mov[i]; return [PTS.n, n, getPtsInRange(0, SPAN + 1).length]; })()"
        arrivals, movA, shownA = pg.evaluate(count)
        self.assertEqual(movA, 0, "arrivals only: nothing marked as a take-off, and no o/ file fetched")
        self.assertEqual(shownA, arrivals)
        self.assertEqual(pg.get_attribute("#mov-row .btn >> nth=0", "aria-pressed"), "true")
        pg.click("#mov-row .btn >> nth=1")                 # Take-offs
        pg.wait_for_timeout(2500)
        loaded, deps, shownD = pg.evaluate(count)
        self.assertGreater(deps, 0)
        self.assertEqual(loaded, arrivals + deps, "the arrivals stay loaded; the take-off files join them")
        self.assertEqual(shownD, deps, "but only the take-offs are shown")
        self.assertNotIn("take-offs", pg.inner_text("#updated"), "the fixture has take-off files for every hour")
        pg.click("#mov-row .btn >> nth=2")                 # Both
        pg.wait_for_timeout(2500)
        both, movB, shownB = pg.evaluate(count)
        self.assertEqual((both, movB, shownB), (arrivals + deps, deps, arrivals + deps))
        # the points stay in time order, which the drawing's binary search relies on
        self.assertTrue(pg.evaluate("(() => { for (let i = 1; i < PTS.n; i++) if (PTS.tmin[i] < PTS.tmin[i - 1]) return false; return true; })()"))
        self.assertEqual(pg.evaluate("localStorage.getItem('iadmap.mov')"), "ad")
        self.assertEqual(errors, [])
        pg.close()

    def test_farm_headline_is_the_forecasts_measure(self):
        pg, errors = self.open("live.html")
        text = pg.evaluate("""() => { drawFarm({center: FARM_CENTER || [39.15, -77.46], passes: [],
            summary: {aircraft: 0, props_excluded: 0, rule: 'parcel'},
            near: {aircraft: 12, hours: 24, radius_mi: 2.0, below_ft: 6000, props: 'included'}});
            return document.getElementById('farm-summary').innerText; }""")
        self.assertIn("12 planes within 2 mi below 6,000 ft", text)
        self.assertIn("No aircraft over the land itself yet", text)
        self.assertEqual(errors, [])
        pg.close()

    def test_a_tapped_flight_fetches_only_the_track_files_it_spans(self):
        # every track file (25 a day, 9.7 MB) was fetched to draw one path (10-24)
        pg, errors = self.open("live.html")
        fetched = []
        pg.on("request", lambda r: fetched.append(r.url) if "/t/" in r.url else None)
        pg.fill("#search-input", "UAL100")            # the first arrival: 13 minutes, inside the first hour
        pg.wait_for_timeout(500)
        pg.click("#search-results .search-result >> nth=0")
        pg.wait_for_timeout(2500)
        self.assertGreater(pg.evaluate("trackLayer.getLayers().length"), 0, "its path is drawn")
        self.assertLessEqual(len(fetched), 2, fetched)    # its hour and the one after; the log has three
        self.assertEqual(errors, [])
        pg.close()

    def test_busy_times_public_page_has_no_farm(self):
        pg, errors = self.open("busy.html?public", data=self.site_url + "no-such-branch/")
        self.assertEqual(errors, [])
        self.assertIsNone(pg.query_selector("#fc-panel"))
        self.assertEqual(pg.evaluate("document.querySelectorAll('.farm').length"), 0)
        self.assertEqual(pg.get_attribute("#back", "href"), "flights.html")
        self.assertIn("3:30\u20134:30p", pg.inner_text("#tbl tr"))
        pg.close()


if __name__ == "__main__":
    unittest.main()
