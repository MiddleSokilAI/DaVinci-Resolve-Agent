"""Bounded local M54 technical take analysis with a mandatory review gate."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Final, Protocol

import av
import numpy as np

from agent.contracts import validate_contract
from agent.media import MediaPolicy
from agent.paths import (
    take_selection_reviews_directory,
    take_selections_directory,
)
from transports.filesystem import atomic_write_json, read_json_object

SELECTION_VERSION: Final = "1.0"
SEGMENT_SELECTION_VERSION: Final = "1.1"
SCRIPTED_SELECTION_VERSION: Final = "1.2"
REVIEW_VERSION: Final = "1.0"
POLICY_ID: Final = "technical-take-v1"
SCRIPTED_POLICY_ID: Final = "script-aware-take-v1"
CANDIDATE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
VIDEO_SAMPLE_FRACTIONS: Final = (0.08, 0.22, 0.36, 0.5, 0.64, 0.78, 0.92)
AUDIO_SAMPLE_LIMIT: Final = 480_000
MAX_SEGMENT_SECONDS: Final = 300.0
MAX_REFERENCE_TEXT_LENGTH: Final = 20_000
POLICY: Final[dict[str, Any]] = {
    "policy_id": POLICY_ID,
    "sampling": {
        "video_fraction_count": len(VIDEO_SAMPLE_FRACTIONS),
        "audio_sample_limit": AUDIO_SAMPLE_LIMIT,
        "fingerprint_algorithm": "sha256-full-file-v2",
    },
    "weights": {"video": 0.75, "audio": 0.25},
}


class TakeSelectionError(ValueError):
    """Raised when take analysis or review violates the M54 boundary."""


class LocalMediaPolicy(Protocol):
    """Existing local media-root validation boundary used by M54."""

    def validate_files(self, paths: list[str]) -> list[str]: ...


class DialogueTranscriber(Protocol):
    """Bounded local transcript provider used by script-aware M54 analysis."""

    def transcribe(
        self,
        source_path: Path,
        *,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
    ) -> dict[str, Any]: ...


class TakeSelectionWorkflow:
    """Analyze local candidates and persist immutable human-review artifacts."""

    def __init__(
        self,
        *,
        media_policy: LocalMediaPolicy | None = None,
        transcriber: DialogueTranscriber | None = None,
        selections_root: Path | None = None,
        reviews_root: Path | None = None,
    ) -> None:
        self._media_policy = media_policy or MediaPolicy.from_local_config()
        self._transcriber = transcriber
        self._selections_root = selections_root or take_selections_directory()
        self._reviews_root = reviews_root or take_selection_reviews_directory()

    def analyze(
        self,
        *,
        selection_name: str,
        candidates: list[dict[str, str | float]],
    ) -> dict[str, Any]:
        """Measure two to eight local candidates without modifying Resolve."""
        name = _selection_name(selection_name)
        normalized = _candidate_inputs(candidates)
        paths = self._media_policy.validate_files(
            [candidate["path"] for candidate in normalized]
        )
        segment_mode = "source_range" in normalized[0]
        measured = [
            _analyze_file(
                Path(path),
                str(candidate["candidate_id"]),
                candidate.get("source_range"),
            )
            for candidate, path in zip(normalized, paths, strict=True)
        ]
        ranked = sorted(
            measured,
            key=lambda item: (-item["technical_score"], item["candidate_id"]),
        )
        for rank, candidate in enumerate(ranked, start=1):
            candidate["rank"] = rank
        score_gap = round(
            ranked[0]["technical_score"] - ranked[1]["technical_score"], 3
        )
        confidence = "high" if score_gap >= 10 else (
            "medium" if score_gap >= 4 else "low"
        )
        payload = {
            "selection_version": (
                SEGMENT_SELECTION_VERSION if segment_mode else SELECTION_VERSION
            ),
            "status": "pending_review",
            "selection_name": name,
            "policy": _policy(segment_mode),
            "candidates": ranked,
            "recommendation": {
                "candidate_id": ranked[0]["candidate_id"],
                "confidence": confidence,
                "score_gap": score_gap,
                "basis": "bounded technical measurements only",
                "limitations": _limitations(segment_mode),
            },
            "review_required": True,
        }
        result = {**payload, "selection_id": _canonical_sha256(payload)}
        validate_contract("take-selection", result)
        self._selections_root.mkdir(parents=True, exist_ok=True)
        path = self._selections_root / f"{result['selection_id']}.json"
        if path.is_file():
            existing = read_json_object(path)
            if existing != result:
                raise TakeSelectionError(
                    "Stored take selection does not match deterministic analysis."
                )
            return existing
        atomic_write_json(path, result)
        return result

    def analyze_scripted(
        self,
        *,
        selection_name: str,
        candidates: list[dict[str, str | float]],
        reference_text: str,
    ) -> dict[str, Any]:
        """Rank bounded dialogue takes against one caller-provided script."""
        if self._transcriber is None:
            raise TakeSelectionError(
                "Script-aware take analysis requires a local transcriber."
            )
        name = _selection_name(selection_name)
        reference_tokens = _reference_tokens(reference_text)
        reference_payload = {
            "sha256": hashlib.sha256(
                reference_text.strip().encode("utf-8")
            ).hexdigest(),
            "token_count": len(reference_tokens),
        }
        normalized = _candidate_inputs(candidates)
        if "source_range" not in normalized[0]:
            raise TakeSelectionError(
                "Script-aware candidates require bounded source ranges."
            )
        paths = self._media_policy.validate_files(
            [candidate["path"] for candidate in normalized]
        )
        request_id = _scripted_request_id(
            name,
            normalized,
            paths,
            reference_payload,
        )
        request_root = self._selections_root / "scripted-requests"
        request_path = request_root / f"{request_id}.json"
        if request_path.is_file():
            request = read_json_object(request_path)
            selection_id = request.get("selection_id")
            if (
                request.get("request_id") != request_id
                or not isinstance(selection_id, str)
            ):
                raise TakeSelectionError("Stored scripted request index is invalid.")
            return self.get(selection_id)
        measured: list[dict[str, Any]] = []
        for candidate, path in zip(normalized, paths, strict=True):
            source_range = _source_range_object(candidate["source_range"])
            if source_range is None:
                raise TakeSelectionError("Candidate source range is missing.")
            source_path = Path(path)
            result = _analyze_file(
                source_path,
                str(candidate["candidate_id"]),
                source_range,
            )
            transcript = self._transcriber.transcribe(
                source_path,
                start_seconds=source_range["start_seconds"],
                end_seconds=source_range["end_seconds"],
            )
            dialogue = _dialogue_metrics(
                transcript,
                reference_tokens,
                source_range["end_seconds"] - source_range["start_seconds"],
            )
            result["dialogue"] = dialogue
            result["selection_score"] = round(
                0.35 * result["technical_score"]
                + 0.65 * dialogue["reference_f1"] * 100,
                3,
            )
            measured.append(result)
        ranked = sorted(
            measured,
            key=lambda item: (-item["selection_score"], item["candidate_id"]),
        )
        for rank, candidate in enumerate(ranked, start=1):
            candidate["rank"] = rank
        score_gap = round(
            ranked[0]["selection_score"] - ranked[1]["selection_score"],
            3,
        )
        confidence = "high" if score_gap >= 10 else (
            "medium" if score_gap >= 4 else "low"
        )
        payload = {
            "selection_version": SCRIPTED_SELECTION_VERSION,
            "status": "pending_review",
            "selection_name": name,
            "reference": reference_payload,
            "policy": _scripted_policy(),
            "candidates": ranked,
            "recommendation": {
                "candidate_id": ranked[0]["candidate_id"],
                "confidence": confidence,
                "score_gap": score_gap,
                "basis": "bounded technical and transcript measurements",
                "limitations": [
                    "Transcript matching can contain speech-recognition errors.",
                    "No acting, emotion, framing, or story-quality judgment.",
                    "Reference text and source ranges are caller-selected.",
                    "A human must approve or reject the recommendation.",
                ],
            },
            "review_required": True,
        }
        result = {**payload, "selection_id": _canonical_sha256(payload)}
        validate_contract("take-selection", result)
        self._selections_root.mkdir(parents=True, exist_ok=True)
        selection_path = self._selections_root / f"{result['selection_id']}.json"
        if selection_path.is_file():
            existing = read_json_object(selection_path)
            if existing != result:
                raise TakeSelectionError(
                    "Stored take selection does not match deterministic analysis."
                )
            stored = existing
        else:
            atomic_write_json(selection_path, result)
            stored = result
        request_root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            request_path,
            {"request_id": request_id, "selection_id": result["selection_id"]},
        )
        return stored

    def get(self, selection_id: str) -> dict[str, Any]:
        """Return one stored take-selection report."""
        identifier = _sha256(selection_id, "selection_id")
        path = self._selections_root / f"{identifier}.json"
        if not path.is_file():
            raise TakeSelectionError("The take selection was not found.")
        result = read_json_object(path)
        validate_contract("take-selection", result)
        if result.get("selection_id") != identifier:
            raise TakeSelectionError("The stored take selection ID is invalid.")
        canonical_payload = dict(result)
        canonical_payload.pop("selection_id", None)
        if _canonical_sha256(canonical_payload) != identifier:
            raise TakeSelectionError("The stored take selection content is invalid.")
        return result

    def list(self, limit: int = 20) -> dict[str, Any]:
        """List bounded stored selection summaries without source paths."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise TakeSelectionError("limit must be between 1 and 100.")
        if not self._selections_root.is_dir():
            return {"selections": [], "count": 0}
        reports = []
        for path in sorted(
            self._selections_root.glob("*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )[:limit]:
            report = read_json_object(path)
            validate_contract("take-selection", report)
            reports.append(
                {
                    "selection_id": report["selection_id"],
                    "selection_name": report["selection_name"],
                    "status": report["status"],
                    "recommended_candidate_id": report["recommendation"][
                        "candidate_id"
                    ],
                    "confidence": report["recommendation"]["confidence"],
                }
            )
        return {"selections": reports, "count": len(reports)}

    def review(
        self,
        *,
        selection_id: str,
        decision: str,
        selected_candidate_id: str | None,
        note: str = "",
    ) -> dict[str, Any]:
        """Persist one immutable approve/reject decision without editing."""
        selection = self.get(selection_id)
        normalized_decision = _decision(decision)
        normalized_note = _note(note)
        candidate_ids = {
            candidate["candidate_id"] for candidate in selection["candidates"]
        }
        if normalized_decision == "approve":
            selected = _candidate_id(selected_candidate_id, "selected_candidate_id")
            if selected not in candidate_ids:
                raise TakeSelectionError(
                    "selected_candidate_id is not part of this selection."
                )
        else:
            if selected_candidate_id is not None:
                raise TakeSelectionError(
                    "selected_candidate_id must be null when rejecting."
                )
            selected = None
        selection_hash = _canonical_sha256(selection)
        review_payload = {
            "review_version": REVIEW_VERSION,
            "selection_id": selection["selection_id"],
            "selection_sha256": selection_hash,
            "decision": normalized_decision,
            "selected_candidate_id": selected,
            "note": normalized_note,
            "timeline_modified": False,
        }
        result = {
            **review_payload,
            "review_id": _canonical_sha256(review_payload),
        }
        validate_contract("take-selection-review", result)
        self._reviews_root.mkdir(parents=True, exist_ok=True)
        path = self._reviews_root / f"{selection['selection_id']}.json"
        if path.is_file():
            existing = read_json_object(path)
            validate_contract("take-selection-review", existing)
            if existing != result:
                raise TakeSelectionError(
                    "This take selection already has a different review."
                )
            return existing
        atomic_write_json(path, result)
        return result

    def get_review(self, selection_id: str) -> dict[str, Any]:
        """Return one canonical immutable take-selection review."""
        selection = self.get(selection_id)
        path = self._reviews_root / f"{selection['selection_id']}.json"
        if not path.is_file():
            raise TakeSelectionError("The take selection has not been reviewed.")
        review = read_json_object(path)
        validate_contract("take-selection-review", review)
        review_payload = dict(review)
        review_id = review_payload.pop("review_id", None)
        if (
            review.get("selection_id") != selection["selection_id"]
            or review.get("selection_sha256") != _canonical_sha256(selection)
            or not isinstance(review_id, str)
            or _canonical_sha256(review_payload) != review_id
        ):
            raise TakeSelectionError("Stored take-selection review is invalid.")
        return review


def _analyze_file(
    path: Path,
    candidate_id: str,
    source_range: object | None = None,
) -> dict[str, Any]:
    normalized_range = _source_range_object(source_range)
    duration, video, audio = _measure_media(path, normalized_range)
    technical_score = _technical_score(video, audio)
    strengths, warnings = _observations(video, audio)
    stat = path.stat()
    result = {
        "candidate_id": candidate_id,
        "display_name": path.name,
        "fingerprint": _file_fingerprint(path, stat.st_size),
        "size_bytes": stat.st_size,
        "duration_seconds": round(duration, 3),
        "video": video,
        "audio": audio,
        "technical_score": technical_score,
        "rank": 1,
        "strengths": strengths,
        "warnings": warnings,
    }
    if normalized_range is not None:
        result["source_range"] = normalized_range
    return result


def _measure_media(
    path: Path,
    source_range: dict[str, float] | None,
) -> tuple[float, dict[str, Any], dict[str, Any]]:
    try:
        with av.open(str(path)) as container:
            video_stream = next(iter(container.streams.video), None)
            audio_stream = next(iter(container.streams.audio), None)
            duration = _duration_seconds(container, video_stream, audio_stream)
        if video_stream is None:
            raise TakeSelectionError(f"Candidate has no video stream: {path.name}")
        start, end = _resolved_bounds(source_range, duration)
        video = _measure_video(
            path,
            start,
            end,
            segment_mode=source_range is not None,
        )
        audio = _measure_audio(path, start, end)
    except (av.error.FFmpegError, OSError, ValueError) as error:
        raise TakeSelectionError(
            f"Could not analyze media candidate {path.name}: {error}"
        ) from error
    return end - start, video, audio


def _duration_seconds(container: Any, *streams: Any) -> float:
    for stream in streams:
        if stream is not None and stream.duration is not None:
            duration = float(stream.duration * stream.time_base)
            if math.isfinite(duration) and duration > 0:
                return duration
    if container.duration is not None:
        duration = float(container.duration / av.time_base)
        if math.isfinite(duration) and duration > 0:
            return duration
    raise TakeSelectionError("Candidate duration is unavailable.")


def _measure_video(
    path: Path,
    start: float,
    end: float,
    *,
    segment_mode: bool,
) -> dict[str, Any]:
    samples: list[tuple[float, float, float]] = []
    width = 0
    height = 0
    frame_rate = 0.0
    with av.open(str(path)) as container:
        stream = next(iter(container.streams.video), None)
        if stream is None:
            raise TakeSelectionError("Candidate has no video stream.")
        width = int(stream.width)
        height = int(stream.height)
        if stream.average_rate is not None:
            frame_rate = float(stream.average_rate)
        for fraction in VIDEO_SAMPLE_FRACTIONS:
            target = start + (end - start) * fraction
            timestamp = int(target / float(stream.time_base))
            container.seek(timestamp, stream=stream, any_frame=False, backward=True)
            frame = _video_frame(
                container,
                stream,
                target,
                exact_segment=segment_mode,
            )
            if frame is None:
                continue
            gray = frame.to_ndarray(format="gray")[::8, ::8].astype(np.float64)
            normalized = gray / 255.0
            luma = float(np.mean(normalized))
            contrast = float(np.std(normalized))
            dx = float(np.mean(np.abs(np.diff(normalized, axis=1))))
            dy = float(np.mean(np.abs(np.diff(normalized, axis=0))))
            samples.append((luma, contrast, (dx + dy) / 2.0))
    if not samples:
        raise TakeSelectionError("No video frames could be sampled.")
    lumas = [sample[0] for sample in samples]
    contrasts = [sample[1] for sample in samples]
    sharpness = [sample[2] for sample in samples]
    return {
        "width": width,
        "height": height,
        "frame_rate": round(frame_rate, 3),
        "sample_count": len(samples),
        "luma_mean": round(float(np.mean(lumas)), 6),
        "contrast_mean": round(float(np.mean(contrasts)), 6),
        "sharpness_mean": round(float(np.mean(sharpness)), 6),
        "black_frame_ratio": round(
            sum(luma < 0.03 for luma in lumas) / len(lumas), 6
        ),
    }


def _measure_audio(path: Path, start: float, end: float) -> dict[str, Any]:
    chunks: list[np.ndarray[Any, Any]] = []
    sample_rate = 0
    total = 0
    with av.open(str(path)) as container:
        stream = next(iter(container.streams.audio), None)
        if stream is None:
            return {"present": False, "sample_rate": 0, "sample_count": 0}
        sample_rate = int(stream.rate or 0)
        if start > 0:
            timestamp = int(start / float(stream.time_base))
            container.seek(timestamp, stream=stream, any_frame=False, backward=True)
        fallback_time = start
        for frame in container.decode(stream):
            values = _normalized_audio(frame.to_ndarray())
            frame_start = _frame_start_seconds(frame, fallback_time)
            frame_end = frame_start + (
                values.size / sample_rate if sample_rate > 0 else 0.0
            )
            fallback_time = frame_end
            if frame_start >= end:
                break
            if frame_end <= start:
                continue
            if sample_rate > 0:
                slice_start = max(0, round((start - frame_start) * sample_rate))
                slice_end = min(values.size, round((end - frame_start) * sample_rate))
                values = values[slice_start:slice_end]
            remaining = AUDIO_SAMPLE_LIMIT - total
            if remaining <= 0:
                break
            values = values[:remaining]
            chunks.append(values)
            total += int(values.size)
    if not chunks:
        return {"present": True, "sample_rate": sample_rate, "sample_count": 0}
    samples = np.concatenate(chunks)
    absolute = np.abs(samples)
    rms = float(np.sqrt(np.mean(np.square(samples))))
    peak = float(np.max(absolute))
    block_size = max(1, sample_rate // 50)
    usable = samples[: (samples.size // block_size) * block_size]
    if usable.size:
        blocks = usable.reshape(-1, block_size)
        block_rms = np.sqrt(np.mean(np.square(blocks), axis=1))
        silence_ratio = float(np.mean(block_rms < 10 ** (-50 / 20)))
    else:
        silence_ratio = 0.0
    return {
        "present": True,
        "sample_rate": sample_rate,
        "sample_count": int(samples.size),
        "rms_dbfs": round(_dbfs(rms), 3),
        "peak_dbfs": round(_dbfs(peak), 3),
        "clipping_ratio": round(float(np.mean(absolute >= 0.999)), 8),
        "silence_ratio": round(silence_ratio, 6),
    }


def _normalized_audio(values: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    flattened = values.astype(np.float64)
    if flattened.ndim > 1:
        flattened = np.mean(flattened, axis=0)
    flattened = flattened.reshape(-1)
    if np.issubdtype(values.dtype, np.integer):
        info = np.iinfo(values.dtype)
        flattened /= max(abs(info.min), abs(info.max))
    return np.clip(flattened, -1.0, 1.0)


def _technical_score(video: dict[str, Any], audio: dict[str, Any]) -> float:
    exposure = max(0.0, 1.0 - abs(video["luma_mean"] - 0.5) / 0.5)
    contrast = min(video["contrast_mean"] / 0.2, 1.0)
    sharpness = min(video["sharpness_mean"] / 0.08, 1.0)
    non_black = 1.0 - video["black_frame_ratio"]
    resolution = min((video["width"] * video["height"]) / 2_073_600, 1.0)
    video_score = 100 * (
        0.2 * exposure
        + 0.2 * contrast
        + 0.35 * sharpness
        + 0.15 * non_black
        + 0.1 * resolution
    )
    if audio.get("present") is not True or audio.get("sample_count", 0) == 0:
        return float(round(video_score * 0.85, 3))
    rms_quality = max(0.0, 1.0 - abs(audio["rms_dbfs"] + 18.0) / 24.0)
    peak_quality = max(0.0, 1.0 - max(audio["peak_dbfs"] + 1.0, 0.0) / 12.0)
    clipping_quality = max(0.0, 1.0 - audio["clipping_ratio"] * 1000.0)
    audio_score = 100 * (
        0.45 * rms_quality + 0.25 * peak_quality + 0.3 * clipping_quality
    )
    return float(round(0.75 * video_score + 0.25 * audio_score, 3))


def _observations(
    video: dict[str, Any], audio: dict[str, Any]
) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    warnings: list[str] = []
    if 0.2 <= video["luma_mean"] <= 0.8:
        strengths.append("Sampled exposure is within the technical target range.")
    else:
        warnings.append("Sampled exposure is unusually dark or bright.")
    if video["sharpness_mean"] >= 0.04:
        strengths.append("Sampled frames have strong edge detail.")
    if video["black_frame_ratio"] > 0.2:
        warnings.append("More than 20% of sampled frames are near black.")
    if audio.get("present") is not True:
        warnings.append("No audio stream is available for technical scoring.")
    elif audio.get("sample_count", 0) == 0:
        warnings.append("The audio stream produced no decoded samples.")
    else:
        if audio["clipping_ratio"] <= 0.0001:
            strengths.append("The bounded audio sample has no material clipping.")
        else:
            warnings.append("The bounded audio sample contains clipped samples.")
    if video["sample_count"] < len(VIDEO_SAMPLE_FRACTIONS):
        warnings.append("Some requested video sample positions could not be decoded.")
    return strengths, warnings


def _file_fingerprint(path: Path, size: int) -> str:
    """Hash every byte with bounded memory and reject concurrent source changes.

    The domain separates full-file identities from legacy edge-only hashes.
    Old approvals must be regenerated; they must never silently bind new media.
    """
    digest = hashlib.sha256()
    digest.update(b"sha256-full-file-v2\0")
    digest.update(str(size).encode())
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        if before.st_size != size:
            raise TakeSelectionError("Media source changed before fingerprinting.")
        while chunk := source.read(1_048_576):
            digest.update(chunk)
        for after in (os.fstat(source.fileno()), path.stat()):
            if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (
                after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev
            ):
                raise TakeSelectionError("Media source changed during fingerprinting.")
    return digest.hexdigest()


def media_file_identity(path: Path) -> dict[str, str | int]:
    """Return the path-redacted file identity used by M54/M55 contracts."""
    resolved = path.resolve()
    if not resolved.is_file():
        raise TakeSelectionError("Media source file does not exist.")
    stat = resolved.stat()
    if stat.st_size <= 0:
        raise TakeSelectionError("Media source file is empty.")
    return {
        "display_name": resolved.name,
        "fingerprint": _file_fingerprint(resolved, stat.st_size),
        "size_bytes": stat.st_size,
    }


def _dbfs(value: float) -> float:
    return -120.0 if value <= 0 else 20.0 * math.log10(value)


def _candidate_inputs(
    candidates: list[dict[str, str | float]],
) -> list[dict[str, Any]]:
    if not isinstance(candidates, list) or not 2 <= len(candidates) <= 8:
        raise TakeSelectionError("candidates must contain between 2 and 8 items.")
    normalized: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    modes: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise TakeSelectionError("Each candidate must be an object.")
        keys = set(candidate)
        full_keys = {"candidate_id", "path"}
        segment_keys = full_keys | {"start_seconds", "end_seconds"}
        if keys != full_keys and keys != segment_keys:
            raise TakeSelectionError(
                "Each candidate requires candidate_id and path, with optional "
                "start_seconds and end_seconds together."
            )
        raw_identifier = candidate["candidate_id"]
        identifier = _candidate_id(
            raw_identifier if isinstance(raw_identifier, str) else None,
            "candidate_id",
        )
        path = candidate["path"]
        if not isinstance(path, str) or not path:
            raise TakeSelectionError("Candidate path must be a non-empty string.")
        if identifier in identifiers:
            raise TakeSelectionError("candidate_id values must be unique.")
        identifiers.add(identifier)
        normalized_candidate: dict[str, Any] = {
            "candidate_id": identifier,
            "path": path,
        }
        if keys == segment_keys:
            normalized_candidate["source_range"] = _source_range(
                candidate["start_seconds"],
                candidate["end_seconds"],
            )
            modes.add("segment")
        else:
            modes.add("file")
        normalized.append(normalized_candidate)
    if len(modes) != 1:
        raise TakeSelectionError(
            "Candidates must all use full files or all use bounded source ranges."
        )
    return normalized


def _source_range(start: object, end: object) -> dict[str, float]:
    if (
        isinstance(start, bool)
        or not isinstance(start, (int, float))
        or isinstance(end, bool)
        or not isinstance(end, (int, float))
    ):
        raise TakeSelectionError(
            "start_seconds and end_seconds must be finite numbers."
        )
    normalized_start = round(float(start), 6)
    normalized_end = round(float(end), 6)
    if not math.isfinite(normalized_start) or not math.isfinite(normalized_end):
        raise TakeSelectionError(
            "start_seconds and end_seconds must be finite numbers."
        )
    if normalized_start < 0 or normalized_end <= normalized_start:
        raise TakeSelectionError(
            "source range must satisfy 0 <= start_seconds < end_seconds."
        )
    if normalized_end - normalized_start > MAX_SEGMENT_SECONDS:
        raise TakeSelectionError("A source range must not exceed 300 seconds.")
    return {
        "start_seconds": normalized_start,
        "end_seconds": normalized_end,
    }


def _source_range_object(value: object | None) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "start_seconds",
        "end_seconds",
    }:
        raise TakeSelectionError("Internal source range is invalid.")
    return _source_range(value["start_seconds"], value["end_seconds"])


def _resolved_bounds(
    source_range: dict[str, float] | None,
    source_duration: float,
) -> tuple[float, float]:
    if source_range is None:
        return 0.0, source_duration
    start = source_range["start_seconds"]
    end = source_range["end_seconds"]
    if start >= source_duration or end > source_duration + 0.05:
        raise TakeSelectionError(
            "Candidate source range exceeds the media duration."
        )
    return start, min(end, source_duration)


def _video_frame(
    container: Any,
    stream: Any,
    target: float,
    *,
    exact_segment: bool,
) -> Any | None:
    for index, frame in enumerate(container.decode(stream)):
        if not exact_segment or _frame_start_seconds(frame, target) >= target - 1e-6:
            return frame
        if index >= 1_800:
            break
    return None


def _frame_start_seconds(frame: Any, fallback: float) -> float:
    frame_time = getattr(frame, "time", None)
    if frame_time is not None:
        value = float(frame_time)
        if math.isfinite(value):
            return value
    pts = getattr(frame, "pts", None)
    time_base = getattr(frame, "time_base", None)
    if pts is not None and time_base is not None:
        value = float(pts * time_base)
        if math.isfinite(value):
            return value
    return fallback


def _policy(segment_mode: bool) -> dict[str, Any]:
    if not segment_mode:
        return POLICY
    return {
        "policy_id": POLICY_ID,
        "sampling": {
            **POLICY["sampling"],
            "max_segment_seconds": MAX_SEGMENT_SECONDS,
        },
        "weights": POLICY["weights"],
    }


def _limitations(segment_mode: bool) -> list[str]:
    if not segment_mode:
        return [
            "No semantic, performance, or story-quality judgment.",
            "Video is sampled at fixed positions rather than fully decoded.",
            "A human must approve or reject the recommendation.",
        ]
    return [
        "No semantic, performance, or story-quality judgment.",
        "Each bounded range is sampled at fixed positions rather than fully decoded.",
        "Candidate ranges are caller-selected and are not discovered automatically.",
        "A human must approve or reject the recommendation.",
    ]


def _scripted_request_id(
    selection_name: str,
    candidates: list[dict[str, Any]],
    paths: list[str],
    reference: dict[str, Any],
) -> str:
    sources: list[dict[str, Any]] = []
    for candidate, path_value in zip(candidates, paths, strict=True):
        path = Path(path_value)
        stat = path.stat()
        sources.append(
            {
                "candidate_id": candidate["candidate_id"],
                "source_range": candidate["source_range"],
                "fingerprint": _file_fingerprint(path, stat.st_size),
                "size_bytes": stat.st_size,
            }
        )
    return _canonical_sha256(
        {
            "selection_version": SCRIPTED_SELECTION_VERSION,
            "selection_name": selection_name,
            "policy_id": SCRIPTED_POLICY_ID,
            "reference": reference,
            "candidates": sources,
        }
    )


def _reference_tokens(reference_text: str) -> list[str]:
    if (
        not isinstance(reference_text, str)
        or not reference_text.strip()
        or len(reference_text) > MAX_REFERENCE_TEXT_LENGTH
    ):
        raise TakeSelectionError(
            "reference_text must contain between 1 and 20000 characters."
        )
    tokens = _text_tokens(reference_text)
    if not tokens:
        raise TakeSelectionError("reference_text must contain words or numbers.")
    return tokens


def _text_tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)?", text.casefold(), re.UNICODE)


def _dialogue_metrics(
    transcript: dict[str, Any],
    reference_tokens: list[str],
    range_duration_seconds: float,
) -> dict[str, Any]:
    segments = transcript.get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= 10_000:
        raise TakeSelectionError("Transcript segments are invalid.")
    texts: list[str] = []
    speech_duration_ms = 0
    previous_end = 0
    for segment in segments:
        if not isinstance(segment, dict):
            raise TakeSelectionError("Transcript segment is invalid.")
        start_ms = segment.get("start_ms")
        end_ms = segment.get("end_ms")
        text = segment.get("text")
        if (
            not isinstance(start_ms, int)
            or isinstance(start_ms, bool)
            or not isinstance(end_ms, int)
            or isinstance(end_ms, bool)
            or start_ms < previous_end
            or end_ms <= start_ms
            or not isinstance(text, str)
            or not text.strip()
            or len(text) > 4_000
        ):
            raise TakeSelectionError("Transcript segment is invalid.")
        previous_end = end_ms
        speech_duration_ms += end_ms - start_ms
        texts.append(" ".join(text.split()))
    transcript_text = " ".join(texts)
    transcript_tokens = _text_tokens(transcript_text)
    if not transcript_tokens:
        raise TakeSelectionError("Transcript contains no words or numbers.")
    matching_tokens = sum(
        block.size
        for block in SequenceMatcher(
            None,
            reference_tokens,
            transcript_tokens,
            autojunk=False,
        ).get_matching_blocks()
    )
    precision = matching_tokens / len(transcript_tokens)
    recall = matching_tokens / len(reference_tokens)
    f1 = 0.0 if precision + recall == 0 else (
        2 * precision * recall / (precision + recall)
    )
    language = transcript.get("language")
    backend = transcript.get("backend")
    model = transcript.get("model")
    probability = transcript.get("language_probability", 0.0)
    if (
        not isinstance(language, str)
        or not language
        or not isinstance(backend, str)
        or not backend
        or not isinstance(model, str)
        or not model
        or isinstance(probability, bool)
        or not isinstance(probability, (int, float))
        or not math.isfinite(float(probability))
        or not 0 <= float(probability) <= 1
    ):
        raise TakeSelectionError("Transcript metadata is invalid.")
    return {
        "backend": backend,
        "model": model,
        "language": language,
        "language_probability": round(float(probability), 6),
        "transcript_sha256": hashlib.sha256(
            transcript_text.encode("utf-8")
        ).hexdigest(),
        "word_count": len(transcript_tokens),
        "speech_duration_seconds": round(speech_duration_ms / 1000, 3),
        "speech_coverage": round(
            min(1.0, speech_duration_ms / 1000 / range_duration_seconds),
            6,
        ),
        "reference_precision": round(precision, 6),
        "reference_recall": round(recall, 6),
        "reference_f1": round(f1, 6),
    }


def _scripted_policy() -> dict[str, Any]:
    return {
        "policy_id": SCRIPTED_POLICY_ID,
        "sampling": {
            **POLICY["sampling"],
            "max_segment_seconds": MAX_SEGMENT_SECONDS,
            "transcription_model": "faster-whisper-small-uk",
        },
        "weights": {"technical": 0.35, "reference_match": 0.65},
    }


def _candidate_id(value: str | None, field: str) -> str:
    if not isinstance(value, str) or CANDIDATE_ID.fullmatch(value) is None:
        raise TakeSelectionError(f"{field} has an invalid identifier.")
    return value


def _selection_name(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 128:
        raise TakeSelectionError("selection_name must contain 1 to 128 characters.")
    return value.strip()


def _decision(value: str) -> str:
    if value not in {"approve", "reject"}:
        raise TakeSelectionError("decision must be approve or reject.")
    return value


def _note(value: str) -> str:
    if not isinstance(value, str) or len(value) > 1000:
        raise TakeSelectionError("note must contain at most 1000 characters.")
    return value


def _sha256(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise TakeSelectionError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
