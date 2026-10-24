# CLAUDE.md — Dulles-area Flight Tracker (rdbfarm/iad-map)

Context for Claude Code. Compiled from earlier Claude.ai conversations
(July–Sept 2026) and brought up to date in Claude Code on 2026-09-23/24;
changes from that session are marked **(09-23)** or **(09-24)**. Where
something is marked **OPEN**, it was never settled — ask Guillaume rather
than assuming.

---

## 0. Start here — status as of 2026-10-24 (evening)

**Running on the Pi** (systemd): `iad-map-collector` (logs every aircraft heard,
every 2 s), `iad-map-alerts` (emergency squawks, low passes, sightings → GitHub
issue + text message), `iad-map-publish.timer` (every 15 min → `live-data`: the
live map's arrivals `h/` and, from 10-24, take-offs `o/`, whole tracks `t/`, 30
days of each, the day files `d/`, both history heat maps, `busy.json`,
`farm-model.json`), `iad-map-aircraft-db.timer` (monthly type lookup),
`iad-map-netcheck.timer` (every 5 min, from 10-22: name lookups, diagnosis,
self-renewal; "Lookup outage" below). `live.json` `meta.pi_version` says what is
installed. **Installed 10-24 (~12:30 PM EDT): 112f2bc** — everything merged that
day (#88–#91 below); `PRAGMA auto_vacuum` on the real log printed `(2,)`.

**Pages** (GitHub Pages, from `main`): `live.html` (75 miles, under 15,000 ft;
day picker for 30 days; Arrivals / Take-offs / Both from 10-24), `flights.html`
(the same without the farm, built by `tools/make_public.py`), `heat.html`
(history: near = 50 mi under 15,000 ft with wind/airport/take-off choices; Wide
= 200 mi, every height, radio beacons), `busy.html` (landings and take-offs at
Dulles per 15 minutes by weekday, with the 7-day farm forecast; `busy.html?public`
from `flights.html`). Every header carries Live map / History / Busy times
(10-24, his request). Page changes need nothing on the Pi.

**Tests:** `python3 -m unittest discover -s tests -v` (73 tests, ~2 min; the
page tests need Playwright: locally `PLAYWRIGHT_CHROMIUM=/opt/pw-browsers/chromium`,
and `LEAFLET_DIST=<leaflet dist folder>` when there is no network). They run on
every PR and push (`.github/workflows/tests.yml`). `tests/fixture.py` is a
made-up 3-hour log; extend it rather than building one-off setups. **When a bug
is fixed, add the test that would have caught it.**

**Measured over a month (09-26 – 10-18):** every hour file present (563; two
3 AM hours thin); 16 alerts (7 emergency squawks, 7 sightings, 1 low pass, 1
test); repo 363 MB compressed with the 30-day window.

**Still open** (not ranked; nothing is blocking):
- **The arrival/take-off rule.** 69% of KIAD take-offs keep their climb in the
  *arrival* files (1,545 of 2,227 over 5 real days; 4.7% of live-map points):
  39% are first heard above 500 ft so the climb rule never starts, and
  GA-flagged points (60% of all) skip the filter. The 10-24 toggle shows the
  split as the rules make it — he looked and said the take-offs "look just like
  I would imagine they would" — it does not fix the rule. A better rule is
  judged against the busy rule's take-offs (the review's method).
- **Publisher memory and drive growth on the real Pi** were never measured (he
  declined self-reporting health, 10-18). `publish.py` loads the whole 24 h
  window every run: 1.04 M in-area points, ~660–760 MB RSS on a synthetic log
  that size (sandbox) — the floor on a 2 GB Pi; querying only unfrozen hours
  is a medium–large change, not done.
- Two medium speed items: `pi/notable.json` (1.87 MB raw, 193 KB gzipped) is
  fetched up front by `live.html`; `heat.html`'s `paint()` goes through a
  base64 data URL (~310–380 ms a paint; now debounced).
- Close-approach thresholds (first guesses; he only sees jets ones).
- **Farm forecast:** the first scores (`score.by_lead` in `farm-model.json`)
  from 10-24; the page shows a line once a lead has 4 scored blocks. Its
  choices (2 mi, 6,000 ft, 6-hour blocks) were mine; on 10-24 he made that
  measure the live map's headline too (below).
- **Scheduled tasks** (each starts its own session): 10-25 10 AM (are the
  scores coming in), 11-19 (UA 2853 on 11-21: wind, seat, the light at the
  farm; re-check the middle runway), 2027-01-15 (777-9 deliveries: drop `B779`).

### What changed on 10-24 (a review he asked for, then his decisions; PRs #88–#93)
Three parallel reviews (the Pi programs, the pages, the rules run over 6 real
days of `t/` files); every number was measured, and each fix has a test.
- **Pi (#88).** The runway table held runway *numbers* read as degrees (KIAD
  "01/19" as 19/199); now true headings (OurAirports; real finals measure
  180.8°/0.7°) — 21.7% of Dulles's own short-final points had scored "en
  route"; frozen hours keep the old labels. The heat map's hour takes the wind
  report nearest its middle (every hour had gone into the *previous* hour's
  wind grid; hours counted before 10-24 stay there). A day file waits for its
  weather (`WX_RETRY_DAYS` = 3; `d/2026-10-21.json` went out with none).
  Pruning deletes one hour per call and stops when a deletion freed nothing
  (it looped a day at a time and could empty the log while a publish query
  held the WAL). The collector saves its buffer on SIGTERM (0 rows were saved
  at every restart). `PRAGMA temp_store = MEMORY` for the tracks and farm
  sorts; hour files written whole; an empty final file is rebuilt.
