from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.lua_native_audio import validate_native_audio


@pytest.mark.parametrize("mode", ["ok", "gap", "overlap", "missing-tail"])
def test_pilot_partition(mode: str) -> None:
    r = [
        {"start_frame": 100, "end_frame": 150, "normalize": True},
        {"start_frame": 150, "end_frame": 200, "normalize": False},
    ]
    if mode == "gap":
        r[1]["start_frame"] = 151
    if mode == "overlap":
        r[1]["start_frame"] = 149
    if mode == "missing-tail":
        r[1]["end_frame"] = 199
    a = {
        "timeline_name": "Pilot",
        "native_audio": dict(
            operation="phrase_pilot",
            item_index=1,
            start_frame=100,
            end_frame=200,
            regions=r,
        ),
    }
    if mode == "ok":
        assert validate_native_audio(a) == a
    else:
        with pytest.raises(ValueError):
            validate_native_audio(a)


@pytest.mark.parametrize(
    "mode",
    [
        "ok",
        "insert-fail",
        "gain-high",
        "normalize-fail",
        "batch",
        "batch-clamped",
        "batch-partial",
        "batch-next",
        "batch-peak-cap",
    ],
)
def test_pilot_routing_and_native_only(mode: str) -> None:
    lua = LuaRuntime()
    lua.globals().mode = mode
    lua.execute("""
 tracks=1;enabled={true,false};a2={};normalizes=0
 asset={GetClipProperty=function()return '60' end}
 function clip(s,e,source,gain)
    return { GetUniqueId = function ( ) return tostring ( s ) end , GetStart =
    function ( ) return s end , GetEnd = function ( ) return e end ,
    GetSourceStartFrame = function ( ) return source - 1 end , GetSourceStartTime =
    function ( ) return source / 60 end , GetLeftOffset = function ( ) return
    source end , GetClipEnabled = function ( ) return true end ,
    GetProperty = function ( ) return gain or 0 end , SetProperty = function ( _ ,
    key , value ) gain = value ; return true end , GetMediaPoolItem = function ( )
    return asset end ,
    GetFades = function ( ) return { FadeIn = 0 , FadeOut = 0 } end , SetFades =
    function ( f ) return true end }
 end
 source={clip(100,200,500)}
 t={GetTrackCount=function(_,kind)return kind=='audio' and tracks or 0 end,
 GetIsTrackEnabled=function(_,kind,idx)return enabled[idx] end,
 GetItemListInTrack=function(_,kind,idx)return idx==1 and source or a2 end,
 GetNormalizeAudioModes=function()return {'ITU-R BS.1770-4'} end,
    GetStartFrame = function ( ) return 100 end , GetEndFrame = function ( ) return
    # source == 2 and 300 or 200 end ,
 AddTrack=function()tracks=2;return true end,
 SetTrackEnable=function(_,kind,idx,value)enabled[idx]=value;return true end,
 NormalizeAudioLevel=function(_,items)
 normalizes=normalizes+1
 if mode=='gain-high' or mode=='batch-clamped' then
  for _,item in ipairs(items)do item:SetProperty('AudioVolume',8)end
 end
 return mode~='normalize-fail' end}
 pool={AppendToTimeline=function(_,infos)
 if mode=='insert-fail' then return nil end
 local x=infos[1];assert(x.mediaType==2 and x.trackIndex==2)
 local added=clip(x.recordFrame,x.recordFrame+x.endFrame-x.startFrame,x.startFrame,0)
 table.insert(a2,added);return {added} end}
    project = { SetCurrentTimeline = function ( ) return true end , GetMediaPool =
    function ( ) return pool end }
 api={NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT=7,OpenPage=function()return true end}
 helpers={check=function(v,e)assert(v,e)end}
    spec = { item_index = 1 , start_frame = 100 , end_frame = 200 , regions = { {
    start_frame = 100 , end_frame = 150 , normalize = true } , { start_frame = 150
    , end_frame = 200 , normalize = false } } }
    if mode == 'batch-peak-cap' then spec . regions [ 1 ] . max_gain_db = - 1 ;
    spec . regions [ 2 ] . max_gain_db = - 2 end
 if mode:find('batch') then
    local part = spec ; spec = { operation = 'phrase_batch' , source_count = 1 ,
    first_item = 1 , last_item = 1 , expected_a2_count = 0 , partitions = { part }
    , finalize = true }
  if mode=='batch-partial' or mode=='batch-next' then
   source[2]=clip(200,300,600);spec.source_count=2
   if mode=='batch-partial' then spec.finalize=false
   else
    tracks = 2 ; a2 = { clip ( 100 , 200 , 500 ) } ; spec . first_item = 2 ; spec .
    last_item = 2 ; spec . expected_a2_count = 1
    spec . partitions = { { item_index = 2 , start_frame = 200 , end_frame = 300 ,
    regions = { { start_frame = 200 , end_frame = 300 , normalize = true } } } }
   end
  end
 end
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
        Path("bridges/resolve/ResolveLuaAudioPilot.lua").read_text(encoding="utf-8")
    )
    operation = module(lua.globals().api, lua.globals().helpers)(
        lua.globals().project, lua.globals().t, lua.globals().spec
    )
    assert lua.globals().tracks == (2 if mode == "batch-next" else 1)
    if mode in {"ok", "batch", "batch-clamped", "batch-next", "batch-peak-cap"}:
        operation()
        assert lua.globals().enabled[1] is False and lua.globals().enabled[2] is True
        assert len(lua.globals().a2) == 2 and lua.globals().normalizes == 1
        if mode == "batch-peak-cap":
            assert lua.globals().a2[1].GetProperty() == -1
            assert lua.globals().a2[2].GetProperty() == -2
        if mode == "batch-clamped":
            assert lua.globals().a2[1].GetProperty() == 4
    elif mode == "batch-partial":
        operation()
        assert lua.globals().enabled[1] is True and lua.globals().enabled[2] is False
    else:
        with pytest.raises(LuaError):
            operation()
        assert lua.globals().enabled[1] is True and lua.globals().enabled[2] is False


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), True, 4.1, -24.1])
def test_invalid_peak_cap(cap: Any) -> None:
    a = {
        "timeline_name": "Review",
        "native_audio": dict(
            operation="phrase_pilot",
            item_index=1,
            start_frame=100,
            end_frame=200,
            regions=[
                dict(start_frame=100, end_frame=200, normalize=True, max_gain_db=cap)
            ],
        ),
    }
    with pytest.raises(ValueError):
        validate_native_audio(a)

LuaRuntime = import_module("lupa.lua51").LuaRuntime
LuaError = import_module("lupa.lua51").LuaError


def test_batch_requires_measured_caps() -> None:
    part: dict[str, Any] = dict(
        item_index=1,
        start_frame=100,
        end_frame=200,
        regions=[dict(start_frame=100, end_frame=200, normalize=True)],
    )
    a = {
        "timeline_name": "Review",
        "native_audio": dict(
            operation="phrase_batch",
            source_count=1,
            first_item=1,
            last_item=1,
            expected_a2_count=0,
            partitions=[part],
            finalize=True,
        ),
    }
    with pytest.raises(ValueError):
        validate_native_audio(a)
    part["regions"][0]["max_gain_db"] = -2
    assert validate_native_audio(a) == a
