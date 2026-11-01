"""Boundaries for finishing, owned render jobs and explicit session cleanup."""

from __future__ import annotations

import json
import re
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from agent.client import BridgeProtocolError
from providers.resolve.lua_cleanup import cleanup_responses
from providers.resolve.lua_finishing import validate_finishing, verify_video
from providers.resolve.lua_transport import LuaSnapshotClient, prepare


def test_replacement_take_is_bounded_to_distinct_allowed_files(tmp_path: Path) -> None:
    source, replacement = tmp_path / "camera.mp4", tmp_path / "processed.mp4"
    source.touch()
    replacement.touch()
    args: dict[str, Any] = dict(
        timeline_name="Review",
        track_index=2,
        item_index=1,
        expected_media_path=str(source),
        replacement_take_path=str(replacement),
    )
    result = validate_finishing("set_clip_properties", args, [tmp_path])
    assert result["replacement_take_path"] == replacement.as_posix()
    for change in (
        {"replacement_take_path": str(source)},
        {"track_index": True},
        {"item_index": 0},
        {"command": "arbitrary"},
    ):
        with pytest.raises(ValueError):
            validate_finishing("set_clip_properties", dict(args, **change), [tmp_path])
    with pytest.raises(ValueError):
        validate_finishing(
            "set_clip_properties",
            dict(args, replacement_take_path=str(tmp_path / "missing.mp4")),
            [tmp_path],
        )


def test_replacement_take_rejects_file_outside_media_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "camera.mp4"
    source.touch()
    outside = tmp_path / "outside.mp4"
    outside.touch()
    with pytest.raises(ValueError):
        validate_finishing(
            "set_clip_properties",
            dict(
                timeline_name="Review",
                track_index=2,
                item_index=1,
                expected_media_path=str(source),
                replacement_take_path=str(outside),
            ),
            [allowed],
        )


@pytest.mark.parametrize("fps", [True, 23, 60.0, "60", float("nan")])
def test_empty_timeline_fps_rejects_invalid_rate(tmp_path: Path, fps: Any) -> None:
    with pytest.raises(ValueError):
        validate_finishing(
            "set_clip_properties",
            {"timeline_name": "Review", "empty_timeline_fps": fps},
            [tmp_path],
        )


def test_empty_timeline_fps_accepts_supported_rate(tmp_path: Path) -> None:
    assert (
        validate_finishing(
            "set_clip_properties",
            {"timeline_name": "Review", "empty_timeline_fps": 60},
            [tmp_path],
        )["empty_timeline_fps"]
        == 60
    )


@pytest.mark.parametrize(
    "change",
    [
        {"center_x": True},
        {"center_y": float("nan")},
        {"diameter": 0},
        {"diameter": float("inf")},
        {"code": "anything"},
    ],
)
def test_circle_rejects_unbounded_geometry(
    tmp_path: Path, change: dict[str, Any]
) -> None:
    media = tmp_path / "camera.mp4"
    media.touch()
    args: dict[str, Any] = dict(
        timeline_name="Review",
        track_index=2,
        item_index=1,
        expected_media_path=str(media),
        circle_mask=dict(center_x=0.5, center_y=0.5, diameter=0.4),
    )
    args["circle_mask"].update(change)
    with pytest.raises(ValueError):
        validate_finishing("set_clip_properties", args, [tmp_path])


def test_circle_requires_exact_source_and_fields(tmp_path: Path) -> None:
    media = tmp_path / "camera.mp4"
    media.touch()
    args = dict(
        timeline_name="Review",
        track_index=2,
        item_index=1,
        expected_media_path=str(media),
        circle_mask=dict(center_x=0.5, center_y=0.5, diameter=0.4),
    )
    assert (
        validate_finishing("set_clip_properties", args, [tmp_path])["circle_mask"]
        == args["circle_mask"]
    )
    for change in ({"track_index": True}, {"lua": "bad"}, {"item_index": 0}):
        with pytest.raises(ValueError):
            validate_finishing("set_clip_properties", dict(args, **change), [tmp_path])
    with pytest.raises(ValueError):
        validate_finishing(
            "set_clip_properties",
            dict(args, expected_media_path=str(tmp_path / "missing.mp4")),
            [tmp_path],
        )


