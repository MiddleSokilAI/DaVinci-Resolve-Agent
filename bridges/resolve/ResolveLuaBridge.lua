-- Experimental background bridge for Resolve Free 21.1.
-- Start once inside Resolve. No UI input is used while serving requests.
-- request.lua is generated exclusively by the local typed Python client;
-- this is a trusted local mailbox, NOT an arbitrary-Lua MCP endpoint.
local root = "__RUNTIME_ROOT__"
local session = "__SESSION__"
local media_roots = __MEDIA_ROOTS__
if _G.ResolveAgentLuaRunning then
    print("Resolve Agent Lua bridge already running")
    return
end
local api = assert(resolve, "Run inside Resolve with injected resolve")
assert(type(bmd.wait) == "function", "Cooperative wait unavailable")
assert(os and type(os.time) == "function", "Clock unavailable")
local writes = {quit_resolve=true, create_project=true, import_media=true, create_timeline=true, duplicate_timeline=true, append_clip=true,
    set_clip_properties=true, add_subtitles=true, prepare_render=true, start_render=true}
local shared = {jobs={}}
local edit = dofile(root .. "/editing.lua")(api, root, media_roots, shared)
_G.ResolveAgentLuaRunning = true
local stop_reason = "error"
print("Resolve Agent Lua started; lifetime=until_stop_or_resolve_exit")
local ok, err = pcall(function()
    local handled = {}
    -- Session lifetime is independent of individual request expiry. Keep the
    -- replay set for this entire loop, including MCP client reconnects.
    while true do
        local loaded, request = pcall(dofile, root .. "/request.lua")
        if loaded and type(request) == "table"
            and request.session == session
            and type(request.id) == "string" and #request.id == 32
            and request.id:match("^[0-9a-f]+$")
            and type(request.expires) == "number" and request.expires >= os.time()
            and not handled[request.id]
            and (request.action == "ping" or request.action == "get_current_project"
                 or request.action == "stop" or request.action == "get_render_status"
                 or request.action == "get_timeline_summary" or request.action == "reload_modules" or writes[request.action]) then
            handled[request.id] = true
            local pm = api:GetProjectManager()
            local project = pm:GetCurrentProject()
            if project then
                local suffix = ""
                if request.action == "quit_resolve" then
                    local succeeded, code = pcall(function()
                        local function check(value, reason) if not value then error(reason, 0) end end
                        check(request.confirm == true, "INVALID_ARGUMENTS")
                        check(project:GetUniqueId() == request.project_id, "PROJECT_CHANGED")
                        check(not project:IsRenderingInProgress(), "RENDERING")
                        check(pm:SaveProject(), "BACKUP_SAVE_FAILED")
                        check(pm:ExportProject(project:GetName(), root .. "/" .. request.id .. ".before.drp", false), "BACKUP_EXPORT_FAILED")
                        check(request.expires >= os.time(), "EXPIRED")
                        check(pm:GetCurrentProject():GetUniqueId() == request.project_id, "PROJECT_CHANGED")
                        check(not project:IsRenderingInProgress(), "RENDERING")
                    end)
                    suffix = succeeded and ".accepted" or ".error_" .. (tostring(code):match("^[A-Z_]+$") or "INTERNAL_ERROR")
                elseif request.action == "reload_modules" then
                    local succeeded = false
                    if not project:IsRenderingInProgress() then
                        local loaded_edit, replacement = pcall(function()
                            return dofile(root .. "/editing.lua")(api, root, media_roots, shared)
                        end)
                        if loaded_edit and type(replacement) == "function" then edit = replacement; succeeded = true end
                    end
                    if not succeeded then suffix = ".error_RELOAD_FAILED" end
                elseif writes[request.action] or request.action == "get_render_status" or request.action == "get_timeline_summary" then
                    local succeeded, code = pcall(edit, pm, project, request)
                    suffix = succeeded and ".ok" or ".error_" .. (tostring(code):match("^[A-Z_]+$") or "INTERNAL_ERROR")
                    if succeeded and type(code) == "string" and code:match("^[%w_%-]+$") then suffix = suffix .. "_" .. code end
                end
                local current = pm:GetCurrentProject()
                local exported = current and pm:ExportProject(current:GetName(), root .. "/" .. request.id .. suffix .. ".drp", false)
                print("Resolve Agent Lua response=" .. tostring(exported) .. suffix)
                if exported and request.action == "quit_resolve" and suffix == ".accepted" then
                    -- Acknowledgement precedes Quit: it proves acceptance, not process exit.
                    stop_reason = "quit_requested"
                    api:Quit()
                    break
                end
                if exported and request.action == "stop" then
                    stop_reason = "acknowledged_stop"
                    break
                end
            else
                print("Resolve Agent Lua requires an open project")
            end
        end
        bmd.wait(0.25)
    end
end)
_G.ResolveAgentLuaRunning = nil
print("Resolve Agent Lua stopped; reason=" .. stop_reason .. "; ok=" .. tostring(ok))
if not ok then print(tostring(err)) end
