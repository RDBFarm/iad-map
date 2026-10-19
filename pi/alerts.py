#!/usr/bin/env python3
"""Watch for emergency squawks and push them to the owner's phone.

Every 2 seconds, reads readsb's /run/readsb/aircraft.json -- every aircraft
the receiver hears, at any range -- and looks for:

  squawk 7700 (general emergency), 7600 (radio failure) or 7500 (unlawful
  interference). Only the squawk itself is pushed. An ADS-B emergency status
  (general, nordo, unlawful) with an ordinary squawk is logged, not pushed:
  the first one seen (2026-09-25, RPA5604, status "unlawful" with squawk
  1631) was a glitch, and a real one comes with the code.

An aircraft has to show it on 3 reads in a row (~6 s) before it counts, so a
single garbled message doesn't raise an alarm. Then, once per episode:

  - the event is appended to /mnt/flightdata/iad-map/events/emergencies.jsonl
  - an alert file is committed to the `alerts` branch and pushed with the
    repo's deploy key; the GitHub Actions workflow on that branch opens an
    issue mentioning @RDBFarm, which GitHub Mobile turns into a phone push.
    (An issue opened with the owner's own token would not notify him:
    GitHub doesn't notify you about your own actions.)

Also: any aircraft whose path between two consecutive positions crosses
the farm's land (the parcel, farm.py) below farm.LOW_ALERT_AGL_FT above the
farm's ground (pressure altitude corrected with KIAD's altimeter setting,
minus the ground elevation) is logged to events/low_passes.jsonl, once per
aircraft per 10 minutes -- unless it is a propeller aeroplane. It is pushed
only when its type is known and is not a prop plane; an unknown type is
logged, not pushed. Helicopters push only below farm.HELI_ALERT_AGL_FT
(150 ft above the ground): a Black Hawk ~300 ft above the fields was heard
and "not concerning at all". Owner's decisions, 2026-09-24/25; "over the
land" and height above ground replaced a 1 nm radius and reported altitude
on 09-25.

Also (owner, 2026-09-26: "an alert if it is within sight of the farm ... I
could see it with my own eyes"): Air Force One and the doomsday planes
(watch.json, from plane-alert-db), and the flights named in
watch_flights.json on the days given there (owner, 2026-10-18: UA 2853 on
Nov 21), push once per visit when they are worth stepping outside for:

  "In sight" is a straight-line distance from the farm, height included,
  within SIGHT_MI -- or within KIAD's reported visibility when that is less
  than 10 miles -- and at least SIGHT_MIN_ELEV degrees above the horizon
  (lower is likely behind trees). SIGHT_MI and SIGHT_MIN_ELEV are estimates,
  not measured figures; cloud between the farm and the aircraft isn't known.
  "Worth it" (owner, 2026-10-18: "look at the direction of the plane in
  terms of if it is even worth while"): its path is projected straight ahead
  at its present track, speed and climb or descent; it pushes only when that
  path is in sight within SIGHT_LEAD_S and stays in sight at least SIGHT_MIN_S.
  So a plane heading away at the edge of the range does not push, and one
  heading this way pushes a few minutes early. Until then every one of the
  first seven sightings had pushed at exactly 12 miles, the moment it
  crossed the line, with a direction that was only where it first appeared.
  The alert says which way to look and how high at its closest, and when.
  Logged to events/sightings.jsonl; one in range that isn't worth it is
  logged too (pushed false, with the reason), once per visit.

Every pushed alert is also sent as a text message when text.json is set up
(owner, 2026-09-26): an email to his carrier's email-to-text address, sent
through an email account used only for this. text.json lives in
/var/lib/iad-map beside the deploy key, readable by the iadmap account only,
and is written by --setup-text, never by hand and never in the repository.
GitHub stays the written record. A text that fails is retried every minute.

Other ADS-B emergency statuses (lifeguard, minfuel, downed, reserved) are
logged but not pushed; the owner asked for the three squawks only. An episode
ends after 10 minutes without the code, and a later one alerts again.
Pushes that fail (no internet) are retried every read until they succeed.

  python3 alerts.py              run
  python3 alerts.py --test       push one alert marked TEST (and text it), then exit
  python3 alerts.py --setup-text ask for the text-message settings, save them, send a test text
"""
import datetime, getpass, json, math, os, smtplib, ssl, subprocess, sys, time
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import aircraft_lookup
import farm

