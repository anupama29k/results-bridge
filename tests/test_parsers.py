from results_bridge.parsers import parse_instrument_file

def test_qubit_parses_to_rows(qubit_csv):
    fmt, rows = parse_instrument_file("test_qubit.csv", qubit_csv.decode())
    assert fmt == "qubit_csv"
    assert len(rows) >= 4
    first = rows[0]
    assert first["sample_id"] == "Sample-001"
    assert first["measurements"]["concentration_ng_per_uL"] == 12.4

def test_unparseable_raises():
    import pytest
    with pytest.raises(Exception):
        parse_instrument_file("junk.csv", "not,a,real\ninstrument,file,1")
