"""Fixed Windows Project Manager inspection and existing-project opening.

Captions and accessibility data only route UI actions. The caller must verify
the resulting library, folder, project name and UUID through a fresh native
project-context response after bridge startup. No arbitrary UI action is exposed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from providers.resolve.lua_editing import bounded_text
from providers.resolve.lua_project_context import validate_project_context_request
from providers.resolve.lua_transport import read_project_snapshot
from providers.resolve.project_library_catalogue import verify_project_library_target
from providers.resolve.windows_bootstrap import _unique_object, _validate_process

_STATES = {
    "resolve_not_running",
    "process_ambiguous",
    "desktop_unavailable",
    "project_manager_unavailable",
    "project_manager",
    "other_project_open",
    "modal_blocked",
}
_NODE_FIELDS = {
    "id",
    "parent",
    "depth",
    "name",
    "automation_id",
    "class_name",
    "control_type",
    "native_handle",
    "enabled",
    "offscreen",
    "selected",
    "value",
    "expand_state",
    "invoke",
    "selection_item",
    "selection",
    "expand_collapse",
    "value_pattern",
    "help_text",
    "toggle",
    "toggle_state",
}
_CONTAINER_PREFIX = (
    "UiProjectManagerDialog.frameProjectManagerBorder.m_ProjectManagerView."
    "projManagerContents.splitterProjects."
)
_CONTAINERS = (
    (
        "projects",
        _CONTAINER_PREFIX + "m_ViewProjects.frame_8.m_ProjectsWidget",
        "ProjectManager::UiProjectManagerBinView",
    ),
    (
        "libraries",
        _CONTAINER_PREFIX + "m_ViewDatabase.m_DbBinWidgetListView",
        "ProjectManager::UiProjectManagerDatabaseBinView",
    ),
)


class WindowsProjectManagerError(RuntimeError):
    """Opening failed before or possibly after the single Open dispatch."""

    def __init__(
        self,
        code: str,
        *,
        uncertain: bool = False,
        operation: str = "open_existing",
    ) -> None:
        self.code = code
        self.uncertain = uncertain
        self.dispatch_started = uncertain
        self.phase = "after_dispatch" if uncertain else "before_dispatch"
        self.operation = operation
        self.project_open_requested = uncertain and operation == "open_existing"
        self.view_changes_uncertain = uncertain and operation in {
            "prepare_context_view",
            "begin_candidate_search",
            "filter_candidate",
            "select_filtered_candidate",
        }
        self.candidate_export_uncertain = uncertain and operation in {
            "begin_candidate_export",
            "save_candidate_export",
            "cancel_candidate_export",
            "export_candidate",
            "cancel_orphaned_probe_export",
        }
        self.export_requested = self.candidate_export_uncertain
        self.diagnostics: dict[str, Any] = {}
        detail = (
            "Export may have opened a dialog; inspect it. "
            "No project Open was requested."
            if self.candidate_export_uncertain
            else "View changes may have occurred; inspect before preparing again."
            if self.view_changes_uncertain
            else "Open may have started; verify native context, do not repeat."
            if uncertain
            else "No project Open action was dispatched."
        )
        super().__init__(f"Windows Project Manager: {code}. " + detail)


def _reject_evidence_links(path: Path) -> None:
    """Keep export evidence away from symlink or Windows junction ancestors."""
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (
            candidate.exists()
            and getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Linked project export evidence is not accepted.")


def _file_facts(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_dev,
        value.st_ino,
    )


def _candidate_export_proof(
    path: Path,
    *,
    project_name: str,
    expected_project_id: str,
) -> dict[str, Any]:
    """Parse and hash one stable, unlinked native DRP without importing it."""
    if not path.is_absolute():
        raise ValueError("Candidate evidence path must be absolute.")
    _reject_evidence_links(path)
    before = path.stat()
    if not path.is_file() or not 0 < before.st_size <= 512 * 1024 * 1024:
        raise ValueError("Candidate DRP exceeds its file bound.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        handle_before = os.fstat(stream.fileno())
        # Windows path stat and fstat can expose different ctime semantics.
        # Compare identity/size/mtime across APIs, each full token to itself.
        if (
            handle_before.st_dev,
            handle_before.st_ino,
            handle_before.st_size,
            handle_before.st_mtime_ns,
        ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise WindowsProjectManagerError("CANDIDATE_EXPORT_CHANGED")
        project = read_project_snapshot(path)
        if project != {"name": project_name, "id": expected_project_id}:
            raise WindowsProjectManagerError("CANDIDATE_PROJECT_MISMATCH")
        count = 0
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > 512 * 1024 * 1024:
                raise ValueError("Candidate DRP exceeds its file bound.")
            digest.update(chunk)
        after_handle = os.fstat(stream.fileno())
    if (
        count != before.st_size
        or _file_facts(after_handle) != _file_facts(handle_before)
        or _file_facts(path.stat()) != _file_facts(before)
    ):
        raise WindowsProjectManagerError("CANDIDATE_EXPORT_CHANGED")
    return {
        "path": str(path),
        "project": project,
        "sha256": digest.hexdigest(),
        "size": before.st_size,
        "file_facts": list(_file_facts(before)),
        "verification": "native_candidate_drp_identity_and_sha256",
        "native_connection_verified": False,
    }


@dataclass(frozen=True)
class WindowsProjectManagerAdapter:
    """Run a fixed semantic driver only with explicit host-side opt-in.

    ``inspect()`` returns bounded accessibility evidence without input or focus.
    ``open_existing(process, library_name=..., folder_path=[], project_name=...)``
    accepts only the process token from that evidence and exact target names.
    Durable dispatch intent and post-open native verification belong to the
    coordinator. This adapter never retries an uncertain Open action.
    """

    enabled: bool = False
    timeout_seconds: float = 45
    evidence_directory: Path | None = None

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("Project Manager opt-in must be boolean.")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 120
        ):
            raise ValueError("Project Manager timeout must be finite and at most 120.")
        if self.evidence_directory is not None and (
            not isinstance(self.evidence_directory, Path)
            or not self.evidence_directory.is_absolute()
            or self.evidence_directory.is_symlink()
        ):
            raise ValueError("Evidence directory must be an absolute unlinked Path.")

    def capabilities(self) -> dict[str, Any]:
        """Report host availability without claiming verified UI compatibility."""
        return {
            "backend": "windows_project_manager",
            "enabled": self.enabled,
            "available": self.enabled and sys.platform == "win32",
            "platform_supported": sys.platform == "win32",
            "operations": [
                "inspect",
                "prepare_context_view",
                "begin_candidate_export",
                "inspect_export_dialog",
                "export_candidate",
                "cancel_orphaned_probe_export",
                "save_candidate_export",
                "cancel_candidate_export",
                "begin_candidate_search",
                "filter_candidate",
                "select_filtered_candidate",
                "open_existing",
            ],
            "native_connection_verified": False,
        }

    def _available(self) -> None:
        if not self.enabled:
            raise WindowsProjectManagerError("OPT_IN_REQUIRED")
        if sys.platform != "win32":
            raise WindowsProjectManagerError("WINDOWS_REQUIRED")

    def inspect(self) -> dict[str, Any]:
        """Read one bounded Project Manager tree; never restore, focus or invoke."""
        self._available()
        return self._request({"operation": "inspect"})

    def prepare_context_view(
        self,
        process: dict[str, Any],
        *,
        refresh_view: bool = False,
    ) -> dict[str, Any]:
        """Enable only the observed library sidebar and list-view checkboxes.

        Fixed InvokePattern presses require determinate TogglePattern readback.
        An explicit refresh cycles an enabled sidebar and presses the fixed
        thumbnail/list pair to fire the view handlers. No project is opened.
        """
        self._available()
        if type(refresh_view) is not bool:
            raise ValueError("Refresh view must be boolean.")
        process = _validate_process(process)
        if (
            process["window_handle"] <= 0
            or process["window_title"] != "Project Manager"
        ):
            raise ValueError("A Project Manager process/window token is required.")
        return self._request(
            {
                "operation": "prepare_context_view",
                "expected_process": process,
                "refresh_view": refresh_view,
            }
        )

    def open_existing(
        self,
        process: dict[str, Any],
        *,
        library_name: str,
        folder_path: list[str],
        project_name: str,
        expected_project_id: str,
        expected_database: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Open one exact existing target in a verified current library/folder.

        No project creation, import, deletion or database management is supported.
        The returned dispatch evidence is not native identity or liveness proof.
        """
        self._available()
        process = _validate_process(process)
        if (
            process["window_handle"] <= 0
            or process["window_title"] != "Project Manager"
        ):
            raise ValueError("A Project Manager process/window token is required.")
        bounded_text(library_name, "Library name", 256)
        bounded_text(project_name, "Project name", 256)
        if not isinstance(folder_path, list) or len(folder_path) > 16:
            raise ValueError("Folder path must be a bounded list of exact names.")
        for name in folder_path:
            bounded_text(name, "Folder name", 256)
        context = validate_project_context_request(
            {
                "expected_database": expected_database,
                "expected_folder": "",
                "expected_project_name": project_name,
            },
            expected_project_id,
        )
        if (
            folder_path
            or context["expected_database"]["DbType"] != "Disk"
            or (context["expected_database"]["DbName"] != library_name)
        ):
            raise ValueError("Only the exact expected Disk library root is supported.")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 600
        ):
            raise ValueError("Open timeout must be finite and at most 600 seconds.")
        deadline = time.monotonic() + (
            timeout_seconds
            if timeout_seconds is not None
            else min(600.0, 6 * self.timeout_seconds)
        )
        exported: dict[str, Any] | None = None
        saved: dict[str, Any] | None = None
        export_saved_verified = False
        arguments: dict[str, Any] = {
            "expected_database": context["expected_database"],
            "project_name": project_name,
            "expected_project_id": expected_project_id,
        }
        try:
            candidate = self._new_candidate_destination()
            catalogue = verify_project_library_target(**arguments)
            self._phase_adapter(deadline).begin_candidate_search(process)
            self._phase_adapter(deadline).filter_candidate(process, **arguments)
            self._phase_adapter(deadline).select_filtered_candidate(
                process,
                **arguments,
                advance_once=True,
            )
            saved = self._phase_adapter(deadline)._export_candidate(
                process, destination=candidate
            )
            exported = {"dialog": {"ownership_token": saved.get("ownership_token")}}
            if (
                saved.get("project_open_requested") is not False
                or saved.get("candidate_export_saved") is not True
                or saved.get("destination") != str(candidate)
                or saved.get("ownership_token") != exported["dialog"]["ownership_token"]
                or type(saved.get("last_input_tick")) is not int
                or not 0 <= saved["last_input_tick"] <= 0xFFFFFFFF
            ):
                raise WindowsProjectManagerError("EXPORT_SAVE_UNVERIFIED")
            export_saved_verified = True
            proof = _candidate_export_proof(
                candidate,
                project_name=project_name,
                expected_project_id=expected_project_id,
            )
            fresh = verify_project_library_target(**arguments)
            if fresh["catalogue_signature"] != catalogue["catalogue_signature"]:
                raise WindowsProjectManagerError("CATALOGUE_CHANGED_DURING_PREFLIGHT")
            # Keep the complete evidence immutable through the last catalogue
            # read. The final driver must recheck the hash and UI fence again.
            if (
                _candidate_export_proof(
                    candidate,
                    project_name=project_name,
                    expected_project_id=expected_project_id,
                )
                != proof
            ):
                raise WindowsProjectManagerError("CANDIDATE_EXPORT_CHANGED")
            phase = self._phase_adapter(deadline)
        except Exception as cause:
            error = WindowsProjectManagerError(
                cause.code
                if isinstance(cause, WindowsProjectManagerError)
                else "PROJECT_OPEN_PREFLIGHT_FAILED",
                operation="open_existing",
            )
            error.candidate_export_uncertain = bool(
                exported is not None
                and not export_saved_verified
                or isinstance(cause, WindowsProjectManagerError)
                and cause.candidate_export_uncertain
            )
            error.export_requested = (
                exported is not None or error.candidate_export_uncertain
            )
            if isinstance(cause, WindowsProjectManagerError):
                error.view_changes_uncertain = cause.view_changes_uncertain
                error.diagnostics = dict(cause.diagnostics)
                error.diagnostics["preflight_operation"] = cause.operation
            if exported is not None and not export_saved_verified:
                error.diagnostics["owned_export_dialog"] = exported["dialog"]
            raise error from cause
        # Only this boundary may report uncertain project Open. No preflight
        # error above can leave the coordinator waiting for a nonexistent Open.
        result = phase._dispatch_verified_open(
            process,
            library_name=library_name,
            project_name=project_name,
            proof=proof,
            last_input_tick=saved["last_input_tick"],
            deadline=deadline,
        )
        result["candidate_export_proof"] = proof
        result["catalogue_guard"] = fresh
        return result

    def _phase_adapter(self, deadline: float) -> WindowsProjectManagerAdapter:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WindowsProjectManagerError("TIMEOUT")
        return replace(self, timeout_seconds=min(self.timeout_seconds, remaining))

    def _new_candidate_destination(self) -> Path:
        if self.evidence_directory is None:
            raise WindowsProjectManagerError("EVIDENCE_DIRECTORY_REQUIRED")
        root = Path(os.path.abspath(self.evidence_directory))
        _reject_evidence_links(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _reject_evidence_links(root)
        attempt = root / uuid4().hex
        attempt.mkdir(mode=0o700)
        _reject_evidence_links(attempt)
        return attempt / "candidate.drp"

    def _save_candidate_export(
        self,
        process: dict[str, Any],
        *,
        exported: dict[str, Any],
        destination: Path,
    ) -> dict[str, Any]:
        self._available()
        process = _validate_process(process)
        self._validate_owned_export(process, exported)
        root = self.evidence_directory
        if root is None or (
            os.path.normcase(os.path.abspath(destination.parent.parent))
            != os.path.normcase(os.path.abspath(root))
            or destination.name != "candidate.drp"
            or not re.fullmatch(r"[0-9a-f]{32}", destination.parent.name)
            or not destination.parent.is_dir()
            or destination.exists()
        ):
            raise WindowsProjectManagerError(
                "FRESH_EXPORT_DESTINATION_REQUIRED", operation="save_candidate_export"
            )
        _reject_evidence_links(destination)
        return self._request(
            {
                "operation": "save_candidate_export",
                "expected_process": process,
                "dialog": exported["dialog"],
                "last_input_tick": exported["last_input_tick"],
                "destination": str(destination),
            }
        )

    def save_candidate_export(
        self, process: dict[str, Any], *, exported: dict[str, Any]
    ) -> dict[str, Any]:
        """Save only the agent-owned candidate export to a fresh private path."""
        self._available()
        self._validate_owned_export(_validate_process(process), exported)
        return self._save_candidate_export(
            process, exported=exported, destination=self._new_candidate_destination()
        )

    def export_candidate(self, process: dict[str, Any]) -> dict[str, Any]:
        """Keep the native Export and Save invocations in one actor lifetime."""
        self._available()
        return self._export_candidate(
            process, destination=self._new_candidate_destination()
        )

    def _export_candidate(
        self, process: dict[str, Any], *, destination: Path
    ) -> dict[str, Any]:
        self._available()
        process = _validate_process(process)
        if (
            process["window_title"] != "Project Manager"
            or process["window_handle"] <= 0
        ):
            raise ValueError("A Project Manager token is required.")
        if self.evidence_directory is None or (
            os.path.normcase(os.path.abspath(destination.parent.parent))
            != os.path.normcase(os.path.abspath(self.evidence_directory))
            or destination.name != "candidate.drp"
            or not re.fullmatch(r"[0-9a-f]{32}", destination.parent.name)
            or not destination.parent.is_dir()
            or destination.exists()
        ):
            raise WindowsProjectManagerError(
                "FRESH_EXPORT_DESTINATION_REQUIRED", operation="export_candidate"
            )
        _reject_evidence_links(destination)
        return self._request(
            {
                "operation": "export_candidate",
                "expected_process": process,
                "ownership_token": uuid4().hex,
                "destination": str(destination),
            }
        )

    def cancel_candidate_export(
        self, process: dict[str, Any], *, exported: dict[str, Any]
    ) -> dict[str, Any]:
        """Cancel only the exact owned Export dialog; never close a project."""
        self._available()
        process = _validate_process(process)
        self._validate_owned_export(process, exported)
        return self._request(
            {
                "operation": "cancel_candidate_export",
                "expected_process": process,
                "dialog": exported["dialog"],
                "last_input_tick": exported["last_input_tick"],
            }
        )

    def cancel_orphaned_probe_export(
        self,
        process: dict[str, Any],
        *,
        previous_exported: dict[str, Any],
    ) -> dict[str, Any]:
        """Explicitly recover one unowned probe dialog using a prior native witness.

        This is never called by production Open or retry logic. It neither
        transfers the old ownership token nor saves or opens a project.
        """
        self._available()
        process = _validate_process(process)
        self._validate_owned_export(process, previous_exported)
        nodes = previous_exported["dialog_tree"]
        hosts = [
            node
            for node in nodes
            if node["automation_id"] == "FileNameControlHost"
            and node["class_name"] == "AppControlHost"
            and node["control_type"] == "ControlType.ComboBox"
        ]
        if len(hosts) != 1:
            raise WindowsProjectManagerError(
                "ORPHAN_PROBE_WITNESS_UNVERIFIED",
                operation="cancel_orphaned_probe_export",
            )
        edits = [
            node
            for node in nodes
            if node["parent"] == hosts[0]["id"]
            and node["automation_id"] == "1001"
            and node["class_name"] == "Edit"
            and node["control_type"] == "ControlType.Edit"
        ]
        filename = hosts[0]["value"]
        if (
            len(edits) != 1
            or not isinstance(filename, str)
            or edits[0]["value"] != filename
            or not filename.endswith(".drp")
            or not 4 < len(filename) <= 260
            or any(value in filename for value in ("/", "\\", "\x00", "\r", "\n"))
        ):
            raise WindowsProjectManagerError(
                "ORPHAN_PROBE_WITNESS_UNVERIFIED",
                operation="cancel_orphaned_probe_export",
            )
        return self._request(
            {
                "operation": "cancel_orphaned_probe_export",
                "expected_process": process,
                "previous_dialog": previous_exported["dialog"],
                "expected_default_filename": filename,
            }
        )

    @staticmethod
    def _validate_owned_export(
        process: dict[str, Any], exported: dict[str, Any]
    ) -> None:
        try:
            WindowsProjectManagerAdapter._validate_response(
                exported,
                {
                    "operation": "begin_candidate_export",
                    "expected_process": process,
                    "ownership_token": exported["dialog"]["ownership_token"],
                },
            )
            if (
                exported["dialog"]["title"] != "Export Project File"
                or exported["dialog"]["class_name"] != "#32770"
                or exported["dialog"]["automation_id"] != ""
            ):
                raise ValueError("Unobserved Export dialog profile.")
        except (ValueError, KeyError, TypeError) as cause:
            raise WindowsProjectManagerError(
                "EXPORT_DIALOG_PROFILE_UNVERIFIED", operation="save_candidate_export"
            ) from cause

    def _dispatch_verified_open(
        self,
        process: dict[str, Any],
        *,
        library_name: str,
        project_name: str,
        proof: dict[str, Any],
        last_input_tick: int,
        deadline: float,
    ) -> dict[str, Any]:
        """Recheck artifact identity, then enter the one fixed Open boundary."""
        try:
            candidate = Path(proof["path"])
            if (
                _candidate_export_proof(
                    candidate,
                    project_name=project_name,
                    expected_project_id=proof["project"]["id"],
                )
                != proof
            ):
                raise WindowsProjectManagerError("CANDIDATE_EXPORT_CHANGED")
            phase = self._phase_adapter(deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WindowsProjectManagerError("TIMEOUT")
            payload = {
                "operation": "open_existing",
                "expected_process": process,
                "library_name": library_name,
                "folder_path": [],
                "project_name": project_name,
                "last_input_tick": last_input_tick,
                "deadline_utc_ms": int((time.time() + remaining) * 1000),
                "candidate_export": {
                    "path": proof["path"],
                    "sha256": proof["sha256"],
                    "size": proof["size"],
                    "mtime_ns": proof["file_facts"][1],
                },
            }
        except WindowsProjectManagerError:
            raise
        except Exception as cause:
            raise WindowsProjectManagerError("CANDIDATE_EXPORT_UNVERIFIED") from cause
        return phase._request(payload)

    def begin_candidate_export(self, process: dict[str, Any]) -> dict[str, Any]:
        """Invoke only fixed Export, then inspect its newly owned modal dialog.

        No destination is entered and no project is opened. The unique dialog
        token is evidence for the coordinator's next fixed export/cancel step.
        """
        self._available()
        process = _validate_process(process)
        if (
            process["window_handle"] <= 0
            or process["window_title"] != "Project Manager"
        ):
            raise ValueError("A Project Manager process/window token is required.")
        return self._request(
            {
                "operation": "begin_candidate_export",
                "expected_process": process,
                "ownership_token": uuid4().hex,
            }
        )

    def inspect_export_dialog(self, process: dict[str, Any]) -> dict[str, Any]:
        """Read same-process windows after Export without repeating any action."""
        self._available()
        process = _validate_process(process)
        if (
            process["window_handle"] <= 0
            or process["window_title"] != "Project Manager"
        ):
            raise ValueError("A Project Manager process/window token is required.")
        return self._request(
            {
                "operation": "inspect_export_dialog",
                "expected_process": process,
            }
        )

    def begin_candidate_search(self, process: dict[str, Any]) -> dict[str, Any]:
        """Show only the observed project search view, then inspect its controls."""
        self._available()
        process = _validate_process(process)
        if (
            process["window_handle"] <= 0
            or process["window_title"] != "Project Manager"
        ):
            raise ValueError("A Project Manager process/window token is required.")
        return self._request(
            {
                "operation": "begin_candidate_search",
                "expected_process": process,
            }
        )

    def filter_candidate(
        self,
        process: dict[str, Any],
        *,
        project_name: str,
        expected_project_id: str,
        expected_database: dict[str, Any],
    ) -> dict[str, Any]:
        """Set only the Name search query, guarded by a fresh Disk catalogue.

        Search text does not prove selection. The selected candidate still
        requires a matching native DRP export before any project Open.
        """
        self._available()
        process = _validate_process(process)
        if (
            process["window_handle"] <= 0
            or process["window_title"] != "Project Manager"
        ):
            raise ValueError("A Project Manager process/window token is required.")
        arguments: dict[str, Any] = {
            "expected_database": expected_database,
            "project_name": project_name,
            "expected_project_id": expected_project_id,
        }
        try:
            before = verify_project_library_target(**arguments)
        except (OSError, ValueError, RuntimeError) as error:
            raise WindowsProjectManagerError(
                "CATALOGUE_TARGET_UNVERIFIED",
                operation="filter_candidate",
            ) from error
        result = self._request(
            {
                "operation": "filter_candidate",
                "expected_process": process,
                "project_name": project_name,
            }
        )
        try:
            after = verify_project_library_target(**arguments)
            if before["catalogue_signature"] != after["catalogue_signature"]:
                raise ValueError("Catalogue changed during filtering.")
        except (OSError, ValueError, RuntimeError) as error:
            raise WindowsProjectManagerError(
                "CATALOGUE_CHANGED_DURING_FILTER",
                uncertain=True,
                operation="filter_candidate",
            ) from error
        result["catalogue_guard"] = after
        return result

    def select_filtered_candidate(
        self,
        process: dict[str, Any],
        *,
        project_name: str,
        expected_project_id: str,
        expected_database: dict[str, Any],
        advance_once: bool = False,
    ) -> dict[str, Any]:
        """Request the first filtered item via one fixed, PM-scoped Home pair.

        UIA must prove keyboard focus lies inside the exact project bin. This
        requests selection only; the native candidate export remains mandatory.
        """
        self._available()
        if type(advance_once) is not bool:
            raise ValueError("Advance once must be boolean.")
        process = _validate_process(process)
        if (
            process["window_handle"] <= 0
            or process["window_title"] != "Project Manager"
        ):
            raise ValueError("A Project Manager process/window token is required.")
        arguments: dict[str, Any] = {
            "expected_database": expected_database,
            "project_name": project_name,
            "expected_project_id": expected_project_id,
        }
        try:
            before = verify_project_library_target(**arguments)
        except (OSError, ValueError, RuntimeError) as error:
            raise WindowsProjectManagerError(
                "CATALOGUE_TARGET_UNVERIFIED",
                operation="select_filtered_candidate",
            ) from error
        result = self._request(
            {
                "operation": "select_filtered_candidate",
                "expected_process": process,
                "project_name": project_name,
                "advance_once": advance_once,
            }
        )
        try:
            after = verify_project_library_target(**arguments)
            if before["catalogue_signature"] != after["catalogue_signature"]:
                raise ValueError("Catalogue changed during selection.")
        except (OSError, ValueError, RuntimeError) as error:
            raise WindowsProjectManagerError(
                "CATALOGUE_CHANGED_DURING_SELECTION",
                uncertain=True,
                operation="select_filtered_candidate",
            ) from error
        result["catalogue_guard"] = after
        return result

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Keep all caller values on stdin, outside the fixed executable command."""
        mutating = payload["operation"] not in {"inspect", "inspect_export_dialog"}

        def failure(
            code: str, *, uncertain: bool = False
        ) -> WindowsProjectManagerError:
            return WindowsProjectManagerError(
                code,
                uncertain=uncertain,
                operation=payload["operation"],
            )

        executable = (
            Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell/v1.0/powershell.exe"
        )
        try:
            completed = subprocess.run(
                [
                    str(executable),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-STA",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(Path(__file__).with_name("ResolveProjectManagerUI.ps1")),
                ],
                input=json.dumps(payload, ensure_ascii=False, allow_nan=False),
                encoding="utf-8",
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as error:
            raise failure("TIMEOUT", uncertain=mutating) from error
        except FileNotFoundError as error:
            raise failure("DRIVER_UNAVAILABLE") from error
        except (OSError, UnicodeError) as error:
            raise failure("DRIVER_IO_FAILED", uncertain=mutating) from error
        try:
            if not isinstance(completed.stdout, str) or len(completed.stdout) > 1048576:
                raise ValueError("Invalid response size.")
            result = json.loads(
                completed.stdout.lstrip("\ufeff"),
                object_pairs_hook=_unique_object,
            )
            if not isinstance(result, dict):
                raise ValueError("Expected an object.")
        except (TypeError, ValueError, RecursionError) as error:
            raise failure("INVALID_RESPONSE", uncertain=mutating) from error
        if result.get("ok") is False:
            detail = result.get("error")
            if (
                set(result) != {"ok", "error"}
                or not isinstance(detail, dict)
                or not {"code", "uncertain", "phase"} <= detail.keys()
                or set(detail) - {"code", "uncertain", "phase", "diagnostics"}
                or not isinstance(detail["code"], str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", detail["code"])
                or type(detail["uncertain"]) is not bool
                or detail["phase"]
                != ("after_dispatch" if detail["uncertain"] else "before_dispatch")
                or not mutating
                and detail["uncertain"]
            ):
                raise failure("INVALID_RESPONSE", uncertain=mutating)
            diagnostics = detail.get("diagnostics", {})
            try:
                if not isinstance(diagnostics, dict):
                    raise ValueError("Invalid diagnostic object.")
                self._validate_bounded_diagnostic(diagnostics)
            except (TypeError, ValueError) as error:
                raise failure("INVALID_RESPONSE", uncertain=mutating) from error
            failure_result = failure(detail["code"], uncertain=detail["uncertain"])
            failure_result.diagnostics = diagnostics
            raise failure_result
        if completed.returncode or result.get("ok") is not True:
            raise failure("DRIVER_FAILED", uncertain=mutating)
        try:
            self._validate_response(result, payload)
        except (ValueError, TypeError, KeyError) as error:
            rejected = failure("UNVERIFIED_RESPONSE", uncertain=mutating)
            rejected.diagnostics = {
                "validation_exception": type(error).__name__[:128],
                "validation_message": str(error)[:384],
                "payload_fields": [key[:64] for key in sorted(payload)[:32]],
                "result_fields": [key[:64] for key in sorted(result)[:32]],
            }
            raise rejected from error
        return result

    @staticmethod
    def _validate_response(result: dict[str, Any], payload: dict[str, Any]) -> None:
        """Reject invented native proof and malformed or unbounded UI evidence."""
        common = {
            "ok",
            "operation",
            "backend",
            "state",
            "eligible",
            "process",
            "project_state",
            "native_connection_verified",
            "dispatch_started",
        }
        opening = payload["operation"] == "open_existing"
        preparing = payload["operation"] == "prepare_context_view"
        exporting = payload["operation"] == "begin_candidate_export"
        inspecting_export = payload["operation"] == "inspect_export_dialog"
        searching = payload["operation"] == "begin_candidate_search"
        filtering = payload["operation"] == "filter_candidate"
        selecting = payload["operation"] == "select_filtered_candidate"
        saving = payload["operation"] in {"save_candidate_export", "export_candidate"}
        cancelling = payload["operation"] == "cancel_candidate_export"
        recovering = payload["operation"] == "cancel_orphaned_probe_export"
        expected = common | (
            {"project_open_requested", "orphan_cancelled", "orphan_witness"}
            if recovering
            else {
                "project_open_requested",
                "export_requested",
                "candidate_export_saved",
                "destination",
                "ownership_token",
                "last_input_tick",
                "file_type",
            }
            if saving
            else {
                "project_open_requested",
                "export_requested",
                "candidate_export_cancelled",
                "ownership_token",
            }
            if cancelling
            else {"project_open_requested", "windows", "windows_truncated", "pm_found"}
            if inspecting_export
            else {
                "project_open_requested",
                "ui_selection_verified",
                "selection_requested",
                "project_name",
                "search_value",
                "search_criteria",
                "last_input_tick",
                "selection_method",
                "home_messages_posted",
                "advance_once",
                "down_messages_posted",
            }
            if selecting
            else {
                "export_requested",
                "project_open_requested",
                "last_input_tick",
                "dialog",
                "dialog_tree",
                "dialog_tree_truncated",
            }
            if exporting
            else {"library_name", "folder_path", "project_name"}
            if opening
            else {
                "tree",
                "tree_truncated",
                "raw_tree",
                "raw_tree_truncated",
                "context",
                "container_accessibility",
            }
        )
        if preparing:
            expected |= {
                "view_changes",
                "view_changed",
                "project_open_requested",
                "refresh_view",
            }
        if searching:
            expected |= {
                "project_open_requested",
                "search_toggle_before",
                "search_toggle_after",
                "view_changed",
                "search_requested",
            }
        if filtering:
            expected |= {
                "project_open_requested",
                "project_name",
                "search_value",
                "search_criteria",
                "search_before",
                "view_changed",
                "ui_selection_verified",
            }
        if exporting and ("invoke_completed" in result or "invoke_succeeded" in result):
            expected |= {"invoke_completed", "invoke_succeeded"}
            if (
                type(result.get("invoke_completed")) is not bool
                or type(result.get("invoke_succeeded")) is not bool
            ):
                raise ValueError("Invalid export invocation diagnostic.")
        if (
            set(result) != expected
            or result["operation"] != payload["operation"]
            or result["backend"] != "windows_project_manager"
            or result["native_connection_verified"] is not False
            or result["project_state"] != "unverified"
            or result["dispatch_started"] is not opening
            or type(result["eligible"]) is not bool
        ):
            raise ValueError("Invalid Project Manager response.")
        if result["process"] is not None:
            _validate_process(result["process"])
        elif result["state"] not in {
            "resolve_not_running",
            "process_ambiguous",
            "desktop_unavailable",
        }:
            raise ValueError("Missing process evidence.")
        if opening:
            if (
                result["state"] != "open_requested"
                or result["eligible"] is not True
                or result["process"] != payload["expected_process"]
                or any(
                    result[key] != payload[key]
                    for key in (
                        "library_name",
                        "folder_path",
                        "project_name",
                    )
                )
            ):
                raise ValueError("Open target identity changed.")
            return
        if recovering:
            witness = result["orphan_witness"]
            if (
                result["state"] != "orphaned_probe_export_cancelled"
                or result["eligible"] is not True
                or result["process"] != payload["expected_process"]
                or result["project_open_requested"] is not False
                or result["orphan_cancelled"] is not True
                or not isinstance(witness, dict)
                or set(witness)
                != {
                    "verification",
                    "previous_dialog_handle",
                    "previous_ownership_token",
                    "recovered_dialog_handle",
                    "recovered_runtime_id",
                    "owner_handles",
                    "default_filename",
                }
                or witness["verification"] != "explicit_unowned_probe_recovery"
                or witness["previous_dialog_handle"]
                != payload["previous_dialog"]["handle"]
                or witness["previous_ownership_token"]
                != payload["previous_dialog"]["ownership_token"]
                or type(witness["recovered_dialog_handle"]) is not int
                or witness["recovered_dialog_handle"] <= 0
                or witness["recovered_dialog_handle"]
                in {
                    payload["expected_process"]["window_handle"],
                    payload["previous_dialog"]["handle"],
                }
                or witness["owner_handles"] != []
                or witness["default_filename"] != payload["expected_default_filename"]
                or not isinstance(witness["recovered_runtime_id"], list)
                or not 1 <= len(witness["recovered_runtime_id"]) <= 16
                or any(
                    type(value) is not int or not -(2**31) <= value < 2**31
                    for value in witness["recovered_runtime_id"]
                )
            ):
                raise ValueError("Unverified orphan recovery witness.")
            return
        if saving or cancelling:
            if (
                result["state"]
                != (
                    "candidate_export_saved" if saving else "candidate_export_cancelled"
                )
                or result["eligible"] is not True
                or result["process"] != payload["expected_process"]
                or result["project_open_requested"] is not False
                or result["export_requested"] is not True
                or result["ownership_token"]
                != (
                    payload["ownership_token"]
                    if payload["operation"] == "export_candidate"
                    else payload["dialog"]["ownership_token"]
                )
                or saving
                and (
                    result["candidate_export_saved"] is not True
                    or result["destination"] != payload["destination"]
                    or result["file_type"] != "Export files (*.drp)"
                    or type(result["last_input_tick"]) is not int
                    or not 0 <= result["last_input_tick"] <= 0xFFFFFFFF
                    or payload["operation"] == "save_candidate_export"
                    and result["last_input_tick"] != payload["last_input_tick"]
                )
                or cancelling
                and result["candidate_export_cancelled"] is not True
            ):
                raise ValueError("Unverified candidate export completion.")
            return
        if exporting:
            dialog = result["dialog"]
            if (
                result["state"] != "candidate_export_dialog"
                or result["eligible"] is not False
                or result["process"] != payload["expected_process"]
                or result["export_requested"] is not True
                or result["project_open_requested"] is not False
                or type(result["last_input_tick"]) is not int
                or not 0 <= result["last_input_tick"] <= 0xFFFFFFFF
                or type(result["dialog_tree_truncated"]) is not bool
                or not isinstance(result["dialog_tree"], list)
                or len(result["dialog_tree"]) > 384
                or not isinstance(dialog, dict)
                or set(dialog)
                != {
                    "ownership_token",
                    "pid",
                    "creation_time",
                    "handle",
                    "title",
                    "class_name",
                    "automation_id",
                    "runtime_id",
                    "owner_handles",
                }
                or dialog["ownership_token"] != payload["ownership_token"]
                or dialog["pid"] != payload["expected_process"]["pid"]
                or type(dialog["pid"]) is not int
                or dialog["creation_time"]
                != payload["expected_process"]["creation_time"]
                or type(dialog["handle"]) is not int
                or not 0 < dialog["handle"] <= 0x7FFFFFFFFFFFFFFF
                or dialog["handle"] == payload["expected_process"]["window_handle"]
                or any(
                    not isinstance(dialog[key], str) or len(dialog[key]) > maximum
                    for key, maximum in (
                        ("title", 512),
                        ("class_name", 128),
                        ("automation_id", 256),
                    )
                )
                or not isinstance(dialog["runtime_id"], list)
                or not 1 <= len(dialog["runtime_id"]) <= 16
                or any(
                    type(value) is not int or not -(2**31) <= value < 2**31
                    for value in dialog["runtime_id"]
                )
                or not isinstance(dialog["owner_handles"], list)
                or not 1 <= len(dialog["owner_handles"]) <= 8
                or any(
                    type(value) is not int or not 0 < value <= 0x7FFFFFFFFFFFFFFF
                    for value in dialog["owner_handles"]
                )
                or payload["expected_process"]["window_handle"]
                not in dialog["owner_handles"]
            ):
                raise ValueError("Unverified candidate export dialog.")
            WindowsProjectManagerAdapter._validate_tree(result["dialog_tree"], 10)
            return
        if inspecting_export:
            if (
                result["state"] != "export_dialog_inspection"
                or result["eligible"] is not False
                or result["process"] != payload["expected_process"]
                or result["project_open_requested"] is not False
                or type(result["pm_found"]) is not bool
                or type(result["windows_truncated"]) is not bool
                or not isinstance(result["windows"], list)
                or len(result["windows"]) > 16
            ):
                raise ValueError("Invalid export dialog inspection.")
            for window in result["windows"]:
                if (
                    not isinstance(window, dict)
                    or set(window)
                    != {
                        "handle",
                        "pid",
                        "name",
                        "class_name",
                        "automation_id",
                        "control_type",
                        "enabled",
                        "offscreen",
                        "modal",
                        "owner_handles",
                        "owner_is_pm",
                        "tree",
                        "tree_truncated",
                    }
                    or type(window["handle"]) is not int
                    or not 0 < window["handle"] < 2**63
                    or type(window["pid"]) is not int
                    or window["pid"] != payload["expected_process"]["pid"]
                    or any(
                        not isinstance(window[key], str) or len(window[key]) > maximum
                        for key, maximum in (
                            ("name", 512),
                            ("class_name", 128),
                            ("automation_id", 256),
                            ("control_type", 64),
                        )
                    )
                    or any(
                        type(window[key]) is not bool
                        for key in (
                            "enabled",
                            "offscreen",
                            "owner_is_pm",
                            "tree_truncated",
                        )
                    )
                    or window["modal"] is not None
                    and type(window["modal"]) is not bool
                    or not isinstance(window["owner_handles"], list)
                    or len(window["owner_handles"]) > 8
                    or any(
                        type(value) is not int or not 0 < value < 2**63
                        for value in window["owner_handles"]
                    )
                    or window["owner_is_pm"]
                    is not (
                        payload["expected_process"]["window_handle"]
                        in window["owner_handles"]
                    )
                    or not isinstance(window["tree"], list)
                    or len(window["tree"]) > 128
                ):
                    raise ValueError("Invalid same-process window diagnostic.")
                WindowsProjectManagerAdapter._validate_tree(window["tree"], 10)
            return
        if selecting:
            if (
                result["state"] != "selection_requested"
                or result["eligible"] is not True
                or result["process"] != payload["expected_process"]
                or result["project_open_requested"] is not False
                or result["ui_selection_verified"] is not False
                or result["selection_requested"] is not True
                or result["project_name"] != payload["project_name"]
                or result["search_value"] != payload["project_name"]
                or result["search_criteria"] != "Name"
                or type(result["last_input_tick"]) is not int
                or not 0 <= result["last_input_tick"] <= 0xFFFFFFFF
                or result["selection_method"]
                != (
                    "fixed_pm_home_then_down_messages"
                    if payload["advance_once"]
                    else "fixed_pm_home_message"
                )
                or type(result["home_messages_posted"]) is not int
                or result["home_messages_posted"] != 2
                or result["advance_once"] is not payload["advance_once"]
                or type(result["down_messages_posted"]) is not int
                or result["down_messages_posted"]
                != (2 if payload["advance_once"] else 0)
            ):
                raise ValueError("Unverified fixed selection request.")
            return
        if searching and (
            result["state"] != "project_manager"
            or result["eligible"] is not True
            or result["process"] != payload["expected_process"]
            or result["project_open_requested"] is not False
            or result["search_requested"] is not True
            or result["search_toggle_before"] not in {"On", "Off"}
            or result["search_toggle_after"] != "On"
            or result["view_changed"] is not (result["search_toggle_before"] == "Off")
        ):
            raise ValueError("Unverified project search view.")
        if filtering and (
            result["state"] != "project_manager"
            or result["eligible"] is not True
            or result["process"] != payload["expected_process"]
            or result["project_open_requested"] is not False
            or result["ui_selection_verified"] is not False
            or result["project_name"] != payload["project_name"]
            or result["search_value"] != payload["project_name"]
            or result["search_criteria"] != "Name"
            or not isinstance(result["search_before"], str)
            or len(result["search_before"]) > 512
            or result["view_changed"]
            is not (result["search_before"] != payload["project_name"])
        ):
            raise ValueError("Unverified exact-name search filter.")
        if preparing:
            changes = result["view_changes"]
            if (
                result["state"] != "project_manager"
                or result["eligible"] is not True
                or result["process"] != payload["expected_process"]
                or result["project_open_requested"] is not False
                or type(result["view_changed"]) is not bool
                or result["refresh_view"] is not payload["refresh_view"]
                or not isinstance(changes, list)
                or len(changes) != 2
            ):
                raise ValueError("Invalid prepared view evidence.")
            for item, control in zip(
                changes, ("library_sidebar", "list_view"), strict=True
            ):
                if (
                    not isinstance(item, dict)
                    or set(item)
                    != {
                        "control",
                        "before",
                        "after",
                        "changed",
                        "press_sequence",
                        "verification",
                    }
                    or item["control"] != control
                    or item["before"] not in {"Off", "On"}
                    or item["after"] != "On"
                    or type(item["changed"]) is not bool
                    or item["verification"] != "windows_uia_invoke_toggle_readback"
                ):
                    raise ValueError("Invalid view toggle evidence.")
                sequence = (
                    [control]
                    if item["before"] == "Off"
                    else [
                        "thumbnail_view" if control == "list_view" else control,
                        control,
                    ]
                    if payload["refresh_view"]
                    else []
                )
                if item["press_sequence"] != sequence or item["changed"] is not bool(
                    sequence
                ):
                    raise ValueError("Invalid fixed view press evidence.")
            if result["view_changed"] is not any(item["changed"] for item in changes):
                raise ValueError("View change summary differs from its evidence.")
        if (
            result["state"] not in _STATES
            or result["eligible"] != (result["state"] == "project_manager")
            or not isinstance(result["tree"], list)
            or len(result["tree"]) > 384
            or type(result["tree_truncated"]) is not bool
            or not isinstance(result["raw_tree"], list)
            or len(result["raw_tree"]) > 384
            or type(result["raw_tree_truncated"]) is not bool
            or result["context"]
            != {
                "library_name": None,
                "folder_path": None,
                "project_names": None,
                "verification": "accessibility_tree_only",
            }
        ):
            raise ValueError("Invalid inspection context.")
        for field, maximum_depth in (("tree", 10), ("raw_tree", 14)):
            WindowsProjectManagerAdapter._validate_tree(result[field], maximum_depth)
        containers = result["container_accessibility"]
        if not isinstance(containers, list) or len(containers) not in {0, 2}:
            raise ValueError("Invalid container diagnostics.")
        for record, (kind, identifier, class_name) in zip(
            containers, _CONTAINERS, strict=False
        ):
            if (
                not isinstance(record, dict)
                or set(record)
                != {
                    "kind",
                    "automation_id",
                    "class_name",
                    "present",
                    "ambiguous",
                    "patterns",
                }
                or record["kind"] != kind
                or record["automation_id"] != identifier
                or record["class_name"] != class_name
                or type(record["present"]) is not bool
                or type(record["ambiguous"]) is not bool
                or record["present"]
                and record["ambiguous"]
            ):
                raise ValueError("Invalid fixed container identity.")
            patterns = record["patterns"]
            if not record["present"]:
                if patterns is not None:
                    raise ValueError("Unexpected absent-container diagnostics.")
                continue
            if not isinstance(patterns, dict) or set(patterns) != {
                "supported_patterns",
                "pattern_list_truncated",
                "errors",
                "legacy",
                "grid",
                "table",
                "item_container",
                "text",
            }:
                raise ValueError("Invalid container pattern diagnostics.")
            if (
                not isinstance(patterns["supported_patterns"], list)
                or len(patterns["supported_patterns"]) > 32
                or any(
                    not isinstance(item, str) or len(item) > 128
                    for item in patterns["supported_patterns"]
                )
                or type(patterns["pattern_list_truncated"]) is not bool
                or not isinstance(patterns["errors"], list)
                or len(patterns["errors"]) > 6
                or any(
                    not isinstance(item, str) or not re.fullmatch(r"[A-Z_]{1,80}", item)
                    for item in patterns["errors"]
                )
                or any(
                    not isinstance(patterns[key], dict)
                    or type(patterns[key].get("available")) is not bool
                    for key in ("legacy", "grid", "table", "item_container", "text")
                )
            ):
                raise ValueError("Invalid container capability evidence.")
            WindowsProjectManagerAdapter._validate_bounded_diagnostic(patterns)

    @staticmethod
    def _validate_bounded_diagnostic(value: Any) -> None:
        """Bound nested read-only pattern metadata; it is never an action selector."""
        remaining = 4096

        def visit(item: Any, depth: int) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining < 0 or depth > 8:
                raise ValueError("Container diagnostic exceeds its structural bound.")
            if item is None or type(item) is bool:
                return
            if type(item) is int and -(2**63) <= item < 2**63:
                return
            if isinstance(item, str) and len(item) <= 2048:
                return
            if isinstance(item, list) and len(item) <= 32:
                for child in item:
                    visit(child, depth + 1)
                return
            if (
                isinstance(item, dict)
                and len(item) <= 32
                and all(isinstance(key, str) and len(key) <= 64 for key in item)
            ):
                for child in item.values():
                    visit(child, depth + 1)
                return
            raise ValueError("Invalid bounded container diagnostic value.")

        visit(value, 0)

    @staticmethod
    def _validate_tree(nodes: list[dict[str, Any]], maximum_depth: int) -> None:
        """Validate each bounded accessibility view independently."""
        seen: set[int] = set()
        for node in nodes:
            if (
                not isinstance(node, dict)
                or set(node) != _NODE_FIELDS
                or type(node["id"]) is not int
                or node["id"] in seen
                or node["id"] < 0
                or node["parent"] is not None
                and (type(node["parent"]) is not int or node["parent"] not in seen)
                or type(node["depth"]) is not int
                or not 0 <= node["depth"] <= maximum_depth
                or type(node["native_handle"]) is not int
                or not 0 <= node["native_handle"] <= 0x7FFFFFFFFFFFFFFF
                or any(
                    type(node[key]) is not bool
                    for key in (
                        "enabled",
                        "offscreen",
                        "invoke",
                        "selection_item",
                        "selection",
                        "expand_collapse",
                        "value_pattern",
                        "toggle",
                    )
                )
                or node["selected"] is not None
                and type(node["selected"]) is not bool
                or node["expand_state"]
                not in {None, "Collapsed", "Expanded", "PartiallyExpanded", "LeafNode"}
                or node["toggle_state"] not in {None, "On", "Off", "Indeterminate"}
                or any(
                    not isinstance(node[key], str) or len(node[key]) > bound
                    for key, bound in (
                        ("name", 512),
                        ("automation_id", 256),
                        ("class_name", 128),
                        ("control_type", 64),
                        ("help_text", 512),
                    )
                )
                or node["value"] is not None
                and (not isinstance(node["value"], str) or len(node["value"]) > 512)
            ):
                raise ValueError("Invalid inspection node.")
            seen.add(node["id"])
