# King County Address to Parcel Number

Convert a King County, WA street address to its 10-digit parcel number (PIN).

```bash
python3 lookup.py "1817 Morris Ave S, Renton, WA 98055"
```

```json
{
  "status": "match",
  "pin": "7222000353",
  "major": "722200",
  "minor": "0353",
  "matched_address": "1817 MORRIS AVE S, Renton, WA, 98055",
  "score": 100
}
```

## Error handling

Instead of failing silently, the tool tells you what's wrong and how to fix it:

| Input | Response |
|---|---|
| Valid KC address | `status: match` with PIN |
| Typo in street name | Still matches (geocoder is fuzzy) with the corrected address |
| Missing directional (S, N, NE) | Still matches, shows the full canonical address |
| Address outside King County | Tells you which county it's actually in |
| No house number | Asks for a full street address |
| Gibberish / no match | Suggestions for what to check |
| Low confidence match | Shows the best guess and asks you to verify |

## Requirements

- Python 3.10+ (stdlib only, no dependencies)
- Network access to `gismaps.kingcounty.gov`

## How it works

Queries the [King County ArcGIS ParcelAddress geocoder](https://gismaps.kingcounty.gov/arcgis/rest/services/Address/KingCo_ParcelAddress_locator/GeocodeServer), which resolves addresses to the official 10-digit parcel identification number (PIN = Major 6 digits + Minor 4 digits).

## As a Claude Code skill

Drop this repo in your Claude Code skills directory. It triggers when someone asks for a King County parcel number, PIN lookup, or property identification.

## License

MIT
