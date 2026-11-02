"""Read local historical evidence and stage names; never approve identities or a schedule."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf
from docx import Document
from openpyxl import load_workbook

from squash_scheduler.domain.config import load_config
from squash_scheduler.domain.models import Division, Snapshot, stable_id, utcnow
from squash_scheduler.importers.word import import_roster
from squash_scheduler.storage.repository import Repository, atomic_json

GROUP = re.compile(r"(?:高中|國中|國小)(?:男子|女子)組|(?:男子|女子)[A-F]組")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_sources(source_root: Path, converted: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    conversion = json.loads((converted.parent / "conversion-report.json").read_text("utf-8-sig"))
    original = Path(conversion["source_path"])
    if (
        conversion["status"] != "converted_for_historical_review"
        or digest(original) != conversion["source_sha256"]
        or digest(converted) != conversion["converted_sha256"]
    ):
        raise ValueError("Conversion provenance checksum mismatch")
    document = Document(converted)
    headings = []
    for paragraph in document.paragraphs:
        normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", paragraph.text))
        if GROUP.fullmatch(normalized) and normalized not in headings:
            headings.append(normalized)
    divisions = [
        Division(
            id=stable_id("historical-2026-division", name),
            name=name,
            category="school" if name.startswith(("高中", "國中", "國小")) else "competitive",
            gender_category="male" if "男子" in name else "female",
            level_order=None
            if name.startswith(("高中", "國中", "國小"))
            else ord(name[-2]) - ord("A") + 1,
        )
        for name in headings
    ]
    config = load_config(source_root / "squash-event-config.template.json")
    config["event"].update(
        id=stable_id("historical-2026", "staging"),
        name="2026中正盃歷史核對草案",
        actor_id="historical-evidence-import",
    )
    config.update(
        divisions_status="configured", divisions=[d.model_dump(mode="json") for d in divisions]
    )
    config["registration"]["parser_profiles"] = [
        {
            "source_sha256": digest(converted),
            "layout": "horizontal_whitespace",
            "join_single_cjk_pairs": True,
            "ignored_paragraphs": [0],
        }
    ]
    snapshot = Snapshot(config=config, divisions=divisions)
    imported = import_roster(snapshot, converted)
    original_id = stable_id("historical-original-source", digest(original))
    original_metadata = {
        "id": original_id,
        "original_name": original.name,
        "media_type": "application/msword",
        "sha256": digest(original),
        "imported_at": utcnow().isoformat(),
    }
    snapshot.sources.append(original_metadata)
    for source in snapshot.sources:
        if source["id"] != original_id:
            source["converted_from_source_id"] = original_id
            source["conversion"] = conversion
    repository = Repository(output / "staging")
    with repository.lock():
        repository.archive_source(original, original_metadata)
        for source in snapshot.sources:
            if source["id"] != original_id:
                repository.archive_source(converted, source)
        snapshot = repository.save(snapshot, None)
    atomic_json(output / "import-report.json", imported)
    candidates = [c for c in imported["candidates"] if c["status"] != "ignored_with_reason"]
    counts = Counter(c["division_id"] for c in candidates)
    workbook_path = source_root / "2026中正盃.xlsx"
    wb = load_workbook(workbook_path, read_only=True, data_only=False)
    sheets = []
    for sheet in wb:
        formulas = sum(cell.data_type == "f" for row in sheet for cell in row)
        sheets.append(
            {
                "name": sheet.title,
                "rows": sheet.max_row,
                "columns": sheet.max_column,
                "formulas": formulas,
            }
        )
    wb.close()
    pdf_path = source_root / "2026中正盃20261023.pdf"
    pdf = pymupdf.open(pdf_path)
    text_root = output / "pdf-text"
    text_root.mkdir()
    pages = []
    for index in range(pdf.page_count):
        page = pdf.load_page(index)
        text = page.get_text()
        (text_root / f"page-{index + 1:03}.txt").write_text(text, "utf-8")
        normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
        pages.append(
            {
                "page": index + 1,
                "characters": len(text),
                "division_headings": sorted(set(GROUP.findall(normalized))),
            }
        )
    pdf.close()
    report = {
        "scope": "historical_evidence_and_unresolved_roster_staging",
        "created_at": utcnow().isoformat(),
        "word": {
            "source_sha256": digest(original),
            "converted_sha256": digest(converted),
            "original_unchanged": True,
            "paragraphs": len(document.paragraphs),
            "tables": len(document.tables),
            "divisions": len(divisions),
            "name_candidates": len(candidates),
            "independent_participants": None,
            "accepted_registrations": 0,
            "counts_by_division": {d.name: counts[d.id] for d in divisions},
            "import_status": imported["status"],
        },
        "excel": {
            "sha256": digest(workbook_path),
            "sheet_count": len(sheets),
            "formula_count": sum(s["formulas"] for s in sheets),
            "sheets": sheets,
        },
        "pdf": {"sha256": digest(pdf_path), "page_count": len(pages), "pages": pages},
        "staging_workspace": str(repository.root),
        "revision_id": snapshot.id,
        "unresolved": [
            "participant identity and cross-division mappings",
            "published match-by-match golden data",
            "historical male-F special slots and sixth knockout match purpose",
            "unassigned historical reason",
            "historical visual layout and organizer approval",
        ],
        "historical_acceptance_complete": False,
    }
    atomic_json(output / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(".."))
    parser.add_argument("--converted", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("output/historical-inventory"))
    args = parser.parse_args()
    summary = inspect_sources(
        args.source_root.resolve(), args.converted.resolve(), args.output.resolve()
    )
    print(
        json.dumps(
            {
                "word_divisions": summary["word"]["divisions"],
                "word_name_candidates": summary["word"]["name_candidates"],
                "word_import_status": summary["word"]["import_status"],
                "excel_sheets": summary["excel"]["sheet_count"],
                "excel_formulas": summary["excel"]["formula_count"],
                "pdf_pages": summary["pdf"]["page_count"],
                "historical_acceptance_complete": False,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
