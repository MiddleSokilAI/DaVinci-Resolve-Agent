"""Native empty-format changes are bounded, read back, and refuse existing edits."""

from importlib import import_module
from pathlib import Path
from typing import Any

import pytest


def fixture() -> tuple[Any, Any]:
    lua = import_module("lupa.lua51").LuaRuntime()
    lua.execute("""
        calls=0; kind=nil; late=false; wrong=false; refuse=false
        settings={timelineFrameRate='24',timelineResolutionWidth='1920',
            timelineResolutionHeight='1080'}
        timeline={GetTrackCount=function() return 1 end,
            GetItemListInTrack=function(_,k)
                if k==kind then return {{GetStart=function() return 0 end,
                    GetEnd=function() return 1 end}} end
                return {} end,
            SetSettings=function(_,s)
                calls=calls+1
                if refuse then return false end
                for k,v in pairs(s) do
                    if not(wrong and k=='timelineResolutionWidth') then
                        settings[k]=v
                    end
                end
                return true end,
            GetSetting=function(_,k) return settings[k] end}
        project={SetCurrentTimeline=function()
            if late then kind='video' end
            return true end}
        api={OpenPage=function() return true end}
        helpers={check=function(v,e) if not v then error(e) end end,
            timelines=function() return timeline end}
    """)
    factory = lua.execute(Path("bridges/resolve/ResolveLuaFinishing.lua").read_text())
    module = factory(lua.globals().api, "", lua.globals().helpers,
                     lua.table(jobs=lua.table()))
    return lua, module


@pytest.mark.parametrize("resolution", [None, "1920x1080", "3840x2160"])
def test_native_sets_and_reads_all_requested_format_fields(
    resolution: str | None,
) -> None:
    lua, module = fixture()
    args = {"timeline_name": "Review", "empty_timeline_fps": 60}
    if resolution is not None:
        args["empty_timeline_resolution"] = resolution
    operation = module.preflight(lua.globals().project, lua.table(
        action="set_clip_properties", arguments=lua.table_from(args)))
    assert lua.globals().calls == 0
    operation()
    assert lua.globals().calls == 1
    assert lua.globals().settings.timelineFrameRate == "60"
    width, height = (resolution or "1920x1080").split("x")
    assert lua.globals().settings.timelineResolutionWidth == width
    assert lua.globals().settings.timelineResolutionHeight == height


@pytest.mark.parametrize("kind", ["video", "audio", "subtitle"])
def test_native_nonempty_timeline_refuses_before_setting_format(kind: str) -> None:
    lua, module = fixture()
    lua.globals().kind = kind
    with pytest.raises(Exception, match="TIMELINE_NOT_EMPTY"):
        module.preflight(lua.globals().project, lua.table(
            action="set_clip_properties", arguments=lua.table(
                timeline_name="Review", empty_timeline_fps=60,
                empty_timeline_resolution="3840x2160")))
    assert lua.globals().calls == 0


@pytest.mark.parametrize("mode,reason", [
    ("late", "TIMELINE_NOT_EMPTY"), ("wrong", "VERIFY_FAILED"),
    ("refuse", "TIMELINE_SETTINGS_FAILED"),
])
def test_native_rechecks_empty_state_and_requires_exact_readback(
    mode: str, reason: str,
) -> None:
    lua, module = fixture()
    lua.globals()[mode] = True
    operation = module.preflight(lua.globals().project, lua.table(
        action="set_clip_properties", arguments=lua.table(
            timeline_name="Review", empty_timeline_fps=60,
            empty_timeline_resolution="3840x2160")))
    with pytest.raises(Exception, match=reason):
        operation()
    assert lua.globals().calls == (0 if mode == "late" else 1)


@pytest.mark.parametrize("resolution", ["4096x2160", "3840x1080", True])
def test_native_invalid_resolution_never_reaches_mutation(resolution: Any) -> None:
    lua, module = fixture()
    with pytest.raises(Exception, match="INVALID_ARGUMENTS"):
        module.preflight(lua.globals().project, lua.table(
            action="set_clip_properties", arguments=lua.table(
                timeline_name="Review", empty_timeline_fps=60,
                empty_timeline_resolution=resolution)))
    assert lua.globals().calls == 0
