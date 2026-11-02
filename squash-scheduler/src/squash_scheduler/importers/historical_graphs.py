"""Preserve published graph pages as source evidence, without reconstructing draws."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from xml.etree import ElementTree as ET


def roster_from_import_report(report: dict) -> list[dict]:
    """Use confirmed exact-name identity; preserve internal whitespace and source refs."""
    divisions = {
        row["division_id"]: row["official_name"]
        for row in report["candidates"]
        if row.get("ignored_reason") == "division_heading"
    }
    roster = []
    for row in report["candidates"]:
        if row.get("ignored_reason") is not None or not row.get("division_id"):
            continue
        name = row["official_name"]
        roster.append(
            {
                "name": name,
                "division": divisions[row["division_id"]],
                "division_id": row["division_id"],
                "participant_id": str(uuid5(NAMESPACE_URL, "full-event/exact-name/" + name)),
                "registration_id": str(
                    uuid5(NAMESPACE_URL, "full-event/source-registration/" + row["id"])
                ),
                "source_ref": row["source_ref"],
            }
        )
    return roster


def extract_source_graphs(pdf_path: Path, directory: Path, page_count: int = 25) -> dict:
    """Export original pages including labels/lines/results exactly as published."""
    import pymupdf

    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    metadata = {}
    with pymupdf.open(pdf_path) as document:
        if len(document) < page_count:
            raise ValueError("Source PDF has fewer pages than requested graph pages")
        for index in range(page_count):
            page = document[index]
            lines = page.get_text().splitlines()
            heading = next(
                (line.strip() for line in lines if re.search(r"(男子|女子).*組", line)), ""
            )
            if not heading:
                raise ValueError(f"Graph page {index + 1} has no identifiable division heading")
            division = re.split(r"\s+", heading, maxsplit=1)[0]
            stage = heading[len(division) :].strip() or "原稿未另標階段"
            filename = f"bracket-source-{index + 1:03d}.svg"
            root = ET.fromstring(page.get_svg_image(text_as_path=True))
            root.set("aria-label", heading + f" · 原稿第 {index + 1} 頁")
            root.set("role", "img")
            root.set("data-division", division)
            root.set("data-source-page", str(index + 1))
            ET.ElementTree(root).write(directory / filename, encoding="utf-8", xml_declaration=True)
            metadata[filename] = {
                "file": filename,
                "division": division,
                "stage": stage,
                "title": heading,
                "source_page": index + 1,
                "source_filename": pdf_path.name,
                "source_sha256": digest,
                "presentation": "original_source_page",
                "svg_sha256": hashlib.sha256((directory / filename).read_bytes()).hexdigest(),
            }
    return metadata
