"""Ordered, provenance-preserving Word parsing with explicit identity review."""

from __future__ import annotations

import hashlib
import io
import re
import stat
import tempfile
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from squash_scheduler.domain.config import ParserProfile
from squash_scheduler.domain.models import Issue, Snapshot, canonical_hash, stable_id, utcnow
from squash_scheduler.importers.legacy_doc import RosterError, convert_legacy

PARSER_VERSION = "word-roster-1.1.0"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NUMBER = re.compile(r"^\s*(?:[0-9０-９]+\s*[.．、)）:]\s*|[（(][0-9０-９]+[)）]\s*)")
NOTE = re.compile(r"\s*([（(][^()（）]*[)）])\s*$")
HEADER = {"姓名", "選手", "選手姓名", "Name", "Player", "編號", "序號", "學校", "單位", "組別"}
NON_ROSTER = re.compile(
    r"^(?:贊助|主辦|承辦|協辦|備註|說明|Sponsor|Note|第\s*\d+\s*頁)", re.IGNORECASE
)


def _text(element: ET.Element) -> str:
    parts = []
    for child in element.iter():
        if child.tag == W + "t":
            parts.append(child.text or "")
        elif child.tag in (W + "br", W + "cr"):
            parts.append("\n")
        elif child.tag == W + "tab":
            parts.append("\t")
    return "".join(parts)


def _key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value)).rstrip(":：")


def _aliases(snapshot: Snapshot) -> dict[str, str]:
    result = {_key(d.name): d.id for d in snapshot.divisions}
    aliases = snapshot.config.get("registration", {}).get("division_aliases", {})
    aliases = aliases or snapshot.config.get("division_aliases", {})
    for alias, target in aliases.items():
        if isinstance(target, list):
            for name in target:
                result[_key(name)] = alias
        else:
            result[_key(alias)] = target
    return result


def _read_docx(path: Path | bytes, expanded_limit: int) -> ET.Element:
    try:
        with zipfile.ZipFile(io.BytesIO(path) if isinstance(path, bytes) else path) as archive:
            members = archive.infolist()
            if len(members) > 10000 or sum(m.file_size for m in members) > expanded_limit:
                raise RosterError("IMPORT_UNSAFE_INPUT", "DOCX expanded size limit exceeded")
            seen = set()
            for member in members:
                name = member.filename.replace("\\", "/")
                item = PurePosixPath(name)
                if (
                    item.is_absolute()
                    or ".." in item.parts
                    or ":" in name
                    or name in seen
                    or stat.S_ISLNK(member.external_attr >> 16)
                ):
                    raise RosterError("IMPORT_UNSAFE_INPUT", "Unsafe or duplicate ZIP member")
                seen.add(name)
                if (
                    member.file_size > 1024 * 1024
                    and member.file_size > max(1, member.compress_size) * 200
                ):
                    raise RosterError("IMPORT_UNSAFE_INPUT", "Suspicious ZIP compression ratio")
                if "vbaproject" in name.lower() or name.startswith("word/embeddings/"):
                    raise RosterError(
                        "IMPORT_UNSUPPORTED_STRUCTURE",
                        "Macros and embedded objects require manual review",
                    )
                if name.endswith(".rels"):
                    relations = _xml(archive.read(member))
                    if any(r.get("TargetMode", "").lower() == "external" for r in relations):
                        raise RosterError(
                            "IMPORT_UNSUPPORTED_STRUCTURE",
                            "External document relationships are unsupported",
                        )
            if "[Content_Types].xml" not in seen or "word/document.xml" not in seen:
                raise RosterError("IMPORT_UNSUPPORTED_TYPE", "ZIP is not a Word DOCX document")
            return _xml(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, ET.ParseError, KeyError, RuntimeError) as exc:
        raise RosterError("IMPORT_INVALID_DOCUMENT", "DOCX package is malformed") from exc


