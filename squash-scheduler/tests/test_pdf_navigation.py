"""Contents pages must reflect actual PDF links and survive multi-page printing."""

from __future__ import annotations

import os

import pymupdf
import pytest

from squash_scheduler.exporters.pdf_navigation import (
    add_pdf_bookmarks,
    extract_navigation,
    fill_toc_pages,
    validate_navigation,
)
from squash_scheduler.exporters.reference_layout import render

HTML = (
    '<a href="#chapter" data-toc-target="chapter"><span data-toc-title>Chapter</span>'
    '<span data-toc-page>00</span></a><section id="chapter">Chapter</section>'
)


def linked_pdf(path, *, printed="2", linked_page=None):
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.xref_set_key(
            pdf.pdf_catalog(),
            "Names",
            f"<< /Dests << /Names [(chapter) [{pdf.page_xref(1)} 0 R /XYZ 0 842 0]] >> >>",
        )
        pdf[0].insert_text((500, 100), printed, fontsize=12)
        link = {
            "kind": pymupdf.LINK_NAMED,
            "from": pymupdf.Rect(50, 85, 520, 105),
            "nameddest": "chapter",
        }
        if linked_page is not None:
            link.update(kind=pymupdf.LINK_GOTO, page=linked_page, to=pymupdf.Point(0, 0))
        pdf[0].insert_link(link)
        pdf.save(path)


def test_actual_destinations_become_printed_numbers_bookmarks_and_top_of_page_links(tmp_path):
    path = tmp_path / "booklet.pdf"
    linked_pdf(path)
    navigation = extract_navigation(path, HTML)
    assert navigation["entries"][0]["page"] == 2
    filled = fill_toc_pages(HTML, navigation)
    assert "<span data-toc-page>2</span>" in filled
    assert validate_navigation(path, filled, navigation) == navigation
    add_pdf_bookmarks(path, navigation)
    assert extract_navigation(path, filled) == navigation
    assert validate_navigation(path, filled, navigation) == navigation
    with pymupdf.open(path) as pdf:
        assert pdf.get_toc() == [[1, "Chapter", 2]]
        link = pdf[0].get_links()[0]
        assert link["kind"] == pymupdf.LINK_GOTO
        assert link["page"] == 1
        assert link["to"].y == 0
        assert pdf.get_toc(simple=False)[0][3]["to"].y == 0


def test_wrong_or_missing_bookmarks_are_rejected_after_postprocessing(tmp_path):
    path = tmp_path / "booklet.pdf"
    linked_pdf(path)
    navigation = extract_navigation(path, HTML)
    add_pdf_bookmarks(path, navigation)
    with pymupdf.open(path) as pdf:
        pdf.set_toc([[1, "Wrong title", 1]])
        pdf.saveIncr()
    with pytest.raises(ValueError, match="bookmarks differ"):
        validate_navigation(path, fill_toc_pages(HTML, navigation), navigation)


def test_a_contents_row_split_across_pdf_pages_is_rejected(tmp_path):
    path = tmp_path / "split.pdf"
    linked_pdf(path)
    with pymupdf.open(path) as pdf:
        pdf[1].insert_link(
            {
                "kind": pymupdf.LINK_NAMED,
                "from": pymupdf.Rect(50, 85, 520, 105),
                "nameddest": "chapter",
            }
        )
        pdf.saveIncr()
    with pytest.raises(ValueError, match="splits across pages"):
        extract_navigation(path, HTML)


def test_wrong_printed_number_is_rejected_in_html_and_actual_pdf(tmp_path):
    path = tmp_path / "wrong.pdf"
    linked_pdf(path, printed="1")
    navigation = extract_navigation(path, HTML)
    with pytest.raises(ValueError, match="HTML printed page"):
        validate_navigation(path, HTML.replace(">00</span>", ">1</span>"), navigation)
    with pytest.raises(ValueError, match="PDF printed page"):
        validate_navigation(path, fill_toc_pages(HTML, navigation), navigation)


