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

from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.parse

# King County's four-county locator returns the PIN *and* the county
# (Subregion) for King/Pierce/Snohomish/Kitsap, so the split cities (Milton,
# Pacific, Auburn) resolve on both sides of the line.
GEOCODER_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services"
    "/Address/FourCounty_locator/GeocodeServer/findAddressCandidates"
)
# City-limits polygons (all four counties): NAME, UNINC. The mailing city is
# not the permitting authority — "Kent, WA" addresses sit in unincorporated
# King County, Vashon has no city at all.
CITY_LIMITS_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services"
    "/Administration/KingCo_AdministrativeAreas/MapServer/2/query"
)
STATE_PLANE_WKID = "2926"          # WA State Plane North, US feet
CITY_LIMITS_NAME_MAP = {"beaux arts": "beaux arts village"}
# Locator Addr_type → how much of the address was really matched.
MATCH_TYPE = {"PointAddress": "point", "Subaddress": "point",
              "Parcel": "point",       # a parcel-address match carries the PIN
              "StreetAddress": "interpolated", "StreetName": "street",
              "Locality": "locality"}
NEARBY_PARCEL_FEET = "40"

ADDRESS_POINTS_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services"
    "/Address/KingCo_AddressPoints/MapServer/0/query"
)

PARCELS_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services"
    "/Property/KingCo_Parcels/MapServer/0/query"
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

HOUSE_NUMBER_RE = re.compile(r"^\s*\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?\b")


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


def has_house_number(address: str) -> bool:
    """Return whether the address starts with a street-address house number."""
    return bool(HOUSE_NUMBER_RE.search(address))


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


def _arcgis_json(url: str, params: dict, timeout: int = 15) -> dict:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def split_house_street(address: str) -> tuple[str, str]:
    """('1817', 'Morris Ave S') from the first comma-free component."""
    first = address.split(",")[0].strip()
    m = HOUSE_NUMBER_RE.search(first)
    if not m:
        return "", first
    house = m.group(0).strip()
    street = first[m.end():].strip()
    street = re.sub(r"\s+\d{5}(?:-\d{4})?$", "", street)      # drop a trailing ZIP
    return house, street


def geocode(query: str, max_locations: int) -> list[dict]:
    data = _arcgis_json(GEOCODER_URL, {
        "SingleLine": query, "outFields": "*", "outSR": STATE_PLANE_WKID,
        "maxLocations": max_locations, "f": "json"})
    return data.get("candidates") or []


def candidate_pin(c: dict) -> str:
    pin = re.sub(r"\D", "", str((c.get("attributes") or {}).get("PIN") or ""))
    return pin if len(pin) == 10 else ""


def candidate_county(c: dict) -> str | None:
    return ((c.get("attributes") or {}).get("Subregion") or "").strip().lower() or None


def candidate_match_type(c: dict) -> str:
    return MATCH_TYPE.get((c.get("attributes") or {}).get("Addr_type") or "", "none")


def usable(c: dict) -> bool:
    """Score ≥ 80, or an exact address point ≥ 70: the locator docks points
    for a non-city locality ("Vashon WA" → 75) even when house number and
    street matched exactly."""
    score = c.get("score", 0)
    return score >= 80 or (score >= 70 and candidate_match_type(c) == "point")


def nearby_parcel(location: dict) -> tuple[str, list[str], str | None]:
    """PIN from the parcel polygon under (then within 40 ft of) a point, for
    interpolated/street matches that carry no PIN — the locator has no
    address points for some towns (Black Diamond, Beaux Arts, Skykomish).
    Returns (pin, candidate_pins, basis)."""
    if location.get("x") is None or location.get("y") is None:
        return "", [], None
    for dist in ("0", NEARBY_PARCEL_FEET):
        try:
            feats = _arcgis_json(PARCELS_URL, {
                "geometry": f"{location['x']},{location['y']}",
                "geometryType": "esriGeometryPoint", "inSR": STATE_PLANE_WKID,
                "spatialRel": "esriSpatialRelIntersects", "distance": dist,
                "units": "esriSRUnit_Foot", "outFields": "PIN",
                "returnGeometry": "false", "f": "json"}).get("features") or []
        except Exception:
            feats = []
        pins = [f["attributes"].get("PIN") for f in feats if (f.get("attributes") or {}).get("PIN")]
        if pins:
            basis = "nearby-parcel" if dist != "0" or len(pins) > 1 else "parcel-polygon"
            return pins[0], pins[:5], basis
    return "", [], None