def _xml(content: bytes) -> ET.Element:
    probe = content.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in probe or b"<!ENTITY" in probe:
        raise RosterError("IMPORT_UNSAFE_INPUT", "XML entities and DTDs are forbidden")
    return ET.fromstring(content)


def _parse(
    snapshot: Snapshot,
    document: ET.Element,
    source_id: str,
    batch_id: str,
    profile: dict | None = None,
) -> tuple[list, list]:
    profile = profile or {}
    aliases = _aliases(snapshot)
    divisions = {d.id for d in snapshot.divisions}
    candidates: list[dict[str, Any]] = []
    issues: list[Issue] = []
    current: str | None = None

    def add(raw: str, ref: dict, *, ignored: str | None = None, forced_review: bool = False):
        nonlocal current
        text = unicodedata.normalize("NFC", raw).strip()
        if not text:
            return
        cid = stable_id("roster-candidate", batch_id, str(len(candidates)))
        source_ref = {"source_file_id": source_id, "page": None, **ref, "raw_text": raw}
        division = aliases.get(_key(text))
        is_heading = division is not None
        if is_heading:
            current = division if division in divisions else None
            ignored = "division_heading" if current else None
            forced_review = not bool(current)
        elif NON_ROSTER.match(text) or text in HEADER:
            ignored = ignored or "non_roster_text"
        elif re.search(r"(?:組|division)\s*[:：]?$", text, re.IGNORECASE):
            current = None
            forced_review = True
        name = NUMBER.sub("", text).strip()
        note = NOTE.search(name)
        annotation = note.group(1) if note else None
        if note:
            name = name[: note.start()].strip()
        status = (
            "ignored_with_reason"
            if ignored
            else ("needs_review" if forced_review or not current or not name else "parsed")
        )
        candidate = {
            "id": cid,
            "status": status,
            "division_id": current,
            "official_name": name,
            "annotation": annotation,
            "source_ref": source_ref,
            "ignored_reason": ignored,
            "resolution": None,
        }
        candidates.append(candidate)
        if status != "ignored_with_reason":
            issues.append(
                Issue(
                    code="IDENTITY_AMBIGUOUS",
                    entity_type="ImportCandidate",
                    entity_id=cid,
                    message="Candidate requires explicit identity and division resolution",
                    suggested_action="Resolve to an existing participant ID or create a participant",
                )
            )

    body = document.find(W + "body")
    if body is None:
        raise RosterError("IMPORT_INVALID_DOCUMENT", "Word document has no body")
    paragraph = table = 0
    unsupported_tags = {
        W + t for t in ("drawing", "pict", "object", "txbxContent", "altChunk", "sdt")
    }
    for block_index, block in enumerate(body):
        if any(e.tag in unsupported_tags for e in block.iter()):
            issues.append(
                Issue(
                    code="IMPORT_UNSUPPORTED_STRUCTURE",
                    entity_type="ImportBatch",
                    entity_id=batch_id,
                    field_path=f"blocks[{block_index}]",
                    message="Images, text boxes, embedded content, and content controls require manual review",
                )
            )
        if block.tag == W + "p":
            for line_index, line in enumerate(_text(block).splitlines()):
                ref: dict[str, Any] = {
                    "block_index": block_index,
                    "paragraph_index": paragraph,
                    "line_index": line_index,
                }
                if paragraph in profile.get("ignored_paragraphs", []):
                    add(line, ref, ignored="source_profile_non_roster_paragraph")
                elif profile.get("layout") == "horizontal_whitespace" and _key(line) not in aliases:
                    for value, start, end, joined in _horizontal_segments(
                        line, profile.get("join_single_cjk_pairs", False)
                    ):
                        add(
                            value,
                            {
                                **ref,
                                "raw_line": line,
                                "character_start": start,
                                "character_end": end,
                            },
                            forced_review=joined,
                        )
                else:
                    add(line, ref)
            paragraph += 1
        elif block.tag == W + "tbl":
            name_column: int | None = None
            if any(e.tag == W + "tbl" for e in block.iter() if e is not block):
                issues.append(
                    Issue(
                        code="IMPORT_UNSUPPORTED_STRUCTURE",
                        entity_type="ImportBatch",
                        entity_id=batch_id,
                        message="Nested tables require manual review",
                    )
                )
            for row_index, row in enumerate(block.findall(W + "tr")):
                cells = row.findall(W + "tc")
                values = [_text(cell).strip() for cell in cells]
                for col, value in enumerate(values):
                    if value in ("姓名", "選手", "選手姓名", "Name", "Player"):
                        name_column = col
                header_row = any(
                    v in ("姓名", "選手", "選手姓名", "Name", "Player") for v in values
                )
                for col, (cell, value) in enumerate(zip(cells, values, strict=True)):
                    ref = {
                        "block_index": block_index,
                        "table_index": table,
                        "row_index": row_index,
                        "cell_index": col,
                    }
                    merged = any(e.tag in (W + "gridSpan", W + "vMerge") for e in cell.iter())
                    heading = _key(value) in aliases
                    if merged and not heading and value:
                        issues.append(
                            Issue(
                                code="IMPORT_UNSUPPORTED_STRUCTURE",
                                entity_type="ImportBatch",
                                entity_id=batch_id,
                                field_path=str(ref),
                                message="Merged roster cells require manual review",
                            )
                        )
                    ignored = (
                        "table_header"
                        if header_row
                        else (
                            "non_name_column"
                            if name_column is not None and col != name_column and not heading
                            else None
                        )
                    )
                    for line_index, line in enumerate(value.splitlines()):
                        add(
                            line,
                            {**ref, "line_index": line_index},
                            ignored=ignored,
                            forced_review=merged and not heading,
                        )
            table += 1
        elif block.tag != W + "sectPr":
            issues.append(
                Issue(
                    code="IMPORT_UNSUPPORTED_STRUCTURE",
                    entity_type="ImportBatch",
                    entity_id=batch_id,
                    message="Unrecognized document body structure",
                )
            )
    return candidates, issues


