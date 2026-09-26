#!/usr/bin/env python3
"""Build pi/notable.json, the nicknames live.html shows for remarkable aircraft.

From plane-alert-db (github.com/sdr-enthusiasts/plane-alert-db), a community
list of notable aircraft, under the Open Database License (ODbL 1.0); the
output keeps its attribution and licence, as the ODbL requires.

Only government, military and public-service aircraft are kept (owner's
choice, 2026-09-26). Categories that point at private people -- celebrity
and wealthy owners' jets, vanity registrations, "as seen on TV" -- and the
list's joke categories are left out, and the list's operator field is never
copied, because it can name a private owner.

    python3 tools/make_notable.py     # downloads the current list, writes pi/notable.json
"""
import csv, io, json, urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/sdr-enthusiasts/plane-alert-db/main/plane-alert-db.csv"
OUT = Path(__file__).resolve().parent.parent / "pi" / "notable.json"

KEEP = {
    # government
    "Radiohead", "Governments", "Head of State", "Royal Aircraft", "Quango", "Nuclear",
    # military
    "USAF", "United States Navy", "United States Marine Corps", "Toy Soldiers", "Other Air Forces",
    "Other Navies", "GAF", "RAF", "Army Air Corps", "Royal Navy Fleet Air Arm", "Special Forces",
    "Gunship", "Zoomies", "UAV", "Oxcart",
    # public service
    "Coastguard", "Police Forces", "UK National Police Air Service", "Flying Doctors",
    "Aerial Firefighter", "Fire Fighting", "CAP",
    # the aircraft, not the owner
    "Aerobatic Teams", "Historic",
}
# a mixed category (the E-4B "doomsday plane" next to a billionaire's jet):
# only its military and government entries
KEEP_IF_MIL_GOV = {"Distinctive"}


def main():
    text = urllib.request.urlopen(URL, timeout=60).read().decode("utf-8")
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        cat, cmpg = r["Category"].strip(), r["#CMPG"].strip()
        if not (cat in KEEP or (cat in KEEP_IF_MIL_GOV and cmpg in ("Mil", "Gov"))):
            continue
        hexid = r["$ICAO"].strip().lower()
        tags = [t.strip() for t in (r["$Tag 1"], r["$#Tag 2"], r["$#Tag 3"]) if t.strip()]
        link = r["$#Link"].strip()
        out[hexid] = [tags, r["$Type"].strip(), cat, link if link.startswith("https://") else "",
                      r["$Registration"].strip()]
    OUT.write_text(json.dumps({
        "_source": "plane-alert-db, github.com/sdr-enthusiasts/plane-alert-db, (C) SDR-Enthusiasts, "
                   "Ramon F. Kolb (kx1t) and contributors; Open Database License 1.0 "
                   "(opendatacommons.org/licenses/odbl/1.0/). Government, military and public-service "
                   "categories only; built by tools/make_notable.py.",
        "_fields": ["tags", "type", "category", "link", "registration"],
        "aircraft": dict(sorted(out.items())),
    }, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(out)} aircraft")


if __name__ == "__main__":
    main()
