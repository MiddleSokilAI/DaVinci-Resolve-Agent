"""Local Lua file loader compatibility without changing interpreter globals."""

LOADER_PREFIX = """-- Resolve Agent local loader compatibility.
local dofile = dofile
if type(dofile) ~= "function" and type(loadfile) == "function" then
    dofile = function(path)
        local chunk, reason = loadfile(path)
        assert(chunk, reason)
        return chunk()
    end
end
assert(type(dofile) == "function",
    "RESOLVE_AGENT_FILE_LOADER_UNAVAILABLE: dofile and loadfile are unavailable")
"""


def with_loader(source: str) -> str:
    """Scope the fallback to this chunk, retaining normal loadfile errors."""
    return LOADER_PREFIX + source
