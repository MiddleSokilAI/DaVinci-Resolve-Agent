-- Fixed editing operations. Never evaluates caller-provided Lua code.
return function(api, root, media_roots, shared)
    local function check(condition, code)
        if not condition then error(code, 0) end
    end
    local function text(value)
        -- C locale classes can mistake UTF-8 bytes for controls on Windows.
        return type(value) == "string" and #value > 0 and #value <= 512 and not value:find("[%z\1-\31\127]")
    end
    local function normalized(path)
        return path:gsub("\\", "/"):lower():gsub("/+$", "")
    end
    local function allowed(path)
        if not text(path) or path:find("/%.%./") or path:find("\\%.%.\\") then return false end
        for _, directory in ipairs(media_roots) do
            local prefix = normalized(directory) .. "/"
            if normalized(path):sub(1, #prefix) == prefix then return true end
        end
        return false
    end
    local function timelines(project, name)
        local found = nil
        for i=1,project:GetTimelineCount() do
            local timeline = project:GetTimelineByIndex(i)
            if timeline:GetName() == name then
                check(found == nil, "AMBIGUOUS_TIMELINE")
                found = timeline
            end
        end
        return found
    end
    local function assets(pool)
        local result, visited = {}, 0
        local function walk(folder, depth)
            visited = visited + 1
            check(depth <= 32 and visited <= 1000, "POOL_TOO_LARGE")
            for _, clip in ipairs(folder:GetClipList() or {}) do
                check(#result < 10000, "POOL_TOO_LARGE")
                result[#result+1] = clip
            end
            for _, child in ipairs(folder:GetSubFolderList() or {}) do walk(child, depth+1) end
        end
        walk(pool:GetRootFolder(), 0)
        return result
    end
    local function find_asset(pool, path)
        local found = nil
        for _, item in ipairs(assets(pool)) do
            local file = item:GetClipProperty("File Path")
            if type(file) == "string" and normalized(file) == normalized(path) then
                check(found == nil, "AMBIGUOUS_ASSET")
                found = item
            end
        end
        return found
    end
    local function item_ids(timeline)
        local ids, count = {}, 0
        for _, kind in ipairs({"video", "audio"}) do
            for track=1,timeline:GetTrackCount(kind) do
                for _, item in ipairs(timeline:GetItemListInTrack(kind, track) or {}) do
                    ids[item:GetUniqueId()] = true
                    count = count + 1
                    check(count <= 10000, "TIMELINE_TOO_LARGE")
                end
            end
        end
        return ids
    end
    -- Separate identity from layout: a duplicate must preserve the layout
    -- while owning different editable timeline items. No source is cleared.
    local function layout(timeline,project)
        -- Track enable getters return false for inactive timelines on 21.1.
        check(project:SetCurrentTimeline(timeline)==true,"TIMELINE_SELECT_FAILED")
        local parts, ids, count = {tostring(timeline:GetSetting("timelineFrameRate")),
            tostring(timeline:GetStartFrame()),tostring(timeline:GetEndFrame())}, {}, 0
        for _,kind in ipairs({"video","audio","subtitle"}) do
            parts[#parts+1]=kind .. ":" .. timeline:GetTrackCount(kind)
            for track=1,timeline:GetTrackCount(kind) do
                parts[#parts+1]=tostring(timeline:GetIsTrackEnabled(kind,track))
                for _,item in ipairs(timeline:GetItemListInTrack(kind,track) or {}) do
                    count=count+1
                    check(count<=10000,"TIMELINE_TOO_LARGE")
                    ids[item:GetUniqueId()]=true
                    local media=item:GetMediaPoolItem()
                    parts[#parts+1]=table.concat({kind,track,item:GetStart(),item:GetEnd(),
                        item:GetSourceStartFrame(),item:GetSourceEndFrame(),
                        media and media:GetUniqueId() or "no-media",
                        #(item:GetLinkedItems() or {}),item:GetFusionCompCount(),
                        tostring(item:GetClipEnabled()),
                        kind=="audio" and tostring(item:GetProperty("AudioVolume")) or ""},":")
                end
            end
        end
        return table.concat(parts,"|"),ids
    end
    local finish = dofile(root .. "/finishing.lua")(api, root, {
        check=check, timelines=timelines, allowed=allowed, normalized=normalized,
        find_asset=find_asset}, shared)
    local sync = dofile(root .. "/sync.lua")({check=check, timelines=timelines,
        allowed=allowed, find_asset=find_asset, normalized=normalized})
    return function(pm, project, request)
        if request.action == "get_render_status" then return finish.status(project, request) end
        if request.action == "get_timeline_summary" then return finish.summary(project, request) end
        check(request.confirm == true and text(request.project_id), "INVALID_ARGUMENTS")
        check(project:GetUniqueId() == request.project_id, "PROJECT_CHANGED")
        check(not project:IsRenderingInProgress(), "RENDERING")
        local a, pool = request.arguments, project:GetMediaPool()
        check(type(a) == "table", "INVALID_ARGUMENTS")
        local timeline, asset, before, finishing, duplicate_source, source_layout, source_ids
        if request.action == "create_timeline" or request.action == "duplicate_timeline" then
            check(text(a.name), "INVALID_ARGUMENTS")
            check(timelines(project, a.name) == nil, "NAME_EXISTS")
            check(a.frame_rate == nil, "INVALID_ARGUMENTS")
            if request.action == "duplicate_timeline" then
                check(text(a.source_timeline_name) and a.source_timeline_name~=a.name,"INVALID_ARGUMENTS")
                duplicate_source=timelines(project,a.source_timeline_name)
                check(duplicate_source~=nil,"TIMELINE_NOT_FOUND")
                check(type(duplicate_source.DuplicateTimeline)=="function","DUPLICATE_UNAVAILABLE")
                source_layout,source_ids=layout(duplicate_source,project)
            else
                check(a.source_timeline_name==nil,"INVALID_ARGUMENTS")
            end
        elseif request.action == "import_media" then
            check(type(a.paths) == "table" and #a.paths >= 1 and #a.paths <= 20, "INVALID_ARGUMENTS")
            local seen = {}
            for _, path in ipairs(a.paths) do
                check(allowed(path), "MEDIA_NOT_ALLOWED")
                check(not seen[normalized(path)], "INVALID_ARGUMENTS")
                seen[normalized(path)] = true
                check(find_asset(pool, path) == nil, "ASSET_EXISTS")
            end
        elseif request.action == "append_clip" and a.sync_groups then
            finishing = sync(project, a)
        elseif request.action == "append_clip" then
            check(text(a.timeline_name) and allowed(a.media_path), "INVALID_ARGUMENTS")
            timeline = timelines(project, a.timeline_name)
            check(timeline ~= nil, "TIMELINE_NOT_FOUND")
            asset = find_asset(pool, a.media_path)
            check(asset ~= nil, "ASSET_NOT_FOUND")
            check((a.start_frame == nil) == (a.end_frame == nil), "INVALID_RANGE")
            if a.start_frame ~= nil then
                local frames = tonumber(asset:GetClipProperty("Frames"))
                check(type(a.start_frame) == "number" and type(a.end_frame) == "number"
                    and a.start_frame == math.floor(a.start_frame) and a.end_frame == math.floor(a.end_frame)
                    and a.start_frame >= 0 and a.end_frame >= a.start_frame
                    and frames ~= nil and a.end_frame < frames, "INVALID_RANGE")
            end
            before = item_ids(timeline)
        else
            finishing = finish.preflight(project, request)
        end
        -- No editing API call may precede these preflight checks and backup.
        check(pm:SaveProject() == true, "BACKUP_SAVE_FAILED")
        check(pm:ExportProject(project:GetName(), root .. "/" .. request.id .. ".before.drp", false) == true, "BACKUP_EXPORT_FAILED")
        check(pm:GetCurrentProject():GetUniqueId() == request.project_id, "PROJECT_CHANGED")
        check(request.expires >= os.time(), "EXPIRED")
        local token
        if finishing then
            token = finishing()
        elseif request.action == "create_timeline" or request.action == "duplicate_timeline" then
            local count = project:GetTimelineCount()
            if duplicate_source then
                timeline=duplicate_source:DuplicateTimeline(a.name)
            else
                timeline=pool:CreateEmptyTimeline(a.name)
            end
            check(timeline ~= nil and timeline:GetName() == a.name, "VERIFY_FAILED")
            check(project:GetTimelineCount() == count+1, "VERIFY_FAILED")
            check(timelines(project, a.name):GetUniqueId() == timeline:GetUniqueId(), "VERIFY_FAILED")
            if duplicate_source then
                check(timeline:GetUniqueId()~=duplicate_source:GetUniqueId(),"VERIFY_FAILED")
                local copied,copied_ids=layout(timeline,project)
                local original,original_ids=layout(duplicate_source,project)
                check(copied==source_layout and original==source_layout,"VERIFY_FAILED")
                for id in pairs(copied_ids) do check(not source_ids[id],"VERIFY_FAILED") end
                for id in pairs(source_ids) do check(original_ids[id],"VERIFY_FAILED") end
                for id in pairs(original_ids) do check(source_ids[id],"VERIFY_FAILED") end
                check(project:SetCurrentTimeline(timeline)==true,"TIMELINE_SELECT_FAILED")
            end
        elseif request.action == "import_media" then
            local imported = pool:ImportMedia(a.paths)
            check(type(imported) == "table" and #imported == #a.paths, "VERIFY_FAILED")
            for _, path in ipairs(a.paths) do check(find_asset(pool, path) ~= nil, "VERIFY_FAILED") end
        elseif request.action == "append_clip" then
            check(project:SetCurrentTimeline(timeline) == true, "VERIFY_FAILED")
            check(project:GetCurrentTimeline():GetUniqueId() == timeline:GetUniqueId(), "VERIFY_FAILED")
            local clips
            local record_frame = timeline:GetStartFrame()
            for _,kind in ipairs({"video", "audio"}) do
                for track=1,timeline:GetTrackCount(kind) do
                    for _,item in ipairs(timeline:GetItemListInTrack(kind, track) or {}) do
                        record_frame = math.max(record_frame, item:GetEnd())
                    end
                end
            end
            if a.start_frame ~= nil then
                clips = pool:AppendToTimeline({{mediaPoolItem=asset, startFrame=a.start_frame, endFrame=a.end_frame, recordFrame=record_frame}})
            else
                clips = pool:AppendToTimeline({{mediaPoolItem=asset, recordFrame=record_frame}})
            end
            check(type(clips) == "table" and #clips >= 1, "VERIFY_FAILED")
            local after = item_ids(timeline)
            for _, clip in ipairs(clips) do
                local id = clip:GetUniqueId()
                check(after[id] == true and not before[id], "VERIFY_FAILED")
                check(clip:GetMediaPoolItem():GetUniqueId() == asset:GetUniqueId(), "VERIFY_FAILED")
            end
        end
        if request.action ~= "start_render" then
            check(pm:SaveProject() == true, "POST_SAVE_FAILED")
        end
        return token
    end
end