- **Pages (#89).** Phone layout measured at 390 × 844: the legend and weather
  card sat 30 px inside the control bar and 114 px over each other; now side
  by side above a 176 px bar, and the panels follow the header's measured
  height (`--hdr`). A long search list scrolls. Day map readable (`body.day`).
  Taps 40 px, text floor 11 px. One missing hour file no longer blanks the
  load ("N hours missing, retrying"). Weather card before the first report;
  type names on search rows; "N passes by M aircraft"; `busy.html` midnight
  label, "from 9p" chips, ✓ on the pressed button; `heat.html` missing-image
  wording. `test_panels_stay_clear_of_each_other` measures every panel pair.
- **Speed (#90), before → after on the real 10-23 files.** First points
  2,062 → 492–836 ms locally and **90.3 → 13.9 s on a 4 Mbit/s link** (the
  map draws as hour files arrive, newest first). A tapped flight fetches the
  track files its points span ± 1 h, widening at a file's edge on airborne
  points: 25 files → 3 for a single visit; a card naming an address draws that
  aircraft only. Search 87–110 → 18–42 ms a keystroke; redraw at the 30-min
  window 195 → 141–155 ms; the heat contrast slider repaints once the thumb
  rests. The page's own copy of the arrival rule is gone (#91): the Pi decides.
- **His decisions (#91, "Include 2 and 3").** Take-offs on the live map as a
  toggle in the heat map's words, the airborne positions the arrival rules
  drop, in `o/<day>/<HH>.json` fetched only when shown; hours before the
  install have none and the header says so. One farm rule — the live map's
  headline is the forecast's measure, "N planes within 2 mi below 6,000 ft
  (what the forecast counts; props included)" (`farm.near` in `live.json` and
  the `d/` files, `publish.near_summary`: farmcast's per-hour records where it
  has them, this run's `t/` files for the rest); the parcel figure stays
  underneath as "Over the land itself" and still drives the passes, highlights
  and alerts. `busy.html`'s bars now use the grid's gutter ("12am slightly left
  of 12am"). **(#93)** the same page buttons on every header.
- **Also measured, unchanged:** the busy rule reproduces `busy.json` exactly
  and balances (2,178 landings / 2,227 take-offs over 5 days); its error is
  reception — 5.8% of landings and 8.0% of take-offs vanish or appear at
  1,500–5,000 ft and are never counted; double counts ≤4 in 4,405. Type lookup:
  66 of 11,121 aircraft untyped (0.6%), corporate callsigns, not airlines.

### How to change something on the Pi
All Pi code is in `pi/` on `main`. Merge the change, then have him run on
the Pi (SSH from the Mac Terminal or Termius on the iPad, **on the farm
network**), as **two separate lines** — a one-line `curl … | sudo bash` lost
its pipe when pasted from the iPad:

```
curl -fsSL https://raw.githubusercontent.com/RDBFarm/iad-map/main/pi/install.sh -o install.sh
sudo bash install.sh
```

The installer is safe to re-run: it keeps the log, events, key and lookup,
replaces the programs, and restarts running services. **Since 10-18 it installs
the exact commit `main` points at** (asks api.github.com, then downloads from
`raw.githubusercontent.com/<sha>/`, and re-runs itself from that commit if the
copy he fetched is older): `.../main/...` was served stale for minutes after a
merge more than once, costing a second run and a `grep -c` check each time. It
writes `/opt/iad-map/VERSION`, and a re-run ends with one line, "Done: version
abc1234 ... installed and running", instead of the first-install key banner,
which he kept pasting back. Check what's running from anywhere:
`meta.pi_version` in `live.json`. Changes to
`live.html` need nothing on the Pi (Pages publishes a minute or two after
merge).

### Health check (one line on the Pi)
```
systemctl is-active iad-map-collector iad-map-alerts iad-map-publish.timer; df -h /mnt/flightdata | tail -1; ls -la /mnt/flightdata/iad-map/flights.db; journalctl -u iad-map-publish -n 5 --no-pager
```
From a cloud session, the published data can be read directly:
`https://raw.githubusercontent.com/RDBFarm/iad-map/live-data/live.json`
(counts, weather, events, farm passes, hour-file and track-file lists).
The Pi itself can't be reached from outside the farm.

### Upload outage 10-21 (noon – 3:15 PM EDT)
- Every push from the 12:00 run failed (`git push` exit 128); the receiver kept logging. Checked from the Pi with him: `https://github.com` reachable (200), the deploy key authenticates (`git push --dry-run` → "Everything up-to-date"), drive 11% full, `~/netwatch.log` with no failure since 09-28. GitHub had an incident open from 14:52 UTC ("Delays for Pull Requests and Webhook deliveries"; Actions/webhooks recovered ~17:09 UTC), but listed Git Operations as working, the Pi had pushed fine at 11:45 (an hour into it) and was still failing at 15:01 EDT. **Cause not established.** It recovered by itself at the 15:15 run, and the missed hours (16–18 UTC) were filled in automatically at normal size: the publisher rebuilds the 24 h window from the log every run, so an outage loses nothing that the log has.
- The log said only "exit status 128" because `publish.git()` captured git's stderr and dropped it. **Since 10-21 it prints git's message before raising** (`publish: git push failed (exit 128): ...`); read that first next time: `journalctl -u iad-map-publish -n 20 --no-pager`.
- Not the farm network: he could see the Pi's own tar1090 map through the same HubDuos (my first guess blamed the Stables HubDuo; he pointed out that couldn't be). The `pi` checks above need no change to anything; a hand push of an old attempt's commits would overwrite newer data until the next run, so don't, once uploads resume.

### Lookup outage 10-21 6:30 PM – 10-22 9:25 AM, and the network check (10-22)
- **Cause found this time (git's message, logged since #76):** `ssh: Could not resolve hostname github.com: Temporary failure in name resolution`. The whole Pi couldn't look up names: the ADS-B Exchange feed and MLAT logged the same until 09:24:30. The Pi stayed reachable on the farm network the whole time (he SSH'd in from 192.168.1.166). Very likely the 10-21 noon outage too.
- **NetworkManager logged nothing from 17:30 to 09:24:59**, then `dhcp4 (eth0): state changed new lease`, then at 09:26:27 `set 'Wired connection 1' (eth0) as default for IPv6 routing and DNS`; lookups worked from then (`getent hosts github.com` → 140.82.114.3; router and 1.1.1.1 ping fine). He restarted nothing. **Both outages ended within minutes of him logging in to the Pi** (10-21 ~3:10 PM, 10-22 ~9:20 AM); a clue, not a cause. Name servers before: `192.168.1.1` (the Fios router) and its IPv6 `2600:4040:27d7:ef00::1`, search `mynetworksettings.com`.
- **Backup name servers, set by hand 10-22** (his "happy to add whatever it takes"): `sudo nmcli connection modify "Wired connection 1" ipv4.ignore-auto-dns yes ipv4.dns "192.168.1.1 1.1.1.1 8.8.8.8" && sudo nmcli device reapply eth0`. NetworkManager then showed IP4.DNS 192.168.1.1, 1.1.1.1, 8.8.8.8 (router first, so local names still resolve). Undo: `sudo nmcli connection modify "Wired connection 1" ipv4.ignore-auto-dns no ipv4.dns "" && sudo nmcli device reapply eth0`. Not in the installer (it edits the farm's network profile; done once by hand).
- **`pi/netcheck.py`** (root, `iad-map-netcheck.timer`, every 5 min): looks up github.com; when that fails, records to `events/netcheck.jsonl` whether the router and 1.1.1.1 answer a ping, which name servers answer a question sent straight to them (each in resolv.conf, NetworkManager's list, 1.1.1.1), and the default routes; after 3 failures in a row (15 min) renews the connection (`nmcli connection up`), at most hourly, and records whether lookups came back; records the recovery with its length. Read on the Pi: `python3 /opt/iad-map/netcheck.py --status`. The point: the next outage explains itself without anyone logging in.

### Things that cost time on 09-23/24
- The deploy key was first added to **rdbf-app** instead of iad-map, giving
  "Permission … denied to deploy key". It lives on **iad-map** only; it must
  never be on rdbf-app (the farm records repo).
- GitHub notifications took a minute or two; he has **working hours** set, so
  night-time alerts arrive silently in the GitHub inbox. Direct Mentions on.
- A credential can't be tested from a cloud session (§5).
- Positions near DCA below ~2,000 ft fade out: reception limit ~35 mi from
  the farm, not a bug (AAL1623, 09-24).

---

## 1. Who you're working with

- **Guillaume** (GitHub: `rdbfarm`) runs Red Devil Bison Farm in Poolesville, MD. Hobbyist Python, **not a developer, not terminal-savvy**.
- Give **one command at a time**, say where to type it (Mac Terminal vs. SSH on the Pi), and ask him to paste the output back before moving on. Multi-line pastes into the shell have garbled before — prefer single-line commands (e.g. `echo '...' | sudo tee -a file` instead of opening an editor).
- **Say which device a step needs.** He moves between a MacBook and an iPad, and is not always at the farm. Anything that reaches the Pi needs the farm network; on the iPad, SSH is via Termius. **(09-24)**
- For destructive steps (formatting, deleting, overwriting), confirm the target first and explain what will happen.
- Works **incrementally**: one workstream at a time, validate before moving on.
- Tests directly and will correct you when you misread his description — take corrections at face value.
- **Fix root causes; never trade away data fidelity for convenience.** He explicitly rejected lowering `KEEP_EVERY_N` before memory-efficiency fixes were tried.
- **Numbers must come from the data, not estimates.** Count before stating totals, show the work for dates/times/quantities, and flag uncertainty explicitly. Verify claims before stating them as fact.
- If you're not good at something (e.g. freehand illustration in SVG/code), say so up front and offer an alternative.
- Don't use the phrase "gut check".
- **Color vision:** Guillaume has real trouble distinguishing yellow / green / red (red-green type). All color encodings must be colorblind-safe — use **viridis** for sequential ramps (e.g. altitude), and never rely on color alone to carry meaning.
- **PRs:** finished, tested work in this repo has been opened and merged without asking. **(09-23)**

---

## 2. Project overview

**This is a forward-looking project (09-24).** The historical May 1, 2025 map
is finished and will not be revisited. Work goes into the farm receiver and
the live map.

1. **Farm receiver (Raspberry Pi):** live ADS-B receiver at the farm, working since Sept 23, 2026.
2. **Live map:** `live.html` on GitHub Pages — rolling 24 hours from the Pi, refreshed every 15 minutes.
3. **Historical map (frozen):** `iad_map_github.html`, May 1, 2025 from ADS-B Exchange data. See §6.

### Airports covered (9)
| ICAO | Name | Type |
|---|---|---|
| KIAD | Washington Dulles | Commercial |
| KDCA | Reagan National | Commercial |
| KBWI | Baltimore/Washington | Commercial |
| KJYO | Leesburg Executive | GA-only |
| KGAI | Montgomery County Airpark | GA-only |
| KHEF | Manassas Regional | GA-only |
| KRMN | Stafford Regional | GA-only |
| KADW | Joint Base Andrews | Military |
| KNYG | Quantico MCAF | Military |

---

## 3. Farm receiver (Raspberry Pi) — current state

**Status: receiving since Sept 23, 2026; the `pi/` logger, publisher and
alerts installed and running since 09-24** (see §0). First result from the
receiver: 291 aircraft (271 with position), ~1,683 messages/sec.

### Hardware (as actually installed)
- Raspberry Pi 4B 2GB, aluminum heatsink case, official-spec 5.1V 3A USB-C supply
- **ADSBexchange "Blue" SDR** (R820T2/RTL2832U, 0.5 PPM TCXO, built-in LNA + 1090 filter) — in a **blue USB 3.0 port** via 6-inch extension
- **5.5 dBi 1090/978 dual-band antenna** (N-female) on the Stables pole, ~5–8 ft above roof apex
- **75 ft LMR400 coax** (KMR400, N-male → SMA-male), ~2.9 dB loss at 1090 MHz — confirmed **not** a meaningful limitation; no mast preamp needed
- **256 GB USB flash drive** in a **black USB 2.0 port** for logging (keeps logging off the SD card)
- microSD: the ADSBexchange feeder image

### Software on the Pi
- **ADSBexchange image** — hostname `adsbexchange`, user `pi`
- Decoder is **readsb**. Live data: **`/run/readsb/aircraft.json`**, rewritten ~once per second. `/run` is tmpfs.
- **tar1090** web map, **graphs1090** stats
- Also running: `adsbexchange-feed`, `adsbexchange-mlat`, `adsbexchange-stats`, `adsbexchange-gpsmon`
- **Feeding ADS-B Exchange: DECIDED — keep feeding (09-23).** He chose to give ADSBx data and get data (including MLAT positions) back. Earlier plans to send data to no one are superseded.
- **Tailscale** (`tailscaled`) is installed and **logged out** (09-23): no tailnet, so nothing can reach the Pi through it. Probably the image's remote-support option, declined — not verified.
- Auto-gain re-adjusts every 24 h (expect step changes in graphs1090 message rate)
- 978 UAT: **disabled** (only one SDR)
- Public feeder marker: random 5-mile offset (privacy); exact coordinates still used for MLAT

### Storage
- USB drive formatted **ext4**, label `flightdata`, mounted at **`/mnt/flightdata`** (~223 GB free)
- Auto-mount via `/etc/fstab` using the drive's UUID, `defaults,noatime 0 2`
- Keep continuous writes **off the SD card**.

### Network
- Wired by Ethernet to the NETGEAR GS324P switch. Router: Verizon Fios CR1000A. DHCP reservation `adsb-pi` → **`192.168.1.190`**.
- Web UI: `http://192.168.1.190/tar1090`. SSH: `ssh pi@192.168.1.190` (the Mac's known_hosts also has it as `192.168.2.3` from bench setup — same host key).
- The Pi is **not reachable from the internet**; cloud-side tools can't pull from it — the Pi pushes.
- Safe shutdown: `sudo shutdown -h now`.
- **Wi-Fi is switched off on purpose** (rfkill; decided in his HubDuo conversation, 09-26: the Pi is on Ethernet). The login banner "Wi-Fi is currently blocked by rfkill" is expected; don't unblock it.

### Receiver location
- Get exact antenna coordinates from the ADSBx config on the Pi rather than guessing.
- **Farm to KIAD is ~14.3 statute mi (12.4 nmi)**, measured from the parcel centre (09-23) — consistent with the A340 seen at 15.4 nmi on short final. The earlier "~30 miles" was wrong.
- `FARM_LAT/LON = 39.1133, -77.4447` in the old `render_github.py` is **not the farm**: 2.7 mi from the parcel and outside it (09-24). Don't use it.
- **Range claim doesn't add up (09-23):** notes said past the 150 nmi ring (~173 statute mi), "beyond Pittsburgh, Charleston WV, Virginia Beach". From the parcel: Pittsburgh 161 mi (inside the ring), Virginia Beach 178 mi, Charleston WV 232 mi (201 nmi). Check against readsb's range outline (`/run/readsb/outline.json`) before quoting.

### Data gotchas seen in live data
- **Negative altitudes are real** (pressure altitude): e.g. UAL1873 at −125 ft. Any ground threshold must tolerate this.
- **Mode S–only aircraft** (altitude, no position): ~20 of 291 in the first snapshot.
- **readsb on this image does not fill the aircraft type (`t`)** — 0 of 258 aircraft on 2026-09-24. `pi/aircraft_lookup.py` looks types up by ICAO address in tar1090-db (`aircraft.csv.gz`, csv branch) kept as SQLite at `/mnt/flightdata/iad-map/aircraft.db`, refreshed monthly by `iad-map-aircraft-db.timer`. Checked against May 1, 2025: found 1,817 of the 1,825 aircraft ADSBx had typed, same type for 97%. Addresses starting `~` (TIS-B, radar-derived) have no airframe and stay unknown — so they are logged and shown, never pushed.

---

## 4. Live map pipeline (built 09-23/24, installed 09-24)

See `README.md` for how to install. In short:

- **`pi/collector.py`** — logs to SQLite at **`/mnt/flightdata/iad-map/flights.db`**. One row per aircraft per read, for every aircraft with a new message: all ranges and altitudes, with or without position. Buffers and commits every 30 s, and saves the buffer when systemd stops it (from 10-24; before, up to 30 s of readings died with every restart). **Refuses to start if `/mnt/flightdata` isn't mounted.** Deletes nothing until the drive is nearly full (Retention, below).
- **`pi/publish.py`** — every 15 min (systemd timer): last 24 h, within **75** statute mi of KIAD (50 until 10-18: his "Yes, widen to 75 miles", after it measured ~21% more traffic below 15,000 ft; hours already frozen keep 50; the history heat map stays at 50 via `HEAT_RADIUS_NM`), ≤15,000 ft → one JSON file per UTC hour plus `live.json` on the **`live-data`** branch, as a single commit replaced and force-pushed, plus `live-data-prev` (same files, with the previous push as parent). Each push sends only the current hour and the index. From 10-24 the airborne positions the arrival rules drop go into a second file per hour, `o/<day>/<HH>.json` (take-offs, for the map's toggle; `live.json` `deps`), built and frozen like the `h/` files.
  - **Fixed 09-26: every push was sending the whole tree (~18 MiB, ~1.8–2.1 GiB/day measured with vnstat on the Pi).** Two causes. (1) Git skips files the remote has only when the remote's commit is a *parent* of the pushed one; an amended single commit has none, so git sent all ~57 MiB of files every run (reproduced locally: one changed file, 19.6 MiB sent). `live-data-prev` supplies the parent link. (2) Finished hour files were rebuilt each run and some changed: e.g. an aircraft last heard low that reappears hours later climbing makes the departure rule drop points from the old hour (shown in a test). Now an hour file is built once, `SETTLE_S` (10 min) after the hour ends, and read back from disk after that; the list of final files is kept in `publish/.git/iad-map-final-hours`. Same for `t/`. On a simulated day the pushes went from ~2,300 KiB each to 17–130 KiB. **Verified on the Pi 09-26** (installed 12:4x EDT, `vnstat -5 -i eth0`): push blocks went from 20.8–22.1 MiB tx to 4.9–5.1 MiB, about 0.4–0.5 MiB above the neighbouring quiet blocks (4.5–4.7 MiB), including the first run after install. Pushes with a **deploy key** scoped to this repo only.
- **Classification** — `classify_airport()`, `is_arrival()` and the climb-sequence departure rule copied from `render_github.py`. The departure rule runs on each aircraft's points thinned to ≥10 s (the spacing it was written for); the map itself is never thinned. **Decided 09-26: a rise counts toward a climb only when the thinned points are ≤60 s apart** (`CLIMB_MAX_GAP_S`; suggested by the Pi/network session, his call). Before it, an aircraft last heard low and heard again hours later higher up read as one climb, and its earlier arrival points were dropped. Frozen hours keep the old rule; only hours built after the installer run use it. Fixed 09-24: GA flag requires an N-number, not any N-callsign (Spirit, NKS, was caught); two-letter airline hints removed.
- **`live.html`** — fetches the hour files from raw.githubusercontent.com (Pages is not rebuilt), refetches only changed hours every 5 min. Points held in typed-array columns. Dots **viridis by altitude**, brightest at the ground; neutral labelled airport markers.

### Measured, on the May 1 day replayed at 2 s density (~815k positions)
- First push ~12 MB; later pushes ~0.5 MB (~30 MB/day of farm upload).
- `publish.py` peak memory ~490 MB (of the Pi's 2 GB); build ~12–18 s on the cloud sandbox — the Pi will be slower.
- `live.html` JS heap ~50 MB (was ~220 MB before typed arrays). Not yet tried on an iPad.
- SQLite ~78 bytes per row in the test database.

### Decided 09-24
- **Sampling interval: 2 s** (`IADMAP_INTERVAL_S`).
- **Retention: keep the log until the drive is nearly full.** Built as: below 10 GB free, the collector deletes the oldest hour at a time (one per call, again after the next 30 s commit while deleting helps) until 15 GB is free (`IADMAP_PRUNE_BELOW_GB` / `_TO_GB`), and stops when a deletion gave no space back (from 10-24; until then it looped a day at a time with no such check and could empty the log, §0); the database uses incremental auto-vacuum so the space really returns. Event logs in `events/` are never pruned. Growth estimate (not measured): ~1 GB/day, so ~7 months before pruning starts — measure after a day of running.
- **Helicopters count** for the over-the-farm figure and alerts, for now.
- **The office is not to go on the map** — the property line is already there.

### Settled 10-24
- **Runway headings:** the copied table held runway *numbers* read as degrees (KIAD 19/199); neither true nor magnetic. Replaced with true headings (OurAirports), which real finals confirm (§0). The page's copy of the rule (`isArrivalPt`) was removed on 10-24: the Pi decides.

### After installing, check
- Done 09-24: aircraft types come through (via the lookup); the aviationweather.gov weather feed parses.
- Done 09-26: installer re-run for the parcel / height-above-ground change. USGS reachable from the Pi: `farm ground: 25 points, 287-355 ft, mean 334 ft` (the fallback had been 380 ft). Lookup rebuilt: 618,617 aircraft, 490,518 with a type. First publish after it (11:45 AM): `farm.ground_ft` 340 (USGS), all 24 weather reports carry `altim_hpa` (so the METAR `altim` field name was right; 1012.6 hPa = 29.90 inHg), first parcel pass QTR3G at ~9,460 ft above the ground. Older passes under the 1 nm rule age out of the 24 h window.
- Still to check: drive growth per day, publisher memory on the Pi, `live.html` on the iPad (see §0).

### Emergencies and close approaches (built and installed 09-24)
- **Emergencies (7500/7600/7700) push to his phone** — his choice (09-24): text or GitHub; just him; everything the receiver hears. Built as `pi/alerts.py` → alert file on the `alerts` branch (deploy key) → `.github/workflows/emergency-alert.yml` opens an issue as github-actions[bot] mentioning @RDBFarm. Not with his own token: GitHub doesn't notify you about your own actions. Needs 3 consecutive reads (~6 s). Medical/min-fuel statuses logged only. Test with `alerts.py --test` after install; he needs the GitHub app with notifications on.
- **Near misses: a log plus map highlights, no push** (his choice, 09-24). `pi/proximity.py`: under 500 ft vertically and closer than **10 s at their relative speed**, kept between 0.15 and 1.0 nm (his request, 09-24: "a slow near miss has a much smaller radius than a fast one"); both airborne; flags near_airport / low / persistent / mlat / tcas. Thresholds are first guesses — tune after real traffic. The first live event (09-24, two AVL-callsign light aircraft passing 0.85 nm apart at ~200 kt relative, 21 nm WNW of the farm) prompted the speed scaling; under it that pass is not logged. Relative speed, not range rate: range rate is zero at every closest point. Call them close approaches, never near misses, unless a TCAS advisory confirms.
- **This readsb does report TCAS advisories (`acas_ra`)** — confirmed 09-24: 6 logged that day from 4 aircraft, all well above 15,000 ft (the first real one: VXP1191, a 737-700, "Level Off" at 30,400 ft descending towards UAL2748 level at 29,000 ft, 3:55:07 PM ET, ~38 nm SSW of the farm; "Clear of Conflict" 61 s later; closest ~4 nm and ~1,800 ft). Each RA is broadcast twice: the advisory and then "Clear of Conflict". **Not shown on the map since 09-25** (his call: "It was never a concern if it happened 4nm from each other"); still logged in `events/tcas.jsonl`, and a close approach flagged `tcas` is still shown.
- At phone width the live map's panels overlapped (inherited layout, worse with the Events list) until 10-24 (§0, review: second PR).

### Over the farm (built and installed 09-24)
- His request (09-24): an over-the-farm figure on the live map, highlight passes **under 2,000 ft**, push to phone **under 1,500 ft**, and **exclude prop planes**.
- **From 10-24 the headline figure is the forecast's measure** (his decision, §0 review: one rule for the map and the forecast): aircraft within 2 mi of the farm centre below 6,000 ft, props included (`farm.near` in `live.json`). The parcel rule below still drives the passes, the low-pass highlights and the alerts, and is shown underneath as "Over the land itself".
- `pi/farm.py`: within **1 nm** of the parcel centre (39.1506, -77.4612), any altitude, airborne; one log line per pass with its closest point. Altitudes are reported pressure altitude, not height above the farm.
- "Prop plane" = my reading, not confirmed by him: fixed-wing (L/S/A/G) with piston, turboprop or electric engines per ICAO Doc 8643 descriptors (`pi/aircraft_types.json`, from tar1090-db). Helicopters and jets stay in. **Unknown types: logged and shown on the map, never pushed** (his call, 09-24). Types come from `aircraft_lookup.py`, since readsb supplies none.
- From the May 1, 2025 cache (≤15,000 ft only): 228 aircraft within 1 nm of the parcel that day, median 3,800 ft, lowest 1,150 ft; 112 at 3,000–3,999 ft, 103 of them southbound and descending/level, noon–7 PM with a southerly wind — consistent with the approach to Dulles's southbound runways (an inference). One day only; north-flow days will differ.

### Map features added 09-24 (his requests)
- **Detail card** on tapping any event: both aircraft (callsign, registration, type and name, altitude, speed, heading, GPS or MLAT, ADS-B Exchange link), geometry, place, flags.
- **Full path of a selected flight** (search result or event) at every altitude and range, from `t/<hour>.json` built by `pi/tracks.py` (simplified for drawing; the log keeps every point). Prompted by AAL1623: its map points began at 13,450 ft (the 15,000 ft ceiling) and ended at 1,575 ft about 12 mi south of DCA, where reception from the farm runs out.
- **Near-the-farm search** (09-25, his request: "all planes within X miles of the farm between this time frame and this height"): header button 📍. Searches the full track files (all altitudes, departures included) segment by segment, so a crossing between two recorded points still counts; heights above the farm's ground (altimeter at the time, `farm.ground_ft` published in live.json). First real run: 11 passes within 2 mi and 0–3,000 ft in 24 h (PAT24 H60, SENTRY7 EC45, Cirrus N658CK at 0.3 mi…).
- Search by type needs the ICAO code (BE35 for the V-tail Bonanza, V22 for the Osprey); "BE35" found 8 on 09-25 — he may have typed a model name. **Plain-name search built 09-26**: names from `pi/aircraft_types.json` (fetched by the page from Pages), matched at word starts, plus aliases V-tail/V35 → BE35. On 09-25 data: Bonanza 24, V-tail 8, Osprey 2, Black Hawk 16, Cirrus 106.
- **Hide viewed incidents**: per browser, in localStorage; "Show N hidden" restores.
- The **Now** button stays at now; playback stops at the live edge.

### Map changes 09-26 (his requests)
- **Close approaches: only with a jet in them** (his words: "way too many flight instructor schools have close calls. maybe limit the close calls to jets?"). Page-side only: at least one aircraft with a J engine descriptor; "both jets" is the alternative if he wants fewer. The Pi still logs every one. On 09-26 data this removed all 5 that were showing (C172/PA31, P28A, BE20/BE9L, C172, C172).
- **Categories on the right** (his list: private, commercial, helicopters, military, military fighters, plus "experimental too"). One per aircraft, first match wins: fighter (type list, general knowledge, T-38s included) → military (US military address block ADF7C8–AFFFFF; 99% of tar1090-db entries there are flagged military; foreign military not recognised) → helicopter (descriptor H) → experimental (**changed 10-07, below**) → commercial (airline-style callsign on anything but a piston plane, so flight schools flying Cessnas under a company callsign count as private) → private. A military helicopter counts as military. Airport toggles moved into a folded "Airports" section. On a phone the panel folds to a "✈ Show" button. 09-26 counts: private 586, commercial 864, helicopters 24, military 38, fighters 0, experimental 9.
- **Tapping a dot on a route** opens the same detail card as an event (replaces the old small popup), with its whole 24 h path drawn. Registration is worked out from a US address (N-number rule; 392,062 of 392,213 agree with tar1090-db, the rest look like re-registrations); the airline name comes from `pi/operators.json`, fetched on first tap.

### Flight search (10-20, his request)
- His words: searching A340 "I get a return of 9. But not all of them are 340s ... I want the search results to stay until I click on a button that clears the search, and I want the details of the flight to show up when I click on that flight in the search results." Also: tapping a line "very carefully or it brings up something else".
- **What was wrong (real 10-20 data):** callsigns matched anywhere inside, so "A340" found RPA3401, RPA3409 and the address label A3401A; and an aircraft's stretches without a callsign are kept under its address, so the two Lufthansa A340-300s (3C64F4, 3C64F7) showed as six "flights". Right answer: **4 flights by 2 aircraft**.
- **Now:** type codes match anywhere; a callsign matches from its start, or by its number when only digits are typed ("2853" → UAL2853); an address label only when typed in full. One row per flight (`searchResults()`): a no-callsign stretch joins the same aircraft's nearest flight within 3 h. The count reads "N flights by M aircraft". A row shows callsign · type code · first seen (hover: type name, address).
- **Sticky:** `searchText` holds the typed search; results and highlight stay until "Clear search" (button beside the count), the ✕, or emptying the box. Opening a card, closing it, an event, or the 5-minute refresh keep them. (Before: tapping a dot replaced the highlight and closing the card wiped the search; the refresh also cleared an open card's highlight when the box was empty.)
- **Clicking a result** opens its details card (the dot card), draws its whole path, and sets the timeline just before it appears. From 700 px wide the card opens beside the search panel (`left:294px`), so the list stays in reach.
- **Taps while a search is on** consider only the searched aircraft, so the dimmed traffic around them can't open the wrong card; a tapped result's row is highlighted. Test: `test_search_matches_stays_and_opens_the_card`.

### Event cards (10-22, his requests)
- "have the events log detail screen (like when there is a close call) to have the button to ignore it moved to the top instead of the bottom": "Hide from the map" now sits right under the card's title (`openEventCard`, `.ev-actions`).
- "if there is a 'Training Wheels' event, to just ignore it altogether": "Training Wheels" is plane-alert-db's tag for military training aircraft (1,438 in `notable.json`: T-38s, T-6s, gliders; 859 USAF), and the ★ label shows a notable aircraft's first tag. A **close approach** with such an aircraft is not shown (`isTraining` in `eventItems`), counted in the events note as "involving military training jets not shown"; still logged on the Pi. In the 23 days to 10-17 there were 11 (3 with both aircraft training jets, e.g. T-38 pairs RO01/RO02, CC01/SP02, JR01/JR02), and **no emergencies or farm passes** by one, so those are left as they were: a 7700 from a training jet would still show. Test: `test_events_training_jets_hidden_and_hide_button_on_top`.

### Busy times (10-22, his request; page from 10-23)
- His words: "when the busiest times are for dulles air traffic ... segregate it by day as well? So over the busiest time might be 4:15, but on wednesdays it might be 6pm", then "make the chart and put it on another page on the website".
- **The rule is mine, not his** (`pi/busy.py`, docstring): on a full path from the `t/` files, a stretch below 1,500 ft within 5 nm of the Dulles reference point is a landing (at its first point) if the path was at or above 2,500 ft in the 20 minutes before, and a take-off (at its last point) if it reaches 2,500 ft within 15 minutes after; paths split at 30 minutes unheard. **Not checked against FAA counts.**
- **Measured, 27 whole days 09-26 – 10-22** (`tools/make_busy.py` over the `t/` files, = `busy-seed.json`): about 897 a day (~450 landings, ~440 take-offs; 91% airline callsigns). Busiest hour 3:30–4:30 PM on every weekday (78–90); Saturday 5:30–6:30 PM (76) and the quietest day (819); Sunday 3:45–4:45 PM. Landings peak 3:30–4:30 PM; take-offs have three peaks (8:30–9:30 AM, ~1 PM, 5:30–6:30 PM). Only ~4 of each weekday so far, so the busiest *15 minutes* per weekday swings; the page says so.
- **On the Pi:** `publish.py` calls `busy.update` each run: every hour whose `t/` file is frozen is counted once (from it and its neighbours, since an approach can start in the hour before), after the next hour is frozen or 3 h have passed; counts per local quarter-hour (America/New_York) go into `/mnt/flightdata/iad-map/busy/state.json`, which grows forever past the 30-day window. `busy.json` on `live-data` (~0.5 KB a day; the seed is 13 KB for 29 days). The first run counts the 30 days of `t/` files, 60 s a run at most.
- **`busy.html`:** heat grid (Every day + Mon–Sun × 96 quarter-hours, viridis, white box on each row's busiest hour), stacked bars for the chosen row (Okabe-Ito orange landings, blue take-offs), table of busiest hour / busiest 15 min / per day. Averages over **whole days only** (23/25 hours on clock-change days); "Last 4 weeks" / "All recorded" once there are more than 28. Falls back to `busy-seed.json` (with a note) until the Pi has published `busy.json`. Opens on **Landings** (10-23, his request: "pre-select 'Landings'"). **Tap for figures** (10-23, his request: "give me the figures when I click on a bar or timeframe"): a time on either grid or a bar on either chart writes its numbers under both of that pair (`pickText` / `fcText`), marks the cell (white outside, black inside, so it isn't the busiest-hour box) and shades the bar; hover previews on a desktop. Linked from both live pages ("Busy times"). Tests: `tests/test_busy.py`, `test_busy_times_*` in `test_pages.py`.

### Farm forecast (10-23, his request)
- His words: "an air traffic forecast for the farm ... expected plane activity based on the expected winds, an hourly as well as blocks of time in the coming 7 days". He pointed at the weather rdbf-app already grabs: its forecasts are **Open-Meteo** (`docs/live-weather.js` there), so this uses Open-Meteo too, fetched by the page (Dulles, 10 m wind, knots, `timeformat=unixtime` so no time-zone parsing). Nothing on the Pi.
- **My choices, unconfirmed:** "activity" = aircraft within **2 miles of the farm centre below 6,000 ft** (`farm.FARM`); blocks Night 12–6a, Morning 6a–12p, Afternoon 12–6p, Evening 6p–12a, Eastern. The note under the heading is his wording (10-24): "Planes expected to fly within 2 miles of the farm during final approach." The shares and the wind go under (i). Block cells show only the number (10-23, his request: the "lands N / S or N" labels went; tapping an hour gives the wind and the chance of landing south).
- **Measured** (`tools/make_farm_model.py` → `farm-model.json`; t/ files 09-25 – 10-23 and KIAD METARs from the Iowa Environmental Mesonet): Dulles landed south (arrivals' direction in the 2.5 min before they got below 1,500 ft; 80% of an hour's landings) in 162 hours and north in 442. **Landing south, 76% of its arrivals passed within 2 mi below 6,000 ft; landing north, 2%.** Higher up, 7.7 an hour either way, so not forecast. Landing south against wind along the runways (speed × cos(from − 181°), 181° = measured south landing direction): under 0 kt 4%, 0–2 kt 28% (calm included), 2–4 kt 72%, 4+ kt 100%. The flow stayed the same as the hour before in 555 of 568 hour pairs.
- **The rule:** expected per hour = Dulles's average landings for that weekday and hour (last 28 whole days of `busy.json`) × (p·0.76 + (1−p)·0.02), p from the forecast wind. **Checked on the same weeks with the winds that blew** (in-sample, not forecasts; re-measured with the shared code, variable winds counted as calm): hourly correlation 0.81; 6-hour blocks 0.89; blocks it called quiet (<10) median 2, busy (≥40) median 109, but one "busy" block had 2: in light or variable wind the rule hedges (26% south) and a busy hour of Dulles landings then gives a middling number that is wrong either way. **But Dulles doesn't always follow the wind:** 10-11 3–5 PM it landed south into a 340° 8–9 kt wind and a "quiet" block had 118. Forecast error comes on top and wasn't measurable here (Open-Meteo is blocked from the cloud sandbox; the page was checked with NWS hourly winds standing in).
- **Kept up to date by the Pi, and scored (10-23, his request: asked whether it would improve with more data, "yes, build both"):** `pi/farmcast.py`, run by `publish.py` after `busy.py`. Each settled hour is counted once (its `t/` file and neighbours): `[flow, landings, near, above]`, plus KIAD's wind `[deg or None, kt]` from publish's METARs, or from the IEM archive for hours they no longer cover (at most every 6 h); state `/mnt/flightdata/iad-map/farmcast/state.json`, which grows past the 30-day window. `farm-model.json` on `live-data` is `model()` of every hour so far, with `"ready": true` from 336 hours (two weeks); **the page uses the Pi's copy once ready, the repository's (`tools/make_farm_model.py`, same functions) until then**, and says how far the Pi has got. Variable winds count as calm (the first tool dropped them); `AXIS_DEG` = 181 is fixed (the tool prints what it measures).
- **Each morning's forecast is kept and checked:** on the first run after 6 AM Eastern (once ready) the Pi fetches the same Open-Meteo forecast and stores what the page would show for every hour not yet over (`predict()`, the Python twin of `buildForecast()`; `test_farm_forecast_follows_the_wind` holds the two equal to 9 places). Kept 35 days. `score()`: every whole 6-hour block (5–7 hours on clock-change days) that is over and counted, forecast against planes that came, by days ahead (0 = later the same day), last 28 days: blocks, median miss, median forecast and actual. The page shows "How it has done" for 0, 1, 3 and 6 days ahead once a lead has 4 blocks. Tests: `tests/test_farmcast.py`, `test_farm_forecast_uses_the_pi_once_ready`.
- **Public:** `flights.html`'s link is `busy.html?public` (rewritten by `make_public.py`), which removes every `.farm` element; a script line touching a farm element must cope with it missing (the first version crashed there; `test_busy_times_public_page_has_no_farm`). Tests: `test_farm_forecast_follows_the_wind`.

### Public page and icon (09-26, his requests)
- **`flights.html` is the live map without the farm** (his words: "a duplicate website but without the farm relevant data"). Built from `live.html` by `tools/make_public.py`: sets `FARM_MODE = false`, empties `PARCELS` (it carried the address), and drops the HTML between `<!--farm-->` markers: over-the-farm summary, 📍 Near the farm, "RDBF receiver". With `FARM_MODE` off there are no low passes, no "From the farm" or height-above-your-ground rows. **Edit `live.html` only, then run `python3 tools/make_public.py`**; the `Public page up to date` workflow fails otherwise. Anything new about the farm in `live.html` needs a marker or a `FARM_MODE` guard.
- It is not private: the farm page, `live.json` (farm centre, passes) and this repo are all public, the address is rdbfarm.github.io, and the coverage pattern centres on the receiver. It only leaves the farm off the page people are sent.
- Tab / home-screen icon: `icon.svg`, `favicon-32.png`, `apple-touch-icon.png` (180 px, square; iOS rounds it). The PNGs were rendered from the SVG in Chromium.

### Remarkable aircraft and radar echoes (09-26, his request)
- Prompted by a close approach between 92-9000 and what he took for a military helicopter (3:34 PM 09-26). 92-9000 is one of the two VC-25A Air Force One 747s (with 82-8000), not a retired one; whether a replacement is in service in 2026 is not known here. The "helicopter" was `~27b203`: a TIS-B target (FAA radar re-broadcast, no identity) at 329 kt, 0.1 nm and 75 ft from the 747, on its course. Almost certainly the 747's own radar return.
- **Nicknames:** `pi/notable.json` from plane-alert-db (ODbL 1.0, credited in the file and on every card that shows one), built by `tools/make_notable.py`. **Government, military and public-service categories only**; celebrity/wealthy owners' jets, vanity plates, "as seen on TV" and the joke categories are left out, and the list's operator field is never copied (it can name a private owner). His decision: "go ahead as described". 13,850 aircraft; 134 of 4,746 heard in the first 35 h were on the full list (Air Force One, two Air Force Two C-32s, Ospreys, police, medevac). Shown in detail cards (★ tags, type, category, link, and the registration when nothing else gives one) and as ★ in event labels. Refresh by re-running the script now and then; nothing on the Pi.
- **Radar echoes hidden from close approaches** (page only; the Pi still logs them): a `~` target within 30° of the other's course and within 30% of its speed. 13 of the first 26 logged close approaches involved a `~` target; 11 matched that rule (0.01–0.1 nm apart), the other 2 crossed at 59° and 127° and are probably real non-ADS-B aircraft.
- Event labels and tooltips are now HTML-escaped (they carry callsigns and community text).

### History heat map (09-27, his request)
- His words: "a heatmap over the history of the database ... the resulting lines shouldn't get broader, just brighter, and with the absolute size of the planes weighting the brightness as well. So an approach that has 100 private planes would have the same brightness as 10 commercial planes (in the broadest sense possible)."
- **`pi/heatmap.py`**, run by `publish.py` every 15 min: a fixed grid of ~110 m cells (1,460 × 1,462) over the map's 50-mile area. Each aircraft's path is drawn through the cells it crosses (straight segments between positions ≤30 s apart), counted **once per cell per aircraft-hour**, so slow aircraft and the 2 s log don't brighten a cell more and lines keep one cell's width. **Weight = ICAO wake turbulence category** (`wtc` added to `pi/aircraft_types.json` from tar1090-db): light 1, medium/heavy/super 10, unknown 1 (mostly private planes). My mapping of "private vs commercial, broadest sense" onto size classes; business jets and Black Hawks are medium, so they count 10.
- Same positions as the live map (≤15,000 ft, 50 mi, departures dropped): frozen `h/` files are read back; hours before the first one kept come from the log with the map's own filters (`publish.map_points_for_hour`). Checked on test data: the two routes agree in every one of the 2.1 M cells. The sum lives in `/mnt/flightdata/iad-map/heat/` (acc.f32 + state.json) and grows forever, beyond the 30 days of hour files.
- Published as `heat.png` (8-bit greyscale, log-scaled: `round(255·ln(1+w)/ln(1+max))`) + `heat.json` on `live-data`, **at most once a day** (~150 KB for 3 test hours; expect ~1 MB when full), so the upload stays small (HubDuo). Image build 0.5 s in the sandbox.
- **`heat.html`**: Leaflet, dark basemap, viridis (colourblind-friendly), transparent where empty, contrast slider; legend folds on a phone. Linked from both live pages (history icon, a clock with a counter-clockwise arrow, drawn as inline SVG since there is no emoji for it; was 🔥 until 09-30; header buttons are icon-only on phones now).

- **By wind (09-27, his request):** "I want to be able to type a degree or use a pointer to move the wind position. So we can see what the heat map looks like when the wind is blowing at any one direction." Each hour also goes into a grid for KIAD's wind at the time: the report nearest the middle of the hour, direction the wind blows **from**, to 10° (`heat/wind/000..350.f32`), or `calm` (calm or variable). Winds are kept per hour in `heat/state.json` as reports arrive (an hour not yet counted takes the nearest report seen so far, `wind_d`; until 10-24 the first seen was kept, which was the hour before's :52 report, so hours counted before then sit in the wind grid of the hour before them — §0, review); the first run fetches up to 168 h back once for the logbook's early hours. An hour waits up to 30 h for its wind, then goes into the all-winds grid only (`no_wind_hours`). Only wind grids that got new hours are re-imaged, once a day (`heat/<key>.png`), so the daily upload is the all-winds image plus a few wind images (expected ~1–5 MB/day once full; 50–80 KB per wind-hour on test data).
- `heat.html`: a dial (drag the pointer; rim ticks show hours per direction), a degree box, a ± range (0–180°), All winds / Calm. A range adds the chosen images back in weight terms and re-scales the sum. Checked against the Pi's grids on test data: one direction and ±180° light exactly the cells the grids do.

- **Installed and verified 09-27** (installer run ~3:30 PM): first heat build covered 77 hours, Thu Sep 24 10 AM – Sun Sep 27 3 PM, 10,194 aircraft-hours, all with a wind report. Only northerly winds so far (310°–050°; most hours at 320° and 000°/350°), so south-flow views are empty until the wind turns. First upload of all images 2.9 MB (heat.png 834 KB, wind images 55–324 KB each); after that, heat.png plus the wind images that got new hours, once a day.

- **CARTO basemaps need the key** (09-27): `heat.html` first loaded `dark_all` tiles without `?key=…`, and every tile came back as a "carto.com/basemaps/apikey" notice. Any new page must use the same public browser key as `live.html` (`TILE_DARK`/`TILE_LIGHT`). CARTO is blocked from the cloud sandbox, so this can't be seen in local screenshots (tiles are aborted there).

- **By airport (09-27, his request):** "give me just airports to select" (he dropped plane-type filters: picking airports stands in for them). Each aircraft's cells in an hour are also split by the live map's airport zone (`classify_airport`, ids 0–9, 7 = en route) of the position that first reached the cell, into `heat/ap/<id>/all` and `heat/ap/<id>/<wind>`, so airports combine exactly with the wind and the zones add up to the total (checked: 0 cells differ, overall and per wind; log-built hours give identical zones to the files). Hours built before airports existed are split once on the next run (`ap_hours`). Grids are memory-mapped files now (~400 grids; the Pi holds only the pages an hour touches); image building visits only lit cells. On test data 40 airport images = 409 KB for 4 hours. He offered to drop the small airports to make it easier; it wouldn't have (every position needs a zone), so all ten stay.
- `heat.html`: ✈ Airports panel (live map's list, All/None); a selection sums the chosen zones' images for the chosen winds, one image at a time, at most 24 decoded images kept (iPad memory). "ⓘ How it works" opens the explanation in a window.

- **By size in two colours, and take-offs (09-27, his request):** "size, orange and blue and departures added or selectable". Layout version 2 of `pi/heatmap.py`: every hour is built **from the log** (`publish.heat_points_for_hour`), because the hour files hold only the live map's arrivals. Arrivals are exactly the live map's positions; take-offs are the airborne positions its take-off rules drop. Grids per scope (all / zone) × wind (all / key) × class (arrival/take-off × small/big): `heat/g/<scope>/<wind>/<class>.f32`. Images `heat/v2/<scope>/<wind>/<a|d>.png` are RGB with red = small, green = big, so the file count doesn't double. A layout change rebuilds from the log, 12 hours per publish run, keeping the winds; the old images stay published until the new set is complete, then are removed. Checked on test data: arrivals equal the v1 heat map in all 146,578 cells; zones add up per wind and class; the page matches the Pi's grids for arrivals, with take-offs, and KIAD + calm + take-offs.
- `heat.html`: "Colour by size" (Okabe-Ito orange = small, sky blue = airliners/jets, one shared log scale so 100 small = 10 big, colours add towards white where both fly) or "Single colour" (viridis, as before); Arrivals / Take-offs / Both buttons (09-28, his request; replaced the "Include take-offs" switch). Arrivals = the live map's traffic. First real layout-2 build (published 7:45 PM 09-27): 81 hours, 12,359 aircraft-hours; lit cells arrivals 1,037,401, take-offs 325,544, both 1,117,104; first upload 10.4 MB (234 images). With a v1 heat.json it falls back to single colour, arrivals only, with a note. His colour vision: his notes say colourblind; orange/blue was suggested over red/blue for that reason and he chose it.

- **Wide (10-13, his request; built 10-14 on my stated assumptions, awaiting his check):** "I might like that for a much larger area and include higher altitudes." He did not give an area; I proposed and built these, and he can change any of them: **200 statute miles each way from Dulles, every height**, a box 36.044–41.844 N, 81.180–73.731 W (`heatmap.WIDE`); cells ~445 m (0.004° × 0.00512°, 1,450 × 1,455, the same pixel count as the near map); the same line and weight rules; **no wind, airport or take-off split** (one grid per size, `heat-wide/{s,b}.f32` beside the near state; one image `heat/wide/all.png`; `heat-wide.json`). Every airborne position in the box (`publish.wide_points_for_hour`: `on_ground = 0`, altitude known, ground vehicles out; the log, not the hour files). Page: a "Wide" button on `heat.html` (remembered as `#wide`), hides the airport/wind/take-off panels, shows the near map's 50 miles as a dashed circle, swaps the small airports for the large ones within 200 miles (OurAirports `large_airport`: RIC, PHL, ORF, PIT).
- **Measured for it** (t/ files, every aircraft, 10-07–10-14, 168 h): 663 aircraft per hour file (median); about 160 aircraft in the box at once; aircraft-time within 50 mi 25%, 100 mi 53%, 150 mi 84%, **200 mi 98.3%**, 230 mi 99.9%; farthest 275 mi. The box's corners reach 283 mi. **A first figure of "99.9% within 200 mi" was wrong:** it came from `all_tracks.pkl`, which holds only aircraft that came within 6 nm of IAD below 2,500 ft (the DEN-flight analysis), so it saw only Dulles traffic. Measure reach from all of `t/`, never from that cache.
- **Pi cost:** ~300,000 positions per hour over the box (the log at 2 s), ~10× the near map. Read sorted by aircraft and streamed one aircraft at a time (`ORDER BY hex, pos_t`, `PRAGMA temp_store = MEMORY` so the sort never touches the SD card); sandbox: ~3 s and ~110 MB peak per hour on a 6-hour test log of every aircraft. Guess for the 4B: ~10–15 s per hour, so each publish run adds hours for at most `WIDE_BUDGET_S` = 120 s (and 12 hours); the first build from the logbook (since 09-24) should take most of a day of runs. Images once a day like the near map: 475 KB for 6 test hours; expect a few MB once full.
- **Checks before merging:** the near map's `hour_cells` gives identical output before and after the refactor (60 real hours, 1.3 M cell weights); the page draws exactly the Pi's lit cells for near (226,952) and wide (126,437), desktop and 390 px, no errors; `#wide` opens straight on wide; without `heat-wide.json` it says the wide map is not built yet.
- **Installed 10-14 (~10 AM) and first published 8:15 PM the same day:** 490 hours (Thu Sep 24 10 AM – Wed Oct 14 8 PM, the whole logbook), 258,915 aircraft-hours, `heat/wide/all.png` 2.1 MB, largest cell weights 1,629 small / 119,820 big. The catch-up took about 10 hours of runs, so the 4B managed about 12 hours of log per 120 s run (≤10 s an hour; my guess was 10–15 s). Checked from `live-data` (`heat-wide.json`, `live.json` `heat_wide.built`), not on the Pi.
- **The knot north of State College (10-16, his question):** the Philipsburg VORTAC (PSB, 40.9163 N 77.9927 W): the wide map's brightest 7×7-cell block is 0.3 nm from it (OurAirports). FAA ATS Route open data: 18 routes meet there (J49, J59, J60, J61, Q71; V6, V30, V33, V35, V184, V265, V501, V576; T216, T307, T430, T460, T477). From the t/ files, Oct 7–14: ~345 aircraft a day within 2 nm, median 32,000 ft, 9 below 18,000 ft all week; 42% westbound from NYC/PHL still climbing (FlightAware: EWR–CLE, LGA–MSN, PHL–SEA), north–south overflights ~FL350 (YUL/SYR–ATL, FedEx MEM–MHT), ~54 a day descending into IAD/DCA from the north (YOW–IAD, SYR–IAD, ROC–DCA). **Pitfall:** label a pass with the callsign of the hour file it falls in, not the aircraft's last callsign; regional jets fly several numbers a day, and the first pass named the wrong flights.
- **Radio beacons on the wide map (10-16, his request):** "mark the beacons on the wide map with the ability to turn them off". `beacons.js` (built by `tools/make_beacons.py` from the FAA NAVAID System open data, public domain): the 70 VOR, VOR/DME and VORTAC stations in the WIDE box (NDBs, DMEs and stand-alone TACANs left out). Hexagon markers in wide mode only, labels from zoom 8 (at 7 they pile up), tap for name and whether it is on the high-altitude charts; a "Radio beacons (VORs)" switch in the legend, remembered per browser (`localStorage` `heat.beacons`). Their pane is at z-index 660, above the airports' canvas pane (650): below it, taps never reached them (found in testing). Re-run the tool if the FAA decommissions VORs (it is retiring some).

### Live map line thickness by plane size (10-03, his request)
- "change the thickness of the lines to equate the size of the plane as well ... just like what you did on the history page." On `live.html` and `flights.html` each dot's radius is multiplied by a size factor from the ICAO wake-turbulence class in `pi/aircraft_types.json` (`wtc`, same table as the history map): light 1, medium 1.7, heavy/super 2.4; unknown types count as light. It combines with the density growth (`1 + 1.5 × level`, was `1 + 2.5 × level`, reduced so a busy widebody route doesn't smear). Legend: "Brighter = more aircraft · thicker = bigger plane". Page-only. Business jets and Black Hawks are class M, so they draw as thick as airliners (his history-map weighting does the same).

### "Experimental" means pre-production (10-07, his request)
- His words: kit-builds are "not 'experimental' they are just hand built which is different, and honestly not that interesting ... make Experimental, truly planes that are in pre-production instead." Kit-builds (RV-7s, Long-EZs, Lancairs...) now fall through to Commercial/Private like any other plane.
- **ADS-B has no pre-production flag and neither does tar1090-db**, so Experimental is by type: `PROTOTYPE_TYPES` in `live.html`: X-2, X-3, X-29, X-32, X-47B, X-49, X-55, X-59, Boom XB-1, Joby JAS-4, Stratolaunch, Proteus, Global Flyer. It is checked first (a military X-plane counts as Experimental). Expect it to read **0 almost always**: none of these was heard in ~12 days of data.
- **Tried and rejected, so no one repeats it:** (1) "owner is the manufacturer" (the database has year and owner): the Boeing-owned planes heard were new 737 MAX / 767s on revenue flights for United, Southwest, Allegiant and UPS (registered to Boeing until delivery), and name matching caught Amazon, flying clubs and the like. (2) Boeing's `BOE` callsign: BOE751 (a 787-9) is a pre-*delivery* flight of a production aircraft, not pre-production, and the callsign can't tell test from delivery. (3) Types "still in certification": status changes and my information was stale: the 737 MAX 7 (B37M) is flying for United (14 callsigns heard) though I'd have called it uncertified. The 777-9 (B779) was left out for the same reason, then **added on 10-08 at his request** ("Go ahead with the change", after I offered it): it counts as Experimental while in flight test. **Its status was not verified** (none heard in the files kept); **remove `B779` from `PROTOTYPE_TYPES` when 777-9 deliveries start**, or every in-service one will be called experimental.
- To add something (a new X-plane, the 777-9 once he wants it, an eVTOL type), add its ICAO type code to `PROTOTYPE_TYPES`. Page-only change.

### Text messages (09-26, his request)
- He asked whether alerts must go through GitHub. He tested his carrier's email-to-text address from his own email and the text arrived (09-26). So every pushed alert (emergency squawks, pushed low passes, within-sight alerts) is also emailed to that address as one plain line (no emoji, ≤160 characters). GitHub stays the written record.
- Sent through an email account used **only for this** (suggested: a new Gmail with an app password), not his personal one. Settings in `/var/lib/iad-map/text.json`, mode 600, owned by `iadmap`, written only by `sudo -u iadmap python3 /opt/iad-map/alerts.py --setup-text` (password typed on the Pi, never in the repo or a chat). No restart needed after setup; `alerts.py --test` also sends a test text. Failed texts retry every minute; without `text.json` nothing is texted.
- Some carriers have shut their email-to-text gateways; if his stops, Pushover was the suggested fallback.
- **Set up and verified 09-27**: test text arrived. Gotchas on the way: (1) the first `--setup-text` run saved the example address from the prompt text, fixed on the next run; (2) Google's "App passwords" page said "not available for your account" until 2-Step Verification was switched on as such (adding a passkey and a recovery phone is not enough); (3) Gmail answered `535 5.7.8 Username and Password not accepted` because Google kept switching him to his other account, so the app password was made there. Fixed by creating it while signed in as RDBFFeed. He was told to delete any stray app password from his personal account. To check the saved password without showing it: `sudo -u iadmap python3 -c "import json; print(len(json.load(open('/var/lib/iad-map/text.json'))['password']))"` (16 for a Gmail app password).

### Within-sight alerts for Air Force One and the doomsday planes (09-26, his request)
- His words: "For AF1 and Doomsday, it would be very cool to get an alert if it is within sight of the farm. as in I could see it with my own eyes, even if I would not be able to decern what it is."
- `pi/watch.json` (built by `tools/make_notable.py`: plane-alert-db entries tagged Air Force One or Doomsday Plane): VC-25A 82-8000 and 92-9000, VC-25B "bridge" 25-3300, E-4B 73-1676, 73-1677, 74-0787, 75-0125, plus four Russian entries that won't come by.
- `pi/alerts.py` pushes once per visit (a visit ends after 30 min out of range) when the straight-line distance from the farm centre, height above the farm's ground included, is within `SIGHT_MI` = 12 statute miles, or within KIAD's reported visibility when under 10 mi. In range on 2 reads in a row. The alert says where to look (compass, degrees above the horizon), distance, height, heading. Logged to `events/sightings.jsonl`.
- **12 miles is my estimate** of how far a big jet shows as a dot in clear air, not a measured or stated figure; he can change it. Cloud between the farm and the aircraft is not known.
- Tested by running `watch()` over scripted reads (AF1 passing ~6 mi E, returning 40 min later, an E-4B 20 mi out, a Cessna overhead, and again with 3 mi visibility): two AF1 alerts, nothing else; nothing at 3 mi visibility.
- **Only when worth looking (10-18, his request):** "make it so sightings look at the direction of the plane in terms of if it is even worth while". Every one of the first 7 sightings (10-07 – 10-13) had pushed at exactly 12 mi, the moment it crossed the line, so "look SE" was where it first appeared. Now the path is projected straight ahead (track, ground speed, climb/descent; `sight_path`), and it pushes only if that path is **in sight within 5 min for at least a minute**, in sight meaning ≤ `SIGHT_MI` and ≥ `SIGHT_MIN_ELEV` = 5° above the horizon (my estimate of the treeline; his to change). The alert gives the closest point (direction, height, distance, minutes away) and where it is now. In range but not worth it is logged once per visit with `why_not` (heading away, too low, only briefly in sight). **Replayed on the 7 real sightings** (t/ files): all 7 still alert, 1–9 min earlier; the predicted closest direction was within one compass point of the actual closest in 6 of 7 (the old direction: 2 of 7). The actual passes were much closer than the alerts said: 5 of 7 came within 2.7–8.5 mi, three of them 58–84° up. Straight-line prediction misses turns (the 10-07 doomsday plane was predicted 5 mi WNW and stayed 11.5 mi SSE).
- **Watched flights (10-18, his request):** `pi/watch_flights.json` (hand-kept; `watch.json` is generated) names flights by callsign with optional Eastern dates; the same rules as AF1. First entry: **UAL2853 on 2026-11-21** (Denver–Dulles; usually a 737-900 landing ~5:05–5:25 PM, likely after sunset). Simulated south-flow arrival down the west lane: pushes ~4–7 min before passing, "look W, about 46° up, 1 mi away".

### 30-day history (built 09-26, his request)
- He first put it on hold until the Barn Office HubDuo hangs after uploads are explained, then (same day): "as long as the file sizes don't change with the longer timeline on the webpage I am comfortable changing that now." So the condition is upload size, and it was measured.
- **Pi (`publish.py`, `tracks.py`):** finished hours (already frozen since PR #34) are kept `KEEP_DAYS` = 30 local days instead of leaving with the 24 h window. They stay on disk and are **never read back into memory** (30 days ≈ 1.5 GB; the Pi has 2 GB). Files live in a folder per UTC day, `h/<YYYYMMDD>/<HH>.json` and `t/…`, so a push rewrites only today's short folder listing; the flat files were moved into folders on the first run (same bytes, nothing re-sent). One `d/<YYYY-MM-DD>.json` per finished **Eastern** day (23 or 25 h at DST), written once ~1:10 AM: its hour files with fingerprints, counts, weather, events and farm passes (enriched), and `first_hour` when the day starts late. If the weather fetch fails that night the file waits (up to `WX_RETRY_DAYS` = 3, then goes without), and a recent day on disk with no weather is built again — from 10-24, after `d/2026-10-21.json` went out with none. `live.json` gains `days: [{date, hash}]`. Full `git gc` once a day (it rewrites every kept object); every other run only `git prune`.
- **Measured before merging** (local bare remote, same data, old code vs new, 30 days of archive in place): pushes 224,837 → 226,342, 332,369 → 333,876, 408,331 → 410,857 bytes (+0.5–0.7%, the days list and folder listings); the switch to folders cost 234 bytes on top of a normal push; days dropping off the end cost ~5–6 KB pushes. Confirm with vnstat on the Pi afterwards.
- **Page:** the "Last 24 hours" label is a day picker. A day loads alone (its hour files fingerprint-checked), opens at its last recorded moment, headings read the date, "Now" returns to the rolling 24 h and frees the day. The Near-the-farm search works on the chosen day. `flights.html` gets it via `make_public.py`.
- 30 days is my suggestion, not his decision; the Pi holds months. GitHub size ≈ 17 MB/day compressed, ~0.5 GB at 30 days: check Settings → repository size after the first week.

### Weather radar (built 09-30, his request)
- A 🌧 button on the live map (and flights.html) shows NEXRAD base reflectivity for the moment the slider shows: IEM's WMS-T archive (`n0q-t.cgi`, layer `nexrad-n0q-wmst`, US composite every 5 min, from 2011, near real time; capabilities list the time extent to 2026-12-31, presumably extended yearly). "Now" uses the frame 10 min back (newest can lag); dragging back or a past day shows that time; the layer only refetches when the 5-minute frame changes. Opacity 0.6, under the dots.
- **The TIME parameter must include seconds** (`2026-09-29T19:45:00Z`): without them IEM returns an empty image for every time, which looked like three dry days across the eastern US.
- RainViewer (from an earlier chat's plan) was not needed: IEM covers "now" too.
- A grey (colourblind) mode was tried and removed: greying the NWS palette makes the faintest returns (pale cyan) the brightest, so night-time fog/clutter looked like heavy rain. A proper colourblind-friendly radar, and hiding the faintest returns, would need each tile re-coloured by reflectivity; offered, not built.
- The cloud sandbox reached IEM and RainViewer only after he widened the environment's network access (09-30).

### Planned, not built
- **Layering live tracks on the heat map** was the original idea; the live map is a separate page instead, and the historical map is frozen.
- **Self-reported health + a "data stopped" watchdog** (disk, log growth, memory, failed pushes in `live.json`; a scheduled Action alerting after an hour without uploads): proposed 10-18, **he said "no need for now"**.

### Checked 10-18 (for the parked items)
- **777-9 (`B779` in Experimental):** no deliveries until at least 2027 (Aviation Week, "Boeing Confirms No 777-9 Deliveries Until At Least 2027"; an Oct 2026 report names Lufthansa as launch operator in 2027). Keep it until then; he agreed to drop it when deliveries start.
- **Dulles middle runway (1C/19C):** closed "at least through November 2026" (Loudoun County notice, 09-25); one report says through Nov 22. Our tracks agree: landings 10-16 – 10-18 used two lanes only, ~0.75–1.0 nm west and ~1.0 nm east of the IAD reference point (340 landing south, 659 landing north), nothing in the middle. So the Nov 21 seat advice (seat A, south-flow days only) stands.

---

## 5. Security notes (09-24)

- The old `deploy_to_github.py` (Mac only, never committed) held a classic GitHub token for RDBFarm. **Revoked 2026-09-24**: it was the "Claude deployment" token in his list (shown as "never used", which was wrong); deleting it made his Mac's check return "Bad credentials". The deploy script and `Deploy_Flight_Map.command` can be deleted.
- **You cannot test a GitHub credential from a cloud session.** The session's gateway substitutes its own GitHub login on every api.github.com request: a made-up token also returned `login: RDBFarm`, with a moving expiry. Checks made that way on 09-23/24 reported the token alive with no scopes, and were all meaningless. Test credentials from his Mac, and test the test with a fake token first.
- The old `fetch_adsb_data.py` contains S3 keys described as ADS-B Exchange's public sample keys — unverified. Keep them out of this public repo.
- The old scripts disable HTTPS certificate checks (`ssl._create_unverified_context`). Don't carry that into new code.

---

## 6. Historical map — frozen (09-24)

Kept for reference only; not to be fixed or rebuilt.

- Built on his Mac by `fetch_adsb_data.py` (ADSBx readsb-hist, every 10 s → `iad_points_cache.json.gz`, 12 values per point: lat, lon, alt, time, hex, flight, category, gs, track, ac_type, squawk, baro_rate) and `render_github.py` (classifies, filters to arrivals, writes `iad_map_github.html`), deployed by `deploy_to_github.py`. None of these are in the repo. `fetch_iad_heatmap.py` is an earlier, unused version.
- Cache: 888,101 points, 5,800 aircraft, 8,640 snapshots exactly 10 s apart. The page shows 163,646 after `KEEP_EVERY_N = 2` and the arrival filters.
- Known issues, left as they are: `KEEP_EVERY_N = 2` halves the data by file position; the GA rule catches any N-callsign; a missing altitude becomes 0; its colours are red/green/yellow and its legend describes altitude while dots are coloured by airport; weather for 8 PM–midnight Apr 30 is wrong; time conversion is fixed at UTC−4.

---

## 7. Where things live

- Repo: `rdbfarm/iad-map` (GitHub Pages). Live map: `live.html`. Historical map: `iad_map_github.html`.
- Pi code: `pi/` (installed to `/opt/iad-map`, run as system user `iadmap`). Tests: `tests/`. Hand-kept Pi data: `pi/watch_flights.json`; generated: `pi/watch.json`, `pi/notable.json` (`tools/make_notable.py`), `beacons.js` (`tools/make_beacons.py`), `busy-seed.json` (`tools/make_busy.py`; the busy page's fallback), `farm-model.json` (`tools/make_farm_model.py`; the farm forecast's numbers until the Pi's are ready). Farm forecast state: `/mnt/flightdata/iad-map/farmcast/state.json`. Busy-times counts: `/mnt/flightdata/iad-map/busy/state.json`. Log: `/mnt/flightdata/iad-map/flights.db`. Type lookup: `/mnt/flightdata/iad-map/aircraft.db`. Events: `/mnt/flightdata/iad-map/events/*.jsonl` (emergencies, low_passes, close_approaches, tcas, farm_passes; never pruned). Deploy key: `/var/lib/iad-map/deploy_key` (added to **iad-map** as "Stables Pi", read/write).
- Branches written by the Pi (machine-written; do not edit): `live-data` (map data, one replaced commit), `live-data-prev` (the same files on top of the previous push, only so git sends just the changes), `alerts` (one commit per alert; the workflow on it opens the issues).
- Pi live JSON: `/run/readsb/aircraft.json`
