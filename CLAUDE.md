# King County Address to Parcel Number

When the user has a King County address and needs the parcel number (PIN), run:

```bash
python3 lookup.py "<address>"
```

Read the `action` field in the JSON response:
- `use` → report the `pin` to the user
- `pick` → show the `candidates` list and ask which one they meant
- `refine` → show the `message` and `suggestions`, ask the user to try again
- `reject` → show the `message`, explain the input is invalid (wrong county, no house number)

Never silently fail — every non-`use` response has enough context for the user to correct and retry.

For agentic pipelines, use `--pipe` for compact output and check exit codes (0=use, 1=pick/refine, 2=reject).
