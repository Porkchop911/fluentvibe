"""Deploy a compiled `.xscr` into the FluentControl UserSpecific datastore.

Implements `Path A — drop-in` from ``docs/deployment.md``:

1. generate a fresh filename GUID,
2. (optionally) rewrite the script's ``<ObjectName>`` and the
   ``<VxWorkspaceDelta><Identifier>`` GUID,
3. copy to ``<datastore_dir>/<file_guid>.xscr``,
4. re-embed a valid ``<Checksum>`` via the real Tecan DLL wrapper
   (``fluentcontrol_core.checksum``),
5. verify the final file's checksum.

Used both by the standalone ``tecanlab deploy`` CLI subcommand and by the
chat ``--fc-gate`` flow (after FluentControl-shell validation passes).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_DATASTORE_DIR = Path(r"C:\ProgramData\Tecan\VisionX\DataBase\UserSpecific")


# Encoded and plain-text variants both occur in `.xscr` payloads — `<ObjectName>`
# is plain XML inside `<Payload>`; the `<VxWorkspaceDelta><Identifier>` block is
# HTML-entity-encoded inside a `<d2p1:string>` element.
_OBJECT_NAME_RE = re.compile(r"<ObjectName>([^<]*)</ObjectName>")
_DELTA_IDENT_RE = re.compile(
    r"&lt;Identifier&gt;([0-9a-fA-F-]{36})&lt;/Identifier&gt;"
)
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class DeploymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeployResult:
    deployed_path: Path
    file_guid: str
    object_name: str | None
    workspace_delta_id: str | None
    checksum: str
    datastore_dir: Path
    inspect: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployed_path": str(self.deployed_path),
            "file_guid": self.file_guid,
            "object_name": self.object_name,
            "workspace_delta_id": self.workspace_delta_id,
            "checksum": self.checksum,
            "datastore_dir": str(self.datastore_dir),
            "inspect": dict(self.inspect),
        }


def deploy_xscr(
    xscr_path: Path,
    *,
    datastore_dir: Path = DEFAULT_DATASTORE_DIR,
    new_object_name: str | None = None,
    new_workspace_delta_id: bool = True,
    require_fc_closed: bool = True,
) -> DeployResult:
    """Drop-in deploy ``xscr_path`` into ``datastore_dir`` under a fresh GUID.

    Parameters
    ----------
    xscr_path:
        The compiled ``.xscr`` to deploy. Must exist.
    datastore_dir:
        FluentControl's ``UserSpecific`` directory. Defaults to the production
        path; tests should pass a temporary directory.
    new_object_name:
        If given, rewrite the script's ``<ObjectName>`` (the first occurrence
        in the payload — the ones inside ``<Reference>`` blocks are not
        touched). Used to disambiguate when multiple deploys share the same
        source name.
    new_workspace_delta_id:
        If True (default), substitute a fresh GUID for the
        ``<VxWorkspaceDelta><Identifier>`` value, so the per-script delta
        identity does not collide with the source.
    require_fc_closed:
        If True, refuse to deploy while ``SystemSW.exe`` is running. The
        datastore is enumerated at FC startup; copies made while it is
        running are not reliably visible. Override only for testing.
    """
    src = Path(xscr_path)
    if not src.exists():
        raise DeploymentError(f"XSCR file not found: {src}")
    if not src.is_file():
        raise DeploymentError(f"Not a file: {src}")

    if require_fc_closed and _systemsw_running():
        raise DeploymentError(
            "FluentControl (SystemSW.exe) is running. Close it before "
            "deploying — the datastore is enumerated at startup. "
            "Pass require_fc_closed=False (or --allow-fc-running) to override."
        )

    target_dir = Path(datastore_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    file_guid = str(uuid.uuid4())
    target = target_dir / f"{file_guid}.xscr"
    if target.exists():
        raise DeploymentError(f"Target already exists (UUID collision?): {target}")

    text = src.read_text(encoding="utf-8")
    text, applied_object_name = _maybe_rewrite_object_name(text, new_object_name)
    text, applied_delta_id = _maybe_rewrite_delta_identifier(text, enabled=new_workspace_delta_id)
    target.write_text(text, encoding="utf-8")

    inspect = _checksum_rewrite_and_verify(target)
    if not inspect.get("is_valid"):
        target.unlink(missing_ok=True)
        raise DeploymentError(
            f"Checksum verification failed after deploy: {inspect}"
        )

    return DeployResult(
        deployed_path=target,
        file_guid=file_guid,
        object_name=applied_object_name,
        workspace_delta_id=applied_delta_id,
        checksum=str(inspect.get("calculated_checksum") or inspect.get("stored_checksum") or ""),
        datastore_dir=target_dir,
        inspect=inspect,
    )


def _maybe_rewrite_object_name(text: str, new_name: str | None) -> tuple[str, str | None]:
    if new_name is None:
        match = _OBJECT_NAME_RE.search(text)
        return text, match.group(1) if match else None
    safe = _xml_escape(new_name)
    replaced, n = _OBJECT_NAME_RE.subn(f"<ObjectName>{safe}</ObjectName>", text, count=1)
    if n == 0:
        raise DeploymentError("Could not find <ObjectName> in source XSCR.")
    return replaced, new_name


def _maybe_rewrite_delta_identifier(text: str, *, enabled: bool) -> tuple[str, str | None]:
    if not enabled:
        match = _DELTA_IDENT_RE.search(text)
        return text, match.group(1) if match else None
    new_guid = str(uuid.uuid4())
    replaced, n = _DELTA_IDENT_RE.subn(
        f"&lt;Identifier&gt;{new_guid}&lt;/Identifier&gt;",
        text,
        count=1,
    )
    if n == 0:
        # Source has no VxWorkspaceDelta — leave it alone, not an error.
        return text, None
    return replaced, new_guid


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _checksum_rewrite_and_verify(path: Path) -> dict[str, Any]:
    core = _import_fluentcontrol_core()
    rewrite = core.rewrite_checksum(path, in_place=True)
    inspect = core.inspect_checksum(path)
    payload = dict(inspect)
    payload["rewrite_payload"] = dict(rewrite) if isinstance(rewrite, dict) else rewrite
    return payload


def _import_fluentcontrol_core():
    try:
        from .catalog.fc_install import shared_core
    except Exception as exc:  # pragma: no cover
        raise DeploymentError(f"Could not load fc_install bridge: {exc}") from exc
    core = shared_core()
    if core is None:
        raise DeploymentError(
            "fluentcontrol_core is not importable. The compiler depends on the "
            "same module — fix the import path before deploying."
        )
    return core


def _systemsw_running() -> bool:
    """Return True if SystemSW.exe (FluentControl) is currently running.

    Uses ``psutil`` if available, else falls back to ``tasklist``.
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None  # type: ignore[assignment]
    if psutil is not None:
        try:
            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").lower()
                if name == "systemsw.exe":
                    return True
            return False
        except Exception:
            pass
    if sys.platform != "win32":
        return False
    import subprocess

    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq SystemSW.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "systemsw.exe" in (out.stdout or "").lower()


__all__ = [
    "DEFAULT_DATASTORE_DIR",
    "DeployResult",
    "DeploymentError",
    "deploy_xscr",
]