def serve(root: Path, status: str, backup: bool = True) -> threading.Thread:
    def run() -> None:
        for _ in range(300):
            try:
                raw = (root / "request.lua").read_text()
            except OSError:
                time.sleep(0.01)
                continue
            match = re.search(r',id="((?:\\[0-9]{3})+)"', raw)
            if match:
                identity = bytes(
                    int(n) for n in re.findall(r"\\(\d{3})", match[1])
                ).decode()
                for suffix in ["before", status] if backup else [status]:
                    with zipfile.ZipFile(
                        root / f"{identity}.{suffix}.drp", "w"
                    ) as archive:
                        archive.writestr(
                            "project.xml",
                            '<SM_Project DbId="test-project">'
                            "<ProjectName>Test</ProjectName></SM_Project>",
                        )
                return
            time.sleep(0.01)

    worker = threading.Thread(target=run)
    worker.start()
    return worker


@pytest.mark.parametrize(
    "props",
    [
        {"AudioVolume": True},
        {"AudioVolume": float("nan")},
        {"AudioVolume": 13},
        {"ZoomX": 2},
        {"AudioVolume": "0"},
        {},
    ],
)
def test_audio_rejects_unsafe_properties(tmp_path: Path, props: dict[str, Any]) -> None:
    media = tmp_path / "audio.wav"
    media.touch()
    with pytest.raises(ValueError):
        validate_finishing(
            "set_clip_properties",
            {
                "timeline_name": "Test",
                "track_type": "audio",
                "track_index": 1,
                "item_index": 1,
                "expected_media_path": str(media),
                "properties": props,
            },
            [tmp_path],
        )


@pytest.mark.parametrize(
    "text",
    [
        "1\n00:00:02,000 --> 00:00:01,000\nBad",
        "1\n00:99:00,000 --> 01:00:00,000\nBad",
        "1\n00:00:00,000 --> 00:00:02,000\nOne\n\n"
        "2\n00:00:01,000 --> 00:00:03,000\nTwo",
        "",
        "not an srt",
    ],
)
def test_malformed_subtitles_rejected(tmp_path: Path, text: str) -> None:
    srt = tmp_path / "captions.srt"
    srt.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        validate_finishing(
            "add_subtitles",
            {"timeline_name": "Test", "subtitle_path": str(srt)},
            [tmp_path],
        )


def test_subtitle_timing_and_media_boundary(tmp_path: Path) -> None:
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,500\nCaption\n", encoding="utf-8")
    args = {"timeline_name": "Test", "subtitle_path": str(srt)}
    validated = validate_finishing("add_subtitles", args, [tmp_path])
    assert (
        validated["cue_count"],
        validated["first_start"],
        validated["last_end"],
    ) == (1, 0.5, 2.5)
    with pytest.raises(ValueError):
        validate_finishing("add_subtitles", args, [tmp_path / "other"])


def prepare_job(root: Path) -> dict[str, Any]:
    worker = serve(root, "ok_job_safe-job-1")
    try:
        return LuaSnapshotClient(root).request(
            "prepare_render",
            3,
            arguments={"timeline_name": "Test"},
            expected_project_id="test-project",
            idempotency_key="prepare",
            confirm=True,
        )
    finally:
        worker.join(4)


@pytest.mark.parametrize(
    "bounds",
    [
        (True, 20),
        (0, False),
        (1.5, 20),
        (-1, 20),
        (20, 20),
        (21, 20),
        (None, 20),
        (0, None),
        (0, 2_147_483_648),
    ],
)
def test_render_range_rejects_invalid_bounds(bounds: tuple[Any, Any]) -> None:
    with pytest.raises(ValueError):
        validate_finishing(
            "prepare_render",
            {
                "timeline_name": "Review",
                "start_frame": bounds[0],
                "end_frame": bounds[1],
            },
            [],
        )