def _horizontal_segments(line: str, join_pairs: bool) -> list[tuple[str, int, int, bool]]:
    """Source-specific layout only: retain names, raw spacing and exact character spans."""
    spans = []
    cursor = 0
    for separator in list(re.finditer(r"\t+|\u3000+| {2,}", line)) + [None]:
        end = separator.start() if separator else len(line)
        value = line[cursor:end]
        start = cursor + len(value) - len(value.lstrip())
        trimmed_end = end - len(value) + len(value.rstrip())
        if start < trimmed_end:
            spans.append((start, trimmed_end))
        cursor = separator.end() if separator else len(line)
    result = []
    index = 0
    while index < len(spans):
        start, end = spans[index]
        joined = False
        if join_pairs and index + 1 < len(spans):
            next_start, next_end = spans[index + 1]
            if re.fullmatch(r"[\u3400-\u9fff]", line[start:end]) and re.fullmatch(
                r"[\u3400-\u9fff]", line[next_start:next_end]
            ):
                end, joined = next_end, True
                index += 1
        result.append((line[start:end], start, end, joined))
        index += 1
    return result


def roster_report(snapshot: Snapshot, batch: dict) -> dict:
    candidates = batch["candidates"]
    accepted = [c for c in candidates if (c.get("resolution") or {}).get("registration_id")]
    active = [e for e in snapshot.entries if e.status == "active"]
    counts = Counter(e.effective_division_id for e in active)
    reg_by_id = {r.id: r for r in snapshot.registrations}
    participant_divisions: dict[str, set[str]] = {}
    for entry in active:
        reg = reg_by_id.get(entry.registration_id)
        if reg:
            participant_divisions.setdefault(reg.participant_id, set()).add(
                entry.effective_division_id
            )
    return {
        "batch_id": batch["id"],
        "source_file_id": batch["source_file_id"],
        "status": batch["status"],
        "parser_version": PARSER_VERSION,
        "counts": {
            "source_rows": len(candidates),
            "accepted_registrations": len(accepted),
            "needs_resolution": sum(
                c["status"] != "ignored_with_reason" and not c.get("resolution") for c in candidates
            ),
            "ignored": sum(c["status"] == "ignored_with_reason" for c in candidates),
            "distinct_participants": len({c["resolution"]["participant_id"] for c in accepted}),
        },
        "ignored_reasons": dict(
            Counter(c["ignored_reason"] for c in candidates if c["ignored_reason"])
        ),
        "active_entries_by_division": {d.id: counts[d.id] for d in snapshot.divisions},
        "dual_registrations": [
            {"participant_id": p, "division_ids": sorted(ds)}
            for p, ds in participant_divisions.items()
            if len(ds) > 1
        ],
        "candidates": candidates,
        "removal_candidates": batch.get("removal_candidates", []),
        "issues": batch["issues"],
    }


