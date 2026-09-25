# iad-map
heat map of flight arrivals to IAD and surrounding airports

[View the IAD Flight Activity Map](iad_map_github.html) — May 1, 2025, from ADS-B Exchange history.

[View the live map](live.html) — the last 24 hours heard by the farm's own receiver, updated every 15 minutes.

## How the live map works

The Raspberry Pi in the stables runs two small programs from `pi/`:

- `collector.py` logs everything the receiver hears, every 2 seconds, to
  SQLite at `/mnt/flightdata/iad-map/flights.db` on the USB drive: every
  aircraft with a new message, all ranges and altitudes, with or without a
  position. It keeps everything until the drive is nearly full: below 10 GB
  free it deletes the oldest day at a time until 15 GB is free. It won't
  start if the drive isn't mounted, so nothing lands on the SD card.
- `publish.py` runs every 15 minutes, takes the last 24 hours of positions
  within 50 miles of Dulles at or below 15,000 ft, and writes, on the `live-data` branch,
  one file per hour of positions (`h/`) and an index, `live.json`, with the
  counts and KIAD weather reports from aviationweather.gov. The branch is one
  commit, amended and force-pushed each time, so the repository doesn't grow;
  a finished hour's file never changes, so each push only uploads the current
  hour.

- `alerts.py` watches everything the receiver hears, every 2 seconds, for
  squawk 7700, 7600 or 7500. Seen on 3 reads in a row, it logs the event to
  `/mnt/flightdata/iad-map/events/emergencies.jsonl` and pushes an alert file
  to the `alerts` branch; `.github/workflows/emergency-alert.yml` then opens
  an issue mentioning @RDBFarm, which the GitHub app turns into a phone
  notification. An ADS-B emergency status sent with an ordinary squawk is
  logged and shown on the map, not pushed (usually a glitch). Alerts name the
  airline from `operators.json` (tar1090-db).
- `proximity.py`, run by `publish.py`, logs close approaches at any range to
  `events/close_approaches.jsonl`: two airborne aircraft under 500 ft apart
  vertically and closer than the distance they'd cover towards each other in
  10 seconds at their relative speed (0.15 nm floor, 1 nm cap), with flags for airport traffic, formation,
  low-level and MLAT. They are close approaches, not confirmed near misses.
  TCAS resolution advisories, if this readsb reports them, go to
  `events/tcas.jsonl`. The live map marks emergencies, TCAS advisories and
  unflagged close approaches, and lists them in the Events panel. Tapping one opens a detail card; "Hide
from the map" removes it for that browser (remembered between visits), and
"Show N hidden" brings hidden ones back.

- `farm.py` logs every aircraft whose path crosses the farm's land (the
  parcel boundary), at any altitude, to `events/farm_passes.jsonl`, with its
  height above the farm's ground: reported altitude corrected with KIAD's
  altimeter setting, minus the ground elevation (USGS, measured once at
  install). The live map shows the last 24 hours as a figure and highlights
  passes under 1,600 ft above the ground; `alerts.py` pushes jets under
  1,100 ft and helicopters under 150 ft. Prop planes (by ICAO type in
  `aircraft_types.json`, from tar1090-db) are left out; unknown types are
  counted and shown, not pushed.

- `aircraft_lookup.py` supplies aircraft types, which readsb on this image
  doesn't: it looks each ICAO address up in tar1090's aircraft database,
  kept on the drive and refreshed monthly.

- `tracks.py`, run by `publish.py`, writes `t/<hour>.json` on `live-data`:
  every aircraft's whole recorded track, at every altitude and range,
  simplified for drawing (every turn, every 200 ft, at least one point a
  minute, both sides of any gap). The map draws a selected flight's full
  path from these; the log keeps every point.

`live.html` reads those files from raw.githubusercontent.com, and on its
5-minute refresh fetches only the hour files that changed. GitHub Pages is not
rebuilt. Install or update on the Pi with
`curl -fsSL https://raw.githubusercontent.com/RDBFarm/iad-map/main/pi/install.sh | sudo bash`.

Dots are coloured by altitude in viridis, brightest at the ground; nothing on
the live map relies on telling red, green and yellow apart.

## Classification

The live map labels and filters points with `classify_airport()`,
`is_arrival()` and the climb-sequence departure rule copied unchanged from
`render_github.py`, the Mac script that builds the May 1 map. One adaptation:
the departure rule ("3+ rises of over 100 ft in a row from below 500 ft") was
written for points about 10 s apart, so it is applied to each aircraft's
points thinned to 10 s; every logged point inside a flagged climb is dropped,
and nothing else is thinned.

Fixed on 2026-09-24: the GA flag now requires an N-number (N then a digit),
as `classify_airport()` does, instead of any callsign starting with "N"
(which caught Spirit, NKS); and the two-letter airline hints (WN, VV, VM, MX),
which could never match, were removed.

Still open: runway headings are runway numbers (magnetic) while ADS-B track
is true north, and KIAD is listed as 19/199. Not yet checked against
published true bearings; to be looked at with real traffic.
