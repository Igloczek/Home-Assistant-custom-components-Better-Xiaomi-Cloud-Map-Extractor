"""The config flow's steps, one module per stage; `config_flow.py` puts them together.

- `base`: the state the steps share
- `login`: choosing how to log in, password (captcha, two-factor) and QR code login
- `reauth`: what the user is told when the stored session stopped working
- `original`: importing from the original integration and from YAML
- `vacuum`: device list, choosing the vacuum, confirming its settings and creating the entry
- `errors`: which form error a failure shows
"""
