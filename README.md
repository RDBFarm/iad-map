# iad-map
heat map of flight arrivals to IAD and surrounding airports

[View the IAD Flight Activity Map](iad_map_github.html) — May 1, 2025, from ADS-B Exchange history.

[View the live map](live.html) — the last 24 hours heard by the farm's own receiver, updated every 15 minutes.

## How the live map works

The Raspberry Pi in the stables runs two small programs from `pi/`:

- `collector.py` records every position its receiver hears within 50 miles of
  Dulles and below 15,000 ft, every 10 seconds, and keeps about a day of it.
- `publish.py` runs every 15 minutes, turns the last 24 hours into
  `live_24h.json`, adds KIAD weather reports from aviationweather.gov, and
  force-pushes it to the `live-data` branch. That branch only ever holds the
  latest commit, so the repository doesn't grow.

`live.html` reads that file from raw.githubusercontent.com, so GitHub Pages is
not rebuilt on each update. Install or update on the Pi with
`curl -fsSL https://raw.githubusercontent.com/RDBFarm/iad-map/main/pi/install.sh | sudo bash`.

The airport colours on the live map come from `publish.py`'s own rule: nearest
airport within 15 nm and below 10,000 ft for IAD, DCA and BWI (12 nm, 8,000 ft
for Andrews), 5 nm and below 3,000 ft for the smaller fields. It is not the
rule behind the May 1 map, which is not recorded here.
