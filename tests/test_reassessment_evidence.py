"""Keep the checked-in experiment evidence verifiable across Git platforms."""

import csv
import hashlib
import io
import json
from pathlib import Path


def test_saved_resource_sweep_has_valid_portable_provenance():
    root = Path(__file__).resolve().parents[1]
    data = (root / "experiments/reassessment_results.csv").read_bytes()
    provenance = json.loads((root / "experiments/reassessment_results.provenance.json").read_text())
    # Normalize a Windows checkout's optional CRLF conversion before checking
    # the export's canonical bytes, without relying on the exporter itself.
    canonical_csv = data.replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical_csv).hexdigest() == provenance["csv_sha256"]
    rows = list(csv.DictReader(io.StringIO(canonical_csv.decode("utf-8"))))
    assert len(rows) == provenance["rows"] == 540
    assert all(row["reference_verified"] == "True" and row["unfinished"] == "0" for row in rows)
    for name, expected in provenance["source_sha256"].items():
        canonical_source = (root / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(canonical_source).hexdigest() == expected, name
