"""The collector: pruning gives the log back an hour at a time and stops
when deleting frees nothing, and a stop (SIGTERM, every installer re-run
and reboot) saves the rows buffered since the last commit."""
import json, os, signal, sqlite3, subprocess, sys, tempfile, time, unittest

import fixture as F
import collector as C

INSERT = f"INSERT INTO positions ({','.join(C.COLUMNS)}) VALUES ({','.join('?' * len(C.COLUMNS))})"


class Prune(unittest.TestCase):
    def setUp(self):
        # the fixture's rows in a database made the way the collector makes it
        # (incremental auto-vacuum, WAL); fixture.make_db bypasses open_db
        self.tmp = tempfile.mkdtemp(prefix="iadmap-prune-")
        self.path = os.path.join(self.tmp, "flights.db")
        self.db = C.open_db(self.path)
        src = sqlite3.connect(F.DB)
        self.db.executemany(INSERT, src.execute("SELECT * FROM positions"))
        self.db.commit()
        src.close()
        self.total = self.count()

    def tearDown(self):
        self.db.close()

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM positions").fetchone()[0]

    def oldest(self):
        return self.db.execute("SELECT MIN(t) FROM positions").fetchone()[0]

    def size_gb(self):
        return sum(os.path.getsize(self.path + s) for s in ("", "-wal") if os.path.exists(self.path + s)) / 1e9

    def test_the_database_gives_deleted_space_back(self):
        self.assertEqual(self.db.execute("PRAGMA auto_vacuum").fetchone()[0], 2, "INCREMENTAL")

    def test_space_stuck_short_does_not_empty_the_log(self):
        # free space that stays at 9 GB whatever is deleted: a reader holding
        # the WAL, or something else filling the drive. One call used to
        # delete every day in the log.
        n, more = C.prune(self.db, self.tmp, free=lambda p: 9.0)
        self.assertGreater(n, 0)
        self.assertFalse(more, "deleting gave nothing back, so no more is deleted")
        self.assertEqual(self.count(), self.total - n)
        self.assertGreaterEqual(self.oldest(), F.T0 + 3600, "the oldest hour went")
        self.assertLess(self.oldest(), F.T0 + 7200, "and only that hour")

    def test_deletes_an_hour_at_a_time_while_space_comes_back(self):
        # free space measured from the files themselves: it rises when the
        # deleted pages really leave the database
        base = self.size_gb()
        free = lambda p: 9.0 + (base - self.size_gb())
        n, more = C.prune(self.db, self.tmp, free=free)
        self.assertGreater(n, 0)
        self.assertTrue(more)
        self.assertGreaterEqual(self.oldest(), F.T0 + 3600)
        self.assertLess(self.oldest(), F.T0 + 7200)
        n2, _ = C.prune(self.db, self.tmp, free=free)
        self.assertGreater(n2, 0)
        self.assertGreaterEqual(self.oldest(), F.T0 + 7200, "the next call takes the next hour")
        self.assertEqual((0, False), C.prune(self.db, self.tmp, free=lambda p: C.PRUNE_BELOW_GB + 1))

    def test_a_reader_holding_the_log_stops_the_pruning(self):
        base = self.size_gb()
        free = lambda p: 9.0 + (base - self.size_gb())
        reader = sqlite3.connect(self.path)
        cur = reader.execute("SELECT * FROM positions")
        cur.fetchone()                           # a publish query part-way through
        try:
            n, more = C.prune(self.db, self.tmp, free=free)
            self.assertGreater(n, 0)
            self.assertFalse(more, "the deleted pages wait in the WAL until the reader is done")
            self.assertGreater(self.count(), 0)
        finally:
            reader.close()


class Stop(unittest.TestCase):
    def test_a_stop_saves_the_buffered_rows(self):
        tmp = tempfile.mkdtemp(prefix="iadmap-stop-")
        reading = os.path.join(tmp, "aircraft.json")
        with open(reading, "w") as f:
            json.dump({"now": time.time(), "aircraft": [
                {"hex": f"a{k:05x}", "flight": f"UAL{k}", "seen": 0, "lat": 39.0, "lon": -77.4,
                 "seen_pos": 0, "alt_baro": 3000 + k, "gs": 200, "track": 181} for k in range(20)]}, f)
        db = os.path.join(tmp, "flights.db")
        env = dict(os.environ, IADMAP_AIRCRAFT_JSON=reading, IADMAP_DB=db,
                   IADMAP_DRIVE=os.path.join(tmp, "no-such-drive"), IADMAP_INTERVAL_S="0.2")
        p = subprocess.Popen([sys.executable, os.path.join(F.PI, "collector.py")], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            for _ in range(100):                 # until it has read the file (a few readings)
                if os.path.exists(db) and time.time() - os.path.getmtime(db) > 0.5:
                    break
                time.sleep(0.1)
            p.send_signal(signal.SIGTERM)       # systemd's stop, well inside the 30 s commit
            out, _ = p.communicate(timeout=20)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("buffered rows saved", out)
        rows = sqlite3.connect(db).execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        self.assertEqual(rows, 20, "every row read before the stop is in the log")


if __name__ == "__main__":
    unittest.main()
