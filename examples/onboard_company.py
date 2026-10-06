"""Onboard a company end to end: create the case, run the checks, act on the decision.

    INFINIHASH_KYC_KEY=... python examples/onboard_company.py
"""
from infinihash_kyc import KYC, is_unscreened

kyc = KYC()

case = kyc.submissions.create(
    entity_type="private_corp",
    entity_name="Acme Widgets Ltd",
    country="GB",
    registration_number="01234567",
    address_line1="1 High Street",
    city="London",
    postal_code="EC1A 1AA",
)
print("created", case["id"], "required checks:", ", ".join(case["requiredChecks"]))

kyc.submissions.run(case["id"])
result = kyc.submissions.wait(case["id"], timeout=300)

summary = result.get("summary") or {}
if is_unscreened(result):
    print("NOT SCREENED -- manual review required:", summary["error"])
else:
    print(f"decision: {summary.get('action')}  (status {result['status']}, "
          f"risk {summary.get('riskScore')} / {summary.get('riskTier')})")
    for c in result["checks"]:
        print(f"  {c['checkType']:<24} {c['status']}")
