# King County Address to Parcel Number

When the user has a King County address and needs the parcel number (PIN), run:

```bash
python3 lookup.py "<address>"
```

Read the `action` field in the JSON response:
- `use` → report `parcel_number` (and `parcel_id`, e.g. `king:7222000353`); say which **jurisdiction** it is in (`jurisdiction.city`, or unincorporated King County) — that, not the mailing city, is who issues permits
- `pick` → show the `candidates` list and ask which one they meant; if `address_resolution.parcel_basis` is `nearby-parcel`/`parcel-polygon`, say the house number was interpolated and the parcel is the one under that point
- `refine` → show the `message` and `suggestions`, ask the user to try again
- `reject` → show the `message`, explain the input is invalid (wrong county, no house number)

Never silently fail — every non-`use` response has enough context for the user to correct and retry.

For agentic pipelines, use `--pipe` for compact output and check exit codes (0=use, 1=pick/refine, 2=reject).

Hosted: `https://parcels-api.secondlandings.ai` (`/api/parcel?q=`, MCP at `/mcp`). For what's on file at the parcel, use the sibling tool king-county-permit-status (`permits-api.secondlandings.ai`).
