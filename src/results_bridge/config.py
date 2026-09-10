"""Configuration via environment variables. Never commit real values."""
import os

ROBOTIC_ASSAYS_PATH = os.environ.get("ROBOTIC_ASSAYS_PATH", "")
BENCHLING_API_URL = os.environ.get("BENCHLING_API_URL", "")      # e.g. https://<tenant>.benchling.com/api/v2
BENCHLING_API_KEY = os.environ.get("BENCHLING_API_KEY", "")
DRY_RUN = not (BENCHLING_API_URL and BENCHLING_API_KEY)          # keyless => dry-run, reviewer-friendly
AUDIT_DB_PATH = os.environ.get("AUDIT_DB_PATH", "audit.db")
