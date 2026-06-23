#!/usr/bin/env python3
"""King County address → parcel number (PIN) lookup.

Uses the King County ArcGIS ParcelAddress geocoder. Returns the 10-digit PIN
(Major 6 + Minor 4) for a residential or commercial property.

Two modes, same JSON output:
  Human:  python3 lookup.py "600 Grady Way, Renton"
  Agent:  python3 lookup.py --pipe "600 Grady Way, Renton"

Every response includes:
  action  — what a pipeline should do: "use", "pick", "refine", "reject"
  message — what a human should read
  pin     — the answer (when action=use) or best guess (when action=pick)

Exit codes:
  0 = exact match (action=use), safe for pipelines to consume pin
  1 = ambiguous (action=pick/refine), candidates available
  2 = bad input (action=reject), do not retry without changing input
"""

import json
import re
import sys
import urllib.request
import urllib.parse

GEOCODER_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services"
    "/Address/KingCo_ParcelAddress_locator/GeocodeServer/findAddressCandidates"
)

ADDRESS_POINTS_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services"
    "/Address/KingCo_AddressPoints/MapServer/0/query"
)

NON_KC_HINTS = {
    "tacoma": "Pierce County", "everett": "Snohomish County",
    "lynnwood": "Snohomish County", "olympia": "Thurston County",
    "spokane": "Spokane County", "vancouver": "Clark County",
    "bellingham": "Whatcom County", "puyallup": "Pierce County",
    "lakewood": "Pierce County", "marysville": "Snohomish County",
    "edmonds": "Snohomish County", "mountlake terrace": "Snohomish County",
    "mukilteo": "Snohomish County", "bremerton": "Kitsap County",
}


def normalize_input(raw: str) -> str:
    """Normalize the input: strip PIN prefixes, extra whitespace, etc."""
    s = raw.strip()
    s = re.sub(r"(?i)^pin[:\s#]+", "", s)
    if re.fullmatch(r"\d{10}", s):
        return s  # already a PIN
    s = re.sub(r"\s+", " ", s)
    return s


def is_pin(s: str) -> bool:
    return bool(re.fullmatch(r"\d{10}", s))


def check_input(address: str) -> dict | None:
    """Pre-flight: reject structurally bad input before hitting the network."""
    addr_lower = address.lower()

    if not any(c.isdigit() for c in address):
        return {
            "action": "reject",
            "message": "No house number found. A street address is required, e.g. '1817 Morris Ave S, Renton, WA 98055'.",
            "pin": None,
            "candidates": [],
        }

    for city, county in NON_KC_HINTS.items():
        if city in addr_lower:
            return {
                "action": "reject",
                "message": f"'{city.title()}' is in {county}, not King County. This tool only covers King County, WA.",
                "pin": None,
                "candidates": [],
            }

    if any(state in addr_lower for state in [", or ", " oregon", ", ca ", " california", ", id ", " idaho"]):
        return {
            "action": "reject",
            "message": "This address doesn't appear to be in Washington state. This tool only covers King County, WA.",
            "pin": None,
            "candidates": [],
        }

    return None


def find_nearby(address: str) -> list[dict]:
    """Search KC AddressPoints for similar addresses when geocoder misses."""
    parts = address.upper().replace(",", " ").split()

    house_num = ""
    street_name = ""
    street_type = ""
    for p in parts:
        if p.isdigit() and not house_num:
            house_num = p
        elif p in ("ST", "AVE", "WAY", "DR", "PL", "CT", "BLVD", "RD", "LN", "CIR"):
            street_type = p
        elif len(p) >= 3 and p not in ("WA", "WAY") and p.isalpha() and not street_name:
            street_name = p

    if not street_name:
        return []

    where_parts = [f"ADDR_SN='{street_name}'"]
    if street_type:
        where_parts.append(f"ADDR_ST='{street_type}'")
    if house_num:
        low = max(0, int(house_num) - 200)
        high = int(house_num) + 200
        where_parts.append(f"ADDR_HN BETWEEN '{low}' AND '{high}'")

    params = urllib.parse.urlencode({
        "where": " AND ".join(where_parts),
        "outFields": "ADDR_FULL,PIN,ADDR_HN",
        "f": "json",
        "resultRecordCount": 8,
        "orderByFields": "ADDR_HN",
    })

    try:
        url = f"{ADDRESS_POINTS_URL}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception:
        return []

    results = []
    for f in data.get("features", []):
        a = f["attributes"]
        pin = a.get("PIN", "")
        if pin and len(pin) == 10:
            distance = abs(int(a.get("ADDR_HN", 0)) - int(house_num)) if house_num else 999
            results.append({
                "address": a.get("ADDR_FULL", ""),
                "pin": pin,
                "distance": distance,
            })

    results.sort(key=lambda r: r["distance"])
    return results


