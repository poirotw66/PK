"""Read files into reviewable rows and source text without changing a tournament."""

from __future__ import annotations

import hashlib
import io
import re
import stat
import unicodedata
import zipfile
from pathlib import PurePosixPath
from typing import Any

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_ROWS = 20000
MAX_TEXT = 2 * 1024 * 1024
NAME_HEADERS = {"姓名", "選手", "選手姓名", "name", "player", "playername"}
DIVISION_HEADERS = {"組別", "參賽組別", "報名組別", "division", "category", "group"}
NUMBERED = re.compile(r"^\s*(?:\d+[.．、)）:]|[（(]\d+[)）])\s*(.+?)\s*$")
DIVISION = re.compile(r"(?:組|division|category)\s*[:：]?$", re.IGNORECASE)
SEPARATOR = re.compile(r"\t+|\s{2,}|\s*[|,;，；]\s*")
PLAIN_NAME = re.compile(
    r"(?:[\u3400-\u9fff·]{2,8}|[A-Z][A-Za-z0-9'-]*(?: [A-Z][A-Za-z0-9'-]*){0,3})$"
)


def _key(value: Any) -> str:
    return re.sub(r"[\s:：_-]+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def _value(value: Any) -> str:
    return "" if value is None else unicodedata.normalize("NFC", str(value)).strip()


def _zip_check(data: bytes, expanded_limit: int) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 10000 or sum(m.file_size for m in members) > expanded_limit:
            raise ValueError("檔案解壓縮後超過安全大小限制。")
        seen = set()
        for member in members:
            name = member.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if (
                name in seen
                or path.is_absolute()
                or ".." in path.parts
                or ":" in name
                or stat.S_ISLNK(member.external_attr >> 16)
                or member.flag_bits & 1
            ):
                raise ValueError("檔案包含不安全的封裝內容。")
            seen.add(name)
            if (
                member.file_size > 1024 * 1024
                and member.file_size > max(1, member.compress_size) * 200
            ):
                raise ValueError("檔案壓縮比例超過安全限制。")
            if "vbaproject" in name.lower() or "/embeddings/" in name:
                raise ValueError("不支援巨集或內嵌物件，請另存成一般檔案。")


def _header(values: list[str]) -> tuple[int, int | None] | None:
    keys = [_key(value) for value in values]
    names = [i for i, value in enumerate(keys) if value in NAME_HEADERS]
    divisions = [i for i, value in enumerate(keys) if value in DIVISION_HEADERS]
    if len(names) == 1 and len(divisions) <= 1:
        return names[0], divisions[0] if divisions else None
    return None


def _append_row(
    rows: list[dict], name: str, division: str, source_ref: dict, warnings: list[str]
) -> None:
    if not name:
        return
    if len(name) > 100 or len(division) > 100 or "\n" in name:
        raise ValueError("姓名或組別欄位過長，請先整理來源檔案。")
    if _key(name) in NAME_HEADERS:
        return
    if len(rows) >= MAX_ROWS:
        raise ValueError("匯入筆數超過 20,000 筆限制。")
    rows.append({"name": name, "division": division, "source_ref": source_ref})
    if not division and "部分姓名尚未指定組別，請在核對時補齊。" not in warnings:
        warnings.append("部分姓名尚未指定組別，請在核對時補齊。")


def _table(
    table: list[tuple[list[str], dict]],
    fallback: str,
    rows: list[dict],
    warnings: list[str],
) -> None:
    columns = None
    for values, ref in table:
        header = _header(values)
        if header:
            columns = header
            continue
        if not columns:
            continue
        name_col, division_col = columns
        name = values[name_col] if name_col < len(values) else ""
        division = (
            values[division_col]
            if division_col is not None and division_col < len(values)
            else fallback
        )
        _append_row(rows, name, division, {**ref, "column": name_col + 1}, warnings)


def _text_rows(lines: list[tuple[str, dict]], rows: list[dict], warnings: list[str]) -> None:
    current = ""
    columns = None
    for text, ref in lines:
        line = text.strip()
        if not line:
            continue
        cells = [cell.strip() for cell in SEPARATOR.split(line)]
        header = _header(cells)
        if header:
            columns = header
            continue
        if DIVISION.search(line) and len(line) <= 100 and len(cells) == 1:
            current = line.rstrip(":：")
            columns = None
            continue
        if columns and len(cells) > 1:
            name_col, division_col = columns
            if name_col < len(cells):
                division = (
                    cells[division_col]
                    if division_col is not None and division_col < len(cells)
                    else current
                )
                _append_row(rows, cells[name_col], division, ref, warnings)
            continue
        numbered = NUMBERED.match(line)
        if current and numbered:
            name = numbered.group(1)
            if PLAIN_NAME.fullmatch(name):
                _append_row(rows, name, current, ref, warnings)


def parse_intake(filename: str, data: bytes, *, expanded_limit: int = 250 * 1024 * 1024) -> dict:
    """Return source evidence and candidate rows; every candidate still needs review."""
    if not isinstance(filename, str) or not filename.strip() or "\x00" in filename:
        raise ValueError("請提供有效的檔案名稱。")
    filename = filename.replace("\\", "/").split("/")[-1]
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE_BYTES:
        raise ValueError("請上傳 25 MiB 以內的非空白檔案。")
    if type(expanded_limit) is not int or expanded_limit <= 0:
        raise ValueError("解壓縮大小限制無效。")
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in {".xlsx", ".docx", ".pdf"}:
        raise ValueError("請使用 .xlsx、.docx 或 .pdf；舊版 Word 請先另存為 .docx。")
    digest = hashlib.sha256(data).hexdigest()
    rows: list[dict] = []
    warnings: list[str] = []
    texts: list[str] = []
    tables: list[dict] = []

    def ref(**location: Any) -> dict:
        return {"filename": filename, "sha256": digest, **location}

    try:
        if suffix in {".xlsx", ".docx"}:
            _zip_check(data, expanded_limit)
        if suffix == ".xlsx":
            from openpyxl import load_workbook

            workbook = load_workbook(
                io.BytesIO(data), read_only=True, data_only=False, keep_links=False
            )
            try:
                if len(workbook.worksheets) > 100:
                    raise ValueError("工作表超過 100 張限制。")
                total = 0
                for sheet in workbook.worksheets:
                    if (sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > 100:
                        raise ValueError("工作表超過 20,000 列或 100 欄限制。")
                    table = []
                    has_header = False
                    for number, cells in enumerate(sheet.iter_rows(), 1):
                        total += 1
                        if total > MAX_ROWS:
                            raise ValueError("工作表合計超過 20,000 列限制。")
                        if any(cell.data_type == "f" for cell in cells):
                            raise ValueError("Excel 包含公式，請先貼上為值後再匯入。")
                        values = [_value(cell.value) for cell in cells]
                        if any(len(value) > 5000 for value in values):
                            raise ValueError("Excel 儲存格文字過長。")
                        if not any(values):
                            continue
                        has_header = has_header or _header(values) is not None
                        table.append((values, ref(sheet=sheet.title, row=number)))
                        texts.append(f"[{sheet.title}:{number}] " + "\t".join(values))
                    fallback = (
                        ""
                        if re.fullmatch(r"(?:sheet|工作表)\s*\d*", sheet.title, re.IGNORECASE)
                        else sheet.title
                    )
                    if table:
                        detected = next(
                            (
                                (values, location, columns)
                                for values, location in table
                                if (columns := _header(values)) is not None
                            ),
                            None,
                        )
                        tables.append(
                            {
                                "sheet": sheet.title,
                                "fallback_division": fallback,
                                "header_row": detected[1]["row"]
                                if detected
                                else table[0][1]["row"],
                                "name_column": detected[2][0] + 1 if detected else None,
                                "division_column": detected[2][1] + 1
                                if detected and detected[2][1] is not None
                                else None,
                                "rows": [
                                    {"values": values, "source_ref": location}
                                    for values, location in table
                                ],
                            }
                        )
                    _table(table, fallback, rows, warnings)
                    if table and not has_header:
                        warnings.append(f"工作表「{sheet.title}」缺少姓名欄標題，未自動擷取名單。")
            finally:
                workbook.close()
        elif suffix == ".docx":
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph

            from .word import _read_docx

            _read_docx(data, expanded_limit)
            document = Document(io.BytesIO(data))
            current = ""
            for index, item in enumerate(document.iter_inner_content(), 1):
                if index > MAX_ROWS:
                    raise ValueError("Word 內容超過 20,000 段限制。")
                if isinstance(item, Paragraph):
                    value = item.text.strip()
                    texts.append(value)
                    is_heading = bool(item.style and item.style.name.startswith("Heading"))
                    if (DIVISION.search(value) or is_heading) and len(value) <= 100:
                        current = value.rstrip(":：")
                    elif current:
                        match = NUMBERED.match(value)
                        name = match.group(1) if match else value
                        if match or PLAIN_NAME.fullmatch(name):
                            _append_row(rows, name, current, ref(paragraph=index), warnings)
                elif isinstance(item, Table):
                    if len(item.rows) > MAX_ROWS:
                        raise ValueError("Word 表格超過 20,000 列限制。")
                    table = []
                    for number, row in enumerate(item.rows, 1):
                        values = [cell.text.strip() for cell in row.cells]
                        texts.append("\t".join(values))
                        table.append((values, ref(table=index, row=number)))
                    _table(table, current, rows, warnings)
        else:
            if not data.startswith(b"%PDF-"):
                raise ValueError("檔案不是有效的 PDF。")
            import pymupdf

            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass:
                    raise ValueError("PDF 已加密，請先移除密碼。")
                if len(document) > 300:
                    raise ValueError("PDF 超過 300 頁限制，請分割後匯入。")
                lines: list[tuple[str, dict]] = []
                text_size = 0
                for page_number, page in enumerate(document, 1):
                    text = "\n".join(
                        block[4] for block in page.get_text("blocks", sort=True) if block[6] == 0
                    )
                    if not text.strip():
                        warnings.append(f"PDF 第 {page_number} 頁沒有可讀文字，該頁可能需要 OCR。")
                    text_size += len(text.encode("utf-8"))
                    if text_size > MAX_TEXT:
                        raise ValueError("擷取文字超過 2 MiB 限制，請分割檔案。")
                    texts.append(f"[第 {page_number} 頁]\n{text}")
                    lines.extend(
                        (line, ref(page=page_number, line=number))
                        for number, line in enumerate(text.splitlines(), 1)
                    )
                if not any(line.strip() for line, _ in lines):
                    raise ValueError("PDF 沒有可讀文字，可能為掃描檔，請先使用 OCR 辨識文字。")
                _text_rows(lines, rows, warnings)
                warnings.append("PDF 文字僅供核對，賽制與規則需由操作者確認，不會自動套用。")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("無法讀取檔案，請確認格式及內容未損壞。") from exc
    text = "\n".join(texts)
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise ValueError("擷取文字超過 2 MiB 限制，請分割檔案。")
    if not rows:
        warnings.append("未擷取到名單，可參考來源文字後手動輸入姓名與組別。")
    return {
        "filename": filename,
        "sha256": digest,
        "kind": suffix[1:],
        "rows": rows,
        "text": text,
        "warnings": warnings,
        "tables": tables,
    }
