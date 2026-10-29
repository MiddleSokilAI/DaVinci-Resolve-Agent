-- Documented native track preparation only. Never starts/stops transport,
-- patches hardware, processes samples, deletes tracks or changes clips.
local function check(value, code)
    if not value then error("RECORDING_" .. code, 0) end
end

local function integer(value, minimum, maximum)
    return type(value)=="number" and value%1==0 and value>=minimum and value<=maximum
end

local function text(value)
    if type(value)~="string" or #value<1 or #value>512
        or value:find("[%z\1-\31\127]") or value:match("^%s") or value:match("%s$") then return false end
    local _, characters=value:gsub("[^\128-\191]", "")
    return characters<=128
end

local function exact(value, required, optional)
    check(type(value)=="table", "INVALID_ARGUMENTS")
    for key in pairs(value) do check(required[key] or (optional and optional[key]), "INVALID_ARGUMENTS") end
    for key in pairs(required) do check(value[key]~=nil, "INVALID_ARGUMENTS") end
end

local function arguments(a, operation)
    exact(a, {timeline_name=true,recording=true})
    check(text(a.timeline_name), "INVALID_ARGUMENTS")
    local s=a.recording
    if operation=="inspect" then exact(s,{operation=true,track_name=true})
    elseif operation=="ensure_mono" then
        exact(s,{operation=true,track_name=true,expected_audio_track_count=true})
        check(integer(s.expected_audio_track_count,0,64), "INVALID_ARGUMENTS")
    elseif operation=="configure_media_root" then
        exact(s,{operation=true,media_root=true})
        check(type(s.media_root)=="string" and #s.media_root>0 and #s.media_root<=8192
            and not s.media_root:find("[%z\1-\31\127]"),"INVALID_ARGUMENTS")
    else
        exact(s,{operation=true,timecode=true},{page=true})
        check(type(s.timecode)=="string" and s.timecode:match("^%d%d:%d%d:%d%d:%d%d$")
            and tonumber(s.timecode:sub(1,2))<=23 and tonumber(s.timecode:sub(4,5))<=59
            and tonumber(s.timecode:sub(7,8))<=59,"INVALID_ARGUMENTS")
        check(s.page==nil or s.page=="fairlight" or s.page=="edit", "INVALID_ARGUMENTS")
    end
    check(s.operation==operation, "INVALID_ARGUMENTS")
    if operation=="inspect" or operation=="ensure_mono" then check(text(s.track_name), "INVALID_ARGUMENTS") end
    return s
end

local function method(value, key)
    check(type(value[key])=="function", "API_UNAVAILABLE")
end

local function timeline(project, name)
    method(project,"GetTimelineCount");method(project,"GetTimelineByIndex")
    local count=project:GetTimelineCount()
    check(integer(count,0,1000),"UNKNOWN_LAYOUT")
    local found
    for i=1,count do
        local t=project:GetTimelineByIndex(i)
        check(t~=nil,"UNKNOWN_LAYOUT");method(t,"GetName")
        if t:GetName()==name then check(found==nil,"AMBIGUOUS_TIMELINE");found=t end
    end
    check(found~=nil,"TIMELINE_NOT_FOUND")
    return found
end

