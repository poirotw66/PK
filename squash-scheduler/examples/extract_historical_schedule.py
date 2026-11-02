"""Write private historical schedule evidence; no event mutation or approval."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import pymupdf

from squash_scheduler.importers.historical_schedule import (
    audit_pdf_locations,
    compare_schedules,
    extract_excel_schedule,
    extract_pdf_schedule,
    validate_evidence,
)


def _write_review(output: Path, artifacts: dict) -> None:
    pdf = artifacts["published-schedule.json"]
    check = artifacts["published-validation.json"]
    parts = [
        "<!doctype html><html lang=en><meta charset=utf-8><title>Historical evidence</title>",
        (
            "<style>body{font:16px system-ui;max-width:1200px;margin:2rem auto;padding:1rem}"
            "table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;"
            "padding:.5rem;text-align:left}code{overflow-wrap:anywhere}</style>"
        ),
        "<h1>Historical schedule — draft evidence</h1>",
        (
            "<p>Published PDF is the baseline. This extraction does not approve an event, "
            "resolve participant identities, or establish courts. Empty fields remain unknown.</p>"
        ),
        f"<p>PDF SHA-256: <code>{pdf['source_sha256']}</code></p>",
        (
            f"<p>{check['numbered_rows']} numbered rows; {check['unique_codes']} unique codes; "
            f"{check['unnumbered_exhibitions']} unnumbered exhibitions. "
            f"Displayed sides: {check['known_display_sides']['2']} both, "
            f"{check['known_display_sides']['1']} one, "
            f"{check['known_display_sides']['0']} neither.</p>"
        ),
        (
            "<p><a href=published-schedule.json>Full PDF evidence</a> · "
            "<a href=published-validation.json>Structural validation</a> · "
            "<a href=source-location-audit.json>Original-file location audit</a></p>"
        ),
    ]
    diff = artifacts.get("schedule-differences.json")
    if diff:
        parts.extend(
            [
                "<h2>Working Excel comparison</h2>",
                (
                    f"<p>{diff['difference_count']} shared numeric-code rows differ: "
                    f"{len(diff['personnel_display_difference_codes'])} displayed-personnel differences, "
                    f"{len(diff['timing_difference_codes'])} timing differences; shared overlap "
                    f"{html.escape(', '.join(diff['personnel_and_timing_codes']))}. "
                    "Text comparison uses NFC and ignores whitespace. It does not resolve identity.</p>"
                ),
                (
                    "<p><a href=schedule-differences.json>Full differences and both source locations</a> · "
                    "<a href=working-schedule.json>Working Excel evidence</a></p>"
                ),
                (
                    "<table><tr><th>Code</th><th>Fields</th><th>Published</th><th>Working</th>"
                    "<th>Source locations</th></tr>"
                ),
            ]
        )
        for row in diff["differences"]:
            pdf_row, excel_row = row["published"], row["working"]
            published_values = " / ".join(str(pdf_row[field]) for field in row["fields"])
            working_values = " / ".join(str(excel_row[field]) for field in row["fields"])
            page = pdf_row["source"]["page"]
            excel_source = excel_row["source"]
            parts.append(
                "<tr>"
                + "".join(
                    f"<td>{html.escape(value)}</td>"
                    for value in (
                        row["display_code"],
                        ", ".join(row["fields"]),
                        published_values,
                        working_values,
                    )
                )
                + f"<td><a href=page-{page:02}.png>PDF page {page}</a>; "
                + html.escape(f"Excel {excel_source['sheet']}!{excel_source['range']}")
                + "</td></tr>"
            )
        parts.append("</table><h2>Additional working display labels</h2><ul>")
        for row in diff["additional_working_display_rows"]:
            source = row["source"]
            parts.append(
                "<li>"
                + html.escape(
                    f"{row['display_code']} — {source['sheet']}!{source['range']}; "
                    "retained separately from the 230 numeric codes and 26-row comparison"
                )
                + "</li>"
            )
        parts.append("</ul>")
    parts.append("<h2>Rendered source pages</h2><p>")
    parts.extend(f"<a href=page-{p['page']:02}.png>Page {p['page']}</a> " for p in pdf["pages"])
    parts.append("</p></html>")
    (output / "review.html").write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--excel", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-count", type=int)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pdf = extract_pdf_schedule(args.pdf)
    expected = (
        {str(n) for n in range(1, args.expected_count + 1)}
        if args.expected_count is not None
        else None
    )
    artifacts = {
        "published-schedule.json": pdf,
        "published-validation.json": validate_evidence(pdf, expected),
        "source-location-audit.json": audit_pdf_locations(pdf, args.pdf),
    }
    if args.excel:
        excel = extract_excel_schedule(args.excel)
        artifacts.update(
            {
                "working-schedule.json": excel,
                "working-validation.json": validate_evidence(excel, expected),
                "schedule-differences.json": compare_schedules(pdf, excel),
            }
        )
    for name, data in artifacts.items():
        (args.output / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    with pymupdf.open(args.pdf) as document:
        for info in pdf["pages"]:
            number = info["page"]
            document[number - 1].get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25)).save(
                args.output / f"page-{number:02}.png"
            )
    _write_review(args.output, artifacts)
    print(
        json.dumps(
            {name: data for name, data in artifacts.items() if name.endswith("validation.json")},
            ensure_ascii=False,
            indent=2,
        )
    )
    if "schedule-differences.json" in artifacts:
        diff = artifacts["schedule-differences.json"]
        print(
            json.dumps(
                {
                    key: value
                    for key, value in diff.items()
                    if key
                    not in (
                        "differences",
                        "additional_working_display_rows",
                        "additional_published_display_rows",
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
