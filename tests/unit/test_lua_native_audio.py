from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.lua_native_audio import validate_native_audio

LuaRuntime = import_module("lupa.lua51").LuaRuntime
LuaError = import_module("lupa.lua51").LuaError


def request() -> dict[str, Any]:
    return {
        "timeline_name": "Review",
        "native_audio": {
            "operation": "normalize",
            "track_index": 1,
            "items": [{"index": 1, "start_frame": 100, "end_frame": 200}],
            "normalization_mode": "ITU-R BS.1770-4",
            "target_loudness": -19,
            "target_peak": -1,
        },
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_peak", 0),
        ("target_loudness", float("nan")),
        ("target_peak", True),
        ("track_index", 0),
        ("items", []),
        ("operation", "exec"),
    ],
)
def test_validation_rejects(field: str, value: Any) -> None:
    a = request()
    a["native_audio"][field] = value
    with pytest.raises(ValueError):
        validate_native_audio(a)


def test_duplicate_selection_rejected() -> None:
    a = request()
    a["native_audio"]["items"] *= 2
    with pytest.raises(ValueError):
        validate_native_audio(a)


def test_valid_native_inputs() -> None:
    assert validate_native_audio(request()) == request()
    assert (
        validate_native_audio(
            {
                "timeline_name": "Review",
                "native_audio": {"operation": "preset", "preset_name": "Dialogue"},
            }
        )["native_audio"]["preset_name"]
        == "Dialogue"
    )


@pytest.mark.parametrize(
    "mode",
    [
        "ok",
        "bad-mode",
        "bad-bounds",
        "disabled",
        "native-failure",
        "readback-failure",
        "layout-change",
        "preset",
        "missing-preset",
    ],
)
def test_native_calls_and_readback(mode: str) -> None:
    lua = LuaRuntime()
    lua.globals().mode = mode
    lua.execute("""
 calls=0
 item={GetUniqueId=function() return 'clip' end,GetStart=function() return 100 end,
 GetEnd=function() return mode=='layout-change' and calls>0 and 201 or 200 end,
 GetClipEnabled=function() return mode~='disabled' end,
 GetProperty=function() return mode=='readback-failure' and 'invalid' or -2 end}
    t = { GetUniqueId = function ( ) return 'timeline' end , GetStartFrame =
    function ( ) return 100 end , GetEndFrame = function ( ) return 200 end ,
 GetTrackCount=function(_,kind) return kind=='audio' and 1 or 0 end,
    GetIsTrackEnabled = function ( ) return true end , GetItemListInTrack =
    function ( ) return { item } end ,
    GetNormalizeAudioModes = function ( ) return mode == 'bad-mode' and { } or {
    'ITU-R BS.1770-4' } end ,
 NormalizeAudioLevel=function(_,items,options)
    calls = calls + 1 ; assert ( # items == 1 and options . setLevelMode == 7 and
    options . targetLoudness == - 19 and options . targetLevel == - 1 )
 return mode~='native-failure' end}
    api = { NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT = 7 , GetFairlightPresets =
    function ( ) return mode == 'missing-preset' and { } or { 'Dialogue' } end }
    project = { SetCurrentTimeline = function ( ) return true end ,
    GetCurrentTimeline = function ( ) return t end ,
    ApplyFairlightPresetToCurrentTimeline = function ( _ , name ) assert ( name ==
    'Dialogue' ) ; calls = calls + 1 ; return true end }
 helpers={check=function(v,e) assert(v,e) end}
    spec = { operation = 'normalize' , track_index = 1 , items = { { index = 1 ,
    start_frame = 100 , end_frame = 200 } } , normalization_mode =
    'ITU-R BS.1770-4' , target_loudness = - 19 , target_peak = - 1 }
 if mode=='bad-bounds' then spec.items[1].end_frame=201 end
    if mode == 'preset' or mode == 'missing-preset' then spec = { operation =
    'preset' , preset_name = 'Dialogue' } end
 """)
    # Simulate Resolve returning false for routing on an inactive timeline.
    lua.execute("""
      current=nil
      project=project or {}
      t.GetUniqueId=t.GetUniqueId or function() return 'timeline' end
      local get_enabled=t.GetIsTrackEnabled
      t.GetIsTrackEnabled=function(self,...)
        return current==self and get_enabled(self,...) or false
      end
      project.SetCurrentTimeline=function(_,target) current=target;return true end
      project.GetCurrentTimeline=function() return current end
    """)
    module = lua.execute(
        Path("bridges/resolve/ResolveLuaNativeAudio.lua").read_text(encoding="utf-8")
    )
    factory = module(lua.globals().api, lua.globals().helpers)
    if mode in {"bad-mode", "bad-bounds", "disabled", "missing-preset"}:
        with pytest.raises(LuaError):
            factory(lua.globals().project, lua.globals().t, lua.globals().spec)
        assert lua.globals().calls == 0
    else:
        operation = factory(lua.globals().project, lua.globals().t, lua.globals().spec)
        assert lua.globals().calls == 0
        if mode in {"ok", "preset"}:
            operation()
            assert lua.globals().calls == 1
        else:
            with pytest.raises(LuaError):
                operation()
            assert lua.globals().calls == 1


