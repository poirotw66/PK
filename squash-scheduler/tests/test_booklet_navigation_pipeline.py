"""The delivered booklet must contain resolved print contents and real PDF navigation."""

from __future__ import annotations

import json
import os

import pymupdf
import pytest
from test_documents import document_event

from squash_scheduler.exporters.documents import build_documents, document_qa
from squash_scheduler.exporters.pdf_navigation import validate_navigation


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("SQUASH_PDF_SMOKE") != "1", reason="Installed Edge PDF check")
def test_complete_booklet_pipeline_resolves_html_pdf_links_and_bookmarks(tmp_path):
    snapshot = document_event()
    snapshot.config["publication"]["layout_template_id"] = "reference-v2"
    folder = tmp_path / "booklet"
    manifest = build_documents(snapshot, folder)
    navigation = json.loads((folder / "navigation-map.json").read_text("utf-8"))
    html = (folder / "review.html").read_text("utf-8")
    assert navigation["entries"]
    assert manifest["qa"]["navigation"]["passed"]
    assert manifest["qa"]["navigation"]["entry_count"] == len(navigation["entries"])
    assert "navigation-map.json" in manifest["artifact_hashes"]
    assert validate_navigation(folder / "booklet.pdf", html, navigation) == navigation
    with pymupdf.open(folder / "booklet.pdf") as pdf:
        assert pdf.get_toc() == [
            [entry["level"], entry["title"], entry["page"]] for entry in navigation["entries"]
        ]
        assert all(item[3]["to"].y == 0 for item in pdf.get_toc(simple=False))
    (folder / "navigation-map.json").unlink()
    with pytest.raises(ValueError, match="navigation evidence is missing"):
        document_qa(json.loads((folder / "render-model.json").read_text("utf-8")), folder)
