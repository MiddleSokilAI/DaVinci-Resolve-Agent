-- Fixed SDK adapter. Requests are JSON data, never source or dynamic chunks.
-- UI snapshots are transport envelopes only: no preset is loaded or applied.
return function(api, root, session, decode_json, policy, read_request, project_changed)
    local null=decode_json('null')
    local function array(value)
        return setmetatable(value or {}, {__sdk_json_array=true})
    end
    local function check(value, reason) if not value then error(reason,0) end end
    local function quote(value)
        return '"'..value:gsub('[%z\1-\31\\"]',function(c)
            local names={['"']='\\"',['\\']='\\\\',['\n']='\\n',['\r']='\\r',['\t']='\\t'}
            return names[c] or string.format('\\u%04x',c:byte())
        end)..'"'
    end
    local function json(value, depth)
        depth=depth or 0;check(depth<=32,'SDK_JSON_DEPTH')
        if value==nil or value==null then return 'null' end
        local kind=type(value)
        if kind=='boolean' then return value and 'true' or 'false' end
        if kind=='number' then
            check(value==value and value~=math.huge and value~=-math.huge,'SDK_NONFINITE')
            return string.format('%.17g',value)
        end
        if kind=='string' then return quote(value) end
        check(kind=='table','SDK_JSON_TYPE')
        local parts={}
        if getmetatable(value) and getmetatable(value).__sdk_json_array then
            for _,item in ipairs(value) do parts[#parts+1]=json(item,depth+1) end
            return '['..table.concat(parts,',')..']'
        end
        local keys={}
        for key in pairs(value) do check(type(key)=='string','SDK_JSON_KEY');keys[#keys+1]=key end
        table.sort(keys)
        for _,key in ipairs(keys) do parts[#parts+1]=quote(key)..':'..json(value[key],depth+1) end
        return '{'..table.concat(parts,',')..'}'
    end
    local function hex(value)
        return (value:gsub('.',function(c) return string.format('%02x',c:byte()) end))
    end
    local function unhex(value)
        check(#value%2==0 and value:match('^[0-9a-f]+$'),'SDK_HEX')
        return (value:gsub('..',function(pair) return string.char(tonumber(pair,16)) end))
    end
    local function token(value)
        return type(value)=='string' and #value==32 and value:match('^[0-9a-f]+$')
    end
    local methods,constants={},{}
    for _,name in ipairs(policy.methods) do methods[name]=true end
    for _,name in ipairs(policy.constants) do constants[name]=true end
    local mutations={}
    for _,name in ipairs(policy.mutations) do mutations[name]=true end
    local invalidates={OpenPage=true}
    for name in pairs(mutations) do invalidates[name]=true end
    local list_results={}
    for _,name in ipairs({
        'GetVersion','GetProjectListInCurrentFolder','GetItemListInTrack','GetClipList',
        'GetSubFolderList','GetLinkedItems','GetRenderJobList','GetRenderPresetList',
        'GetRenderResolutions','GetVersionNameList','AppendToTimeline','ImportMedia',
    }) do list_results[name]=true end
    local result_kinds={
        GetProjectManager='ProjectManager',GetCurrentProject='Project',CreateProject='Project',
        GetCurrentTimeline='Timeline',GetTimelineByIndex='Timeline',DuplicateTimeline='Timeline',
        CreateEmptyTimeline='Timeline',GetMediaPool='MediaPool',GetRootFolder='Folder',
        GetSubFolderList='Folder',GetClipList='MediaPoolItem',ImportMedia='MediaPoolItem',
        GetMediaPoolItem='MediaPoolItem',GetItemListInTrack='TimelineItem',
        GetLinkedItems='TimelineItem',AppendToTimeline='TimelineItem',
        InsertTitleIntoTimeline='TimelineItem',InsertFusionTitleIntoTimeline='TimelineItem',
        GetNodeGraph='Graph',
    }
    local prefetch={
        Resolve={{'GetProductName'},{'GetVersionString'},{'GetVersion'},
            {'GetCurrentPage'},{'GetProjectManager'}},
        ProjectManager={{'GetCurrentProject'}},
        Project={{'GetName'},{'GetUniqueId'},{'GetTimelineCount'},
            {'GetCurrentTimeline'},{'GetMediaPool'},{'GetSetting'}},
        Timeline={{'GetName'},{'GetUniqueId'},{'GetStartFrame'},{'GetEndFrame'},
            {'GetTrackCount','video'},{'GetTrackCount','audio'},{'GetTrackCount','subtitle'},
            {'GetSetting'}},
        TimelineItem={{'GetName'},{'GetUniqueId'},{'GetStart'},{'GetEnd'},{'GetDuration'},
            {'GetStart',false},{'GetEnd',false},{'GetDuration',false},
            {'GetSourceStartFrame'},{'GetSourceEndFrame'},{'GetClipEnabled'},
            {'GetTrackTypeAndIndex'},{'GetProperty'},{'GetMediaPoolItem'},
            {'GetLinkedItems'},{'GetFusionCompCount'}},
        MediaPoolItem={{'GetName'},{'GetMediaId'},{'GetClipProperty','File Path'}},
        Folder={{'GetName'},{'GetUniqueId'}},
        Graph={{'GetNumNodes'}},
    }
    local operation,objects,identities,descriptors,serial,readset
    local completed={}
    local pack,register
    local function sdk_function(object, name)
        local ok,value=pcall(function() return object[name] end)
        if ok and type(value)=='function' then return value end
        return nil
    end
    local function stable_key(object,kind,origin)
        if kind=='Resolve' then return 'resolve' end
        if kind=='ProjectManager' then return 'project_manager' end
        if kind=='Project' or kind=='Timeline' or kind=='TimelineItem' or kind=='Folder'
            or kind=='MediaPoolItem' then
            local name=kind=='MediaPoolItem' and 'GetMediaId' or 'GetUniqueId'
            local f=sdk_function(object,name)
            if f then
                local ok,id=pcall(f,object)
                if ok and type(id)=='string' and id~='' then return kind..':'..id end
            end
        end
        return kind..':'..origin
    end
    local function decode(value,depth)
        depth=depth or 0;check(depth<=32,'SDK_ARGUMENT_DEPTH')
        if value==null then return nil end
        if type(value)~='table' then
            check(type(value)=='string' or type(value)=='boolean' or type(value)=='number','SDK_ARGUMENT')
            return value
        end
        local count=0;for _ in pairs(value) do count=count+1 end
        check(count==1,'SDK_ARGUMENT_TAG')
        if value.r~=nil then
            check(type(value.r)=='string' and objects[value.r]~=nil,'SDK_REFERENCE')
            return objects[value.r].value
        end
        if value.a~=nil then
            check(type(value.a)=='table','SDK_ARGUMENT_ARRAY')
            local result=setmetatable({}, {__sdk_arg_length=#value.a})
            for index,item in ipairs(value.a) do result[index]=decode(item,depth+1) end
            return result
        end
        check(type(value.m)=='table','SDK_ARGUMENT_MAP')
        local result,seen={},{}
        for _,pair in ipairs(value.m) do
            check(type(pair)=='table' and #pair==2,'SDK_ARGUMENT_PAIR')
            local key=decode(pair[1],depth+1)
            check((type(key)=='string' or type(key)=='number' or type(key)=='boolean')
                and not seen[key],'SDK_ARGUMENT_KEY')
            seen[key]=true;result[key]=decode(pair[2],depth+1)
        end
        return result
    end
    local function normalize_wire(value)
        if type(value)~='table' or value==null then return value end
        if value.a then
            array(value.a)
            for _,item in ipairs(value.a) do normalize_wire(item) end
        elseif value.m then
            array(value.m)
            for _,pair in ipairs(value.m) do
                array(pair);normalize_wire(pair[1]);normalize_wire(pair[2])
            end
        end
        return value
    end
    local function remember(target,name,args,result,wire_args)
        readset[#readset+1]={target=target,name=name,args=args,result=result,wire_args=wire_args}
    end
    pack=function(value,kind,origin,as_list)
        if value==nil or value==null then return null end
        if type(value)=='userdata' then return {r=register(value,kind or 'Object',origin)} end
        if type(value)~='table' then
            check(type(value)=='string' or type(value)=='boolean' or type(value)=='number','SDK_RESULT')
            return value
        end
        if getmetatable(value) and sdk_function(value,'GetName') then
            return {r=register(value,kind or 'Object',origin)}
        end
        -- FusionScript marks tuple returns (for example track type/index).
        -- Python exposes them as sequences rather than dictionaries.
        if as_list or value.__flags==4194304 then
            local values=array()
            for index,item in ipairs(value) do values[index]=pack(item,kind,origin..'/'..index) end
            return {a=values}
        end
        local values=array()
        for key,item in pairs(value) do
            check(type(key)=='string' or type(key)=='number' or type(key)=='boolean','SDK_RESULT_KEY')
            values[#values+1]=array({key,pack(item,kind,origin..'/'..tostring(key))})
        end
        table.sort(values,function(a,b) return json(a[1])<json(b[1]) end)
        return {m=values}
    end
    register=function(value,kind,origin)
        local key=stable_key(value,kind,origin)
        local identity=identities[key]
        if not identity then serial=serial+1;identity='o'..serial;identities[key]=identity end
        objects[identity]={value=value,kind=kind,origin=origin}
        if descriptors[identity] then return identity end
        local definition={kind=kind,methods=array(),properties={},cache=array()}
        descriptors[identity]=definition -- Break cycles before following references.
        for _,name in ipairs(policy.methods) do
            if sdk_function(value,name) then definition.methods[#definition.methods+1]=name end
        end
        if kind=='Resolve' then
            for _,name in ipairs(policy.constants) do
                local ok,result=pcall(function() return value[name] end)
                if ok and (type(result)=='number' or type(result)=='string') then
                    definition.properties[name]=result
                end
            end
        end
        for _,call in ipairs(prefetch[kind] or {}) do
            local name=call[1]
            local f=sdk_function(value,name)
            if f then
                local args={};for index=2,#call do args[index-1]=call[index] end
                local ok,result=pcall(f,value,unpack(args))
                if ok then
                    local wire_args={a=array(args)}
                    local encoded=pack(result,result_kinds[name],identity..'.'..name..json(wire_args),list_results[name])
                    definition.cache[#definition.cache+1]={method=name,arguments=wire_args,result=encoded}
                    remember(identity,name,args,encoded,wire_args)
                end
            end
        end
        if kind=='Project' then
            local count=value:GetTimelineCount()
            if type(count)=='number' and count<=128 then
                for index=1,count do
                    local result=pack(value:GetTimelineByIndex(index),'Timeline',identity..'.timeline'..index)
                    definition.cache[#definition.cache+1]={method='GetTimelineByIndex',arguments={a=array({index})},result=result}
                    remember(identity,'GetTimelineByIndex',{index},result,{a=array({index})})
                end
            end
        end
        return identity
    end
    local function current_project_id()
        local p=api:GetProjectManager():GetCurrentProject()
        return p and p:GetUniqueId() or ''
    end
    local function authorize(name)
        if not mutations[name] then return end
        local action=operation.action
        local permitted=policy.write_methods[action]
        check(type(permitted)=='table' and operation.create_backup==true,'SDK_WRITE_NOT_AUTHORIZED')
        local allowed=false
        for _,method in ipairs(permitted) do if method==name then allowed=true end end
        for _,method in ipairs(policy.common_write_methods) do if method==name then allowed=true end end
        check(allowed,'SDK_METHOD_OUTSIDE_ACTION')
        if name~='SaveProject' then
            check(operation.backup_ready==true,'SDK_NATIVE_BACKUP_REQUIRED')
        end
        if name=='DeleteClips' then check(operation.allow_destructive==true,'SDK_DESTRUCTIVE_DISABLED') end
        check(os.time()<=operation.expires,'SDK_OPERATION_EXPIRED')
    end
    local function assert_readset()
        -- Resolve may process UI events between packets. Recheck observed values
        -- immediately before a write instead of trusting the host's read cache.
        local previous=readset;readset={}
        for _,item in ipairs(previous) do
            local object=objects[item.target]
            check(object~=nil,'SDK_READSET_REFERENCE')
            local f=sdk_function(object.value,item.name)
            check(f~=nil,'SDK_READSET_METHOD')
            local result=f(object.value,unpack(item.args))
            local encoded=pack(result,result_kinds[item.name],item.target..'.'..item.name..json(item.wire_args),list_results[item.name])
            check(json(encoded)==json(item.result),'SDK_CONCURRENT_CHANGE')
        end
        readset={}
    end
    local function perform(request)
        local context=request.operation
        check(type(context)=='table' and token(context.id) and type(context.action)=='string'
            and type(context.expires)=='number' and type(context.fingerprint)=='string'
            and #context.fingerprint==64,'SDK_OPERATION')
        if not operation or operation.id~=context.id then
            operation=context;objects={};identities={};serial=0;readset={}
            operation.project_id=current_project_id()
        else
            check(operation.action==context.action and operation.fingerprint==context.fingerprint
                and operation.expires==context.expires and operation.create_backup==context.create_backup
                and operation.allow_destructive==context.allow_destructive,'SDK_OPERATION_CHANGED')
        end
        check(current_project_id()==operation.project_id,'SDK_PROJECT_CHANGED')
        descriptors={}
        local call=request.call
        check(type(call)=='table','SDK_CALL')
        if call.kind=='root' then return {r=register(api,'Resolve','resolve')} end
        check(call.kind=='call' and type(call.object)=='string' and objects[call.object]
            and methods[call.method],'SDK_CALL_NOT_ALLOWED')
        local target=objects[call.object]
        local f=sdk_function(target.value,call.method)
        check(f~=nil,'SDK_METHOD_UNAVAILABLE')
        normalize_wire(call.arguments)
        local args=decode(call.arguments)
        local count=type(args)=='table' and getmetatable(args) and getmetatable(args).__sdk_arg_length
        check(type(count)=='number','SDK_ARGUMENTS')
        if call.method=='ExportProject' then
            check(policy.write_methods[operation.action]~=nil and operation.create_backup==true,
                'SDK_BACKUP_NOT_AUTHORIZED')
            check(type(args[2])=='string','SDK_BACKUP_PATH')
            local path=args[2]:gsub('\\','/'):lower()
            for segment in (path..'/'):gmatch('([^/]+)/') do
                check(segment~='.' and segment~='..','SDK_BACKUP_PATH')
            end
            local permitted=false
            for _,base in ipairs(policy.backup_roots or {}) do
                base=base:gsub('\\','/'):lower()
                if path:sub(1,#base+1)==base..'/' then permitted=true end
            end
            check(permitted and path:sub(-4)=='.drp','SDK_BACKUP_PATH')
        end
        authorize(call.method)
        if invalidates[call.method] then assert_readset();descriptors={} end
        local result=f(target.value,unpack(args,1,count))
        if call.method=='ExportProject' and result==true then operation.backup_ready=true end
        if call.method=='CreateProject' and result~=nil then
            operation.project_id=current_project_id()
            if project_changed then project_changed(operation.project_id) end
        end
        local encoded=pack(result,result_kinds[call.method],call.object..'.'..call.method..json(call.arguments),list_results[call.method])
        if invalidates[call.method] then
            register(api,'Resolve','resolve') -- Fresh graph for subsequent guards/readback.
        else
            remember(call.object,call.method,args,encoded,call.arguments)
        end
        return encoded
    end
    local function export(name,path,ready)
        local owned=false
        for _,existing in ipairs(api:GetLayoutPresetList() or {}) do check(existing~=name,'SDK_PRESET_EXISTS') end
        local ok,reason=pcall(function()
            check(api:SaveLayoutPreset(name)==true,'SDK_PRESET_SAVE');owned=true
            check(api:ExportLayoutPreset(name,path)==true,'SDK_PRESET_EXPORT')
            if ready then check(api:ExportLayoutPreset(name,ready)==true,'SDK_PRESET_READY') end
        end)
        if owned then check(api:DeleteLayoutPreset(name)==true,'SDK_PRESET_CLEANUP') end
        check(ok,reason)
    end
    local prefix='DRA_RPC1_'..session..'_'
    local template=root..'/sdk-template.preset'
    export('DRA_TEMPLATE_'..session,template)
    local packets,messages={},{}
    local last_id
    local function poll()
        local message=read_request()
        if message==nil then return end
        check(message.action=='ping' and type(message.arguments)=='table','SDK_PACKET_ENVELOPE')
        local part=message.arguments.sdk
        check(type(part)=='table' and token(part.id) and type(part.data)=='string'
            and #part.data<=2000 and type(part.seq)=='number' and type(part.count)=='number'
            and part.seq%1==0 and part.count%1==0 and part.seq>=1 and part.seq<=part.count
            and part.count<=1024,'SDK_PACKET')
        local id=part.id
        check(not completed[id] or not completed[id].retired,'SDK_RETIRED_REQUEST')
        if last_id and last_id~=id then
            if completed[last_id] then completed[last_id]={retired=true} end
            packets={};messages={}
        end
        last_id=id
        local packet=packets[id]
        if not packet then packet={next=1,count=part.count,parts={}};packets[id]=packet end
        check(packet.count==part.count,'SDK_PACKET_CHANGED')
        if not messages[message.id] then
            check(part.seq==packet.next,'SDK_PACKET_ORDER')
            packet.parts[part.seq]=part.data;packet.next=packet.next+1
            messages[message.id]={id=id,seq=part.seq,data=part.data}
        else
            local seen=messages[message.id]
            check(seen.id==id and seen.seq==part.seq and seen.data==part.data,'SDK_MESSAGE_CHANGED')
        end
        if part.seq<part.count then
            local ack=root..'/sdk-ack-'..message.id..'.preset'
            if not bmd.fileexists(ack) then export('DRA_SDK_ACK_'..session..'_'..message.id,ack) end
            return
        end
        local bytes=table.concat(packet.parts)
        check(#bytes<=1048576,'SDK_REQUEST_SIZE')
        local source=hex(bytes)
        local record=completed[id]
        local reply
        if record then
            check(record.source==source,'SDK_REQUEST_CHANGED')
            if bmd.fileexists(root..'/sdk-'..id..'.ready.preset') then return end
            reply=record.reply
        else
            local request=decode_json(unhex(source))
            local ok,result=pcall(perform,request)
            local output_result=null
            if ok then output_result=result end
            reply={result=output_result,objects=ok and descriptors or {},
                error=ok and null or tostring(result),request_hex=source}
            completed[id]={source=source,reply=reply}
        end
        local output=prefix..id..'_O_'..hex(json(reply))
        check(#output<=2098176,'SDK_RESPONSE_SIZE')
        export(output,root..'/sdk-'..id..'.preset',root..'/sdk-'..id..'.ready.preset')
    end
    return {poll=poll}
end
