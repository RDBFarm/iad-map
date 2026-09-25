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

Other ADS-B emergency statuses (lifeguard, minfuel, downed, reserved) are
logged but not pushed; the owner asked for the three squawks only. An episode
ends after 10 minutes without the code, and a later one alerts again.
Pushes that fail (no internet) are retried every read until they succeed.

  python3 alerts.py          run
  python3 alerts.py --test   push one alert marked TEST, then exit
"""
import json, math, os, subprocess, sys, time

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


_ALTIM = {"at": 0, "hpa": None}


def current_altim():
    """KIAD's latest altimeter setting (hPa) from the map build's weather,
    refreshed every 5 minutes; None if missing or over 3 hours old."""
    now = time.time()
    if now - _ALTIM["at"] > 300:
        _ALTIM["at"], _ALTIM["hpa"] = now, None
        try:
            with open(os.path.join(WORK_DIR, "live.json")) as f:
                wx = [w for w in json.load(f).get("wx", []) if w.get("altim_hpa") and w.get("epoch")]
            if wx and now - wx[-1]["epoch"] < 3 * 3600:
                _ALTIM["hpa"] = wx[-1]["altim_hpa"]
        except (OSError, ValueError):
            pass
    return _ALTIM["hpa"]


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
        for ac in data.get("aircraft", []):
            if ac.get("seen", 99) > 10:
                continue
            h = ac.get("hex")
            prev = last_pos.get(h)
            if prev and not (0 < now - prev[0] <= farm.PREV_MAX_S):
                prev = None
            if ac.get("lat") is not None and isinstance(ac.get("alt_baro"), (int, float)):
                last_pos[h] = (now, ac["lat"], ac["lon"], ac["alt_baro"])
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
        for key, last in list(active.items()):
            if now - last > EPISODE_GAP_S:
                del active[key]
        if pending:
            pending = not push_pending()
        time.sleep(max(0.2, INTERVAL_S - (time.time() - started)))


def main():
    if not os.path.ismount(DRIVE) and EVENTS_DIR.startswith(DRIVE):
        raise SystemExit(f"alerts: {DRIVE} is not mounted; not writing to the SD card instead")
    if "--test" in sys.argv:
        rx = receiver_position()
        ev = event_from({"hex": "000000", "flight": "TEST", "lat": rx[0], "lon": rx[1],
                         "alt_baro": 0, "squawk": "7700"}, "7700", None, time.time(), rx)
        ev["test"] = True
        queue_alert(ev, test=True)
        print("alerts: test alert", "pushed" if push_pending() else "NOT pushed — see the error above")
        return
    watch()


if __name__ == "__main__":
    main()
