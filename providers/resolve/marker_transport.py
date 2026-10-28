"""Bounded data-only marker mailbox and renewable host lease for Free Resolve.

The native side reads fixed file-presence bits. It never evaluates payload code.
All paths here belong to a newly prepared private runtime; foreign slots and
uncertain writes are retained for explicit reconciliation.
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import threading
import time
import zlib
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

WIRE_VERSION = 1
MAX_PAYLOAD_BYTES = 4096
DEFAULT_LEASE_SECONDS = 600
HEADER = struct.Struct("<BBHI")
LEASE = struct.Struct("<II")
MAGIC = 0x4D
_IDENTITY = re.compile("[0-9a-f]{32}")
_MARKER = re.compile("b([0-9]{5})_([0-7])[.]bit")


class MarkerTransportError(RuntimeError):
    """The marker mailbox cannot be used safely in its current state."""


class MarkerSlotBusyError(MarkerTransportError):
    """Another or uncertain request still owns the immutable inbox."""


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        raise ValueError(f"Invalid marker {label}.")
    return value


def _payload_limit(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= MAX_PAYLOAD_BYTES:
        raise ValueError(f"Marker payload limit must be 1..{MAX_PAYLOAD_BYTES}.")
    return value


def _lease_limit(value: Any) -> int:
    if type(value) is not int or not 5 <= value <= 3600:
        raise ValueError("Marker lease must be 5..3600 seconds.")
    return value


def validate_marker_metadata(metadata: dict[str, Any]) -> None:
    """Validate only marker-specific session policy; keep protocol 4 contracts."""
    _identity(metadata.get("session"), "session")
    if metadata.get("protocol") != 4 or metadata.get("transport") != "marker":
        raise ValueError("Marker transport requires a protocol-4 marker session.")
    if metadata.get("marker_protocol") != WIRE_VERSION:
        raise ValueError("Unsupported marker wire protocol.")
    _payload_limit(metadata.get("max_payload_bytes"))
    _lease_limit(metadata.get("lease_seconds"))


def _metadata(root: Path) -> dict[str, Any]:
    path = root / "session.json"
    if path.stat().st_size > 65536:
        raise ValueError("Marker session metadata is too large.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid marker session metadata.")
    validate_marker_metadata(value)
    return value


def _validate_envelope(value: dict[str, Any]) -> None:
    required = {"session", "id", "action", "expires"}
    optional = {"confirm", "project_id", "arguments"}
    if not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("Invalid marker request fields.")
    _identity(value["session"], "session")
    _identity(value["id"], "request ID")
    if not isinstance(value["action"], str) or not re.fullmatch(
        "[a-z_]{1,64}", value["action"]
    ):
        raise ValueError("Invalid marker action.")
    if type(value["expires"]) is not int or not 0 < value["expires"] <= 0xFFFFFFFF:
        raise ValueError("Invalid marker expiry.")
    if "confirm" in value and type(value["confirm"]) is not bool:
        raise ValueError("Invalid marker confirmation.")
    if "project_id" in value and not isinstance(value["project_id"], str):
        raise ValueError("Invalid marker project identity.")
    if "arguments" in value and not isinstance(value["arguments"], dict):
        raise ValueError("Marker arguments must be an object.")


def _json_shape(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("Marker JSON nesting exceeds 32 levels.")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("Marker JSON object keys must be strings.")
            _json_shape(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            if child is None:
                raise ValueError("Marker JSON arrays cannot contain null.")
            _json_shape(child, depth + 1)


def encode_request(
    request: dict[str, Any], max_payload_bytes: int = MAX_PAYLOAD_BYTES
) -> bytes:
    """Encode an already typed/validated request as bounded UTF-8 JSON data."""
    limit = _payload_limit(max_payload_bytes)
    _validate_envelope(request)
    _json_shape(request)
    payload = json.dumps(
        request,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if not 1 <= len(payload) <= limit:
        raise ValueError(f"Marker request exceeds {limit} payload bytes.")
    return (
        HEADER.pack(MAGIC, WIRE_VERSION, len(payload), zlib.adler32(payload)) + payload
    )


def _owner(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_dir():
        raise MarkerTransportError("Marker slot is not an owned directory.")
    manifest = path / ".owner.json"
    if manifest.is_symlink() or manifest.stat().st_size > 2048:
        raise MarkerTransportError("Marker ownership metadata is invalid.")
    result = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise MarkerTransportError("Marker ownership metadata must be an object.")
    return result


def _owned_directory(path: Path, root: Path, owner: dict[str, Any]) -> None:
    if path.parent.resolve() != root or _owner(path) != owner:
        raise MarkerTransportError("Refusing a foreign marker slot.")
    for child in path.iterdir():
        marker = _MARKER.fullmatch(child.name)
        allowed = child.name in {"ready.marker", ".owner.json"} or (
            marker is not None and int(marker[1]) < owner["packet_bytes"]
        )
        if not allowed or child.is_symlink() or not child.is_file():
            raise MarkerTransportError("Refusing unknown content in marker slot.")


def _remove_owned(path: Path, root: Path, owner: dict[str, Any]) -> None:
    """Remove exact owned files only; never recursively traverse a directory."""
    if not path.exists():
        return
    _owned_directory(path, root, owner)
    (path / "ready.marker").unlink(missing_ok=True)
    for child in path.iterdir():
        if child.name != ".owner.json":
            child.unlink()
    # Keep ownership evidence if a transient Windows lock interrupts deletion.
    (path / ".owner.json").unlink()
    path.rmdir()


def _clear_recorded(
    root: Path,
    session: str,
    identity: str,
    expected_owner: dict[str, Any] | None = None,
) -> None:
    _identity(identity, "request ID")
    retired = root / f".inbox-retired-{identity}"
    for path in (root / "inbox", retired):
        if not path.exists() and not path.is_symlink():
            continue
        # An empty retired directory contains no foreign data to remove and
        # may remain after its final rmdir was temporarily denied on Windows.
        if (
            path == retired
            and not path.is_symlink()
            and path.is_dir()
            and not any(path.iterdir())
        ):
            path.rmdir()
            continue
        owner = _owner(path)
        if path.name == "inbox" and owner.get("id") != identity:
            continue  # Never clear a newer or foreign request on cached replay.
        size = owner.get("packet_bytes")
        if (
            owner.get("session") != session
            or owner.get("id") != identity
            or owner.get("kind") != "request"
            or type(size) is not int
            or not HEADER.size < size <= HEADER.size + MAX_PAYLOAD_BYTES
            or expected_owner is not None
            and owner != expected_owner
        ):
            raise MarkerTransportError("Refusing foreign completed-slot content.")
        _owned_directory(path, root, owner)
        if path.name == "inbox":
            if retired.exists() or retired.is_symlink():
                raise MarkerTransportError("Retired marker slot is already occupied.")
            path.rename(retired)
            path = retired
        _remove_owned(path, root, owner)


def _clear_with_retry(
    root: Path,
    session: str,
    identity: str,
    expected_owner: dict[str, Any] | None = None,
) -> None:
    for attempt in range(6):
        try:
            _clear_recorded(root, session, identity, expected_owner)
            return
        except PermissionError:
            # Windows can briefly retain a handle after stat/unlink. Recheck
            # exact ownership on every retry; never hide a persistent failure.
            if attempt == 5:
                raise
            time.sleep(0.025 * 2**attempt)


def _stage_bits(root: Path, name: str, packet: bytes, owner: dict[str, Any]) -> Path:
    stage = root / name
    stage.mkdir(exist_ok=False)
    (stage / ".owner.json").write_text(json.dumps(owner), encoding="ascii")
    try:
        for index, byte in enumerate(packet):
            for bit in range(8):
                if byte & (1 << bit):
                    (stage / f"b{index:05d}_{bit}.bit").touch(exist_ok=False)
        (stage / "ready.marker").touch(exist_ok=False)
    except BaseException:
        _remove_owned(stage, root, owner)
        raise
    return stage


class MarkerSlot:
    """Prepare immutable request data without performing publication yet."""

    def __init__(
        self, root: Path, request: dict[str, Any], max_payload_bytes: int
    ) -> None:
        self.root = root.resolve()
        self.packet = encode_request(request, max_payload_bytes)
        self.request = json.loads(self.packet[HEADER.size :])
        self.identity = request["id"]
        self.owner = {
            "kind": "request",
            "session": request["session"],
            "id": self.identity,
            "action": request["action"],
            "expires": request["expires"],
            "packet_bytes": len(self.packet),
        }
        self.path = self.root / "inbox"
        self.published = False

    @classmethod
    def existing(
        cls, root: Path, session: str, identity: str, max_payload_bytes: int
    ) -> MarkerSlot:
        """Validate the original immutable data for acknowledgement-only recovery."""
        root = root.resolve()
        owner = _owner(root / "inbox")
        if (
            owner.get("session") != session
            or owner.get("id") != identity
            or owner.get("kind") != "request"
        ):
            raise MarkerTransportError("Pending marker slot identity mismatch.")
        length = owner.get("packet_bytes")
        if type(
            length
        ) is not int or not HEADER.size < length <= HEADER.size + _payload_limit(
            max_payload_bytes
        ):
            raise MarkerTransportError("Pending marker slot length is invalid.")
        path = root / "inbox"
        _owned_directory(path, root, owner)
        if not (path / "ready.marker").is_file():
            raise MarkerTransportError("Pending marker slot has no ready marker.")
        packet = bytes(
            sum(
                1 << bit
                for bit in range(8)
                if (path / f"b{index:05d}_{bit}.bit").is_file()
            )
            for index in range(length)
        )
        magic, version, payload_length, checksum = HEADER.unpack(packet[: HEADER.size])
        payload = packet[HEADER.size :]
        if (
            magic != MAGIC
            or version != WIRE_VERSION
            or payload_length != len(payload)
            or zlib.adler32(payload) != checksum
        ):
            raise MarkerTransportError(
                "Pending marker packet failed integrity validation."
            )
        request = json.loads(payload.decode("utf-8"))
        if not isinstance(request, dict):
            raise MarkerTransportError("Pending marker request is not an object.")
        result = cls(root, request, max_payload_bytes)
        if result.packet != packet or result.owner != owner:
            raise MarkerTransportError("Pending marker packet differs from its owner.")
        result.published = True
        return result

    @classmethod
    def occupied(cls, root: Path, session: str, max_payload_bytes: int) -> MarkerSlot:
        """Decode an occupied slot; owner claims alone never authorize cleanup."""
        owner = _owner(root.resolve() / "inbox")
        identity = _identity(owner.get("id"), "request ID")
        return cls.existing(root, session, identity, max_payload_bytes)

    def ensure_available(self) -> None:
        """Reject a known busy slot before a new write receipt is begun."""
        if self.path.exists() or self.path.is_symlink():
            raise MarkerSlotBusyError(
                "Marker inbox remains occupied; reconcile its uncertain "
                "owner/response first."
            )

    def publish(self, deadline: float) -> None:
        """Atomically expose a complete slot; never replace an occupied inbox."""
        self.ensure_available()
        stage = _stage_bits(
            self.root, f".inbox-{self.identity}.tmp", self.packet, self.owner
        )
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Marker deadline elapsed before publication; "
                        "no native request was exposed."
                    )
                if self.path.exists() or self.path.is_symlink():
                    raise MarkerSlotBusyError("Another request owns the marker inbox.")
                try:
                    stage.rename(self.path)
                    self.published = True
                    return
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(min(0.025, max(0, deadline - time.monotonic())))
        finally:
            if stage.exists():
                _remove_owned(stage, self.root, self.owner)

    def clear(self) -> None:
        """Release this request after acknowledgement or a safe read timeout."""
        if self.published:
            _clear_with_retry(
                self.root, self.owner["session"], self.identity, self.owner
            )

    @staticmethod
    def clear_completed(root: Path, session: str, identity: str) -> None:
        """Retry only original owned cleanup after a durable completed receipt."""
        _clear_with_retry(root.resolve(), session, identity)


def publish_lease(root: Path) -> int:
    """Publish eight data bytes; Lua retains its last valid deadline across rotation."""
    root = root.resolve()
    metadata = _metadata(root)
    expires = math.ceil(time.time()) + _lease_limit(metadata["lease_seconds"])
    if expires > 0xFFFFFFFF:
        raise ValueError("Marker lease timestamp is out of range.")
    stamp = struct.pack("<I", expires)
    packet = stamp + struct.pack("<I", zlib.adler32(stamp))
    identity = uuid4().hex
    owner = {
        "kind": "lease",
        "session": metadata["session"],
        "id": identity,
        "packet_bytes": LEASE.size,
    }
    lock = root / "lease-publish.lock"
    with lock.open("x", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    stage: Path | None = None
    retired: Path | None = None
    previous_owner: dict[str, Any] | None = None
    target = root / "lease"
    try:
        stage = _stage_bits(root, f".lease-{identity}.tmp", packet, owner)
        if target.exists() or target.is_symlink():
            previous_owner = _owner(target)
            if (
                previous_owner.get("session") != metadata["session"]
                or previous_owner.get("kind") != "lease"
                or previous_owner.get("packet_bytes") != LEASE.size
            ):
                raise MarkerTransportError("Refusing a foreign lease slot.")
            _owned_directory(target, root, previous_owner)
            retired = root / f".lease-retired-{identity}"
            target.rename(retired)
        try:
            stage.rename(target)
            stage = None
        except BaseException:
            if retired is not None and not target.exists():
                retired.rename(target)
                retired = None
            raise
        if retired is not None and previous_owner is not None:
            _remove_owned(retired, root, previous_owner)
            retired = None
        return expires
    finally:
        try:
            if stage is not None and stage.exists():
                _remove_owned(stage, root, owner)
        finally:
            lock.unlink(missing_ok=True)


def inspect_pending(root: Path) -> dict[str, Any]:
    """Return local recovery diagnostics; these do not establish native liveness."""
    root = root.resolve()
    metadata = _metadata(root)
    pending = []
    for path in sorted((root / "receipts").glob("*.json")):
        if path.stat().st_size > 65536:
            raise MarkerTransportError("Receipt exceeds the diagnostic size bound.")
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("status") == "pending":
            pending.append(
                {"receipt_id": path.stem, "command_id": saved.get("command_id")}
            )
            if len(pending) == 128:
                break
    inbox = None
    if (root / "inbox").exists() or (root / "inbox").is_symlink():
        owner = _owner(root / "inbox")
        inbox = {key: owner.get(key) for key in ("id", "action", "expires")} | {
            "session_matches": owner.get("session") == metadata["session"]
        }
    return {
        "transport": "marker",
        "pending_receipts": pending,
        "inbox": inbox,
        "client_lock_present": (root / "client.lock").exists(),
        "native_session_consumed": (root / "marker-session-consumed.drp").exists(),
        "native_exit_exports": [
            path.name for path in sorted(root.glob("marker-exit-*.drp"))
        ],
        "liveness": "unverified; requires a fresh correlated native response",
        "recovery": (
            "Repeat the same write with the same arguments and idempotency key "
            "to inspect its original acknowledgement; no request is republished."
        ),
    }


class MarkerLeaseKeeper:
    """Keep an already bootstrapped native loop leased while an MCP client lives.

    Host renewal is not native liveness evidence. Fresh native acknowledgements
    remain the authority for connection health.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        metadata = _metadata(self.root)
        self.interval = min(30.0, metadata["lease_seconds"] / 3)
        self.last_error: str | None = None
        self.expires: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def renew(self) -> None:
        try:
            self.expires = publish_lease(self.root)
            self.last_error = None
        except FileExistsError:
            # A concurrent publisher owns this short rotation, not a request.
            return
        except (OSError, ValueError, MarkerTransportError) as error:
            self.last_error = type(error).__name__
            raise

    def __enter__(self) -> MarkerLeaseKeeper:
        if self._thread is not None:
            raise RuntimeError("Lease keeper is already started.")
        self.renew()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            if (self.root / "stopped.json").exists():
                return
            try:
                self.renew()
            except (OSError, ValueError, MarkerTransportError):
                # Keep diagnostics, let the finite native lease fail closed.
                continue

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def prepare_marker(
    root: Path,
    media_roots: list[Path] | None = None,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> Path:
    """Prepare a new protocol-4 marker session and its fixed self-contained script."""
    _lease_limit(lease_seconds)
    _payload_limit(max_payload_bytes)
    from providers.resolve.lua_marker_bundle import build_marker_bridge
    from providers.resolve.lua_transport import prepare

    prepare(root, media_roots)
    root = root.resolve()
    metadata_path = root / "session.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update(
        transport="marker",
        marker_protocol=WIRE_VERSION,
        max_payload_bytes=max_payload_bytes,
        lease_seconds=lease_seconds,
        poll_interval_seconds=0.25,
    )
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    script = root / "marker-bridge.lua"
    script.write_text(build_marker_bridge(root), encoding="utf-8")
    publish_lease(root)
    return script