def lookup(address: str) -> dict:
    """Core lookup. Returns a unified result dict for both human and agent."""

    normalized = normalize_input(address)

    # If it's already a 10-digit PIN, just validate and return
    if is_pin(normalized):
        return {
            "action": "use",
            "pin": normalized,
            "major": normalized[:6],
            "minor": normalized[6:],
            "matched_address": None,
            "score": 100,
            "input": address,
            "message": f"Input is already a PIN: {normalized}",
            "candidates": [],
        }

    # Pre-flight sanity check
    rejection = check_input(normalized)
    if rejection:
        rejection["input"] = address
        return rejection

    # Hit the geocoder
    params = urllib.parse.urlencode({
        "SingleLine": normalized,
        "outFields": "*",
        "maxLocations": 5,
        "f": "json",
    })
    try:
        req = urllib.request.Request(
            f"{GEOCODER_URL}?{params}", headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {
            "action": "refine",
            "pin": None,
            "message": f"Could not reach King County geocoder: {e}",
            "candidates": [],
            "input": address,
        }

    candidates = data.get("candidates", [])

    # --- No geocoder hits: fall back to nearby address search ---
    if not candidates:
        nearby = find_nearby(normalized)
        if nearby:
            best = nearby[0]
            return {
                "action": "pick",
                "pin": best["pin"],
                "matched_address": best["address"],
                "score": None,
                "input": address,
                "message": (
                    f"No exact match for '{normalized}'. "
                    f"Closest: {best['address']} (PIN {best['pin']}). "
                    "Did you mean one of these?"
                ),
                "suggestions": [
                    "Include directional suffixes (S, N, NE, SW) — they matter in KC",
                    "Verify the house number exists on this street",
                ],
                "candidates": nearby,
            }
        return {
            "action": "refine",
            "pin": None,
            "input": address,
            "message": (
                f"No match found for '{normalized}'. "
                "Check for typos — needs a valid King County street address."
            ),
            "suggestions": [
                "Verify the street name spelling",
                "Include the city (e.g. Renton, Seattle, Bellevue)",
                "Include directional suffixes (S, N, NE, SW)",
                "Try the full format: '1234 Main St S, Renton, WA 98055'",
            ],
            "candidates": [],
        }

    # --- Process geocoder results ---
    best = candidates[0]
    score = best.get("score", 0)
    attrs = best.get("attributes", {})
    pin = attrs.get("PIN", "")
    matched = attrs.get("Match_addr", "")

    # High confidence exact match
    if score >= 90 and pin and len(pin) == 10:
        return {
            "action": "use",
            "pin": pin,
            "major": pin[:6],
            "minor": pin[6:],
            "matched_address": matched,
            "score": score,
            "input": address,
            "message": f"Matched: {matched} → PIN {pin}",
            "candidates": [],
        }

    # Medium confidence — have a PIN but score is lower
    if score >= 70 and pin and len(pin) == 10:
        return {
            "action": "pick",
            "pin": pin,
            "matched_address": matched,
            "score": score,
            "input": address,
            "message": (
                f"Best match: '{matched}' (confidence {score:.0f}%). "
                "Verify this is the right property."
            ),
            "candidates": [{"address": matched, "pin": pin, "score": score}],
        }

    # Low confidence or no PIN — gather alternatives
    alts = []
    for c in candidates:
        ca = c.get("attributes", {})
        cp = ca.get("PIN", "")
        cm = ca.get("Match_addr", "")
        cs = c.get("score", 0)
        if cp and len(cp) == 10 and cs >= 50:
            alts.append({"address": cm, "pin": cp, "score": cs})

    if alts:
        return {
            "action": "pick",
            "pin": alts[0]["pin"],
            "matched_address": alts[0]["address"],
            "score": alts[0]["score"],
            "input": address,
            "message": f"Low confidence. Best guess: {alts[0]['address']}. Did you mean one of these?",
            "candidates": alts,
        }

    # Nothing usable
    nearby = find_nearby(normalized)
    return {
        "action": "refine",
        "pin": None,
        "input": address,
        "message": f"Could not resolve '{normalized}' to a parcel. Geocoder returned '{matched}' but no PIN.",
        "suggestions": [
            "Check the street name and directional suffix (S, N, NE, SW)",
            "Make sure the city is in King County",
        ],
        "candidates": nearby,
    }


EXIT_CODES = {"use": 0, "pick": 1, "refine": 1, "reject": 2}


def main():
    args = sys.argv[1:]
    pipe_mode = "--pipe" in args
    args = [a for a in args if a != "--pipe"]

    if not args:
        print("Usage: lookup.py [--pipe] <address>")
        print('  Human:  lookup.py "1817 Morris Ave S, Renton, WA 98055"')
        print('  Agent:  lookup.py --pipe "1817 Morris Ave S, Renton, WA 98055"')
        print("")
        print("Actions:  use (exit 0) | pick (exit 1) | refine (exit 1) | reject (exit 2)")
        sys.exit(2)

    address = " ".join(args)
    result = lookup(address)

    if pipe_mode:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print(json.dumps(result, indent=2))

    sys.exit(EXIT_CODES.get(result["action"], 1))


if __name__ == "__main__":
    main()
