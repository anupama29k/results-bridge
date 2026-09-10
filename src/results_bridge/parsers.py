"""Adapter over the robotic-assays ingestion module (the ecosystem hub).

Resolution order for the library: $ROBOTIC_ASSAYS_PATH, then a sibling
../robotic-assays checkout — the same pattern Assayer uses.

Note: robotic-assays' run_data_ingestion imports `supabase` at module level
for its own optional persistence. We stub it if absent so parsing stays
dependency-light here. (Upstream fix tracked: make that import lazy.)
"""
import os, sys, types

from .config import ROBOTIC_ASSAYS_PATH

def _locate_library() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(here))
    candidates = [
        ROBOTIC_ASSAYS_PATH,
        os.path.join(os.path.dirname(repo_root), "robotic-assays"),
    ]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "run_data_ingestion.py")):
            return c
    raise RuntimeError(
        "robotic-assays not found. Clone github.com/anupama29k/robotic-assays "
        "next to this repo, or set ROBOTIC_ASSAYS_PATH to its checkout."
    )

_LIB = _locate_library()
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

try:  # stub optional upstream dependency
    import supabase  # noqa: F401
except ModuleNotFoundError:
    stub = types.ModuleType("supabase")
    stub.create_client = lambda *a, **k: None
    sys.modules["supabase"] = stub

from run_data_ingestion import parse_uploaded_file, detect_file_format  # noqa: E402


def parse_instrument_file(filename: str, content):
    """Parse any supported instrument file into normalized result rows.

    Returns (format, rows). Raises ValueError with the upstream error when
    parsing fails — callers surface it, never swallow it.
    """
    fmt = detect_file_format(filename, content)
    out = parse_uploaded_file(filename, content, expected_format=fmt)
    if not out.get("success"):
        raise ValueError(out.get("error") or f"parse failed for format {fmt}")
    return fmt, out["results"]
