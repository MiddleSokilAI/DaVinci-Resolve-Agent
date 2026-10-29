"""Generate a self-contained, data-only Free marker menu bridge.

Trusted generated modules are embedded as Lua function factories. Incoming
marker bytes go only to the strict packet reader, never to a Lua compiler.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from providers.resolve.lua_console import MODULES
from providers.resolve.lua_editing import lua_value
from providers.resolve.lua_transport import lua_string


def _replace_once(source: str, anchor: str, replacement: str) -> str:
    if source.count(anchor) != 1:
        raise ValueError(f"Unsupported bridge template anchor: {anchor}")
    return source.replace(anchor, replacement, 1)


def build_marker_bridge(root: Path) -> str:
    """Bundle one prepared marker runtime without writing or launching it."""
    root = root.resolve()
    metadata = json.loads((root / "session.json").read_text(encoding="utf-8"))
    session = metadata.get("session")
    lease = metadata.get("lease_seconds", 600)
    limit = metadata.get("max_payload_bytes", 4096)
    roots = metadata.get("media_roots")
    if (
        metadata.get("transport") != "marker"
        or metadata.get("marker_protocol") != 1
        or not isinstance(session, str)
        or not re.fullmatch(r"[0-9a-f]{32}", session)
        or type(lease) is not int
        or not 5 <= lease <= 3600
        or type(limit) is not int
        or not 1 <= limit <= 4096
        or not isinstance(roots, list)
        or any(not isinstance(value, str) for value in roots)
    ):
        raise ValueError("Invalid prepared marker session metadata")
    bridge = (root / "bridge.lua").read_text(encoding="utf-8-sig")
    for anchor in (
        "local root = " + lua_string(root.as_posix()),
        "local session = " + lua_string(session),
        "local media_roots = " + lua_value(roots),
    ):
        if bridge.count(anchor) != 1:
            raise ValueError("Generated bridge and marker metadata disagree")
    bridge = _replace_once(bridge, "while true do", "while marker_reader.is_alive() do")
    bridge = _replace_once(
        bridge, 'local stop_reason = "error"', 'local stop_reason = "lease_expired"'
    )
    bridge = _replace_once(
        bridge,
        "lifetime=until_stop_or_resolve_exit",
        "mode=free_marker; lifetime=bounded_lease",
    )
    # A persistent marker bundle cannot reload modules from mutable runtime files.
    bridge = _replace_once(
        bridge,
        'or request.action == "reload_modules" or writes[request.action]',
        "or writes[request.action]",
    )
    bridge += """
local exit_pm=api:GetProjectManager()
local exit_project=exit_pm:GetCurrentProject()
local owned_project_id=shared.project_id or marker_project_id
if exit_project and exit_project:GetUniqueId()==owned_project_id then
    local reason=ok and stop_reason or "error"
    pcall(function()
        exit_pm:ExportProject(exit_project:GetName(),
            root.."/marker-exit-"..reason..".drp", false)
    end)
end
"""
    reader = (
        Path(__file__).resolve().parents[2]
        / "bridges"
        / "resolve"
        / "ResolveLuaMarkerReader.lua"
    ).read_text(encoding="utf-8-sig")
    sources = {name: (root / name).read_text(encoding="utf-8-sig") for name in MODULES}
    sources["bridge.lua"] = bridge
    factories = "\n".join(
        "factories["
        + lua_string((root / name).as_posix())
        + "]=function(...)\nlocal dofile=...\n"
        + source
        + "\nend"
        for name, source in sources.items()
    )
    return (
        "-- Generated self-contained Free marker bridge. Run from the menu once.\n"
        "-- Incoming packets are data; no runtime files are compiled or evaluated.\n"
        "if _G.ResolveAgentLuaRunning or _G.ResolveAgentMarkerSession then\n"
        "    print('Resolve Agent bridge already running'); return\nend\n"
        "assert(type(bmd)=='table' and type(bmd.fileexists)=='function',"
        "'MARKER_FILEEXISTS_UNAVAILABLE')\n"
        "assert(type(bmd.wait)=='function','MARKER_WAIT_UNAVAILABLE')\n"
        "assert(type(os)=='table' and type(os.time)=='function',"
        "'MARKER_CLOCK_UNAVAILABLE')\n"
        "local root=" + lua_string(root.as_posix()) + "\n"
        "local session=" + lua_string(session) + "\n"
        "local marker="
        + lua_string((root / "marker-session-consumed.drp").as_posix())
        + "\n"
        "assert(not bmd.fileexists(marker),'MARKER_SESSION_ALREADY_CONSUMED')\n"
        "local pm=assert(resolve:GetProjectManager())\n"
        "local project=assert(pm:GetCurrentProject(),'MARKER_PROJECT_REQUIRED')\n"
        "local marker_project_id=project:GetUniqueId()\n"
        "assert(type(marker_project_id)=='string' and marker_project_id~='',"
        "'MARKER_PROJECT_REQUIRED')\n"
        "local reader_factory=(function()\n" + reader + "\nend)()\n"
        "local marker_reader=reader_factory(root,session,"
        + str(limit)
        + ","
        + str(lease)
        + ")\n"
        "local factories={}\n" + factories + "\n"
        "local load_module\nload_module=function(path)\n"
        "    if path==root..'/request.lua' then\n"
        "        return marker_reader.read_request()\n    end\n"
        "    local factory=assert(factories[path],'MARKER_UNBUNDLED_MODULE')\n"
        "    return factory(load_module)\nend\n"
        "if _G.ResolveAgentLuaRunning or _G.ResolveAgentMarkerSession then\n"
        "    print('Resolve Agent bridge already running'); return\nend\n"
        "_G.ResolveAgentMarkerSession=session\n"
        "local succeeded,reason=pcall(function()\n"
        "    assert(not bmd.fileexists(marker),'MARKER_SESSION_ALREADY_CONSUMED')\n"
        "    assert(pm:ExportProject(project:GetName(),marker,false),"
        "'MARKER_CONSUMPTION_EXPORT_FAILED')\n"
        "    assert(bmd.fileexists(marker),'MARKER_CONSUMPTION_EXPORT_MISSING')\n"
        "    return load_module(root..'/bridge.lua')\nend)\n"
        "_G.ResolveAgentMarkerSession=nil\n"
        "if not succeeded then\n"
        "    _G.ResolveAgentLuaRunning=nil\n"
        "    print('Resolve Agent marker bridge initialization failed: '\n"
        "        ..tostring(reason))\n"
        "end\n"
    )
