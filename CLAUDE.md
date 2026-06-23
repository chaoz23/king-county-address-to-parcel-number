# King County Parcel Lookup

When the user asks for a King County parcel number, PIN, or wants to identify a KC property by address, run:

```bash
python3 lookup.py "<address>"
```

The output is JSON. On `status: match`, report the PIN. On any other status, show the user the message and suggestions so they can correct their input and try again — don't treat errors as terminal.
