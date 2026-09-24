#!/usr/bin/env python3
"""Find close approaches between aircraft in the log. Run by publish.py.

Works through flights.db in 15-minute slices from where it last stopped
(state in events/proximity_state.json), at every range the receiver hears.
Two airborne aircraft (not on the ground, 50 kt or more) whose positions,
taken within 2 s of each other, are less than V_FT apart vertically and
closer horizontally than the distance they would close in TAU_S seconds at
their speed relative to each other -- kept between MIN_NM and MAX_NM -- are "close". So the
limit scales with speed, as the owner asked (2026-09-24): two jets head-on
(~500 kt closing) count within 1.0 nm, two light planes head-on (~200 kt)
within 0.55 nm, an overtake or a formation (little closing speed) only
within 0.15 nm. The floor allows for position error (MLAT especially); the
cap keeps fast traffic at normal separation out. Closing speed comes from
each aircraft's ground speed and track; if a track is missing, the two
speeds are added (the head-on worst case). Detections of the same pair less than
60 s apart form one event; each event is appended, once it has ended, to
events/close_approaches.jsonl with its closest point.

These are CLOSE APPROACHES, not confirmed near misses. ADS-B can't settle a
few hundred feet: MLAT positions can be off by that much and altitude comes
in 25-100 ft steps. The thresholds are first guesses, to tune once real
traffic has been seen. Each event carries flags instead of being dropped:
  near_airport  at some point both inside the same airport zone: 10 nm and
                below 4,000 ft for IAD, DCA, BWI and Andrews (parallel
                approaches), 5 nm and below 3,000 ft for the small fields
  low           both below 2,000 ft (pattern traffic at other fields)
  persistent    close for over 2 minutes (formation flights)
  mlat          either position came from MLAT (less accurate)
  tcas          either aircraft broadcast a TCAS resolution advisory in the
                event (if this readsb reports them) -- the strongest signal

Separately, every TCAS resolution advisory in the log is appended to
events/tcas.jsonl, once per aircraft per minute, whether or not a close
pair was found.
"""
import json, math, os, sqlite3, time

import aircraft_lookup

TAU_S = 10
MIN_NM = 0.15
MAX_NM = 1.0
V_FT = 500
MIN_GS = 50
PAIR_DT_S = 2
EVENT_GAP_S = 60
PERSISTENT_S = 120
SLICE_S = 900
COMMIT_LAG_S = 90   # the collector commits every 30 s; stay clear of it
AIRPORT_ZONES = [  # (lat, lon, radius_nm, below_ft)
    (38.9444, -77.4558, 10, 4000), (38.8521, -77.0377, 10, 4000), (39.1754, -76.6683, 10, 4000),
    (38.8108, -76.8674, 10, 4000),                                   # IAD, DCA, BWI, ADW
    (39.0779, -77.5578, 5, 3000), (39.1683, -77.1660, 5, 3000), (38.7214, -77.5153, 5, 3000),
    (38.5997, -77.4542, 5, 3000), (38.5036, -77.3050, 5, 3000),     # JYO, GAI, HEF, RMN, NYG
]


def dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def airport_zone(p):
    for i, (a, b, r, below) in enumerate(AIRPORT_ZONES):
        if p["alt"] < below and dist_nm(p["lat"], p["lon"], a, b) <= r:
            return i
    return None


def load(db, a, b):
    rows, seen = [], set()
    for t, hexid, lat, lon, alt, gs, track, flight, actype, mlat, ra in db.execute("""
            SELECT pos_t, hex, lat, lon, alt_baro, gs, track, flight, type, mlat, acas_ra
            FROM positions
            WHERE t BETWEEN ? AND ? AND pos_t >= ? AND pos_t < ?
              AND lat IS NOT NULL AND on_ground = 0 AND alt_baro IS NOT NULL
              AND gs >= ?""", (a - 5, b + 30, a, b, MIN_GS)):
        if (hexid, t) in seen:
            continue
        seen.add((hexid, t))
        rows.append({"t": t, "hex": hexid, "lat": lat, "lon": lon, "alt": alt, "gs": gs,
                     "track": track, "flight": flight or "",
                     "type": actype or aircraft_lookup.type_for(hexid) or "",
                     "mlat": mlat, "ra": ra})
    return rows


def closing_kt(p, q):
    """Their speed relative to each other: how fast they would meet if their
    paths crossed. Not the rate the distance is shrinking -- that is zero at
    the closest point of every pass, which is exactly where it's measured."""
    if p["track"] is None or q["track"] is None:
        return p["gs"] + q["gs"]
    def vel(x):
        t = math.radians(x["track"])
        return x["gs"] * math.sin(t), x["gs"] * math.cos(t)   # east, north (kt)
    (pe, pn), (qe, qn) = vel(p), vel(q)
    return math.hypot(qe - pe, qn - pn)


def limit_nm(closing):
    return min(MAX_NM, max(MIN_NM, max(closing, 0) * TAU_S / 3600))


