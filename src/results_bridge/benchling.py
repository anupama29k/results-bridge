"""Benchling delivery layer.

Two implementations behind one interface:

- DryRunClient — always available; renders the exact payload the real client
  would POST and returns it, so the service is fully reviewable with no keys.
- BenchlingClient — the real write-back against /api/v2 results endpoints.
  *** M3: implemented by Anu. *** The interface, auth pattern and idempotency
  contract are specified below; the method bodies are hers to write and to
  defend in interviews.
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


class BenchlingClient:
    """M3 (Anu): real write-back.

    Contract to implement:
      * auth: Authorization: Basic base64(api_key + ":") against BENCHLING_API_URL
      * POST {url}/results with the payload from build_payload()
      * idempotency: before POSTing, query results filtered by source_file ==
        file_sha; if present, return delivered=True, duplicate=True, skip POST
      * retries: up to 3 attempts on 429/5xx with exponential backoff
      * on 4xx: raise with the response body so the caller can audit the failure
    """
    mode = "live"

    def __init__(self):
        self.url = BENCHLING_API_URL.rstrip("/")
        self.key = BENCHLING_API_KEY

    def post_results(self, payload: dict) -> dict:
        raise NotImplementedError("M3: Anu implements the live Benchling write-back — see class docstring")


def get_client():
    return DryRunClient() if DRY_RUN else BenchlingClient()
