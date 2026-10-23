#!/usr/bin/env python3
"""Build busy-seed.json, what busy.html shows until the Pi publishes its own
busy.json (pi/busy.py): the same counts, made here from the t/ hour files on the
live-data branch, which hold the last 30 days.

    git fetch --depth 1 origin live-data
    python3 tools/make_busy.py [git ref, default FETCH_HEAD]
"""
import json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pi"))
import busy, tracks  # noqa: E402

REF = sys.argv[1] if len(sys.argv) > 1 else "FETCH_HEAD"
git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT, check=True).stdout
names = [n for n in git("ls-tree", "-r", "--name-only", REF).split() if tracks.parse_name(n) and n.startswith("t/")]
hours = sorted(tracks.parse_name(n)[1] for n in names)
cache = {}


def load(h):
    if h not in cache:
        name = tracks.hour_name("t", h)
        cache[h] = json.loads(git("show", f"{REF}:{name}")) if name in names else None
    return cache[h]


st = {"days": {}, "hours_per_day": {}}
for h in hours[:-1]:                      # the newest hour isn't settled
    busy.count_hour(h, load, st)
    for old in [k for k in cache if k < h - 3600]:
        del cache[old]
first, last = time.strftime("%Y-%m-%d", time.gmtime(hours[0])), time.strftime("%Y-%m-%d", time.gmtime(hours[-1]))
busy.write(os.path.join(ROOT, "busy-seed.json"), st["days"], st["hours_per_day"], hours[-1],
           source=f"the published recorded paths, {first} to {last} (a snapshot until the Pi publishes busy.json)")
print(len(hours), "hours;", len(busy.complete_days(st["hours_per_day"])), "complete days -> busy-seed.json")
