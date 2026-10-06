# infinihash-kyc

Python SDK for the [Infinihash KYC API](https://kyc.infinihash.com): KYC and KYB cases,
sanctions screening, and case reports.

```bash
pip install "git+https://github.com/Infinihash/kyc-python@v0.1.0"
export INFINIHASH_KYC_KEY=...        # your API key
```

## Screen a name

```python
from infinihash_kyc import KYC, ScreeningUnavailable

kyc = KYC()
try:
    r = kyc.screen("Vladimir Putin", country="RU")
    print(r["status"], r["totalHits"])        # HIT 6
except ScreeningUnavailable:
    ...                                       # lists were NOT searched -- never treat as clear
```

## Onboard a company

```python
from infinihash_kyc import KYC, is_unscreened

kyc = KYC()
case = kyc.submissions.create(
    entity_type="private_corp",
    entity_name="Acme Widgets Ltd",
    country="GB",
    registration_number="01234567",
)
kyc.submissions.run(case["id"])
result = kyc.submissions.wait(case["id"])      # complete | review | failed

if is_unscreened(result):
    print("manual review:", result["summary"]["error"])
else:
    print(result["summary"]["action"])         # approve | enhanced_review | manual_review | block_report
pdf = kyc.submissions.export_pdf(case["id"])
```

Runnable versions are in [`examples/`](examples/).

## Fail-closed by design

- **`screen()` never returns an unscreened result.** It raises `ScreeningUnavailable`
  on HTTP 503 or when the server reports `screened: false`.
- **A case the API could not screen ends in `review` with `summary.error` set.** It
  never ends in `complete`. `is_unscreened(case)` tells you which happened.
- **`submissions.create()` rejects unknown fields locally,** because the API would
  silently drop them. It also validates `entity_type` and `risk_tier` against the
  same lists the API uses.

## Reference

| Method | API |
|---|---|
| `submissions.create(entity_type, entity_name, risk_tier="low", **fields)` | `POST /api/kyc` |
| `submissions.run(id)` | `POST /api/kyc/{id}/run` |
| `submissions.get(id)` / `wait(id, timeout=300)` | `GET /api/kyc/{id}` |
| `submissions.list()` | `GET /api/kyc` |
| `submissions.export_pdf(id)` | `GET /api/kyc/{id}/export/pdf` |
| `screen(name, country=None, dob=None)` | `POST /api/kyc/screen` |

Entity types: `individual, listed_public, wholly_owned_sub, pension_erisa, governmental,
nonprofit, trust, spv, complex, private_corp`. The full schema is the
[OpenAPI spec](https://kyc-api.infinihash.com/api/v1/openapi.json).

Errors: any non-2xx raises `KYCError(status, message, body)`.

## License

MIT
