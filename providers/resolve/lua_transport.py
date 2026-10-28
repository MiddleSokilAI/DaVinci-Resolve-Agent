"""Opt-in Lua prototype using a local mailbox and DRP snapshots.

The mailbox contains client-generated Lua literals, never caller-supplied code.
It must remain in a trusted local directory, like the installed bridge itself.
No GUI automation is imported or invoked by this transport.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree as ET

from agent.client import BridgeProtocolError, CommandTimeoutError
from providers.resolve.lua_compat import with_loader
from providers.resolve.lua_editing import (
    WRITE_ACTIONS,
    WriteReceipt,
    atomic_json,
    bounded_text,
    lua_value,
    validate_edit,
)
from providers.resolve.lua_finishing import (
    FINISH_ACTIONS,
    validate_finishing,
    verify_video,
)
from providers.resolve.marker_transport import (
    MarkerSlot,
    MarkerTransportError,
    publish_lease,
    validate_marker_metadata,
)


class LuaCommandTimeoutError(CommandTimeoutError):
    """A missing prototype response, without suggesting the Python bridge."""

    def __init__(
        self, command_id: str, timeout_seconds: float, *, marker: bool = False
    ) -> None:
        super().__init__(command_id, timeout_seconds)
        if marker:
            self.args = (
                "No fresh native marker response within the timeout. Check this "
                "session's Scripts-menu marker-bridge.lua, native exit exports, "
                "lease and occupied inbox. A host lease is not native liveness. "
                "For an uncertain write, repeat only the same arguments/key to "
                "inspect its original acknowledgement without resubmitting.",
            )
            return
        self.args = (
            "No experimental Lua response within the timeout. Open a saved "
            "project and start this session's bridge.lua in the Lua console; "
            "check the Lua console for a stopped loop or export failure. "
            "A timeout alone does not identify the cause; do not replay writes.",
        )


def lua_string(value: str) -> str:
    """Encode UTF-8 as fixed-width decimal escapes, without executable syntax."""
    return '"' + "".join(f"\\{byte:03d}" for byte in value.encode("utf-8")) + '"'


def prepare(root: Path, media_roots: list[Path] | None = None) -> Path:
    """Create a new isolated session; never overwrite a running session."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    session = uuid4().hex
    roots = [path.resolve().as_posix() for path in (media_roots or [])]
    if any(not Path(path).is_dir() for path in roots):
        raise ValueError("Every allowed media root must be an existing directory.")
    source = (
        Path(__file__).resolve().parents[2]
        / "bridges"
        / "resolve"
        / "ResolveLuaBridge.lua"
    ).read_text(encoding="utf-8")
    source = source.replace('"__RUNTIME_ROOT__"', lua_string(root.as_posix()))
    source = source.replace('"__SESSION__"', lua_string(session))
    source = source.replace("__MEDIA_ROOTS__", lua_value(roots))
    (root / "bridge.lua").write_text(source, encoding="utf-8")
    editing = (
        Path(__file__).resolve().parents[2]
        / "bridges"
        / "resolve"
        / "ResolveLuaEditing.lua"
    )
    (root / "editing.lua").write_text(
        editing.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "finishing.lua").write_text(
        editing.with_name("ResolveLuaFinishing.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "native_audio.lua").write_text(
        editing.with_name("ResolveLuaNativeAudio.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "audio_pilot.lua").write_text(
        editing.with_name("ResolveLuaAudioPilot.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "sync.lua").write_text(
        editing.with_name("ResolveLuaSync.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "ripple.lua").write_text(
        editing.with_name("ResolveLuaRipple.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "camera_visibility.lua").write_text(
        editing.with_name("ResolveLuaCameraVisibility.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "ripple_batch.lua").write_text(
        editing.with_name("ResolveLuaRippleBatch.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "ripple_span.lua").write_text(
        editing.with_name("ResolveLuaRippleSpan.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "privacy.lua").write_text(
        editing.with_name("ResolveLuaPrivacy.lua").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "session.json").write_text(
        json.dumps(
            {
                "session": session,
                "protocol": 4,
                "media_roots": roots,
                "capabilities": ["quit_resolve"],
            }
        ),
        encoding="utf-8",
    )
    (root / "request.lua").write_text("return nil\n", encoding="ascii")
    for script in root.glob("*.lua"):
        if script.name != "request.lua":
            script.write_text(
                with_loader(script.read_text(encoding="utf-8")), encoding="utf-8"
            )
    return root / "bridge.lua"


def read_project_snapshot(path: Path) -> dict[str, str]:
    """Read only the bounded project XML, without extracting archive paths."""
    if path.stat().st_size > 512 * 1024 * 1024:
        raise BridgeProtocolError("Experimental DRP response exceeds 512 MiB.")
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("project.xml")
        if info.file_size > 16 * 1024 * 1024:
            raise BridgeProtocolError("Project XML exceeds 16 MiB.")
        data = archive.read(info)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise BridgeProtocolError("DTD/entity declarations are not accepted.")
    # Resolve's later payload can contain non-XML element names (for example
    # numeric configuration keys). Parse only its identity header, not that
    # undocumented project graph. Never rewrite/import the archive.
    end = data.find(b"</ProjectName>")
    if end < 0 or end > 1024 * 1024:
        raise BridgeProtocolError("Export has no bounded project identity header.")
    project = ET.fromstring(data[: end + len(b"</ProjectName>")] + b"</SM_Project>")
    name, identity = project.findtext("ProjectName"), project.get("DbId")
    if project.tag != "SM_Project" or not name or not identity:
        raise BridgeProtocolError("Export has no recognized project identity.")
    return {"name": name, "id": identity}


class LuaSnapshotClient:
    """One serialized request per session; requires an open Resolve project."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        metadata = json.loads((self.root / "session.json").read_text(encoding="utf-8"))
        self.session = metadata["session"]
        self.protocol = metadata.get("protocol", 1)
        self.transport = metadata.get("transport", "lua")
        self.max_payload_bytes = metadata.get("max_payload_bytes", 4096)
        if self.transport == "marker":
            validate_marker_metadata(metadata)
        elif self.transport != "lua":
            raise ValueError("Unsupported Lua session transport.")
        self.capabilities = metadata.get("capabilities", [])
        self.media_roots = [Path(p) for p in metadata.get("media_roots", [])]
        if not isinstance(self.session, str) or len(self.session) != 32:
            raise ValueError("Invalid Lua session.")

    def _recover_marker_read_slot(self) -> None:
        """With client.lock held, release only a decoded acknowledged/expired read."""
        inbox = self.root / "inbox"
        if not inbox.exists() and not inbox.is_symlink():
            return
        slot = MarkerSlot.occupied(self.root, self.session, self.max_payload_bytes)
        if slot.request["action"] not in {
            "ping",
            "get_current_project",
            "get_timeline_summary",
            "get_render_status",
        }:
            return  # All writes, stop and unknown actions remain fail-closed.
        expired = math.floor(time.time()) > slot.request["expires"]
        candidates = [self.root / f"{slot.identity}.drp"]
        if slot.request["action"] in {"get_timeline_summary", "get_render_status"}:
            candidates = list(self.root.glob(f"{slot.identity}.ok_*.drp"))
            candidates += list(self.root.glob(f"{slot.identity}.error_*.drp"))
        acknowledged = False
        for candidate in candidates:
            try:
                read_project_snapshot(candidate)
                acknowledged = True
                break
            except (
                OSError,
                BridgeProtocolError,
                zipfile.BadZipFile,
                EOFError,
                KeyError,
                ET.ParseError,
            ):
                continue  # Partial/corrupt archives do not establish an ACK.
        if acknowledged or expired:
            slot.clear()

    def request(
        self,
        action: str,
        timeout_seconds: float = 30,
        *,
        arguments: dict[str, Any] | None = None,
        expected_project_id: str | None = None,
        confirm: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        recovery_allowed = (
            self.transport == "marker"
            and action in WRITE_ACTIONS | FINISH_ACTIONS
            and bool(idempotency_key)
        )
        if (self.root / "stopped.json").exists() and not recovery_allowed:
            raise ValueError(
                "Session is stopped. Prepare a new runtime before restarting."
            )
        if (
            action
            not in {
                "ping",
                "get_current_project",
                "stop",
                "get_render_status",
                "get_timeline_summary",
                "reload_modules",
            }
            | WRITE_ACTIONS
            | FINISH_ACTIONS
        ):
            raise ValueError("Unsupported experimental Lua action.")
        if action == "reload_modules" and self.transport == "marker":
            raise ValueError(
                "Marker modules are embedded; prepare a new session to reload."
            )
        batch_write = (
            action == "set_clip_properties"
            and isinstance(arguments, dict)
            and (
                "ripple_batch" in arguments
                or (
                    isinstance(arguments.get("native_audio"), dict)
                    and arguments["native_audio"].get("operation") == "phrase_batch"
                )
            )
        )
        maximum_timeout = 600 if batch_write else 120
        if (
            not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= maximum_timeout
        ):
            raise ValueError(
                f"Timeout must be finite and between 0 and {maximum_timeout} seconds."
            )
        write = action in WRITE_ACTIONS | FINISH_ACTIONS
        if action == "quit_resolve" and "quit_resolve" not in self.capabilities:
            raise ValueError(
                "Quit requires a freshly prepared bridge with quit support."
            )
        status_request = action == "get_render_status"
        summary_request = action == "get_timeline_summary"
        detail_request = status_request or summary_request
        if action in {"get_timeline_summary", "reload_modules"} and self.protocol != 4:
            raise ValueError("This operation requires a protocol-4 session.")
        if (action in FINISH_ACTIONS or status_request) and self.protocol not in {3, 4}:
            raise ValueError("Finishing requires a new protocol-3 session.")
        normalized: dict[str, Any] = {}
        receipt = None
        if write:
            if self.protocol not in {2, 3, 4} or confirm is not True:
                raise ValueError(
                    "Editing requires a protocol-2 or protocol-3 session "
                    "and explicit confirmation."
                )
            project_id = bounded_text(expected_project_id, "Expected project ID")
            bounded_text(idempotency_key, "Idempotency key")
            validator = (
                validate_finishing if action in FINISH_ACTIONS else validate_edit
            )
            normalized = validator(action, arguments or {}, self.media_roots)
            assert idempotency_key is not None
            receipt = WriteReceipt(
                self.root, idempotency_key, action, project_id, normalized
            )
        elif detail_request:
            bounded_text(expected_project_id, "Expected project ID")
            if confirm or idempotency_key:
                raise ValueError("Status is read-only.")
            normalized = validate_finishing(action, arguments or {}, self.media_roots)
        elif arguments or expected_project_id or confirm or idempotency_key:
            raise ValueError("Read requests accept no editing arguments.")
        owned_job = None
        accepted_start = False
        if action in {"start_render", "get_render_status"}:
            for path in (self.root / "receipts").glob("*.json"):
                saved = json.loads(path.read_text(encoding="utf-8"))
                saved_result = saved.get("result", {})
                if (
                    saved.get("status") == "completed"
                    and saved_result.get("action") == "prepare_render"
                    and saved_result.get("job_id") == normalized["job_id"]
                    and saved_result.get("project", {}).get("id") == expected_project_id
                ):
                    owned_job = saved_result
                if (
                    saved.get("status") == "completed"
                    and saved_result.get("action") == "start_render"
                    and saved_result.get("status") == "accepted"
                    and saved_result.get("arguments", {}).get("job_id")
                    == normalized["job_id"]
                    and saved_result.get("project", {}).get("id") == expected_project_id
                ):
                    accepted_start = True
            if owned_job is None:
                raise ValueError("Render job is not owned by this session and project.")
        lock = self.root / "client.lock"
        # Exclusive filesystem ownership also prevents independent MCP processes
        # from overwriting each other's mailbox. A crash leaves an explicit lock.
        with lock.open("x", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
        command_id = uuid4().hex
        response = self.root / (
            f"{command_id}.ok.drp" if write else f"{command_id}.drp"
        )
        pending = self.root / f"{command_id}.tmp"
        marker_slot: MarkerSlot | None = None
        acknowledged = False
        resuming = False
        try:
            if receipt:
                if self.transport == "marker" and receipt.path.exists():
                    saved = json.loads(receipt.path.read_text(encoding="utf-8"))
                    if saved.get("fingerprint") != receipt.fingerprint:
                        raise ValueError(
                            "Idempotency key was already used for different arguments."
                        )
                    if saved.get("status") == "pending":
                        command_id = saved.get("command_id", "")
                        if not isinstance(command_id, str) or not re.fullmatch(
                            "[0-9a-f]{32}", command_id
                        ):
                            raise BridgeProtocolError(
                                "Pending receipt command ID is invalid."
                            )
                        receipt.value = saved
                        response = self.root / f"{command_id}.ok.drp"
                        resuming = True
                        if (self.root / "inbox").exists():
                            marker_slot = MarkerSlot.existing(
                                self.root,
                                self.session,
                                command_id,
                                self.max_payload_bytes,
                            )
                            expected = dict(
                                marker_slot.request,
                                action=action,
                                confirm=True,
                                project_id=expected_project_id,
                                arguments=normalized,
                            )
                            if marker_slot.request != expected:
                                raise BridgeProtocolError(
                                    "Pending packet differs from the requested write."
                                )
                if not resuming:
                    replay = receipt.replay()
                    if replay is not None:
                        if self.transport == "marker":
                            MarkerSlot.clear_completed(
                                self.root, self.session, receipt.value["command_id"]
                            )
                        return replay
            if (self.root / "stopped.json").exists() and not resuming:
                raise ValueError("Session is stopped. Prepare a new runtime.")
            expires = math.ceil(time.time() + timeout_seconds)
            if self.transport == "marker" and not resuming:
                self._recover_marker_read_slot()
                expires = math.ceil(time.time() + timeout_seconds)
                envelope: dict[str, Any] = dict(
                    session=self.session, id=command_id, action=action, expires=expires
                )
                if write or detail_request:
                    envelope.update(
                        project_id=expected_project_id, arguments=normalized
                    )
                if write:
                    envelope["confirm"] = True
                # Bound deterministic data errors before creating a pending receipt.
                marker_slot = MarkerSlot(self.root, envelope, self.max_payload_bytes)
                marker_slot.ensure_available()
            if receipt and not resuming:
                if action == "start_render":
                    assert owned_job is not None
                    destination = Path(owned_job["output_path"])
                    if destination.exists() or any(destination.parent.iterdir()):
                        raise ValueError(
                            "Render destination is no longer empty; refusing overwrite."
                        )
                receipt.begin(command_id)
            if action == "prepare_render" and not resuming:
                (self.root / "renders" / command_id).mkdir(parents=True, exist_ok=False)
            payload = (
                "return {session="
                + lua_string(self.session)
                + ",id="
                + lua_string(command_id)
                + ",action="
                + lua_string(action)
                + f",expires={expires}"
                + (
                    (",confirm=true,project_id=" if write else ",project_id=")
                    + lua_string(str(expected_project_id))
                    + ",arguments="
                    + lua_value(normalized)
                    if write or detail_request
                    else ""
                )
                + "}\n"
            )
            deadline = time.monotonic() + timeout_seconds
            if self.transport == "marker":
                if not resuming:
                    # The MCP lease keeper may be rotating the same lease.
                    with suppress(FileExistsError):
                        publish_lease(self.root)
                    assert marker_slot is not None
                    marker_slot.publish(deadline)
            else:
                pending.write_text(payload, encoding="ascii")
                while True:
                    try:
                        pending.replace(self.root / "request.lua")
                        break
                    except PermissionError:
                        # Windows readers may briefly deny delete/rename sharing.
                        # Retry publication of the SAME ID, never a new operation.
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(min(0.025, max(0, deadline - time.monotonic())))
            while time.monotonic() < deadline:
                try:
                    errors = (
                        list(self.root.glob(f"{command_id}.error_*.drp"))
                        if write or detail_request or action == "reload_modules"
                        else []
                    )
                    if errors:
                        read_project_snapshot(
                            errors[0]
                        )  # wait for a complete error export
                        code = (
                            errors[0].name.split(".error_", 1)[1].removesuffix(".drp")
                        )
                        if receipt and code in {
                            "INVALID_ARGUMENTS",
                            "PROJECT_CHANGED",
                            "RENDERING",
                            "NAME_EXISTS",
                            "MEDIA_NOT_ALLOWED",
                            "ASSET_EXISTS",
                            "AMBIGUOUS_ASSET",
                            "AMBIGUOUS_TIMELINE",
                            "POOL_TOO_LARGE",
                            "TIMELINE_TOO_LARGE",
                            "TIMELINE_NOT_FOUND",
                            "DUPLICATE_UNAVAILABLE",
                            "ASSET_NOT_FOUND",
                            "INVALID_RANGE",
                            "SYNC_FPS_MISMATCH",
                            "BACKUP_SAVE_FAILED",
                            "BACKUP_EXPORT_FAILED",
                            "EXPIRED",
                            "ITEM_NOT_FOUND",
                            "SOURCE_CHANGED",
                            "FUSION_COMP_EXISTS",
                            "TIMELINE_NOT_EMPTY",
                            "TAKES_EXIST",
                            "TAKE_FORMAT_MISMATCH",
                            "SUBTITLES_EXIST",
                            "SUBTITLE_REQUIRES_EMPTY_AV",
                            "EMPTY_TIMELINE",
                            "RENDER_RANGE_INVALID",
                            "JOB_NOT_OWNED",
                            "JOB_ALREADY_STARTED",
                            "JOB_CHANGED",
                            "PILOT_REQUIRES_ONE_AUDIO_TRACK",
                            "SOURCE_AUDIO_DISABLED",
                            "PILOT_ITEM_CHANGED",
                            "SOURCE_AUDIO_NOT_UNITY",
                            "SOURCE_FPS_UNSUPPORTED",
                            "SOURCE_FADES_UNAVAILABLE",
                            "NORMALIZATION_UNAVAILABLE",
                            "NORMALIZATION_MODE_UNAVAILABLE",
                            "NORMALIZATION_CONSTANT_UNAVAILABLE",
                            "AUDIO_ITEM_CHANGED",
                            "AUDIO_ITEM_DISABLED",
                            "FAIRLIGHT_PRESET_MISSING_OR_AMBIGUOUS",
                            "INVALID_AUDIO_OPERATION",
                        }:
                            receipt.reject(code)
                            acknowledged = True
                        elif not write:
                            acknowledged = True
                        raise BridgeProtocolError(
                            f"Lua edit failed: {code}. No automatic retry; "
                            "inspect the receipt and backup."
                        )
                    token = None
                    accepted_response = self.root / f"{command_id}.accepted.drp"
                    if (
                        action in {"start_render", "quit_resolve"}
                        and accepted_response.exists()
                    ):
                        response = accepted_response
                    if action == "prepare_render" or detail_request:
                        matches = list(self.root.glob(f"{command_id}.ok_*.drp"))
                        if not matches:
                            raise FileNotFoundError("Waiting for typed response.")
                        if len(matches) != 1:
                            raise BridgeProtocolError("Ambiguous typed response.")
                        response = matches[0]
                        token = response.name[len(command_id) + 4 : -4]
                    project = read_project_snapshot(response)
                except (OSError, zipfile.BadZipFile, EOFError, KeyError, ET.ParseError):
                    # ExportProject creates the file before writing the ZIP footer.
                    time.sleep(0.1)
                    continue
                if write:
                    if project["id"] != expected_project_id:
                        raise BridgeProtocolError(
                            "Write response belongs to another project; "
                            "outcome is uncertain."
                        )
                    backup = self.root / f"{command_id}.before.drp"
                    if read_project_snapshot(backup)["id"] != expected_project_id:
                        raise BridgeProtocolError("Write backup identity mismatch.")
                    result: dict[str, Any] = {
                        "status": "accepted"
                        if response.name.endswith(".accepted.drp")
                        else "completed",
                        "action": action,
                        "project": project,
                        "arguments": normalized,
                        "backup_path": str(backup),
                        "response_path": str(response),
                        "replayed": False,
                        "readback_verified_by": "ResolveLuaFinishing"
                        if action in FINISH_ACTIONS
                        else "ResolveLuaEditing",
                    }
                    if action == "prepare_render":
                        ranged = "start_frame" in normalized
                        job_match = re.fullmatch(
                            r"job_([a-zA-Z0-9-]{1,64})_(\d+)_(\d+)"
                            if ranged
                            else r"job_([a-zA-Z0-9-]{1,64})",
                            token or "",
                        )
                        if (
                            not job_match
                            or ranged
                            and (
                                int(job_match[2]) != normalized["start_frame"]
                                or int(job_match[3]) != normalized["end_frame"]
                            )
                        ):
                            raise BridgeProtocolError(
                                "Invalid render job acknowledgement."
                            )
                        result.update(
                            job_id=job_match[1],
                            output_path=str(
                                self.root / "renders" / command_id / "video.mp4"
                            ),
                        )
                    assert receipt is not None
                    if action == "quit_resolve":
                        result["readback_verified_by"] = "ResolveLuaBridge"
                    if result["status"] == "accepted":
                        result["completion_verified"] = False
                        result["next_action"] = (
                            "Verify process exit independently; do not resubmit."
                            if action == "quit_resolve"
                            else "Poll get_render_status; never resubmit this start."
                        )
                    if resuming:
                        result["reconciled_original_command"] = True
                    receipt.complete(result)
                    acknowledged = True
                    return result
                acknowledged = True
                if summary_request:
                    if normalized.get("inspect_camera_visibility"):
                        match = re.fullmatch(
                            r"visibility_(\d+)_(\d+)_(\d+)_(\d+)", token or ""
                        )
                        if not match or project["id"] != expected_project_id:
                            raise BridgeProtocolError(
                                "Invalid camera visibility response."
                            )
                        return {
                            "project": project,
                            "visibility": dict(
                                zip(
                                    [
                                        "interval_count",
                                        "interval_checksum",
                                        "local_first",
                                        "local_last",
                                    ],
                                    map(int, match.groups()),
                                    strict=True,
                                )
                            ),
                            "source": "live_lua_api",
                        }
                    if normalized.get("ripple_cut") or normalized.get("ripple_batch"):
                        if (
                            token != "ripple_verified"
                            or project["id"] != expected_project_id
                        ):
                            raise BridgeProtocolError("Invalid ripple audit response.")
                        return {
                            "status": "verified",
                            "project": project,
                            "read_only": True,
                        }
                    if normalized.get("ripple_span"):
                        if (
                            token != "span_ready"
                            or project["id"] != expected_project_id
                        ):
                            raise BridgeProtocolError(
                                "Invalid span preflight response."
                            )
                        return {
                            "status": "ready",
                            "project": project,
                            "read_only": True,
                        }
                    if normalized.get("inspect_privacy"):
                        match = re.fullmatch(
                            r"privacy2_" + "_".join([r"(\d+)"] * 15),
                            token or "",
                        )
                        if not match or project["id"] != expected_project_id:
                            raise BridgeProtocolError("Invalid privacy inspection.")
                        return {
                            "project": project,
                            "privacy": dict(
                                zip(
                                    (
                                        "node_count",
                                        "output_connected",
                                        "local_start_frame",
                                        "local_end_frame",
                                        "strength_milli",
                                        "center_x_milli",
                                        "center_y_milli",
                                        "width_milli",
                                        "height_milli",
                                        "active_before",
                                        "active_first",
                                        "active_last",
                                        "active_after",
                                        "interval_count",
                                        "interval_checksum",
                                    ),
                                    map(int, match.groups()),
                                    strict=True,
                                )
                            ),
                            "source": "live_lua_api",
                        }
                    if normalized.get("inspect_circle"):
                        fusion = re.fullmatch(
                            r"fusion_(-?\d+)_(-?\d+)_(\d+)_(-?\d+)_(\d+)_(-?\d+)",
                            token or "",
                        )
                        if not fusion or project["id"] != expected_project_id:
                            raise BridgeProtocolError("Invalid Fusion inspection.")
                        return {
                            "project": project,
                            "fusion": dict(
                                zip(
                                    (
                                        "composition_count",
                                        "composition_names_count",
                                        "add_comp_callable",
                                        "node_count",
                                        "output_connected",
                                        "diameter_milli",
                                    ),
                                    map(int, fusion.groups()),
                                    strict=True,
                                )
                            ),
                            "source": "live_lua_api",
                        }
                    if "track_type" in normalized:
                        item_match = re.fullmatch(
                            r"item_(-?\d+)_(-?\d+)_(-?\d+)_(-?\d+)_(\d+)(?:_(-?\d+)_(-?\d+)_(-?\d+))?",
                            token or "",
                        )
                        if not item_match or project["id"] != expected_project_id:
                            raise BridgeProtocolError("Invalid item readback.")
                        return {
                            "project": project,
                            "selector": normalized,
                            "item": dict(
                                zip(
                                    (
                                        "start_frame",
                                        "end_frame",
                                        "source_start_frame",
                                        "source_end_frame",
                                        "linked_items",
                                    ),
                                    (int(v) for v in item_match.groups()[:5]),
                                    strict=True,
                                )
                            )
                            | (
                                dict(
                                    zip(
                                        (
                                            "source_start_time_microseconds",
                                            "source_end_time_microseconds",
                                            "left_offset_microframes",
                                        ),
                                        map(int, item_match.groups()[5:]),
                                        strict=True,
                                    )
                                )
                                if item_match[6] is not None
                                else {}
                            ),
                            "source": "live_lua_api",
                        }
                    match = re.fullmatch(
                        r"timeline_(-?\d+)_(-?\d+)_(-?\d+)_(-?\d+)_(-?\d+)_(-?\d+)_(-?\d+)_(-?\d+)",
                        token or "",
                    )
                    if not match or project["id"] != expected_project_id:
                        raise BridgeProtocolError("Invalid timeline summary.")
                    values = [int(v) for v in match.groups()]
                    fields = [
                        "video_items",
                        "audio_items",
                        "subtitle_items",
                        "start_frame",
                        "end_frame",
                        "subtitle_first_frame",
                        "subtitle_last_frame",
                        "fps_milli",
                    ]
                    return {
                        "project": project,
                        "timeline": normalized["timeline_name"],
                        "summary": dict(zip(fields, values, strict=True)),
                        "source": "live_lua_api",
                    }
                if action == "reload_modules":
                    return {"status": "reloaded", "owned_render_jobs_preserved": True}
                if status_request:
                    if project["id"] != expected_project_id or token not in {
                        "state_complete",
                        "state_failed",
                        "state_running",
                        "state_queued",
                        "state_unknown",
                    }:
                        raise BridgeProtocolError(
                            "Invalid render status acknowledgement."
                        )
                    assert owned_job is not None
                    result = {
                        "status": token[6:],
                        "job_id": normalized["job_id"],
                        "project": project,
                    }
                    if token == "state_complete":
                        result["output"] = verify_video(Path(owned_job["output_path"]))
                    return result
                if action == "ping":
                    return {
                        "message": "pong",
                        "transport": "experimental_free_marker"
                        if self.transport == "marker"
                        else "experimental_lua_snapshot",
                    }
                if action == "stop":
                    atomic_json(self.root / "stopped.json", {"session": self.session})
                    return {"status": "stopping"}
                return {"project": project, "source": "exported_snapshot"}
            if status_request and accepted_start:
                return {
                    "status": "awaiting_status",
                    "job_id": normalized["job_id"],
                    "last_confirmed_state": "accepted",
                    "completion_verified": False,
                    "reason": (
                        "No fresh status reply; rendering can block exports, "
                        "but the bridge may also be unavailable."
                    ),
                    "next_action": "Poll again; do not start the job again.",
                }
            raise LuaCommandTimeoutError(
                command_id, timeout_seconds, marker=self.transport == "marker"
            )
        except (OSError, MarkerTransportError):
            if (
                receipt is not None
                and marker_slot is not None
                and not resuming
                and not marker_slot.published
                and receipt.value.get("status") == "pending"
            ):
                # No immutable inbox was exposed: this is a known host failure,
                # not an uncertain native write. Retain its rejected receipt.
                receipt.reject("HOST_NOT_PUBLISHED")
            raise
        finally:
            try:
                if self.transport == "marker":
                    if marker_slot is not None and (acknowledged or not write):
                        marker_slot.clear()
                else:
                    (self.root / "request.lua").write_text(
                        "return nil\n", encoding="ascii"
                    )
                    pending.unlink(missing_ok=True)
                # Retain archives locally for diagnosis; never upload/commit them.
            finally:
                lock.unlink(missing_ok=True)