def jurisdiction_for(location: dict, geocoder_city: str | None) -> dict:
    """{city, basis, unincorporated} from the city-limits polygon the point
    falls in; falls back to the locator's city."""
    out = {"city": None, "basis": None, "unincorporated": None}
    if location.get("x") is not None and location.get("y") is not None:
        try:
            feats = _arcgis_json(CITY_LIMITS_URL, {
                "geometry": f"{location['x']},{location['y']}",
                "geometryType": "esriGeometryPoint", "inSR": STATE_PLANE_WKID,
                "spatialRel": "esriSpatialRelIntersects", "outFields": "NAME,UNINC",
                "returnGeometry": "false", "f": "json"}).get("features") or []
        except Exception:
            feats = []
        if feats:
            attrs = feats[0].get("attributes") or {}
            name = " ".join((attrs.get("NAME") or "").split())
            key = CITY_LIMITS_NAME_MAP.get(name.lower(), name.lower())
            out["unincorporated"] = bool(attrs.get("UNINC"))
            out["city"] = None if out["unincorporated"] else key.title()
            out["basis"] = "city-limits"
            return out
    if geocoder_city:
        out["city"] = geocoder_city.title()
        out["basis"] = "geocoder-city"
        out["unincorporated"] = False
    return out


def resolution_for(c: dict, address: str) -> dict:
    """Everything an agent needs to trust (or not) a candidate: county,
    namespaced parcel id, jurisdiction, match quality, parcel basis."""
    attrs = c.get("attributes") or {}
    county = candidate_county(c)
    pin = candidate_pin(c)
    match_type = candidate_match_type(c)
    parcel_basis = "address-point" if pin else None
    candidate_parcels: list[str] = []
    location = c.get("location") or {}
    if not pin and county == "king":
        pin, candidate_parcels, parcel_basis = nearby_parcel(location)
    house_in, _ = split_house_street(address)
    house_out = str(attrs.get("AddNum") or "").strip()
    jur = jurisdiction_for(location, (attrs.get("City") or "").strip() or None)
    out = {
        "pin": pin,
        "parcel_id": f"{county}:{pin}" if county and pin else None,
        "county": county,
        "jurisdiction": jur,
        "address_resolution": {
            "matched_address": c.get("address"),
            "score": c.get("score"),
            "match_type": match_type,
            "partial_match": match_type != "point",
            "street_number_snapped": bool(house_in and house_out and house_in != house_out),
            "parcel_basis": parcel_basis,
        },
    }
    if candidate_parcels and len(candidate_parcels) > 1:
        out["address_resolution"]["candidate_parcels"] = candidate_parcels
    return out


def pin_verification_unavailable(pin: str, original_input: str, detail: str) -> dict:
    """Return a retryable result without claiming that a PIN is invalid."""
    return {
        "action": "refine",
        "parcel_number": None,
        "matched_address": None,
        "score": None,
        "input": original_input,
        "message": f"Could not verify parcel number {pin}: {detail}",
        "suggestions": ["Retry the same PIN when the King County parcel service is available"],
        "candidates": [],
    }