def test_render_range_is_bound_to_receipt_and_preserves_full_default(
    tmp_path: Path,
) -> None:
    assert validate_finishing("prepare_render", {"timeline_name": "Review"}, []) == {
        "timeline_name": "Review"
    }
    root = tmp_path / "session"
    prepare(root)
    args = {"timeline_name": "Review", "start_frame": 216600, "end_frame": 217560}
    kwargs: dict[str, Any] = dict(
        arguments=args,
        expected_project_id="test-project",
        confirm=True,
        idempotency_key="local-range",
    )
    worker = serve(root, "ok_job_range-job_216600_217560")
    try:
        result = LuaSnapshotClient(root).request("prepare_render", 3, **kwargs)
    finally:
        worker.join(4)
    assert result["arguments"] == args
    assert LuaSnapshotClient(root).request("prepare_render", 1, **kwargs)["replayed"]
    with pytest.raises(ValueError, match="different arguments"):
        LuaSnapshotClient(root).request(
            "prepare_render", 1, **dict(kwargs, arguments=dict(args, end_frame=217561))
        )


@pytest.mark.parametrize("ranged", [False, True])
def test_selected_lua_render_parent_is_reserved_and_replay_bound(
    tmp_path: Path, ranged: bool,
) -> None:
    destination = tmp_path / "Videos"
    destination.mkdir()
    (destination / "existing.mp4").write_bytes(b"preserve")
    root = tmp_path / "session"
    prepare(root, media_roots=[destination])
    args: dict[str, Any] = {
        "timeline_name": "Review", "output_directory": destination.as_posix(),
    }
    if ranged:
        args.update(start_frame=10, end_frame=20)
    kwargs: dict[str, Any] = dict(
        arguments=args, expected_project_id="test-project", confirm=True,
        idempotency_key="selected-parent",
    )
    worker = serve(root, "ok_job_export_selected" + ("_10_20" if ranged else ""))
    client = LuaSnapshotClient(root)
    try:
        result = client.request("prepare_render", 3, **kwargs)
    finally:
        worker.join(4)
    output = Path(result["output_path"])
    assert output.parent.parent == destination
    assert output.parent.is_dir()
    assert not (root / "renders").exists()
    assert client.request("prepare_render", 1, **kwargs)["replayed"]
    other = destination / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="different arguments"):
        client.request("prepare_render", 1, **dict(
            kwargs, arguments=dict(args, output_directory=other.as_posix()),
        ))
    output.write_bytes(b"do not overwrite")
    with pytest.raises(ValueError, match="no longer empty"):
        client.request(
            "start_render", arguments={"job_id": result["job_id"]},
            expected_project_id="test-project", confirm=True, idempotency_key="start",
        )
    assert output.read_bytes() == b"do not overwrite"
    assert (destination / "existing.mp4").read_bytes() == b"preserve"


