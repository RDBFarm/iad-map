#!/usr/bin/env python3
"""Build flights.html, the live map without the farm, from live.html.

live.html is the only page anybody edits. This takes out everything about
the farm: the land outline (and the address that came with it), the
over-the-farm summary, low passes, the near-the-farm search, distances and
heights relative to the farm, and the receiver's name.

    python3 tools/make_public.py           # writes flights.html
    python3 tools/make_public.py --check   # fails if flights.html is out of date (CI)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, OUT = ROOT / "live.html", ROOT / "flights.html"


def build(src):
    out, n = re.subn(r"<!--farm-->.*?<!--/farm-->", "", src, flags=re.S)
    if n < 4:
        raise SystemExit(f"expected at least 4 farm blocks in live.html, found {n}")
    for old, new in [
        ("const FARM_MODE = true;", "const FARM_MODE = false;"),
        ("<title>IAD Flight Activity — Live</title>", "<title>IAD Flight Activity</title>"),
    ]:
        if out.count(old) != 1:
            raise SystemExit(f"live.html no longer has exactly one {old!r}")
        out = out.replace(old, new)
    out, n = re.subn(r"^const PARCELS\s*=.*$", "const PARCELS = [];", out, count=1, flags=re.M)
    if n != 1:
        raise SystemExit("PARCELS line not found in live.html")
    # Nothing identifying the farm may survive into the public page.
    for word in ("EDWARDS FERRY", "18020", "RDBF receiver", "🦬", "Near the farm"):
        if word in out:
            raise SystemExit(f"{word!r} is still in the public page; mark it with farm comments in live.html")
    return ("<!-- Built from live.html by tools/make_public.py. Do not edit; edit live.html. -->\n" + out)


def main():
    page = build(SRC.read_text(encoding="utf-8"))
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != page:
            raise SystemExit("flights.html is out of date: run python3 tools/make_public.py")
        print("flights.html is up to date")
        return
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT.name}")


if __name__ == "__main__":
    main()
