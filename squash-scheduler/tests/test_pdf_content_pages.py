"""Document QA rejects pagination that leaks a footer or draft stamp onto another page."""

from __future__ import annotations

import os

import pymupdf
import pytest
from jinja2 import Environment

from squash_scheduler.exporters.documents import _require_pdf_body_pages, render_pdf
from squash_scheduler.exporters.reference_layout import TEMPLATE


def test_body_content_is_distinct_from_page_counter_and_draft_stamp():
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 50), "Synthetic tournament body")
    page.insert_text((50, 800), "1 / 1")
    _require_pdf_body_pages(document)
    document.close()


@pytest.mark.parametrize("text", ["", "2 / 2", "DRAFT Synthetic tournament\n2 / 2"])
def test_empty_or_stamp_only_overflow_page_fails(text):
    document = pymupdf.open()
    page = document.new_page()
    if text:
        page.insert_text((50, 50), text)
    with pytest.raises(ValueError, match="page 1 has no body content"):
        _require_pdf_body_pages(document)
    document.close()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SQUASH_PDF_SMOKE") != "1", reason="Real installed-browser PDF pagination"
)
def test_graph_alias_notes_and_stamp_share_one_landscape_page(tmp_path):
    html = (
        Environment(autoescape=True)
        .from_string(TEMPLATE)
        .render(
            name="Synthetic pagination",
            draft=True,
            classic=True,
            publication={},
            graphs=[
                {
                    "title": "Synthetic bracket",
                    "stage_type": "elimination",
                    "svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 530"><path d="M100 450V200H500V100M900 450V200H500" stroke="black" fill="none"/><text x="100" y="490">Reference 1</text></svg>',
                    "notes": [
                        f"Reference {index}: Synthetic Player {index}" for index in range(1, 5)
                    ],
                }
            ],
            schedule_pages=[],
            rule_summaries=[],
            font_css="",
            kai_fonts="serif",
        )
    )
    path = tmp_path / "graph.pdf"
    render_pdf(html, path)
    with pymupdf.open(path) as document:
        assert len(document) == 2  # One graph page and the separate version-record page.
        text = document[0].get_text()
        assert "Synthetic Player 4" in text
        _require_pdf_body_pages(document)
