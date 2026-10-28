# Free marker MCP backend

`free-marker` is an implemented, opt-in backend of `davinci-agent-mcp`. It
connects the typed Lua tool surface to a self-contained Scripts-menu bridge
without `dofile`, `loadfile`, external scripting or a payload-code evaluator.
The default `python` backend and its existing filesystem queue are unchanged.
This backend does not expose every tool from the Python server.

Offline Python/Lua 5.1 tests pass against native API doubles. Real MCP stdio
reads, a guarded native operation with backup/readback, and client reconnect
with exact-key replay also passed on **Windows Resolve Free 21.1.2.10**.
The [manual matrix](manual-integration-testing.md#free-marker-mcp-backend--2026-10-29)
records the exact scope and measured latency. This does not establish every
operation family, comprehensive long-session responsiveness or recording readiness.

## Prepare and connect

Use an installed checkout with its virtual environment and a **new, nonexistent
runtime directory** for every native bridge session. The examples use absolute
placeholder paths; replace all of them with paths on the same local machine.

```powershell
Set-Location 'C:/path/DaVinci-Resolve-Agent'
& 'C:/path/DaVinci-Resolve-Agent/.venv/Scripts/python.exe' `
    -m mcp_server.free_marker `
    --runtime 'C:/path/runtime/marker-session-001' `
    --prepare --install-script
```

Preparation creates `session.json`, the immutable `marker-bridge.lua` bundle
and an initial lease. Local responses and receipts are created as needed. `--install-script`
installs only the managed `Resolve Agent Marker.lua` in the existing Windows
user `Support/Fusion/Scripts/Utility` directory. It refuses an unmanaged file
with that name. `--scripts-directory ABSOLUTE_PATH` selects another existing
Scripts directory. Optional repeatable `--media-root ABSOLUTE_PATH` arguments
allow media imports from those existing directories. With no media roots,
imports have no allowed source directory. `--lease-seconds` defaults to 600
and accepts 5–3600 seconds.

Start the MCP server through the actual client's configuration, or run it in
a terminal for a stdio client:

```powershell
& 'C:/path/DaVinci-Resolve-Agent/.venv/Scripts/davinci-agent-mcp.exe' `
    --backend free-marker --runtime 'C:/path/runtime/marker-session-001'
```

The equivalent module entrypoint is `python -m mcp_server.free_marker --runtime
ABSOLUTE_RUNTIME`. Do not start a second server for the same runtime while the
client already owns it. Server startup renews the lease but does not launch
Resolve or invoke a script.

With the saved target project open in Resolve, launch **Workspace > Scripts >
Utility > Resolve Agent Marker** once. Some menu layouts omit `Utility`.
Only the menu launch is supported: do not use Fusion `Execute`, the console,
a preference-file loader or an autostart mechanism for this backend. Keep the
same runtime in the installed menu script and the MCP configuration.

Verify MCP `initialize` and `tools/list`, then call
`resolve_free_connection_status`. Its new native request and correlated export
establish connection health and current project identity. A successful stdio
handshake or lease renewal alone does not prove Resolve is connected.

## Configure the client's actual scope

For Codex, use `~/.codex/config.toml` for user scope or the trusted project's
`.codex/config.toml` for project scope. A config in another checkout does not
register this server in the current workspace. The absolute executable, `cwd`
and runtime argument must all match the prepared installation. See the
[official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

```toml
[mcp_servers.davinci-resolve-free]
command = 'C:/path/DaVinci-Resolve-Agent/.venv/Scripts/davinci-agent-mcp.exe'
args = ["--backend", "free-marker", "--runtime", "C:/path/runtime/marker-session-001"]
cwd = 'C:/path/DaVinci-Resolve-Agent'
startup_timeout_sec = 20
tool_timeout_sec = 150
default_tools_approval_mode = "writes"
required = false
```

The editing tools retain the `resolve_lua_*` names, including
`resolve_lua_get_project`, `resolve_lua_native_audio` and `resolve_lua_stop`.
`resolve_lua_reload_modules` is omitted because the menu bundle is immutable.
The backend adds `resolve_free_connection_status` and
`resolve_free_pending_requests`; it exposes no raw Lua endpoint or public
arbitrary UI automation tool.

## Session lifetime and recovery

The native bridge has a renewable **600-second lease** by default. The running
MCP server publishes a compact lease every 30 seconds by default, or more often
for a shorter configured lease; the Lua loop checks it cooperatively. When the
server disconnects or dies, the loop can
finish when the last valid lease expires. Lease expiry cannot interrupt a
native API call already in progress. An old request left in the inbox does not
keep extending the lease. Use `resolve_lua_stop` for an explicit acknowledged
stop; stopping this loop does not quit Resolve or start recording.

A client may reconnect to the same runtime while its original native loop is
still alive. Once that loop stops, expires, errors or Resolve restarts, the
runtime is consumed and **cannot be bootstrapped again**. The native
`marker-session-consumed.drp` marker prevents reuse after losing in-memory
request/job ownership. Inspect the old runtime and reconcile outstanding work,
then prepare a fresh directory, reinstall its menu bundle, update the client
runtime argument and launch the new menu session once. Do not delete the
consumed marker or reuse a runtime to force a retry. A native background render
may outlive the bridge; a fresh runtime does not restore its ownership map.

`resolve_free_pending_requests` inspects local pending receipts, the occupied
inbox and lifecycle exports without dispatching anything. It is a recovery
diagnostic, not a native liveness check. `video_agent_status` belongs to the
default Python bridge and its cached heartbeat does not describe this backend.

Writes retain exact project identity checks, explicit confirmation, backups,
readback and durable receipts. If a write times out, retain the original
runtime. Repeat only the **same tool, exact arguments and idempotency key** to
collect its original acknowledgement; this recovery does not publish a new
request or extend its original expiry. A different key is not a retry. If no
terminal acknowledgement exists, inspect the project and backup before deciding
how to recover. Acceptance of a render job is distinct from completed output.

## Data transport and cost

The single-slot mailbox contains typed JSON with a **4096-byte maximum for the
complete UTF-8 envelope**. A fixed 8-byte header carries magic, version, bounded
length and an Adler-32 checksum. Set-bit files use predefined names; the host
publishes the completed inbox directory atomically. The Lua reader validates
UTF-8, JSON shape, allowed action, session, request ID, expiry and checksum before
dispatch. The checksum detects corruption; it is not authentication against
another process that can write the private runtime directory.

The generated menu script embeds only the fixed trusted bridge modules. Incoming
data is never evaluated as Lua, Python, shell text or a Resolve expression. No
global disk loader or `loadstring` is needed. The bridge reuses the existing
typed action handlers and mutation guards rather than accepting caller-defined
native API calls.

Native `.drp` exports provide results correlated by the private session directory
and request nonce in the response filename; the bounded parser validates project
identity. These exports contain project data and
can be expensive. Keep them local, measure latency and storage on the actual
project, and use the bounded response cleanup tool when appropriate. Preserve
pending-write and lifecycle evidence until reconciled. A passing small-project
test is not a throughput guarantee for a large project or long editing session.

## Resolve API and GUI boundary

The backend offers the existing typed Lua editing and native-audio operations,
subject to their per-action guards and installed Resolve API support. Its new
transport does not add native APIs for physical microphone selection, input
patching, record arming, or track/bus EQ, Dynamics and all effect inserts. Those
application settings require a separately verified GUI workflow; a mono track,
fresh status response or successful native-audio command does not establish that
the microphone is routed or its processing bypassed. See
[native audio limits](native-audio.md).

[`scripts/capture-resolve-window.ps1`](../scripts/capture-resolve-window.ps1) is
an optional bounded Windows diagnostic for an operator who explicitly authorizes
UI Automation/GDI capture. It targets a specified Resolve process/title and
produces a local screenshot. Its optional maximize/focus actions and capture do
not configure audio. It is not the default backend, a general desktop controller
or a public MCP tool.

## Evidence and version scope

- Historical resident Lua editing/render results apply to Windows Free
  **21.1.0.17**, documented in [the Lua route](lua-experimental.md).
- On Windows Free **21.1.2.10**, console and menu probes found `dofile=nil`,
  `loadfile=nil` and menu `SavePrefs=nil`; native `ExportProject` worked. A
  finite self-contained marker request/acknowledgement succeeded without project
  edits. Those observations motivated this separate backend.
- Offline tests exercise the real generated Lua 5.1 bundle with API doubles,
  malformed/partial/replayed/wrong-session requests, leases, guards and typed
  stdio dispatch. Live latency, UI responsiveness, reconnect/stop, guarded
  mutation/readback and exact-key replay remain acceptance gates recorded in the
  [manual matrix](manual-integration-testing.md).
- Blackmagic's built-in AI assistant integration is a separate Studio route;
  see the [official 21.1 announcement](https://www.blackmagicdesign.com/media/release/20260908-03)
  and the installed *DaVinci Resolve 21.1 Reference Manual*, *Using DaVinci
  Resolve with AI Assistants*, page 4318 (`File > Setup AI Assistants`). This
  package does not claim to enable that feature in Free. The installed
  `Support/Developer/Scripting/README.md` and
  `Support/Developer/Workflow Integrations/README.txt` document their own
  external-scripting and Studio plugin scopes; see also the
  [official Studio product page](https://www.blackmagicdesign.com/products/davinciresolve/studio).
