# King County Address to Parcel Number

When the user has a King County address and needs the parcel number (PIN), run:

```bash
python3 lookup.py "<address>"
```

The output is JSON. On `status: match`, report the PIN. On any other status, show the user the message and suggestions so they can correct their input and try again — don't treat errors as terminal.
