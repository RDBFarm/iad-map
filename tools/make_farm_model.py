#!/usr/bin/env python3
"""Build farm-model.json, the numbers behind busy.html's forecast for the farm,
for the repository: what the page uses until the Pi's own copy (pi/farmcast.py,
published on live-data) covers farmcast.READY_HOURS. Same functions as the Pi.

Owner (2026-10-23): "an air traffic forecast for the farm ... expected plane
activity based on the expected winds, an hourly as well as blocks of time in
the coming 7 days".

From the t/ hour files on live-data (the last 30 days) and KIAD's hourly wind
reports (METARs, from the Iowa Environmental Mesonet archive). Also prints the
south landing direction it measures, to check farmcast.AXIS_DEG against.

    git fetch --depth 1 origin +live-data:refs/remotes/origin/live-data
    python3 tools/make_farm_model.py origin/live-data
"""
import json, os, statistics, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pi"))
import farmcast, tracks  # noqa: E402

REF = sys.argv[1] if len(sys.argv) > 1 else "origin/live-data"
git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT, check=True).stdout
names = {n for n in git("ls-tree", "-r", "--name-only", REF).split() if n.startswith("t/") and tracks.parse_name(n)}
hours = sorted(tracks.parse_name(n)[1] for n in names)[:-1]     # the newest hour isn't settled
cache = {}


def load(h):
    if h not in cache:
        n = tracks.hour_name("t", h)
        cache[h] = json.loads(git("show", f"{REF}:{n}")) if n in names else None
    return cache[h]


records, south_degs = {}, []
for h in hours:
    records[h] = farmcast.hour_record(h, load, south_degs)
    for old in [k for k in cache if k < h - 3600]:
        del cache[old]
winds = farmcast.winds_for(hours, farmcast.iem_reports(hours[0], hours[-1]))
m = farmcast.model(records, winds, "tools/make_farm_model.py: the farm receiver's recorded paths (live-data t/ "
                                   "files) and KIAD METARs from the Iowa Environmental Mesonet")
with open(os.path.join(ROOT, "farm-model.json"), "w") as f:
    json.dump(m, f, indent=1)
    f.write("\n")
print(json.dumps(m, indent=1))
print("south landing direction measured:", round(statistics.median(south_degs)), "(farmcast.AXIS_DEG:", farmcast.AXIS_DEG, ")")
