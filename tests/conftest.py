import os, sys, pathlib
os.environ.setdefault("ROBOTIC_ASSAYS_PATH", os.environ.get("ROBOTIC_ASSAYS_PATH", ""))
os.environ["AUDIT_DB_PATH"] = str(pathlib.Path(__file__).parent / "test_audit.db")
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

@pytest.fixture
def qubit_csv() -> bytes:
    return (FIXTURES / "test_qubit.csv").read_bytes()

@pytest.fixture(autouse=True)
def clean_audit():
    db = pathlib.Path(os.environ["AUDIT_DB_PATH"])
    if db.exists(): db.unlink()
    yield
    if db.exists(): db.unlink()
