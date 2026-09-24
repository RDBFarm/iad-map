# CLAUDE.md — Dulles-area Flight Tracker (rdbfarm/iad-map)

Context for Claude Code. Compiled from earlier Claude.ai conversations
(July–Sept 2026) and brought up to date in Claude Code on 2026-09-23/24;
changes from that session are marked **(09-23)** or **(09-24)**. Where
something is marked **OPEN**, it was never settled — ask Guillaume rather
than assuming.

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

**Status: receiving (Sept 23, 2026). The logger and publisher in `pi/` are
built and merged but NOT YET INSTALLED** — installing needs him on the farm
network. First result from the receiver: 291 aircraft (271 with position),
~1,683 messages/sec.

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

### Receiver location
- Get exact antenna coordinates from the ADSBx config on the Pi rather than guessing.
- **Farm to KIAD is ~14.3 statute mi (12.4 nmi)**, measured from the parcel centre (09-23) — consistent with the A340 seen at 15.4 nmi on short final. The earlier "~30 miles" was wrong.
- `FARM_LAT/LON = 39.1133, -77.4447` in the old `render_github.py` is **not the farm**: 2.7 mi from the parcel and outside it (09-24). Don't use it.
- **Range claim doesn't add up (09-23):** notes said past the 150 nmi ring (~173 statute mi), "beyond Pittsburgh, Charleston WV, Virginia Beach". From the parcel: Pittsburgh 161 mi (inside the ring), Virginia Beach 178 mi, Charleston WV 232 mi (201 nmi). Check against readsb's range outline (`/run/readsb/outline.json`) before quoting.

### Data gotchas seen in live data
- **Negative altitudes are real** (pressure altitude): e.g. UAL1873 at −125 ft. Any ground threshold must tolerate this.
- **Mode S–only aircraft** (altitude, no position): ~20 of 291 in the first snapshot.
- **readsb on this image does not fill the aircraft type (`t`)** — 0 of 258 aircraft on 2026-09-24. `pi/aircraft_lookup.py` looks types up by ICAO address in tar1090-db (`aircraft.csv.gz`, csv branch) kept as SQLite at `/mnt/flightdata/iad-map/aircraft.db`, refreshed monthly by `iad-map-aircraft-db.timer`. Checked against May 1, 2025: found 1,817 of the 1,825 aircraft ADSBx had typed, same type for 97%. Addresses starting `~` (TIS-B, radar-derived) have no airframe and stay unknown — so they count as "not a prop" and can raise low-pass alerts.

---

## 4. Live map pipeline (built 09-23/24, not yet installed)

See `README.md` for how to install. In short:

- **`pi/collector.py`** — logs to SQLite at **`/mnt/flightdata/iad-map/flights.db`**. One row per aircraft per read, for every aircraft with a new message: all ranges and altitudes, with or without position. Buffers and commits every 30 s. **Refuses to start if `/mnt/flightdata` isn't mounted.** Deletes nothing.
- **`pi/publish.py`** — every 15 min (systemd timer): last 24 h, within 50 statute mi of KIAD, ≤15,000 ft → one JSON file per UTC hour plus `live.json` on the **`live-data`** branch, as a single commit amended and force-pushed. Finished hours are byte-identical run to run, so each push sends only the current hour. Pushes with a **deploy key** scoped to this repo only.
- **Classification** — `classify_airport()`, `is_arrival()` and the climb-sequence departure rule copied from `render_github.py`. The departure rule runs on each aircraft's points thinned to ≥10 s (the spacing it was written for); the map itself is never thinned. Fixed 09-24: GA flag requires an N-number, not any N-callsign (Spirit, NKS, was caught); two-letter airline hints removed.
- **`live.html`** — fetches the hour files from raw.githubusercontent.com (Pages is not rebuilt), refetches only changed hours every 5 min. Points held in typed-array columns. Dots **viridis by altitude**, brightest at the ground; neutral labelled airport markers.

