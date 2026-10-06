"""Infinihash KYC Python SDK.

Thin `requests` wrapper around the Infinihash KYC API (https://kyc-api.infinihash.com).
Every call returns the JSON the server sent; errors surface as exceptions.

Quick start
-----------
    from infinihash_kyc import KYC

    kyc = KYC(api_key="...")                    # or set INFINIHASH_KYC_KEY

    # Onboard a company: create the case, run the checks, wait for the decision
    case = kyc.submissions.create(
        entity_type="private_corp",
        entity_name="Acme Widgets Ltd",
        country="GB",
        registration_number="01234567",
    )
    kyc.submissions.run(case["id"])
    result = kyc.submissions.wait(case["id"])
    print(result["status"], result["summary"]["action"])

    # Screen one name against the sanctions lists
    hit = kyc.screen("Vladimir Putin", country="RU")
    print(hit["status"], hit["totalHits"])

Fail-closed by design
---------------------
* `screen()` raises ScreeningUnavailable when the server says it did not screen
  (HTTP 503 or `screened: false`). It never returns a result you could mistake
  for "clear".
* A case that could not be screened finishes in status `review` with
  `summary.error` set. `wait()` returns it; `is_unscreened()` tells you.

Errors
------
Non-2xx responses raise KYCError(status, message, body).
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests

DEFAULT_BASE_URL = "https://kyc-api.infinihash.com"
DEFAULT_TIMEOUT = 30

ENTITY_TYPES = [
    "individual", "listed_public", "wholly_owned_sub", "pension_erisa", "governmental",
    "nonprofit", "trust", "spv", "complex", "private_corp",
]
RISK_TIERS = ["low", "medium", "high"]
TERMINAL_STATUSES = {"complete", "review", "failed"}

# snake_case keyword -> API field. Anything not listed is rejected locally,
# because the server silently drops unknown fields.
_FIELDS = {
    "lei": "lei", "crd": "crd", "jurisdiction": "jurisdiction", "ticker": "ticker",
    "country": "country", "email": "email", "naics_code": "naicsCode",
    "business_description": "businessDescription", "registration_number": "registrationNumber",
    "incorporation_date": "incorporationDate", "principal_place_of_business": "principalPlaceOfBusiness",
    "dob": "dob", "nationality": "nationality", "smart_import": "smartImport", "source_doc": "sourceDoc",
    "address_line1": "addressLine1", "address_line2": "addressLine2", "city": "city",
    "region": "region", "postal_code": "postalCode",
    "officers": "officers", "owners": "owners", "signatory": "signatory",
}


class KYCError(Exception):
    """Any non-2xx response. Attributes: status (int), body (dict | str | None)."""

    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body


class ScreeningUnavailable(KYCError):
    """The server did not screen the name. Never treat this as a clear result."""


class _Submissions:
    def __init__(self, client: "KYC"):
        self._c = client

    def create(self, entity_type: str, entity_name: str, risk_tier: str = "low", **fields: Any) -> Dict[str, Any]:
        """POST /api/kyc. Returns {id, status, requiredChecks, _links, ...}."""
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of {ENTITY_TYPES}")
        if risk_tier not in RISK_TIERS:
            raise ValueError(f"risk_tier must be one of {RISK_TIERS}")
        unknown = sorted(set(fields) - set(_FIELDS))
        if unknown:
            raise ValueError(f"unknown field(s) {unknown}; the API would silently drop them")
        body = {"entityType": entity_type, "entityName": entity_name, "riskTier": risk_tier}
        body.update({_FIELDS[k]: v for k, v in fields.items() if v is not None})
        return self._c._request("POST", "/api/kyc", json=body)

    def run(self, submission_id: str) -> Dict[str, Any]:
        """POST /api/kyc/{id}/run. Starts the checks (asynchronous)."""
        return self._c._request("POST", f"/api/kyc/{submission_id}/run")

    def get(self, submission_id: str) -> Dict[str, Any]:
        """GET /api/kyc/{id}. Status, checks and (once finished) the summary."""
        return self._c._request("GET", f"/api/kyc/{submission_id}")

    def list(self) -> List[Dict[str, Any]]:
        """GET /api/kyc. Your submissions, newest first."""
        return self._c._request("GET", "/api/kyc")

    def wait(self, submission_id: str, timeout: float = 300, poll: float = 3) -> Dict[str, Any]:
        """Poll until the case reaches complete / review / failed, or raise TimeoutError."""
        deadline = time.monotonic() + timeout
        while True:
            sub = self.get(submission_id)
            if sub.get("status") in TERMINAL_STATUSES:
                return sub
            if time.monotonic() >= deadline:
                raise TimeoutError(f"submission {submission_id} still {sub.get('status')!r} after {timeout}s")
            time.sleep(poll)

    def export_pdf(self, submission_id: str) -> bytes:
        """GET /api/kyc/{id}/export/pdf. The case report as PDF bytes."""
        return self._c._request("GET", f"/api/kyc/{submission_id}/export/pdf", raw=True)


def is_unscreened(submission: Dict[str, Any]) -> bool:
    """True when the case finished without being fully screened (fail-closed review).

    Either no checklist ran (``summary.error``), or a screen could not be
    performed (``summary.screeningErrors``, e.g. a sanctions list that was not
    searched). Such a case is never ``complete``; treat it as unscreened, not clear.
    """
    summary = (submission or {}).get("summary") or {}
    return bool(summary.get("error") or summary.get("screeningErrors"))


def create_sandbox_key(email: str, base_url: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Self-serve sandbox key (``kyc_sbx_...``), returned once. No account needed.

    Sandbox keys run the real case lifecycle with deterministic results and are
    never billed. Magic entity names: SANCTIONED, UNSCREENED, PEP, ADVERSE,
    LIVENESS_FAIL; any other name passes. See ``GET /api/sandbox``.
    """
    url = (base_url or os.environ.get("INFINIHASH_KYC_URL") or DEFAULT_BASE_URL).rstrip("/")
    r = requests.post(url + "/api/sandbox/keys", json={"email": email}, timeout=timeout,
                      headers={"User-Agent": "infinihash-kyc-python/0.2.0"})
    if r.status_code != 201:
        try:
            msg = r.json().get("error")
        except ValueError:
            msg = r.text[:200]
        raise KYCError(r.status_code, msg or r.reason)
    return r.json()["apiKey"]


