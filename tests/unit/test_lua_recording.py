"""Native recording-preparation boundaries exercised with real Lua 5.1.

Resolve, a microphone stream and the desktop are never opened by these tests.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.lua_recording import validate_recording

LuaRuntime = import_module("lupa.lua51").LuaRuntime
LuaError = import_module("lupa.lua51").LuaError
MODULE = Path(__file__).resolve().parents[2] / "bridges/resolve/ResolveLuaRecording.lua"


def envelope(operation: str, **values: Any) -> dict[str, Any]:
    """Create one typed request without executable source interpolation."""
    return {"timeline_name": "Голос", "recording": {"operation": operation, **values}}


@pytest.mark.parametrize(
    "action,arguments",
    [
        ("get_timeline_summary", envelope("inspect", track_name="Чистий голос")),
        (
            "set_clip_properties",
            envelope(
                "ensure_mono", track_name="Fifine RAW", expected_audio_track_count=1
            ),
        ),
        ("set_clip_properties", envelope("seek", timecode="01:00:00:00")),
        (
            "set_clip_properties",
            envelope("seek", timecode="01:00:00:00", page="fairlight"),
        ),
    ],
)
def test_recording_validation(action: str, arguments: dict[str, Any]) -> None:
    assert validate_recording(action, arguments, []) == arguments


@pytest.mark.parametrize(
    "action,arguments",
    [
        ("set_clip_properties", envelope("inspect", track_name="RAW")),
        ("get_timeline_summary", envelope("seek", timecode="01:00:00:00")),
        ("get_timeline_summary", envelope("inspect", track_name="RAW", enabled=False)),
        ("get_timeline_summary", envelope("inspect", track_name=" ")),
        ("get_timeline_summary", envelope("inspect", track_name="a\nb")),
        (
            "set_clip_properties",
            envelope("ensure_mono", track_name="RAW", expected_audio_track_count=True),
        ),
        (
            "set_clip_properties",
            envelope("ensure_mono", track_name="RAW", expected_audio_track_count=65),
        ),
        ("set_clip_properties", envelope("seek", timecode="24:00:00:00")),
        ("set_clip_properties", envelope("seek", timecode="01:60:00:00")),
        ("set_clip_properties", envelope("seek", timecode="01:00:00;00")),
        ("set_clip_properties", envelope("seek", timecode="01:00:00:00", page=[])),
        ("set_clip_properties", envelope("seek", timecode="01:00:00:00", page="color")),
        ("set_clip_properties", envelope("record")),
        ("get_timeline_summary", {**envelope("inspect", track_name="RAW"), "code": ""}),
        ("get_timeline_summary", {"timeline_name": "Голос", "recording": False}),
    ],
)
def test_recording_rejects_unknown_or_wrong_action(
    action: str, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        validate_recording(action, arguments, [])


def test_media_root_is_existing_resolved_and_allowlisted(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    capture = allowed / "Український голос"
    capture.mkdir(parents=True)
    result = validate_recording(
        "set_clip_properties",
        envelope("configure_media_root", media_root=str(capture)),
        [allowed],
    )
    assert result["recording"]["media_root"] == capture.resolve().as_posix()
    for value in (tmp_path, capture / "absent", Path("relative")):
        with pytest.raises(ValueError):
            validate_recording(
                "set_clip_properties",
                envelope("configure_media_root", media_root=str(value)),
                [allowed],
            )
    with pytest.raises(ValueError):
        validate_recording(
            "get_timeline_summary",
            envelope("configure_media_root", media_root=str(capture)),
            [allowed],
        )


def test_media_root_rejects_resolved_symlink_escape(tmp_path: Path) -> None:
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    link = allowed / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("The host cannot create test directory symlinks.")
    with pytest.raises(ValueError, match="outside allowed roots"):
        validate_recording(
            "set_clip_properties",
            envelope("configure_media_root", media_root=str(link)),
            [allowed],
        )


class NativeRecording:
    """Supply stateful native doubles and retain all mutation evidence."""

    def __init__(self) -> None:
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute("""
            calls={};page='fairlight';timecode='01:18:33:00';mode='ok'
            location='C:/Old';rate='48000'
            tracks={{name='Audio 1',subtype='stereo',
                items={{id='original'},__flags=0}}}
            function note(name) calls[#calls+1]=name end
            target={
                GetName=function() return 'Голос' end,
                GetUniqueId=function() return 'timeline-target' end,
                GetTrackCount=function(_,kind) assert(kind=='audio');return #tracks end,
                GetTrackName=function(_,kind,i)
                    assert(kind=='audio');return tracks[i].name
                end,
                GetTrackSubType=function(_,kind,i)
                    assert(kind=='audio');return tracks[i].subtype
                end,
                GetItemListInTrack=function(_,kind,i)
                    assert(kind=='audio');return tracks[i].items
                end,
                AddTrack=function(_,kind,subtype)
                    note('add');assert(kind=='audio' and subtype=='mono')
                    tracks[#tracks+1]={name='Audio '..(#tracks+1),
                        subtype=mode=='wrong-subtype' and 'stereo' or subtype,
                        items={__flags=0}}
                    return mode~='create-fails'
                end,
                SetTrackName=function(_,kind,i,name)
                    note('rename');assert(kind=='audio')
                    if mode=='rename-fails' then return false end
                    tracks[i].name=mode=='rename-ignored' and 'Wrong' or name
                    return true
                end,
                SetCurrentTimecode=function(_,value)
                    note('seek')
                    timecode=mode=='seek-readback' and '01:00:00:01' or value
                    return mode~='seek-fails'
                end,
                GetCurrentTimecode=function() return timecode end,
                DeleteTrack=function() error('track deletion forbidden') end,
            }
            other={GetUniqueId=function() return 'other' end};current=other
            project={
                GetTimelineCount=function() return 1 end,
                GetTimelineByIndex=function() return target end,
                SetCurrentTimeline=function(_,t)
                    note('select');current=t;return mode~='select-fails'
                end,
                GetCurrentTimeline=function() return current end,
                GetSettings=function()
                    return {timelineSampleRate=rate,projectMediaLocation=location}
                end,
                SetSettings=function(_,settings)
                    note('settings')
                    assert(settings.projectMediaLocation
                        and not settings.timelineSampleRate)
                    location=mode=='settings-ignored' and 'C:/Wrong'
                        or settings.projectMediaLocation
                    return mode~='settings-fails'
                end,
                StartRecording=function() error('recording forbidden') end,
            }
            api={
                GetCurrentPage=function() return page end,
                OpenPage=function(_,value)
                    note('page');page=value;return mode~='page-fails'
                end,
            }
        """)
        self.module = self.lua.execute(MODULE.read_text(encoding="utf-8"))

    def call(self, function: str, operation: str, **values: Any) -> str:
        """Execute the real Lua module with data-only request arguments."""
        return str(
            self.module[function](
                self.lua.globals().api,
                self.lua.globals().project,
                self.lua.table_from(envelope(operation, **values), recursive=True),
                self.lua.table_from(["C:/Allowed"]),
            )
        )

    def calls(self) -> list[str]:
        """Return ordered mutations without calling a native getter or setter."""
        return list(self.lua.globals().calls.values())


def test_inspection_is_read_only_and_distinguishes_missing_track() -> None:
    native = NativeRecording()
    assert native.call("inspect", "inspect", track_name="Missing") == (
        "recording_inspect_0_1_0_missing_48000_0"
    )
    assert native.call("inspect", "inspect", track_name="Audio 1") == (
        "recording_inspect_1_1_1_stereo_48000_0"
    )
    assert native.calls() == []
    assert native.lua.eval("current==other")


@pytest.mark.parametrize("clip_count", [0, 1, 3])
@pytest.mark.parametrize("flags", [0, 7, -1])
def test_native_list_flags_do_not_count_as_clips(clip_count: int, flags: int) -> None:
    native = NativeRecording()
    items: dict[str | int, Any] = {"__flags": flags}
    for index in range(1, clip_count + 1):
        items[index] = {"id": f"original-{index}"}
    native.lua.globals().tracks[1]["items"] = native.lua.table_from(
        items, recursive=True
    )
    assert native.call("inspect", "inspect", track_name="Audio 1") == (
        f"recording_inspect_1_1_{clip_count}_stereo_48000_0"
    )
    assert native.calls() == []


@pytest.mark.parametrize(
    "items",
    [
        "{unknown=0}",
        "{__flags='0'}",
        "{__flags=true}",
        "{__flags={}}",
        "{__flags=math.huge}",
        "{__flags=0/0}",
        "{[0]={},__flags=0}",
        "{[-1]={},__flags=0}",
        "{[1.5]={},__flags=0}",
        "{[1]={},[3]={},__flags=0}",
    ],
)
def test_native_list_rejects_other_metadata_and_invalid_indices(items: str) -> None:
    native = NativeRecording()
    native.lua.execute("tracks[1].items=" + items)
    with pytest.raises(LuaError, match="RECORDING_UNKNOWN_LAYOUT"):
        native.call("inspect", "inspect", track_name="Audio 1")
    assert native.calls() == []


@pytest.mark.parametrize("method", ["GetTrackSubType", "GetItemListInTrack"])
def test_missing_native_track_api_fails_closed(method: str) -> None:
    native = NativeRecording()
    native.lua.globals().target[method] = None
    with pytest.raises(LuaError, match="RECORDING_API_UNAVAILABLE"):
        native.call("inspect", "inspect", track_name="Audio 1")
    assert native.calls() == []


@pytest.mark.parametrize(
    "subtype,error", [("mono", "AMBIGUOUS_TRACK"), ("adaptive1", "UNKNOWN_LAYOUT")]
)
def test_ambiguous_or_unknown_track_layout_fails_before_mutation(
    subtype: str, error: str
) -> None:
    native = NativeRecording()
    native.lua.execute("tracks[1].name='RAW';tracks[1].subtype='" + subtype + "'")
    if subtype == "mono":
        native.lua.execute("tracks[2]={name='RAW',subtype='mono',items={}}")
    with pytest.raises(LuaError, match="RECORDING_" + error):
        native.call(
            "apply", "ensure_mono", track_name="RAW", expected_audio_track_count=1
        )
    assert native.calls() == []


def test_ensure_mono_appends_names_and_verifies_without_touching_clips() -> None:
    native = NativeRecording()
    name = 'Чистий голос " return resolve:Quit()'
    assert native.call(
        "apply", "ensure_mono", track_name=name, expected_audio_track_count=1
    ) == ("recording_ensure_mono_2_2_0_mono_48000_0")
    assert native.lua.globals().tracks[1]["items"][1].id == "original"
    assert native.lua.globals().tracks[2].name == name
    assert native.calls() == ["select", "add", "rename"]
    assert native.lua.globals().page == "fairlight"
    assert native.call(
        "apply", "ensure_mono", track_name=name, expected_audio_track_count=2
    ).startswith("recording_ensure_mono_2_2_0_mono_")
    assert native.calls() == ["select", "add", "rename"]


@pytest.mark.parametrize(
    "expected,name,error",
    [(0, "RAW", "TRACK_COUNT_CHANGED"), (1, "Audio 1", "TRACK_NOT_MONO")],
)
def test_ensure_rejects_stale_count_and_existing_stereo(
    expected: int, name: str, error: str
) -> None:
    native = NativeRecording()
    with pytest.raises(LuaError, match="RECORDING_" + error):
        native.call(
            "apply", "ensure_mono", track_name=name, expected_audio_track_count=expected
        )
    assert native.calls() == []


@pytest.mark.parametrize(
    "mode,error",
    [
        ("rename-fails", "TRACK_RENAME_FAILED"),
        ("rename-ignored", "TRACK_CREATE_READBACK_FAILED"),
        ("create-fails", "TRACK_CREATE_FAILED"),
        ("wrong-subtype", "TRACK_CREATE_READBACK_FAILED"),
    ],
)
def test_partial_creation_is_reported_and_never_deleted(mode: str, error: str) -> None:
    native = NativeRecording()
    native.lua.globals().mode = mode
    with pytest.raises(LuaError, match="RECORDING_" + error):
        native.call(
            "apply", "ensure_mono", track_name="RAW", expected_audio_track_count=1
        )
    assert len(native.lua.globals().tracks) == 2
    assert native.lua.globals().tracks[1].name == "Audio 1"
    assert "delete" not in native.calls()


def test_seek_preserves_current_page_without_starting_transport() -> None:
    native = NativeRecording()
    assert native.call("apply", "seek", timecode="01:00:00:00") == (
        "recording_seek_0_0_0_missing_48000_01000000"
    )
    assert native.calls() == ["select", "seek"]
    assert native.lua.globals().page == "fairlight"


def test_seek_explicit_page_and_native_readback_failure() -> None:
    native = NativeRecording()
    native.call("apply", "seek", timecode="01:00:00:00", page="edit")
    assert native.calls() == ["select", "page", "seek"]
    native.lua.globals().mode = "seek-readback"
    with pytest.raises(LuaError, match="RECORDING_SEEK_READBACK_FAILED"):
        native.call("apply", "seek", timecode="01:00:00:00")


def test_sample_rate_legacy_read_and_unknown_are_not_guessed() -> None:
    native = NativeRecording()
    native.lua.execute(
        "project.GetSettings=nil;project.GetSetting=function() return '96000' end"
    )
    assert "_96000_0" in native.call("inspect", "inspect", track_name="Audio 1")
    native.lua.execute("project.GetSetting=nil")
    assert "_0_0" in native.call("inspect", "inspect", track_name="Audio 1")
    assert native.calls() == []


def test_native_unknown_fields_and_inactive_page_fail_before_mutation() -> None:
    native = NativeRecording()
    with pytest.raises(LuaError, match="RECORDING_INVALID_ARGUMENTS"):
        native.call("inspect", "inspect", track_name="RAW", arbitrary=True)
    native.lua.globals().page = "fusion"
    with pytest.raises(LuaError, match="RECORDING_SEEK_PAGE_UNSUPPORTED"):
        native.call("apply", "seek", timecode="01:00:00:00")
    assert native.calls() == []


def test_ambiguous_timeline_is_not_selected() -> None:
    native = NativeRecording()
    native.lua.execute("project.GetTimelineCount=function() return 2 end")
    with pytest.raises(LuaError, match="RECORDING_AMBIGUOUS_TIMELINE"):
        native.call(
            "apply", "ensure_mono", track_name="RAW", expected_audio_track_count=1
        )
    assert native.calls() == []


def test_mono_track_capacity_reuses_but_never_appends_a_sixty_fifth() -> None:
    native = NativeRecording()
    native.lua.execute("""
        for i=2,64 do tracks[i]={name='Track '..i,subtype='mono',items={}} end
    """)
    assert (
        native.call(
            "apply", "ensure_mono", track_name="Track 64", expected_audio_track_count=64
        )
        == "recording_ensure_mono_64_64_0_mono_48000_0"
    )
    with pytest.raises(LuaError, match="RECORDING_TRACK_LIMIT"):
        native.call(
            "apply", "ensure_mono", track_name="RAW", expected_audio_track_count=64
        )
    assert native.calls() == []


def test_media_root_is_verified_and_never_exposed_in_filename() -> None:
    native = NativeRecording()
    result = native.call("apply", "configure_media_root", media_root="C:/Allowed/Голос")
    assert result == "recording_configure_media_root_0_0_0_missing_48000_0"
    assert native.lua.globals().location == "C:/Allowed/Голос"
    assert native.calls() == ["settings"]
    native.lua.execute(
        "project.SetSettings=nil;project.SetSetting=function(_,key,value) "
        "note('legacy-settings');location=value;return true end"
    )
    native.call("apply", "configure_media_root", media_root="C:/Allowed/Other")
    assert native.calls() == ["settings", "legacy-settings"]


@pytest.mark.parametrize(
    "path", ["C:/Other", "C:/AllowedElsewhere", "C:/Allowed/../Other"]
)
def test_native_media_root_rejects_prefix_collision_and_parent_escape(
    path: str,
) -> None:
    native = NativeRecording()
    with pytest.raises(
        LuaError, match="RECORDING_(MEDIA_ROOT_NOT_ALLOWED|INVALID_ARGUMENTS)"
    ):
        native.call("apply", "configure_media_root", media_root=path)
    assert native.calls() == []


@pytest.mark.parametrize(
    "mode,error",
    [
        ("settings-fails", "MEDIA_ROOT_SET_FAILED"),
        ("settings-ignored", "MEDIA_ROOT_READBACK_FAILED"),
    ],
)
def test_media_root_failure_does_not_retry_an_uncertain_setter(
    mode: str, error: str
) -> None:
    native = NativeRecording()
    native.lua.globals().mode = mode
    native.lua.execute("project.SetSetting=function() error('must not retry') end")
    with pytest.raises(LuaError, match="RECORDING_" + error):
        native.call("apply", "configure_media_root", media_root="C:/Allowed/Голос")
    assert native.calls() == ["settings"]