def test_wrong_link_page_and_page_bottom_destination_are_rejected(tmp_path):
    path = tmp_path / "booklet.pdf"
    linked_pdf(path)
    navigation = extract_navigation(path, HTML)
    add_pdf_bookmarks(path, navigation)
    with pymupdf.open(path) as pdf:
        link = pdf[0].get_links()[0]
        pdf[0].update_link({**link, "page": 0})
        pdf.saveIncr()
    with pytest.raises(ValueError, match="wrong page"):
        validate_navigation(path, fill_toc_pages(HTML, navigation), navigation)
    with pymupdf.open(path) as pdf:
        link = pdf[0].get_links()[0]
        pdf[0].update_link({**link, "page": 1, "to": pymupdf.Point(0, 800)})
        pdf.saveIncr()
    with pytest.raises(ValueError, match="page top"):
        validate_navigation(path, fill_toc_pages(HTML, navigation), navigation)


def test_stability_check_rejects_changed_page_count_and_duplicate_html_targets(tmp_path):
    path = tmp_path / "booklet.pdf"
    linked_pdf(path)
    navigation = extract_navigation(path, HTML)
    with pymupdf.open(path) as pdf:
        pdf.new_page()
        pdf.saveIncr()
    with pytest.raises(ValueError, match="between render passes"):
        validate_navigation(path, fill_toc_pages(HTML, navigation), navigation)
    with pytest.raises(ValueError, match="duplicate contents target"):
        fill_toc_pages(HTML + HTML, navigation)


def test_no_contents_v1_remains_unchanged(tmp_path):
    path = tmp_path / "legacy.pdf"
    linked_pdf(path)
    html = "<h1>Legacy booklet</h1>"
    navigation = extract_navigation(path, html)
    assert navigation["entries"] == []
    assert fill_toc_pages(html, navigation) == html
    add_pdf_bookmarks(path, navigation)
    assert validate_navigation(path, html, navigation) == navigation


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("SQUASH_PDF_SMOKE") != "1", reason="Installed Edge PDF check")
def test_edge_prints_long_contents_across_pages_without_splitting_rows(tmp_path):
    from squash_scheduler.exporters.documents import render_pdf

    # Long titles force genuine wrapping; enough rows overflow a portrait page.
    context = {
        "name": "Navigation test",
        "draft": True,
        "front_matter": True,
        "publication": {"document_profile": "prospectus"},
        "event_dates": [],
        "courts": [],
        "prospectus": {"configured": True, "approved": True, "sections": []},
        "unplayed_divisions": [{"name": "Unplayed", "names": ["Player"]}],
        "rule_summaries": [],
        "graphs": [
            {"title": f"Division {i:02d} " + "long wrapped contents title " * 4, "svg": ""}
            for i in range(30)
        ],
        "schedule_pages": [
            {"title": "Day one", "date": "2027-01-01", "rows": []},
            {"title": "Day one continued", "date": "2027-01-01", "rows": []},
            {"title": "Day two", "date": "2027-01-02", "rows": []},
        ],
        "sponsors": [{"display_name": "Sponsor", "text": "Thanks", "image_data": None}],
    }
    html = render(context)
    path = tmp_path / "long.pdf"
    # render_pdf itself may now perform the two passes; a second invocation also
    # proves that already-filled contents remain stable and idempotent.
    render_pdf(html, path)
    navigation = extract_navigation(path, html)
    assert len({entry["toc_page"] for entry in navigation["entries"]}) >= 2
    assert {"unplayed-divisions", "prospectus", "rules", "notices"} <= {
        entry["target"] for entry in navigation["entries"]
    }
    assert (
        len([entry for entry in navigation["entries"] if entry["target"].startswith("schedule-")])
        == 2
    )
    filled = fill_toc_pages(html, navigation)
    render_pdf(filled, path)
    final = validate_navigation(path, filled, navigation)
    add_pdf_bookmarks(path, final)
    validate_navigation(path, filled, final)
    with pymupdf.open(path) as pdf:
        assert len(pdf.get_toc()) == len(final["entries"])
