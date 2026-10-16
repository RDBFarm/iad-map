#!/usr/bin/env python3
"""Build beacons.js, the radio beacons heat.html marks on the wide map.

Owner's request (2026-10-16), after a bright knot on the wide map north of
State College turned out to be the Philipsburg VORTAC (PSB): "mark the
beacons on the wide map with the ability to turn them off".

From the FAA's NAVAID System layer (FAA Aeronautical Information Services
open data; US government work, public domain). Kept: every VOR, VOR/DME and
VORTAC inside the wide map's box (pi/heatmap.py WIDE), the beacons the
published air routes are drawn between. Left out: NDBs, DMEs and stand-alone
military TACANs, which the airliner routes on the map are not built on.

    python3 tools/make_beacons.py     # downloads the current list, writes beacons.js
"""
import json, sys, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pi"))
import heatmap  # noqa: E402  (the wide map's box)

URL = "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/NAVAIDSystem/FeatureServer/0/query"
OUT = ROOT / "beacons.js"


def kind(cls):
    """FAA class text (e.g. 'H-VORTACW', 'L-VORW/DME') -> VORTAC, VOR/DME or VOR."""
    c = cls.split("-", 1)[-1]
    return "VORTAC" if "VORTAC" in c else "VOR/DME" if "DME" in c else "VOR"


def main():
    a = heatmap.WIDE
    q = urllib.parse.urlencode({
        "geometry": f"{a.lon_min},{a.lat_min},{a.lon_max},{a.lat_max}", "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "IDENT,CLASS_TXT,NAME_TXT,US_HIGH,STATUS",
        "returnGeometry": "true", "outSR": 4326, "f": "json"})
    with urllib.request.urlopen(f"{URL}?{q}", timeout=60) as r:
        data = json.load(r)
    if "error" in data or data.get("exceededTransferLimit"):
        sys.exit(f"FAA query failed or was cut short: {data.get('error')}")
    out = []
    for f in data["features"]:
        at, g = f["attributes"], f["geometry"]
        cls = at.get("CLASS_TXT") or ""
        if "VOR" not in cls:
            continue
        out.append([at["IDENT"], (at.get("NAME_TXT") or "").title(), round(g["y"], 4), round(g["x"], 4),
                    kind(cls), 1 if at.get("US_HIGH") else 0])
    out.sort()
    body = ",\n".join("  " + json.dumps(b) for b in out)
    OUT.write_text(
        "// Radio beacons (VOR, VOR/DME, VORTAC) in the wide history map's box, for heat.html.\n"
        "// Built by tools/make_beacons.py from the FAA's NAVAID System open data (public domain).\n"
        "// [ident, name, lat, lon, kind, on the high-altitude (jet) route charts 1/0]\n"
        f"const BEACONS = [\n{body}\n];\n")
    print(f"{len(out)} beacons -> {OUT.name}")


if __name__ == "__main__":
    main()
