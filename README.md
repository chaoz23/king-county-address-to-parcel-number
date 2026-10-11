# King County Address to Parcel Number

Convert a King County, WA street address to its 10-digit parcel number (PIN).

Two modes — same JSON schema, different formatting:

```bash
# Human (pretty-printed)
python3 lookup.py "1817 Morris Ave S, Renton, WA 98055"

# Agent pipeline (compact, deterministic exit codes)
python3 lookup.py --pipe "1817 Morris Ave S, Renton, WA 98055"

# Print tool definition (Anthropic/OpenAI tool-call schema)
python3 lookup.py --schema
```

## Response contract

Every response has the same shape:

```json
{
  "action": "use",
  "parcel_number": "7222000353",
  "matched_address": "1817 MORRIS AVE S, Renton, WA, 98055",
  "score": 100,
  "message": "Matched: 1817 MORRIS AVE S → parcel 7222000353",
  "candidates": []
}
```

| Field | For | Description |
|---|---|---|
| `action` | Agent | `use` (take the parcel number), `pick` (choose from candidates), `refine` (try different input), `reject` (bad input, don't retry) |
| `parcel_number` | Both | The answer (action=use) or best guess (action=pick), null otherwise |
| `parcel_id` | Both | County-namespaced id — `king:7222000353`, or `pierce:5985001690` for the Pierce side of Milton/Pacific/Auburn |
| `county` | Both | `king` / `pierce` / `snohomish` / `kitsap` (the four-county locator) |
| `jurisdiction` | Both | **The permitting authority**, from the city-limits polygon the point falls in: `{city, basis: city-limits \| geocoder-city, unincorporated}`. The mailing city is not the authority — "Kent, WA" addresses can be unincorporated King County, Vashon has no city |
| `address_resolution` | Agent | Match quality: `match_type` (`point` = exact address point · `interpolated` = house number placed along the street · `street`), `partial_match`, `street_number_snapped`, `parcel_basis` (`address-point` · `parcel-polygon` · `nearby-parcel` = the PIN came from the parcel layer under / within 40 ft of the point; `candidate_parcels` lists alternatives when several) |
| `candidates` | Agent | Ranked alternatives using the candidate contract below |
| `message` | Human | Plain-English explanation of what happened |
| `suggestions` | Human | What to try next when it doesn't match |

Every candidate has the same shape regardless of which King County service
produced it:

| Candidate field | Description |
|---|---|
| `address` | Canonical candidate address |
| `parcel_number` | Candidate's 10-digit parcel number |
| `score` | Geocoder confidence from 0–100, or `null` for nearby-address results |
| `house_number_distance` | Absolute difference between input and candidate house numbers, or `null` for geocoder results; this is not a physical distance |

## Exit codes

| Code | Action | Pipeline behavior |
|---|---|---|
| 0 | `use` | Consume `parcel_number` directly |
| 1 | `pick` / `refine` | Candidates available — agent picks best or asks upstream |
| 2 | `reject` | Bad input (wrong county, no house number) — don't retry |

## Input normalization

The tool handles messy input:

| Input | Result |
|---|---|
| `"1817 Morris Ave S, Renton, WA 98055"` | `use` — exact match, score 100 |
| `"1817 Moris Ave S, Renton"` | `use` — fuzzy match corrects typo, score 95 |
| `"1817 Morris Ave, Renton"` | `use` — infers missing directional "S", score 99 |
| `"PIN: 7222000353"` | `use` — verifies the PIN against King County's parcel layer |
| `"7222000353"` | `use` — verifies the bare PIN before returning it |
| `"600 Grady Way, Renton"` | `pick` — the house number was interpolated; the parcel under that point is offered with `parcel_basis: nearby-parcel` |
| `"13205 Vashon Hwy SW, Vashon, WA 98070"` | `use` — retried as `13205 Vashon Hwy SW, WA`; `jurisdiction.unincorporated: true` |
| `"1000 Laurel St, Milton, WA 98354"` | `use` — `pierce:5985001690`, with a note that it is a Pierce County APN |
| `"220 4th Ave S"` | `use` — exists in Kent and Edmonds; King County wins the tie (an all-King tie becomes `pick`) |
| `"123 Main St, Tacoma"` | `reject` — Tacoma is Pierce County |
| `"Morris Ave Renton"` | `reject` — no house number |

## Hosted API + MCP (no install)

| | |
|---|---|
| **MCP** (Streamable HTTP) | `POST https://parcels-api.secondlandings.ai/mcp` — tool `king_county_address_to_parcel`, arg `address` |
| **REST** | `GET https://parcels-api.secondlandings.ai/api/parcel?q=1817+Morris+Ave+S,+Renton+WA` |
| **Docs** | `/openapi.json` · `/llms.txt` · `/tool.json` · `/healthz` |

`server.py` is stdlib-only; results are cached 24 h per query and rate-limited per client (30/min). Run it yourself with `python3 server.py` or `docker compose up` (`deploy/README.md`). `server.json` is the MCP Registry manifest.

## Quality checks

| Check | Question |
|---|---|
| `python3 -m unittest discover -s tests` | Does the code still do what it did? (offline; also runs Mondays) |
| `python3 scripts/qc_parcels.py` | Does every King County city's QC address (`qc_addresses.json`, shared with the permit tool) resolve to a parcel in the right jurisdiction, live? Runs Mondays → `qc_health.json` |

## For agents

`tool.json` at the repo root contains the full tool definition in Anthropic/OpenAI tool-call format. An agent can load it directly or fetch the live version:

```bash
python3 lookup.py --schema
```

```json
{
  "name": "king_county_address_to_parcel",
  "description": "Convert a King County, WA street address to its 10-digit parcel number...",
  "input_schema": {
    "type": "object",
    "properties": {
      "address": { "type": "string", "description": "Street address in King County, WA..." }
    },
    "required": ["address"]
  },
  "output_schema": { ... },
  "invocation": {
    "command": "python3 lookup.py --pipe \"{address}\"",
    "exit_codes": {
      "0": "action=use — parcel_number is valid",
      "1": "action=pick or refine — needs user input or a different address",
      "2": "action=reject — do not retry without changing input"
    }
  }
}
```

**Pipeline pattern:**
```bash
PARCEL=$(python3 lookup.py --pipe "1817 Morris Ave S, Renton" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['parcel_number'] if d['action']=='use' else '')")
```

**Related tools in this series:**
- [`king-county-permit-status`](https://github.com/chaoz23/king-county-permit-status) — look up permit history by address, parcel, or permit number
- [`wa-contractor-license`](https://github.com/chaoz23/wa-contractor-license) — verify WA contractor license status and violations
- [`king-county-property-tax-appeal`](https://github.com/chaoz23/king-county-property-tax-appeal) — build a filing-ready tax appeal packet

## Requirements

- Python 3.10+ (stdlib only, no dependencies)
- Network access to `gismaps.kingcounty.gov` (four-county locator, city-limits and parcel layers; PIN verification)

## Development

From a fresh clone, the network-free tests should pass before you try a live
lookup:

```bash
git clone https://github.com/chaoz23/king-county-address-to-parcel-number.git
cd king-county-address-to-parcel-number
python3 -m unittest discover -s tests -v
```

Then smoke-test the live King County geocoder with both human-readable and
pipeline output:

```bash
python3 lookup.py "1817 Morris Ave S, Renton, WA 98055"
python3 lookup.py --pipe "1817 Morris Ave S, Renton, WA 98055"
```

## License

MIT