local function tracks(t, name)
    for _,key in ipairs({"GetTrackCount","GetTrackName","GetTrackSubType","GetItemListInTrack"}) do method(t,key) end
    local count=t:GetTrackCount("audio")
    check(integer(count,0,64),"UNKNOWN_LAYOUT")
    local names,index={},0
    for i=1,count do
        local current=t:GetTrackName("audio",i)
        check(type(current)=="string" and #current<=4096,"UNKNOWN_LAYOUT")
        names[i]=current
        if current==name then check(index==0,"AMBIGUOUS_TRACK");index=i end
    end
    local subtype,clips="missing",0
    if index>0 then
        subtype=t:GetTrackSubType("audio",index)
        check(subtype=="mono" or subtype=="stereo","UNKNOWN_LAYOUT")
        local items=t:GetItemListInTrack("audio",index)
        check(type(items)=="table" and #items<=10000,"UNKNOWN_LAYOUT")
        clips=#items
        local item_count=0
        for key,value in pairs(items) do
            -- Resolve 21.1's native Lua lists include numeric __flags metadata;
            -- it is not a timeline item and must not affect the clip count.
            if key=="__flags" then
                check(type(value)=="number" and value==value
                    and value>-math.huge and value<math.huge,"UNKNOWN_LAYOUT")
            else
                check(integer(key,1,clips),"UNKNOWN_LAYOUT")
                item_count=item_count+1
            end
        end
        check(item_count==clips,"UNKNOWN_LAYOUT")
    end
    return {count=count,index=index,subtype=subtype,clips=clips,names=names}
end

local function sample_rate(project)
    -- A zero acknowledgement means unknown, never an inferred recording format.
    local value
    if type(project.GetSettings)=="function" then
        local ok,settings=pcall(project.GetSettings,project)
        if ok and type(settings)=="table" then value=settings.timelineSampleRate end
    end
    if value==nil and type(project.GetSetting)=="function" then
        local ok,legacy=pcall(project.GetSetting,project,"timelineSampleRate")
        if ok then value=legacy end
    end
    local rate=tonumber(value)
    return integer(rate,3000,768000) and rate or 0
end

local function tag(operation, state, project, timecode)
    return string.format("recording_%s_%d_%d_%d_%s_%d_%s", operation,
        state.index,state.count,state.clips,state.subtype,sample_rate(project),timecode or "0")
end

local function inspect(api, project, a)
    local s=arguments(a,"inspect")
    return tag("inspect",tracks(timeline(project,a.timeline_name),s.track_name),project)
end

local function select_target(project, t)
    method(project,"SetCurrentTimeline");method(project,"GetCurrentTimeline");method(t,"GetUniqueId")
    local id=t:GetUniqueId()
    check(type(id)=="string" and id~="", "UNKNOWN_LAYOUT")
    check(project:SetCurrentTimeline(t)==true,"TIMELINE_SELECT_FAILED")
    local current=project:GetCurrentTimeline()
    check(current and current:GetUniqueId()==id,"TIMELINE_CHANGED")
end

local function media_path(value)
    local normalized=value:gsub("\\","/")
    if normalized~="/" and not normalized:match("^%a:/$") then normalized=normalized:gsub("/+$", "") end
    if normalized:match("^%a:/") or normalized:match("^//") then normalized=normalized:lower() end
    return normalized
end

local function media_root(project, path, roots)
    check(type(roots)=="table", "MEDIA_POLICY_UNAVAILABLE")
    local normalized=media_path(path)
    check(normalized:match("^%a:/") or normalized:match("^/"),"INVALID_ARGUMENTS")
    check(not normalized:find("/%./") and not normalized:find("/%.%./")
        and not normalized:match("/%.%.?$"),"INVALID_ARGUMENTS")
    local allowed=false
    for _,root in ipairs(roots) do
        check(type(root)=="string" and root~="","MEDIA_POLICY_UNAVAILABLE")
        local base=media_path(root)
        local prefix=base:sub(-1)=="/" and base or base.."/"
        if normalized==base or normalized:sub(1,#prefix)==prefix then allowed=true end
    end
    check(allowed,"MEDIA_ROOT_NOT_ALLOWED")
    local function read()
        if type(project.GetSettings)=="function" then
            local ok,settings=pcall(project.GetSettings,project)
            if ok and type(settings)=="table" and type(settings.projectMediaLocation)=="string" then
                return settings.projectMediaLocation
            end
        end
        if type(project.GetSetting)=="function" then
            local ok,value=pcall(project.GetSetting,project,"projectMediaLocation")
            if ok then return value end
        end
    end
    check(type(read())=="string","MEDIA_ROOT_READBACK_UNAVAILABLE")
    if type(project.SetSettings)=="function" then
        check(project:SetSettings({projectMediaLocation=path})==true,"MEDIA_ROOT_SET_FAILED")
    else
        method(project,"SetSetting")
        check(project:SetSetting("projectMediaLocation",path)==true,"MEDIA_ROOT_SET_FAILED")
    end
    local after=read()
    check(type(after)=="string" and media_path(after)==normalized,"MEDIA_ROOT_READBACK_FAILED")
end

local function apply(api, project, a, roots)
    check(type(a)=="table" and type(a.recording)=="table","INVALID_ARGUMENTS")
    local operation=a.recording.operation
    check(operation=="ensure_mono" or operation=="seek" or operation=="configure_media_root","INVALID_ARGUMENTS")
    local s=arguments(a,operation)
    local t=timeline(project,a.timeline_name)
    if operation=="configure_media_root" then
        media_root(project,s.media_root,roots)
        return tag(operation,{index=0,count=0,clips=0,subtype="missing"},project)
    end
    if operation=="ensure_mono" then
        local before=tracks(t,s.track_name)
        check(before.count==s.expected_audio_track_count,"TRACK_COUNT_CHANGED")
        check(before.subtype=="missing" or before.subtype=="mono","TRACK_NOT_MONO")
        if before.index>0 then return tag(operation,before,project) end
        check(before.count<64,"TRACK_LIMIT")
        method(t,"AddTrack");method(t,"SetTrackName")
        select_target(project,t)
        check(t:GetTrackCount("audio")==before.count,"TRACK_COUNT_CHANGED")
        check(t:AddTrack("audio","mono")==true,"TRACK_CREATE_FAILED")
        check(t:GetTrackCount("audio")==before.count+1,"TRACK_CREATE_READBACK_FAILED")
        local index=before.count+1
        check(t:GetTrackSubType("audio",index)=="mono","TRACK_CREATE_READBACK_FAILED")
        check(t:SetTrackName("audio",index,s.track_name)==true,"TRACK_RENAME_FAILED")
        local after=tracks(t,s.track_name)
        check(after.count==before.count+1 and after.index==index and after.subtype=="mono"
            and after.clips==0,"TRACK_CREATE_READBACK_FAILED")
        for i=1,before.count do check(after.names[i]==before.names[i],"EXISTING_TRACK_CHANGED") end
        return tag(operation,after,project)
    end
    for _,key in ipairs({"GetCurrentPage","OpenPage"}) do method(api,key) end
    for _,key in ipairs({"SetCurrentTimecode","GetCurrentTimecode"}) do method(t,key) end
    local wanted=s.page or api:GetCurrentPage()
    local seek_pages={cut=true,edit=true,color=true,fairlight=true,deliver=true}
    check(seek_pages[wanted],"SEEK_PAGE_UNSUPPORTED")
    select_target(project,t)
    if api:GetCurrentPage()~=wanted then check(api:OpenPage(wanted)==true,"PAGE_OPEN_FAILED") end
    check(api:GetCurrentPage()==wanted,"PAGE_READBACK_FAILED")
    check(t:SetCurrentTimecode(s.timecode)==true,"SEEK_FAILED")
    check(t:GetCurrentTimecode()==s.timecode and api:GetCurrentPage()==wanted,"SEEK_READBACK_FAILED")
    -- Seeking does not select or inspect a track; its zero fields mean not queried.
    return tag(operation,{index=0,count=0,clips=0,subtype="missing"},project,(s.timecode:gsub(":","")))
end

local function protected(operation, ...)
    local ok,result=pcall(operation,...)
    if not ok then
        local code=type(result)=="string" and result:match("^(RECORDING_[A-Z_]+)$")
        error(code or "RECORDING_NATIVE_ERROR",0)
    end
    return result
end

return {
    inspect=function(...) return protected(inspect,...) end,
    apply=function(...) return protected(apply,...) end,
}
