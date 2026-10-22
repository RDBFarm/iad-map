#!/usr/bin/env python3
"""Every 5 minutes (iad-map-netcheck.timer, as root): can the Pi look up
internet addresses? If not, write down what still works, and after 15 minutes
renew the Pi's network settings.

Why (owner, 2026-10-22: "happy to add whatever it takes to make this thing
work"): twice in two days the Pi could not look up any address for hours
(10-21 noon to 3:15 PM; 10-21 6:30 PM to 10-22 9:25 AM). Uploads, the weather
and the ADS-B Exchange feed all failed with "Temporary failure in name
resolution" while the Pi stayed reachable on the farm network. NetworkManager
logged nothing in between; at 9:24:59 the Pi renewed its lease from the router,
at 9:26 NetworkManager set its IPv6 route and name servers again, and lookups
worked from then on. Both outages ended within minutes of the owner logging in
to the Pi from his computer, so the trouble may be between the Pi and the
router, or the router's lookup service. Logging in can't be how we find out.

Each check looks up github.com the normal way. When that fails it records, in
events/netcheck.jsonl on the drive and in the journal:
  - whether the router answers a ping, and 1.1.1.1 (the internet by number)
  - which name servers answer a lookup sent straight to them: each one in
    /etc/resolv.conf, the ones NetworkManager says it is using, and 1.1.1.1
  - whether there is a default route, IPv4 and IPv6
After FAIL_RENEW failed checks in a row it renews the network settings (a
fresh lease from the router, which came just before the 10-22 recovery), at
most once every RENEW_EVERY_S, and records whether lookups came back. The end
of an outage is recorded too, with how long it lasted.

  python3 netcheck.py            one check (what the timer runs)
  python3 netcheck.py --status   the last records, for reading on the Pi
"""
import datetime, json, os, random, socket, struct, subprocess, sys, time

DRIVE = os.environ.get("IADMAP_DRIVE", "/mnt/flightdata")
DATA_DIR = os.path.join(DRIVE, "iad-map")
EVENTS_DIR = os.environ.get("IADMAP_EVENTS_DIR", os.path.join(DATA_DIR, "events"))
STATE = os.path.join(DATA_DIR, "netcheck_state.json")
ROUTER = os.environ.get("IADMAP_ROUTER", "192.168.1.1")
PUBLIC_DNS = "1.1.1.1"
NAME = "github.com"
IFACE = "eth0"
FAIL_RENEW = 3            # failed checks in a row (15 minutes) before renewing
RENEW_EVERY_S = 3600      # at most one renewal an hour while it lasts
SETTLE_S = 20             # wait this long after renewing before looking again


def run(*cmd, timeout=40):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def lookup(name=NAME):
    """The normal way every program on the Pi looks a name up."""
    try:
        socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def dns_query(name):
    """A standard DNS question for name's IPv4 address: (id, packet)."""
    qid = random.randrange(65536)
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split("."))
    return qid, struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + labels + b"\0" + struct.pack(">HH", 1, 1)


def dns_verdict(qid, reply):
    if len(reply) < 12:
        return "short reply"
    rid, flags, _, answers = struct.unpack(">HHHH", reply[:8])
    if rid != qid:
        return "wrong reply"
    rcode = flags & 0x0F
    return "ok" if rcode == 0 and answers else f"refused (code {rcode})" if rcode else "no address"


def ask(server, name=NAME, timeout=3):
    """Send the question straight to one name server, bypassing the Pi's own
    resolver: 'ok', or what went wrong."""
    qid, packet = dns_query(name)
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(packet, (server, 53))
            reply, _ = s.recvfrom(4096)
    except socket.timeout:
        return "no answer"
    except OSError as e:
        return f"unreachable ({e.strerror or e})"
    return dns_verdict(qid, reply)


def ping(host):
    return run("ping", "-c", "1", "-W", "2", host, timeout=10)[0] == 0


def resolv_conf_servers(path="/etc/resolv.conf"):
    try:
        with open(path) as f:
            return [line.split()[1] for line in f if line.startswith("nameserver") and len(line.split()) > 1]
    except OSError:
        return []


def nm_servers():
    code, out = run("nmcli", "-g", "IP4.DNS,IP6.DNS", "device", "show", IFACE, timeout=10)
    return [s.replace("\\:", ":") for line in out.splitlines() for s in line.split(" | ") if s] if code == 0 else []


def diagnose():
    servers = resolv_conf_servers()
    nm = nm_servers()
    asked = {s: ask(s) for s in dict.fromkeys(servers + nm + [PUBLIC_DNS])}
    return {"router_ping": ping(ROUTER), "internet_ping": ping(PUBLIC_DNS),
            "resolv_conf": servers, "networkmanager_dns": nm, "dns": asked,
            "default_route_v4": bool(run("ip", "route", "show", "default", timeout=10)[1]),
            "default_route_v6": bool(run("ip", "-6", "route", "show", "default", timeout=10)[1])}


def renew():
    """A fresh lease and settings for eth0's connection: (ok, what happened)."""
    code, conn = run("nmcli", "-g", "GENERAL.CONNECTION", "device", "show", IFACE, timeout=10)
    if code != 0 or not conn:
        return False, f"no connection on {IFACE}: {conn}"
    code, out = run("nmcli", "-w", "30", "connection", "up", conn, timeout=60)
    return code == 0, out[-300:]


TOOLS = {"lookup": lookup, "diagnose": diagnose, "renew": renew, "sleep": time.sleep}


def load_state(path=STATE):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state, path=STATE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(path + ".tmp", path)


def record(ev, events_dir=EVENTS_DIR):
    os.makedirs(events_dir, exist_ok=True)
    line = json.dumps(ev, separators=(",", ":"))
    with open(os.path.join(events_dir, "netcheck.jsonl"), "a") as f:
        f.write(line + "\n")
    print("netcheck:", line, flush=True)


def stamp(t):
    return datetime.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def check(state, now, tools=TOOLS, events_dir=EVENTS_DIR):
    """One check; returns the record written, or None when all is well."""
    if tools["lookup"]():
        if not state.get("fail_since"):
            return None
        ev = {"kind": "recovered", "t": int(now), "local": stamp(now), "since": stamp(state["fail_since"]),
              "down_min": round((now - state["fail_since"]) / 60), "renewals": state.get("renewals", 0)}
        state.clear()
        record(ev, events_dir)
        return ev
    state.setdefault("fail_since", now)
    state["fails"] = state.get("fails", 0) + 1
    ev = {"kind": "lookup_failed", "t": int(now), "local": stamp(now), "since": stamp(state["fail_since"]),
          "fails": state["fails"], **tools["diagnose"]()}
    if state["fails"] >= FAIL_RENEW and now - state.get("renewed_at", 0) >= RENEW_EVERY_S:
        ok, what = tools["renew"]()
        state["renewed_at"], state["renewals"] = now, state.get("renewals", 0) + 1
        tools["sleep"](SETTLE_S)
        ev.update(renewed=ok, renew_output=what, lookup_after_renew=tools["lookup"]())
    record(ev, events_dir)
    return ev


def main():
    if "--status" in sys.argv:
        try:
            with open(os.path.join(EVENTS_DIR, "netcheck.jsonl")) as f:
                print("".join(f.readlines()[-10:]) or "no records: lookups have not failed")
        except OSError:
            print("no records: lookups have not failed")
        return
    if not os.path.ismount(DRIVE):
        raise SystemExit(f"netcheck: {DRIVE} is not mounted; not writing to the SD card instead")
    state = load_state()
    check(state, time.time())
    save_state(state)


if __name__ == "__main__":
    main()
