# Tushare optional source

Tushare is an optional, credentialed source. The installable Skill does not
ship a token and does not require Tushare for the free baseline.

Opt in for a local diagnostic by setting the process environment variable:

```bash
export TUSHARE_TOKEN='your-token'

# optional diagnostic; emits only redacted JSON
python skill/a-share-research/scripts/probe_tushare_optional.py
```

CI should provide the same variable through its secret store. Do not place a
token in source files, request JSON, command arguments, logs, result JSON,
fixtures, or evidence artifacts. The optional probe reports only whether the
credential is configured and whether the provider accepted the request. It
does not enable `market_screen_research`, and a successful probe does not prove
PIT, revision, BSE factor, or release qualification.

The default probe is the `daily` endpoint, which Tushare documents as available
from the 120-point tier. Set `TUSHARE_API_NAME=daily_basic` to explicitly test
the factor endpoint; that endpoint requires the higher permission tier. Other
supported probe names are `new_share` and `stock_basic`.

The optional operation reads `TUSHARE_API_URL` only when an HTTPS endpoint is
needed for controlled testing; the default is `https://api.tushare.pro`.
