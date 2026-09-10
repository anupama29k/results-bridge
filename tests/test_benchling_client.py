"""M3 contract tests: the real BenchlingClient against a mocked Benchling.

Why mocked: free/academic Benchling tenants gate the Developer Platform, so no
API key is available — verified live on the tenant. These tests stand in for a
sandbox: FakeBenchling emulates /api/v2 results endpoints (auth check, existing
results lookup, flaky 429s, validation 4xx) via httpx.MockTransport, and the
tests assert the exact contract in BenchlingClient's docstring.

These tests SKIP until post_results is implemented (so CI stays green during
M3), then arm automatically. Run just this file while implementing:

    ROBOTIC_ASSAYS_PATH=../robotic-assays-lib pytest tests/test_benchling_client.py -v
"""
import base64
import json

import httpx
import pytest

from results_bridge import benchling
from results_bridge.benchling import BenchlingApiError, BenchlingClient, build_payload

API_KEY = "test_key_abc123"
BASE = "https://mock.benchling.com/api/v2"


# --------------------------------------------------------------------------
# Fake Benchling tenant
# --------------------------------------------------------------------------
class FakeBenchling:
    """Just enough of /api/v2 to test the client contract.

    Configure per-test:
      existing_shas  — file SHAs that already have results (idempotency hits)
      fail_429_times — respond 429 this many times before succeeding
      reject_400    — always respond 400 with a validation-style body
    """

    def __init__(self, existing_shas=(), fail_429_times=0, reject_400=False):
        self.existing_shas = set(existing_shas)
        self.fail_429_times = fail_429_times
        self.reject_400 = reject_400
        self.posted = []          # every payload that reached POST /results
        self.requests = []        # (method, path) log

    def transport(self):
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))

        # -- auth: Basic base64(key + ":") — anything else is a 401
        expected = "Basic " + base64.b64encode(f"{API_KEY}:".encode()).decode()
        if request.headers.get("Authorization") != expected:
            return httpx.Response(401, json={"error": {"message": "Invalid credentials"}})

        if request.method == "GET" and request.url.path.endswith("/results"):
            sha = request.url.params.get("source_file", "")
            hits = [{"id": "res_existing"}] if sha in self.existing_shas else []
            return httpx.Response(200, json={"results": hits})

        if request.method == "POST" and request.url.path.endswith("/results"):
            if self.fail_429_times > 0:
                self.fail_429_times -= 1
                return httpx.Response(429, json={"error": {"message": "Rate limited"}})
            if self.reject_400:
                return httpx.Response(400, json={"error": {"message": "Invalid schemaId"}})
            self.posted.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"id": "res_new_001"}]})

        return httpx.Response(404, json={"error": {"message": "Not found"}})


# --------------------------------------------------------------------------
# Harness plumbing
# --------------------------------------------------------------------------
def make_client(fake: FakeBenchling, monkeypatch) -> BenchlingClient:
    monkeypatch.setattr(benchling, "BENCHLING_API_URL", BASE, raising=False)
    monkeypatch.setattr(benchling, "BENCHLING_API_KEY", API_KEY, raising=False)
    monkeypatch.setattr(BenchlingClient, "backoff", staticmethod(lambda attempt: 0.0))
    client = BenchlingClient(transport=fake.transport())
    # Constructor may have read module constants before the patch; pin them.
    client.url = BASE
    client.key = API_KEY
    return client


def sample_payload(sha="sha256:deadbeef"):
    return build_payload(
        assay_id="NGS_001_library_prep_input",
        results=[{"sample_id": "Sample-001", "value": 42.0, "unit": "ng/uL",
                  "verdict": "PASS", "reason": "within 10-100 ng/uL"}],
        file_sha=sha,
    )


def _implemented() -> bool:
    fake = FakeBenchling()
    client = BenchlingClient(transport=fake.transport())
    client.url, client.key = BASE, API_KEY
    try:
        client.post_results(sample_payload())
    except NotImplementedError:
        return False
    except Exception:
        return True  # it ran real logic and hit something — that's implemented
    return True


pytestmark = pytest.mark.skipif(
    not _implemented(),
    reason="M3 not implemented yet — write BenchlingClient.post_results, these tests arm automatically",
)


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------
def test_happy_path_posts_with_basic_auth(monkeypatch):
    fake = FakeBenchling()
    client = make_client(fake, monkeypatch)

    out = client.post_results(sample_payload())

    assert out["delivered"] is True
    assert out.get("duplicate") is False
    assert len(fake.posted) == 1
    posted = fake.posted[0]["results"][0]["fields"]
    assert posted["sample"]["value"] == "Sample-001"
    assert posted["source_file"]["value"] == "sha256:deadbeef"
    # auth was verified by the fake itself: a wrong header would have 401'd


def test_idempotency_checks_before_posting(monkeypatch):
    fake = FakeBenchling(existing_shas={"sha256:deadbeef"})
    client = make_client(fake, monkeypatch)

    out = client.post_results(sample_payload("sha256:deadbeef"))

    assert out["delivered"] is True
    assert out["duplicate"] is True
    assert fake.posted == []  # nothing POSTed
    assert ("GET", "/api/v2/results") in fake.requests


def test_retries_on_429_then_succeeds(monkeypatch):
    fake = FakeBenchling(fail_429_times=2)  # attempts 1+2 rate-limited, 3rd OK
    client = make_client(fake, monkeypatch)

    out = client.post_results(sample_payload())

    assert out["delivered"] is True
    assert len(fake.posted) == 1
    post_count = sum(1 for m, p in fake.requests if m == "POST")
    assert post_count == 3


def test_gives_up_after_three_429s(monkeypatch):
    fake = FakeBenchling(fail_429_times=99)
    client = make_client(fake, monkeypatch)

    with pytest.raises(Exception):  # exhausted retries must surface, not vanish
        client.post_results(sample_payload())
    post_count = sum(1 for m, p in fake.requests if m == "POST")
    assert post_count == 3  # exactly 3 attempts, no infinite loop


def test_4xx_raises_with_body_and_no_retry(monkeypatch):
    fake = FakeBenchling(reject_400=True)
    client = make_client(fake, monkeypatch)

    with pytest.raises(BenchlingApiError) as exc:
        client.post_results(sample_payload())

    assert exc.value.status_code == 400
    assert "Invalid schemaId" in exc.value.body
    post_count = sum(1 for m, p in fake.requests if m == "POST")
    assert post_count == 1  # 4xx is not retried
