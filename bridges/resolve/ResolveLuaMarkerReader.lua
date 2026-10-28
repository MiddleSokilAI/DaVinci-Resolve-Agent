-- Fixed data-only packet reader for the experimental Free marker transport.
-- No incoming bytes are evaluated as Lua. The caller supplies trusted runtime
-- metadata; the host atomically publishes one immutable inbox directory.
return function(root, session, max_payload, lease_seconds)
    assert(type(bmd) == "table" and type(bmd.fileexists) == "function", "MARKER_FILEEXISTS_UNAVAILABLE")
    assert(type(bmd.wait) == "function", "MARKER_WAIT_UNAVAILABLE")
    assert(type(os) == "table" and type(os.time) == "function", "MARKER_CLOCK_UNAVAILABLE")
    assert(type(root) == "string" and root ~= "", "MARKER_ROOT_INVALID")
    assert(type(session) == "string" and #session == 32 and session:match("^[0-9a-f]+$"), "MARKER_SESSION_INVALID")
    assert(type(max_payload) == "number" and max_payload >= 1 and max_payload <= 4096 and max_payload % 1 == 0, "MARKER_LIMIT_INVALID")
    assert(type(lease_seconds) == "number" and lease_seconds >= 5 and lease_seconds <= 3600 and lease_seconds % 1 == 0, "MARKER_LEASE_INVALID")
    local null = {}
    local actions = {
        ping=true, get_current_project=true, stop=true, get_render_status=true,
        get_timeline_summary=true, quit_resolve=true, import_media=true,
        create_timeline=true, duplicate_timeline=true, append_clip=true,
        set_clip_properties=true, add_subtitles=true, prepare_render=true,
        start_render=true,
    }
    local writes = {
        quit_resolve=true, import_media=true, create_timeline=true,
        duplicate_timeline=true, append_clip=true, set_clip_properties=true,
        add_subtitles=true, prepare_render=true, start_render=true,
    }
    local envelope = {session=true, id=true, action=true, expires=true,
        confirm=true, project_id=true, arguments=true}
    local last_error, lease_poll = nil, -1
    local renewed = {}
    local deadline = os.time() + lease_seconds

    local function fail(reason) error(reason, 0) end
    local function byte_string(cp)
        if cp < 128 then return string.char(cp) end
        if cp < 2048 then return string.char(192 + math.floor(cp/64), 128 + cp%64) end
        if cp < 65536 then
            return string.char(224 + math.floor(cp/4096), 128 + math.floor(cp/64)%64, 128 + cp%64)
        end
        return string.char(240 + math.floor(cp/262144), 128 + math.floor(cp/4096)%64,
            128 + math.floor(cp/64)%64, 128 + cp%64)
    end

    local function decode_json(source)
        local pos, tokens = 1, 0
        local size = #source
        local function white()
            while pos <= size and source:sub(pos,pos):match("[ \t\r\n]") do pos=pos+1 end
        end
        local function hex4()
            local text=source:sub(pos,pos+3)
            if #text~=4 or not text:match("^[0-9a-fA-F]+$") then fail("MARKER_JSON_UNICODE") end
            pos=pos+4
            return tonumber(text,16)
        end
        local function text()
            assert(source:sub(pos,pos)=='"', "MARKER_JSON_STRING")
            pos=pos+1
            local parts={}
            while pos<=size do
                local at, char=pos, source:byte(pos)
                if char==34 then pos=pos+1; return table.concat(parts) end
                if char<32 then fail("MARKER_JSON_CONTROL") end
                if char==92 then
                    pos=pos+1
                    local escape=source:sub(pos,pos)
                    local map={['"']='"', ['\\']='\\', ['/']='/', b='\b', f='\f', n='\n', r='\r', t='\t'}
                    if map[escape] then parts[#parts+1]=map[escape]; pos=pos+1
                    elseif escape=='u' then
                        pos=pos+1
                        local cp=hex4()
                        if cp>=55296 and cp<=56319 then
                            if source:sub(pos,pos+1)~='\\u' then fail("MARKER_JSON_SURROGATE") end
                            pos=pos+2
                            local low=hex4()
                            if low<56320 or low>57343 then fail("MARKER_JSON_SURROGATE") end
                            cp=65536+(cp-55296)*1024+low-56320
                        elseif cp>=56320 and cp<=57343 then fail("MARKER_JSON_SURROGATE") end
                        parts[#parts+1]=byte_string(cp)
                    else fail("MARKER_JSON_ESCAPE") end
                elseif char>=128 then
                    local count, second=0, source:byte(pos+1)
                    if char>=194 and char<=223 then count=2
                    elseif char>=224 and char<=239 then count=3
                    elseif char>=240 and char<=244 then count=4
                    else fail("MARKER_JSON_UTF8") end
                    if not second or (char==224 and second<160) or (char==237 and second>159)
                        or (char==240 and second<144) or (char==244 and second>143) then fail("MARKER_JSON_UTF8") end
                    for offset=1,count-1 do
                        local following=source:byte(pos+offset)
                        if not following or following<128 or following>191 then fail("MARKER_JSON_UTF8") end
                    end
                    parts[#parts+1]=source:sub(at,at+count-1); pos=pos+count
                else parts[#parts+1]=string.char(char); pos=pos+1 end
            end
            fail("MARKER_JSON_UNTERMINATED")
        end
        local value
        value=function(depth)
            tokens=tokens+1
            if depth>32 or tokens>8192 then fail("MARKER_JSON_COMPLEXITY") end
            white()
            local char=source:sub(pos,pos)
            if char=='"' then return text() end
            if char=='{' then
                pos=pos+1; white()
                local object, seen={}, {}
                if source:sub(pos,pos)=='}' then pos=pos+1; return object end
                while true do
                    white()
                    if source:sub(pos,pos)~='"' then fail("MARKER_JSON_KEY") end
                    local key=text()
                    if seen[key] then fail("MARKER_JSON_DUPLICATE") end
                    seen[key]=true; white()
                    if source:sub(pos,pos)~=':' then fail("MARKER_JSON_COLON") end
                    pos=pos+1; object[key]=value(depth+1); white()
                    local separator=source:sub(pos,pos); pos=pos+1
                    if separator=='}' then return object end
                    if separator~=',' then fail("MARKER_JSON_OBJECT") end
                end
            end
            if char=='[' then
                pos=pos+1; white()
                local array={}
                if source:sub(pos,pos)==']' then pos=pos+1; return array end
                while true do
                    local item=value(depth+1)
                    if item==null then fail("MARKER_JSON_NULL_ARRAY") end
                    array[#array+1]=item; white()
                    local separator=source:sub(pos,pos); pos=pos+1
                    if separator==']' then return array end
                    if separator~=',' then fail("MARKER_JSON_ARRAY") end
                end
            end
            for literal, result in pairs({['true']=true, ['false']=false, ['null']=null}) do
                if source:sub(pos,pos+#literal-1)==literal then pos=pos+#literal; return result end
            end
            local start=pos
            if char=='-' then pos=pos+1 end
            local first=source:sub(pos,pos)
            if first=='0' then pos=pos+1
            elseif first:match('^[1-9]$') then
                repeat pos=pos+1 until not source:sub(pos,pos):match('^[0-9]$')
            else fail("MARKER_JSON_VALUE") end
            if source:sub(pos,pos)=='.' then
                pos=pos+1
                if not source:sub(pos,pos):match('^[0-9]$') then fail("MARKER_JSON_NUMBER") end
                repeat pos=pos+1 until not source:sub(pos,pos):match('^[0-9]$')
            end
            if source:sub(pos,pos):match('^[eE]$') then
                pos=pos+1
                if source:sub(pos,pos):match('^[+-]$') then pos=pos+1 end
                if not source:sub(pos,pos):match('^[0-9]$') then fail("MARKER_JSON_NUMBER") end
                repeat pos=pos+1 until not source:sub(pos,pos):match('^[0-9]$')
            end
            local number=tonumber(source:sub(start,pos-1))
            if not number or number~=number or number==math.huge or number==-math.huge then fail("MARKER_JSON_NUMBER") end
            return number
        end
        local result=value(0); white()
        if pos~=size+1 then fail("MARKER_JSON_TRAILING") end
        return result
    end

    local function adler(source)
        local a,b=1,0
        for index=1,#source do a=(a+source:byte(index))%65521; b=(b+a)%65521 end
        return b*65536+a
    end
    local function read_byte(base, index)
        local byte=0
        for bit=0,7 do
            if bmd.fileexists(base..string.format('/b%05d_%d.bit',index,bit)) then byte=byte+2^bit end
        end
        return byte
    end
    local function bytes(base, start, count)
        local result={}
        for index=start,start+count-1 do result[#result+1]=string.char(read_byte(base,index)) end
        return table.concat(result)
    end
    local function uint32(source,offset)
        return source:byte(offset)+source:byte(offset+1)*256+source:byte(offset+2)*65536+source:byte(offset+3)*16777216
    end
    local function read_packet()
        local base=root..'/inbox'
        if not bmd.fileexists(base..'/ready.marker') then return nil end
        local header=bytes(base,0,8)
        if header:byte(1)~=77 or header:byte(2)~=1 then fail("MARKER_HEADER") end
        local length=header:byte(3)+header:byte(4)*256
        if length<1 or length>max_payload then fail("MARKER_LENGTH") end
        local payload=bytes(base,8,length)
        if adler(payload)~=uint32(header,5) then fail("MARKER_CHECKSUM") end
        if not bmd.fileexists(base..'/ready.marker') or bytes(base,0,8)~=header then fail("MARKER_CHANGED") end
        return decode_json(payload)
    end
    local function strip_nulls(value)
        if type(value)~='table' or value==null then return value end
        for key,item in pairs(value) do
            if item==null then value[key]=nil else strip_nulls(item) end
        end
        return value
    end
    local function valid_request(request, now)
        if type(request)~='table' or request==null then fail("MARKER_ENVELOPE") end
        for key in pairs(request) do if not envelope[key] then fail("MARKER_FIELD") end end
        if request.session~=session then fail("MARKER_SESSION") end
        if type(request.id)~='string' or #request.id~=32 or not request.id:match('^[0-9a-f]+$') then fail("MARKER_ID") end
        if type(request.action)~='string' or not actions[request.action] then fail("MARKER_ACTION") end
        if type(request.expires)~='number' or request.expires%1~=0
            or request.expires<now or request.expires>now+3600 then fail("MARKER_EXPIRED") end
        if request.project_id~=nil and (type(request.project_id)~='string' or #request.project_id<1 or #request.project_id>128) then fail("MARKER_PROJECT") end
        if request.arguments~=nil and (type(request.arguments)~='table' or request.arguments==null) then fail("MARKER_ARGUMENTS") end
        if writes[request.action] and (request.confirm~=true or request.project_id==nil or request.arguments==nil) then fail("MARKER_CONFIRM") end
        if request.confirm~=nil and type(request.confirm)~='boolean' then fail("MARKER_CONFIRM") end
        return strip_nulls(request)
    end
    local function poll_lease(now)
        if now==lease_poll then return end
        lease_poll=now
        local base=root..'/lease'
        if not bmd.fileexists(base..'/ready.marker') then return end
        local packet=bytes(base,0,8)
        if not bmd.fileexists(base..'/ready.marker') or bytes(base,0,8)~=packet then return end
        local expires=uint32(packet,1)
        if adler(packet:sub(1,4))~=uint32(packet,5) then return end
        if expires>=now and expires<=now+lease_seconds+2 then deadline=math.max(deadline,expires) end
    end
    return {
        read_request=function()
            local now=os.time()
            local ok, result=pcall(function()
                local request=read_packet()
                if request==nil then return nil end
                local valid=valid_request(request,now)
                if not renewed[valid.id] then
                    renewed[valid.id]=true
                    deadline=math.max(deadline,now+lease_seconds)
                end
                return valid
            end)
            if not ok then last_error=tostring(result); return nil end
            last_error=nil; return result
        end,
        is_alive=function()
            local now=os.time(); poll_lease(now); return now<deadline
        end,
        get_last_error=function() return last_error end,
        get_deadline=function() return deadline end,
        decode_json=decode_json,
    }
end