def lookup_pin(pin: str, original_input: str) -> dict:
    """Verify a 10-digit PIN against King County's authoritative parcel layer."""
    params = urllib.parse.urlencode({
        "where": f"PIN='{pin}'",
        "outFields": "PIN,MAJOR,MINOR",
        "returnGeometry": "false",
        "resultRecordCount": 1,
        "f": "json",
    })

    try:
        req = urllib.request.Request(
            f"{PARCELS_URL}?{params}", headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        features = data.get("features")
        if data.get("error") or not isinstance(features, list):
            raise ValueError("unexpected parcel service response")
        if features:
            feature = features[0]
            if not isinstance(feature, dict):
                raise ValueError("unexpected parcel feature")
            attrs = feature.get("attributes")
            if not isinstance(attrs, dict):
                raise ValueError("unexpected parcel attributes")
    except Exception as e:
        return pin_verification_unavailable(pin, original_input, str(e))

    if not features:
        return {
            "action": "reject",
            "parcel_number": None,
            "matched_address": None,
            "score": None,
            "input": original_input,
            "message": f"No King County parcel exists with parcel number {pin}.",
            "suggestions": ["Check all 10 digits against the source document"],
            "candidates": [],
        }

    verified_pin = attrs.get("PIN", "")
    if verified_pin != pin:
        return pin_verification_unavailable(
            pin, original_input, "King County returned an unexpected parcel"
        )

    major = attrs.get("MAJOR") or pin[:6]
    minor = attrs.get("MINOR") or pin[6:]
    return {
        "action": "use",
        "parcel_number": verified_pin,
        "parcel_id": f"king:{verified_pin}",
        "county": "king",
        "major": major,
        "minor": minor,
        "matched_address": None,
        "score": 100,
        "input": original_input,
        "message": f"Verified King County parcel number: {verified_pin}",
        "candidates": [],
    }


def check_input(address: str) -> dict | None:
    """Pre-flight: reject structurally bad input before hitting the network."""
    if not has_house_number(address):
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

    # Verify 10-digit PINs against the authoritative parcel layer.
    if is_pin(normalized):
        return lookup_pin(normalized, address)

    # Pre-flight sanity check
    rejection = check_input(normalized)
    if rejection:
        rejection["input"] = address
        return rejection

    # Hit the geocoder (four-county locator). A miss is retried once as
    # "<house> <street>, WA": the locator needs a region hint when the query
    # has none, and returns nothing for a non-city locality plus ZIP
    # ("13205 Vashon Hwy SW, Vashon, WA 98070" → [] while "…, WA" matches).
    try:
        candidates = geocode(normalized, 5)
        retried = False
        if not any(usable(c) for c in candidates):
            house, street = split_house_street(normalized)
            if house and street:
                candidates = geocode(f"{house} {street}, WA", 5) or candidates
                retried = True
    except Exception as e:
        return {
            "action": "refine",
            "parcel_number": None,
            "message": f"Could not reach King County geocoder: {e}",
            "candidates": [],
            "input": address,
        }

    good = [c for c in candidates if usable(c)]
    if retried and good:
        # Without a city the same street address can exist in several cities
        # ("220 4th Ave S" is in Kent and Edmonds): keep the top score only if
        # it is unambiguous, preferring King County — this is a KC tool.
        top = good[0].get("score", 0)
        points = [c for c in good if c.get("score", 0) == top and candidate_match_type(c) == "point"]
        cities = {((c.get("attributes") or {}).get("City") or "", candidate_county(c)) for c in points}
        if len(cities) > 1:
            king = [c for c in points if candidate_county(c) == "king"]
            good = king if len({(c.get("attributes") or {}).get("City") for c in king}) == 1 else []
            if not good:
                alts = [make_candidate(c.get("address", ""), candidate_pin(c), score=c.get("score"))
                        for c in points if candidate_pin(c)]
                return {
                    "action": "pick",
                    "parcel_number": alts[0]["parcel_number"] if alts else None,
                    "matched_address": None,
                    "score": None,
                    "input": address,
                    "message": "That street address exists in more than one city — add the city.",
                    "suggestions": ["Add the city or ZIP code"],
                    "candidates": alts,
                }

    # --- No usable hit: fall back to nearby address search ---
    if not good:
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

    # --- Process the best usable candidate ---
    best = good[0]
    score = best.get("score", 0)
    matched = best.get("address", "") or (best.get("attributes") or {}).get("Match_addr", "")
    res = resolution_for(best, normalized)
    pin = res["pin"]
    exact = res["address_resolution"]["match_type"] == "point"
    extras = {"parcel_id": res["parcel_id"], "county": res["county"],
              "jurisdiction": res["jurisdiction"], "address_resolution": res["address_resolution"]}
    if res["county"] and res["county"] != "king":
        extras["note"] = (f"This parcel is in {res['county'].title()} County; the parcel number is "
                          f"that county's APN, not a King County PIN.")

    # High confidence: an exact address point carrying its own PIN. An
    # interpolated match (house number placed along the street — it may not
    # exist) is a pick even when the parcel polygon under it gave a PIN.
    if pin and res["address_resolution"]["parcel_basis"] == "address-point" and (exact or score >= 90):
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
            **extras,
        }

    # Medium confidence — have a PIN (possibly from the parcel polygon)
    if pin:
        return {
            "action": "pick",
            "parcel_number": pin,
            "matched_address": matched,
            "score": score,
            "input": address,
            "message": (
                f"Best match: '{matched}' (confidence {score:.0f}%"
                + ("; the house number was interpolated and the parcel taken from the polygon under that point"
                   if res["address_resolution"]["parcel_basis"] in ("parcel-polygon", "nearby-parcel") else "")
                + "). Verify this is the right property."
            ),
            "candidates": [make_candidate(matched, pin, score=score)],
            **extras,
        }

    # Low confidence or no PIN — gather alternatives
    alts = []
    for c in candidates:
        cp = candidate_pin(c)
        cm = c.get("address", "") or (c.get("attributes") or {}).get("Match_addr", "")
        cs = c.get("score", 0)
        if cp and cs >= 50:
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
            **extras,
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
        **extras,
    }


