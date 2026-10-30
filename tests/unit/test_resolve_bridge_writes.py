"""Safe Resolve bridge write-operation tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from bridges.resolve.ResolveBridge import (
    _apply_verified_capabilities,
    _positive_frame_rate_setting,
    _positive_integer_property,
    _positive_number_property,
    collect_bridge_state,
    ensure_runtime_directories,
    process_pending_commands,
)


class FakeMediaItem:
    def __init__(self, asset_id: str, name: str) -> None:
        self._asset_id = asset_id
        self._name = name

    def GetMediaId(self) -> str:
        return self._asset_id

    def GetName(self) -> str:
        return self._name

    def GetClipProperty(self, key: str) -> str:
        return {"Frames": "240", "FPS": "60"}[key]


def test_clip_metadata_numeric_properties_are_strictly_normalized() -> None:
    assert _positive_integer_property("240") == 240
    assert _positive_integer_property(240.0) == 240
    assert _positive_number_property("59.94") == 59.94
    assert _positive_number_property(60) == 60.0
    assert _positive_frame_rate_setting("29.97 DF") == 29.97

    for invalid in (True, 0, -1, float("inf"), "unknown", ""):
        with pytest.raises(ValueError):
            _positive_number_property(invalid)
    with pytest.raises(ValueError):
        _positive_integer_property(23.976)


class FakeColorGraph:
    def GetNumNodes(self) -> int:
        return 1

    def GetNodeLabel(self, node_index: int) -> str:
        assert node_index == 1
        return "Primary"

    def GetLUT(self, node_index: int) -> str:
        assert node_index == 1
        return ""


class FakeTimelineItem:
    def __init__(
        self,
        item_id: str,
        name: str,
        *,
        timeline_start: int = 0,
        timeline_end: int = 0,
        source_start: int = 0,
        source_end: int = 0,
        track_type: str = "video",
        track_index: int = 1,
        generated: bool = False,
        fusion_comp_count: int = 0,
    ) -> None:
        self._item_id = item_id
        self._name = name
        self._timeline_start = timeline_start
        self._timeline_end = timeline_end
        self._source_start = source_start
        self._source_end = source_end
        self._track_type = track_type
        self._track_index = track_index
        self._generated = generated
        self._fusion_comp_count = fusion_comp_count
        self._enabled = True
        self._linked_items: list[FakeTimelineItem] = []
        self._properties: dict[str, bool | float] = {
            "Pan": 0.0,
            "Tilt": 0.0,
            "ZoomGang": True,
            "ZoomX": 1.0,
            "ZoomY": 1.0,
            "RotationAngle": 0.0,
            "Opacity": 100.0,
        }
        self._color_graph = FakeColorGraph()
        self._current_version = "Version 1"
        self._color_versions = ["Version 1"]
        self._cdl: dict[str, str] | None = None

    def GetUniqueId(self) -> str:
        return self._item_id

    def GetName(self) -> str:
        return self._name

    def GetStart(self, subframe_precision: bool) -> int:
        assert subframe_precision is False
        return self._timeline_start

    def GetEnd(self, subframe_precision: bool) -> int:
        assert subframe_precision is False
        return self._timeline_end

    def GetDuration(self, subframe_precision: bool) -> int:
        assert subframe_precision is False
        return self._timeline_end - self._timeline_start

    def GetSourceStartFrame(self) -> int:
        return self._source_start

    def GetSourceEndFrame(self) -> int:
        return self._source_end

    def GetMediaPoolItem(self) -> object | None:
        return None if self._generated else object()

    def GetTrackTypeAndIndex(self) -> list[str | int]:
        return [self._track_type, self._track_index]

    def GetFusionCompCount(self) -> int:
        return self._fusion_comp_count

    def SetClipEnabled(self, enabled: bool) -> bool:
        self._enabled = enabled
        return True

    def GetClipEnabled(self) -> bool:
        return self._enabled

    def GetLinkedItems(self) -> list[FakeTimelineItem]:
        return self._linked_items

    def SetProperty(self, properties: dict[str, bool | float]) -> bool:
        self._properties.update(properties)
        return True

    def GetProperty(self, key: str) -> bool | float:
        return self._properties[key]

    def GetCurrentVersion(self) -> dict[str, str | int]:
        return {"versionName": self._current_version, "versionType": 0}

    def GetVersionNameList(self, version_type: int) -> list[str]:
        assert version_type == 0
        return list(self._color_versions)

    def GetNodeGraph(self) -> FakeColorGraph:
        return self._color_graph

    def SetCDL(self, values: dict[str, str]) -> bool:
        self._cdl = dict(values)
        return True

    def AddVersion(self, version_name: str, version_type: int) -> bool:
        assert version_name
        assert version_type == 0
        self._color_versions.append(version_name)
        return True

    def LoadVersionByName(self, version_name: str, version_type: int) -> bool:
        assert version_name
        assert version_type == 0
        if version_name not in self._color_versions:
            return False
        self._current_version = version_name
        return True


class FakeFolder:
    def __init__(
        self,
        folder_id: str = "folder-root",
        name: str = "Master",
    ) -> None:
        self._folder_id = folder_id
        self._name = name
        self.clips: list[FakeMediaItem] = []
        self.subfolders: list[FakeFolder] = []

    def GetUniqueId(self) -> str:
        return self._folder_id

    def GetName(self) -> str:
        return self._name

    def GetClipList(self) -> list[FakeMediaItem]:
        return self.clips

    def GetSubFolderList(self) -> list[FakeFolder]:
        return self.subfolders


class FakeTimeline:
    def __init__(self, timeline_id: str, name: str) -> None:
        self._timeline_id = timeline_id
        self._name = name
        self._project: FakeProject | None = None
        self.markers: dict[int, dict[str, Any]] = {}
        self.items: list[FakeTimelineItem] = []
        self.track_counts = {"video": 1, "audio": 1, "subtitle": 0}
        self.added_tracks: list[tuple[str, str | None]] = []
        self.subtitle_settings: dict[object, object] | None = None
        self.current_timecode = "01:00:10:00"
        self.clamp_timecode = False

    def GetUniqueId(self) -> str:
        return self._timeline_id

    def GetName(self) -> str:
        return self._name

    def DuplicateTimeline(self, name: str) -> FakeTimeline | None:
        if self._project is None:
            return None
        duplicate = FakeTimeline(
            f"timeline-{len(self._project.timelines) + 1}",
            name,
        )
        duplicate._project = self._project
        duplicate.items = list(self.items)
        self._project.timelines.append(duplicate)
        self._project.current_timeline = duplicate
        return duplicate

    def GetStartFrame(self) -> int:
        return 86400

    def GetEndFrame(self) -> int:
        return max(
            [86400]
            + [item.GetEnd(False) for item in self.items]
        )

    def GetSetting(self, name: str) -> str:
        settings = {
            "timelineResolutionWidth": "1920",
            "timelineResolutionHeight": "1080",
            "timelineFrameRate": "60",
        }
        return settings[name]

    def GetStartTimecode(self) -> str:
        return "01:00:00:00"

    def GetCurrentTimecode(self) -> str:
        return self.current_timecode

    def SetCurrentTimecode(self, timecode: str) -> bool:
        if not self.clamp_timecode:
            self.current_timecode = timecode
        return True

    def InsertTitleIntoTimeline(self, title_name: str) -> FakeTimelineItem:
        hours, minutes, seconds, frames = (
            int(part) for part in self.current_timecode.split(":")
        )
        timeline_start = (
            ((hours * 60 + minutes) * 60 + seconds) * 60 + frames
        )
        item = FakeTimelineItem(
            f"title-{len(self.items) + 1}",
            title_name,
            timeline_start=timeline_start,
            timeline_end=timeline_start + 120,
            track_type="video",
            track_index=1,
            generated=True,
        )
        self.items.append(item)
        return item

    def InsertFusionTitleIntoTimeline(
        self, title_name: str
    ) -> FakeTimelineItem:
        hours, minutes, seconds, frames = (
            int(part) for part in self.current_timecode.split(":")
        )
        timeline_start = (
            ((hours * 60 + minutes) * 60 + seconds) * 60 + frames
        )
        item = FakeTimelineItem(
            f"animation-{len(self.items) + 1}",
            title_name,
            timeline_start=timeline_start,
            timeline_end=timeline_start + 120,
            track_type="video",
            track_index=1,
            generated=True,
            fusion_comp_count=1,
        )
        self.items.append(item)
        return item

    def GetTrackCount(self, track_type: str) -> int:
        assert track_type in {"video", "audio", "subtitle"}
        return self.track_counts[track_type]

    def GetTrackName(self, track_type: str, track_index: int) -> str:
        assert track_type == "subtitle"
        assert track_index == 1
        return "Subtitles 1"

    def AddTrack(
        self,
        track_type: str,
        sub_track_type: str | None = None,
    ) -> bool:
        assert track_type in {"video", "audio"}
        if track_type == "audio":
            assert sub_track_type == "stereo"
        else:
            assert sub_track_type is None
        self.track_counts[track_type] += 1
        self.added_tracks.append((track_type, sub_track_type))
        return True

    def GetIsTrackLocked(self, track_type: str, track_index: int) -> bool:
        assert track_type in {"video", "audio"}
        assert 1 <= track_index <= self.track_counts[track_type]
        return False

    def GetItemListInTrack(
        self,
        track_type: str,
        track_index: int,
    ) -> list[FakeTimelineItem]:
        return [
            item
            for item in self.items
            if item.GetTrackTypeAndIndex() == [track_type, track_index]
        ]

    def CreateSubtitlesFromAudio(self, settings: dict[object, object]) -> bool:
        self.subtitle_settings = settings
        self.track_counts["subtitle"] = 1
        self.items.append(
            FakeTimelineItem(
                "subtitle-1",
                "Generated caption",
                timeline_start=86400,
                timeline_end=86460,
                track_type="subtitle",
                track_index=1,
            )
        )
        return True

    def DeleteClips(
        self,
        timeline_items: list[FakeTimelineItem],
        ripple: bool,
    ) -> bool:
        assert ripple is False
        if len(timeline_items) != 1 or timeline_items[0] not in self.items:
            return False
        self.items.remove(timeline_items[0])
        return True

    def SetClipsLinked(
        self,
        timeline_items: list[FakeTimelineItem],
        linked: bool,
    ) -> bool:
        selected = set(timeline_items)
        for item in timeline_items:
            existing = [
                linked_item
                for linked_item in item._linked_items
                if linked_item not in selected
            ]
            item._linked_items = (
                existing + [peer for peer in timeline_items if peer is not item]
                if linked
                else existing
            )
        return True

    def AddMarker(
        self,
        frame: int,
        color: str,
        name: str,
        note: str,
        duration: int,
        custom_data: str,
    ) -> bool:
        self.markers[frame] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": duration,
            "custom_data": custom_data,
        }
        return True


class FakeMediaPool:
    def __init__(self, project: FakeProject) -> None:
        self._project = project
        self.root = FakeFolder()
        self.appended: list[FakeMediaItem] = []

    def GetRootFolder(self) -> FakeFolder:
        return self.root

    def ImportMedia(self, paths: list[str]) -> list[FakeMediaItem]:
        imported = [
            FakeMediaItem(f"asset-{index}", Path(path).name)
            for index, path in enumerate(paths, start=1)
        ]
        self.root.clips.extend(imported)
        return imported

    def CreateEmptyTimeline(self, name: str) -> FakeTimeline:
        timeline = FakeTimeline(f"timeline-{len(self._project.timelines) + 1}", name)
        timeline._project = self._project
        self._project.timelines.append(timeline)
        return timeline

    def AppendToTimeline(self, items: list[Any]) -> list[FakeTimelineItem]:
        if isinstance(items[0], dict):
            inserted: list[FakeTimelineItem] = []
            for raw_clip_info in items:
                clip_info = cast(dict[str, Any], raw_clip_info)
                media_item = cast(FakeMediaItem, clip_info["mediaPoolItem"])
                self.appended.append(media_item)
                track_type = (
                    "video" if clip_info["mediaType"] == 1 else "audio"
                )
                duration = clip_info["endFrame"] - clip_info["startFrame"]
                item = FakeTimelineItem(
                    f"item-{len(self.appended)}",
                    media_item.GetName(),
                    timeline_start=clip_info["recordFrame"],
                    timeline_end=clip_info["recordFrame"] + duration,
                    source_start=clip_info["startFrame"],
                    source_end=clip_info["endFrame"],
                    track_type=track_type,
                    track_index=clip_info["trackIndex"],
                )
                if self._project.current_timeline is not None:
                    self._project.current_timeline.items.append(item)
                inserted.append(item)
            return inserted
        media_items = cast(list[FakeMediaItem], items)
        self.appended.extend(media_items)
        if media_items[0].GetName().casefold().endswith(".srt"):
            subtitle = FakeTimelineItem(
                "subtitle-import-1",
                "Imported subtitle",
                timeline_start=86424,
                timeline_end=86448,
                track_type="subtitle",
                track_index=1,
            )
            if self._project.current_timeline is not None:
                self._project.current_timeline.track_counts["subtitle"] = 1
                self._project.current_timeline.items.append(subtitle)
            return [subtitle]
        item = FakeTimelineItem(
            f"item-{len(self.appended)}",
            media_items[0].GetName(),
        )
        if self._project.current_timeline is not None:
            self._project.current_timeline.items.append(item)
        return [item]


class FakeProject:
    def __init__(self) -> None:
        self.timelines: list[FakeTimeline] = []
        self.media_pool = FakeMediaPool(self)
        self.current_timeline: FakeTimeline | None = None
        self.render_jobs: list[dict[str, Any]] = []
        self.render_settings: dict[str, Any] = {}
        self.render_format = ""
        self.render_codec = ""
        self.render_mode = 0
        self.render_preset = ""
        self.rendering = False
        self.render_start_count = 0
        self.render_statuses: dict[str, dict[str, Any]] = {}
        self.force_invalid_audio_job = False

    def GetName(self) -> str:
        return "M4 Test Project"

    def GetUniqueId(self) -> str:
        return "native-project-uuid"

    def GetMediaPool(self) -> FakeMediaPool:
        return self.media_pool

    def GetTimelineCount(self) -> int:
        return len(self.timelines)

    def GetTimelineByIndex(self, index: int) -> FakeTimeline | None:
        if 1 <= index <= len(self.timelines):
            return self.timelines[index - 1]
        return None

    def GetCurrentTimeline(self) -> FakeTimeline | None:
        return self.current_timeline

    def SetCurrentTimeline(self, timeline: FakeTimeline) -> bool:
        self.current_timeline = timeline
        return True

    def GetRenderFormats(self) -> dict[str, str]:
        return {"mp4": "mp4", "Wave": "wav"}

    def GetRenderCodecs(self, render_format: str) -> dict[str, str]:
        if render_format == "mp4":
            return {"H.264": "H264"}
        assert render_format == "Wave"
        return {}

    def GetCurrentRenderFormatAndCodec(self) -> dict[str, str]:
        if self.render_format:
            return {"format": self.render_format, "codec": self.render_codec}
        return {
            "format": "mp4",
            "codec": "H264",
        }

    def GetRenderPresetList(self) -> list[str]:
        return ["YouTube - 1080p", "YouTube - 2160p", "Audio Only"]

    def LoadRenderPreset(self, preset_name: str) -> bool:
        self.render_preset = preset_name
        if preset_name == "Audio Only":
            self.render_format = "unknown"
            self.render_codec = ""
            return True
        return preset_name in {"YouTube - 1080p", "YouTube - 2160p"}

    def GetRenderResolutions(
        self,
        render_format: str,
        codec: str,
    ) -> list[dict[str, int]]:
        assert render_format == "MP4"
        assert codec == "H264"
        return [
            {"Width": 1920, "Height": 1080},
            {"Width": 3840, "Height": 2160},
        ]

    def SetCurrentRenderFormatAndCodec(
        self,
        render_format: str,
        codec: str,
    ) -> bool:
        if render_format == "Wave" and codec == "":
            return False
        self.render_format = render_format
        self.render_codec = codec
        return True

    def SetCurrentRenderMode(self, render_mode: int) -> bool:
        self.render_mode = render_mode
        return True

    def SetRenderSettings(self, settings: dict[str, Any]) -> bool:
        self.render_settings = settings
        return True

    def AddRenderJob(self) -> str:
        job_id = f"job-{len(self.render_jobs) + 1}"
        if self.render_settings["ExportVideo"] is False:
            job = {
                "JobId": job_id,
                "TargetDir": self.render_settings["TargetDir"],
                "OutputFilename": (
                    f"{self.render_settings['CustomName']}.mp4"
                    if self.force_invalid_audio_job
                    else f"{self.render_settings['CustomName']}.wav"
                ),
                "PresetName": self.render_preset,
                "IsExportVideo": False,
                "IsExportAudio": True,
                "AudioBitDepth": self.render_settings["AudioBitDepth"],
                "AudioSampleRate": self.render_settings["AudioSampleRate"],
            }
        else:
            job = {
                "JobId": job_id,
                "TargetDir": self.render_settings["TargetDir"],
                "OutputFilename": f"{self.render_settings['CustomName']}.mp4",
                "PresetName": self.render_preset,
                "VideoFormat": self.render_format,
                "VideoCodec": "H.264",
                "FormatWidth": self.render_settings["FormatWidth"],
                "FormatHeight": self.render_settings["FormatHeight"],
            }
        self.render_jobs.append(job)
        self.render_statuses[job_id] = {
            "JobStatus": "Ready",
            "CompletionPercentage": 0,
        }
        return job_id

    def DeleteRenderJob(self, job_id: str) -> bool:
        before = len(self.render_jobs)
        self.render_jobs = [
            job for job in self.render_jobs if job.get("JobId") != job_id
        ]
        self.render_statuses.pop(job_id, None)
        return len(self.render_jobs) < before

    def GetRenderJobList(self) -> list[dict[str, Any]]:
        return self.render_jobs

    def GetRenderJobStatus(self, job_id: str) -> dict[str, Any]:
        return self.render_statuses[job_id]

    def IsRenderingInProgress(self) -> bool:
        return self.rendering

    def StartRendering(
        self,
        job_ids: list[str],
        interactive: bool,
    ) -> bool:
        assert interactive is False
        if len(job_ids) != 1 or job_ids[0] not in self.render_statuses:
            return False
        self.rendering = True
        self.render_start_count += 1
        self.render_statuses[job_ids[0]] = {
            "JobStatus": "Rendering",
            "CompletionPercentage": 1,
        }
        return True


class FakeProjectManager:
    def __init__(self, project: FakeProject) -> None:
        self.project = project
        self.export_count = 0

    def GetCurrentProject(self) -> FakeProject:
        return self.project

    def SaveProject(self) -> bool:
        return True

    def ExportProject(
        self,
        project_name: str,
        file_path: str,
        with_stills_and_luts: bool,
    ) -> bool:
        assert project_name == self.project.GetName()
        assert with_stills_and_luts is False
        Path(file_path).write_text("project backup", encoding="utf-8")
        self.export_count += 1
        return True


class FakeResolve:
    SUBTITLE_LANGUAGE = "subtitle_language"
    SUBTITLE_CAPTION_PRESET = "subtitle_caption_preset"
    SUBTITLE_CHARS_PER_LINE = "subtitle_chars_per_line"
    SUBTITLE_LINE_BREAK = "subtitle_line_break"
    SUBTITLE_GAP = "subtitle_gap"
    AUTO_CAPTION_AUTO = "auto"
    AUTO_CAPTION_SUBTITLE_DEFAULT = "default"
    AUTO_CAPTION_LINE_SINGLE = "single"

    def __init__(self) -> None:
        self.project = FakeProject()
        self.project_manager = FakeProjectManager(self.project)
        self.current_page = "edit"

    def GetProductName(self) -> str:
        return "DaVinci Resolve"

    def GetVersionString(self) -> str:
        return "21.0.3.7"

    def GetVersion(self) -> list[int]:
        return [21, 0, 3, 7, 0]

    def GetProjectManager(self) -> FakeProjectManager:
        return self.project_manager

    def GetCurrentPage(self) -> str:
        return self.current_page

    def OpenPage(self, page_name: str) -> bool:
        self.current_page = page_name
        return True


def _command(
    command_id: str,
    action: str,
    arguments: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "protocol_version": "1.0",
        "command_id": command_id,
        "idempotency_key": idempotency_key or command_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "provider": "resolve",
        "action": action,
        "arguments": arguments,
        "safety": {
            "allow_destructive": allow_destructive,
            "create_backup": True,
        },
    }


def _run_command(
    root: Path,
    resolve: FakeResolve,
    state: dict[str, Any],
    command: dict[str, Any],
) -> dict[str, Any]:
    directories = ensure_runtime_directories(root)
    command_path = directories["commands"] / f"{command['command_id']}.json"
    command_path.write_text(json.dumps(command), encoding="utf-8")
    assert process_pending_commands(directories, state, resolve) == 1
    return cast(
        dict[str, Any],
        json.loads(
            (directories["responses"] / command_path.name).read_text(
                encoding="utf-8"
            )
        ),
    )


def test_create_subtitles_uses_fixed_policy_backup_and_readback(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Caption Test")
    resolve.project.current_timeline = timeline
    state = collect_bridge_state(resolve)

    response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "command-subtitles",
            "create_subtitles_from_audio",
            {"timeline_id": timeline.GetUniqueId(), "confirm_create": True},
        ),
    )

    assert response["status"] == "success"
    result = response["result"]
    assert result["previous_subtitle_item_count"] == 0
    assert result["subtitle_environment"]["subtitle_item_count"] == 1
    assert result["subtitle_environment"]["auto_caption"]["verified"] is True
    assert timeline.subtitle_settings == {
        "subtitle_language": "auto",
        "subtitle_caption_preset": "default",
        "subtitle_chars_per_line": 42,
        "subtitle_line_break": "single",
        "subtitle_gap": 0,
    }
    assert resolve.project_manager.export_count == 1
    assert state["capabilities"]["subtitle.auto_caption"] is True


def test_standard_title_insert_is_confirmed_backed_up_and_replay_safe(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Title Test")
    resolve.project.current_timeline = timeline
    state = collect_bridge_state(resolve)
    command = _command(
        "command-title",
        "insert_title",
        {
            "timeline_id": timeline.GetUniqueId(),
            "title_name": "Text",
            "timecode": "01:00:05:00",
            "confirm_insert": True,
        },
    )

    first = _run_command(tmp_path, resolve, state, command)
    replay = _run_command(tmp_path, resolve, state, command)

    assert first["status"] == "success"
    assert replay["result"] == first["result"]
    assert first["result"]["item"]["name"] == "Text"
    assert first["result"]["requested_timecode"] == "01:00:05:00"
    assert timeline.GetCurrentTimecode() == "01:00:10:00"
    assert resolve.project_manager.export_count == 1
    assert state["capabilities"]["title.insert"] is True

    list_command = _command(
        "list-title-items",
        "list_timeline_items",
        {"timeline_id": timeline.GetUniqueId()},
    )
    list_command["safety"]["create_backup"] = False
    listed = _run_command(tmp_path, resolve, state, list_command)
    title = next(
        item
        for item in listed["result"]["items"]
        if item["timeline_item_id"] == first["result"]["item"]["timeline_item_id"]
    )
    assert title["source_type"] == "generated"
    assert title["source_start_frame"] is None
    assert title["source_end_frame"] is None

    occupied = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "occupied-title",
            "insert_title",
            {
                "timeline_id": timeline.GetUniqueId(),
                "title_name": "Text",
                "timecode": "01:00:01:00",
                "confirm_insert": True,
            },
        ),
    )
    assert occupied["status"] == "error"
    assert occupied["error"]["code"] == "TITLE_APPEND_ONLY"
    assert resolve.project_manager.export_count == 1


def test_packaged_animation_template_discovery_insert_and_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))
    template_target = (
        appdata
        / "Blackmagic Design"
        / "DaVinci Resolve"
        / "Support"
        / "Fusion"
        / "Templates"
        / "Edit"
        / "Titles"
        / "DaVinci Agent Accent Card.setting"
    )
    template_target.parent.mkdir(parents=True)
    template_target.write_bytes(
        Path(
            "config/fusion_templates/Edit/Titles/"
            "DaVinci Agent Accent Card.setting"
        ).read_bytes()
    )
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Animation Test")
    timeline.items.append(
        FakeTimelineItem(
            "animation-source",
            "source.mkv",
            timeline_start=215880,
            timeline_end=216000,
        )
    )
    resolve.project.current_timeline = timeline
    state = collect_bridge_state(resolve)
    environment_command = _command(
        "animation-environment",
        "get_animation_template_environment",
        {"timeline_id": timeline.GetUniqueId()},
    )
    environment_command["safety"]["create_backup"] = False
    environment = _run_command(
        tmp_path, resolve, state, environment_command
    )

    assert environment["status"] == "success"
    assert environment["result"]["ready"] is True
    assert environment["result"]["templates"] == [
        {
            "template_id": "accent-card-v1",
            "resolve_name": "DaVinci Agent Accent Card",
            "installed": True,
            "sha256_matches": True,
        }
    ]

    command = _command(
        "insert-animation",
        "insert_animation_template",
        {
            "timeline_id": timeline.GetUniqueId(),
            "template_id": "accent-card-v1",
            "timecode": "01:00:00:00",
            "confirm_insert": True,
        },
    )
    first = _run_command(tmp_path, resolve, state, command)
    replay = _run_command(tmp_path, resolve, state, command)

    assert first["status"] == "success"
    assert replay["result"] == first["result"]
    assert first["result"]["template_id"] == "accent-card-v1"
    assert first["result"]["fusion_comp_count"] == 1
    assert first["result"]["item"]["name"] == "DaVinci Agent Accent Card"
    assert timeline.GetCurrentTimecode() == "01:00:10:00"
    assert resolve.project_manager.export_count == 1
    assert state["capabilities"]["animation.template.insert"] is True

    future = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "future-animation",
            "insert_animation_template",
            {
                "timeline_id": timeline.GetUniqueId(),
                "template_id": "accent-card-v1",
                "timecode": "01:00:03:00",
                "confirm_insert": True,
            },
        ),
    )
    assert future["status"] == "error"
    assert future["error"]["code"] == "ANIMATION_TEMPLATE_APPEND_ONLY"
    assert resolve.project_manager.export_count == 1


def test_color_environment_is_read_only_and_skips_generated_items(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Color Test")
    timeline.items.extend(
        [
            FakeTimelineItem(
                "media-video",
                "source.mkv",
                timeline_start=86400,
                timeline_end=86520,
            ),
            FakeTimelineItem(
                "generated-title",
                "Title",
                timeline_start=86520,
                timeline_end=86640,
                generated=True,
            ),
        ]
    )
    resolve.project.current_timeline = timeline
    state = collect_bridge_state(resolve)
    command = _command(
        "color-environment",
        "get_color_environment",
        {"timeline_id": timeline.GetUniqueId()},
    )
    command["safety"]["create_backup"] = False

    response = _run_command(tmp_path, resolve, state, command)

    assert response["status"] == "success"
    assert response["result"]["ready"] is True
    assert response["result"]["apply_candidate"] is True
    assert [
        item["timeline_item_id"] for item in response["result"]["items"]
    ] == ["media-video"]
    assert response["result"]["items"][0]["nodes"] == [
        {"index": 1, "label": "Primary", "lut": ""}
    ]
    assert resolve.project_manager.export_count == 0


def test_apply_color_preset_uses_fixed_cdl_and_new_local_version(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Color Apply")
    item = FakeTimelineItem(
        "media-video",
        "source.mkv",
        timeline_start=86400,
        timeline_end=86520,
    )
    timeline.items.append(item)
    resolve.project.current_timeline = timeline
    state = collect_bridge_state(resolve)

    response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "color-apply",
            "apply_color_preset",
            {
                "timeline_id": timeline.GetUniqueId(),
                "timeline_item_ids": ["media-video"],
                "preset_id": "tutorial-clean-v1",
                "confirm_apply": True,
            },
        ),
    )

    assert response["status"] == "success"
    assert response["result"]["version_name"] == (
        "DaVinci Agent Tutorial Clean v1"
    )
    assert response["result"]["items"][0]["node_index"] == 1
    assert item._current_version == "DaVinci Agent Tutorial Clean v1"
    assert item._cdl == {
        "NodeIndex": "1",
        "Slope": "1.03 1.03 1.03",
        "Offset": "0.0 0.0 0.0",
        "Power": "1.0 1.0 1.0",
        "Saturation": "1.05",
    }
    assert resolve.project_manager.export_count == 1


def test_animation_template_rejects_clamped_playhead_before_insertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))
    template_target = (
        appdata
        / "Blackmagic Design"
        / "DaVinci Resolve"
        / "Support"
        / "Fusion"
        / "Templates"
        / "Edit"
        / "Titles"
        / "DaVinci Agent Accent Card.setting"
    )
    template_target.parent.mkdir(parents=True)
    template_target.write_bytes(
        Path(
            "config/fusion_templates/Edit/Titles/"
            "DaVinci Agent Accent Card.setting"
        ).read_bytes()
    )
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Clamped Animation")
    timeline.items.append(
        FakeTimelineItem(
            "animation-source",
            "source.mkv",
            timeline_start=215880,
            timeline_end=216000,
        )
    )
    resolve.project.current_timeline = timeline
    timeline.clamp_timecode = True
    state = collect_bridge_state(resolve)

    response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "clamped-animation",
            "insert_animation_template",
            {
                "timeline_id": timeline.GetUniqueId(),
                "template_id": "accent-card-v1",
                "timecode": "01:00:00:00",
                "confirm_insert": True,
            },
        ),
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "TIMECODE_READBACK_FAILED"
    assert [item.GetUniqueId() for item in timeline.items] == [
        "animation-source"
    ]
    assert resolve.project_manager.export_count == 1


def test_append_subtitle_file_uses_import_receipt_and_append_frame(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Subtitle Placement")
    resolve.project.current_timeline = timeline
    state = collect_bridge_state(resolve)
    media_root = tmp_path / "media"
    media_root.mkdir()
    subtitle_path = media_root / "captions.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nTest\n",
        encoding="utf-8",
    )
    directories = ensure_runtime_directories(tmp_path)
    (directories["state"] / "media-policy.json").write_text(
        json.dumps({"policy_version": "1.0", "allowed_roots": [str(media_root)]}),
        encoding="utf-8",
    )
    imported = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "import-subtitle",
            "import_media",
            {"paths": [str(subtitle_path.resolve())]},
            idempotency_key="subtitle-import-key",
        ),
    )
    asset_id = imported["result"]["items"][0]["asset_id"]

    response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "append-subtitle",
            "append_subtitle_file",
            {
                "timeline_id": timeline.GetUniqueId(),
                "asset_id": asset_id,
                "subtitle_path": str(subtitle_path.resolve()),
                "import_idempotency_key": "subtitle-import-key",
                "confirm_apply": True,
            },
        ),
    )

    assert response["status"] == "success"
    assert response["result"]["timeline_start_frame"] == 86400
    assert response["result"]["append_frame"] == 86400
    assert response["result"]["items"][0]["timeline_start_frame"] == 86424
    assert state["capabilities"]["subtitle.import"] is True


def test_write_operations_create_backups_and_structured_results(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    state = collect_bridge_state(resolve)
    media_root = tmp_path / "media"
    media_root.mkdir()
    media_file = media_root / "sample.wav"
    media_file.write_bytes(b"fixture")
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    (state_directory / "media-policy.json").write_text(
        json.dumps(
            {
                "policy_version": "1.0",
                "allowed_roots": [str(media_root)],
            }
        ),
        encoding="utf-8",
    )

    imported = _run_command(
        tmp_path,
        resolve,
        state,
        _command("01-import", "import_media", {"paths": [str(media_file)]}),
    )
    created = _run_command(
        tmp_path,
        resolve,
        state,
        _command("02-create", "create_timeline", {"name": "M4 Timeline"}),
    )
    timeline_id = created["result"]["timeline"]["timeline_id"]
    asset_id = imported["result"]["items"][0]["asset_id"]
    appended = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "03-append",
            "append_clip",
            {"timeline_id": timeline_id, "asset_id": asset_id},
        ),
    )
    marker = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "04-marker",
            "add_marker",
            {
                "timeline_id": timeline_id,
                "frame": 0,
                "color": "Green",
                "name": "M4",
                "note": "safe marker",
                "duration": 1,
            },
        ),
    )

    assert imported["status"] == "success"
    assert created["status"] == "success"
    assert appended["result"]["items"][0]["name"] == "sample.wav"
    assert marker["result"]["custom_data"] == "davinci-agent:04-marker"
    assert resolve.project.timelines[0].markers[0]["color"] == "Green"
    assert len(list((tmp_path / "backups").glob("*.drp"))) == 4
    assert state["capabilities"]["media.import"] is True
    assert state["capabilities"]["timeline.create"] is True
    assert state["capabilities"]["clip.insert"] is True
    assert state["capabilities"]["marker.create"] is True
    fresh_state = collect_bridge_state(resolve)
    _apply_verified_capabilities(fresh_state, tmp_path / "state")
    assert fresh_state["capabilities"]["media.import"] is True
    assert fresh_state["capabilities"]["timeline.create"] is True
    assert fresh_state["capabilities"]["clip.insert"] is True
    assert fresh_state["capabilities"]["marker.create"] is True


def test_write_command_replays_receipt_without_duplicate_edit(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    state = collect_bridge_state(resolve)
    first = _command(
        "create-first",
        "create_timeline",
        {"name": "Idempotent"},
        idempotency_key="stable-create",
    )
    replay = _command(
        "create-replay",
        "create_timeline",
        {"name": "Idempotent"},
        idempotency_key="stable-create",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["result"] == replay_response["result"]
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert len(resolve.project.timelines) == 1
    assert resolve.project_manager.export_count == 1


def test_timeline_duplication_is_backed_up_and_replay_safe(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    source = resolve.project.media_pool.CreateEmptyTimeline("Source")
    resolve.project.SetCurrentTimeline(source)
    state = collect_bridge_state(resolve)
    arguments = {
        "timeline_id": source.GetUniqueId(),
        "name": "Source - Agent Draft",
    }
    first = _command(
        "duplicate-first",
        "duplicate_timeline",
        arguments,
        idempotency_key="stable-duplicate",
    )
    replay = _command(
        "duplicate-replay",
        "duplicate_timeline",
        arguments,
        idempotency_key="stable-duplicate",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert first_response["result"]["source_timeline"] == {
        "timeline_id": source.GetUniqueId(),
        "name": "Source",
    }
    duplicate = first_response["result"]["timeline"]
    assert duplicate["timeline_id"] != source.GetUniqueId()
    assert duplicate["name"] == "Source - Agent Draft"
    assert first_response["result"]["current_timeline"] == {
        "timeline_id": source.GetUniqueId(),
        "name": "Source",
    }
    assert resolve.project.GetTimelineCount() == 2
    assert resolve.project.GetCurrentTimeline() is source
    assert resolve.project_manager.export_count == 1
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert state["capabilities"]["timeline.duplicate"] is True


def test_timeline_tracks_are_added_with_readback_and_replay_safety(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Track Probe")
    state = collect_bridge_state(resolve)
    arguments = {
        "timeline_id": timeline.GetUniqueId(),
        "video_track_count": 2,
        "audio_track_count": 1,
    }
    first = _command(
        "tracks-first",
        "ensure_timeline_tracks",
        arguments,
        idempotency_key="stable-tracks",
    )
    replay = _command(
        "tracks-replay",
        "ensure_timeline_tracks",
        arguments,
        idempotency_key="stable-tracks",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"]["before"] == {
        "video_track_count": 1,
        "audio_track_count": 1,
    }
    assert first_response["result"]["after"] == {
        "video_track_count": 2,
        "audio_track_count": 1,
    }
    assert first_response["result"]["added"] == {
        "video_track_count": 1,
        "audio_track_count": 0,
    }
    assert timeline.added_tracks == [("video", None)]
    assert first_response["result"] == replay_response["result"]
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert resolve.project_manager.export_count == 1
    assert state["capabilities"]["timeline.track.create"] is True


def test_current_timeline_selection_is_backed_up_and_replay_safe(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    first_timeline = resolve.project.media_pool.CreateEmptyTimeline("First")
    second_timeline = resolve.project.media_pool.CreateEmptyTimeline("Second")
    resolve.project.SetCurrentTimeline(first_timeline)
    state = collect_bridge_state(resolve)
    first = _command(
        "select-first",
        "set_current_timeline",
        {"timeline_id": second_timeline.GetUniqueId()},
        idempotency_key="stable-select",
    )
    replay = _command(
        "select-replay",
        "set_current_timeline",
        first["arguments"],
        idempotency_key="stable-select",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert first_response["result"]["previous_timeline"] == {
        "timeline_id": first_timeline.GetUniqueId(),
        "name": "First",
    }
    assert first_response["result"]["timeline"] == {
        "timeline_id": second_timeline.GetUniqueId(),
        "name": "Second",
    }
    assert resolve.project.GetCurrentTimeline() is second_timeline
    assert state["current_timeline_id"] == second_timeline.GetUniqueId()
    assert state["current_timeline_name"] == "Second"
    assert resolve.project_manager.export_count == 1
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert state["capabilities"]["timeline.select"] is True


def test_ranged_clip_insert_is_backed_up_and_replay_safe(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M10 Short")
    resolve.project.SetCurrentTimeline(timeline)
    media_item = FakeMediaItem("asset-1", "source.mkv")
    resolve.project.media_pool.root.clips.append(media_item)
    state = collect_bridge_state(resolve)
    first = _command(
        "insert-first",
        "insert_clip",
        {
            "timeline_id": timeline.GetUniqueId(),
            "asset_id": media_item.GetMediaId(),
            "source_start_frame": 0,
            "source_end_frame": 240,
            "position_frames": 0,
            "track_type": "video",
            "track_index": 1,
        },
        idempotency_key="stable-insert",
    )
    replay = _command(
        "insert-replay",
        "insert_clip",
        first["arguments"],
        idempotency_key="stable-insert",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert first_response["result"]["item"] == {
        "timeline_item_id": "item-1",
        "name": "source.mkv",
        "timeline_start_frame": 86400,
        "timeline_end_frame": 86640,
        "source_start_frame": 0,
        "source_end_frame": 240,
        "track_type": "video",
        "track_index": 1,
    }
    assert len(resolve.project.media_pool.appended) == 1
    assert resolve.project_manager.export_count == 1
    assert state["capabilities"]["clip.range_insert"] is True


def test_batched_ranged_insert_uses_one_backup_and_is_replay_safe(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M42 Compacted")
    timeline.track_counts["video"] = 2
    resolve.project.SetCurrentTimeline(timeline)
    screen = FakeMediaItem("screen", "screen.mkv")
    webcam = FakeMediaItem("webcam", "webcam.mkv")
    resolve.project.media_pool.root.clips.extend([screen, webcam])
    state = collect_bridge_state(resolve)
    placements = [
        {
            "asset_id": "screen",
            "source_start_frame": 0,
            "source_end_frame": 119,
            "position_frames": 0,
            "track_type": "video",
            "track_index": 1,
        },
        {
            "asset_id": "screen",
            "source_start_frame": 0,
            "source_end_frame": 119,
            "position_frames": 0,
            "track_type": "audio",
            "track_index": 1,
        },
        {
            "asset_id": "webcam",
            "source_start_frame": 12,
            "source_end_frame": 119,
            "position_frames": 5,
            "track_type": "video",
            "track_index": 2,
        },
    ]
    first = _command(
        "batch-first",
        "insert_clips",
        {"timeline_id": timeline.GetUniqueId(), "placements": placements},
        idempotency_key="stable-batch-insert",
    )
    replay = _command(
        "batch-replay",
        "insert_clips",
        first["arguments"],
        idempotency_key="stable-batch-insert",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert len(first_response["result"]["items"]) == 3
    assert first_response["result"]["items"][2] == {
        "placement_index": 2,
        "asset_id": "webcam",
        "timeline_item_id": "item-3",
        "name": "webcam.mkv",
        "timeline_start_frame": 86405,
        "timeline_end_frame": 86512,
        "source_start_frame": 12,
        "source_end_frame": 119,
        "track_type": "video",
        "track_index": 2,
    }
    assert len(resolve.project.media_pool.appended) == 3
    assert resolve.project_manager.export_count == 1
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert state["capabilities"]["clip.range_insert"] is True


def test_clip_enabled_state_is_backed_up_read_back_and_replay_safe(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M12 Enable")
    resolve.project.SetCurrentTimeline(timeline)
    item = FakeTimelineItem("item-1", "source.mkv")
    timeline.items.append(item)
    state = collect_bridge_state(resolve)
    first = _command(
        "disable-first",
        "set_clip_enabled",
        {
            "timeline_id": timeline.GetUniqueId(),
            "timeline_item_id": item.GetUniqueId(),
            "enabled": False,
        },
        idempotency_key="stable-disable",
    )
    replay = _command(
        "disable-replay",
        "set_clip_enabled",
        first["arguments"],
        idempotency_key="stable-disable",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert first_response["result"]["previous_enabled"] is True
    assert first_response["result"]["enabled"] is False
    assert first_response["result"]["track_type"] == "video"
    assert item.GetClipEnabled() is False
    assert resolve.project_manager.export_count == 1
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert state["capabilities"]["clip.enable"] is True


def test_clip_links_are_backed_up_read_back_and_replay_safe(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M19 Links")
    resolve.project.SetCurrentTimeline(timeline)
    video = FakeTimelineItem("video-1", "source.mkv", track_type="video")
    audio = FakeTimelineItem("audio-1", "source.mkv", track_type="audio")
    timeline.items.extend([video, audio])
    state = collect_bridge_state(resolve)
    arguments = {
        "timeline_id": timeline.GetUniqueId(),
        "timeline_item_ids": ["video-1", "audio-1"],
        "linked": True,
    }
    first = _command(
        "link-first",
        "set_clips_linked",
        arguments,
        idempotency_key="stable-link",
    )
    replay = _command(
        "link-replay",
        "set_clips_linked",
        arguments,
        idempotency_key="stable-link",
    )
    unlink = _command(
        "unlink",
        "set_clips_linked",
        {**arguments, "linked": False},
        idempotency_key="stable-unlink",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert first_response["result"]["linked"] is True
    assert first_response["result"]["items"][0]["linked_item_ids"] == [
        "audio-1"
    ]
    assert first_response["result"]["items"][1]["linked_item_ids"] == [
        "video-1"
    ]
    assert resolve.project_manager.export_count == 1
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]

    unlink_response = _run_command(tmp_path, resolve, state, unlink)

    assert unlink_response["status"] == "success"
    assert unlink_response["result"]["linked"] is False
    assert all(
        not item["linked_item_ids"]
        for item in unlink_response["result"]["items"]
    )
    assert resolve.project_manager.export_count == 2
    assert state["capabilities"]["clip.link"] is True


def test_clip_link_groups_are_independent_with_one_backup(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M43 Links")
    resolve.project.SetCurrentTimeline(timeline)
    items = [
        FakeTimelineItem(f"item-{index}", "source.mkv")
        for index in range(4)
    ]
    timeline.items.extend(items)
    state = collect_bridge_state(resolve)
    command = _command(
        "group-links",
        "set_clip_link_groups",
        {
            "timeline_id": timeline.GetUniqueId(),
            "groups": [["item-0", "item-1"], ["item-2", "item-3"]],
            "linked": True,
        },
        idempotency_key="stable-group-links",
    )

    first = _run_command(tmp_path, resolve, state, command)
    replay = _run_command(tmp_path, resolve, state, command)

    assert first["status"] == "success"
    assert first["result"] == replay["result"]
    assert first["result"]["groups"][0]["items"][0][
        "linked_item_ids"
    ] == ["item-1"]
    assert first["result"]["groups"][1]["items"][0][
        "linked_item_ids"
    ] == ["item-3"]
    assert resolve.project_manager.export_count == 1


def test_clip_transforms_batch_uses_one_backup_and_replays(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M43 PIP")
    resolve.project.SetCurrentTimeline(timeline)
    items = [
        FakeTimelineItem(f"webcam-{index}", "webcam.mkv")
        for index in range(2)
    ]
    timeline.items.extend(items)
    state = collect_bridge_state(resolve)
    command = _command(
        "batch-transform",
        "set_clip_transforms",
        {
            "timeline_id": timeline.GetUniqueId(),
            "items": [
                {
                    "timeline_item_id": item.GetUniqueId(),
                    "position_x": 614.4,
                    "position_y": -345.6,
                    "zoom": 0.25,
                }
                for item in items
            ],
        },
        idempotency_key="stable-batch-transform",
    )

    first = _run_command(tmp_path, resolve, state, command)
    replay = _run_command(tmp_path, resolve, state, command)

    assert first["status"] == "success"
    assert first["result"] == replay["result"]
    assert [item["properties"]["ZoomX"] for item in first["result"]["items"]] == [
        0.25,
        0.25,
    ]
    assert resolve.project_manager.export_count == 1


def test_timeline_items_are_discovered_with_bounded_metadata(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M16 Items")
    resolve.project.SetCurrentTimeline(timeline)
    timeline.items.extend(
        [
            FakeTimelineItem(
                "video-1",
                "screen.mkv",
                timeline_start=86400,
                timeline_end=86640,
                source_start=0,
                source_end=240,
                track_type="video",
            ),
            FakeTimelineItem(
                "audio-1",
                "screen.mkv",
                timeline_start=86400,
                timeline_end=86640,
                source_start=0,
                source_end=240,
                track_type="audio",
            ),
        ]
    )
    state = collect_bridge_state(resolve)
    command = _command(
        "list-items",
        "list_timeline_items",
        {"timeline_id": timeline.GetUniqueId()},
    )
    command["safety"]["create_backup"] = False

    response = _run_command(tmp_path, resolve, state, command)

    assert response["status"] == "success"
    assert response["result"]["timeline_id"] == timeline.GetUniqueId()
    assert response["result"]["items"] == [
        {
            "timeline_item_id": "video-1",
                "name": "screen.mkv",
                "track_type": "video",
                "track_index": 1,
                "source_type": "media",
                "duration_frames": 240,
            "timeline_start_frame": 86400,
            "timeline_end_frame": 86640,
            "source_start_frame": 0,
            "source_end_frame": 240,
        },
        {
            "timeline_item_id": "audio-1",
                "name": "screen.mkv",
                "track_type": "audio",
                "track_index": 1,
                "source_type": "media",
                "duration_frames": 240,
            "timeline_start_frame": 86400,
            "timeline_end_frame": 86640,
            "source_start_frame": 0,
            "source_end_frame": 240,
        },
    ]
    assert resolve.project_manager.export_count == 0
    assert state["capabilities"]["clip.read"] is True


def test_media_pool_items_are_discovered_recursively_without_backup(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    root = resolve.project.media_pool.root
    root.clips.append(FakeMediaItem("asset-root", "screen.mkv"))
    child = FakeFolder("folder-child", "Interviews")
    child.clips.append(FakeMediaItem("asset-child", "webcam.mkv"))
    root.subfolders.append(child)
    state = collect_bridge_state(resolve)
    command = _command("list-items", "list_media_pool_items", {})
    command["safety"]["create_backup"] = False

    response = _run_command(tmp_path, resolve, state, command)

    assert response["status"] == "success"
    assert response["result"] == {
        "items": [
            {
                "asset_id": "asset-root",
                "name": "screen.mkv",
                "folder_id": "folder-root",
                "folder_path": ["Master"],
            },
            {
                "asset_id": "asset-child",
                "name": "webcam.mkv",
                "folder_id": "folder-child",
                "folder_path": ["Master", "Interviews"],
            },
        ],
        "folder_count": 2,
    }
    assert resolve.project_manager.export_count == 0
    assert state["capabilities"]["media.read"] is True


def test_editing_metadata_is_bounded_read_only_discovery(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = FakeTimeline("timeline-draft", "sMailer M5 Draft")
    resolve.project.timelines.append(timeline)
    resolve.project.current_timeline = timeline
    resolve.project.media_pool.root.clips.append(
        FakeMediaItem("asset-screen", "screen.mkv")
    )
    state = collect_bridge_state(resolve)
    command = _command(
        "editing-metadata",
        "get_editing_metadata",
        {"timeline_id": "timeline-draft", "asset_ids": ["asset-screen"]},
    )
    command["safety"]["create_backup"] = False

    response = _run_command(tmp_path, resolve, state, command)

    assert response["status"] == "success"
    assert response["result"] == {
        "timeline": {
            "timeline_id": "timeline-draft",
            "name": "sMailer M5 Draft",
            "video_track_count": 1,
            "audio_track_count": 1,
            "frame_rate": 60.0,
            "resolution_width": 1920,
            "resolution_height": 1080,
        },
        "assets": [
            {
                "asset_id": "asset-screen",
                "name": "screen.mkv",
                "duration_frames": 240,
                "frame_rate": 60.0,
            }
        ],
    }
    assert resolve.project_manager.export_count == 0
    assert state["capabilities"]["media.metadata.read"] is True


def test_editing_metadata_supports_timeline_only_discovery(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = FakeTimeline("timeline-target", "M55 Target")
    resolve.project.timelines.append(timeline)
    resolve.project.current_timeline = timeline
    state = collect_bridge_state(resolve)
    command = _command(
        "timeline-only-metadata",
        "get_editing_metadata",
        {"timeline_id": "timeline-target", "asset_ids": []},
    )
    command["safety"]["create_backup"] = False

    response = _run_command(tmp_path, resolve, state, command)

    assert response["status"] == "success"
    assert response["result"] == {
        "timeline": {
            "timeline_id": "timeline-target",
            "name": "M55 Target",
            "video_track_count": 1,
            "audio_track_count": 1,
            "frame_rate": 60.0,
            "resolution_width": 1920,
            "resolution_height": 1080,
        },
        "assets": [],
    }
    assert resolve.project_manager.export_count == 0
    assert state["capabilities"]["media.metadata.read"] is True


def test_workspace_snapshot_collects_read_only_sections_in_one_command(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = FakeTimeline("timeline-main", "Main")
    timeline.items.append(
        FakeTimelineItem(
            "item-video",
            "screen.mkv",
            timeline_start=86400,
            timeline_end=86496,
            source_start=0,
            source_end=240,
        )
    )
    resolve.project.timelines.append(timeline)
    resolve.project.current_timeline = timeline
    resolve.project.media_pool.root.clips.append(
        FakeMediaItem("asset-screen", "screen.mkv")
    )
    state = collect_bridge_state(resolve)
    command = _command("workspace-snapshot", "get_workspace_snapshot", {})
    command["safety"]["create_backup"] = False

    response = _run_command(tmp_path, resolve, state, command)

    assert response["status"] == "success"
    result = response["result"]
    assert result["project"] == {"name": "M4 Test Project"}
    assert result["current_timeline"] == {
        "timeline_id": "timeline-main",
        "name": "Main",
    }
    assert result["timeline_items"]["items"][0]["timeline_item_id"] == (
        "item-video"
    )
    assert result["media_pool"]["items"][0]["asset_id"] == "asset-screen"
    assert result["render"]["current"] == {
        "format": "mp4",
        "codec": "H264",
    }
    assert not list((tmp_path / "backups").glob("*.drp"))
    assert state["capabilities"]["clip.read"] is True
    assert state["capabilities"]["media.read"] is True
    assert state["capabilities"]["render.discovery"] is True


def test_clip_transform_is_bounded_backed_up_and_replay_safe(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M14 Transform")
    resolve.project.SetCurrentTimeline(timeline)
    item = FakeTimelineItem("item-1", "source.mkv")
    timeline.items.append(item)
    state = collect_bridge_state(resolve)
    arguments = {
        "timeline_id": timeline.GetUniqueId(),
        "timeline_item_id": item.GetUniqueId(),
        "position_x": 320.0,
        "position_y": -180.0,
        "zoom": 0.5,
        "rotation_degrees": 5.0,
        "opacity_percent": 80.0,
    }
    first = _command(
        "transform-first",
        "set_clip_transform",
        arguments,
        idempotency_key="stable-transform",
    )
    replay = _command(
        "transform-replay",
        "set_clip_transform",
        arguments,
        idempotency_key="stable-transform",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert first_response["result"]["previous_properties"] == {
        "Pan": 0.0,
        "Tilt": 0.0,
        "ZoomGang": True,
        "ZoomX": 1.0,
        "ZoomY": 1.0,
        "RotationAngle": 0.0,
        "Opacity": 100.0,
    }
    assert first_response["result"]["properties"] == {
        "Pan": 320.0,
        "Tilt": -180.0,
        "ZoomGang": True,
        "ZoomX": 0.5,
        "ZoomY": 0.5,
        "RotationAngle": 5.0,
        "Opacity": 80.0,
    }
    assert resolve.project_manager.export_count == 1
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert state["capabilities"]["clip.transform"] is True


def test_clip_delete_requires_destructive_flag_backup_and_replays_safely(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M15 Delete")
    resolve.project.SetCurrentTimeline(timeline)
    item = FakeTimelineItem(
        "item-1",
        "disposable.mkv",
        timeline_start=86400,
        timeline_end=86640,
    )
    timeline.items.append(item)
    state = collect_bridge_state(resolve)
    arguments = {
        "timeline_id": timeline.GetUniqueId(),
        "timeline_item_id": item.GetUniqueId(),
        "confirm_delete": True,
    }
    first = _command(
        "delete-first",
        "delete_clip",
        arguments,
        idempotency_key="stable-delete",
        allow_destructive=True,
    )
    replay = _command(
        "delete-replay",
        "delete_clip",
        arguments,
        idempotency_key="stable-delete",
        allow_destructive=True,
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert first_response["result"]["deleted"] is True
    assert first_response["result"]["ripple"] is False
    assert first_response["result"]["item"] == {
        "timeline_item_id": "item-1",
        "name": "disposable.mkv",
        "timeline_start_frame": 86400,
        "timeline_end_frame": 86640,
        "track_type": "video",
        "track_index": 1,
    }
    assert timeline.items == []
    assert resolve.project_manager.export_count == 1
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert state["capabilities"]["clip.delete"] is True


def test_import_outside_bridge_policy_is_rejected_before_backup(
    tmp_path: Path,
) -> None:
    resolve = FakeResolve()
    state = collect_bridge_state(resolve)
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside_file = tmp_path / "outside.wav"
    outside_file.write_bytes(b"fixture")
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    (state_directory / "media-policy.json").write_text(
        json.dumps(
            {
                "policy_version": "1.0",
                "allowed_roots": [str(allowed_root)],
            }
        ),
        encoding="utf-8",
    )

    response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "blocked-import",
            "import_media",
            {"paths": [str(outside_file)]},
        ),
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "MEDIA_PATH_NOT_ALLOWED"
    assert not list((tmp_path / "backups").glob("*.drp"))


def test_prepare_render_job_is_backed_up_and_replay_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Render Timeline")
    resolve.project.SetCurrentTimeline(timeline)
    state = collect_bridge_state(resolve)
    first = _command(
        "render-first",
        "prepare_render_job",
        {"custom_name": "M7 Test"},
        idempotency_key="stable-render",
    )
    replay = _command(
        "render-replay",
        "prepare_render_job",
        {"custom_name": "M7 Test"},
        idempotency_key="stable-render",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert first_response["result"]["started"] is False
    assert first_response["result"]["format"] == "MP4"
    assert first_response["result"]["codec"] == "H264"
    assert first_response["result"]["width"] == 1920
    assert first_response["result"]["height"] == 1080
    assert Path(first_response["result"]["target_directory"]) == (
        tmp_path
        / "local"
        / "DaVinciResolveAgent"
        / "media"
        / "renders"
    )
    assert len(resolve.project.render_jobs) == 1
    assert resolve.project_manager.export_count == 1
    assert state["capabilities"]["render.configure"] is True
    assert resolve.current_page == "edit"


def test_prepare_render_job_selects_and_reports_explicit_timeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    resolve = FakeResolve()
    other = resolve.project.media_pool.CreateEmptyTimeline("Other Timeline")
    target = resolve.project.media_pool.CreateEmptyTimeline("M44 Final")
    resolve.project.SetCurrentTimeline(other)
    state = collect_bridge_state(resolve)

    response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "render-explicit-timeline",
            "prepare_render_job",
            {
                "custom_name": "M44 Final",
                "timeline_id": target.GetUniqueId(),
                "profile": "youtube-1080p-h264-v1",
            },
        ),
    )

    assert response["status"] == "success"
    assert response["result"]["timeline_id"] == target.GetUniqueId()
    assert response["result"]["timeline_name"] == "M44 Final"
    assert resolve.project.GetCurrentTimeline() is target
    assert resolve.project_manager.export_count == 1


def test_prepare_render_job_supports_verified_fixed_4k_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("4K Timeline")
    resolve.project.SetCurrentTimeline(timeline)
    state = collect_bridge_state(resolve)

    response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "render-4k",
            "prepare_render_job",
            {
                "custom_name": "M9 4K Test",
                "profile": "youtube-2160p-h264-v1",
            },
        ),
    )

    assert response["status"] == "success"
    assert response["result"]["preset"] == "youtube-2160p-h264-v1"
    assert response["result"]["resolve_preset"] == "YouTube - 2160p"
    assert response["result"]["width"] == 3840
    assert response["result"]["height"] == 2160
    assert resolve.project.render_jobs[0]["FormatWidth"] == 3840
    assert resolve.project.render_jobs[0]["FormatHeight"] == 2160


def test_prepare_and_start_fixed_audio_only_wav_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M46 Audio")
    resolve.project.SetCurrentTimeline(timeline)
    state = collect_bridge_state(resolve)

    prepared = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "audio-prepare",
            "prepare_render_job",
            {
                "custom_name": "M46 Dialogue Source",
                "timeline_id": timeline.GetUniqueId(),
                "profile": "audio-only-pcm-wav-v1",
            },
            idempotency_key="stable-audio-prepare",
        ),
    )

    assert prepared["status"] == "success"
    assert prepared["result"]["format"] == "Wave"
    assert prepared["result"]["codec"] == ""
    assert prepared["result"]["export_video"] is False
    assert prepared["result"]["export_audio"] is True
    assert prepared["result"]["audio_bit_depth"] == 16
    assert prepared["result"]["audio_sample_rate"] == 48_000
    assert Path(prepared["result"]["target_directory"]) == (
        tmp_path
        / "local"
        / "DaVinciResolveAgent"
        / "media"
        / "audio-sources"
    )

    started = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "audio-start",
            "start_render_job",
            {"job_id": prepared["result"]["job_id"]},
            idempotency_key="stable-audio-start",
        ),
    )
    assert started["status"] == "success"
    assert started["result"]["started"] is True


def test_invalid_audio_only_job_is_removed_before_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    resolve = FakeResolve()
    resolve.project.force_invalid_audio_job = True
    timeline = resolve.project.media_pool.CreateEmptyTimeline("M46 Audio")
    resolve.project.SetCurrentTimeline(timeline)
    state = collect_bridge_state(resolve)

    response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "audio-invalid",
            "prepare_render_job",
            {
                "custom_name": "M46 Invalid",
                "timeline_id": timeline.GetUniqueId(),
                "profile": "audio-only-pcm-wav-v1",
            },
        ),
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "RENDER_JOB_POLICY_MISMATCH"
    assert response["error"]["details"]["job_deleted"] is True
    assert resolve.project.render_jobs == []


def test_agent_prepared_render_job_starts_once_and_reports_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    resolve = FakeResolve()
    timeline = resolve.project.media_pool.CreateEmptyTimeline("Render Timeline")
    resolve.project.SetCurrentTimeline(timeline)
    state = collect_bridge_state(resolve)
    prepared = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "render-prepare",
            "prepare_render_job",
            {"custom_name": "M8 Test"},
            idempotency_key="stable-prepare",
        ),
    )
    job_id = prepared["result"]["job_id"]
    first = _command(
        "render-start",
        "start_render_job",
        {"job_id": job_id},
        idempotency_key="stable-start",
    )
    replay = _command(
        "render-start-replay",
        "start_render_job",
        {"job_id": job_id},
        idempotency_key="stable-start",
    )

    first_response = _run_command(tmp_path, resolve, state, first)
    replay_response = _run_command(tmp_path, resolve, state, replay)
    status_command = _command(
        "render-status",
        "get_render_job_status",
        {"job_id": job_id},
    )
    status_command["safety"]["create_backup"] = False
    status_response = _run_command(
        tmp_path,
        resolve,
        state,
        status_command,
    )
    resolve.project.rendering = False
    second_key_response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "render-start-second-key",
            "start_render_job",
            {"job_id": job_id},
            idempotency_key="different-start-key",
        ),
    )

    assert first_response["status"] == "success"
    assert first_response["result"] == replay_response["result"]
    assert first_response["result"]["started"] is True
    assert first_response["result"]["status"]["JobStatus"] == "Rendering"
    assert replay_response["warnings"] == [
        "Returned the stored idempotent result."
    ]
    assert status_response["result"]["rendering_in_progress"] is True
    assert second_key_response["status"] == "error"
    assert second_key_response["error"]["code"] == "RENDER_JOB_ALREADY_STARTED"
    assert resolve.project.render_start_count == 1
    assert resolve.project_manager.export_count == 2
    assert state["capabilities"]["render.start"] is True
    assert len(list((tmp_path / "state" / "render-starts").glob("*.json"))) == 1


def test_unprepared_render_job_cannot_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    resolve = FakeResolve()
    resolve.project.render_jobs.append(
        {
            "JobId": "foreign-job",
            "TargetDir": str(tmp_path),
            "OutputFilename": "foreign.mp4",
            "PresetName": "YouTube - 1080p",
            "VideoFormat": "MP4",
            "VideoCodec": "H.264",
        }
    )
    resolve.project.render_statuses["foreign-job"] = {"JobStatus": "Ready"}
    state = collect_bridge_state(resolve)

    response = _run_command(
        tmp_path,
        resolve,
        state,
        _command(
            "foreign-start",
            "start_render_job",
            {"job_id": "foreign-job"},
        ),
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "RENDER_JOB_NOT_AGENT_PREPARED"
    assert resolve.project.render_start_count == 0
    assert resolve.project_manager.export_count == 0
