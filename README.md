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
| `candidates` | Agent | Ranked alternatives with address, parcel number, distance from input |
| `message` | Human | Plain-English explanation of what happened |
| `suggestions` | Human | What to try next when it doesn't match |

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
| `"PIN: 7222000353"` | `use` — detects it's already a PIN |
| `"7222000353"` | `use` — bare PIN passthrough |
| `"600 Grady Way, Renton"` | `pick` — address doesn't exist, suggests 601 S Grady Way |
| `"123 Main St, Tacoma"` | `reject` — Tacoma is Pierce County |
| `"Morris Ave Renton"` | `reject` — no house number |

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
- [`king-county-property-tax-appeal`](https://github.com/chaoz23/king-county-property-tax-appeal) — build a filing-ready tax appeal packet

## Requirements

- Python 3.10+ (stdlib only, no dependencies)
- Network access to `gismaps.kingcounty.gov`

## License

MIT
