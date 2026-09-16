"""Read-only access to the robotic-assays protocol library (the hub).

Loads the assay definitions from the five field modules and serves them
as one merged, source-tagged list. This is the knowledge counterpart to
parsers.py (which reuses the hub's *code*; this reuses its *data*).

Import of `results_bridge.parsers` first is deliberate: it locates the
library checkout, puts it on sys.path, and stubs optional upstream
dependencies — this module rides on that setup rather than repeating it.
"""
import importlib

from . import parsers  # noqa: F401  (side effect: library on sys.path)

# module name -> attribute holding its assay list
_FIELD_MODULES = {
    "field_01_biopharma": "BIOPHARMA_ASSAYS",
    "field_01_biopharma_v2": "BIOPHARMA_ASSAYS",
    "field_03_genomics_ngs": "GENOMICS_ASSAYS",
    "field_04_drug_discovery_hts_v2": "HTS_ASSAYS",
    "field_05_core_lab_methods": "CORE_LAB_METHODS_ASSAYS",
}

_cache = None


def load_all_assays() -> list:
    """All assay entries from the library, each tagged with its `source`
    module. Later modules win on name collisions when callers de-dupe
    (v2 schemas come after v1 in _FIELD_MODULES order). Cached after
    the first call — the library is static for the process lifetime."""
    global _cache
    if _cache is None:
        merged = []
        for mod_name, attr in _FIELD_MODULES.items():
            mod = importlib.import_module(mod_name)
            for entry in getattr(mod, attr):
                e = dict(entry)
                e["source"] = mod_name
                merged.append(e)
        _cache = merged
    return _cache


def display_name(entry: dict) -> str:
    """An entry's human name, whichever key carries it."""
    return entry.get("name") or entry.get("assay_id") or "<unnamed>"