def import_roster(snapshot: Snapshot, path: Path) -> dict:
    path = Path(path)
    runtime = snapshot.config.get("runtime", {})
    size_limit = runtime.get("import_max_file_mib", 25) * 1024 * 1024
    if not path.is_file() or path.is_symlink():
        raise RosterError("IMPORT_UNSAFE_INPUT", "Input must be a regular file")
    if path.stat().st_size > size_limit:
        raise RosterError("IMPORT_UNSAFE_INPUT", "Input file size limit exceeded")
    with path.open("rb") as source:
        raw = source.read(int(size_limit) + 1)
    if len(raw) > size_limit:
        raise RosterError("IMPORT_UNSAFE_INPUT", "Input file size limit exceeded")
    digest = hashlib.sha256(raw).hexdigest()
    profiles = [
        p
        for p in snapshot.config.get("registration", {}).get("parser_profiles", [])
        if p.get("source_sha256") == digest
    ]
    if len(profiles) > 1:
        raise RosterError(
            "IMPORT_UNSUPPORTED_STRUCTURE", "Multiple parser profiles match this source"
        )
    profile = ParserProfile.model_validate(profiles[0]).model_dump() if profiles else {}
    parser_config_hash = canonical_hash({"aliases": _aliases(snapshot), "profile": profile})
    for batch in snapshot.imports:
        if (
            batch.get("sha256") == digest
            and batch.get("parser_version") == PARSER_VERSION
            and batch.get("parser_config_hash") == parser_config_hash
            and batch.get("status") not in {"blocked", "superseded"}
        ):
            return {**roster_report(snapshot, batch), "idempotent": True}
    source_id = stable_id("roster-source", digest)
    previous_attempts = sum(
        b.get("sha256") == digest
        and b.get("parser_version") == PARSER_VERSION
        and b.get("parser_config_hash") == parser_config_hash
        for b in snapshot.imports
    )
    batch_id = stable_id(
        "roster-batch", digest, PARSER_VERSION, parser_config_hash, str(previous_attempts)
    )
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    expanded_limit = runtime.get("import_max_expanded_mib", 250) * 1024 * 1024
    conversion = None
    try:
        if raw.startswith(b"PK"):
            document = _read_docx(raw, expanded_limit)
        elif raw.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") or raw.lstrip().startswith(
            b"{\\rtf"
        ):
            media_type = "application/msword" if raw.startswith(b"\xd0") else "application/rtf"
            with tempfile.TemporaryDirectory(prefix="squash-import-") as tmp:
                output = Path(tmp)
                # Copy bytes to a controlled filename, independent of the supplied name or extension.
                local = output / (
                    "input.doc" if media_type == "application/msword" else "input.rtf"
                )
                local.write_bytes(raw)
                converted = convert_legacy(
                    local, output, runtime.get("conversion_timeout_seconds", 60)
                )
                if converted.stat().st_size > size_limit:
                    raise RosterError("IMPORT_UNSAFE_INPUT", "Converted file size limit exceeded")
                conversion = {
                    "converter": "LibreOffice",
                    "sha256": hashlib.sha256(converted.read_bytes()).hexdigest(),
                    "original_source_file_id": source_id,
                }
                document = _read_docx(converted, expanded_limit)
        else:
            raise RosterError(
                "IMPORT_UNSUPPORTED_TYPE", "Input is neither DOCX, binary Word, nor RTF"
            )
        candidates, issues = _parse(snapshot, document, source_id, batch_id, profile)
        if not candidates:
            issues.append(
                Issue(
                    code="IMPORT_UNSUPPORTED_STRUCTURE",
                    entity_type="ImportBatch",
                    entity_id=batch_id,
                    message="No textual roster candidates were found",
                )
            )
    except RosterError as exc:
        candidates = []
        issues = [
            Issue(code=exc.code, entity_type="ImportBatch", entity_id=batch_id, message=str(exc))
        ]
    # A changed file is evidence for review, never an instruction to withdraw entries.
    removal_candidates = []
    if candidates and not any(i.code.startswith("IMPORT_") for i in issues):
        present = {
            (c["official_name"], c["division_id"])
            for c in candidates
            if c["status"] != "ignored_with_reason"
        }
        prior = next((b for b in reversed(snapshot.imports) if b.get("status") == "applied"), None)
        if prior:
            for candidate in prior["candidates"]:
                resolution = candidate.get("resolution")
                if (
                    resolution
                    and (candidate["official_name"], resolution["division_id"]) not in present
                ):
                    removal_candidates.append(
                        {
                            "registration_id": resolution["registration_id"],
                            "previous_candidate_id": candidate["id"],
                            "status": "needs_review",
                        }
                    )
                    issues.append(
                        Issue(
                            code="REGISTRATION_REMOVAL_REVIEW_REQUIRED",
                            severity="warning",
                            entity_type="Registration",
                            entity_id=resolution["registration_id"],
                            message="Registration is absent from the changed source; review without automatic withdrawal",
                        )
                    )
    batch = {
        "id": batch_id,
        "source_file_id": source_id,
        "sha256": digest,
        "parser_version": PARSER_VERSION,
        "parser_config_hash": parser_config_hash,
        "status": "blocked" if any(i.code.startswith("IMPORT_") for i in issues) else "staged",
        "candidates": candidates,
        "removal_candidates": removal_candidates,
        "issues": [i.model_dump(mode="json") for i in issues],
    }
    superseded_candidates: set[str] = set()
    superseded_batch_ids: set[str] = set()
    for prior_batch in snapshot.imports:
        if (
            prior_batch.get("sha256") == digest
            and prior_batch.get("status") in {"staged", "blocked"}
            and not any(c.get("resolution") for c in prior_batch.get("candidates", []))
        ):
            prior_batch["status"] = "superseded"
            prior_batch["superseded_by_batch_id"] = batch_id
            superseded_batch_ids.add(prior_batch["id"])
            superseded_candidates.update(c["id"] for c in prior_batch.get("candidates", []))
            for prior_issue in prior_batch.get("issues", []):
                prior_issue.update(status="resolved", resolution_id=batch_id)
    for prior_issue_model in snapshot.issues:
        if prior_issue_model.entity_id in superseded_candidates | superseded_batch_ids:
            prior_issue_model.status = "resolved"
            prior_issue_model.resolution_id = batch_id
    snapshot.imports.append(batch)
    if not any(s["id"] == source_id for s in snapshot.sources):
        snapshot.sources.append(
            {
                "id": source_id,
                "original_name": path.name,
                "media_type": media_type,
                "sha256": digest,
                "imported_at": utcnow().isoformat(),
                "conversion": conversion,
            }
        )
    snapshot.issues.extend(issues)
    return {**roster_report(snapshot, batch), "idempotent": False}


def resolve_roster(snapshot: Snapshot, patch: dict) -> dict:
    from squash_scheduler.domain.registrations import resolve_roster as resolve

    return resolve(snapshot, patch)