AIRCRAFT_JSON = os.environ.get("IADMAP_AIRCRAFT_JSON", "/run/readsb/aircraft.json")
RECEIVER_JSON = os.environ.get("IADMAP_RECEIVER_JSON", "/run/readsb/receiver.json")
DRIVE = os.environ.get("IADMAP_DRIVE", "/mnt/flightdata")
EVENTS_DIR = os.environ.get("IADMAP_EVENTS_DIR", os.path.join(DRIVE, "iad-map", "events"))
REPO_DIR = os.environ.get("IADMAP_ALERTS_REPO", os.path.join(DRIVE, "iad-map", "alerts-repo"))
DEPLOY_KEY = os.environ.get("IADMAP_DEPLOY_KEY", "/var/lib/iad-map/deploy_key")
REMOTE = os.environ.get("IADMAP_REMOTE", "git@github.com:RDBFarm/iad-map.git")
BRANCH = "alerts"
INTERVAL_S = 2
CONFIRM_READS = 3
EPISODE_GAP_S = 600

SQUAWKS = {"7700": "general emergency", "7600": "radio failure", "7500": "unlawful interference"}
STATUS_WORDS = {"general": "general emergency", "nordo": "radio failure",
                "unlawful": "unlawful interference", "lifeguard": "medical priority",
                "minfuel": "minimum fuel", "downed": "downed aircraft", "reserved": "reserved"}
WORK_DIR = os.environ.get("IADMAP_PUBLISH_DIR", os.path.join(DRIVE, "iad-map", "publish"))
LOW_PASS_REPEAT_S = 600
SIGHT_MI = 12            # statute miles, straight line; an estimate, see above
SIGHT_MIN_ELEV = 5       # degrees above the horizon; lower is likely behind trees (an estimate)
SIGHT_AHEAD_S = 600      # its path is projected this far ahead
SIGHT_LEAD_S = 300       # push when it will be in sight within this long...
SIGHT_MIN_S = 60         # ...and stay in sight at least this long
SIGHT_STEP_S = 5
SIGHT_CONFIRM_READS = 2  # worth it on this many reads in a row before it counts
SIGHT_GAP_S = 1800       # out of range this long ends a visit; the next one alerts again
LOCAL = ZoneInfo("America/New_York")
TEXT_CONF = os.path.join(os.path.dirname(DEPLOY_KEY), "text.json")
TEXT_MAX = 160           # one SMS
WATCH_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch.json")
WATCH_FLIGHTS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch_flights.json")
COMPASS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def git(*args, check=True):
    env = dict(os.environ, GIT_SSH_COMMAND=(
        f"ssh -i {DEPLOY_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={os.path.dirname(DEPLOY_KEY)}/known_hosts"))
    return subprocess.run(["git", *args], cwd=REPO_DIR, env=env, check=check,
                          timeout=120, capture_output=True, text=True)


def ensure_repo():
    """A checkout of the alerts branch; the first time, the branch starts
    from main so it carries the workflow that opens the issues."""
    if os.path.isdir(os.path.join(REPO_DIR, ".git")):
        return
    os.makedirs(REPO_DIR, exist_ok=True)
    git("init", "-q")
    git("remote", "add", "origin", REMOTE)
    if git("fetch", "-q", "--depth=1", "origin", BRANCH, check=False).returncode == 0:
        git("checkout", "-q", "-b", BRANCH, "FETCH_HEAD")
    else:
        git("fetch", "-q", "--depth=1", "origin", "main")
        git("checkout", "-q", "-b", BRANCH, "FETCH_HEAD")


def receiver_position():
    try:
        with open(RECEIVER_JSON) as f:
            r = json.load(f)
        return r.get("lat"), r.get("lon")
    except (OSError, ValueError):
        return None, None


def dist_bearing(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    nm = 3440.065 * 2 * math.asin(math.sqrt(a))
    brg = math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                  math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)))
    return nm, (brg + 360) % 360


def compose_low_pass(ev):
    who, op, idlines = who_lines(ev)
    kind = ""
    title = (f"✈ Over the farm: {who}" + (f" ({op[0]})" if op else "")
             + (f" {ev['type']}" if ev.get("type") else "") + f" at {ev['agl']:,} ft above the ground")
    body = "\n".join([
        f"@RDBFarm — {who}{kind} crossed the farm's land at {ev['utc']} UTC.",
        "",
        f"- **Height above the farm's ground:** about {ev['agl']:,} ft "
        f"(reported {ev['alt']:,} ft; ground {ev['ground_ft']} ft, {ev['ground_basis']}; "
        + (f"pressure-corrected with KIAD's altimeter {ev['altim_hpa'] * 0.02953:.2f} inHg)" if ev.get("altim_hpa")
           else "no altimeter correction available)"),
        f"- **Speed:** {ev['gs']:.0f} kt; **track** {ev['track']:.0f}°" if ev.get("gs") is not None and ev.get("track") is not None else "- **Speed/track:** not reported",
    ] + idlines + [
        "",
        f"[ADS-B Exchange](https://globe.adsbexchange.com/?icao={ev['hex']}) · "
        f"[Farm receiver (farm network only)](http://192.168.1.190/tar1090/?icao={ev['hex']}) · "
        "[Live map](https://rdbfarm.github.io/iad-map/live.html)",
        "",
        f"_Automatic alert from `pi/alerts.py`: path crossed the farm's land below "
        f"{farm.LOW_ALERT_AGL_FT:,} ft above the ground ({farm.HELI_ALERT_AGL_FT} ft for helicopters). "
        "Prop planes and unknown types are logged, not alerted. Heights are +/- about 100 ft._"])
    return title, body


