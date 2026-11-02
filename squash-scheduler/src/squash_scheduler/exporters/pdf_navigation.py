"""Resolve printed contents from the PDF, then verify and install PDF navigation.

All page numbers in the public map are one-based physical PDF pages. No chapter
length estimates are used: Chromium's named destinations and link annotations
are the source of truth, including when the contents spans several pages.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pymupdf


def _fail(message: str) -> ValueError:
    return ValueError("PDF_NAVIGATION_MISMATCH: " + message)


class _Contents(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.offsets = [0]
        for line in html.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.entries: list[dict[str, Any]] = []
        self.ids: dict[str, int] = {}
        self.current: dict[str, Any] | None = None
        self.capture: str | None = None
        self.feed(html)
        targets = [entry["target"] for entry in self.entries]
        if len(set(targets)) != len(targets):
            raise _fail("duplicate contents target")
        for entry in self.entries:
            if self.ids.get(entry["target"]) != 1:
                raise _fail(f"missing or duplicate HTML destination {entry['target']}")
            if not entry["title"].strip() or "number_end" not in entry:
                raise _fail(f"incomplete contents row {entry['target']}")

    def character_position(self) -> int:
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            identifier = str(values["id"])
            self.ids[identifier] = self.ids.get(identifier, 0) + 1
        if tag == "a" and "data-toc-target" in values:
            target = values["data-toc-target"]
            if not target or values.get("href") != "#" + target:
                raise _fail("contents href differs from its target")
            if self.current is not None:
                raise _fail("nested contents links")
            self.current = {"target": target, "title": "", "printed_page": "", "level": 1}
        if self.current is not None and tag == "span":
            if "data-toc-title" in values:
                self.capture = "title"
            elif "data-toc-page" in values:
                self.capture = "printed_page"
                self.current["number_start"] = self.character_position() + len(
                    self.get_starttag_text() or ""
                )

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.capture:
            self.current[self.capture] += data

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag == "span":
            if self.capture == "printed_page":
                self.current["number_end"] = self.character_position()
            self.capture = None
        elif tag == "a":
            self.current["title"] = self.current["title"].strip()
            self.entries.append(self.current)
            self.current = None


def _rect_equal(first: Any, second: Any) -> bool:
    return all(abs(a - b) < 0.15 for a, b in zip(first, second, strict=True))


def _navigation(pdf: pymupdf.Document, html: str, expected: dict | None = None) -> dict:
    rows = _Contents(html).entries
    names = pdf.resolve_names() if rows else {}
    links = [(index + 1, link) for index in range(len(pdf)) for link in pdf[index].get_links()]
    prior = {entry["target"]: entry for entry in (expected or {}).get("entries", [])}
    entries = []
    for row in rows:
        target = row["target"]
        reference = prior.get(target)
        candidates = [
            (page, link)
            for page, link in links
            if link.get("nameddest") == target or link.get("id") == "toc-target:" + target
        ]
        # Postprocessing replaces Chromium's mixed-orientation named links with
        # explicit top-of-page GoTo links. Preserve identity by their source rect.
        if not candidates and reference:
            candidates = [
                (page, link)
                for page, link in links
                if page == reference["toc_page"]
                and any(_rect_equal(link["from"], rect) for rect in reference["link_rects"])
            ]
        if not candidates:
            raise _fail(f"missing PDF contents link {target}")
        source_pages = {page for page, _ in candidates}
        if len(source_pages) != 1:
            raise _fail(f"contents row splits across pages: {target}")
        destination = names.get(target)
        if destination is None or not 0 <= destination.get("page", -1) < len(pdf):
            raise _fail(f"missing or invalid PDF named destination {target}")
        page_number = destination["page"] + 1
        if any(link.get("page", -1) + 1 != page_number for _, link in candidates):
            raise _fail(f"contents link points to the wrong page: {target}")
        for _, link in candidates:
            if link["kind"] == pymupdf.LINK_GOTO:
                point = link.get("to")
                if point is None or abs(point.y) > 1:
                    raise _fail(f"contents GoTo does not open at page top: {target}")
        entries.append(
            {
                "target": target,
                "title": row["title"],
                "level": row["level"],
                "page": page_number,
                "toc_page": next(iter(source_pages)),
                "link_rects": [list(link["from"]) for _, link in candidates],
            }
        )
    return {"schema_version": "1.0.0", "page_count": len(pdf), "entries": entries}


def extract_navigation(pdf_path: Path, html_text: str) -> dict:
    """Read first-pass destinations without treating the placeholders as page numbers."""
    with pymupdf.open(pdf_path) as pdf:
        return _navigation(pdf, html_text)


def fill_toc_pages(html_text: str, navigation: dict) -> str:
    """Fill only reserved page-number spans, preserving the rest of the HTML verbatim."""
    rows = _Contents(html_text).entries
    entries = navigation["entries"]
    if [row["target"] for row in rows] != [entry["target"] for entry in entries]:
        raise _fail("HTML contents targets differ from navigation map")
    for row, entry in reversed(list(zip(rows, entries, strict=True))):
        number = entry["page"]
        if not isinstance(number, int) or not 1 <= number <= navigation["page_count"]:
            raise _fail("invalid printed page number")
        html_text = html_text[: row["number_start"]] + str(number) + html_text[row["number_end"] :]
    return html_text


def validate_navigation(
    pdf_path: Path, html_text: str, expected_navigation: dict | None = None
) -> dict:
    """Verify actual links, printed numbers and pagination stability after the second pass."""
    with pymupdf.open(pdf_path) as pdf:
        navigation = _navigation(pdf, html_text, expected_navigation)
        if expected_navigation is not None and navigation != expected_navigation:
            raise _fail("pagination or contents links changed between render passes")
        for row, entry in zip(_Contents(html_text).entries, navigation["entries"], strict=True):
            number = str(entry["page"])
            if row["printed_page"].strip() != number:
                raise _fail(f"HTML printed page differs from destination: {entry['target']}")
            bounds = pymupdf.Rect(entry["link_rects"][0])
            for rect in entry["link_rects"][1:]:
                bounds |= pymupdf.Rect(rect)
            # The template reserves a 12mm right-hand column for the page label.
            clip = pymupdf.Rect(bounds.x1 - 40, bounds.y0 - 1, bounds.x1 + 1, bounds.y1 + 1)
            printed = pdf[entry["toc_page"] - 1].get_text("text", clip=clip).strip()
            if "".join(printed.split()) != number:
                raise _fail(f"PDF printed page differs from destination: {entry['target']}")
        normalized = any(
            link.get("id", "").startswith("toc-target:")
            for index in range(len(pdf))
            for link in pdf[index].get_links()
        )
        if normalized:
            expected_outline = [
                [entry["level"], entry["title"], entry["page"]] for entry in navigation["entries"]
            ]
            if pdf.get_toc() != expected_outline:
                raise _fail("PDF bookmarks differ from the contents")
            for bookmark in pdf.get_toc(simple=False):
                point = bookmark[3].get("to")
                if point is None or abs(point.y) > 1:
                    raise _fail("PDF bookmark does not open at page top")
        return navigation


def add_pdf_bookmarks(pdf_path: Path, navigation: dict) -> None:
    """Add outline entries and make every contents click open the destination page top."""
    if not navigation["entries"]:
        return
    with pymupdf.open(pdf_path) as pdf:
        if len(pdf) != navigation["page_count"]:
            raise _fail("PDF page count changed before adding bookmarks")
        for entry in navigation["entries"]:
            page = pdf[entry["toc_page"] - 1]
            links = page.get_links()
            for rect in entry["link_rects"]:
                matched = [link for link in links if _rect_equal(link["from"], rect)]
                if len(matched) != 1:
                    raise _fail(f"missing or ambiguous contents link: {entry['target']}")
                page.update_link(
                    {
                        **matched[0],
                        "kind": pymupdf.LINK_GOTO,
                        "page": entry["page"] - 1,
                        "to": pymupdf.Point(0, 0),
                        "zoom": 0,
                    }
                )
                # Keep semantic identity after replacing the named action, so
                # a fresh read can validate the finished PDF without a sidecar.
                pdf.xref_set_key(
                    matched[0]["xref"], "NM", pymupdf.get_pdf_str("toc-target:" + entry["target"])
                )
        pdf.set_toc(
            [[entry["level"], entry["title"], entry["page"]] for entry in navigation["entries"]],
            collapse=0,
        )
        # set_toc's default top is 36pt; explicitly start every bookmark at page top.
        for index, entry in enumerate(navigation["entries"]):
            pdf.set_toc_item(
                index, kind=pymupdf.LINK_GOTO, pno=entry["page"], to=pymupdf.Point(0, 0)
            )
        pdf.saveIncr()