def test_old_lua_session_rejects_export_parent_without_dispatch(tmp_path: Path) -> None:
    destination = tmp_path / "Videos"
    destination.mkdir()
    root = tmp_path / "session"
    prepare(root, media_roots=[destination])
    metadata = json.loads((root / "session.json").read_text())
    metadata["capabilities"].remove("render_output_directory_v1")
    (root / "session.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="render_output_directory_v1"):
        LuaSnapshotClient(root).request(
            "prepare_render", arguments={
                "timeline_name": "Review", "output_directory": str(destination),
            }, expected_project_id="test-project", confirm=True, idempotency_key="old",
        )
    assert list(destination.iterdir()) == []
    assert not (root / "client.lock").exists()
    assert not (root / "receipts").exists()
    assert (root / "request.lua").read_text() == "return nil\n"


def test_export_parent_rejects_legacy_native_acknowledgement(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "Videos"
    destination.mkdir()
    root = tmp_path / "session"
    prepare(root, media_roots=[destination])
    worker = serve(root, "ok_job_old-bridge")
    try:
        with pytest.raises(BridgeProtocolError, match="render job acknowledgement"):
            LuaSnapshotClient(root).request(
                "prepare_render", 3, arguments={
                    "timeline_name": "Review", "output_directory": str(destination),
                }, expected_project_id="test-project", confirm=True,
                idempotency_key="ack",
            )
    finally:
        worker.join(4)


def test_render_requires_owned_job_and_replays_preparation(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    client = LuaSnapshotClient(root)
    with pytest.raises(ValueError, match="not owned"):
        client.request(
            "start_render",
            arguments={"job_id": "foreign"},
            expected_project_id="test-project",
            confirm=True,
            idempotency_key="start",
        )
    first = prepare_job(root)
    assert first["job_id"] == "safe-job-1"
    assert Path(first["output_path"]).parent.is_dir()
    replay = client.request(
        "prepare_render",
        arguments={"timeline_name": "Test"},
        expected_project_id="test-project",
        confirm=True,
        idempotency_key="prepare",
    )
    assert replay["replayed"] and replay["job_id"] == first["job_id"]
    with pytest.raises(ValueError, match="not owned"):
        client.request(
            "get_render_status",
            arguments={"job_id": first["job_id"]},
            expected_project_id="other-project",
        )


@pytest.mark.parametrize(
    "token", ["ok_job_legacy-job", "ok_job_wrong-range_216600_217561"]
)
def test_range_render_requires_matching_native_range_ack(
    tmp_path: Path, token: str
) -> None:
    root = tmp_path / "session"
    prepare(root)
    client = LuaSnapshotClient(root)
    worker = serve(root, token)
    try:
        with pytest.raises(BridgeProtocolError, match="acknowledgement"):
            client.request(
                "prepare_render",
                3,
                arguments={
                    "timeline_name": "Review",
                    "start_frame": 216600,
                    "end_frame": 217560,
                },
                expected_project_id="test-project",
                confirm=True,
                idempotency_key="ranged",
            )
    finally:
        worker.join(4)
    # A legacy server may have queued a whole timeline, but no ownership receipt
    # authorizes starting it. Inspection is required; never retry preparation.
    with pytest.raises(ValueError, match="not owned"):
        client.request(
            "start_render",
            arguments={"job_id": "legacy-job"},
            expected_project_id="test-project",
            confirm=True,
            idempotency_key="start",
        )


def test_complete_status_requires_real_output(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    job = prepare_job(root)
    worker = serve(root, "ok_state_complete", backup=False)
    try:
        with pytest.raises(ValueError, match="non-empty"):
            LuaSnapshotClient(root).request(
                "get_render_status",
                3,
                arguments={"job_id": job["job_id"]},
                expected_project_id="test-project",
            )
    finally:
        worker.join(4)


def test_existing_output_blocks_start_without_pending_receipt(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    job = prepare_job(root)
    output = Path(job["output_path"])
    output.write_bytes(b"previous video")
    before = set((root / "receipts").glob("*.json"))
    with pytest.raises(ValueError, match="refusing overwrite"):
        LuaSnapshotClient(root).request(
            "start_render",
            arguments={"job_id": job["job_id"]},
            expected_project_id="test-project",
            confirm=True,
            idempotency_key="start",
        )
    assert output.read_bytes() == b"previous video"
    assert set((root / "receipts").glob("*.json")) == before
    assert (root / "request.lua").read_text().strip() == "return nil"


def test_completed_start_replays_even_when_output_exists(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    job = prepare_job(root)
    kwargs: dict[str, Any] = dict(
        arguments={"job_id": job["job_id"]},
        expected_project_id="test-project",
        confirm=True,
        idempotency_key="start",
    )
    worker = serve(root, "ok")
    try:
        result = LuaSnapshotClient(root).request("start_render", 3, **kwargs)
    finally:
        worker.join(4)
    Path(job["output_path"]).write_bytes(b"render output")
    replay = LuaSnapshotClient(root).request("start_render", 3, **kwargs)
    assert replay["replayed"] and replay["response_path"] == result["response_path"]


def test_real_video_and_audio_are_decoded(tmp_path: Path) -> None:
    import av
    import numpy as np

    path = tmp_path / "fixture.mov"
    with av.open(str(path), "w") as container:
        video = container.add_stream("mpeg4", rate=24)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        audio = container.add_stream("pcm_s16le", rate=48000)
        audio.layout = "stereo"
        frame = av.VideoFrame.from_ndarray(
            np.zeros((48, 64, 3), dtype=np.uint8), format="rgb24"
        )
        for packet in video.encode(frame):
            container.mux(packet)
        samples = av.AudioFrame.from_ndarray(
            np.zeros((1, 4000), dtype=np.int16), format="s16", layout="stereo"
        )
        samples.sample_rate = 48000
        for packet in audio.encode(samples):
            container.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None):
                container.mux(packet)
    result = verify_video(path)
    assert result["video_decode_verified"] and result["audio_decode_verified"]
    assert result["width"] == 64 and result["audio_streams"] == 1


def test_malformed_job_ack_is_uncertain(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    worker = serve(root, "ok_job_bad_token")
    try:
        with pytest.raises(BridgeProtocolError, match="acknowledgement"):
            LuaSnapshotClient(root).request(
                "prepare_render",
                3,
                arguments={"timeline_name": "Test"},
                expected_project_id="test-project",
                confirm=True,
                idempotency_key="prepare",
            )
    finally:
        worker.join(4)
    assert (
        json.loads(next((root / "receipts").glob("*.json")).read_text())["status"]
        == "pending"
    )


def test_cleanup_requires_stop_and_preserves_backups_renders_receipts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session"
    prepare(root)
    with pytest.raises(FileNotFoundError):
        cleanup_responses(root, confirm=True)
    metadata = json.loads((root / "session.json").read_text())
    (root / "stopped.json").write_text(json.dumps({"session": metadata["session"]}))
    response = root / ("a" * 32 + ".ok.drp")
    backup = root / ("a" * 32 + ".before.drp")
    response.write_bytes(b"response")
    backup.write_bytes(b"backup")
    accepted = root / ("b" * 32 + ".accepted.drp")
    accepted.write_bytes(b"accepted")
    (root / "renders").mkdir()
    video = root / "renders" / "video.mp4"
    video.write_bytes(b"video")
    assert cleanup_responses(root)["status"] == "preview"
    assert response.exists()
    assert cleanup_responses(root, confirm=True)["files"] == 2
    assert not accepted.exists()
    assert not response.exists() and backup.exists() and video.exists()
    assert cleanup_responses(root, confirm=True)["files"] == 0
    with pytest.raises(ValueError, match="stopped"):
        LuaSnapshotClient(root).request("ping")
    (root / "receipts").mkdir()
    (root / "receipts" / "uncertain.json").write_text('{"status":"pending"}')
    with pytest.raises(ValueError, match="uncertain"):
        cleanup_responses(root, confirm=True)


def test_invalid_render_not_reported_as_verified(tmp_path: Path) -> None:
    path = tmp_path / "video.mp4"
    path.touch()
    with pytest.raises(ValueError, match="non-empty"):
        verify_video(path)


def test_live_summary_uses_native_counts_not_exported_timeline(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    worker = serve(
        root, "ok_timeline_1_1_1_86400_86472_86412_86460_24000", backup=False
    )
    try:
        result = LuaSnapshotClient(root).request(
            "get_timeline_summary",
            3,
            arguments={"timeline_name": "Test"},
            expected_project_id="test-project",
        )
    finally:
        worker.join(4)
    assert result["source"] == "live_lua_api"
    assert result["summary"]["subtitle_items"] == 1
    assert result["summary"]["subtitle_first_frame"] == 86412
    assert not list(root.glob("*.before.drp"))


def test_reload_accepts_no_source_or_path(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    for arguments in ({"path": "other.lua"}, {"source": "print(1)"}):
        with pytest.raises(ValueError, match="no editing arguments"):
            LuaSnapshotClient(root).request("reload_modules", arguments=arguments)


@pytest.mark.parametrize("valid", [True, False])
def test_privacy_readback_uses_bounded_native_token(
    tmp_path: Path, valid: bool
) -> None:
    root = tmp_path / "session"
    prepare(root, [tmp_path])
    source = tmp_path / "screen.mkv"
    source.touch()
    assert (root / "privacy.lua").is_file()
    token = "ok_privacy2_4_1_500_3000_60000_150_500_300_1000_0_1_1_0_64_12345"
    worker = serve(root, token if valid else token + "_unexpected", backup=False)
    try:
        client = LuaSnapshotClient(root)
        arguments = dict(
            timeline_name="Test",
            track_type="video",
            track_index=1,
            item_index=1,
            expected_media_path=str(source),
            inspect_privacy=True,
        )
        if not valid:
            with pytest.raises(BridgeProtocolError, match="privacy"):
                client.request(
                    "get_timeline_summary",
                    3,
                    arguments=arguments,
                    expected_project_id="test-project",
                )
        else:
            result = client.request(
                "get_timeline_summary",
                3,
                arguments=arguments,
                expected_project_id="test-project",
            )
            assert result["privacy"]["interval_count"] == 64
            assert result["privacy"]["interval_checksum"] == 12345
            assert result["privacy"]["local_end_frame"] == 3000
    finally:
        worker.join(4)


def test_render_acceptance_is_not_completion_and_never_restarts(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    job = prepare_job(root)
    client = LuaSnapshotClient(root)
    args: dict[str, Any] = dict(
        arguments={"job_id": job["job_id"]},
        expected_project_id="test-project",
        confirm=True,
        idempotency_key="start",
    )
    worker = serve(root, "accepted")
    try:
        result = client.request("start_render", 3, **args)
    finally:
        worker.join(4)
    assert result["status"] == "accepted" and not result["completion_verified"]
    Path(job["output_path"]).write_bytes(b"unfinished render")
    replay = client.request("start_render", 1, **args)
    assert replay["replayed"] and replay["status"] == "accepted"
    assert len(list(root.glob("*.before.drp"))) == 2
    # No responder: could be rendering OR a stopped bridge, never claim running.
    state = client.request(
        "get_render_status",
        0.1,
        arguments={"job_id": job["job_id"]},
        expected_project_id="test-project",
    )
    assert state["status"] == "awaiting_status"
    assert not state["completion_verified"]
    # A subsequent native failure must supersede that acceptance.
    worker = serve(root, "ok_state_failed", backup=False)
    try:
        state = client.request(
            "get_render_status",
            3,
            arguments={"job_id": job["job_id"]},
            expected_project_id="test-project",
        )
    finally:
        worker.join(4)
    assert state["status"] == "failed"


def test_status_timeout_without_accepted_start_is_not_masked(tmp_path: Path) -> None:
    from providers.resolve.lua_transport import LuaCommandTimeoutError

    root = tmp_path / "session"
    prepare(root)
    job = prepare_job(root)
    with pytest.raises(LuaCommandTimeoutError):
        LuaSnapshotClient(root).request(
            "get_render_status",
            0.1,
            arguments={"job_id": job["job_id"]},
            expected_project_id="test-project",
        )


@pytest.mark.parametrize("precise", [False, True])
def test_item_readback_is_native_and_identity_bound(
    tmp_path: Path, precise: bool
) -> None:
    media = tmp_path / "screen.mkv"
    media.touch()
    root = tmp_path / "session"
    prepare(root, [tmp_path])
    suffix = "_60000000_80000000_3600000000" if precise else ""
    raw_start = 3599 if precise else 3600
    worker = serve(
        root, f"ok_item_216000_217200_{raw_start}_4800_2" + suffix, backup=False
    )
    try:
        result = LuaSnapshotClient(root).request(
            "get_timeline_summary",
            3,
            arguments={
                "timeline_name": "Sync",
                "track_type": "video",
                "track_index": 1,
                "item_index": 1,
                "expected_media_path": str(media),
            },
            expected_project_id="test-project",
        )
    finally:
        worker.join(4)
    assert result["item"]["end_frame"] - result["item"]["start_frame"] == 1200
    assert result["item"]["linked_items"] == 2
    assert result["item"]["source_start_frame"] == raw_start
    if precise:
        assert result["item"]["source_start_time_microseconds"] == 60_000_000
        assert result["item"]["source_end_time_microseconds"] == 80_000_000
        assert result["item"]["left_offset_microframes"] == 3_600_000_000
    else:
        assert "source_start_time_microseconds" not in result["item"]
    assert not list(root.glob("*.before.drp"))
