-- Documented native APIs only. No PCM generation or signal processing.
return function(api, helpers)
    local check = helpers.check
    local function layout(t)
        local rows = {tostring(t:GetStartFrame()), tostring(t:GetEndFrame())}
        for _,kind in ipairs({"video","audio","subtitle"}) do
            rows[#rows+1] = kind .. ":" .. t:GetTrackCount(kind)
            for track=1,t:GetTrackCount(kind) do
                rows[#rows+1] = tostring(t:GetIsTrackEnabled(kind,track))
                for _,item in ipairs(t:GetItemListInTrack(kind,track) or {}) do
                    rows[#rows+1] = table.concat({item:GetUniqueId(),item:GetStart(),item:GetEnd(),
                        tostring(item:GetClipEnabled())}, ":")
                end
            end
        end
        return table.concat(rows,"|")
    end
    return function(project,timeline,spec)
        local function select_target()
            check(project:SetCurrentTimeline(timeline)==true,"TIMELINE_SELECT_FAILED")
            local current=project:GetCurrentTimeline()
            check(current and current:GetUniqueId()==timeline:GetUniqueId(),"TIMELINE_CHANGED")
        end
        -- Inactive timelines can report every track as disabled.
        select_target()
        local id, before = timeline:GetUniqueId(), layout(timeline)
        if spec.operation=="voice_isolation" then
            check(spec.track_index<=timeline:GetTrackCount("audio")
                and timeline:GetIsTrackEnabled("audio",spec.track_index)==true,"VOICE_TRACK_UNAVAILABLE")
            local state=timeline:GetVoiceIsolationState(spec.track_index)
            check(type(state)=="table" and state.isEnabled==spec.expected_state.isEnabled
                and state.amount==spec.expected_state.amount,"VOICE_STATE_CHANGED")
            local gains={}
            for _,item in ipairs(timeline:GetItemListInTrack("audio",spec.track_index) or {}) do
                gains[#gains+1]={item=item,gain=item:GetProperty("AudioVolume")}
            end
            return function()
                select_target()
                check(layout(timeline)==before,"TIMELINE_CHANGED")
                check(project:SetCurrentTimeline(timeline)==true,"TIMELINE_SELECT_FAILED")
                check(api:OpenPage("fairlight")==true,"PAGE_OPEN_FAILED")
                check(timeline:SetVoiceIsolationState(spec.track_index,{isEnabled=spec.enabled,amount=spec.amount})==true,
                    "VOICE_ISOLATION_REJECTED")
                local after=timeline:GetVoiceIsolationState(spec.track_index)
                check(type(after)=="table" and after.isEnabled==spec.enabled and after.amount==spec.amount,
                    "VOICE_ISOLATION_READBACK_FAILED")
                check(layout(timeline)==before,"AUDIO_LAYOUT_CHANGED")
                for _,v in ipairs(gains) do check(v.item:GetProperty("AudioVolume")==v.gain,"AUDIO_GAIN_CHANGED") end
            end
        end
        if spec.operation=="discard_failed_pilot" or spec.operation=="discard_failed_batch" then
            check(timeline:GetTrackCount("audio")==2 and timeline:GetIsTrackEnabled("audio",1)==true
                and timeline:GetIsTrackEnabled("audio",2)==false,"FAILED_PILOT_ROUTING_CHANGED")
            local source=timeline:GetItemListInTrack("audio",1) or {}
            local partial=timeline:GetItemListInTrack("audio",2) or {}
            local expected=spec.items or {spec}
            check(#source==spec.source_count and #partial==#expected,"FAILED_PILOT_LAYOUT_CHANGED")
            for i,b in ipairs(expected) do
                local item=partial[i]
                check(item:GetStart()==b.start_frame and item:GetEnd()==b.end_frame
                    and item:GetSourceStartFrame()==b.source_start_frame
                    and item:GetProperty("AudioVolume")==0
                    and #(item:GetLinkedItems() or {})==0,"FAILED_PILOT_ITEM_CHANGED")
            end
            return function()
                select_target()
                check(layout(timeline)==before,"TIMELINE_CHANGED")
                check(timeline:DeleteTrack("audio",2)==true,"FAILED_PILOT_DELETE_FAILED")
                check(timeline:GetTrackCount("audio")==1 and timeline:GetIsTrackEnabled("audio",1)==true,
                      "FAILED_PILOT_CLEANUP_READBACK_FAILED")
                local remaining=timeline:GetItemListInTrack("audio",1) or {}
                check(#remaining==#source,"FAILED_PILOT_CLEANUP_READBACK_FAILED")
                for i,v in ipairs(source) do
                    check(remaining[i]:GetUniqueId()==v:GetUniqueId(),"FAILED_PILOT_CLEANUP_READBACK_FAILED")
                end
            end
        end
        local chosen = {}
        if spec.operation == "normalize" then
            check(type(timeline.NormalizeAudioLevel)=="function", "NORMALIZATION_UNAVAILABLE")
            local supported=false
            for _,name in pairs(timeline:GetNormalizeAudioModes() or {}) do
                if name==spec.normalization_mode then supported=true end
            end
            check(supported,"NORMALIZATION_MODE_UNAVAILABLE")
            check(api.NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT~=nil,"NORMALIZATION_CONSTANT_UNAVAILABLE")
            local items=timeline:GetItemListInTrack("audio",spec.track_index) or {}
            for _,selection in ipairs(spec.items) do
                local item=items[selection.index]
                check(item and item:GetStart()==selection.start_frame
                    and item:GetEnd()==selection.end_frame,"AUDIO_ITEM_CHANGED")
                check(item:GetClipEnabled()==true,"AUDIO_ITEM_DISABLED")
                chosen[#chosen+1]=item
            end
        elseif spec.operation == "preset" then
            local matches=0
            for _,name in pairs(api:GetFairlightPresets() or {}) do
                if name==spec.preset_name then matches=matches+1 end
            end
            check(matches==1,"FAIRLIGHT_PRESET_MISSING_OR_AMBIGUOUS")
        else check(false,"INVALID_AUDIO_OPERATION") end
        return function()
            select_target()
            check(layout(timeline)==before,"TIMELINE_CHANGED")
            check(project:SetCurrentTimeline(timeline)==true,"TIMELINE_SELECT_FAILED")
            check(project:GetCurrentTimeline():GetUniqueId()==id,"TIMELINE_CHANGED")
            if spec.operation=="normalize" then
                check(timeline:NormalizeAudioLevel(chosen,{
                    normalizationMode=spec.normalization_mode,
                    targetLevel=spec.target_peak,
                    targetLoudness=spec.target_loudness,
                    setLevelMode=api.NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT,
                })==true,"NORMALIZATION_FAILED")
                for _,item in ipairs(chosen) do
                    local value=item:GetProperty("AudioVolume")
                    check(type(value)=="number" and value==value
                        and value>=-60 and value<=12,"AUDIO_GAIN_READBACK_FAILED")
                end
            else
                check(project:ApplyFairlightPresetToCurrentTimeline(spec.preset_name)==true,
                      "FAIRLIGHT_PRESET_APPLY_FAILED")
            end
            check(project:GetCurrentTimeline():GetUniqueId()==id and layout(timeline)==before,
                  "AUDIO_LAYOUT_CHANGED")
            -- API success and invariant checks are not an audible/loudness QA verdict.
        end
    end
end
