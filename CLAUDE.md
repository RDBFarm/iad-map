# CLAUDE.md — Dulles-area Flight Tracker (rdbfarm/iad-map)

Context for Claude Code. Compiled from earlier Claude.ai conversations
(July–Sept 2026) and brought up to date in Claude Code on 2026-09-23/24;
changes from that session are marked **(09-23)** or **(09-24)**. Where
something is marked **OPEN**, it was never settled — ask Guillaume rather
than assuming.

---

## 0. Start here — status as of 2026-09-24

**Everything below is installed and running on the Pi** (installed 09-24,
~10:30 AM ET). Live map: **https://rdbfarm.github.io/iad-map/live.html**.

- **Running on the Pi** (systemd): `iad-map-collector` (logs every 2 s),
  `iad-map-alerts` (emergencies + low passes → phone), `iad-map-publish.timer`
  (every 15 min → `live-data` branch), `iad-map-aircraft-db.timer` (monthly).
- **Verified live on 09-24:** first upload 10:30 AM; weather parses (29 KIAD
  reports); aircraft types come through via the lookup (209/209); the test
  alert reached his phone (issues #10–12, closed); full-path files arrive
  (6 hours: 2,296 aircraft, 2.7 MB compressed, ~0.5 MB per hour).
- **Not yet checked on the real Pi:** drive growth per day (estimate ~1 GB),
  `publish.py` memory on the 2 GB Pi (measured ~490 MB in the sandbox), the
  live map on his iPad.
- **Decided 09-25: "over the farm" = over the land, heights above the ground.** A pass counts only when the aircraft's path (consecutive positions, or one position) crosses the parcel polygon (`farm.PARCEL`, Maryland iMAP, 213.4 acres; the office is inside) — replacing the 1 nm radius. Heights shown and used are **above the farm's ground**: reported pressure altitude + (KIAD altimeter − 29.92 inHg) × 1000, minus ground elevation at the nearest of a grid of USGS EPQS points measured once by the installer (`/mnt/flightdata/iad-map/farm_ground.json`; falls back to the notes' ~380 ft). Limits converted to keep his intent (~400 ft of ground): jets alert < 1,100 ft AGL, helicopters < 150 ft AGL, map highlights < 1,600 ft AGL. **Check after install:** that `farm_ground.json` exists (USGS reachable from the Pi) and that `wx[].altim_hpa` in live.json is filled (the METAR `altim` field name is unverified from here).
- **Decided 09-25: emergencies push only on the squawk itself** (7500/7600/7700). The first 7500 alert (issue #25, 8:39 AM 09-25) was RPA5604, an E175 (N241JQ) at 27,000 ft ~97 nm SW, whose ADS-B emergency *status* read "unlawful" while its squawk was an ordinary 1631 — a glitch, and the old code titled it "7500". Status-only events are now logged and shown on the map as "status …", not pushed; titles show the real squawk. Alerts and cards now name the airline from `pi/operators.json` (tar1090-db): e.g. RPA = Republic Airlines (BRICKYARD), AVL = Aviation Adventures (the 09-24 close-approach Cessnas: a flight school), VXP = Avelo (AVELO).
- **Decided 09-25: helicopters push only below 550 ft reported** (≈150 ft above the fields). The first real low-pass alert, PAT26 (an H60 Black Hawk) at 700 ft reported, ≈300 ft above the fields, 5:47 PM 09-24 (issue #21), was heard and "not concerning at all". Jets still push below 1,500 ft; helicopters between 550 and 1,500 ft are logged and shown, not pushed.
- **Open:** close-approach thresholds (tune after a week of real traffic),
  runway headings in the classifier (magnetic vs true), live map panels
  overlapping at phone width.

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
replaces the programs, and restarts running services. It prints the deploy
key again at the end; ignore it unless the key changed. Changes to
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

- **`pi/collector.py`** — logs to SQLite at **`/mnt/flightdata/iad-map/flights.db`**. One row per aircraft per read, for every aircraft with a new message: all ranges and altitudes, with or without position. Buffers and commits every 30 s. **Refuses to start if `/mnt/flightdata` isn't mounted.** Deletes nothing.
- **`pi/publish.py`** — every 15 min (systemd timer): last 24 h, within 50 statute mi of KIAD, ≤15,000 ft → one JSON file per UTC hour plus `live.json` on the **`live-data`** branch, as a single commit replaced and force-pushed, plus `live-data-prev` (same files, with the previous push as parent). Each push sends only the current hour and the index.
  - **Fixed 09-26: every push was sending the whole tree (~18 MiB, ~1.8–2.1 GiB/day measured with vnstat on the Pi).** Two causes. (1) Git skips files the remote has only when the remote's commit is a *parent* of the pushed one; an amended single commit has none, so git sent all ~57 MiB of files every run (reproduced locally: one changed file, 19.6 MiB sent). `live-data-prev` supplies the parent link. (2) Finished hour files were rebuilt each run and some changed: e.g. an aircraft last heard low that reappears hours later climbing makes the departure rule drop points from the old hour (shown in a test). Now an hour file is built once, `SETTLE_S` (10 min) after the hour ends, and read back from disk after that; the list of final files is kept in `publish/.git/iad-map-final-hours`. Same for `t/`. On a simulated day the pushes went from ~2,300 KiB each to 17–130 KiB. Pushes with a **deploy key** scoped to this repo only.
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
- Done 09-24: aircraft types come through (via the lookup); the aviationweather.gov weather feed parses.
- Done 09-26: installer re-run for the parcel / height-above-ground change. USGS reachable from the Pi: `farm ground: 25 points, 287-355 ft, mean 334 ft` (the fallback had been 380 ft). Lookup rebuilt: 618,617 aircraft, 490,518 with a type. First publish after it (11:45 AM): `farm.ground_ft` 340 (USGS), all 24 weather reports carry `altim_hpa` (so the METAR `altim` field name was right; 1012.6 hPa = 29.90 inHg), first parcel pass QTR3G at ~9,460 ft above the ground. Older passes under the 1 nm rule age out of the 24 h window.
- Still to check: drive growth per day, publisher memory on the Pi, `live.html` on the iPad (see §0).

### Emergencies and close approaches (built and installed 09-24)
- **Emergencies (7500/7600/7700) push to his phone** — his choice (09-24): text or GitHub; just him; everything the receiver hears. Built as `pi/alerts.py` → alert file on the `alerts` branch (deploy key) → `.github/workflows/emergency-alert.yml` opens an issue as github-actions[bot] mentioning @RDBFarm. Not with his own token: GitHub doesn't notify you about your own actions. Needs 3 consecutive reads (~6 s). Medical/min-fuel statuses logged only. Test with `alerts.py --test` after install; he needs the GitHub app with notifications on.
- **Near misses: a log plus map highlights, no push** (his choice, 09-24). `pi/proximity.py`: under 500 ft vertically and closer than **10 s at their relative speed**, kept between 0.15 and 1.0 nm (his request, 09-24: "a slow near miss has a much smaller radius than a fast one"); both airborne; flags near_airport / low / persistent / mlat / tcas. Thresholds are first guesses — tune after real traffic. The first live event (09-24, two AVL-callsign light aircraft passing 0.85 nm apart at ~200 kt relative, 21 nm WNW of the farm) prompted the speed scaling; under it that pass is not logged. Relative speed, not range rate: range rate is zero at every closest point. Call them close approaches, never near misses, unless a TCAS advisory confirms.
- **This readsb does report TCAS advisories (`acas_ra`)** — confirmed 09-24: 6 logged that day from 4 aircraft, all well above 15,000 ft (the first real one: VXP1191, a 737-700, "Level Off" at 30,400 ft descending towards UAL2748 level at 29,000 ft, 3:55:07 PM ET, ~38 nm SSW of the farm; "Clear of Conflict" 61 s later; closest ~4 nm and ~1,800 ft). Each RA is broadcast twice: the advisory and then "Clear of Conflict". **Not shown on the map since 09-25** (his call: "It was never a concern if it happened 4nm from each other"); still logged in `events/tcas.jsonl`, and a close approach flagged `tcas` is still shown.
- At phone width the live map's panels overlap (inherited layout, worse with the Events list). Not fixed.

### Over the farm (built and installed 09-24)
- His request (09-24): an over-the-farm figure on the live map, highlight passes **under 2,000 ft**, push to phone **under 1,500 ft**, and **exclude prop planes**.
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
- **Categories on the right** (his list: private, commercial, helicopters, military, military fighters, plus "experimental too"). One per aircraft, first match wins: fighter (type list, general knowledge, T-38s included) → military (US military address block ADF7C8–AFFFFF; 99% of tar1090-db entries there are flagged military; foreign military not recognised) → helicopter (descriptor H) → experimental (kit builders matched by type name; misses kit planes whose type isn't reported) → commercial (airline-style callsign on anything but a piston plane, so flight schools flying Cessnas under a company callsign count as private) → private. A military helicopter counts as military. Airport toggles moved into a folded "Airports" section. On a phone the panel folds to a "✈ Show" button. 09-26 counts: private 586, commercial 864, helicopters 24, military 38, fighters 0, experimental 9.
- **Tapping a dot on a route** opens the same detail card as an event (replaces the old small popup), with its whole 24 h path drawn. Registration is worked out from a US address (N-number rule; 392,062 of 392,213 agree with tar1090-db, the rest look like re-registrations); the airline name comes from `pi/operators.json`, fetched on first tap.

### Public page and icon (09-26, his requests)
- **`flights.html` is the live map without the farm** (his words: "a duplicate website but without the farm relevant data"). Built from `live.html` by `tools/make_public.py`: sets `FARM_MODE = false`, empties `PARCELS` (it carried the address), and drops the HTML between `<!--farm-->` markers: over-the-farm summary, 📍 Near the farm, "RDBF receiver". With `FARM_MODE` off there are no low passes, no "From the farm" or height-above-your-ground rows. **Edit `live.html` only, then run `python3 tools/make_public.py`**; the `Public page up to date` workflow fails otherwise. Anything new about the farm in `live.html` needs a marker or a `FARM_MODE` guard.
- It is not private: the farm page, `live.json` (farm centre, passes) and this repo are all public, the address is rdbfarm.github.io, and the coverage pattern centres on the receiver. It only leaves the farm off the page people are sent.
- Tab / home-screen icon: `icon.svg`, `favicon-32.png`, `apple-touch-icon.png` (180 px, square; iOS rounds it). The PNGs were rendered from the SVG in Chromium.

### Planned, not built
- **Weather radar overlay** — historical mode via IEM WMS-T NEXRAD (round the slider's UTC time to 5 min); live mode via RainViewer current tiles. Both as transparent overlays behind the dots.
- **Layering live tracks on the heat map** was the original idea; the live map is a separate page instead, and the historical map is frozen.

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
- Pi code: `pi/` (installed to `/opt/iad-map`, run as system user `iadmap`). Log: `/mnt/flightdata/iad-map/flights.db`. Type lookup: `/mnt/flightdata/iad-map/aircraft.db`. Events: `/mnt/flightdata/iad-map/events/*.jsonl` (emergencies, low_passes, close_approaches, tcas, farm_passes; never pruned). Deploy key: `/var/lib/iad-map/deploy_key` (added to **iad-map** as "Stables Pi", read/write).
- Branches written by the Pi (machine-written; do not edit): `live-data` (map data, one replaced commit), `live-data-prev` (the same files on top of the previous push, only so git sends just the changes), `alerts` (one commit per alert; the workflow on it opens the issues).
- Pi live JSON: `/run/readsb/aircraft.json`
