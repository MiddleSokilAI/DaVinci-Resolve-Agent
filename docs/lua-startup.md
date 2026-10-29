# Experimental Windows startup launcher

Status: implemented and tested offline with Lua 5.1 API doubles, but the
**2026-09-18 cold-start acceptance on Resolve 21.1 Free / Windows failed**.
The launcher started Resolve and installed the configured hook. More than 180
seconds after the application main loop started, it remained in Project Manager;
there was no consumed-session export and two MCP pings timed out. No manual
project opening or Lua bootstrap was substituted. Project, timeline and page
readback therefore remain unverified.

The installed folder matches the scripting documentation and local path map.
A subsequently supplied Lua console screenshot **confirmed the hook and worker
executed** and stopped at `Another project is open; automatic switching refused`.
A diagnostic after manual opening read the expected UUID, Edit page and expected
timeline. A further instrumented cold start captured the actual conflict:
`Untitled Project`, string UUID, nil current page, root project folder, zero
timelines, no saved-name match, and exactly one target-name match. The narrow
placeholder handling below is implemented and tested offline; its next
cold-start acceptance remains pending.
This is an experimental candidate, **not a working automatic-start guarantee**.
Existing editing acceptance does not prove startup compatibility. Do not interrupt
a review or restart Resolve solely to install this feature.

## Evidence and choice

The installed Resolve 21.1 scripting README lists internal console/menu/render
and composition scripts. Scanning a Scripts folder at startup populates menus;
it does not itself establish that ordinary `.lua` files execute automatically.
The local bundled Python external-API probe returned no Resolve object.

