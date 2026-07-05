#!/usr/bin/env python3
"""King County address → parcel number lookup.

Uses the King County ArcGIS ParcelAddress geocoder. Returns the 10-digit
parcel number (Major 6 + Minor 4) for a residential or commercial property.

Two modes, same JSON output:
  Human:  python3 lookup.py "600 Grady Way, Renton"
  Agent:  python3 lookup.py --pipe "600 Grady Way, Renton"

Every response includes:
  action  — what a pipeline should do: "use", "pick", "refine", "reject"
  message — what a human should read
  parcel_number — the answer (when action=use) or best guess (when action=pick)

Exit codes:
  0 = exact match (action=use), safe for pipelines to consume parcel_number
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

NON_WA_STATES = ("or", "oregon", "ca", "california", "id", "idaho")


def normalize_input(raw: str) -> str:
    """Normalize the input: strip parcel number prefixes, extra whitespace, etc."""
    s = raw.strip()
    s = re.sub(r"(?i)^pin[:\s#]+", "", s)
    if re.fullmatch(r"\d{10}", s):
        return s  # already a PIN
    s = re.sub(r"\s+", " ", s)
    return s


def is_pin(s: str) -> bool:
    return bool(re.fullmatch(r"\d{10}", s))


def find_non_kc_locality(address: str) -> tuple[str, str] | None:
    """Return an explicitly stated non-King-County city and county.

    City names can also be street names (for example, Lakewood Ave S in
    Seattle), so the street component must not be searched for locality hints.
    """
    parts = [part.strip().lower() for part in address.split(",")]

    if len(parts) > 1:
        locality_parts = parts[1:]
        for part in locality_parts:
            for city, county in NON_KC_HINTS.items():
                city_pattern = re.escape(city)
                if re.fullmatch(
                    rf"{city_pattern}(?:\s+(?:wa|washington))?"
                    rf"(?:\s+\d{{5}}(?:-\d{{4}})?)?",
                    part,
                ):
                    return city, county
        return None

    # Without commas, only reject a city in the conventional trailing
    # "<city> WA [ZIP]" position. Ambiguous input is left to the KC geocoder.
    for city, county in NON_KC_HINTS.items():
        city_pattern = re.escape(city)
        if re.search(
            rf"\b{city_pattern}\s+(?:wa|washington)"
            rf"(?:\s+\d{{5}}(?:-\d{{4}})?)?\s*$",
            address.lower(),
        ):
            return city, county

    return None


def has_explicit_non_wa_state(address: str) -> bool:
    """Return whether a supported non-Washington state is explicit."""
    parts = [part.strip().lower() for part in address.split(",")]
    state_pattern = "|".join(re.escape(state) for state in NON_WA_STATES)

    if len(parts) > 1:
        return any(
            re.search(rf"\b(?:{state_pattern})\b", part)
            for part in parts[1:]
        )

    return bool(re.search(
        rf"\b(?:{state_pattern})(?:\s+\d{{5}}(?:-\d{{4}})?)?\s*$",
        address.lower(),
    ))


def make_candidate(
    address: str,
    parcel_number: str,
    *,
    score: float | None = None,
    house_number_distance: int | None = None,
) -> dict:
    """Build one candidate using the public, source-independent contract."""
    return {
        "address": address,
        "parcel_number": parcel_number,
        "score": score,
        "house_number_distance": house_number_distance,
    }


def check_input(address: str) -> dict | None:
    """Pre-flight: reject structurally bad input before hitting the network."""
    if not any(c.isdigit() for c in address):
        return {
            "action": "reject",
            "message": "No house number found. A street address is required, e.g. '1817 Morris Ave S, Renton, WA 98055'.",
            "parcel_number": None,
            "candidates": [],
        }

    non_kc_locality = find_non_kc_locality(address)
    if non_kc_locality:
        city, county = non_kc_locality
        return {
            "action": "reject",
            "message": f"'{city.title()}' is in {county}, not King County. This tool only covers King County, WA.",
            "parcel_number": None,
            "candidates": [],
        }

    if has_explicit_non_wa_state(address):
        return {
            "action": "reject",
            "message": "This address doesn't appear to be in Washington state. This tool only covers King County, WA.",
            "parcel_number": None,
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
            results.append(make_candidate(
                a.get("ADDR_FULL", ""),
                pin,
                house_number_distance=distance,
            ))

    results.sort(key=lambda r: r["house_number_distance"])
    return results


def lookup(address: str) -> dict:
    """Core lookup. Returns a unified result dict for both human and agent."""

    normalized = normalize_input(address)

    # If it's already a 10-digit PIN, just validate and return
    if is_pin(normalized):
        return {
            "action": "use",
            "parcel_number": normalized,
            "major": normalized[:6],
            "minor": normalized[6:],
            "matched_address": None,
            "score": 100,
            "input": address,
            "message": f"Input is already a parcel number: {normalized}",
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
            "parcel_number": None,
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
                "parcel_number": best["parcel_number"],
                "matched_address": best["address"],
                "score": None,
                "input": address,
                "message": (
                    f"No exact match for '{normalized}'. "
                    f"Closest: {best['address']} (parcel {best['parcel_number']}). "
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
            "parcel_number": None,
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
            "parcel_number": pin,
            "major": pin[:6],
            "minor": pin[6:],
            "matched_address": matched,
            "score": score,
            "input": address,
            "message": f"Matched: {matched} → parcel {pin}",
            "candidates": [],
        }

    # Medium confidence — have a PIN but score is lower
    if score >= 70 and pin and len(pin) == 10:
        return {
            "action": "pick",
            "parcel_number": pin,
            "matched_address": matched,
            "score": score,
            "input": address,
            "message": (
                f"Best match: '{matched}' (confidence {score:.0f}%). "
                "Verify this is the right property."
            ),
            "candidates": [make_candidate(matched, pin, score=score)],
        }

    # Low confidence or no PIN — gather alternatives
    alts = []
    for c in candidates:
        ca = c.get("attributes", {})
        cp = ca.get("PIN", "")
        cm = ca.get("Match_addr", "")
        cs = c.get("score", 0)
        if cp and len(cp) == 10 and cs >= 50:
            alts.append(make_candidate(cm, cp, score=cs))

    if alts:
        return {
            "action": "pick",
            "parcel_number": alts[0]["parcel_number"],
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
        "parcel_number": None,
        "input": address,
        "message": f"Could not resolve '{normalized}' to a parcel. Geocoder returned '{matched}' but no parcel number.",
        "suggestions": [
            "Check the street name and directional suffix (S, N, NE, SW)",
            "Make sure the city is in King County",
        ],
        "candidates": nearby,
    }


EXIT_CODES = {"use": 0, "pick": 1, "refine": 1, "reject": 2}

TOOL_SCHEMA = {
    "name": "king_county_address_to_parcel",
    "description": (
        "Convert a King County, WA street address to its 10-digit parcel number. "
        "Returns action=use (exact match), action=pick (ambiguous — show candidates), "
        "action=refine (no match — try different input), or action=reject (bad input). "
        "Also accepts a bare parcel number or 'PIN: XXXXXXXXXX' as passthrough. "
        "Use this before any tool that requires a King County parcel number."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "address": {
                "type": "string",
                "description": (
                    "Street address in King County, WA. Include house number and street name. "
                    "City is recommended. Also accepts bare 10-digit parcel numbers. "
                    "Examples: '1817 Morris Ave S, Renton, WA 98055', "
                    "'600 Grady Way, Renton', '7222000353'."
                ),
            }
        },
        "required": ["address"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["use", "pick", "refine", "reject"],
                "description": (
                    "use — parcel_number is valid, consume it; "
                    "pick — multiple candidates, present to user or pick highest-score; "
                    "refine — no match found, try a different input; "
                    "reject — bad input (wrong county, no house number), do not retry"
                ),
            },
            "parcel_number": {
                "type": ["string", "null"],
                "description": "10-digit King County parcel number. Present when action=use or action=pick.",
            },
            "matched_address": {
                "type": ["string", "null"],
                "description": "Geocoder's canonical address string.",
            },
            "score": {
                "type": ["number", "null"],
                "description": "Match confidence 0–100. Scores ≥90 are reliable.",
            },
            "candidates": {
                "type": "array",
                "description": (
                    "Ranked alternatives. Every candidate has the same fields; "
                    "source-specific ranking values are null when unavailable."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "address": {"type": "string"},
                        "parcel_number": {"type": "string"},
                        "score": {
                            "type": ["number", "null"],
                            "description": "Geocoder confidence from 0–100.",
                        },
                        "house_number_distance": {
                            "type": ["integer", "null"],
                            "description": (
                                "Absolute house-number difference for nearby-address "
                                "fallback results; not a physical distance."
                            ),
                        },
                    },
                    "required": [
                        "address",
                        "parcel_number",
                        "score",
                        "house_number_distance",
                    ],
                },
            },
            "message": {"type": "string"},
            "suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hints for the user when action=refine or reject.",
            },
        },
        "required": ["action", "message"],
    },
    "invocation": {
        "command": "python3 lookup.py --pipe \"{address}\"",
        "exit_codes": {
            "0": "action=use — parcel_number is valid",
            "1": "action=pick or refine — needs user input or a different address",
            "2": "action=reject — do not retry without changing input",
        },
    },
}


def main():
    args = sys.argv[1:]
    pipe_mode = "--pipe" in args
    schema_mode = "--schema" in args
    args = [a for a in args if a not in ("--pipe", "--schema")]

    if schema_mode:
        print(json.dumps(TOOL_SCHEMA, indent=2))
        sys.exit(0)

    if not args:
        print("Usage: lookup.py [--pipe] [--schema] <address>")
        print('  Human:  lookup.py "1817 Morris Ave S, Renton, WA 98055"')
        print('  Agent:  lookup.py --pipe "1817 Morris Ave S, Renton, WA 98055"')
        print('  Schema: lookup.py --schema')
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