EXIT_CODES = {"use": 0, "pick": 1, "refine": 1, "reject": 2}

TOOL_SCHEMA = {
    "name": "king_county_address_to_parcel",
    "description": (
        "Convert a King County, WA street address to its 10-digit parcel number, "
        "with the permitting jurisdiction derived from the city-limits polygon "
        "(not the mailing city) and the match quality. "
        "Returns action=use (exact match), action=pick (ambiguous — show candidates), "
        "action=refine (no match — try different input), or action=reject (bad input). "
        "Also verifies a bare parcel number or 'PIN: XXXXXXXXXX' against the "
        "official King County parcel layer. Split cities (Milton, Pacific, Auburn) "
        "resolve on the Pierce side too, namespaced 'pierce:'. "
        "Use this before any tool that requires a King County parcel number."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "address": {
                "type": "string",
                "description": (
                    "Street address in King County, WA. Include house number and street name. "
                    "City is recommended. Also verifies bare 10-digit parcel numbers. "
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
            "parcel_id": {
                "type": ["string", "null"],
                "description": "County-namespaced id, e.g. 'king:7222000353' or 'pierce:5985002900' (split cities straddle the county line).",
            },
            "county": {"type": ["string", "null"], "description": "king | pierce | snohomish | kitsap"},
            "jurisdiction": {
                "type": "object",
                "description": "The permitting authority: city (null when unincorporated), basis (city-limits = point inside the city's polygon — authoritative; geocoder-city = locator's city), unincorporated. The mailing city is NOT the authority: 'Kent, WA' addresses can be unincorporated King County.",
                "properties": {
                    "city": {"type": ["string", "null"]},
                    "basis": {"type": ["string", "null"], "enum": ["city-limits", "geocoder-city", None]},
                    "unincorporated": {"type": ["boolean", "null"]},
                },
            },
            "address_resolution": {
                "type": "object",
                "description": "Match quality: match_type (point = exact address point; interpolated = number placed along the street; street = street only), partial_match, street_number_snapped (the matched house number differs from the input), parcel_basis (address-point | parcel-polygon | nearby-parcel — the last two mean the PIN came from the parcel layer under/within 40 ft of the point; check candidate_parcels when present).",
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
