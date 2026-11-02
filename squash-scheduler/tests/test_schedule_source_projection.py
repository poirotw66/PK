"""Printed schedule columns must retain source order while the bracket retains its tree."""

import json
import re
from copy import deepcopy

import pytest
from openpyxl import load_workbook
from test_documents import document_event

from squash_scheduler.exporters.documents import export_excel, render_html, view_model
from squash_scheduler.exporters.portal import write_portal
from squash_scheduler.exporters.reference_layout import schedule_cells
from squash_scheduler.exporters.schedule_rows import schedule_display


def test_source_columns_are_preserved_without_retargeting_bracket_sides(tmp_path):
    snapshot = document_event()
    model = view_model(snapshot)
    row = model["matches"][0]
    graph_sides = (row["side_a"], row["side_b"])
    row["source_schedule_sides"] = ["SyntheticPlayer057", None]
    before = deepcopy(model)
    assert schedule_cells(row)[-2:] == ["SyntheticPlayer057", "原稿未載"]
    export_excel(snapshot, model, tmp_path / "schedule.xlsx")
    workbook = load_workbook(tmp_path / "schedule.xlsx")
    data = list(workbook["Schedule"].iter_rows(values_only=True))
    printed = dict(zip(data[0], data[1], strict=True))
    assert (printed["side_a"], printed["side_b"]) == ("SyntheticPlayer057", "原稿未載")
    portal = write_portal(model, tmp_path).read_text("utf-8")
    payload = re.search(r'<script[^>]*id="data"[^>]*>(.*?)</script>', portal, re.DOTALL)
    assert payload is not None
    visible = json.loads(payload.group(1))["matches"][0]
    assert (visible["side_a"], visible["side_b"]) == ("SyntheticPlayer057", "原稿未載")
    assert (row["side_a"], row["side_b"]) == graph_sides
    assert model == before


@pytest.mark.parametrize("invalid", [["A"], ["A", "B", "C"], [3, "B"], "AB"])
def test_source_columns_reject_ambiguous_or_malformed_values(invalid):
    with pytest.raises(ValueError, match="Source schedule"):
        schedule_display({"side_a": "A", "side_b": "B", "source_schedule_sides": invalid})


def test_source_projection_cannot_be_relabelled_as_a_formal_publication(tmp_path):
    model = view_model(document_event(), draft=False)
    model["matches"][0]["source_schedule_sides"] = ["A", "B"]
    with pytest.raises(ValueError, match="Source-only historical evidence"):
        render_html(model, tmp_path)
