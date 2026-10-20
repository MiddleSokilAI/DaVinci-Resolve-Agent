from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.lua_finishing import validate_finishing

LuaRuntime = import_module("lupa.lua51").LuaRuntime
LuaError = import_module("lupa.lua51").LuaError

@pytest.mark.parametrize("page", ["edit", "fairlight"])
def test_page_validation(page: str) -> None:
    assert (
        validate_finishing(
            "set_clip_properties", {"timeline_name": "Review", "open_page": page}, []
        )["open_page"]
        == page
    )


@pytest.mark.parametrize(
    "args",
    [
        {"timeline_name": "Review", "open_page": "deliver"},
        {"timeline_name": "Review", "open_page": "fairlight", "extra": 1},
    ],
)
def test_page_rejects_unknown(args: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        validate_finishing("set_clip_properties", args, [])


@pytest.mark.parametrize(
    "mode", ["ok", "select-fails", "page-fails", "wrong-page", "wrong-timeline"]
)
def test_native_page_readback(mode: str) -> None:
    lua = LuaRuntime()
    lua.globals().mode = mode
    lua.execute("""
      calls=0
      t={GetUniqueId=function() return 'expected' end}
    project = { SetCurrentTimeline = function ( ) calls = calls + 1 ; return mode
    ~= 'select-fails' end ,
    GetCurrentTimeline = function ( ) return { GetUniqueId = function ( ) return
    mode == 'wrong-timeline' and 'other' or 'expected' end } end }
      api={OpenPage=function(_,p) calls=calls+1;return mode~='page-fails' end,
    GetCurrentPage = function ( ) return mode == 'wrong-page' and 'edit' or
    'fairlight' end }
      helpers={check=function(v,e) assert(v,e) end,timelines=function() return t end}
    request = { action = 'set_clip_properties' , arguments = { timeline_name =
    'Review' , open_page = 'fairlight' } }
    """)
    module = lua.execute(
        Path("bridges/resolve/ResolveLuaFinishing.lua").read_text(encoding="utf-8")
    )
    finish = module(
        lua.globals().api,
        "unused",
        lua.globals().helpers,
        lua.table_from({"jobs": lua.table()}),
    )
    operation = finish.preflight(lua.globals().project, lua.globals().request)
    assert lua.globals().calls == 0
    if mode == "ok":
        operation()
        assert lua.globals().calls == 2
    else:
        with pytest.raises(LuaError):
            operation()
