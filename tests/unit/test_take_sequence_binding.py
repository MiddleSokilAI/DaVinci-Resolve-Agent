"""M55.1 approved take-sequence source-binding tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from agent.contracts import validate_contract
from agent.media import MediaPolicy
from agent.take_selection import media_file_identity
from agent.take_sequence_binding import (
    TakeSequenceBindingError,
    TakeSequenceBindingWorkflow,
)


class StubSequenceReader:
    def __init__(self, sequence: dict[str, Any]) -> None:
        self._sequence = sequence

    def get(self, sequence_id: str) -> dict[str, Any]:
        assert sequence_id == self._sequence["sequence_id"]
        return self._sequence


def test_binding_validates_exact_sources_and_redacts_public_receipt(
    tmp_path: Path,
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    source = media / "take.mkv"
    source.write_bytes(b"approved source")
    sequence = _sequence(source)
    workflow = TakeSequenceBindingWorkflow(
        StubSequenceReader(sequence),
        MediaPolicy([media]),
        tmp_path / "bindings",
    )

    first = workflow.bind(
        sequence_id=sequence["sequence_id"],
        sources=[{"order": 1, "path": str(source)}],
    )
    replay = workflow.bind(
        sequence_id=sequence["sequence_id"],
        sources=[{"order": 1, "path": str(source)}],
    )

    assert first == replay
    assert first["status"] == "bound"
    assert first["paths_redacted"] is True
    assert first["timeline_modified"] is False
    assert first["apply_supported"] is False
    assert str(tmp_path) not in json.dumps(first)
    validate_contract("take-sequence-binding", first)
    assert workflow.get(first["binding_id"]) == first
    private_sources = workflow.resolve_sources(first["binding_id"])
    assert private_sources[0]["path"] == str(source.resolve())


def test_binding_rejects_file_that_differs_from_approved_identity(
    tmp_path: Path,
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    approved = media / "take.mkv"
    replacement = media / "replacement.mkv"
    approved.write_bytes(b"approved")
    replacement.write_bytes(b"different")
    sequence = _sequence(approved)
    workflow = TakeSequenceBindingWorkflow(
        StubSequenceReader(sequence),
        MediaPolicy([media]),
        tmp_path / "bindings",
    )

    with pytest.raises(TakeSequenceBindingError, match="does not match"):
        workflow.bind(
            sequence_id=sequence["sequence_id"],
            sources=[{"order": 1, "path": str(replacement)}],
        )


def test_binding_get_rejects_tampered_private_artifact(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    source = media / "take.mkv"
    source.write_bytes(b"approved")
    sequence = _sequence(source)
    root = tmp_path / "bindings"
    workflow = TakeSequenceBindingWorkflow(
        StubSequenceReader(sequence),
        MediaPolicy([media]),
        root,
    )
    binding = workflow.bind(
        sequence_id=sequence["sequence_id"],
        sources=[{"order": 1, "path": str(source)}],
    )
    private_path = root / f"{binding['binding_id']}.private.json"
    private = json.loads(private_path.read_text(encoding="utf-8"))
    private["sources"][0]["path"] = str(media / "other.mkv")
    private_path.write_text(json.dumps(private), encoding="utf-8")

    with pytest.raises(TakeSequenceBindingError, match="invalid"):
        workflow.get(binding["binding_id"])


def test_binding_resolve_rejects_source_changed_after_binding(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    source = media / "take.mkv"
    source.write_bytes(b"approved")
    sequence = _sequence(source)
    workflow = TakeSequenceBindingWorkflow(
        StubSequenceReader(sequence),
        MediaPolicy([media]),
        tmp_path / "bindings",
    )
    binding = workflow.bind(
        sequence_id=sequence["sequence_id"],
        sources=[{"order": 1, "path": str(source)}],
    )
    source.write_bytes(b"changed")

    with pytest.raises(TakeSequenceBindingError, match="changed"):
        workflow.resolve_sources(binding["binding_id"])


def test_binding_rejects_same_size_middle_change_with_unchanged_mtime(
    tmp_path: Path,
) -> None:
    source = tmp_path / "take.mkv"
    source.write_bytes(b"a" * (4 * 1_048_576))
    sequence = _sequence(source)
    workflow = TakeSequenceBindingWorkflow(
        StubSequenceReader(sequence), MediaPolicy([tmp_path]), tmp_path / "bindings"
    )
    binding = workflow.bind(
        sequence_id=sequence["sequence_id"],
        sources=[{"order": 1, "path": str(source)}],
    )
    original = source.stat()
    with source.open("r+b") as stream:
        stream.seek(2 * 1_048_576)
        stream.write(b"b")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert source.stat().st_size == original.st_size
    with pytest.raises(TakeSequenceBindingError, match="changed"):
        workflow.resolve_sources(binding["binding_id"])


def test_legacy_edge_hash_approval_requires_new_analysis(tmp_path: Path) -> None:
    source = tmp_path / "take.mkv"
    source.write_bytes(b"approved source")
    sequence = _sequence(source)
    legacy = hashlib.sha256(str(source.stat().st_size).encode() + source.read_bytes())
    sequence["entries"][0]["fingerprint"] = legacy.hexdigest()
    workflow = TakeSequenceBindingWorkflow(
        StubSequenceReader(sequence), MediaPolicy([tmp_path]), tmp_path / "bindings"
    )
    with pytest.raises(TakeSequenceBindingError, match="does not match"):
        workflow.bind(
            sequence_id=sequence["sequence_id"],
            sources=[{"order": 1, "path": str(source)}],
        )


def _sequence(source: Path) -> dict[str, Any]:
    identity = media_file_identity(source)
    return {
        "sequence_version": "1.0",
        "sequence_id": "a" * 64,
        "status": "approved_plan",
        "sequence_name": "Approved",
        "entries": [
            {
                "order": 1,
                "selection_id": "b" * 64,
                "selection_sha256": "c" * 64,
                "review_id": "d" * 64,
                "review_sha256": "e" * 64,
                "selected_candidate_id": "take-a",
                **identity,
                "duration_seconds": 10.0,
                "source_range": {
                    "start_seconds": 10.0,
                    "end_seconds": 20.0,
                },
                "technical_score": 80.0,
            }
        ],
        "total_duration_seconds": 10.0,
        "timeline_modified": False,
        "apply_supported": False,
    }