def who_lines(ev):
    """Callsign, airline, registration and type, for the alert body."""
    who = ev.get("flight") or ev["hex"].upper()
    op = aircraft_lookup.operator_for(ev.get("flight"))
    reg = aircraft_lookup.reg_for(ev.get("hex"))
    name = farm.type_name(ev.get("type"))
    lines = [f"- **Flight:** {who}" + (f" — {op[0]}" + (f" (radio callsign {op[1]})" if op[1] else "") if op else "")]
    lines.append(f"- **Aircraft:** " + ", ".join(x for x in [
        reg and f"registration {reg}", ev.get("type") and f"type {ev['type']}" + (f" ({name})" if name else ""),
        f"ICAO {ev['hex'].upper()}"] if x))
    return who, op, lines


def compose(ev, test=False):
    if ev.get("kind") == "low_pass":
        return compose_low_pass(ev)
    if ev.get("kind") == "sighting":
        return compose_sighting(ev)
    code = ev["code"]
    what = SQUAWKS.get(code) or STATUS_WORDS.get(ev.get("emergency"), ev.get("emergency") or "")
    who, op, idlines = who_lines(ev)
    airline = f" ({op[0]})" if op else ""
    title = (f"{'TEST — ' if test else ''}🚨 {code} {who}{airline}"
             + (f" {ev['type']}" if ev.get("type") else "") + f" — {what}")
    lines = [
        f"@RDBFarm — heard by the farm receiver at {ev['utc']} UTC.",
        "",
        f"- **Squawk:** {ev.get('squawk') or 'not reported'}" + (f" ({what})" if ev.get("squawk") in SQUAWKS else ""),
    ] + ([f"- **ADS-B emergency status:** {STATUS_WORDS.get(ev['emergency'], ev['emergency'])}"] if ev.get("emergency") else []) + idlines
    if ev.get("lat") is not None:
        pos = f"- **Position:** {ev['lat']:.4f}, {ev['lon']:.4f}"
        if ev.get("dist_nm") is not None:
            pos += f" — {ev['dist_nm']:.0f} nm from the farm, bearing {ev['bearing']:.0f}°"
        lines.append(pos)
    else:
        lines.append("- **Position:** not reported")
    alt = ev.get("alt")
    alt = f"{alt:,} ft" if isinstance(alt, (int, float)) else (alt or "not reported")
    lines.append(f"- **Altitude:** {alt}"
                 + (f"; **speed** {ev['gs']:.0f} kt" if ev.get("gs") is not None else "")
                 + (f"; **track** {ev['track']:.0f}°" if ev.get("track") is not None else ""))
    lines += ["",
              f"[ADS-B Exchange](https://globe.adsbexchange.com/?icao={ev['hex']}) · "
              f"[Farm receiver (farm network only)](http://192.168.1.190/tar1090/?icao={ev['hex']}) · "
              "[Live map](https://rdbfarm.github.io/iad-map/live.html)",
              "",
              "_Automatic alert from `pi/alerts.py`. A squawk can be set by mistake and corrected "
              "within seconds; this confirms only that the code was seen on 3 reads in a row._"]
    return title, "\n".join(lines)


_TEXT_PENDING = []
_TEXT_RETRY = {"after": 0}
TEXT_RETRY_S = 60


def text_line(title):
    """An alert title as one plain-text SMS: no emoji (they garble or split
    a text), at most TEXT_MAX characters."""
    for a, b in (("°", " deg"), ("—", "-"), ("–", "-"), ("🚨 ", ""), ("👀 ", ""), ("✈ ", ""), ("’", "'")):
        title = title.replace(a, b)
    title = title.encode("ascii", "ignore").decode().strip()
    return title if len(title) <= TEXT_MAX else title[:TEXT_MAX - 3].rstrip() + "..."


