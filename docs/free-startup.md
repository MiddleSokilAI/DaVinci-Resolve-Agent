# Start Resolve Free and understand connection status

Opening Resolve, opening a project, starting the native bridge and connecting
the MCP client are separate steps. The Free Marker backend needs an **already
open saved project**. Its native replies are project exports, so it cannot
bootstrap or return a project catalogue from an empty Project Manager screen.

Two modes use the same native menu bundle. Static `--runtime` mode requires
the menu step below. Opt-in `--managed-profile` mode delegates that step and
runtime rotation to a Windows supervisor; see the
[managed autostart guide](managed-autostart.md). Neither mode chooses or opens
a project from Project Manager.

## The manual path (`--runtime`)

1. Launch the installed Resolve application and open the intended **existing**
   project. Do not create a replacement with a similar name.
2. Use the prepared runtime in the MCP configuration and its matching managed
   `Resolve Agent Marker.lua` menu file. Start the MCP client.
3. In the open project, run **Workspace > Scripts > Utility > Resolve Agent
   Marker** once. Some layouts omit Utility.
4. Call `resolve_free_connection_status` with `target_project_name` set to the
   intended name. Check `connected`, `native_roundtrip_verified`, the returned
   project name/ID and `target_project_matches` before any media mutation.

A fresh connection to a different project is still a connection, but its target
match is false. Open the correct existing project and verify again. A connected
response does not establish input routing, audio quality or recording readiness.
A healthy read can coexist with an earlier uncertain write. Connection status
preserves recovery instructions for that operation; inspect them before changing
project media or timelines. It does not acknowledge or clear an earlier write.

The static Free backend has no MCP command to launch Resolve or list/open an
existing project from Project Manager. Static configuration alone does not start
the menu bridge. The managed supervisor can invoke the fixed menu after a saved
project window is available, then requires a fresh native reply. Its persisted
status is exposed through `resolve_free_autostart_status`; it is separate from
the per-runtime diagnostics below. The experimental scriptlib launcher remains
an independent route with pending acceptance and known upstream regression
evidence; it is not substituted by either mode.

## Diagnose before waiting for another timeout

`resolve_free_startup_status` reads local evidence without publishing a native
request. Pass the intended `target_project_name` to label its manual handoff.
It does not start Resolve, change projects, invoke scripts or import media.
With managed autostart enabled, inspect the supervisor status first. Do not
manually invoke a second bridge while a dispatch is in progress or its outcome
is uncertain.

| State | Evidence and next step |
| --- | --- |
| `session_exited` | Valid consumed and exit exports show the immutable native session ended. Reconcile original operations first. If the menu already belongs to another prepared runtime, align/reconnect MCP to that runtime and inspect it before preparing another one. |
| `menu_alignment_required` | The configured runtime and installed managed menu do not match, or the menu is missing/unreadable. Align them and reconnect the client. |
| `bootstrap_required` | No valid native start export was observed. Ensure the existing saved project is open, run the managed menu once, then request a fresh native check. Absence of a marker does not prove why bootstrap has not completed. |
| `recovery_required` | An original request or pending receipt needs inspection. Preserve the runtime and reconcile that operation with its original arguments/key. |
| `inspection_required` | Evidence is invalid/incomplete or a client/lease publisher may be active. Inspect or wait and retry diagnostics; do not delete locks or rotate the runtime blindly. |
| `runtime_invalid` | Session metadata is missing, corrupt, unreadable or oversized. Audit old operation evidence before preparing a fresh runtime. |
| `native_check_required` | Start evidence is present; only a new correlated native reply can establish current connection health. |

A known terminal session with clear recovery evidence returns
`failure_code="native_session_exited"` immediately from connection status. It
does not publish another request into that exited session. The response states
`native_request_attempted=false` and `liveness_source="native_lifecycle_exports"`.
A native timeout is `no_native_response`; it does **not** identify its cause.
Other transport or request failures have separate codes and retain diagnostics.

## Optional host hints

Set `inspect_host_metadata=true` on startup status to read bounded Windows
process metadata. It uses no UI input or capture. A `Project Manager` title,
process ID, responding window or renewed lease never proves the active native
project, its absence or a working bridge. Host metadata and native evidence are
reported separately. With the default false, no host probe runs.

## Diagnose an installation that cannot start MCP

The standalone diagnostic also works without valid session metadata:

```powershell
& 'C:/Tools/DaVinci-Resolve-Agent/.venv/Scripts/python.exe' `
    -m mcp_server.free_marker --runtime 'C:/DaVinciAgent/runtime/session-001' `
    --diagnose --target-project-name 'Existing project'
```

This prints JSON and does not prepare or modify the runtime. Optional
`--scripts-directory` inspects a specific existing Scripts directory;
`--inspect-host-metadata` adds the read-only host hint. Diagnostic mode cannot be
combined with preparation, script installation or media-root changes.

For the initial preparation/configuration commands see the
[Free backend guide](free-mcp-backend.md). Updating a configuration file does not
prove an already-running client's connection was refreshed: compare its reported
session with the intended runtime and matching menu bundle.

## Before importing approved media

Keep a failed connection check as a stop point. Establish the fresh native
project identity first, inspect the existing timeline and media, then perform
only the requested guarded import/append operation. Preserve existing media,
projects and timelines. Do not use a production import as a connection test.

These diagnostics have offline automated coverage. They do not add live
acceptance of Windows UI, automatic project opening or unattended startup.
Managed autostart has its own acceptance requirements; an installed watcher or
successful menu dispatch is not proof of a native connection.
