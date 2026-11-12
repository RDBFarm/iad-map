#!/usr/bin/env python3
"""Which way Dulles lands, hour by hour, and how the farm forecast's direction rules score on it.

Owner, 11-12: keep the way Dulles is landing until the wind clearly changes ("yes, build it"), carry it
through the quiet night and let the forecast wind ahead set it at 5 AM ("make your changes now ... but
reassess later too"). pi/farmcast.py does both; this measures them again as the record grows.

    python3 tools/flow_rules.py add <folder of t/ hour files>   # count their hours into flow-hours.json
    python3 tools/flow_rules.py score                           # the comparisons, from flow-hours.json

flow-hours.json keeps every hour counted ({epoch hour: [flow, landings, near, above, degrees, knots]}),
because live-data keeps only 30 days of t/ files. Winds are KIAD METARs from the Iowa Environmental
Mesonet, the ones that blew, standing in for forecasts.
"""
import glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pi"))
import farmcast as C

STORE = os.path.join(ROOT, "tools", "flow-hours.json")


def load_store():
    try:
        with open(STORE) as f:
            return {int(h): r for h, r in json.load(f).items()}
    except OSError:
        return {}


def add(folder):
    starts = {}
    for name in glob.glob(os.path.join(folder, "*.json")):
        try:
            with open(name) as f:
                head = f.read(40)
            if head.startswith('{"start"'):
                starts[int(head.split(":")[1].split(",")[0])] = name
        except (OSError, ValueError):
            pass
    cache = {}

    def load(h):
        if h not in starts:
            return None
        if h not in cache:
            with open(starts[h]) as f:
                cache[h] = json.load(f)
        return cache[h]

    store, new = load_store(), {}
    for h in sorted(starts):
        if h - 3600 in starts and h + 3600 in starts and h not in store:
            new[h] = C.hour_record(h, load)
        for k in [k for k in cache if k < h - 3600]:
            del cache[k]
    if new:
        winds = C.winds_for(sorted(new), C.iem_reports(min(new), max(new)))
        for h, r in new.items():
            store[h] = r + (winds.get(h) or [None, None])
    with open(STORE, "w") as f:
        json.dump({str(h): store[h] for h in sorted(store)}, f, separators=(",", ":"))
    print(f"{len(new)} hours added; {len(store)} in all, {C.local(min(store)):%Y-%m-%d} to {C.local(max(store)):%Y-%m-%d}")


def score():
    store = load_store()
    rec = {h: r[:4] for h, r in store.items()}
    win = {h: r[4:6] for h, r in store.items() if r[5] is not None}
    m = {"axis_deg": C.AXIS_DEG, "keep": {"to_south_kt": C.TO_SOUTH_KT}}
    m.update(C.model(rec, win, "tools/flow_rules.py"))

    def now_flow(h, carry):
        f = rec[h][0]
        if f in ("S", "N", "M") or not carry:
            return f if f in ("S", "N") else None
        for g in range(h - 3600, h - carry * 3600 - 1, -3600):
            if g in rec and rec[g][0] in ("S", "N", "M"):
                return rec[g][0] if rec[g][0] != "M" else None
        return None

    def run(label, carry, look, keep=True, only=None):
        tot = right = 0
        for h in sorted(rec):
            f0 = now_flow(h, carry) if keep else None
            for k in range(1, C.KEEP_H + 1):
                t = h + k * 3600
                if t not in rec or t not in win or rec[t][0] not in ("S", "N") or (only and not only(t)):
                    continue
                f = f0
                if look and f in ("S", "N"):
                    r = next((g for g in range(h + 3600, t + 1, 3600) if C.local(g).hour == C.RESTART_HOUR), None)
                    if r is not None:
                        f = next((x for x in (C.favours(m, *win[r + 3600 * i]) for i in range(look)
                                              if r + 3600 * i in win) if x), f)
                p = C.p_south(m, *win[t]) if f not in ("S", "N") else \
                    {"N": 0.0, "S": 1.0}.get(C.favours(m, *win[t]), 1.0 if f == "S" else 0.0)
                tot += 1
                right += (p >= 0.5) == (rec[t][0] == "S")
        print(f"  {label:<48} {100 * right / tot:5.1f}% of {tot}")

    nights = len({C.local(h).date() for h in rec})
    print(f"{len(rec)} hours, {nights} days, {C.local(min(rec)):%Y-%m-%d} to {C.local(max(rec)):%Y-%m-%d}; "
          f"share of hours whose direction was right, 1-{C.KEEP_H} h ahead")
    for name, only in (("all hours", None), ("5-10 AM hours", lambda t: 5 <= C.local(t).hour <= 10)):
        print("-", name)
        run("wind only", 0, 0, keep=False, only=only)
        run("kept until the wind clearly turns", 0, 0, only=only)
        run("+ carried through quiet hours", C.QUIET_H, 0, only=only)
        run(f"+ {C.RESTART_HOUR} AM restart for the wind {C.LOOK_H} h ahead", C.QUIET_H, C.LOOK_H, only=only)


if __name__ == "__main__":
    if sys.argv[1:2] == ["add"]:
        add(sys.argv[2])
    elif sys.argv[1:2] == ["score"]:
        score()
    else:
        print(__doc__)
