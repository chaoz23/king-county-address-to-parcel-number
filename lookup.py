#!/usr/bin/env python3
"""King County address → parcel number (PIN) lookup.

Uses the King County ArcGIS ParcelAddress geocoder. Returns the 10-digit PIN
(Major 6 + Minor 4) for a residential or commercial property.

Error handling returns suggestions instead of halting — designed for
conversational use where the user can correct and retry.
"""

import json
import sys
import urllib.request
import urllib.parse

GEOCODER_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services"
    "/Address/KingCo_ParcelAddress_locator/GeocodeServer/findAddressCandidates"
)

KING_COUNTY_CITIES = {
    "algona", "auburn", "beaux arts village", "bellevue", "black diamond",
    "bothell", "burien", "carnation", "clyde hill", "covington",
    "des moines", "duvall", "enumclaw", "fall city", "federal way",
    "hunts point", "issaquah", "kenmore", "kent", "kirkland",
    "lake forest park", "maple valley", "medina", "mercer island",
    "milton", "newcastle", "normandy park", "north bend", "pacific",
    "redmond", "renton", "sammamish", "seatac", "seattle", "shoreline",
    "skykomish", "snoqualmie", "tukwila", "woodinville", "yarrow point",
    "vashon", "white center", "skyway", "fairwood", "east renton highlands",
    "union hill-novelty hill", "cottage lake", "wilderness rim",
}

NON_KC_HINTS = {
    "tacoma": "Pierce County",
    "everett": "Snohomish County",
    "lynnwood": "Snohomish County",
    "olympia": "Thurston County",
    "spokane": "Spokane County",
    "vancouver": "Clark County",
    "bellingham": "Whatcom County",
    "puyallup": "Pierce County",
    "lakewood": "Pierce County",
    "marysville": "Snohomish County",
    "edmonds": "Snohomish County",
    "mountlake terrace": "Snohomish County",
    "mukilteo": "Snohomish County",
    "bremerton": "Kitsap County",
}


def check_address_sanity(address: str) -> dict | None:
    """Pre-flight check: is this plausibly a King County address?

    Returns None if OK, or a dict with suggestions if something looks off.
    """
    addr_lower = address.lower().strip()

    if not any(c.isdigit() for c in address):
        return {
            "issue": "no_street_number",
            "message": "This doesn't look like a street address (no house number). Try something like '1817 Morris Ave S, Renton, WA 98055'.",
        }

    for city, county in NON_KC_HINTS.items():
        if city in addr_lower:
            return {
                "issue": "wrong_county",
                "message": f"'{city.title()}' is in {county}, not King County. This tool only covers King County, WA.",
            }

    if any(state in addr_lower for state in [", or ", " oregon", ", ca ", " california", ", id ", " idaho"]):
        return {
            "issue": "wrong_state",
            "message": "This address doesn't appear to be in Washington state. This tool only covers King County, WA.",
        }

    return None


def geocode(address: str) -> dict:
    """Query the KC geocoder and return structured results."""
    params = urllib.parse.urlencode({
        "SingleLine": address,
        "outFields": "*",
        "maxLocations": 5,
        "f": "json",
    })
    url = f"{GEOCODER_URL}?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"status": "error", "message": f"Could not reach King County geocoder: {e}"}

    candidates = data.get("candidates", [])
    if not candidates:
        return {
            "status": "no_match",
            "message": (
                f"No match found for '{address}'. Check for typos — "
                "the geocoder needs a valid King County street address with house number."
            ),
            "suggestions": [
                "Verify the street name spelling",
                "Include the city (e.g. Renton, Seattle, Bellevue)",
                "Include directional suffixes (S, N, NE, SW) — they matter in KC",
                "Try the full format: '1234 Main St S, Renton, WA 98055'",
            ],
        }

    best = candidates[0]
    score = best.get("score", 0)
    attrs = best.get("attributes", {})
    pin = attrs.get("PIN", "")
    matched = attrs.get("Match_addr", "")

    if score >= 90 and pin and len(pin) == 10:
        return {
            "status": "match",
            "pin": pin,
            "major": pin[:6],
            "minor": pin[6:],
            "matched_address": matched,
            "score": score,
            "input_address": address,
        }

    if score >= 70:
        result = {
            "status": "low_confidence",
            "message": f"Best match is '{matched}' (confidence: {score}%). Verify this is the right property.",
            "pin": pin if pin and len(pin) == 10 else None,
            "matched_address": matched,
            "score": score,
        }
        if not pin or len(pin) != 10:
            result["message"] += " (No parcel number returned — the match may be a street-level interpolation, not a specific property.)"
        return result

    others = []
    for c in candidates[1:]:
        ca = c.get("attributes", {})
        cp = ca.get("PIN", "")
        cm = ca.get("Match_addr", "")
        cs = c.get("score", 0)
        if cs >= 50 and cp and len(cp) == 10:
            others.append({"address": cm, "pin": cp, "score": cs})

    return {
        "status": "poor_match",
        "message": f"Best match '{matched}' has low confidence ({score}%). Did you mean one of these?",
        "candidates": others if others else None,
        "suggestions": [
            "Check the street name and directional suffix (S, N, NE, SW)",
            "Make sure the city is in King County",
            f"Best guess was: {matched}",
        ],
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: lookup.py <address>")
        print('Example: lookup.py "1817 Morris Ave S, Renton, WA 98055"')
        sys.exit(1)

    address = " ".join(sys.argv[1:])

    sanity = check_address_sanity(address)
    if sanity:
        print(json.dumps(sanity, indent=2))
        sys.exit(1)

    result = geocode(address)
    print(json.dumps(result, indent=2))

    if result["status"] == "match":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
