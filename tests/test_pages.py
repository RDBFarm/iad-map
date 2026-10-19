"""The three pages in a real browser on the made-up data: they load without
errors and show it. Needs Playwright with Chromium; skipped without it.

  PLAYWRIGHT_CHROMIUM=/path/to/chromium   use that browser binary
  LEAFLET_DIST=/path/to/leaflet/dist      serve Leaflet locally (no network)
"""
import functools, http.server, os, threading, unittest

import fixture as F
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

    def open(self, page_name, width=1280, height=800, hash_=""):
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
            if url.startswith(("http://127.0.0.1", "https://cdnjs.cloudflare.com")):
                return r.continue_()
            return r.abort()       # map tiles, radar: not needed, and must not make a test flaky
        pg.route("**/*", route)
        pg.goto(f"{self.site_url}{page_name}?data={self.data_url}{hash_}", wait_until="load")
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

    def test_public_page_has_no_farm(self):
        pg, errors = self.open("flights.html")
        self.assertEqual(errors, [])
        self.assertNotIn("RDBF receiver", pg.inner_text("#hdr"))
        pg.close()

    def test_history_near_and_wide(self):
        pg, errors = self.open("heat.html")
        self.assertEqual(errors, [])
        self.assertIn("aircraft-hours", pg.inner_text("#status"))
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


if __name__ == "__main__":
    unittest.main()