@pytest.mark.parametrize("changed", [False, True])
def test_discard_only_exact_muted_unprocessed_batch(changed: bool) -> None:
    lua = LuaRuntime()
    lua.globals().changed = changed
    lua.execute("""
 deleted=false;tracks=2
    source = { GetUniqueId = function ( ) return 'original' end , GetStart =
    function ( ) return 100 end , GetEnd = function ( ) return 200 end ,
    GetClipEnabled = function ( ) return true end }
    partial = { GetUniqueId = function ( ) return 'partial' end , GetStart =
    function ( ) return 100 end , GetEnd = function ( ) return 150 end ,
    GetClipEnabled = function ( ) return true end , GetSourceStartFrame = function
    ( ) return 500 end , GetLinkedItems = function ( ) return { } end , GetProperty
    = function ( ) return changed and 1 or 0 end }
    t = { GetUniqueId = function ( ) return 't' end , GetStartFrame = function ( )
    return 100 end , GetEndFrame = function ( ) return 200 end , GetTrackCount =
    function ( _ , kind ) return kind == 'audio' and tracks or 0 end ,
    GetIsTrackEnabled = function ( _ , kind , idx ) return idx == 1 end ,
    GetItemListInTrack = function ( _ , kind , idx ) return idx == 1 and { source }
    or { partial } end , DeleteTrack = function ( _ , kind , idx ) assert ( kind ==
    'audio' and idx == 2 ) ; tracks = 1 ; deleted = true ; return true end }
    helpers = { check = function ( v , e ) assert ( v , e ) end } ; spec = {
    operation = 'discard_failed_batch' , source_count = 1 , items = { { start_frame
    = 100 , end_frame = 150 , source_start_frame = 500 } } }
 """)
    # Simulate Resolve returning false for routing on an inactive timeline.
    lua.execute("""
      current=nil
      project=project or {}
      t.GetUniqueId=t.GetUniqueId or function() return 'timeline' end
      local get_enabled=t.GetIsTrackEnabled
      t.GetIsTrackEnabled=function(self,...)
        return current==self and get_enabled(self,...) or false
      end
      project.SetCurrentTimeline=function(_,target) current=target;return true end
      project.GetCurrentTimeline=function() return current end
    """)
    factory = lua.execute(
        Path("bridges/resolve/ResolveLuaNativeAudio.lua").read_text()
    )(lua.table(), lua.globals().helpers)
    if changed:
        with pytest.raises(LuaError):
            factory(lua.globals().project, lua.globals().t, lua.globals().spec)
        assert not lua.globals().deleted
    else:
        factory(lua.globals().project, lua.globals().t, lua.globals().spec)()
        assert lua.globals().deleted


