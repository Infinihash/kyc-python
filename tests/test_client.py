"""SDK behaviour against a local stub that speaks the API's documented shapes
(KYC-API openapi.json components: CreateSubmission, SubmissionCreated,
RunStarted, Submission, ScreenResult). No network."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from infinihash_kyc import KYC, KYCError, ScreeningUnavailable, create_sandbox_key, is_unscreened

STATE = {}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        STATE.setdefault("keys", []).append(self.headers.get("X-API-Key"))
        if self.path == "/api/kyc/sub_1":
            STATE["polls"] = STATE.get("polls", 0) + 1
            if STATE["polls"] < 3:
                return self._send(200, {"id": "sub_1", "status": "running", "summary": None, "checks": []})
            return self._send(200, STATE.get("final") or {
                "id": "sub_1", "status": "complete", "riskScore": 12,
                "summary": {"action": "approve", "riskScore": 12, "riskTier": "low"},
                "checks": [{"checkType": "sanctions_ofac", "status": "pass"}]})
        if self.path == "/api/kyc":
            return self._send(200, [{"id": "sub_1", "status": "complete"}])
        if self.path == "/api/kyc/sub_1/export/pdf":
            return self._send(200, b"%PDF-1.7 stub", "application/pdf")
        if self.path == "/api/kyc/missing":
            return self._send(404, {"error": "Submission not found"})
        return self._send(404, {"error": "no route"})

    def do_POST(self):
        STATE.setdefault("keys", []).append(self.headers.get("X-API-Key"))
        b = self._body()
        if self.path == "/api/kyc":
            STATE["created"] = b
            if b.get("entityName", "").strip() == "x":
                return self._send(400, {"error": "entityName must be 2–300 characters"})
            return self._send(201, {"id": "sub_1", "status": "pending", "entityType": b["entityType"],
                                    "requiredChecks": ["sanctions_ofac", "pep"], "_links": {}})
        if self.path == "/api/sandbox/keys":
            if "@" not in b.get("email", ""):
                return self._send(400, {"error": "a valid email is required"})
            return self._send(201, {"apiKey": "kyc_sbx_" + "0" * 48, "tier": "sandbox"})
        if self.path == "/api/kyc/sub_1/run":
            return self._send(200, {"message": "Checks started", "submissionId": "sub_1"})
        if self.path == "/api/kyc/screen":
            if b["name"] == "down":
                return self._send(503, {"name": "down", "status": "ERROR", "screened": False, "matches": []})
            if b["name"] == "soft-fail":
                return self._send(200, {"name": "soft-fail", "status": "PARTIAL", "screened": False, "matches": []})
            hit = b["name"].lower().startswith("vladimir")
            return self._send(200, {"name": b["name"], "status": "HIT" if hit else "CLEAR", "screened": True,
                                    "totalHits": 1 if hit else 0,
                                    "matches": [{"list": "OFAC SDN", "name": "PUTIN, Vladimir", "score": 0.97}] if hit else []})
        return self._send(404, {"error": "no route"})


@pytest.fixture
def kyc():
    STATE.clear()
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield KYC(api_key="test-key", base_url=f"http://127.0.0.1:{srv.server_port}")
    srv.shutdown()


def test_create_maps_snake_case_fields_and_sends_the_key(kyc):
    out = kyc.submissions.create("private_corp", "Acme Widgets Ltd", country="GB",
                                 registration_number="01234567", postal_code="EC1A 1AA")
    assert out["id"] == "sub_1"
    assert STATE["created"] == {"entityType": "private_corp", "entityName": "Acme Widgets Ltd", "riskTier": "low",
                                "country": "GB", "registrationNumber": "01234567", "postalCode": "EC1A 1AA"}
    assert STATE["keys"] == ["test-key"]


def test_unknown_fields_and_bad_enums_are_rejected_before_the_request(kyc):
    with pytest.raises(ValueError, match="silently drop"):
        kyc.submissions.create("private_corp", "Acme", company_number="1")
    with pytest.raises(ValueError, match="entity_type"):
        kyc.submissions.create("business", "Acme")
    with pytest.raises(ValueError, match="risk_tier"):
        kyc.submissions.create("individual", "Jane Doe", risk_tier="extreme")
    assert "created" not in STATE


def test_server_validation_error_is_a_kycerror_with_the_message(kyc):
    with pytest.raises(KYCError) as e:
        kyc.submissions.create("individual", "x")
    assert e.value.status == 400 and "2–300" in str(e.value)


def test_run_then_wait_returns_the_finished_case(kyc):
    kyc.submissions.run("sub_1")
    res = kyc.submissions.wait("sub_1", poll=0.01)
    assert res["status"] == "complete" and res["summary"]["action"] == "approve"
    assert STATE["polls"] == 3
    assert not is_unscreened(res)


def test_wait_returns_a_fail_closed_review_and_flags_it(kyc):
    STATE["final"] = {"id": "sub_1", "status": "review",
                      "summary": {"action": "manual_review", "error": "checklist_unresolved"}, "checks": []}
    res = kyc.submissions.wait("sub_1", poll=0.01)
    assert res["status"] == "review" and is_unscreened(res)


def test_wait_times_out(kyc):
    with pytest.raises(TimeoutError):
        kyc.submissions.wait("sub_1", timeout=0, poll=0)


def test_screen_hit_and_clear(kyc):
    assert kyc.screen("Vladimir Putin", country="RU")["status"] == "HIT"
    assert kyc.screen("Jane Q Public")["status"] == "CLEAR"


@pytest.mark.parametrize("name", ["down", "soft-fail"])
def test_screen_never_returns_an_unscreened_result(kyc, name):
    with pytest.raises(ScreeningUnavailable):
        kyc.screen(name)


def test_not_found_list_and_pdf(kyc):
    with pytest.raises(KYCError) as e:
        kyc.submissions.get("missing")
    assert e.value.status == 404
    assert kyc.submissions.list()[0]["id"] == "sub_1"
    assert kyc.submissions.export_pdf("sub_1").startswith(b"%PDF")


def test_api_key_is_required(monkeypatch):
    monkeypatch.delenv("INFINIHASH_KYC_KEY", raising=False)
    with pytest.raises(ValueError):
        KYC()


def test_a_sanctions_screening_error_counts_as_unscreened(kyc):
    # KYC-API 2026-10-06: a sanctions list that was NOT SEARCHED routes the case
    # to review with screeningErrors=['sanctions'] but no summary.error.
    STATE["final"] = {"id": "sub_1", "status": "review",
                      "summary": {"action": "manual_review", "screeningErrors": ["sanctions"]}, "checks": []}
    assert is_unscreened(kyc.submissions.wait("sub_1", poll=0.01))


def test_create_sandbox_key(kyc):
    assert create_sandbox_key("dev@example.com", base_url=kyc.base_url).startswith("kyc_sbx_")
    with pytest.raises(KYCError) as e:
        create_sandbox_key("nope", base_url=kyc.base_url)
    assert e.value.status == 400
