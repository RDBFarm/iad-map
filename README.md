# iad-map
heat map of flight arrivals to IAD and surrounding airports

[View the IAD Flight Activity Map](iad_map_github.html) — May 1, 2025, from ADS-B Exchange history.

[View the live map](live.html) — the last 24 hours heard by the farm's own receiver, updated every 15 minutes.

## How the live map works

The Raspberry Pi in the stables runs two small programs from `pi/`:

- `collector.py` records every position its receiver hears within 50 miles of
  Dulles and below 15,000 ft, every 2 seconds, and keeps about a day of it.
- `publish.py` runs every 15 minutes and writes, on the `live-data` branch,
  one file per hour of positions (`h/`) and an index, `live.json`, with the
  counts and KIAD weather reports from aviationweather.gov. The branch is one
  commit, amended and force-pushed each time, so the repository doesn't grow;
  a finished hour's file never changes, so each push only uploads the current
  hour.

`live.html` reads those files from raw.githubusercontent.com, and on its
5-minute refresh fetches only the hour files that changed. GitHub Pages is not
rebuilt. Install or update on the Pi with
`curl -fsSL https://raw.githubusercontent.com/RDBFarm/iad-map/main/pi/install.sh | sudo bash`.

The airport colours on the live map come from `publish.py`'s own rule: nearest
airport within 15 nm and below 10,000 ft for IAD, DCA and BWI (12 nm, 8,000 ft
for Andrews), 5 nm and below 3,000 ft for the smaller fields. It is not the
rule behind the May 1 map, which is not recorded here.
