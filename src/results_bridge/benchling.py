"""Benchling delivery layer.

Two implementations behind one interface:

- DryRunClient — always available; renders the exact payload the real client
  would POST and returns it, so the service is fully reviewable with no keys.
- BenchlingClient — the real write-back against /api/v2 results endpoints.
  *** M3: implemented by Anu. *** The interface, auth pattern and idempotency
  contract are specified below; the method bodies are hers to write and to
  defend in interviews.

Testing without a live tenant: free/academic Benchling tenants do not expose
API keys (Developer Platform access is enterprise-gated), so the test suite
runs the real client against a *mocked* Benchling — tests/test_benchling_client.py
spins up a fake /api/v2 via httpx.MockTransport and asserts the full contract
(auth header, idempotency pre-check, 429 retry, 4xx raise). That is why
BenchlingClient accepts an optional `transport`: tests inject the fake,
production passes nothing and gets the real network.
"""
from .config import BENCHLING_API_URL, BENCHLING_API_KEY, DRY_RUN

RESULT_SCHEMA_ID = "assaysch_resultsbridge"  # created once in the sandbox tenant


def build_payload(assay_id: str, results: list[dict], file_sha: str) -> dict:
    """Benchling-shaped results payload. file_sha doubles as idempotency key."""
    return {
        "results": [{
            "schemaId": RESULT_SCHEMA_ID,
            "fields": {
                "assay":         {"value": assay_id},
                "sample":        {"value": r["sample_id"]},
                "concentration": {"value": r["value"]},
                "unit":          {"value": r["unit"]},
                "verdict":       {"value": r["verdict"]},
                "reason":        {"value": r["reason"]},
                "source_file":   {"value": file_sha},
            },
        } for r in results]
    }


class DryRunClient:
    mode = "dry-run"

    def post_results(self, payload: dict) -> dict:
        return {"delivered": False, "mode": self.mode, "payload": payload}


class BenchlingApiError(Exception):
    """Raised on a 4xx from Benchling. Carries status + body for the audit log."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Benchling returned {status_code}: {body}")


class BenchlingClient:
    """M3 (Anu): real write-back.

    Contract to implement in post_results():
      * auth: HTTP Basic — username = api_key, password = "" (i.e. the header
        Authorization: Basic base64(api_key + ":")). With httpx that is simply
        auth=(self.key, "").
      * client: build httpx.Client(transport=self.transport) when
        self.transport is not None, else a plain httpx.Client() — this is what
        lets the tests substitute a fake Benchling.
      * idempotency FIRST: GET {url}/results?source_file={file_sha} — the
        file_sha is in payload["results"][0]["fields"]["source_file"]["value"].
        If the response's "results" list is non-empty, return
        {"delivered": True, "duplicate": True, "mode": self.mode} WITHOUT
        posting.
      * then POST {url}/results with the payload as JSON.
      * retries: up to 3 total attempts when the response is 429 or 5xx, with
        exponential backoff (self.backoff(attempt) — patchable to 0 in tests).
      * on 4xx (other than 429): raise BenchlingApiError(status, body) so the
        caller can audit the failure. Do not retry 4xx.
      * on success (2xx): return {"delivered": True, "duplicate": False,
        "mode": self.mode, "response": <parsed json>}.
    """
    mode = "live"

    def __init__(self, transport=None):
        self.url = BENCHLING_API_URL.rstrip("/")
        self.key = BENCHLING_API_KEY
        self.transport = transport  # tests pass httpx.MockTransport; prod passes None

    @staticmethod
    def backoff(attempt: int) -> float:
        """Seconds to sleep before retry `attempt` (1-based). Patched to 0 in tests."""
        return 0.5 * (2 ** (attempt - 1))

    def post_results(self, payload: dict) -> dict:
        raise NotImplementedError("M3: Anu implements the live Benchling write-back — see class docstring")


def get_client():
    return DryRunClient() if DRY_RUN else BenchlingClient()
