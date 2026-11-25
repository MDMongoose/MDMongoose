"""Schema and volume names per bundle target (mdm/names.py)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from mdm import exports, names  # noqa: E402


def test_names_follow_the_target():
    saved = (names.CATALOG, names.CORE, names.STAGING, names.EXPORT, names.VOLUME)
    try:
        names.configure("uat_cat", "mdm_core_uat", "mdm_staging_uat", "mdm_export_uat", "files")
        assert names.nodes_table() == "uat_cat.mdm_core_uat.mdmnodes"
        assert exports.location({"kind": "csv", "name": "x"}, "uat_cat") == "/Volumes/uat_cat/mdm_export_uat/files/x.csv"
        assert exports.location({"kind": "mv", "name": "x"}, "uat_cat") == "uat_cat.mdm_export_uat.x"
        names.configure(None, "", None)  # empty values keep what is set
        assert names.CORE == "mdm_core_uat"
    finally:
        names.configure(*saved)


def test_me_reports_the_environment():
    from fastapi.testclient import TestClient

    from backend.main import app
    assert TestClient(app).get("/api/me").json()["environment"] == "local"