@pytest.mark.parametrize(
    "mode", ["ok", "rejected", "state-changed", "readback-failed", "gain-changed"]
)
def test_voice_isolation_guard_and_native_readback(mode: str) -> None:
    lua = LuaRuntime()
    lua.globals().mode = mode
    lua.execute("""
 calls=0;voice={isEnabled=false,amount=0}
    item = { GetUniqueId = function ( ) return 'clip' end , GetStart = function ( )
    return 100 end , GetEnd = function ( ) return 200 end , GetClipEnabled =
    function ( ) return true end , GetProperty = function ( ) return mode ==
    'gain-changed' and calls > 0 and 2 or 0 end }
    t = { GetUniqueId = function ( ) return 't' end , GetStartFrame = function ( )
    return 100 end , GetEndFrame = function ( ) return 200 end , GetTrackCount =
    function ( _ , kind ) return kind == 'audio' and 1 or 0 end , GetIsTrackEnabled
    = function ( ) return true end , GetItemListInTrack = function ( ) return {
    item } end , GetVoiceIsolationState = function ( ) return mode ==
    'state-changed' and { } or voice end , SetVoiceIsolationState = function ( _ ,
    track , state ) assert ( track == 1 ) ; calls = calls + 1 ; if mode ~=
    'readback-failed' and mode ~= 'rejected' then voice = state end ; return mode
    ~= 'rejected' end }
    project = { SetCurrentTimeline = function ( ) return true end } ; api = {
    OpenPage = function ( _ , page ) assert ( page == 'fairlight' ) ; return true
    end } ; helpers = { check = function ( v , e ) assert ( v , e ) end } ; spec =
    { operation = 'voice_isolation' , track_index = 1 , enabled = true , amount =
    20 , expected_state = { isEnabled = false , amount = 0 } }
 """)
    # Simulate Resolve returning false for routing on an inactive timeline.
    lua.execute("""
      current=nil
      project=project or {}
      t.GetUniqueId=t.GetUniqueId or function() return 'timeline' end
      local get_enabled=t.GetIsTrackEnabled
      t.GetIsTrackEnabled=function(self,...)
        return current==self and get_enabled(self,...) or false
      end
      project.SetCurrentTimeline=function(_,target) current=target;return true end
      project.GetCurrentTimeline=function() return current end
    """)
    factory = lua.execute(
        Path("bridges/resolve/ResolveLuaNativeAudio.lua").read_text()
    )(lua.globals().api, lua.globals().helpers)
    if mode == "state-changed":
        with pytest.raises(LuaError):
            factory(lua.globals().project, lua.globals().t, lua.globals().spec)
        assert lua.globals().calls == 0
    else:
        run = factory(lua.globals().project, lua.globals().t, lua.globals().spec)
        if mode == "ok":
            run()
            assert lua.globals().voice.amount == 20
        else:
            with pytest.raises(LuaError):
                run()
        assert lua.globals().calls == 1


@pytest.mark.parametrize("amount", [-1, 41, True, float("nan")])
def test_voice_pilot_validation(amount: Any) -> None:
    with pytest.raises(ValueError):
        validate_native_audio(
            {
                "timeline_name": "Copy",
                "native_audio": dict(
                    operation="voice_isolation",
                    track_index=2,
                    enabled=True,
                    amount=amount,
                    expected_state={"isEnabled": False, "amount": 0},
                ),
            }
        )


@pytest.mark.parametrize("module_name", ["NativeAudio", "AudioPilot"])
@pytest.mark.parametrize("mode", ["select-failed", "wrong-current"])
def test_target_selection_fails_before_routing_reads(
    module_name: str, mode: str
) -> None:
    lua = LuaRuntime()
    lua.globals().mode = mode
    lua.execute("""
      reads=0
      target={GetUniqueId=function() return 'expected' end,
        GetIsTrackEnabled=function() reads=reads+1;return true end}
      project={
        SetCurrentTimeline=function() return mode~='select-failed' end,
        GetCurrentTimeline=function()
          return {GetUniqueId=function() return 'other' end}
        end}
      helpers={check=function(value,message) assert(value,message) end}
    """)
    factory = lua.execute(
        Path(f"bridges/resolve/ResolveLua{module_name}.lua").read_text()
    )(lua.table(), lua.globals().helpers)
    with pytest.raises(LuaError, match="TIMELINE_SELECT_FAILED|TIMELINE_CHANGED"):
        factory(lua.globals().project, lua.globals().target, lua.table())
    assert lua.globals().reads == 0
