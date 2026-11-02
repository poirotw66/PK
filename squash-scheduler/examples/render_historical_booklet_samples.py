"""Render editable source-evidence sample pages with shared PDF/Excel checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from historical_booklet_samples import build_model

from squash_scheduler.domain.models import Snapshot
from squash_scheduler.exporters.documents import document_qa, export_excel, render_html, render_pdf
from squash_scheduler.exporters.portal import write_portal
from squash_scheduler.storage.repository import atomic_json


def build(source: Path, output: Path, *, all_groups: bool = False) -> dict:
    if output.exists():
        raise FileExistsError("Choose a fresh historical review output directory")
    snapshot = Snapshot.model_validate_json((source / "snapshot.json").read_text("utf-8"))
    project = Path(__file__).resolve().parents[1]
    if all_groups:
        from historical_booklet_model import build_model as build_all_groups

        model = build_all_groups(project, snapshot.config["publication"]["font_asset_id"])
    else:
        model = build_model(project, snapshot.config["publication"]["font_asset_id"])
    output.mkdir(parents=True)
    html = render_html(model, output, asset_root=source / "assets")
    (output / "review.html").write_text(html, "utf-8")
    # The source-evidence model has no approved registration/identity Snapshot.
    # Export only its explicit source rows; an empty snapshot adds no identities.
    export_excel(Snapshot(config={}), model, output / "schedule.xlsx")
    renderer = (
        render_pdf(html, output / "booklet.pdf", html_path=output / "review.html")
        if model["publication"].get("layout_template_id") == "reference-v2"
        else render_pdf(html, output / "booklet.pdf")
    )
    qa = document_qa(model, output)
    atomic_json(output / "render-model.json", model)
    atomic_json(output / "qa-report.json", qa)
    write_portal(model, output)
    source_page_to_generated_pages: dict[str, list[int]] = {}
    if (output / "navigation-map.json").is_file():
        navigation = json.loads((output / "navigation-map.json").read_text("utf-8"))
        destinations = {entry["target"]: entry["page"] for entry in navigation["entries"]}
        for index, graph in enumerate(model["graph_metadata"].values(), 1):
            if graph.get("source_page") is not None:
                source_page_to_generated_pages.setdefault(str(graph["source_page"]), []).append(
                    destinations[f"graph-{index}"]
                )
    if model.get("historical_graph_catalog"):
        from historical_graph_catalog import render_html as render_catalog
        from historical_graph_catalog import with_generation_evidence

        delivered_catalog = with_generation_evidence(
            model["historical_graph_catalog"], source_page_to_generated_pages, len(qa["pages"])
        )
        atomic_json(output / "graph-catalog.json", delivered_catalog)
        (output / "graph-catalog.html").write_text(
            render_catalog(delivered_catalog, json_filename="graph-catalog.json"), "utf-8"
        )
    result = {
        "status": "draft_source_evidence_review_required",
        "source_sha256": model["metadata"]["source_sha256"],
        "source_pages": model["metadata"]["source_pages"],
        "matches": len(model["matches"]),
        "graph_count": len(model["graph_metadata"]),
        "source_graph_count": len(
            {
                stage.get("source_page")
                for stage in model["stages"]
                if stage.get("source_page") is not None
            }
        ),
        "unlabelled_source_edges": len(model.get("unlabelled_edges", [])),
        "source_page_to_generated_pages": source_page_to_generated_pages,
        "page_count": len(qa["pages"]),
        "document_qa_passed": qa["passed"],
        "qr_values": qa["qr_values"],
        "formal_publication_approved": False,
        "renderer": renderer,
    }
    atomic_json(output / "review-report.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("output/full-roster-simulation-delivery")
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--all-groups", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.source, args.output, all_groups=args.all_groups), indent=2, ensure_ascii=True
        )
    )
