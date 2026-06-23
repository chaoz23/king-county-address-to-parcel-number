# King County Address to Parcel Number

Convert a King County, WA street address to its 10-digit parcel number (PIN).

Two modes — same JSON schema, different formatting:

```bash
# Human (pretty-printed)
python3 lookup.py "1817 Morris Ave S, Renton, WA 98055"

# Agent pipeline (compact, deterministic exit codes)
python3 lookup.py --pipe "1817 Morris Ave S, Renton, WA 98055"
```

## Response contract

Every response has the same shape:

```json
{
  "action": "use",
  "pin": "7222000353",
  "matched_address": "1817 MORRIS AVE S, Renton, WA, 98055",
  "score": 100,
  "message": "Matched: 1817 MORRIS AVE S → PIN 7222000353",
  "candidates": []
}
```

| Field | For | Description |
|---|---|---|
| `action` | Agent | `use` (take the PIN), `pick` (choose from candidates), `refine` (try different input), `reject` (bad input, don't retry) |
| `pin` | Both | The answer (action=use) or best guess (action=pick), null otherwise |
| `candidates` | Agent | Ranked alternatives with address, PIN, distance from input |
| `message` | Human | Plain-English explanation of what happened |
| `suggestions` | Human | What to try next when it doesn't match |

## Exit codes

| Code | Action | Pipeline behavior |
|---|---|---|
| 0 | `use` | Consume `pin` directly |
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

## Requirements

- Python 3.10+ (stdlib only, no dependencies)
- Network access to `gismaps.kingcounty.gov`

## License

MIT
