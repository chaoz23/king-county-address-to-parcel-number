#!/usr/bin/env python3
"""Live conformance QC: one real address per King County city (+ unincorporated)
must resolve to a parcel in the right jurisdiction.

Addresses are shared with king-county-permit-status (qc_addresses.json), already
validated against the city-limits polygon there. Checks per city:
  action in (use, pick)         a parcel came back
  parcel_number is 10 digits    and parcel_id is county-namespaced
  jurisdiction.city == city     (polygon or locator), or unincorporated for the
                                 county row; Milton is expected on the Pierce side

  python3 scripts/qc_parcels.py              # all (~1 min)
  python3 scripts/qc_parcels.py kent vashon  # subset
  python3 scripts/qc_parcels.py --json out.json
Exit 1 on any FAIL.
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import lookup  # noqa: E402

ADDRESSES = os.path.join(ROOT, "qc_addresses.json")
EXPECT_COUNTY = {"milton": "pierce"}


def check(city: str, address: str) -> dict:
    t0 = time.time()
    r = lookup.lookup(address)
    dt = round(time.time() - t0, 1)
    fails = []
    j = r.get("jurisdiction") or {}
    pin = r.get("parcel_number") or ""
    if r.get("action") not in ("use", "pick"):
        fails.append(f"action={r.get('action')}: {r.get('message', '')[:80]}")
    if len(pin) != 10:
        fails.append(f"parcel_number={pin!r}")
    county = EXPECT_COUNTY.get(city, "king")
    if r.get("county") != county:
        fails.append(f"county={r.get('county')} (expected {county})")
    if r.get("parcel_id") != f"{county}:{pin}" and pin:
        fails.append(f"parcel_id={r.get('parcel_id')}")
    if city == "king county":
        if not j.get("unincorporated"):
            fails.append(f"expected unincorporated, got {j}")
    elif (j.get("city") or "").lower() != city:
        fails.append(f"jurisdiction={j.get('city')!r} via {j.get('basis')}")
    return {"city": city, "address": address, "action": r.get("action"), "parcel_id": r.get("parcel_id"),
            "basis": (r.get("address_resolution") or {}).get("parcel_basis"),
            "match": (r.get("address_resolution") or {}).get("match_type"),
            "elapsed_s": dt, "outcome": "FAIL" if fails else "PASS", "fails": fails}


def main() -> int:
    argv = sys.argv[1:]
    out_path = None
    if "--json" in argv:
        i = argv.index("--json"); out_path = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    want = [a.lower() for a in argv]
    addresses = json.load(open(ADDRESSES)); addresses.pop("_comment", None)
    cities = [c for c in addresses if not want or c in want]
    print(f"Parcel QC: {len(cities)} jurisdictions (live)\n" + "=" * 72)
    reports = [check(c, addresses[c]) for c in cities]
    for r in reports:
        print(f"  {r['outcome']:4} {r['city']:22} {r['action']:6} {str(r['parcel_id']):18} {str(r['match']):12} {str(r['basis']):14} {r['elapsed_s']:4.1f}s")
        for f in r["fails"]:
            print(f"         ✗ {f}")
    tally = {k: sum(1 for r in reports if r["outcome"] == k) for k in ("PASS", "FAIL")}
    print("=" * 72 + f"\n  {tally}")
    if out_path:
        json.dump({"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tally": tally, "cities": reports},
                  open(out_path, "w"), indent=1)
        print(f"  wrote {out_path}")
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
