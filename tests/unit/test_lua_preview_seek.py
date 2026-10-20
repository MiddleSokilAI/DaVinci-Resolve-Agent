"""Preview seeking is bounded and never invokes playback or edits clips."""

from importlib import import_module
from pathlib import Path

import pytest

from providers.resolve.lua_finishing import validate_finishing


@pytest.mark.parametrize("frame", [True, -1, 1.5, "240000", 10_000_001])
def test_invalid_preview_frame(frame: object) -> None:
    with pytest.raises(ValueError):
        validate_finishing(
            "set_clip_properties",
            {
                "timeline_name": "Review",
                "preview_start": True,
                "preview_frame": frame,
            },
            [],
        )


@pytest.mark.parametrize(
    "frame,expected", [(None, "01:00:00:00"), (235543, "01:05:25:43")]
)
def test_native_preview_seek_and_start(frame: int | None, expected: str) -> None:
    lua = import_module("lupa.lua51").LuaRuntime()
    lua.execute("""
        mutations=0
        timeline={GetStartTimecode=function() return '01:00:00:00' end,
          GetStartFrame=function() return 216000 end,
          GetEndFrame=function() return 300000 end,
          GetSetting=function() return '60' end,
          GetUniqueId=function() return 'timeline-id' end,
          SetCurrentTimecode=function(_,tc)
            current=tc; mutations=mutations+1; return true end,
          GetCurrentTimecode=function() return current end}
        project={SetCurrentTimeline=function(_,t) active=t; return true end,
          GetCurrentTimeline=function() return active end}
        api={OpenPage=function(_,p) page=p; return true end,
          GetCurrentPage=function() return page end}
        helpers={check=function(v,e) if not v then error(e) end end,
          timelines=function() return timeline end}
    """)
    factory = lua.execute(Path("bridges/resolve/ResolveLuaFinishing.lua").read_text())
    module = factory(
        lua.globals().api, "", lua.globals().helpers, lua.table(jobs=lua.table())
    )
    args = {"timeline_name": "Review", "preview_start": True}
    if frame is not None:
        args["preview_frame"] = frame
    validated = validate_finishing("set_clip_properties", args, [])
    request = lua.table(
        action="set_clip_properties", arguments=lua.table_from(validated)
    )
    mutation = module.preflight(lua.globals().project, request)
    assert lua.globals().mutations == 0
    mutation()
    assert lua.globals().current == expected and lua.globals().page == "edit"
    assert lua.globals().mutations == 1
    for invalid in (215999, 300000):
        request.arguments.preview_frame = invalid
        with pytest.raises(Exception, match="INVALID_PREVIEW_FRAME"):
            module.preflight(lua.globals().project, request)
        assert lua.globals().mutations == 1