def close_pairs(rows):
    """Yield (p, q, h_nm, v_ft, closing_kt, limit_nm) for close pairs taken
    within PAIR_DT_S."""
    cells = {}
    for p in rows:
        key = (int(p["t"] // PAIR_DT_S), int(p["lat"] / 0.05), int(p["lon"] / 0.05))
        cells.setdefault(key, []).append(p)
    for (tb, la, lo), ps in cells.items():
        for dt in (0, 1):
            for dla in (-1, 0, 1):
                for dlo in (-1, 0, 1):
                    other = cells.get((tb + dt, la + dla, lo + dlo))
                    if not other:
                        continue
                    for p in ps:
                        for q in other:
                            if q["hex"] <= p["hex"] if dt == 0 and (dla, dlo) == (0, 0) else q["hex"] == p["hex"]:
                                continue
                            if abs(p["t"] - q["t"]) > PAIR_DT_S or abs(p["alt"] - q["alt"]) >= V_FT:
                                continue
                            h = dist_nm(p["lat"], p["lon"], q["lat"], q["lon"])
                            if h >= MAX_NM:
                                continue
                            c = closing_kt(p, q)
                            lim = limit_nm(c)
                            if h < lim:
                                yield p, q, h, abs(p["alt"] - q["alt"]), c, lim


def side(p):
    return {k: p[k] for k in ("hex", "flight", "type", "lat", "lon", "alt", "gs", "track", "mlat")}


def finish(ep):
    flags = []
    if ep["near_airport"]:
        flags.append("near_airport")
    if ep["low"]:
        flags.append("low")
    if ep["end"] - ep["start"] > PERSISTENT_S:
        flags.append("persistent")
    if ep["mlat"]:
        flags.append("mlat")
    if ep["tcas"]:
        flags.append("tcas")
    return {"start": int(ep["start"]), "end": int(ep["end"]), "cpa_t": int(ep["cpa_t"]),
            "h_nm": round(ep["h_nm"], 2), "v_ft": int(ep["v_ft"]),
            "closing_kt": round(ep.get("closing_kt", 0)), "limit_nm": round(ep.get("limit_nm", MAX_NM), 2),
            "a": ep["a"], "b": ep["b"], "flags": flags}


def update(db_path, events_dir, now=None):
    """Process new slices. Returns the number of close approaches logged."""
    now = time.time() if now is None else now
    os.makedirs(events_dir, exist_ok=True)
    state_path = os.path.join(events_dir, "proximity_state.json")
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {"done_until": int(now - SLICE_S), "open": {}, "last_ra": {}}
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60)
    logged = 0
    stop = int(now - COMMIT_LAG_S)
    while state["done_until"] < stop:
        a = state["done_until"]
        b = min(a + SLICE_S, stop)
        rows = load(db, a, b)
        with open(os.path.join(events_dir, "tcas.jsonl"), "a") as tf:
            for p in rows:
                if p["ra"] and p["t"] - state["last_ra"].get(p["hex"], 0) > 60:
                    state["last_ra"][p["hex"]] = p["t"]
                    tf.write(json.dumps({"t": int(p["t"]), **side(p), "acas_ra": json.loads(p["ra"])},
                                        separators=(",", ":")) + "\n")
        for p, q, h, v, c, lim in close_pairs(rows):
            key = "|".join(sorted((p["hex"], q["hex"])))
            t = min(p["t"], q["t"])
            ep = state["open"].get(key)
            if ep is None:
                ep = state["open"][key] = {"start": t, "end": t, "cpa_t": t, "h_nm": h, "v_ft": v,
                                           "closing_kt": c, "limit_nm": lim,
                                           "a": side(p), "b": side(q), "near_airport": False,
                                           "low": True, "mlat": False, "tcas": False}
            ep["start"], ep["end"] = min(ep["start"], t), max(ep["end"], t)
            if h < ep["h_nm"]:
                ep.update(cpa_t=t, h_nm=h, v_ft=v, closing_kt=c, limit_nm=lim, a=side(p), b=side(q))
            zone = airport_zone(p)
            ep["near_airport"] = ep["near_airport"] or (zone is not None and zone == airport_zone(q))
            ep["low"] = ep["low"] and p["alt"] < 2000 and q["alt"] < 2000
            ep["mlat"] = ep["mlat"] or bool(p["mlat"] or q["mlat"])
            ep["tcas"] = ep["tcas"] or bool(p["ra"] or q["ra"])
        with open(os.path.join(events_dir, "close_approaches.jsonl"), "a") as cf:
            for key, ep in list(state["open"].items()):
                if ep["end"] < b - EVENT_GAP_S:
                    cf.write(json.dumps(finish(ep), separators=(",", ":")) + "\n")
                    del state["open"][key]
                    logged += 1
        state["last_ra"] = {h: t for h, t in state["last_ra"].items() if t > b - 600}
        state["done_until"] = b
        with open(state_path + ".tmp", "w") as f:
            json.dump(state, f)
        os.replace(state_path + ".tmp", state_path)
    db.close()
    return logged


def recent(events_dir, name, since):
    """Events from events/<name>.jsonl at or after `since` (epoch s)."""
    out = []
    try:
        with open(os.path.join(events_dir, name)) as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("t", ev.get("cpa_t", 0)) >= since:
                    out.append(ev)
    except OSError:
        pass
    return out
