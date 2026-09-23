# iad-map
heat map of flight arrivals to IAD and surrounding airports

[View the IAD Flight Activity Map](iad_map_github.html) — May 1, 2025, from ADS-B Exchange history.

[View the live map](live.html) — the last 24 hours heard by the farm's own receiver, updated every 15 minutes.

## How the live map works

The Raspberry Pi in the stables runs two small programs from `pi/`:

- `collector.py` logs everything the receiver hears, every 2 seconds, to
  SQLite at `/mnt/flightdata/iad-map/flights.db` on the USB drive: every
  aircraft with a new message, all ranges and altitudes, with or without a
  position. It deletes nothing (how long to keep the log is undecided) and
  won't start if the drive isn't mounted, so nothing lands on the SD card.
- `publish.py` runs every 15 minutes, takes the last 24 hours of positions
  within 50 miles of Dulles at or below 15,000 ft, and writes, on the `live-data` branch,
  one file per hour of positions (`h/`) and an index, `live.json`, with the
  counts and KIAD weather reports from aviationweather.gov. The branch is one
  commit, amended and force-pushed each time, so the repository doesn't grow;
  a finished hour's file never changes, so each push only uploads the current
  hour.

`live.html` reads those files from raw.githubusercontent.com, and on its
5-minute refresh fetches only the hour files that changed. GitHub Pages is not
rebuilt. Install or update on the Pi with
`curl -fsSL https://raw.githubusercontent.com/RDBFarm/iad-map/main/pi/install.sh | sudo bash`.

Dots are coloured by altitude in viridis, brightest at the ground; nothing on
the live map relies on telling red, green and yellow apart.

The airport labels on the live map come from `publish.py`'s own rule: nearest
airport within 15 nm and below 10,000 ft for IAD, DCA and BWI (12 nm, 8,000 ft
for Andrews), 5 nm and below 3,000 ft for the smaller fields, and an airline-shaped callsign (three letters then a
digit) is never given Leesburg, Gaithersburg, Manassas or Stafford. It is not the
rule behind the May 1 map, which is not recorded here.
