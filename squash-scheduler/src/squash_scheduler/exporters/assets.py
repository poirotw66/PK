"""Content-addressed local print assets; file paths never enter publication config.

Embedding bits follow https://learn.microsoft.com/en-us/typography/opentype/spec/os2.
The browser PDF pipeline subsets fonts, so no-subsetting and bitmap-only fonts
cannot be used by this renderer even when another embedding permission is set.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from ..domain.models import utcnow
from ..storage.repository import atomic_json

AssetKind = Literal["font", "image"]
MAX_ASSET_BYTES = 32 * 1024 * 1024
ASSET_ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _font(data: bytes) -> tuple[dict[str, Any], bytes, set[int]]:
    from fontTools import __version__ as fonttools_version
    from fontTools.ttLib import TTFont

    if data[:4] not in {b"\x00\x01\x00\x00", b"OTTO", b"ttcf"}:
        raise ValueError("Unsupported font container; use TrueType, OpenType, or a font collection")
    try:
        font = TTFont(io.BytesIO(data), fontNumber=0, lazy=False, recalcTimestamp=False)
        try:
            if "OS/2" not in font or "name" not in font:
                raise ValueError("Font has no reviewable embedding permissions or version")
            fs_type = int(font["OS/2"].fsType)
            if fs_type & 0x0002:
                raise ValueError("Font embedding is restricted by its OpenType fsType permissions")
            if fs_type & 0x0100:
                raise ValueError("No-subsetting fonts are unsupported by the browser PDF pipeline")
            if fs_type & 0x0200:
                raise ValueError("Bitmap-only font embedding cannot embed printable outline text")
            if fs_type & ~0x030E:
                raise ValueError("Font has unsupported embedding permission bits")
            names = font["name"]
            version = names.getDebugName(5)
            family = names.getDebugName(1)
            if not version or not family:
                raise ValueError("Font family and version metadata are required")
            coverage = {
                code for code, glyph in (font.getBestCmap() or {}).items() if glyph != ".notdef"
            }
            if not coverage:
                raise ValueError("Font has no Unicode character coverage")
            is_otf = font.sfntVersion == "OTTO"
            output = io.BytesIO()
            font.save(output, reorderTables=True)
            embedded = output.getvalue()
            meta = {
                "version": version,
                "family": family,
                "full_name": names.getDebugName(4),
                "postscript_name": names.getDebugName(6),
                "fs_type": fs_type,
                "face_index": 0,
                "collection_face_count": int.from_bytes(data[8:12], "big")
                if data[:4] == b"ttcf"
                else 1,
                "coverage_count": len(coverage),
                "format": "opentype" if is_otf else "truetype",
                "media_type": "font/otf" if is_otf else "font/ttf",
                "embedded_sha256": hashlib.sha256(embedded).hexdigest(),
                "fonttools_version": fonttools_version,
            }
            return meta, embedded, coverage
        finally:
            font.close()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Font file is invalid or unsupported") from exc


def _image(data: bytes) -> dict[str, Any]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError("Only local PNG and JPEG sponsor assets are supported")
            if image.width < 1 or image.height < 1 or image.width * image.height > 16_000_000:
                raise ValueError("Image dimensions exceed the supported print asset limit")
            meta = {
                "version": image.format,
                "format": image.format,
                "media_type": "image/png" if image.format == "PNG" else "image/jpeg",
                "width": image.width,
                "height": image.height,
            }
            image.verify()
            return meta
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Image asset is invalid or unsupported") from exc


def _inspect(data: bytes, kind: AssetKind) -> dict[str, Any]:
    if not data or len(data) > MAX_ASSET_BYTES:
        raise ValueError("Asset must be nonempty and at most 32 MiB")
    if kind == "font":
        return _font(data)[0]
    if kind == "image":
        return _image(data)
    raise ValueError("Asset kind must be font or image")


def _regular(path: Path, root: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.resolve().parent != root.resolve():
        raise ValueError("Asset must be a regular file inside the controlled asset directory")


def _atomic_bytes(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-asset-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def import_asset(workspace: Path, path: Path, kind: AssetKind) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Import source must be a regular file")
    if path.stat().st_size > MAX_ASSET_BYTES:
        raise ValueError("Asset is larger than 32 MiB")
    data = path.read_bytes()
    details = _inspect(data, kind)
    asset_id = hashlib.sha256(data).hexdigest()
    root = workspace / "assets"
    root.mkdir(parents=True, exist_ok=True)
    if not root.resolve().is_relative_to(workspace.resolve()):
        raise ValueError("Asset storage resolves outside the workspace")
    metadata = {
        "schema_version": "1.0.0",
        "asset_id": asset_id,
        "sha256": asset_id,
        "kind": kind,
        "original_name": path.name,
        "size_bytes": len(data),
        "imported_at": utcnow().isoformat(),
        **details,
    }
    binary_path, metadata_path = root / f"{asset_id}.bin", root / f"{asset_id}.json"
    if binary_path.exists() or metadata_path.exists():
        existing = load_asset(root, asset_id, kind)
        return {key: value for key, value in existing.items() if key != "bytes"}
    _atomic_bytes(binary_path, data)
    try:
        atomic_json(metadata_path, metadata)
    except Exception:
        binary_path.unlink(missing_ok=True)
        raise
    return metadata


def load_asset(asset_root: Path, asset_id: str, kind: AssetKind) -> dict[str, Any]:
    if not isinstance(asset_id, str) or ASSET_ID_PATTERN.fullmatch(asset_id) is None:
        raise ValueError("Asset IDs must be lowercase SHA256 content hashes")
    binary_path, metadata_path = asset_root / f"{asset_id}.bin", asset_root / f"{asset_id}.json"
    _regular(binary_path, asset_root)
    _regular(metadata_path, asset_root)
    if binary_path.stat().st_size > MAX_ASSET_BYTES or metadata_path.stat().st_size > 65536:
        raise ValueError("Stored asset exceeds its supported size limit")
    data = binary_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != asset_id:
        raise ValueError("Asset content checksum mismatch")
    try:
        metadata = json.loads(metadata_path.read_text("utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError("Asset metadata cannot be read") from exc
    details = _inspect(data, kind)
    expected = {
        "schema_version": "1.0.0",
        "asset_id": asset_id,
        "sha256": asset_id,
        "kind": kind,
        "size_bytes": len(data),
        **details,
    }
    if not isinstance(metadata, dict) or any(
        metadata.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("Asset metadata differs from its verified content")
    return {**metadata, "bytes": data}


def font_payload(asset: dict[str, Any], text: str) -> tuple[bytes, dict[str, Any]]:
    """Select/extract face zero and prove coverage before browser fallback can hide gaps."""
    metadata, embedded, coverage = _font(asset["bytes"])
    missing = sorted({ord(char) for char in text if not char.isspace()} - coverage)
    if missing:
        labels = ", ".join(f"U+{value:04X}" for value in missing[:12])
        raise ValueError(f"FONT_GLYPH_MISSING: controlled font does not cover {labels}")
    return embedded, {"asset_id": asset["asset_id"], "sha256": asset["sha256"], **metadata}
