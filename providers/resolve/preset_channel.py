"""One serialized, data-only request slot for the native SDK adapter."""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.client import BridgeProtocolError, CommandTimeoutError
from bridges.resolve.ResolveBridge import ALLOWED_ACTIONS, WRITE_ACTIONS
from providers.resolve.lua_editing import atomic_json
from providers.resolve.marker_transport import MarkerSlot
from providers.resolve.preset_envelope import (
    name_from_preset,
    read_payload,
)


class PresetSDKChannel:
    """Caller holds the managed operation lock for the complete SDK operation.

    A timeout preserves the request and its trace. It never publishes a second
    request or repeats an SDK mutation. The ready export follows the completed
    result export, so its header acknowledges a closed result file.
    """

    def __init__(
        self,
        root: Path,
        session: str,
        operation: dict[str, Any],
        *,
        timeout_seconds: float = 30,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("SDK timeout must be positive and finite.")
        self.root = root.resolve()
        self.session, self.operation = session, operation
        self.timeout_seconds = timeout_seconds
        template = self.root / "sdk-template.preset"
        if template.is_symlink():
            raise BridgeProtocolError("Linked SDK template is not accepted.")
        self.template = template.read_bytes()
        if name_from_preset(self.template) != "DRA_TEMPLATE_" + session:
            raise BridgeProtocolError("SDK template belongs to another session.")
        self.trace = self.root / "sdk-trace"
        if self.trace.is_symlink():
            raise BridgeProtocolError("Linked SDK trace directory is not accepted.")
        self.trace.mkdir(exist_ok=True)
        self.packets = self.root / "sdk-rpc"
        if self.packets.is_symlink():
            raise BridgeProtocolError("Linked SDK packet directory is not accepted.")
        self.packets.mkdir(exist_ok=True)

    def _recover_expired_read(self) -> None:
        """Release only an exact expired packet from a read-only operation.

        Lua cannot mutate project content under a read action. A delayed getter
        finishes before the next packet on that same native loop. Write scopes
        remain fenced even when their last individual RPC was a getter.
        """
        owner_path = self.packets / "inbox/.owner.json"
        if owner_path.is_symlink() or owner_path.stat().st_size > 2048:
            raise BridgeProtocolError("Invalid pending SDK packet ownership.")
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        if owner.get("session") != self.session:
            raise BridgeProtocolError("Pending SDK packet belongs to another session.")
        for trace in self.trace.glob("*.json"):
            if trace.is_symlink() or trace.stat().st_size > 4 * 1024 * 1024:
                raise BridgeProtocolError("Invalid SDK trace record.")
            record = json.loads(trace.read_text(encoding="utf-8"))
            if record.get("message_id") != owner.get("id"):
                continue
            packet = record.get("packet")
            operation = record.get("operation", {})
            if (
                record.get("session") != self.session
                or operation.get("action") not in ALLOWED_ACTIONS - WRITE_ACTIONS
                or not isinstance(packet, dict)
                or type(packet.get("expires")) is not int
                or packet["expires"] >= time.time()
            ):
                return
            slot = MarkerSlot.existing(self.packets, self.session, owner["id"], 4096)
            if slot.request != packet:
                raise BridgeProtocolError("SDK trace differs from its native packet.")
            slot.clear()
            record["phase"] = "expired_read"
            atomic_json(trace, record)
            return

    def exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        identity = uuid4().hex
        request = {"operation": self.operation, "call": payload}
        encoded = json.dumps(
            request,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request_hex = encoded.hex()
        inbox = self.packets / "inbox"
        output = self.root / f"sdk-{identity}.preset"
        ready = self.root / f"sdk-{identity}.ready.preset"
        trace = self.trace / (identity + ".json")
        record: dict[str, Any] = {
            "session": self.session,
            "id": identity,
            "operation": self.operation,
            "call": payload,
            "request_sha256": hashlib.sha256(encoded).hexdigest(),
            "phase": "prepared",
        }
        if inbox.exists() or inbox.is_symlink():
            self._recover_expired_read()
        if inbox.exists() or inbox.is_symlink():
            raise BridgeProtocolError(
                "An SDK request is already pending; no new call sent."
            )
        atomic_json(trace, record)
        deadline = time.monotonic() + self.timeout_seconds
        parts = [
            encoded[i : i + 2000].decode("ascii") for i in range(0, len(encoded), 2000)
        ]
        slot: MarkerSlot | None = None
        for index, part in enumerate(parts, 1):
            message_id = uuid4().hex
            packet = {
                "session": self.session,
                "id": message_id,
                "action": "ping",
                "expires": math.ceil(time.time() + self.timeout_seconds + 2),
                "arguments": {
                    "sdk": {
                        "id": identity,
                        "seq": index,
                        "count": len(parts),
                        "data": part,
                    }
                },
            }
            record.update(phase="dispatch_started", message_id=message_id, part=index)
            record["packet"] = packet
            atomic_json(trace, record)
            slot = MarkerSlot(self.packets, packet, 4096)
            slot.publish(deadline)
            if index < len(parts):
                ack = self.root / f"sdk-ack-{message_id}.preset"
                while time.monotonic() < deadline:
                    if ack.is_file():
                        try:
                            if name_from_preset(ack.read_bytes()) == (
                                f"DRA_SDK_ACK_{self.session}_{message_id}"
                            ):
                                break
                        except (OSError, BridgeProtocolError):
                            pass
                    time.sleep(0.025)
                else:
                    raise CommandTimeoutError(identity, self.timeout_seconds)
                slot.clear()
                ack.unlink()
        assert slot is not None
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if ready.is_file() and output.is_file():
                try:
                    acknowledgement = read_payload(
                        ready,
                        session=self.session,
                        command_id=identity,
                        direction="O",
                    )
                    response = read_payload(
                        output,
                        session=self.session,
                        command_id=identity,
                        direction="O",
                    )
                    if (
                        response != acknowledgement
                        or response.get("request_hex") != request_hex
                    ):
                        raise BridgeProtocolError(
                            "Native SDK acknowledgement differs from its request."
                        )
                    record.update(phase="completed", response=response)
                    atomic_json(trace, record)
                    slot.clear()
                    output.unlink()
                    ready.unlink()
                    return response
                except (OSError, BridgeProtocolError) as error:
                    last_error = error
            time.sleep(0.025)
        record["phase"] = "uncertain"
        if last_error is not None:
            record["last_error"] = type(last_error).__name__
        atomic_json(trace, record)
        raise CommandTimeoutError(identity, self.timeout_seconds)