### Measured, on the May 1 day replayed at 2 s density (~815k positions)
- First push ~12 MB; later pushes ~0.5 MB (~30 MB/day of farm upload).
- `publish.py` peak memory ~490 MB (of the Pi's 2 GB); build ~12–18 s on the cloud sandbox — the Pi will be slower.
- `live.html` JS heap ~50 MB (was ~220 MB before typed arrays). Not yet tried on an iPad.
- SQLite ~78 bytes per row in the test database.

### Decided 09-24
- **Sampling interval: 2 s** (`IADMAP_INTERVAL_S`).
- **Retention: keep the log until the drive is nearly full.** Built as: below 10 GB free, the collector deletes the oldest day at a time until 15 GB is free (`IADMAP_PRUNE_BELOW_GB` / `_TO_GB`); the database uses incremental auto-vacuum so the space really returns. Event logs in `events/` are never pruned. Growth estimate (not measured): ~1 GB/day, so ~7 months before pruning starts — measure after a day of running.
- **Helicopters count** for the over-the-farm figure and alerts, for now.
- **The office is not to go on the map** — the property line is already there.

### Still OPEN
- **Runway headings** in the copied classifier are runway numbers (magnetic); ADS-B track is true. KIAD listed as 19/199. Check with real traffic.

### After installing, check
- Drive growth per day, publisher memory on the Pi, whether aircraft types come through, whether the aviationweather.gov weather feed parses (untested — sandbox couldn't reach it), and `live.html` on the iPad.

### Emergencies and close approaches (built 09-24, not yet installed)
- **Emergencies (7500/7600/7700) push to his phone** — his choice (09-24): text or GitHub; just him; everything the receiver hears. Built as `pi/alerts.py` → alert file on the `alerts` branch (deploy key) → `.github/workflows/emergency-alert.yml` opens an issue as github-actions[bot] mentioning @RDBFarm. Not with his own token: GitHub doesn't notify you about your own actions. Needs 3 consecutive reads (~6 s). Medical/min-fuel statuses logged only. Test with `alerts.py --test` after install; he needs the GitHub app with notifications on.
- **Near misses: a log plus map highlights, no push** (his choice, 09-24). `pi/proximity.py`: under 1 nm and 500 ft, both airborne; flags near_airport / low / persistent / mlat / tcas. Thresholds are first guesses — tune after real traffic. Call them close approaches, never near misses, unless a TCAS advisory confirms.
- Whether this readsb reports TCAS advisories (`acas_ra`) is unconfirmed.
- At phone width the live map's panels overlap (inherited layout, worse with the Events list). Not fixed.

### Over the farm (built 09-24, not yet installed)
- His request (09-24): an over-the-farm figure on the live map, highlight passes **under 2,000 ft**, push to phone **under 1,500 ft**, and **exclude prop planes**.
- `pi/farm.py`: within **1 nm** of the parcel centre (39.1506, -77.4612), any altitude, airborne; one log line per pass with its closest point. Altitudes are reported pressure altitude, not height above the farm.
- "Prop plane" = my reading, not confirmed by him: fixed-wing (L/S/A/G) with piston, turboprop or electric engines per ICAO Doc 8643 descriptors (`pi/aircraft_types.json`, from tar1090-db). Helicopters and jets stay in; **unknown types stay in** (an extra alert beats a missed one). Types come from `aircraft_lookup.py`, since readsb supplies none.
- From the May 1, 2025 cache (≤15,000 ft only): 228 aircraft within 1 nm of the parcel that day, median 3,800 ft, lowest 1,150 ft; 112 at 3,000–3,999 ft, 103 of them southbound and descending/level, noon–7 PM with a southerly wind — consistent with the approach to Dulles's southbound runways (an inference). One day only; north-flow days will differ.

### Planned, not built
- **Weather radar overlay** — historical mode via IEM WMS-T NEXRAD (round the slider's UTC time to 5 min); live mode via RainViewer current tiles. Both as transparent overlays behind the dots.
- **Layering live tracks on the heat map** was the original idea; the live map is a separate page instead, and the historical map is frozen.

---

## 5. Security notes (09-24)

- The old `deploy_to_github.py` (Mac only, never committed) contains a classic GitHub token for RDBFarm, expiring 2026-09-24 04:28 UTC. It did not appear in his token list. Don't copy it anywhere; the deploy script and `Deploy_Flight_Map.command` can be deleted from the Mac.
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
- Pi code: `pi/` (installed to `/opt/iad-map`). Log: `/mnt/flightdata/iad-map/flights.db`. Deploy key: `/var/lib/iad-map/deploy_key`.
- Live data: `live-data` branch (machine-written; do not edit).
- Pi live JSON: `/run/readsb/aircraft.json`
