"""Controlled asset integrity, OpenType permissions, and actual local font rendering."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pymupdf
import pytest
from fontTools.ttLib import TTFont
from PIL import Image
from test_documents import document_event

from squash_scheduler.exporters import assets as asset_module
from squash_scheduler.exporters import documents
from squash_scheduler.exporters.assets import font_payload, import_asset, load_asset


@pytest.fixture
def font_path():
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/msjh.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        pytest.skip("No local test font is installed")
    return path


def png(tmp_path):
    path = tmp_path / "logo.png"
    Image.new("RGB", (32, 24), color=(30, 80, 50)).save(path)
    return path


def test_import_is_content_addressed_and_duplicate_import_is_idempotent(tmp_path):
    source = png(tmp_path)
    workspace = tmp_path / "workspace"
    first = import_asset(workspace, source, "image")
    second = import_asset(workspace, source, "image")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    assert first == second
    assert first["asset_id"] == first["sha256"] == expected
    assert (workspace / "assets" / f"{expected}.bin").read_bytes() == source.read_bytes()
    loaded = load_asset(workspace / "assets", expected, "image")
    assert (loaded["width"], loaded["height"], loaded["media_type"]) == (32, 24, "image/png")
    assert loaded["bytes"] == source.read_bytes()
    assert not list((workspace / "assets").glob(".pending-*"))


def test_failed_metadata_commit_does_not_leave_an_unloadable_binary_asset(tmp_path, monkeypatch):
    source = png(tmp_path)

    def fail_commit(*_args):
        raise OSError("Synthetic metadata commit failure")

    monkeypatch.setattr(asset_module, "atomic_json", fail_commit)
    with pytest.raises(OSError, match="metadata commit failure"):
        import_asset(tmp_path, source, "image")
    assert not list((tmp_path / "assets").iterdir())


@pytest.mark.parametrize(
    "asset_id", ["../secret", "/absolute/path", "a" * 63, "A" * 64, "a" * 64 + ".bin"]
)
def test_asset_identifiers_never_become_arbitrary_paths(tmp_path, asset_id):
    with pytest.raises(ValueError, match="SHA256"):
        load_asset(tmp_path, asset_id, "image")


def test_loading_detects_binary_or_metadata_tampering_and_wrong_asset_kind(tmp_path):
    source = png(tmp_path)
    metadata = import_asset(tmp_path, source, "image")
    asset_id = metadata["asset_id"]
    root = tmp_path / "assets"
    binary = root / f"{asset_id}.bin"
    binary.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        load_asset(root, asset_id, "image")
    binary.write_bytes(source.read_bytes())
    meta_path = root / f"{asset_id}.json"
    changed = dict(metadata, version="forged-version")
    meta_path.write_text(json.dumps(changed), "utf-8")
    with pytest.raises(ValueError, match="metadata differs"):
        load_asset(root, asset_id, "image")
    meta_path.write_text(json.dumps(metadata), "utf-8")
    with pytest.raises(ValueError, match="Unsupported font"):
        load_asset(root, asset_id, "font")


def test_untrusted_svg_and_invalid_font_do_not_create_asset_records(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text("<svg><script>alert(1)</script></svg>", "utf-8")
    with pytest.raises(ValueError, match="invalid or unsupported"):
        import_asset(tmp_path, svg, "image")
    with pytest.raises(ValueError, match="Unsupported font"):
        import_asset(tmp_path, svg, "font")
    assert not (tmp_path / "assets").exists()


def test_font_metadata_version_and_unicode_coverage_are_verified(tmp_path, font_path):
    metadata = import_asset(tmp_path, font_path, "font")
    assert metadata["version"] and metadata["family"] and metadata["coverage_count"] > 100
    asset = load_asset(tmp_path / "assets", metadata["asset_id"], "font")
    embedded, proof = font_payload(asset, "Tournament 0123456789")
    assert hashlib.sha256(embedded).hexdigest() == proof["embedded_sha256"]
    with pytest.raises(ValueError, match=r"FONT_GLYPH_MISSING.*U\+10FFFF"):
        font_payload(asset, "unsupported\U0010ffff")


@pytest.mark.parametrize(
    "fs_type, message", [(0x0002, "restricted"), (0x0100, "No-subsetting"), (0x0200, "Bitmap-only")]
)
def test_embedding_permission_flags_are_enforced(tmp_path, font_path, fs_type, message):
    path = tmp_path / "restricted.ttf"
    with TTFont(font_path, fontNumber=0, recalcTimestamp=False) as font:
        font["OS/2"].fsType = fs_type
        font.save(path)
    with pytest.raises(ValueError, match=message):
        import_asset(tmp_path, path, "font")
    assert not (tmp_path / "assets").exists()


def test_preview_fallback_is_explicit_and_unknown_template_is_rejected(tmp_path):
    snapshot = document_event()
    model = documents.view_model(snapshot)
    documents.render_html(model, tmp_path)
    assert model["render_assets"]["font"]["mode"] == "system_preview"
    snapshot.config["publication"]["layout_template_id"] = "arbitrary-custom-path"
    with pytest.raises(ValueError, match="Unknown publication"):
        documents.render_html(documents.view_model(snapshot), tmp_path)


def test_formal_render_requires_standard_template_and_controlled_font_root(tmp_path):
    snapshot = document_event()
    model = documents.view_model(snapshot, draft=False)
    with pytest.raises(ValueError, match="standard-v1"):
        documents.render_html(model, tmp_path)
    snapshot.config["publication"]["layout_template_id"] = "standard-v1"
    model = documents.view_model(snapshot, draft=False)
    with pytest.raises(ValueError, match="controlled font_asset_id"):
        documents.render_html(model, tmp_path)
    snapshot.config["publication"]["font_asset_id"] = "a" * 64
    with pytest.raises(ValueError, match="asset_root"):
        documents.render_html(documents.view_model(snapshot, draft=False), tmp_path)


def test_sponsor_images_are_embedded_only_from_verified_controlled_assets(tmp_path):
    source = png(tmp_path)
    asset = import_asset(tmp_path, source, "image")
    snapshot = document_event()
    snapshot.config["publication"].update(
        sponsor_mode="provided",
        sponsors=[
            {
                "display_name": "Synthetic Sponsor",
                "text": "Test only",
                "asset_id": asset["asset_id"],
            }
        ],
    )
    model = documents.view_model(snapshot)
    with pytest.raises(ValueError, match="asset_root"):
        documents.render_html(model, tmp_path)
    html = documents.render_html(model, tmp_path, asset_root=tmp_path / "assets")
    assert 'class="sponsor-asset" src="data:image/png;base64,' in html
    assert model["render_assets"]["images"][0]["sha256"] == asset["asset_id"]
    assert not any("bytes" in image for image in model["render_assets"]["images"])
    snapshot.config["publication"]["sponsors"] = [
        {
            "display_name": "Test",
            "text": "Text only",
            "image_data": "https://example.test/tracker.png",
        }
    ]
    html = documents.render_html(documents.view_model(snapshot), tmp_path)
    assert "https://example.test/tracker.png" not in html


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SQUASH_PDF_SMOKE") != "1",
    reason="Set SQUASH_PDF_SMOKE=1 for actual local-font PDF integration",
)
def test_formal_pdf_embeds_verified_local_collection_font_and_records_versions(tmp_path):
    source = Path("C:/Windows/Fonts/msjh.ttc")
    if not source.is_file():
        pytest.skip("Traditional Chinese local test font is unavailable")
    asset = import_asset(tmp_path, source, "font")
    snapshot = document_event()
    snapshot.config["publication"].update(
        layout_template_id="standard-v1", font_asset_id=asset["asset_id"]
    )
    manifest = documents.build_documents(
        snapshot, tmp_path / "formal-font-render", draft=False, asset_root=tmp_path / "assets"
    )
    assert manifest["status"] == "qa_pending" and manifest["qa"]["passed"]
    font = manifest["render_assets"]["font"]
    assert font["mode"] == "controlled" and font["sha256"] == asset["asset_id"]
    assert font["face_index"] == 0 and font["collection_face_count"] >= 1
    assert manifest["build_versions"]["template_id"] == "standard-v1"
    assert manifest["build_versions"]["template_version"] and font["version"]
    html = (tmp_path / "formal-font-render" / "review.html").read_text("utf-8")
    assert "@font-face" in html and "data:font/ttf;base64," in html
    with pymupdf.open(tmp_path / "formal-font-render" / "booklet.pdf") as pdf:
        embedded_names = [
            entry[3]
            for index in range(pdf.page_count)
            for entry in pdf.load_page(index).get_fonts()
        ]
    assert any(font["postscript_name"] in name for name in embedded_names)
