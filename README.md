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

## Classification

The live map labels and filters points with `classify_airport()`,
`is_arrival()` and the climb-sequence departure rule copied unchanged from
`render_github.py`, the Mac script that builds the May 1 map. One adaptation:
the departure rule ("3+ rises of over 100 ft in a row from below 500 ft") was
written for points about 10 s apart, so it is applied to each aircraft's
points thinned to 10 s; every logged point inside a flagged climb is dropped,
and nothing else is thinned.

Issues found in that code on 2026-09-24, copied as they are and not yet fixed
(each needs the owner's go-ahead):

- `KEEP_EVERY_N = 2` in render_github.py drops every other cache row by file
  position. (Not copied: the live map keeps every point.)
- The GA test used for the map's points treats *any* callsign starting with
  "N" as GA, while `classify_airport()` requires N followed by a digit.
- `AIRLINE_AIRPORT` has two-letter keys (WN, VV, VM, MX) that can never
  match, since the code compares three letters.
- Runway headings are runway numbers (magnetic); ADS-B track is true north.
  KIAD is listed as 19/199. Not yet checked against published true bearings.
- render_github.py turns off HTTPS certificate checks for all its downloads.
- On the May 1 map, the weather request covers May 1 local time only, so
  8 PM to midnight on Apr 30 shows the 12:52 AM May 1 report; and the
  "5,800 aircraft" badge is typed in, not counted.