def load_text_conf():
    try:
        with open(TEXT_CONF) as f:
            c = json.load(f)
        return c if c.get("to") and c.get("user") and c.get("password") else None
    except (OSError, ValueError):
        return None


def send_text(conf, line):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = conf["user"], ", ".join(conf["to"]), ""
    msg.set_content(line)
    with smtplib.SMTP(conf.get("host", "smtp.gmail.com"), int(conf.get("port", 587)), timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(conf["user"], conf["password"])
        smtp.send_message(msg)


def send_texts_pending():
    """Send queued texts. Returns True when nothing is left."""
    conf = load_text_conf()
    if not conf:
        _TEXT_PENDING.clear()
        return True
    while _TEXT_PENDING:
        try:
            send_text(conf, _TEXT_PENDING[0])
        except (OSError, smtplib.SMTPException) as e:
            print("alerts: text failed, will retry:", str(e)[:200], flush=True)
            return False
        _TEXT_PENDING.pop(0)
    return True


def setup_text():
    """Ask for the sending account and the phone's email-to-text address,
    save them where only this account can read them, and send a test."""
    print("Text messages are sent as email to your phone's email-to-text address,\n"
          "through an email account used only for this (for Gmail: an app password).\n")
    old = load_text_conf() or {}
    user = input(f"Sending email address [{old.get('user', '')}]: ").strip() or old.get("user", "")
    password = getpass.getpass("App password (not shown; Enter keeps the saved one): ").replace(" ", "") or old.get("password", "")
    to = input(f"Send texts to, comma-separated [{', '.join(old.get('to', []))}]: ").strip()
    to = [t.strip() for t in to.split(",") if t.strip()] or old.get("to", [])
    conf = {"host": old.get("host", "smtp.gmail.com"), "port": old.get("port", 587),
            "user": user, "password": password, "to": to}
    fd = os.open(TEXT_CONF + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(conf, f)
    os.replace(TEXT_CONF + ".tmp", TEXT_CONF)
    try:
        send_text(conf, "RDBF flight alerts: test text. Texts are set up.")
        print("Saved, and a test text was sent. If it doesn't arrive in a minute, check the address.")
    except (OSError, smtplib.SMTPException) as e:
        print("Saved, but the test text failed:", e)


def append_event(ev, name="emergencies.jsonl"):
    os.makedirs(EVENTS_DIR, exist_ok=True)
    with open(os.path.join(EVENTS_DIR, name), "a") as f:
        f.write(json.dumps(ev, separators=(",", ":")) + "\n")


def queue_alert(ev, test=False):
    """Commit the alert file; push_pending() sends it."""
    ensure_repo()
    title, body = compose(ev, test)
    os.makedirs(os.path.join(REPO_DIR, "alerts"), exist_ok=True)
    name = (f"alerts/{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(ev['t']))}-{ev['hex']}-"
            f"{ev.get('code') or ev.get('kind')}.json")
    with open(os.path.join(REPO_DIR, name), "w") as f:
        json.dump({"title": title, "body": body, "event": ev}, f, indent=1)
    git("add", name)
    git("-c", "user.name=RDBF ADS-B receiver", "-c", "user.email=adsb-receiver@localhost",
        "commit", "-q", "-m", title)
    _TEXT_PENDING.append(text_line(title))


def push_pending():
    """Push unpushed alert commits. Returns True when nothing is left."""
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        return True
    r = git("push", "-q", "origin", f"HEAD:{BRANCH}", check=False)
    if r.returncode != 0:
        print("alerts: push failed, will retry:", r.stderr.strip()[:200], flush=True)
        return False
    return True


def event_from(ac, code, status, now, rx):
    ev = {"t": int(now), "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
          "hex": ac.get("hex", ""), "flight": (ac.get("flight") or "").strip(), "type": actype(ac),
          "code": code, "emergency": status if status not in (None, "none") else None,
          "squawk": ac.get("squawk"), "lat": ac.get("lat"), "lon": ac.get("lon"),
          "alt": ac.get("alt_baro"), "gs": ac.get("gs"), "track": ac.get("track"),
          "mlat": 1 if "lat" in (ac.get("mlat") or []) else 0}
    if rx[0] is not None and ev["lat"] is not None:
        ev["dist_nm"], ev["bearing"] = [round(v, 1) for v in dist_bearing(rx[0], rx[1], ev["lat"], ev["lon"])]
    return ev


def actype(ac):
    return ac.get("t") or aircraft_lookup.type_for(ac.get("hex"))


_ALTIM = {"at": 0, "hpa": None, "vis": None}


def _refresh_wx():
    """KIAD's latest altimeter setting (hPa) and visibility (statute miles)
    from the map build's weather, refreshed every 5 minutes; None if missing
    or over 3 hours old."""
    now = time.time()
    if now - _ALTIM["at"] > 300:
        _ALTIM.update(at=now, hpa=None, vis=None)
        try:
            with open(os.path.join(WORK_DIR, "live.json")) as f:
                wx = [w for w in json.load(f).get("wx", []) if w.get("epoch")]
            recent = [w for w in wx if now - w["epoch"] < 3 * 3600]
            alt = [w for w in recent if w.get("altim_hpa")]
            vis = [w for w in recent if isinstance(w.get("vsby"), (int, float))]
            _ALTIM["hpa"] = alt[-1]["altim_hpa"] if alt else None
            _ALTIM["vis"] = vis[-1]["vsby"] if vis else None
        except (OSError, ValueError):
            pass


def current_altim():
    _refresh_wx()
    return _ALTIM["hpa"]


def current_visibility():
    _refresh_wx()
    return _ALTIM["vis"]


def load_watch():
    try:
        with open(WATCH_JSON) as f:
            return {h.lower(): v for h, v in json.load(f)["aircraft"].items()}
    except (OSError, ValueError, KeyError):
        return {}


def load_watch_flights():
    """{callsign: {"name", "dates"}} from watch_flights.json; no dates = every day."""
    try:
        with open(WATCH_FLIGHTS_JSON) as f:
            return {c.strip().upper(): v for c, v in json.load(f)["flights"].items()}
    except (OSError, ValueError, KeyError, AttributeError):
        return {}


def watched(ac, watch, flights, now):
    """(name, registration, type name, callsign if a watched flight) for a
    watched aircraft, or a watched flight on one of its days; None otherwise."""
    h = (ac.get("hex") or "").lower()
    if h in watch:
        name, reg, type_name = watch[h]
        return name, reg, type_name, None
    fl = (ac.get("flight") or "").strip().upper()
    e = flights.get(fl)
    if not e:
        return None
    day = datetime.datetime.fromtimestamp(now, LOCAL).date().isoformat()
    if e.get("dates") and day not in e["dates"]:
        return None
    t = actype(ac)
    return e.get("name") or fl, aircraft_lookup.reg_for(h), farm.type_name(t) or t or "type unknown", fl


def sight_limit_mi():
    """SIGHT_MI, or KIAD's visibility when it is reported below 10 miles
    (10 means "10 or more" in a METAR)."""
    vis = current_visibility()
    return min(SIGHT_MI, vis) if vis is not None and vis < 10 else SIGHT_MI


def look(x, y, agl):
    """From the farm to a point x nm east and y nm north, agl ft above its
    ground: (slant_mi, horiz_mi, bearing, elevation_deg)."""
    horiz = math.hypot(x, y) * 1.15078
    slant = math.hypot(horiz, agl / 5280)
    elev = math.degrees(math.atan2(agl, horiz * 5280)) if horiz or agl else 90.0
    return slant, horiz, (math.degrees(math.atan2(x, y)) + 360) % 360, elev


def in_sight(v, limit):
    return v[0] <= limit and v[3] >= SIGHT_MIN_ELEV


def sight_path(ac, agl, limit):
    """Its path straight ahead for SIGHT_AHEAD_S at its present track, ground
    speed and climb or descent (flat earth: fine at these distances) ->
    {"now": look(), "closest": (in_s, slant, horiz, bearing, elev, agl),
     "window": (first, last) seconds from now in sight, or None}."""
    x = (ac["lon"] - farm.FARM[1]) * 60 * math.cos(math.radians(farm.FARM[0]))
    y = (ac["lat"] - farm.FARM[0]) * 60
    gs, trk = ac.get("gs"), ac.get("track")
    rate = ac.get("baro_rate") if ac.get("baro_rate") is not None else ac.get("geom_rate")
    moving = gs is not None and trk is not None and gs > 30
    now = look(x, y, agl)
    best, first, last = None, None, None
    for t in range(0, SIGHT_AHEAD_S + 1, SIGHT_STEP_S) if moving else (0,):
        d = gs * t / 3600 if moving else 0.0
        a = max(0.0, agl + (rate or 0) * t / 60)
        v = look(x + d * math.sin(math.radians(trk or 0)), y + d * math.cos(math.radians(trk or 0)), a)
        if best is None or v[0] < best[1]:
            best = (t, *v, a)
        if in_sight(v, limit):
            first = t if first is None else first
            last = t
        elif last is not None:
            break                          # out of sight again: the window is over
    if not moving and first is not None:
        last = SIGHT_AHEAD_S               # not going anywhere: in sight for now
    return {"now": now, "closest": best, "window": (first, last) if first is not None else None}


def sight_verdict(p, limit):
    """None if the projected path is worth an alert, else why not."""
    w = p["window"]
    if w is None:
        return "too low" if p["closest"][1] <= limit else "not coming within sight"
    if w[0] > SIGHT_LEAD_S:
        return "not yet"
    if w[1] - w[0] < SIGHT_MIN_S:
        return "heading away" if w[0] == 0 else "only briefly in sight"
    return None


def sighting(ac, who):
    """For a watched aircraft with a fresh position: (path from sight_path,
    agl_ft now); None otherwise."""
    alt = ac.get("alt_baro")
    if who is None or ac.get("lat") is None or ac.get("seen_pos", 99) > 10:
        return None
    if alt == "ground":
        agl = 0
    elif isinstance(alt, (int, float)):
        agl = max(0, farm.height_agl(alt, ac["lat"], ac["lon"], current_altim()) or 0)
    else:
        return None
    return sight_path(ac, agl, sight_limit_mi()), agl


def sighting_event(ac, now, p, agl, who, limit):
    slant, horiz, brg, elev = p["now"]
    in_s, c_slant, c_horiz, c_brg, c_elev, c_agl = p["closest"]
    name, reg, type_name, flight_watch = who
    return {"kind": "sighting", "t": int(now), "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            "hex": ac.get("hex", ""), "flight": (ac.get("flight") or "").strip(), "type": actype(ac),
            "name": name, "reg": reg, "type_name": type_name, "watched_flight": flight_watch,
            "slant_mi": round(slant, 1), "horiz_mi": round(horiz, 1), "bearing": round(brg),
            "elev_deg": round(elev), "agl": round(agl), "alt": ac.get("alt_baro"),
            "closest": {"in_s": in_s, "slant_mi": round(c_slant, 1), "horiz_mi": round(c_horiz, 1),
                        "bearing": round(c_brg), "elev_deg": round(c_elev), "agl": round(c_agl)},
            "in_sight_s": list(p["window"]) if p["window"] else None,
            "gs": ac.get("gs"), "track": ac.get("track"), "lat": ac["lat"], "lon": ac["lon"],
            "limit_mi": limit, "visibility_mi": current_visibility(),
            "mlat": 1 if "lat" in (ac.get("mlat") or []) else 0}


def compose_sighting(ev):
    c = ev.get("closest") or {"in_s": 0, "slant_mi": ev["slant_mi"], "horiz_mi": ev["horiz_mi"],
                              "bearing": ev["bearing"], "elev_deg": ev["elev_deg"], "agl": ev["agl"]}
    look_c = COMPASS16[round(c["bearing"] / 22.5) % 16]
    look_now = COMPASS16[round(ev["bearing"] / 22.5) % 16]
    heading = (f", heading {COMPASS16[round(ev['track'] / 22.5) % 16]}" if ev.get("track") is not None else "")
    who = ev["reg"] or ev.get("flight") or ev["hex"].upper()
    mins = round(c["in_s"] / 60)
    when = "in sight now" if mins < 1 else f"coming into sight: closest in about {mins} min"
    title = (f"👀 {ev['name']} ({who}) {when}, look {look_c}, "
             f"about {c['elev_deg']}° up, {c['slant_mi']:.0f} mi away")
    body = "\n".join([
        f"@RDBFarm — {ev['name']} ({who}, {ev['type_name']}) is worth looking for from the farm ({ev['utc']} UTC).",
        "",
        f"- **At its closest** ({'now' if mins < 1 else f'in about {mins} min'}): look {look_c} (bearing {c['bearing']}°), "
        f"about {c['elev_deg']}° above the horizon, {c['slant_mi']:.1f} miles away, about {c['agl']:,} ft up",
        f"- **Right now:** {look_now} (bearing {ev['bearing']}°), {ev['slant_mi']:.1f} miles away, "
        f"about {ev['agl']:,} ft above the farm's ground{heading}"
        + (f", {ev['gs']:.0f} kt" if ev.get("gs") is not None else ""),
        f"- **Callsign:** {ev.get('flight') or 'not broadcast'}; ICAO {ev['hex'].upper()}",
        "",
        f"[ADS-B Exchange](https://globe.adsbexchange.com/?icao={ev['hex']}) · "
        f"[Farm receiver (farm network only)](http://192.168.1.190/tar1090/?icao={ev['hex']}) · "
        "[Live map](https://rdbfarm.github.io/iad-map/live.html)",
        "",
        f"_Automatic alert from `pi/alerts.py`, once per visit. Its path is projected straight ahead at its present "
        f"course, speed and climb or descent; it alerts only when that path is in sight (within {ev['limit_mi']:g} miles"
        + (f", KIAD visibility {ev['visibility_mi']:g} mi" if ev.get("visibility_mi") is not None and ev["visibility_mi"] < 10 else "")
        + f", at least {SIGHT_MIN_ELEV}° up) within {SIGHT_LEAD_S // 60} minutes for at least a minute. "
        "Those limits are estimates; a turn changes the path, and cloud isn't known._"])
    return title, body


def low_pass_candidate(ac, prev):
    """(lat, lon, alt, agl) of the lower end of a path segment that crosses
    the farm's land below LOW_ALERT_AGL_FT, or None. prev is the aircraft's
    previous position (t, lat, lon, alt) from an earlier read, or None."""
    alt = ac.get("alt_baro")
    if not isinstance(alt, (int, float)) or ac.get("lat") is None or ac.get("seen_pos", 99) > 10:
        return None
    if prev is None or not farm.crosses(prev[1], prev[2], ac["lat"], ac["lon"]):
        return None
    lat, lon, a = (prev[1], prev[2], prev[3]) if prev[3] < alt else (ac["lat"], ac["lon"], alt)
    if not farm.inside(lat, lon):
        lat, lon = ac["lat"], ac["lon"]
    agl = farm.height_agl(a, lat, lon, current_altim())
    if agl is None or agl >= farm.LOW_ALERT_AGL_FT or farm.is_prop(actype(ac)) is True:
        return None
    return lat, lon, a, agl


def low_pass_should_push(ac, agl):
    """Known non-prop type, and for a helicopter, below HELI_ALERT_AGL_FT."""
    t = actype(ac)
    if farm.is_prop(t) is not False:
        return False                     # prop plane or unknown type
    if farm.is_helicopter(t):
        return agl < farm.HELI_ALERT_AGL_FT
    return True


def low_pass_event(ac, now, where):
    lat, lon, alt, agl = where
    ground, basis = farm.ground_ft(lat, lon)
    return {"kind": "low_pass", "t": int(now),
            "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            "hex": ac.get("hex", ""), "flight": (ac.get("flight") or "").strip(), "type": actype(ac),
            "prop": farm.is_prop(actype(ac)), "alt": alt, "agl": agl, "ground_ft": ground,
            "ground_basis": basis, "altim_hpa": current_altim(), "gs": ac.get("gs"),
            "track": ac.get("track"), "lat": lat, "lon": lon,
            "mlat": 1 if "lat" in (ac.get("mlat") or []) else 0}


def watch():
    streak = {}     # (hex, code) -> consecutive reads
    active = {}     # (hex, code) -> last time seen
    last_pos = {}    # hex -> (t, lat, lon, alt) from the previous read
    low_last = {}    # hex -> time of last low-pass push
    low_logged = {}  # hex -> time of last low-pass logged without a push
    sight_streak = {}  # hex -> consecutive reads worth an alert
    sight_last = {}    # hex -> last time near, for the visit it alerted
    sight_logged = {}  # hex -> last time near, for a visit logged as not worth it
    watch_list = load_watch()
    watch_flights = load_watch_flights()
    pending = False
    rx = receiver_position()
    while True:
        started = time.time()
        try:
            with open(AIRCRAFT_JSON) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print("alerts: skipped a reading:", e, flush=True)
            data = {"aircraft": []}
        now = data.get("now", started)
        seen_now = set()
        streak_now = {}   # sight_streak for this read only: an aircraft not heard starts again
        for ac in data.get("aircraft", []):
            if ac.get("seen", 99) > 10:
                continue
            h = ac.get("hex")
            prev = last_pos.get(h)
            if prev and not (0 < now - prev[0] <= farm.PREV_MAX_S):
                prev = None
            if ac.get("lat") is not None and isinstance(ac.get("alt_baro"), (int, float)):
                last_pos[h] = (now, ac["lat"], ac["lon"], ac["alt_baro"])
            who = watched(ac, watch_list, watch_flights, now)
            s = sighting(ac, who) if who else None
            limit = sight_limit_mi()
            if s and (s[0]["now"][0] <= limit or s[0]["window"]):
                p, agl = s
                why_not = sight_verdict(p, limit)
                if h in sight_last:
                    sight_last[h] = now                   # same visit, already alerted
                elif why_not is None:
                    streak_now[h] = sight_streak.get(h, 0) + 1
                    if streak_now[h] >= SIGHT_CONFIRM_READS:
                        sight_last[h] = now
                        ev = sighting_event(ac, now, p, agl, who, limit)
                        ev["pushed"] = True
                        append_event(ev, "sightings.jsonl")
                        print("alerts: sighting", ev["name"], ev["reg"], ev["closest"]["slant_mi"], "mi in",
                              ev["closest"]["in_s"], "s", flush=True)
                        try:
                            queue_alert(ev)
                            pending = True
                        except (OSError, subprocess.SubprocessError) as e:
                            print("alerts: could not queue alert:", e, flush=True)
                elif p["now"][0] <= limit:
                    if h not in sight_logged:            # in range but not worth it: logged once per visit
                        ev = sighting_event(ac, now, p, agl, who, limit)
                        ev["pushed"], ev["why_not"] = False, why_not
                        append_event(ev, "sightings.jsonl")
                        print("alerts: sighting not pushed (" + why_not + ")", ev["name"], ev["reg"], flush=True)
                    sight_logged[h] = now
            where = low_pass_candidate(ac, prev)
            if where:
                push = low_pass_should_push(ac, where[3])
                due = (now - low_last.get(h, 0) > LOW_PASS_REPEAT_S if push
                       else now - low_logged.get(h, 0) > LOW_PASS_REPEAT_S and now - low_last.get(h, 0) > LOW_PASS_REPEAT_S)
                if due:
                    (low_last if push else low_logged)[h] = now
                    ev = low_pass_event(ac, now, where)
                    ev["pushed"] = push
                    append_event(ev, "low_passes.jsonl")
                    print("alerts: low pass", ev["hex"], ev.get("flight"), ev["agl"], "ft AGL",
                          "pushed" if ev["pushed"] else "logged only", flush=True)
                    if ev["pushed"]:
                        try:
                            queue_alert(ev)
                            pending = True
                        except (OSError, subprocess.SubprocessError) as e:
                            print("alerts: could not queue alert:", e, flush=True)
            status = ac.get("emergency")
            if ac.get("squawk") in SQUAWKS:
                code = ac.get("squawk")                 # pushed
            elif status and status != "none":
                code = "status-" + status               # logged, never pushed
            else:
                continue
            key = (ac.get("hex"), code)
            seen_now.add(key)
            streak[key] = streak.get(key, 0) + 1
            if streak[key] == CONFIRM_READS and key not in active:
                ev = event_from(ac, code, status, now, rx)
                ev["pushed"] = code in SQUAWKS
                append_event(ev)
                print("alerts:", ev["code"], ev["hex"], ev["flight"], flush=True)
                if ev["pushed"]:
                    try:
                        queue_alert(ev)
                        pending = True
                    except (OSError, subprocess.SubprocessError) as e:
                        print("alerts: could not queue alert:", e, flush=True)
            if streak[key] >= CONFIRM_READS:
                active[key] = now
        for key in list(streak):
            if key not in seen_now:
                del streak[key]
        last_pos = {h: v for h, v in last_pos.items() if now - v[0] <= farm.PREV_MAX_S}
        low_last = {h: t for h, t in low_last.items() if now - t <= LOW_PASS_REPEAT_S}
        low_logged = {h: t for h, t in low_logged.items() if now - t <= LOW_PASS_REPEAT_S}
        sight_streak = streak_now
        sight_last = {h: t for h, t in sight_last.items() if now - t <= SIGHT_GAP_S}
        sight_logged = {h: t for h, t in sight_logged.items() if now - t <= SIGHT_GAP_S}
        for key, last in list(active.items()):
            if now - last > EPISODE_GAP_S:
                del active[key]
        if pending:
            pending = not push_pending()
        if _TEXT_PENDING and time.time() >= _TEXT_RETRY["after"]:
            if not send_texts_pending():
                _TEXT_RETRY["after"] = time.time() + TEXT_RETRY_S
        time.sleep(max(0.2, INTERVAL_S - (time.time() - started)))


def main():
    if not os.path.ismount(DRIVE) and EVENTS_DIR.startswith(DRIVE):
        raise SystemExit(f"alerts: {DRIVE} is not mounted; not writing to the SD card instead")
    if "--setup-text" in sys.argv:
        setup_text()
        return
    if "--test" in sys.argv:
        rx = receiver_position()
        ev = event_from({"hex": "000000", "flight": "TEST", "lat": rx[0], "lon": rx[1],
                         "alt_baro": 0, "squawk": "7700"}, "7700", None, time.time(), rx)
        ev["test"] = True
        queue_alert(ev, test=True)
        print("alerts: test alert", "pushed" if push_pending() else "NOT pushed — see the error above")
        if load_text_conf():
            print("alerts: test text", "sent" if send_texts_pending() else "NOT sent — see the error above")
        return
    watch()


if __name__ == "__main__":
    main()
