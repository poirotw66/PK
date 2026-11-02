"""Controlled, bounded LibreOffice conversion for binary Word and RTF input."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class RosterError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def convert_legacy(source: Path, output: Path, timeout: int = 60) -> Path:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise RosterError("IMPORT_CONVERTER_UNAVAILABLE", "LibreOffice is not installed")
    profile = output / "profile"
    user = profile / "user"
    user.mkdir(parents=True)
    # This fresh profile has no trusted macro sources, recovery jobs, or user extensions.
    (user / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
        '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
        '</item><item oor:path="/org.openoffice.Office.Writer/Content/Update">'
        '<prop oor:name="Link" oor:op="fuse"><value>0</value></prop>'
        '<prop oor:name="Field" oor:op="fuse"><value>false</value></prop>'
        "</item></oor:items>",
        encoding="utf-8",
    )
    command = [
        executable,
        f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--headless",
        "--norestore",
        "--nodefault",
        "--nolockcheck",
        "--nofirststartwizard",
        "--convert-to",
        "docx:Office Open XML Text",
        "--outdir",
        str(output.resolve()),
        str(source.resolve()),
    ]
    try:
        completed = subprocess.run(
            command,
            shell=False,
            timeout=timeout,
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RosterError(
            "IMPORT_CONVERSION_FAILED", "Word conversion failed or timed out"
        ) from exc
    result = output / (source.stem + ".docx")
    if completed.returncode or not result.is_file() or result.is_symlink():
        raise RosterError("IMPORT_CONVERSION_FAILED", "Converter did not produce a DOCX document")
    if result.resolve().parent != output.resolve():
        raise RosterError(
            "IMPORT_UNSAFE_INPUT", "Conversion output escaped its temporary directory"
        )
    return result
