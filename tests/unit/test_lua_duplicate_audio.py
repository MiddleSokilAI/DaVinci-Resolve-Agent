from importlib import import_module
from pathlib import Path

import pytest

LuaRuntime = import_module("lupa.lua51").LuaRuntime
LuaError = import_module("lupa.lua51").LuaError

@pytest.mark.parametrize("count", [2884, 10001])
def test_duplicate_layout_limit_and_balanced_audio(count: int) -> None:
    lua = LuaRuntime()
    lua.globals().count = count
    lua.execute("""
 gain=-2;enabled=false;clips={}
    for i = 1 , count do local n = i ; clips [ i ] = { GetUniqueId = function ( )
    return tostring ( n ) end , GetStart = function ( ) return n end , GetEnd =
    function ( ) return n + 1 end , GetSourceStartFrame = function ( ) return n end
    , GetSourceEndFrame = function ( ) return n + 1 end , GetMediaPoolItem =
    function ( ) return { GetUniqueId = function ( ) return 'media' end } end ,
    GetLinkedItems = function ( ) return { } end , GetFusionCompCount = function (
    ) return 0 end , GetClipEnabled = function ( ) return true end , GetProperty =
    function ( ) return gain end } end
    t = { GetSetting = function ( ) return 60 end , GetStartFrame = function ( )
    return 0 end , GetEndFrame = function ( ) return count + 1 end , GetTrackCount
    = function ( _ , kind ) return kind == 'audio' and 1 or 0 end ,
    GetIsTrackEnabled = function ( self ) return current == self and enabled or
    false end , GetItemListInTrack = function ( ) return clips end }
    project = { SetCurrentTimeline = function ( _ , value ) current = value ;
    return true end } ; check = function ( v , e ) assert ( v , e ) end
 """)
    s = Path("bridges/resolve/ResolveLuaEditing.lua").read_text()
    chunk = s[s.index("    local function layout(") : s.index("    local finish =")]
    layout = lua.execute(chunk + "\nreturn layout")
    if count > 10000:
        with pytest.raises(LuaError, match="TIMELINE_TOO_LARGE"):
            layout(lua.globals().t, lua.globals().project)
    else:
        first, _ = layout(lua.globals().t, lua.globals().project)
        lua.globals().gain = -1
        second, _ = layout(lua.globals().t, lua.globals().project)
        assert first != second
        lua.globals().gain = -2
        lua.globals().enabled = True
        third, _ = layout(lua.globals().t, lua.globals().project)
        assert first != third
