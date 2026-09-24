#!/usr/bin/env python3
"""Watch for emergency squawks and push them to the owner's phone.

Every 2 seconds, reads readsb's /run/readsb/aircraft.json -- every aircraft
the receiver hears, at any range -- and looks for:

  squawk 7700 (general emergency), 7600 (radio failure), 7500 (unlawful
  interference), or the matching ADS-B emergency status (general, nordo,
  unlawful).

An aircraft has to show it on 3 reads in a row (~6 s) before it counts, so a
single garbled message doesn't raise an alarm. Then, once per episode:

  - the event is appended to /mnt/flightdata/iad-map/events/emergencies.jsonl
  - an alert file is committed to the `alerts` branch and pushed with the
    repo's deploy key; the GitHub Actions workflow on that branch opens an
    issue mentioning @RDBFarm, which GitHub Mobile turns into a phone push.
    (An issue opened with the owner's own token would not notify him:
    GitHub doesn't notify you about your own actions.)

Also: any aircraft within 1 nm of the farm below 1,500 ft (reported
pressure altitude), seen on 2 reads in a row, is logged to
events/low_passes.jsonl, once per aircraft per 10 minutes -- unless it is a
propeller aeroplane. It is pushed only when its type is known and is not a
prop plane (jets, helicopters); an unknown type is logged, not pushed.
Owner's decisions, 2026-09-24. farm.py decides what is a prop plane.

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
PUSH_STATUS = {"general": "7700", "nordo": "7600", "unlawful": "7500"}
LOG_ONLY_STATUS = {"lifeguard", "minfuel", "downed", "reserved"}
LOW_PASS_FT = 1500
LOW_PASS_READS = 2
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
    who = ev.get("flight") or ev["hex"].upper()
    kind = ""
    title = f"✈ Low over the farm: {who}" + (f" ({ev['type']})" if ev.get("type") else "") + f" at {ev['alt']:,} ft"
    body = "\n".join([
        f"@RDBFarm — {who}{kind} was {ev['dist_nm']} nm from the centre of the farm, and inside 1 nm, at {ev['utc']} UTC.",
        "",
        f"- **Altitude:** {ev['alt']:,} ft (reported pressure altitude, roughly above sea level; "
        "the farm's ground is a few hundred feet up)",
        f"- **Speed:** {ev['gs']:.0f} kt; **track** {ev['track']:.0f}°" if ev.get("gs") is not None and ev.get("track") is not None else "- **Speed/track:** not reported",
        f"- **Aircraft:** ICAO {ev['hex'].upper()}" + (f", type {ev['type']}" if ev.get("type") else ", type not reported"),
        "",
        f"[ADS-B Exchange](https://globe.adsbexchange.com/?icao={ev['hex']}) · "
        f"[Farm receiver (farm network only)](http://192.168.1.190/tar1090/?icao={ev['hex']}) · "
        "[Live map](https://rdbfarm.github.io/iad-map/live.html)",
        "",
        f"_Automatic alert from `pi/alerts.py`: within {farm.FARM_RADIUS_NM} nm of the farm below "
        f"{LOW_PASS_FT:,} ft on {LOW_PASS_READS} reads in a row. Only known non-prop types (jets, "
        "helicopters) are alerted; unknown types are logged._"])
    return title, body


def compose(ev, test=False):
    if ev.get("kind") == "low_pass":
        return compose_low_pass(ev)
    code = ev["code"]
    what = SQUAWKS.get(code, ev.get("emergency") or "")
    who = ev.get("flight") or ev["hex"].upper()
    title = f"{'TEST — ' if test else ''}🚨 {code} {who}" + (f" ({ev['type']})" if ev.get("type") else "") + f" — {what}"
    lines = [
        f"@RDBFarm — heard by the farm receiver at {ev['utc']} UTC.",
        "",
        f"- **Code:** {code} ({what})" + (f"; ADS-B status: {ev['emergency']}" if ev.get("emergency") else ""),
        f"- **Aircraft:** {who}, ICAO {ev['hex'].upper()}" + (f", type {ev['type']}" if ev.get("type") else ""),
    ]
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


def low_pass_candidate(ac):
    alt = ac.get("alt_baro")
    if not isinstance(alt, (int, float)) or alt >= LOW_PASS_FT:
        return False   # "ground", missing, or high enough
    if ac.get("lat") is None or ac.get("seen_pos", 99) > 10:
        return False
    if farm.dist_nm(ac["lat"], ac["lon"]) > farm.FARM_RADIUS_NM:
        return False
    return farm.is_prop(actype(ac)) is not True


def low_pass_event(ac, now):
    return {"kind": "low_pass", "t": int(now),
            "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            "hex": ac.get("hex", ""), "flight": (ac.get("flight") or "").strip(), "type": actype(ac),
            "prop": farm.is_prop(actype(ac)), "alt": ac.get("alt_baro"), "gs": ac.get("gs"),
            "track": ac.get("track"), "lat": ac.get("lat"), "lon": ac.get("lon"),
            "dist_nm": round(farm.dist_nm(ac["lat"], ac["lon"]), 2),
            "mlat": 1 if "lat" in (ac.get("mlat") or []) else 0}


def watch():
    streak = {}     # (hex, code) -> consecutive reads
    active = {}     # (hex, code) -> last time seen
    low_streak = {}  # hex -> consecutive low reads over the farm
    low_last = {}    # hex -> time of last low-pass alert
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
        low_now = set()
        for ac in data.get("aircraft", []):
            if ac.get("seen", 99) > 10:
                continue
            if low_pass_candidate(ac):
                h = ac.get("hex")
                low_now.add(h)
                low_streak[h] = low_streak.get(h, 0) + 1
                if low_streak[h] == LOW_PASS_READS and now - low_last.get(h, 0) > LOW_PASS_REPEAT_S:
                    low_last[h] = now
                    ev = low_pass_event(ac, now)
                    ev["pushed"] = ev["prop"] is False   # unknown type: log only
                    append_event(ev, "low_passes.jsonl")
                    print("alerts: low pass", ev["hex"], ev.get("flight"), ev["alt"],
                          "pushed" if ev["pushed"] else "logged (type unknown)", flush=True)
                    if ev["pushed"]:
                        try:
                            queue_alert(ev)
                            pending = True
                        except (OSError, subprocess.SubprocessError) as e:
                            print("alerts: could not queue alert:", e, flush=True)
            status = ac.get("emergency")
            code = ac.get("squawk") if ac.get("squawk") in SQUAWKS else PUSH_STATUS.get(status)
            if code is None and status in LOG_ONLY_STATUS:
                code = status  # logged, never pushed
            if code is None:
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
        for h in list(low_streak):
            if h not in low_now:
                del low_streak[h]
        low_last = {h: t for h, t in low_last.items() if now - t <= LOW_PASS_REPEAT_S}
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
