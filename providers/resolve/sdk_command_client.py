"""Run canonical commands unchanged against a native SDK object adapter."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from agent.client import BridgeProtocolError, FilesystemCommandClient
from bridges.resolve import ResolveBridge as bridge
from providers.resolve.lua_editing import atomic_json
from providers.resolve.preset_channel import PresetSDKChannel
from providers.resolve.remote_sdk import RemoteSDK, SDKChannel
from providers.resolve.sdk_capabilities import augment_sdk_state


class SDKCommandClient(FilesystemCommandClient):
    """Preserve command validation, audit, receipts, backup and SDK readback.

    The coordinator must hold its operation lock for the complete request. The
    SDK ledger is durable before native dispatch and refuses uncertain replays.
    Native references and cached reads never escape one canonical operation.
    """

    def __init__(
        self,
        native_root: Path,
        session: str,
        *,
        runtime_root: Path | None = None,
        channel_factory: Callable[[dict[str, Any], float], SDKChannel] | None = None,
    ) -> None:
        self.native_root, self.session = native_root.resolve(), session
        super().__init__(runtime_root or self.native_root / "core")
        self.channel_factory = channel_factory or (
            lambda operation, timeout: PresetSDKChannel(
                self.native_root,
                self.session,
                operation,
                timeout_seconds=timeout,
            )
        )

    def _await_response(
        self,
        *,
        command_id: str,
        response_path: Path,
        timeout_seconds: float,
    ) -> Any:
        path = self._layout.commands / f"{command_id}.json"
        command = bridge.validate_command(json.loads(path.read_text(encoding="utf-8")))
        fingerprint = bridge._request_fingerprint(command)
        is_write = command["action"] in bridge.WRITE_ACTIONS
        records = self._layout.state / "sdk-operations"
        records.mkdir(exist_ok=True)
        if records.is_symlink():
            raise BridgeProtocolError("Linked SDK operation ledger is not accepted.")
        key = hashlib.sha256(command["idempotency_key"].encode()).hexdigest()
        ledger = records / f"{key}.json"
        if is_write and (ledger.exists() or ledger.is_symlink()):
            if ledger.is_symlink():
                raise BridgeProtocolError("Linked SDK receipt is not accepted.")
            original = json.loads(ledger.read_text(encoding="utf-8"))
            if (
                original.get("fingerprint") != fingerprint
                or original.get("session") != self.session
            ):
                raise BridgeProtocolError(
                    "SDK key belongs to its original request/session."
                )
            if original.get("phase") == "completed":
                replay = dict(original["response"])
                replay["command_id"] = command_id
                atomic_json(response_path, replay)
                path.unlink()
                return self._read_response(command_id, response_path)
            raise BridgeProtocolError(
                "An SDK write is pending or uncertain; no new native call was sent."
            )
        operation = {
            "id": UUID(command_id).hex,
            "fingerprint": fingerprint,
            "action": command["action"],
            "create_backup": command["safety"]["create_backup"],
            "allow_destructive": command["safety"]["allow_destructive"],
            "expires": int(
                datetime.fromisoformat(
                    command["expires_at"].replace("Z", "+00:00")
                ).timestamp()
            ),
        }
        record = {
            "session": self.session,
            "fingerprint": fingerprint,
            "command_id": command_id,
            "phase": "dispatch_started",
        }
        if is_write:
            atomic_json(ledger, record)
        directories = bridge.ensure_runtime_directories(self._layout.root)
        processing = self._layout.processing / path.name
        path.rename(processing)
        try:
            sdk = RemoteSDK(self.channel_factory(operation, timeout_seconds))
            native = sdk.root()
            state = bridge.collect_bridge_state(native)
            bridge._apply_verified_capabilities(state, directories["state"])
            augment_sdk_state(
                state,
                native,
                directories["state"],
                discover=command["action"] == "get_capabilities",
            )
            bridge.process_command_file(
                processing,
                response_path,
                self._layout.failed / path.name,
                state,
                native,
                directories,
            )
            if is_write:
                response = json.loads(response_path.read_text(encoding="utf-8"))
                if response["status"] != "success" and sdk.mutations_sent:
                    raise BridgeProtocolError(
                        "SDK write failed after dispatch; original evidence retained."
                    )
                record.update(
                    phase="completed",
                    response=response,
                )
                atomic_json(ledger, record)
            # The canonical command has already backed up, read back and
            # persisted its outcome. Optional discovery/state refresh must not
            # turn that known result into an uncertain write or a lost response.
            try:
                augment_sdk_state(state, native, directories["state"])
                bridge.publish_bridge_state(
                    directories["state"] / "bridge.json",
                    state,
                    directories["logs"] / "bridge.jsonl",
                )
            except Exception:
                logging.getLogger(__name__).exception("SDK state refresh failed")
            return self._read_response(command_id, response_path)
        except Exception:
            if is_write and record["phase"] != "completed":
                record["phase"] = "uncertain"
                atomic_json(ledger, record)
            raise
