-- Fixed finishing operations. Preflight returns a mutation closure.
return function(api, root, helpers, shared)
    local check, timelines, allowed = helpers.check, helpers.timelines, helpers.allowed
    local normalized = helpers.normalized
    local jobs = shared.jobs -- Reload retains this running bridge's owned jobs.
    local function counts(timeline)
        local result = {video=0, audio=0, subtitle=0, first=-1, last=-1}
        for _,kind in ipairs({"video", "audio", "subtitle"}) do
            for track=1,timeline:GetTrackCount(kind) do
                for _,item in ipairs(timeline:GetItemListInTrack(kind, track) or {}) do
                    result[kind] = result[kind] + 1
                    if kind == "subtitle" then
                        result.first = result.first == -1 and item:GetStart() or math.min(result.first,item:GetStart())
                        result.last = math.max(result.last,item:GetEnd())
                    end
                end
            end
        end
        return result
    end
    local function summary(project, request)
        check(project:GetUniqueId() == request.project_id, "PROJECT_CHANGED")
        local timeline = timelines(project, request.arguments.timeline_name)
        check(timeline ~= nil, "TIMELINE_NOT_FOUND")
        local a=request.arguments
        if a.ripple_batch then
            return dofile(root..'/ripple_batch.lua')(helpers,root,request.id,true)(project,a)
        end
        if a.ripple_cut then
            return dofile(root..'/ripple.lua')(helpers,root,request.id,true)(project,a)
        end
        if a.ripple_span then
            dofile(root.."/ripple_span.lua")(helpers,root,request.id)(project,a)
            return "span_ready"
        end
        if a.track_type then
            local item=(timeline:GetItemListInTrack(a.track_type,a.track_index) or {})[a.item_index]
            check(item and item:GetMediaPoolItem()
                and normalized(item:GetMediaPoolItem():GetClipProperty("File Path"))==normalized(a.expected_media_path), "SOURCE_CHANGED")
            if a.inspect_camera_visibility then
                return dofile(root.."/camera_visibility.lua")(helpers).inspect(item)
            end
            if a.inspect_privacy then
                return dofile(root .. "/privacy.lua")(helpers).inspect(item)
            end
            if a.inspect_circle then
                local ok,n=pcall(function() return item:GetFusionCompCount() end)
                local names_ok,names=pcall(function() return item:GetFusionCompNames() end)
                local nodes, connected, diameter=-1,0,-1
                local probe_ok=pcall(function()
                    local c=item:GetFusionCompByIndex(1)
                    local ts=c:GetToolList(false)
                    nodes=0
                    for _ in pairs(ts) do nodes=nodes+1 end
                    local out=c:FindTool("MediaOut1")
                    local merge=c:FindTool("Merge1")
                    local ellipse=c:FindTool("Ellipse1")
                    if out and merge and ellipse then
                        diameter=math.floor(ellipse:GetInput("Width")*1000+0.5)
                        local link=out.Input:GetConnectedOutput()
                        connected=link and link:GetTool():GetAttrs().TOOLS_Name==merge:GetAttrs().TOOLS_Name and 1 or 0
                    end
                end)
                return string.format("fusion_%d_%d_%d_%d_%d_%d",ok and type(n)=="number" and n or -1,
                    names_ok and type(names)=="table" and #names or -1,
                    type(item.AddFusionComp)=="function" and 1 or 0,probe_ok and nodes or -1,connected,diameter)
            end
            return string.format("item_%d_%d_%d_%d_%d_%d_%d_%d", item:GetStart(),item:GetEnd(),
                item:GetSourceStartFrame(),item:GetSourceEndFrame(),#(item:GetLinkedItems() or {}),
                math.floor(item:GetSourceStartTime()*1000000+0.5),
                math.floor(item:GetSourceEndTime()*1000000+0.5),
                math.floor(item:GetLeftOffset(true)*1000000+0.5))
        end
        local c = counts(timeline)
        local fps = tonumber(tostring(timeline:GetSetting("timelineFrameRate")):match("[%d.]+"))
        check(fps ~= nil, "VERIFY_FAILED")
        return string.format("timeline_%d_%d_%d_%d_%d_%d_%d_%d", c.video,c.audio,c.subtitle,
            timeline:GetStartFrame(),timeline:GetEndFrame(),c.first,c.last,math.floor(fps*1000+0.5))
    end
    local function job(project, a)
        local owned = jobs[a.job_id]
        check(owned and owned.project == project:GetUniqueId(), "JOB_NOT_OWNED")
        return owned
    end
    local function status(project, request)
        check(project:GetUniqueId() == request.project_id, "PROJECT_CHANGED")
        local owned = job(project, request.arguments)
        if owned.start_failed then return "state_failed" end
        local state = project:GetRenderJobStatus(request.arguments.job_id)
        check(type(state) == "table", "VERIFY_FAILED")
        local value = tostring(state.JobStatus):lower()
        if value == "complete" or value == "completed" then return "state_complete" end
        if value == "failed" or value == "cancelled" or value == "canceled" then return "state_failed" end
        if value == "rendering" or value == "in progress" then return "state_running" end
        if value == "ready" or value == "queued" then return "state_queued" end
        return "state_unknown"
    end
    local function preflight(project, request)
        local a, action = request.arguments, request.action
        if action == "set_clip_properties" and a.ripple_batch then
            return dofile(root..'/ripple_batch.lua')(helpers,root,request.id)(project,a)
        end
        if action == "set_clip_properties" and a.ripple_repair then
            return dofile(root..'/ripple.lua')(helpers,root,request.id,true,true)(project,{timeline_name=a.timeline_name,ripple_cut=a.ripple_repair})
        end
        if action == "set_clip_properties" and a.privacy_batch then
            return dofile(root.."/privacy.lua")(helpers).preflight_batch(project,a)
        end
        if action == "set_clip_properties" and a.privacy_blur then
            return dofile(root .. "/privacy.lua")(helpers).preflight(project,a)
        end
        if action == "set_clip_properties" and a.camera_visibility then
            return dofile(root.."/camera_visibility.lua")(helpers).preflight(project,a)
        end
        if action == "set_clip_properties" and a.ripple_span then
            return dofile(root .. "/ripple_span.lua")(helpers,root,request.id)(project,a)
        end
        if action == "set_clip_properties" and a.ripple_cut then
            return dofile(root .. "/ripple.lua")(helpers,root,request.id)(project, a)
        end
        if action == "start_render" then
            local owned = job(project, a)
            check(not owned.started, "JOB_ALREADY_STARTED")
            local unchanged = false
            for _, info in ipairs(project:GetRenderJobList() or {}) do
                if info.JobId == a.job_id then
                    check(type(info.TargetDir) == "string"
                        and normalized(info.TargetDir) == normalized(owned.directory)
                        and info.TimelineName == owned.name, "JOB_CHANGED")
                    if owned.mark_in then
                        check(tonumber(info.MarkIn)==owned.mark_in
                            and tonumber(info.MarkOut)==owned.mark_out,"JOB_CHANGED")
                    end
                    unchanged = true
                end
            end
            check(unchanged, "JOB_CHANGED")
            return function()
                check(project:GetCurrentTimeline():GetUniqueId() == owned.timeline, "VERIFY_FAILED")
                -- Acceptance is exported BEFORE rendering blocks ExportProject.
                -- It confirms dispatch, never rendering or completion.
                local pm = api:GetProjectManager()
                check(pm:ExportProject(project:GetName(), root .. "/" .. request.id .. ".accepted.drp", false) == true, "ACCEPT_EXPORT_FAILED")
                owned.started = true -- Never resubmit after acceptance, even on failure.
                local ok, started = pcall(function() return project:StartRendering({a.job_id}, false) end)
                owned.start_failed = not ok or started ~= true
                check(not owned.start_failed, "RENDER_START_FAILED")
            end
        end
        local timeline = timelines(project, a.timeline_name)
        check(timeline ~= nil, "TIMELINE_NOT_FOUND")
        if action == "set_clip_properties" and a.native_audio then
            if a.native_audio.operation=="phrase_pilot" or a.native_audio.operation=="phrase_batch" then
                return dofile(root.."/audio_pilot.lua")(api,helpers)(project,timeline,a.native_audio)
            end
            return dofile(root.."/native_audio.lua")(api,helpers)(project,timeline,a.native_audio)
        end
        if action == "set_clip_properties" and a.open_page then
            check(a.open_page == "edit" or a.open_page == "fairlight", "INVALID_ARGUMENTS")
            local id = timeline:GetUniqueId()
            return function()
                check(project:SetCurrentTimeline(timeline) == true, "TIMELINE_SELECT_FAILED")
                check(project:GetCurrentTimeline():GetUniqueId() == id, "TIMELINE_CHANGED")
                check(api:OpenPage(a.open_page) == true, "PAGE_OPEN_FAILED")
                check(api:GetCurrentPage() == a.open_page, "PAGE_READBACK_FAILED")
            end
        end
        if action=="set_clip_properties" and a.replacement_take_path then
            local item=(timeline:GetItemListInTrack("video",a.track_index) or {})[a.item_index]
            check(item and item:GetMediaPoolItem()
                and normalized(item:GetMediaPoolItem():GetClipProperty("File Path"))==normalized(a.expected_media_path),"SOURCE_CHANGED")
            check(allowed(a.replacement_take_path),"MEDIA_NOT_ALLOWED")
            local replacement=helpers.find_asset(project:GetMediaPool(),a.replacement_take_path)
            check(replacement~=nil,"ASSET_NOT_FOUND")
            check(item:GetTakesCount()==0,"TAKES_EXIST")
            check(item:GetFusionCompCount()==0,"FUSION_COMP_EXISTS")
            local first,last=item:GetStart(),item:GetEnd()
            local frames=tonumber(replacement:GetClipProperty("Frames"))
            local fps=tonumber(replacement:GetClipProperty("FPS"))
            check(frames==last-first and fps==tonumber(timeline:GetSetting("timelineFrameRate")),"TAKE_FORMAT_MISMATCH")
            local links={}
            for _,linked in ipairs(item:GetLinkedItems() or {}) do
                links[linked:GetUniqueId()]={first=linked:GetStart(),last=linked:GetEnd()}
            end
            return function()
                check(api:OpenPage("edit")==true,"VERIFY_FAILED")
                check(project:SetCurrentTimeline(timeline)==true,"VERIFY_FAILED")
                check(item:AddTake(replacement)==true,"TAKE_ADD_FAILED")
                local index=item:GetTakesCount()
                check(index==2,"VERIFY_FAILED")
                local take=item:GetTakeByIndex(index)
                check(take and take.mediaPoolItem and take.mediaPoolItem:GetUniqueId()==replacement:GetUniqueId(),"VERIFY_FAILED")
                check(item:SelectTakeByIndex(index)==true,"TAKE_SELECT_FAILED")
                check(item:GetSelectedTakeIndex()==index and item:GetStart()==first and item:GetEnd()==last,"VERIFY_FAILED")
                check(item:GetMediaPoolItem():GetUniqueId()==replacement:GetUniqueId(),"VERIFY_FAILED")
                check(item:GetSourceStartFrame()==0 and item:GetSourceEndFrame()>=frames-1
                    and item:GetSourceEndFrame()<=frames,"VERIFY_FAILED")
                for _,linked in ipairs(item:GetLinkedItems() or {}) do
                    local prior=links[linked:GetUniqueId()]
                    check(prior and prior.first==linked:GetStart() and prior.last==linked:GetEnd(),"VERIFY_FAILED")
                    links[linked:GetUniqueId()]=nil
                end
                check(next(links)==nil,"VERIFY_FAILED")
            end
        end
        if action=="set_clip_properties" and a.empty_timeline_fps then
            local fps=a.empty_timeline_fps
            check(fps==24 or fps==25 or fps==30 or fps==50 or fps==60,"INVALID_ARGUMENTS")
            local c=counts(timeline)
            check(c.video==0 and c.audio==0 and c.subtitle==0,"TIMELINE_NOT_EMPTY")
            return function()
                check(api:OpenPage("edit")==true,"VERIFY_FAILED")
                check(project:SetCurrentTimeline(timeline)==true,"VERIFY_FAILED")
                check(timeline:SetSettings({useCustomSettings="1",timelineFrameRate=tostring(fps)})==true,"TIMELINE_SETTINGS_FAILED")
                check(tonumber(timeline:GetSetting("timelineFrameRate"))==fps,"VERIFY_FAILED")
            end
        end
        if action == "set_clip_properties" and a.preview_start == true then
            local target=timeline:GetStartTimecode()
            if a.preview_frame~=nil then
                local frame=a.preview_frame
                local fps=tonumber(timeline:GetSetting("timelineFrameRate"))
                check(type(frame)=="number" and frame%1==0 and frame>=timeline:GetStartFrame()
                    and frame<timeline:GetEndFrame(), "INVALID_PREVIEW_FRAME")
                check(fps and (fps==24 or fps==25 or fps==30 or fps==50 or fps==60), "UNSUPPORTED_PREVIEW_FPS")
                local seconds=math.floor(frame/fps)
                target=string.format("%02d:%02d:%02d:%02d",math.floor(seconds/3600),
                    math.floor(seconds/60)%60,seconds%60,frame%fps)
            end
            return function()
                check(project:SetCurrentTimeline(timeline)==true, "VERIFY_FAILED")
                check(api:OpenPage("edit")==true, "VERIFY_FAILED")
                check(timeline:SetCurrentTimecode(target)==true, "VERIFY_FAILED")
                check(project:GetCurrentTimeline():GetUniqueId()==timeline:GetUniqueId()
                    and timeline:GetCurrentTimecode()==target
                    and api:GetCurrentPage()=="edit", "VERIFY_FAILED")
            end
        end
        if action == "set_clip_properties" and a.circle_mask then
            check(type(a.track_index)=="number" and a.track_index>=1 and a.track_index%1==0
                and type(a.item_index)=="number" and a.item_index>=1 and a.item_index%1==0, "INVALID_ARGUMENTS")
            local item=(timeline:GetItemListInTrack("video",a.track_index) or {})[a.item_index]
            check(item and item:GetMediaPoolItem()
                and normalized(item:GetMediaPoolItem():GetClipProperty("File Path"))==normalized(a.expected_media_path), "SOURCE_CHANGED")
            local m=a.circle_mask
            for _,key in ipairs({"center_x","center_y","diameter"}) do
                local v=m[key]
                check(type(v)=="number" and v==v and v>=0.05 and v<=(key=="diameter" and 0.9 or 0.95), "INVALID_ARGUMENTS")
            end
            check(item:GetFusionCompCount()==0, "FUSION_COMP_EXISTS")
            return function()
                check(project:SetCurrentTimeline(timeline)==true, "VERIFY_FAILED")
                local comp=item:AddFusionComp()
                check(comp~=nil, "FUSION_UNAVAILABLE")
                local input=comp:FindTool("MediaIn1")
                local output=comp:FindTool("MediaOut1")
                check(input and output, "FUSION_IO_MISSING")
                local ellipse=comp:AddTool("EllipseMask",-2,1)
                local bg=comp:AddTool("Background",-2,-1)
                local merge=comp:AddTool("Merge",0,0)
                check(ellipse and bg and merge, "FUSION_NODE_FAILED")
                ellipse:SetInput("Center",{m.center_x,m.center_y})
                ellipse:SetInput("Width",m.diameter)
                -- Fusion mask width/height use aspect-corrected coordinates:
                -- equal values produce a circle on square-pixel footage.
                ellipse:SetInput("Height",m.diameter)
                ellipse:SetInput("SoftEdge",0.002)
                bg:SetInput("TopLeftAlpha",0)
                merge:ConnectInput("Background",bg)
                merge:ConnectInput("Foreground",input)
                merge:ConnectInput("EffectMask",ellipse)
                output:ConnectInput("Input",merge)
                check(math.abs(ellipse:GetInput("Width")-m.diameter)<0.0001
                    and math.abs(ellipse:GetInput("Height")-m.diameter)<0.0001
                    and bg:GetInput("TopLeftAlpha")==0
                    and merge.Background:GetConnectedOutput():GetTool():GetAttrs().TOOLS_Name==bg:GetAttrs().TOOLS_Name
                    and merge.Foreground:GetConnectedOutput():GetTool():GetAttrs().TOOLS_Name==input:GetAttrs().TOOLS_Name
                    and merge.EffectMask:GetConnectedOutput():GetTool():GetAttrs().TOOLS_Name==ellipse:GetAttrs().TOOLS_Name
                    and output.Input:GetConnectedOutput():GetTool():GetAttrs().TOOLS_Name==merge:GetAttrs().TOOLS_Name, "VERIFY_FAILED")
            end
        end
        if action == "set_clip_properties" then
            check(a.track_type == "video" or a.track_type == "audio", "INVALID_ARGUMENTS")
            local items = timeline:GetItemListInTrack(a.track_type, a.track_index) or {}
            local item = items[a.item_index]
            check(item ~= nil, "ITEM_NOT_FOUND")
            local media = item:GetMediaPoolItem()
            check(media and normalized(media:GetClipProperty("File Path")) == normalized(a.expected_media_path), "SOURCE_CHANGED")
            local limits = {ZoomX={0.1,4}, ZoomY={0.1,4}, Pan={-3840,3840}, Tilt={-2160,2160}, Opacity={0,100}, AudioVolume={-60,12}}
            check(type(a.properties) == "table" and next(a.properties) ~= nil, "INVALID_ARGUMENTS")
            for key,value in pairs(a.properties) do
                local bound = limits[key]
                check(bound and type(value) == "number" and value >= bound[1] and value <= bound[2], "INVALID_ARGUMENTS")
                check((a.track_type == "audio") == (key == "AudioVolume"), "INVALID_ARGUMENTS")
            end
            return function()
                check(project:SetCurrentTimeline(timeline) == true, "VERIFY_FAILED")
                local changes = {}
                for k,v in pairs(a.properties) do changes[k] = v end
                if a.track_type == "audio" then changes.AudioVolumeEnabled = true
                else
                    if changes.ZoomX or changes.ZoomY or changes.Pan or changes.Tilt then
                        changes.TransformEnabled = true
                    end
                    if changes.ZoomX or changes.ZoomY then changes.ZoomGang = false end
                    if changes.Opacity then changes.CompositeEnabled = true end
                end
                check(item:SetProperties(changes) == true, "VERIFY_FAILED")
                local result = item:GetProperties()
                for k,v in pairs(changes) do
                    check(type(v) == "number" and type(result[k]) == "number" and math.abs(result[k]-v) < 0.001 or result[k] == v, "VERIFY_FAILED")
                end
            end
        elseif action == "add_subtitles" then
            check(allowed(a.subtitle_path) and a.cue_count >= 1 and a.cue_count <= 2000, "INVALID_ARGUMENTS")
            local existing = counts(timeline)
            check(existing.video == 0 and existing.audio == 0, "SUBTITLE_REQUIRES_EMPTY_AV")
            for track=1,timeline:GetTrackCount("subtitle") do
                check(#(timeline:GetItemListInTrack("subtitle", track) or {}) == 0, "SUBTITLES_EXIST")
            end
            return function()
                check(project:SetCurrentTimeline(timeline) == true, "VERIFY_FAILED")
                local pool = project:GetMediaPool()
                local imported = pool:ImportMedia({a.subtitle_path})
                check(type(imported) == "table" and #imported == 1, "SUBTITLE_IMPORT_FAILED")
                local appended = pool:AppendToTimeline({{mediaPoolItem=imported[1], recordFrame=timeline:GetStartFrame()}})
                check(type(appended) == "table", "SUBTITLE_APPEND_FAILED")
                local until_time = math.min(request.expires, os.time()+5)
                local c = counts(timeline)
                while c.subtitle < a.cue_count and os.time() < until_time do
                    bmd.wait(0.1)
                    c = counts(timeline)
                end
                local fps = tonumber(tostring(timeline:GetSetting("timelineFrameRate")):match("[%d.]+"))
                check(fps and c.subtitle == a.cue_count, "SUBTITLE_COUNT_FAILED")
                check(math.abs(c.first-timeline:GetStartFrame()-a.first_start*fps) <= 1.1, "SUBTITLE_TIMING_FAILED")
                check(math.abs(c.last-timeline:GetStartFrame()-a.last_end*fps) <= 1.1, "SUBTITLE_TIMING_FAILED")
            end
        elseif action == "prepare_render" then
            check(timeline:GetEndFrame() > timeline:GetStartFrame(), "EMPTY_TIMELINE")
            local ranged=a.start_frame~=nil or a.end_frame~=nil
            if ranged then
                check(type(a.start_frame)=="number" and a.start_frame%1==0
                    and type(a.end_frame)=="number" and a.end_frame%1==0
                    and a.start_frame>=timeline:GetStartFrame()
                    and a.end_frame<=timeline:GetEndFrame()
                    and a.start_frame<a.end_frame,"RENDER_RANGE_INVALID")
            end
            return function()
                check(project:SetCurrentTimeline(timeline) == true, "VERIFY_FAILED")
                local previous_page = api:GetCurrentPage()
                check(api:OpenPage("deliver") == true, "DELIVER_PAGE_FAILED")
                bmd.wait(0.25)
                check(project:LoadRenderPreset("YouTube - 1080p") == true, "RENDER_PRESET_FAILED")
                check(project:SetCurrentRenderFormatAndCodec("MP4", "H264") == true, "RENDER_FORMAT_FAILED")
                check(project:SetCurrentRenderMode(1) == true, "RENDER_MODE_FAILED")
                local directory = root .. "/renders/" .. request.id
                local settings={SelectAllFrames=not ranged, TargetDir=directory,
                    CustomName="video", ExportVideo=true, ExportAudio=true,
                    FormatWidth=1920, FormatHeight=1080}
                if ranged then settings.MarkIn=a.start_frame;settings.MarkOut=a.end_frame-1 end
                check(project:SetRenderSettings(settings) == true, "RENDER_BASE_SETTINGS_FAILED")
                check(project:SetRenderSettings({ExportSubtitle=true, SubtitleFormat="BurnIn"}) == true, "RENDER_SUBTITLE_SETTINGS_FAILED")
                check(project:SetRenderSettings({DataBurnIn="None"}) == true, "RENDER_BURNIN_SETTINGS_FAILED")
                -- Resolve Free rejects ReplaceExistingFilesInPlace in single-clip mode.
                -- Python reserves a fresh directory and rechecks it before start.
                local id = project:AddRenderJob()
                check(type(id) == "string" and #id <= 64 and id:match("^[%w%-]+$"), "RENDER_QUEUE_FAILED")
                local found = false
                for _,info in ipairs(project:GetRenderJobList() or {}) do
                    if info.JobId == id then
                        check(normalized(info.TargetDir) == normalized(directory), "VERIFY_FAILED")
                        check(info.TimelineName == timeline:GetName(), "VERIFY_FAILED")
                        if ranged then
                            check(tonumber(info.MarkIn)==a.start_frame
                                and tonumber(info.MarkOut)==a.end_frame-1,"VERIFY_FAILED")
                        end
                        found = true
                    end
                end
                check(found, "VERIFY_FAILED")
                jobs[id] = {project=project:GetUniqueId(), timeline=timeline:GetUniqueId(),
                    name=timeline:GetName(), directory=directory, started=false,
                    mark_in=ranged and a.start_frame or nil,
                    mark_out=ranged and a.end_frame-1 or nil}
                if previous_page then api:OpenPage(previous_page) end
                if ranged then
                    return string.format("job_%s_%d_%d",id,a.start_frame,a.end_frame)
                end
                return "job_" .. id
            end
        end
        error("INVALID_ARGUMENTS", 0)
    end
    return {preflight=preflight, status=status, summary=summary}
end
