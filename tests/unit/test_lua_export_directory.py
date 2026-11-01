"""Native Lua render settings retain a caller-selected, allowlisted parent."""

from importlib import import_module
from pathlib import Path

import pytest


@pytest.mark.parametrize("parent", [None, "G:/Videos"])
def test_native_lua_queues_and_owns_the_selected_render_target(
    parent: str | None,
) -> None:
    lua = import_module("lupa.lua51").LuaRuntime()
    lua.execute("""
        calls=0; page='edit'
        bmd={wait=function() end}
        timeline={GetStartFrame=function() return 0 end,
          GetEndFrame=function() return 120 end,
          GetUniqueId=function() return 'timeline-id' end,
          GetName=function() return 'Review' end}
        project={GetUniqueId=function() return 'project-id' end,
          SetCurrentTimeline=function() calls=calls+1; return true end,
          GetCurrentTimeline=function() return timeline end,
          LoadRenderPreset=function() return true end,
          SetCurrentRenderFormatAndCodec=function() return true end,
          SetCurrentRenderMode=function() return true end,
          SetRenderSettings=function(_,s)
            if s.TargetDir then target=s.TargetDir end; return true end,
          AddRenderJob=function() return 'job-1' end,
          GetRenderJobList=function() return {{JobId='job-1',
            TargetDir=target, TimelineName='Review'}} end}
        api={OpenPage=function(_,p) page=p; return true end,
          GetCurrentPage=function() return page end}
        helpers={check=function(v,e) if not v then error(e) end end,
          timelines=function() return timeline end,
          normalized=function(s) return s end,
          allowed=function(p) return p=='G:/Videos/video.mp4' end}
    """)
    jobs = lua.table()
    factory = lua.execute(Path("bridges/resolve/ResolveLuaFinishing.lua").read_text())
    module = factory(
        lua.globals().api, "session", lua.globals().helpers, lua.table(jobs=jobs)
    )
    args = lua.table(timeline_name="Review")
    if parent is not None:
        args.output_directory = parent
    request = lua.table(action="prepare_render", id="abc", arguments=args)
    mutation = module.preflight(lua.globals().project, request)
    assert lua.globals().calls == 0
    assert mutation() == "job_job-1"
    expected = "G:/Videos/render-abc" if parent else "session/renders/abc"
    assert lua.globals().target == jobs["job-1"].directory == expected
    assert lua.globals().page == "edit"
    args.output_directory = "C:/Windows"
    with pytest.raises(Exception, match="INVALID_ARGUMENTS"):
        module.preflight(lua.globals().project, request)
    assert lua.globals().calls == 1