class KYC:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT, session: Optional[requests.Session] = None):
        self.api_key = api_key or os.environ.get("INFINIHASH_KYC_KEY")
        if not self.api_key:
            raise ValueError("api_key is required (or set INFINIHASH_KYC_KEY)")
        self.base_url = (base_url or os.environ.get("INFINIHASH_KYC_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._s = session or requests.Session()
        self._s.headers.update({"X-API-Key": self.api_key, "User-Agent": "infinihash-kyc-python/0.2.0"})
        self.submissions = _Submissions(self)

    def screen(self, name: str, country: Optional[str] = None, dob: Optional[str] = None) -> Dict[str, Any]:
        """POST /api/kyc/screen. Raises ScreeningUnavailable if the name was not screened."""
        body = {"name": name, **({"country": country} if country else {}), **({"dob": dob} if dob else {})}
        try:
            out = self._request("POST", "/api/kyc/screen", json=body)
        except KYCError as e:
            if e.status == 503:
                raise ScreeningUnavailable(e.status, "screening unavailable -- NOT a clear result", e.body) from None
            raise
        if out.get("screened") is False or out.get("status") == "ERROR":
            raise ScreeningUnavailable(200, "server reports the name was not screened", out)
        return out

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health", auth=False)

    def _request(self, method: str, path: str, json: Any = None, raw: bool = False, auth: bool = True):
        headers = None if auth else {"X-API-Key": None}
        r = self._s.request(method, self.base_url + path, json=json, timeout=self.timeout, headers=headers)
        if not 200 <= r.status_code < 300:
            try:
                body = r.json()
                msg = body.get("error") if isinstance(body, dict) else str(body)
            except ValueError:
                body, msg = r.text, r.text[:200]
            raise KYCError(r.status_code, msg or r.reason, body)
        if raw:
            return r.content
        return r.json()
