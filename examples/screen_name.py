"""Screen one name against the sanctions lists.

    INFINIHASH_KYC_KEY=... python examples/screen_name.py "Vladimir Putin" RU
"""
import sys

from infinihash_kyc import KYC, ScreeningUnavailable

name = sys.argv[1] if len(sys.argv) > 1 else "Vladimir Putin"
country = sys.argv[2] if len(sys.argv) > 2 else None

kyc = KYC()
try:
    r = kyc.screen(name, country=country)
except ScreeningUnavailable as e:
    # Never treat this as clear: the lists were not searched.
    sys.exit(f"NOT SCREENED -- retry later ({e})")

print(f"{r['name']}: {r['status']} ({r['totalHits']} hit(s))")
for m in r["matches"][:5]:
    print(f"  - {m.get('list')}: {m.get('name')} (score {m.get('score')})")