Blackmagic's [Fusion scripting guide, Script Libraries](https://documents.blackmagicdesign.com/UserManuals/Fusion8_Scripting_Guide.pdf)
documents root `Scripts:` `.scriptlib` loading. This is Fusion documentation,
not a guarantee for every Resolve edition. An earlier AutoSubs implementation
used that hook with `fusion:Execute()`. Its maintainer's
[2026-09-19 investigation](https://github.com/tmoroney/auto-subs/blob/main/Resolve-Integration/docs/resident-bridge-fusion-regression.md)
reports Fusion control regressions and says the startup scriptlib was removed.
The proposed `RunScript` and trampoline alternatives were not pursued. That
historical implementation is not current evidence for a reliable resident
startup context. This launcher remains experimental; the separate
[managed Windows autostart](managed-autostart.md) invokes the fixed Scripts-menu
entry and needs its own live acceptance.

Our hook dispatches only a locally generated worker. It never runs the resident
loop synchronously during startup. By default it waits for the configured project
UUID without opening projects. Explicit `--project-name` enables loading the
existing named project. Neither mode changes pages, seeks or starts playback.
No arbitrary script argument, network listener, scheduled task, service or
keyboard/mouse automation is introduced.

## Prepare, then launch

Run from the repository's Python environment. Supply a previous runtime whose
receipts have been reconciled. Preparation only writes a fresh staging folder:

```powershell
python -m providers.resolve.lua_launcher --base G:/ResolveAgentStartup --previous-runtime G:/PreviousRuntime --project-id PROJECT_UUID --media-root G:/ApprovedMedia
```

After the user finishes reviewing and closes Resolve, repeat with `--launch`.
That explicit option installs **one** file,
`%APPDATA%/Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/ResolveAgentStartup.scriptlib`,
and opens the installed Resolve.exe. A custom `--executable` must name Resolve.exe.
Existing unmanaged files at that hook path are never overwritten.

To open an existing project automatically, supply both `--project-name` with its
exact name and `--project-id` with its known UUID. `LoadProject(name)` is documented
in the installed 21.1 `DaVinciResolveScript.pyi`. The worker requires exactly one
matching name in the **current Project Manager folder**; it never searches or
switches databases/folders, creates, imports or deletes projects. If any other
project is already open, it refuses to switch even if that project is saved,
apart from the strictly checked startup placeholder described below.
It rechecks immediately before loading and verifies both the returned and current
project UUID afterward. On mismatch it stops before exporting/starting the bridge;
the incorrectly matched project may already be open, because this API cannot
query an unopened project's UUID. It never saves or edits that project.

The only placeholder exception requires all these conditions: dispatch by our
startup hook for this session, an actual nil current page, the exact name
`Untitled Project`, a nonempty string UUID, root project folder, zero timelines,
no saved-project name match, and exactly one configured target-name match. The
media pool must expose an empty root clip list **and** empty subfolder list;
unavailable or failing media APIs refuse the exception. Immediately before
`LoadProject`, the worker re-reads the current UUID and every condition. Any
change cancels loading. No `CloseProject`, save, delete, import or database reset
is performed. Direct `LoadProject` is attempted once, then both returned and
current UUIDs must match the expected target. Live success is not yet confirmed.

Readiness polling is bounded by `--ready-timeout-seconds` (default 180, range
1–600). This timeout is for startup only, not the persistent bridge lifetime.
Missing/ambiguous names, load failure and identity mismatch fail closed without
retries. Whether this Free build exposes the Project Manager to an asynchronous
startup hook before a project is open remains a **live acceptance requirement**.
Adding `LoadProject` does not solve a missing/non-executing hook by itself.

The Python CLI works without PowerShell script execution. If `.ps1` launchers are
blocked by Windows ExecutionPolicy, invoke the Python command directly or use a
local `.cmd` wrapper invoking Python with a saved argument file. Do not change
machine/user policy or silently introduce console keystrokes as a fallback.

**Use this launcher for subsequent starts.** It generates a fresh random session
token and directory each time. Read `<base>/active.json` for the runtime to pass
to `mcp_server.lua_experimental --runtime ...`; this file is a locator, not proof
of connection. Confirm MCP ping and project readback after opening the target
project. Startup waiting itself does not export or save the project. Once the
target opens, one local DRP export records that this runtime has been consumed.
Ordinary starts with the old installed hook cannot reuse a consumed session.

## Safety and lifecycle

- The launcher refuses to run while Resolve.exe exists. Its exclusive lock
  serializes launcher instances; a stale lock requires explicit reconciliation.
- Previous pending/unknown receipt states, malformed receipts, and client locks
  block rotation. Old backups, receipts and media are preserved. Successful
  results are not automatically replayed in a new runtime.
- Startup uses a shared Fusion preference claim and an invocation guard to avoid
  duplicate dispatch. The session's consumed DRP marker prevents restarting it
  after loss of the in-memory replay set. It is not a playback/render artifact.
- Do not concurrently start Resolve by another route, or manually bootstrap a
  second bridge in another scripting context. No forced takeover is attempted.
- Once running, the bridge has no lifetime cutoff. Closing/reopening the MCP
  client retains the running Lua session. Acknowledged stop, Lua error or Resolve
  exit ends it; errors do not trigger an automatic restart or write replay.
- Startup error messages go to the Lua console. If the hook never loads, a
  missing ping is inconclusive: inspect startup diagnostics rather than adding
  GUI automation. Do not mark startup verified on the strength of unit tests.
- A foreign-project refusal now emits `STARTUP_PROJECT_CONFLICT` with the actual
  name, UUID and its type, current page/folder, timeline count and exact-name match
  counts at the time of refusal. It attempts to save `startup-conflict.txt` in the
  private runtime only when `io.open` is available; restricted Lua contexts print
  `STARTUP_CONFLICT_FILE_SAVED=false` and require copying the console output.
  No project is exported, saved, closed or switched for this diagnostic. Even an
  empty project named `Untitled Project` remains protected unless the entire
  startup signature above passes twice; its name alone is never sufficient.
  Keep diagnostics private.

Guard diagnostics evaluate **all** operands once, without short-circuiting, and
record the exact snapshot used for the decision. Each field includes value,
Lua type and read success; tables expose counts, not their contents. The compact
console row includes specific reason codes (`CONTEXT_MISSING`, `PAGE_NOT_NIL`,
`READ_ERROR_clips`, `CLIPS_NOT_EMPTY_TABLE`, etc.) and the refusal phase. A race
before loading records the original UUID, the fresh snapshot and whether identity
changed. Only the first refusal is persisted. A later manual project load cannot
overwrite this historical evidence.

If `io.open` is unavailable, the worker attempts the documented
`Fusion:SavePrefs(filename)` API to save `startup-conflict.prefs` **inside the
session directory**. It temporarily places the report in a session-specific
diagnostic preference, saves to that explicit path, then restores and checks the
previous value. It never invokes parameterless SavePrefs, loads preferences or
exports/changes a project. This file may contain other private Fusion preferences;
keep it local and extract only the `STARTUP_PROJECT_CONFLICT` report. File
existence and restoration determine the printed `STARTUP_CONFLICT_PREFS_SAVED`
result. Native support for this fallback in this Free build is not yet verified;
the console report remains available if saving fails.

The completed console probes confirmed context survives both direct `dofile`
and asynchronous `fusion:Execute` followed by `dofile`. The shared owner value
is intentionally the string produced by `tostring({})`; seeing `type=string`
with a `table: ...` value is not evidence of a preference conversion defect.
Neither observation recovers the earlier placeholder's media or page types.

A subsequent native preference snapshot narrowed the actual refusal to the
clip/subfolder tables: both were successfully returned as tables with one entry,
while context, page type, project identity and the other predicates passed.
The worker now additionally captures `clips_shape` and `subfolders_shape` at that
same decision: up to eight entries with key, key type, value type and a scalar
value capped at 100 characters. Objects are not traversed, control characters
are flattened, and truncation is explicit. This evidence is saved through the
same explicit-path preferences export that worked in the live test. The next
native snapshot proved that each otherwise empty list contained only the string
key `__flags` with numeric value `4194304`. The guard now excludes **only that
exact typed metadata entry** from emptiness checks. Empty tables remain valid;
real array items (including sparse indices), unknown keys, different flag values
and string-valued flags still refuse loading. Both initial and pre-load checks
use this rule. Offline tests cover the observed representation and those negative
cases; successful native autoload after this fix remains pending. This avoids requiring F6 inside
Project Manager or losing the placeholder by manually opening another project.

To disable future startup, remove only the managed `ResolveAgentStartup.scriptlib`
file. This does not stop an already running bridge; use the normal acknowledged
stop with a saved project open. Retain runtime directories until receipts and
backups are no longer needed. No automatic deletion is performed.

## Live acceptance still required

With review finished: launch once through the helper, open the expected project,
verify ping and identity, close the console and repeat ping. With explicit project
selection, first verify that the existing project opens automatically. Confirm no change
to the timeline/playhead. Then test an acknowledged stop and a subsequent fresh
application start. Check that a consumed runtime cannot restart and that an
uncertain write blocks migration. Offline tests cover these guard decisions and
worker control flow; only the live test proves the installed Free build loads
the hook and supports the asynchronous context.
