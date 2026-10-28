"""Bounded console bootstrap for Resolve contexts without disk Lua loaders.

The existing typed client must publish the request first. This adapter bundles
only generated session modules and the immutable request; it is not a resident
bridge and deliberately excludes render jobs and replay-dependent operations.
"""

import re
from pathlib import Path

from providers.resolve.lua_transport import lua_string

ALLOWED_ACTIONS = {
    "ping",
    "get_current_project",
    "create_timeline",
    "set_clip_properties",
    "get_timeline_summary",
}
MODULES = (
    "editing.lua",
    "finishing.lua",
    "native_audio.lua",
    "audio_pilot.lua",
    "sync.lua",
    "ripple.lua",
    "camera_visibility.lua",
    "ripple_batch.lua",
    "ripple_span.lua",
    "privacy.lua",
)


def lua_source_literal(source: str) -> str:
    """Keep the console input on one line and preserve source bytes."""
    return (
        '"'
        + source.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        + '"'
    )


def console_command(root: Path) -> str:
    """Build one local command from a typed request and fixed session files."""
    root = root.resolve()
    request = (root / "request.lua").read_text(encoding="ascii")
    match = re.search(r',action="((?:\\\d{3})+)"', request)
    if not match:
        raise ValueError("No typed request is pending")
    action = bytes(int(n) for n in re.findall(r"\\(\d{3})", match[1])).decode()
    if action not in ALLOWED_ACTIONS:
        raise ValueError("Action unsupported in one-shot console mode")
    bridge = (root / "bridge.lua").read_text(encoding="utf-8")
    if any(
        bridge.count(anchor) != 1 for anchor in ("while true do", "local handled = {}")
    ):
        raise ValueError("Unsupported bridge source")
    bridge = bridge.replace("while true do", "for console_iteration = 1, 1 do")
    bridge = bridge.replace(
        "local handled = {}",
        "_G.ResolveAgentConsoleHandled = _G.ResolveAgentConsoleHandled or {}; "
        "local handled = _G.ResolveAgentConsoleHandled[session] or {}; "
        "_G.ResolveAgentConsoleHandled[session] = handled",
    )
    bridge = bridge.replace(
        "lifetime=until_stop_or_resolve_exit", "mode=one_shot_console"
    ).replace('local stop_reason = "error"', 'local stop_reason = "one_shot_complete"')
    sources = {name: (root / name).read_text(encoding="utf-8") for name in MODULES}
    sources.update({"bridge.lua": bridge, "request.lua": request})
    entries = ",".join(
        "[" + lua_string((root / name).as_posix()) + "]=" + lua_source_literal(source)
        for name, source in sources.items()
    )
    return (
        "local sources={" + entries + "}; local run; run=function(path) "
        "local source=assert(sources[path], 'UNBUNDLED_MODULE'); "
        "local make=assert(loadstring('return function(dofile) '..source..' end')); "
        "return make()(run) end; run("
        + lua_string((root / "bridge.lua").as_posix())
        + ")"
    )
