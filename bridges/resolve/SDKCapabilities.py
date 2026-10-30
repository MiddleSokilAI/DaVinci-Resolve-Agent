"""SDK interface availability observed from actual native object bindings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bridges.resolve.ResolveBridge import atomic_write_json

REQUIRED: dict[str, dict[str, tuple[str, ...]]] = {
    "project.close": {
        "ProjectManager": (
            "CloseProject", "SaveProject", "ExportProject", "GetCurrentProject"
        ),
        "Project": ("GetUniqueId", "IsRenderingInProgress"),
    },
    "project.create": {"ProjectManager": ("CreateProject", "SaveProject")},
    "timeline.create": {"MediaPool": ("CreateEmptyTimeline",)},
    "timeline.track.create": {"Timeline": ("AddTrack",)},
    "timeline.duplicate": {"Timeline": ("DuplicateTimeline",)},
    "timeline.select": {"Project": ("SetCurrentTimeline",)},
    "media.import": {"MediaPool": ("ImportMedia",)},
    "media.read": {"Folder": ("GetClipList", "GetSubFolderList")},
    "media.metadata.read": {"MediaPoolItem": ("GetClipProperty",)},
    "clip.insert": {"MediaPool": ("AppendToTimeline",)},
    "clip.range_insert": {"MediaPool": ("AppendToTimeline",)},
    "clip.read": {
        "TimelineItem": ("GetUniqueId", "GetStart", "GetEnd", "GetTrackTypeAndIndex")
    },
    "clip.enable": {"TimelineItem": ("SetClipEnabled", "GetClipEnabled")},
    "clip.link": {"Timeline": ("SetClipsLinked",), "TimelineItem": ("GetLinkedItems",)},
    "clip.transform": {"TimelineItem": ("SetProperty", "GetProperty")},
    "clip.delete": {"Timeline": ("DeleteClips",)},
    "marker.create": {"Timeline": ("AddMarker",)},
    "title.insert": {"Timeline": ("InsertTitleIntoTimeline",)},
    "animation.template.insert": {"Timeline": ("InsertFusionTitleIntoTimeline",)},
    "subtitle.read": {"Timeline": ("GetTrackCount", "GetItemListInTrack")},
    "subtitle.import": {"MediaPool": ("ImportMedia", "AppendToTimeline")},
    "subtitle.auto_caption": {"Timeline": ("CreateSubtitlesFromAudio",)},
    "color.inspect": {"Graph": ("GetNumNodes", "GetNodeLabel", "GetLUT")},
    "color.cdl.apply": {"TimelineItem": ("AddVersion", "LoadVersionByName", "SetCDL")},
    "render.discovery": {
        "Project": ("GetRenderFormats", "GetRenderCodecs", "GetRenderPresetList")
    },
    "render.configure": {
        "Project": ("SetRenderSettings", "AddRenderJob", "LoadRenderPreset")
    },
    "render.start": {"Project": ("StartRendering", "GetRenderJobStatus")},
}


def augment_sdk_state(
    state: dict[str, Any],
    native: Any,
    state_directory: Path,
    *,
    discover: bool = False,
    transport: str = "lua_marker_preset",
) -> None:
    """Keep bindings scoped to the observed Resolve product and version.

    An interface is callable subject to the canonical per-command preflights;
    presence does not establish successful editing or bypass edition checks.
    """
    sdk = getattr(native, "_sdk", None)
    path = state_directory / "sdk-surface.json"
    identity = {key: state[key] for key in ("product_name", "resolve_version")}
    observed: dict[str, list[str]] = {}
    if path.is_file() and not path.is_symlink():
        try:
            if path.stat().st_size > 1048576:
                raise ValueError("Oversized discovery cache")
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("identity") == identity:
                kinds = cached.get("types", {})
                if isinstance(kinds, dict) and all(
                    isinstance(key, str)
                    and isinstance(value, list)
                    and all(isinstance(method, str) for method in value)
                    for key, value in kinds.items()
                ):
                    observed = kinds
        except (OSError, ValueError, RecursionError, AttributeError):
            pass  # Optional discovery cache, never an operation receipt.

    def observe(kind: str, obj: Any) -> None:
        if obj is None or sdk is not None:
            return
        names = {
            method for classes in REQUIRED.values() for method in classes.get(kind, ())
        }
        observed[kind] = sorted(
            name for name in names if callable(getattr(obj, name, None))
        )

    manager = native.GetProjectManager()
    observe("ProjectManager", manager)
    project = manager.GetCurrentProject()
    observe("Project", project)
    if project is not None:
        observe("MediaPool", project.GetMediaPool())
        observe("Timeline", project.GetCurrentTimeline())
    if discover and project is not None:
        pool = project.GetMediaPool()
        if pool is not None:
            folder = pool.GetRootFolder()
            observe("Folder", folder)
            clips = folder.GetClipList()
            if clips:
                observe("MediaPoolItem", clips[0])
        timeline = project.GetCurrentTimeline()
        if timeline is None and project.GetTimelineCount():
            timeline = project.GetTimelineByIndex(1)
        if timeline is not None:
            for kind in ("video", "audio"):
                if timeline.GetTrackCount(kind):
                    items = timeline.GetItemListInTrack(kind, 1)
                    if items:
                        observe("TimelineItem", items[0])
                    if (
                        items
                        and kind == "video"
                        and callable(getattr(items[0], "GetNodeGraph", None))
                    ):
                        observe("Graph", items[0].GetNodeGraph())
    if sdk is not None:
        observed.update(
            {kind: sorted(methods) for kind, methods in sdk.surfaces.items()}
        )
    atomic_write_json(path, {"identity": identity, "types": observed})
    for capability, classes in REQUIRED.items():
        if any(kind not in observed for kind in classes):
            state["capabilities"].setdefault(capability, "unknown")
            continue
        state["capabilities"][capability] = all(
            set(methods) <= set(observed[kind]) for kind, methods in classes.items()
        )
    studio = "studio" in str(state["product_name"]).lower()
    state["edition"] = "studio" if studio else "free"
    if not studio:
        state["capabilities"]["subtitle.auto_caption"] = False
    current = native.GetProjectManager().GetCurrentProject()
    state["native_connection"] = {
        "transport": transport,
        "interface_evidence": "native_sdk_bindings",
        "project_id": None if current is None else current.GetUniqueId(),
    }
