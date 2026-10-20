-- A native-only pilot: copy source audio to A2, partition one clip, normalize
-- speech regions with Resolve. Retain A1 unchanged and disable only its track.
return function(api,helpers)
    local check=helpers.check
    return function(project,t,spec)
        local function select_target()
            check(project:SetCurrentTimeline(t)==true,"TIMELINE_SELECT_FAILED")
            local current=project:GetCurrentTimeline()
            check(current and current:GetUniqueId()==t:GetUniqueId(),"TIMELINE_CHANGED")
        end
        select_target()
        local batch=spec.operation=="phrase_batch"
        local prefix=batch and spec.expected_a2_count or 0
        local reuse_empty=batch and prefix==0 and t:GetTrackCount("audio")==2
            and #(t:GetItemListInTrack("audio",2) or {})==0
        check(reuse_empty or t:GetTrackCount("audio")== (prefix==0 and 1 or 2),"PILOT_REQUIRES_ONE_AUDIO_TRACK")
        check(t:GetIsTrackEnabled("audio",1)==true,"SOURCE_AUDIO_DISABLED")
        local source=t:GetItemListInTrack("audio",1) or {}
        local selected={}
        if batch then
            check(#source==spec.source_count,"PILOT_ITEM_CHANGED")
            for _,part in ipairs(spec.partitions) do
                local item=source[part.item_index]
                check(item and item:GetStart()==part.start_frame and item:GetEnd()==part.end_frame,"PILOT_ITEM_CHANGED")
                selected[part.item_index]=part
            end
            if prefix>0 then
                local existing=t:GetItemListInTrack("audio",2) or {}
                check(#existing==prefix and t:GetIsTrackEnabled("audio",2)==false,"PILOT_PREFIX_CHANGED")
                check(existing[prefix]:GetEnd()==source[spec.first_item]:GetStart(),"PILOT_PREFIX_CHANGED")
            end
        else
            local pilot=source[spec.item_index]
            check(pilot and pilot:GetStart()==spec.start_frame and pilot:GetEnd()==spec.end_frame,"PILOT_ITEM_CHANGED")
        end
        local mode="ITU-R BS.1770-4"
        local supported=false
        for _,v in pairs(t:GetNormalizeAudioModes() or {}) do if v==mode then supported=true end end
        check(supported and api.NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT~=nil,"NORMALIZATION_UNAVAILABLE")
        local before_video={}
        for track=1,t:GetTrackCount("video") do
            for _,item in ipairs(t:GetItemListInTrack("video",track) or {}) do
                before_video[#before_video+1]={item=item,id=item:GetUniqueId(),start=item:GetStart(),finish=item:GetEnd(),enabled=item:GetClipEnabled(),comps=item:GetFusionCompCount()}
            end
        end
        local plan={}
        for index,item in ipairs(source) do
          if not batch or selected[index] then
            check(item:GetClipEnabled()==true and item:GetProperty("AudioVolume")==0,"SOURCE_AUDIO_NOT_UNITY")
            local asset=item:GetMediaPoolItem()
            check(asset~=nil and tonumber(asset:GetClipProperty("FPS"))==60,"SOURCE_FPS_UNSUPPORTED")
            local fades=item:GetFades()
            check(type(fades)=="table" and type(fades.FadeIn)=="number"
                and type(fades.FadeOut)=="number","SOURCE_FADES_UNAVAILABLE")
            local regions=batch and selected[index].regions or index==spec.item_index and spec.regions or {
                {start_frame=item:GetStart(),end_frame=item:GetEnd(),normalize=false}
            }
            for _,region in ipairs(regions) do
                local source_frame=item:GetSourceStartTime()*60+region.start_frame-item:GetStart()
                check(math.abs(source_frame-math.floor(source_frame+0.5))<0.0001,"SOURCE_SUBFRAME_UNSUPPORTED")
                plan[#plan+1]={asset=asset,source=math.floor(source_frame+0.5),
                    start=region.start_frame,finish=region.end_frame,normalize=region.normalize,
                    max_gain=region.max_gain_db or 4,
                    split=index==spec.item_index,
                    fades={FadeIn=region.start_frame==item:GetStart() and fades.FadeIn or 0,
                           FadeOut=region.end_frame==item:GetEnd() and fades.FadeOut or 0}}
            end
          end
        end
        local first,last=t:GetStartFrame(),t:GetEndFrame()
        return function()
            select_target()
            if batch then check(api:OpenPage("edit")==true,"PAGE_OPEN_FAILED") end
            check(t:GetTrackCount("audio")==((prefix==0 and not reuse_empty) and 1 or 2),"AUDIO_TRACKS_CHANGED")
            if prefix==0 and not reuse_empty then check(t:AddTrack("audio","stereo")==true,"AUDIO_TRACK_CREATE_FAILED") end
            check(t:GetTrackCount("audio")==2,"AUDIO_TRACK_CREATE_FAILED")
            if prefix==0 then
                check(t:SetTrackEnable("audio",2,false)==true,"PILOT_TRACK_MUTE_FAILED")
            end
            check(t:GetIsTrackEnabled("audio",2)==false,"PILOT_TRACK_MUTE_FAILED")
            local pool=project:GetMediaPool()
            local normalized={}
            local inserted={}
            for _,part in ipairs(plan) do
                local added=pool:AppendToTimeline({{mediaPoolItem=part.asset,startFrame=part.source,
                    -- Native audio-only append on this build uses the exclusive
                    -- source boundary (verified against item frame/time readback).
                    endFrame=part.source+part.finish-part.start,mediaType=2,trackIndex=2,recordFrame=part.start}})
                check(type(added)=="table" and #added==1,"AUDIO_INSERT_FAILED")
                local item=added[1]
                inserted[#inserted+1]={item=item,cap=part.max_gain}
                check(item:GetStart()==part.start and item:GetEnd()==part.finish
                    and math.abs(item:GetSourceStartTime()*60-part.source)<0.0001
                    and math.abs(item:GetLeftOffset(true)-part.source)<0.0001,"AUDIO_COVERAGE_CHANGED")
                check(item:GetProperty("AudioVolume")==0,"NEW_AUDIO_NOT_UNITY")
                check(item:SetFades(part.fades)==true,"AUDIO_FADE_COPY_FAILED")
                local fades=item:GetFades()
                check(fades.FadeIn==part.fades.FadeIn and fades.FadeOut==part.fades.FadeOut,
                      "AUDIO_FADE_READBACK_FAILED")
                if part.normalize then normalized[#normalized+1]=item end
                -- No fades: split boundaries must be inside verified silence;
                -- do not truncate consonants or alter original full-clip edges.
            end
            if #normalized>0 then
                check(t:NormalizeAudioLevel(normalized,{normalizationMode=mode,targetLoudness=-19,
                    targetLevel=-1,setLevelMode=api.NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT})==true,"NORMALIZATION_FAILED")
            end
            for _,entry in ipairs(inserted) do
                local item=entry.item
                local gain=item:GetProperty("AudioVolume")
                check(type(gain)=="number" and gain==gain,"AUDIO_GAIN_READBACK_FAILED")
                local upper=batch and entry.cap or 4
                local lower=math.min(-4,upper)
                if batch and (gain < lower or gain > upper) then
                    local bounded=math.max(lower,math.min(upper,gain))
                    check(item:SetProperty("AudioVolume",bounded)==true,"AUDIO_GAIN_SET_FAILED")
                    gain=item:GetProperty("AudioVolume")
                    check(type(gain)=="number" and math.abs(gain-bounded)<0.001,"AUDIO_GAIN_READBACK_FAILED")
                end
                check(gain>=lower-0.001 and gain<=upper+0.001,"PILOT_GAIN_EXCEEDS_BOUND")
            end
            local actual=t:GetItemListInTrack("audio",2) or {}
            check(#actual==prefix+#plan and #source==#(t:GetItemListInTrack("audio",1) or {}),"AUDIO_COUNT_CHANGED")
            for i,part in ipairs(plan) do
                local item=actual[prefix+i]
                check(item:GetStart()==part.start and item:GetEnd()==part.finish,"AUDIO_COVERAGE_CHANGED")
            end
            if not batch or spec.finalize then
                if batch then
                    local cursor=first
                    for _,item in ipairs(actual) do
                        check(item:GetStart()==cursor,"AUDIO_COVERAGE_CHANGED");cursor=item:GetEnd()
                    end
                    check(cursor==last,"AUDIO_COVERAGE_CHANGED")
                end
                check(t:SetTrackEnable("audio",1,false)==true,"SOURCE_TRACK_DISABLE_FAILED")
                if t:SetTrackEnable("audio",2,true)~=true then
                    t:SetTrackEnable("audio",1,true)
                    check(false,"PILOT_TRACK_ENABLE_FAILED")
                end
                check(t:GetIsTrackEnabled("audio",1)==false and t:GetIsTrackEnabled("audio",2)==true,"AUDIO_ROUTING_READBACK_FAILED")
            else
                check(t:GetIsTrackEnabled("audio",1)==true and t:GetIsTrackEnabled("audio",2)==false,"AUDIO_ROUTING_READBACK_FAILED")
            end
            for _,v in ipairs(before_video) do
                local item=v.item
                check(item:GetUniqueId()==v.id and item:GetStart()==v.start and item:GetEnd()==v.finish
                    and item:GetClipEnabled()==v.enabled and item:GetFusionCompCount()==v.comps,"VIDEO_CHANGED")
            end
            check(t:GetStartFrame()==first and t:GetEndFrame()==last,"TIMELINE_BOUNDS_CHANGED")
        end
    end
end
