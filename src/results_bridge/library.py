"""Read-only access to the robotic-assays protocol library (the hub).

Discovers the field_*.py modules present in the library checkout and
merges their assay lists into one source-tagged collection. Discovery
(not a hardcoded module list) means the hub can add or remove field
files without breaking this consumer — the lesson of a real bug: a
hardcoded list broke the day the hub deleted its v1 biopharma file.

Import of `results_bridge.parsers` first is deliberate: it locates the
library checkout, puts it on sys.path, and stubs optional upstream
dependencies — this module rides on that setup rather than repeating it.
"""
import glob
import importlib
import os

from . import parsers  # noqa: F401  (side effect: library on sys.path)
from .parsers import _LIB

_cache = None


def load_all_assays() -> list:
    """All assay entries from every field_*.py module in the library,
    each tagged with its `source` module. Modules load in sorted filename
    order, so v2 files come after v1 — callers that keep the last match
    get the newest schema. Cached after the first call."""
    global _cache
    if _cache is None:
        merged = []
        for path in sorted(glob.glob(os.path.join(_LIB, "field_*.py"))):
            mod_name = os.path.splitext(os.path.basename(path))[0]
            mod = importlib.import_module(mod_name)
            for attr in dir(mod):
                if attr.endswith("_ASSAYS") and isinstance(getattr(mod, attr), list):
                    for entry in getattr(mod, attr):
                        e = dict(entry)
                        e["source"] = mod_name
                        merged.append(e)
        _cache = merged
    return _cache


def display_name(entry: dict) -> str:
    """An entry's human name, whichever key carries it."""
    return entry.get("name") or entry.get("assay_id") or "<unnamed>"
