-- Read-only project context guard. No save, load, folder/database switch or UI.
return function(pm, initial_project, request)
    local function check(value, reason) if not value then error(reason, 0) end end
    local function text(value, limit)
        return type(value) == "string" and #value > 0 and #value <= limit
            and not value:find("[%z\1-\31\127]")
            and value:sub(1,1) ~= " " and value:sub(-1) ~= " "
    end
    local function database(value, expected)
        check(type(value) == "table", "PROJECT_CONTEXT_DATABASE_UNAVAILABLE")
        for key, item in pairs(value) do
            check(key == "DbType" or key == "DbName" or key == "IpAddress"
                or (not expected and key == "__flags" and type(item) == "number"
                    and item == 4194304), "PROJECT_CONTEXT_DATABASE_UNAVAILABLE")
        end
        check(value.DbType == "Disk" or value.DbType == "PostgreSQL",
            "PROJECT_CONTEXT_DATABASE_UNAVAILABLE")
        check(text(value.DbName, 512), "PROJECT_CONTEXT_DATABASE_UNAVAILABLE")
        check(value.IpAddress == nil or (value.DbType == "PostgreSQL"
            and text(value.IpAddress, 512)), "PROJECT_CONTEXT_DATABASE_UNAVAILABLE")
        return value
    end
    local function same(left, right)
        return left.DbType == right.DbType and left.DbName == right.DbName
            and left.IpAddress == right.IpAddress
    end
    local a = request.arguments
    check(type(a) == "table" and (request.confirm == nil or request.confirm == false)
        and text(request.project_id, 128), "PROJECT_CONTEXT_INVALID_ARGUMENTS")
    local count = 0
    for key in pairs(a) do
        check(key == "expected_database" or key == "expected_folder"
            or key == "expected_project_name", "PROJECT_CONTEXT_INVALID_ARGUMENTS")
        count = count + 1
    end
    check(count == 3 and a.expected_folder == ""
        and text(a.expected_project_name, 512), "PROJECT_CONTEXT_INVALID_ARGUMENTS")
    local expected = database(a.expected_database, true)
    local function verify()
        check(type(request.expires) == "number" and request.expires >= os.time(),
            "PROJECT_CONTEXT_EXPIRED")
        local current_database = database(pm:GetCurrentDatabase(), false)
        check(same(current_database, expected), "PROJECT_CONTEXT_DATABASE_MISMATCH")
        local registered = pm:GetDatabaseList()
        check(type(registered) == "table", "PROJECT_CONTEXT_DATABASE_LIST_UNAVAILABLE")
        local total, matches = 0, 0
        for key, value in pairs(registered) do
            if key ~= "__flags" or type(value) ~= "number" or value ~= 4194304 then
                total = total + 1
                check(total <= 128 and type(key) == "number" and key % 1 == 0
                    and key >= 1 and key <= 128,
                    "PROJECT_CONTEXT_DATABASE_LIST_UNAVAILABLE")
                if same(database(value, false), expected) then matches = matches + 1 end
            end
        end
        check(matches == 1, "PROJECT_CONTEXT_DATABASE_AMBIGUOUS")
        check(pm:GetCurrentFolder() == "", "PROJECT_CONTEXT_FOLDER_MISMATCH")
        local names = pm:GetProjectListInCurrentFolder()
        check(type(names) == "table", "PROJECT_CONTEXT_PROJECT_LIST_UNAVAILABLE")
        local projects, matching_names = 0, 0
        for key, value in pairs(names) do
            if key ~= "__flags" or type(value) ~= "number" or value ~= 4194304 then
                projects = projects + 1
                check(projects <= 128 and type(key) == "number" and key % 1 == 0
                    and key >= 1 and key <= 128 and text(value, 512),
                    "PROJECT_CONTEXT_PROJECT_LIST_UNAVAILABLE")
                if value == a.expected_project_name then matching_names = matching_names + 1 end
            end
        end
        check(matching_names == 1, "PROJECT_CONTEXT_PROJECT_NAME_AMBIGUOUS")
        local current = pm:GetCurrentProject()
        check(current and current:GetUniqueId() == request.project_id
            and current:GetName() == a.expected_project_name
            and initial_project:GetUniqueId() == request.project_id
            and initial_project:GetName() == a.expected_project_name,
            "PROJECT_CONTEXT_PROJECT_MISMATCH")
    end
    verify()
    verify()
    return "context_verified"
end
