# Experimental Lua MCP bridge (Resolve Free 21.1)

This is an opt-in **prototype**, separate from the Python bridge. Protocol 4
adds live timeline summaries and fixed-module reload to protocol-3 finishing.
It does not replace the production MCP server or expose its entire surface.
Live-tested on Windows 10 build 19045, Resolve Free 21.1.0.17 and Python
3.12.10: import, AV assembly, gain, zoom, captions-first SRT and short MP4 render.
**This remains experimental; a five-minute render and closed-console requests
are now live-verified. Continuous render progress is unavailable.** It is not a full production-provider replacement.

After one bootstrap inside Resolve, requests use a local mailbox. No keyboard,
mouse, window activation, screenshots, desktop automation or network connection
is used by the MCP server or Lua request loop.

## Setup from a checkout

Choose a new private local directory for each session (not a shared folder).
From the repository root:

```powershell
.\.venv\Scripts\python.exe -m mcp_server.lua_experimental --runtime C:/ResolveAgentLua/session1 --prepare --media-root C:/Users/YourName/Videos
```

Open a **saved project** in Resolve. In Workspace → Console, choose Lua and run
the generated script once:

```lua
dofile("C:/ResolveAgentLua/session1/bridge.lua")
```

Configure an optional MCP server with the repository as its working directory:

```toml
[mcp_servers.davinci-resolve-lua-experimental]
command = ".venv/Scripts/python.exe"
args = ["-m", "mcp_server.lua_experimental", "--runtime", "C:/ResolveAgentLua/session1"]
startup_timeout_sec = 20
tool_timeout_sec = 150
required = false
```

Repeat `--media-root` for additional approved media directories. Without media
roots, importing/appending files is disabled. Roots are fixed in both the session
manifest and the installed Lua script; use a new session to change them. Old
protocol-1 sessions remain read-only and need a fresh preparation for editing.
Protocol-2 sessions cannot use finishing commands; prepare a new runtime.
Protocol-3 sessions cannot use timeline summary or module reload; those require
a fresh protocol-4 bootstrap.

These tools are advertised:

- `resolve_lua_ping`: fresh round-trip, requires an open project.
- `resolve_lua_get_project`: project name and ID from a fresh DRP export.
- `resolve_lua_stop`: acknowledge via export, then stop the Lua loop.
- `resolve_lua_import_media`: import 1–20 local media files into the current
  Media Pool folder, rejecting files already present in the pool.
- `resolve_lua_create_timeline`: create an empty timeline with a unique exact
  name and the project's current defaults.
- `resolve_lua_duplicate_timeline`: copy an existing timeline with its clips and
  frame rate to a new exact name, verifying the original layout is unchanged.
- `resolve_lua_append_clip`: append already imported media to one uniquely
  named timeline, either whole or with both source frame bounds supplied.
- `resolve_lua_set_clip_properties`: set and read back clip audio gain, or
  video zoom, position and opacity. Uses one-based track/item indices and
  requires the expected source path. This is not noise removal or automatic mixing.
- `resolve_lua_privacy_blur`: add a bounded native blur to screen V1 for one or
  more separate frame intervals; preserves higher camera tracks and AV timing.
  Requires exact clip/source guards and a fresh composition on a timeline copy.
- `resolve_lua_privacy_blur_batch`: preflight 1–100 distinct screen targets, then
  apply their masks with one backup and one whole-timeline consistency check.
- `resolve_lua_add_subtitles`: import UTF-8 SRT within a media root, reject
  overlapping cues or any existing AV/subtitle items, verify cue count and first/
  last timing. This provides captions, not arbitrary designed title templates.
- `resolve_lua_prepare_render`: queue a 1080p H.264 MP4 using the installed
  `YouTube - 1080p` preset, with audio and burnt-in subtitles. Creates a new
  `renders/<request>/video.mp4` destination. Optional `output_directory` chooses
  an existing parent inside the session media roots (for example `G:/Videos`);
  the result is `<output_directory>/render-<request>/video.mp4`. Preparation
  reserves a new folder, replay reuses it, and a non-empty or replaced folder
  blocks start. Existing videos are never intentionally overwritten.
  Selected parents require the session capability `render_output_directory_v1`;
  older sessions reject this option before creating a request or receipt. The
  acknowledgement also distinguishes selected-directory preparation from older
  native modules, so a legacy default-directory job is never reported as an export.
- `resolve_lua_start_render`: start only a job prepared by this running bridge.
  Returns `accepted` before dispatch, not running or complete. The acceptance
  export precedes StartRendering because rendering blocks exports.
  A stopped/restarted Lua loop loses job ownership.
  Rechecks the queued job's timeline/destination and refuses a non-empty output
  directory. A successful replay returns its receipt without starting again.
- `resolve_lua_get_render_status`: poll that owned job. It
  returns `awaiting_status` after an accepted start when no fresh response
  arrives. This does not claim the job is running: an unavailable bridge is also
  possible. Never resubmit start. A completed response requires a non-empty
  output whose first video frame and first audio frame
  (when an audio stream exists) decode successfully.
  This is not a full visual/audio quality inspection.
- `resolve_lua_cleanup_responses`: preview eligible response files by default;
  `confirm=true` removes them only after an acknowledged stop, with no pending
  writes. Keeps every backup, render, receipt, script and unrelated file.
- `resolve_lua_get_timeline_summary`: read live API counts for video, audio and
  subtitles, timeline bounds, subtitle first/last frame and frame rate. This avoids
  treating an exported project snapshot as a complete view of unsaved timeline state.
- `resolve_lua_reload_modules`: reload only the fixed trusted `editing.lua` and
  `finishing.lua` and `sync.lua` installed in this session by the operator. Accepts no source,
  paths or arbitrary actions. Keeps job ownership, handled IDs and the original
  deadline. Does not clear receipts or allow uncertain writes to be retried.
  Rendering blocks reload; a failed load keeps the previous dispatcher.

## Editing safety and replay

Each edit requires `expected_project_id` from `resolve_lua_get_project`, explicit
`confirm=true`, and a stable `idempotency_key`. Names are exact; ambiguous assets
or timelines are rejected. Media paths are normalized, checked for existence
and confined to approved session roots. No arbitrary source code is accepted.

Before the editing API call, Lua checks the open project and rendering state,
saves it, and exports `<request>.before.drp`. Failed backup blocks the edit.
Afterward Lua verifies creation/import/new timeline-item IDs and source identity,
saves the project, and exports `<request>.ok.drp`. Render start skips the post-save
while rendering; preparation saves before starting the job. The client validates response
and backup project IDs before recording success.

Append selects the named timeline, adding after the last video/audio item;
subtitle tails do not move the AV insertion point. It does not insert,
overwrite or delete existing clips. `start_frame` and `end_frame` map to the
Resolve API's inclusive source-frame bounds, not timeline frames. Converted
duration depends on source/timeline frame rates. Both must be provided or both
omitted. Source range must fit the imported clip's reported frame count.

Successful receipts survive MCP client restarts in the **same session directory**.
Reusing the same key/arguments returns the recorded result without resubmission;
reusing a key with different arguments is rejected. This is not global
deduplication across newly prepared session directories.

A timeout, failed post-check/save, or missing response can mean a partial edit.
Its receipt stays `pending`, blocking all further writes in that session. Read
tools and stop remain usable. Do not create a new key/session to bypass this:
inspect the project and retained backup first. Known preflight failures are
recorded as `rejected`. There is no automatic rollback or uncertain-write replay.

Newly prepared loops have **no session expiry**. They run cooperatively until
an acknowledged stop, a Lua/API error, or Resolve exits. The two-hour cutoff in
older copied scripts was a bridge policy, not a Resolve restriction. Updating
the package or reloading editing modules does not replace an already running
loop. Prepare a fresh runtime and bootstrap it once to activate this change;
stop any old running loop first. Do not bypass pending write receipts when
migrating: reconcile uncertain edits against the project and backups first.

An MCP client may disconnect and reconnect to the same **running** session:
the Lua request-ID replay set, owned render jobs and durable client receipts
remain intact. Individual requests still expire. No network listener, wider
media roots, automatic write retry, auto-start or unattended restart is added.
The local mailbox must remain writable only by trusted local users/programs.

For an acknowledged stop, keep a saved project open: the protocol confirms
shutdown by exporting its response. An export failure does not acknowledge or
stop the loop. After stop, error or Resolve restart, reconcile pending writes
and prepare a fresh session; never restart an old loop and assume its in-memory
replay/job state survived. The client refuses reuse of acknowledged stopped
sessions. No idle pings or project exports are needed to keep a session alive.

The Lua console prints start mode and an explicit stop reason
(`acknowledged_stop` or `error`). A killed Resolve process cannot print a final
reason. A response timeout alone does not prove expiry or a stopped loop;
export errors and a busy Resolve can also cause it. Simulated-clock Lua tests
cover requests beyond two hours, replay/token/expiry guards, project reopening,
failed stop exports and error cleanup. A real long-duration Resolve soak remains
a separate acceptance check.

## Protocol and limits

An opt-in [experimental Windows startup launcher](lua-startup.md) is available
for a future cold-start test. Its `.scriptlib` route is not yet live-verified on
the current Free installation; manual bootstrap remains the verified path.

The Python client publishes one atomically replaced `request.lua`, with a
random request ID, session token, fixed action and expiry. All string values
are byte-escaped literals. Tools accept no Lua source or arbitrary action.
The local mailbox is **trusted executable input**: do not edit it or permit
untrusted programs/users to write there. This prototype is not a sandbox or
an authenticated multi-user service.

Lua polls with `dofile` and `bmd.wait`; the injected global `resolve` provides
API access. `Resolve()` and `bmd.scriptapp("Resolve")` returned nil in our
console test. Normal `io`, `require`, `package`, and UIManager were unavailable.

Each response exports the current project under its unique request ID. The
client waits for a complete ZIP and reads only the bounded identity header of
`project.xml`; no archive members are extracted. Later Resolve configuration
elements are not standard XML, so the full project graph is not parsed.
This export layout is version-specific and is not a stable public API.

Exports can be large/slow. Responses above 512 MiB or identity XML above
16 MiB are rejected. They remain locally for diagnosis and may contain private
project metadata. No source media is included. Response cleanup is explicit,
never automatic. Backups and final videos still consume disk space and require
a separate retention decision. A new directory is created per render, not per
editing command. Do not remove an entire session unless its backups/renders
are no longer needed. A crashed client can leave `client.lock`;
remove it only after confirming no client owns the session.

Read-only tools do not call SaveProject or modify project contents. Editing
tools save and export before/after the mutation. General effects, deletion,
track layout, transitions and the production rough-cut workflows are not
connected to this bridge. Read-only exported state may differ from unsaved UI
edits. A missing project, failed export or stopped loop yields a timeout. Fixed
editing error codes use separately correlated DRP filenames. Render preparation
returns a bounded job-ID token in the response filename; status uses a fixed
allowlist of tokens. There is no
heartbeat or general-purpose result channel.
Concurrent requests are rejected instead of overwriting another request.
On Windows, a reader can briefly block atomic mailbox replacement. Publication
retries within the request deadline using the same request ID and payload; it
never generates or replays a new editing operation.

## Live acceptance, 2026-09-17

The initial read-only MCP stdio handshake advertised three tools.
`ping → get_project → ping` returned pong, the expected open project's name/ID,
and pong through three separately correlated exports. No GUI input occurred
between these calls. MCP stop returned `stopping`; a subsequent ping timed out,
confirming that the loop no longer served requests. The earlier bounded diagnostic also served two separately
published canary requests after one launch.

The protocol-2 editing acceptance test used an existing disposable test project
and a locally generated 48-frame, 24 fps video. A wrong-project edit was rejected
with `PROJECT_CHANGED` before mutation. Import, empty-timeline creation and a
trimmed append (source frames 12–35) all completed through MCP. Each repeated
request returned `replayed=true`; there were exactly three baseline backups.
Independent inspection of the exported timeline found one video item, source
in-point 12, and timeline duration field 23. Frame-rate conversion was not
independently measured.
This verifies creation and no duplicate append; it does not independently prove
every frame-rate conversion or the source end-frame readback. Audio-only fixtures were not tested in that run. Later protocol-4 acceptance
below covers whole-clip mixed audio/video append. The loop was stopped after testing.

Protocol-3 live acceptance progressed after a manual bootstrap. MCP ping and
project identity succeeded, followed by import of a synthetic three-second stereo
AV fixture, timeline creation, whole-clip append, audio gain -6 dB and video zoom
1.2 on both axes. All five writes returned verified success and retained backups;
no desktop input occurred between these requests.

SRT import returned `VERIFY_FAILED` during immediate readback. The target sequence
export initially showed no subtitle, but a later live API check returned
`SUBTITLES_EXIST`. The export-based conclusion that no subtitle was added was
therefore incorrect: exported state cannot settle a pending write outcome. The
original reviewed partial-failure receipt and backups are retained; no duplicate
caption append was executed. Exact cue timing remains pending live summary.
An unrelated existing Fusion composition also changed its exported modification
timestamp, so whole-project byte identity is not claimed.
A subsequent render preparation returned `VERIFY_FAILED`; the next diagnostic
revision isolated `RENDER_SETTINGS_FAILED`. Preset and MP4 format were present
and accepted; no new job or completed video was confirmed. Both loops were stopped.

Protocol 4 acceptance used a fresh runtime and the same disposable project.
Live summary exposed why captions initially failed: Resolve placed SRT after
existing AV despite recordFrame. The supported order is therefore **create empty
timeline -> add SRT -> append AV -> gain/framing -> prepare -> start -> status**.
Non-empty AV timelines reject caption import before mutation. A fresh timeline
returned one video, one audio and one subtitle, 72 frames at 24 fps, with the
caption at relative frames 12-60 (0.5-2.5 seconds).

Render settings were isolated: ReplaceExistingFilesInPlace=false was rejected
in single-clip mode. It is omitted; the client creates a fresh output directory
and checks emptiness before start. The first successful render start could not
export its acknowledgement during rendering. Its pending receipt was reviewed
against a later owned-job complete status and decoded output, without restarting
that job. A bounded cooperative wait now allows short jobs to finish before the
acknowledgement export. A separately prepared validation job then completed
prepare/start/status with all acknowledgements. Module reload preserved owned
jobs; successful start replay did not launch again.

The output is 1920x1080 H.264/AAC MP4, 72 video frames, container duration 3.008 s.
Decoded picture inspection showed the burnt-in caption; audio RMS measured
-6.037 dB relative to the original tone, matching the requested -6 dB. Gain and
zoom 1.2 also passed native property readback. User source recordings were not
used or modified. Local acceptance logs, receipts, backups and outputs are kept
outside the repository.

**Render acceptance:** the bridge exports `<request>.accepted.drp` after the
backup and all job checks, immediately before calling StartRendering. The client
validates both project identities and records a durable accepted result. Receipt
status `completed` means the dispatch request was acknowledged; its result status
`accepted` and `completion_verified=false` explicitly do not certify execution
or completion. Replaying that key returns acceptance without calling Resolve.
If Resolve rejects the start, the owned job remembers failure for the next status.

A missing status reply after acceptance returns `awaiting_status` with the last
confirmed state and instructions to poll, never a fabricated running/complete
state. Rendering blocks exports, but a stopped/unavailable bridge can cause the
same symptom. There is no continuous percentage progress; completion requires a
fresh owned-job response and decodable output. Missing start acceptance still
leaves an uncertain receipt and blocks writes. A crash between acceptance and
StartRendering cannot safely be distinguished from an uncertain dispatch; do not
restart automatically. Keep the same session and inspect if status never returns.
Older copied finishing modules retain the old start behavior. Update the fixed
modules and reload an active protocol-4 session, or prepare a new session.

A subsequent five-minute synthetic AV test passed with the console closed:
accepted start -> awaiting_status during render -> completed native status with
1920x1080 H.264/AAC output (300.010667 seconds, 15,768,358 bytes). A repeated start
returned the accepted receipt with replayed=true, with no second render.
Full output decoding checked all 7,200 video frames and the complete audio stream.

After the operator closed the console, a fresh real MCP ping returned pong and
a live timeline summary returned the expected one video/audio/subtitle item,
72-frame timeline and subtitle bounds 12-60. No keyboard or mouse input was used.
This verifies read requests with the console closed, not a long render.

Remaining acceptance: live cleanup preview and a long-duration run of the
current persistent loop. Automatic console input remains unreliable.

This does not verify general editing, no-project startup, every Resolve version, or application focus
behavior under every concurrent user activity. See the manual integration
matrix for the exact scope of the earlier console project-creation test.

## Synchronized pair assembly (local development)

The typed `resolve_lua_append_synced_pairs` tool supports batches of 1-25 groups.
Each group creates screen V1, camera V2 and screen master audio A1, links all three
items, then verifies native source identity, track, position, duration, source
bounds and links. The same ordered cut-map drives all three placements. Existing
items are not deleted or overwritten. A partial failure blocks further writes.

Two group shapes are supported:

- Cut group: screen_path, camera_path, screen_start_frame, camera_start_frame,
  frame_count. The public covered range is inclusive start..start+count-1.
- Full pair: screen_path, camera_path, camera_delay_frames (0..600). Preserves
  whole source clips, including unmatched edges; the camera begins later by that
  many timeline frames. Sessions follow one another at the latest AV end.

All source and timeline frame rates must match. Create an empty timeline, wait
for its acknowledgement, then call `resolve_lua_set_empty_timeline_fps` with
`frame_rate=60` before using 60 fps sources.
Other accepted integer rates are 24, 25, 30 and 50; no fractional conversion or
speed adjustment is performed. Camera composition is unchanged.

Resolve Free 21.1 live evidence showed different source-end conventions for
explicit ranges and whole clips. A requested 1200-frame range required native
endFrame=start+1200; item start/end duration and source readback were verified.
Whole-clip native source ends were inclusive. Some MKV container/AAC durations
extended one frame beyond the source video end; only that observed one-frame
padding is allowed for whole clips, with exact timeline duration still checked.
This is version-specific, not a general assertion about every Resolve build.

`resolve_lua_get_item` reads native source/timeline bounds and linked-item count
for an exact track/item selector with expected source-path and project checks.
`resolve_lua_preview_start` selects Edit, the named timeline and its start timecode.
Optional `preview_frame` selects an absolute frame inside that timeline for reviewing
a particular join (integer 24/25/30/50/60 fps timelines only), with native readback;
it never starts playback. Both use fixed typed operations, not arbitrary code.

Active protocol-4 sessions can load this addition without console input: update
editing.lua, finishing.lua and sync.lua from the trusted checkout, then reload.
The envelope reuses existing fixed actions; old modules reject the new argument
shapes. New prepare copies all three modules. Do not mix updated editing.lua with
a missing sync.lua. No new bootstrap or deadline extension occurs on reload.

Live synchronized assembly was checked through native readback and playback.
Keep source-specific offsets, cut maps, recordings and diagnostic reports outside
version control. Retain backups and uncertain-write receipts for recovery.

# Short review renders

`resolve_lua_prepare_render` accepts optional `start_frame` and `end_frame`.
Both are absolute timeline frame positions, with an exclusive end. Without
them the existing whole-timeline behavior is unchanged. Bounds must lie inside
the named nonempty timeline. The bridge converts the exclusive end to Resolve's
inclusive MarkOut, verifies queued MarkIn/MarkOut and checks them again before
dispatch. A range mismatch never authorizes starting a whole-timeline render.
Use this to review a local edit without rendering a multi-hour timeline.
The acknowledgement also carries the verified bounds. A legacy whole-timeline
acknowledgement cannot establish ownership of a requested range job, so the
client refuses to start it. Inspect any resulting uncertain queued job before
continuing. Reload the updated trusted finishing module before using ranges in
a running protocol-4 session.

# Circular camera mask (experimental)

`resolve_lua_circle_mask` applies one fixed Fusion graph to a video item selected
by timeline, one-based track/item and expected source path. It uses the existing
project guard, pre-edit export and idempotent receipt. Existing compositions are
rejected rather than overwritten. No caller-supplied code or composition file is
accepted. The graph merges MediaIn over an alpha-zero Background through an
Ellipse mask, then connects MediaOut. Center coordinates are normalized (Fusion
Y points upwards); diameter is relative to image width. Equal Ellipse Width and
Height produce a circle on square-pixel footage. Pan/Zoom are separate commands.

`resolve_lua_get_item(..., inspect_circle=true)` reads composition/node counts,
output connection and mask diameter without modifying the clip. A failed write
can have applied partially: inspect before resolving a pending receipt; never
blindly replay it or delete a composition to retry.

Live Free 21.1 testing confirmed composition creation, transparent compositing
and a short native H.264 render with corrected circular dimensions. A local
reference composite alone is never proof of native acceptance.

`resolve_lua_set_empty_timeline_fps` repairs an existing empty timeline after
backup, refusing all nonempty timelines. It opens Edit before switching and
configuring the timeline. Read native state to reconcile an uncertain creation
before using this recovery operation.

**Runtime limitation:** combining creation and an immediate FPS change hung in
live testing. The combined operation is deliberately rejected; creation keeps
the project defaults and FPS configuration is a separate guarded request on an
acknowledged empty timeline. Recovery of an existing empty timeline passed live
testing. Do not retry uncertain creation, clear its pending receipt or restart
repeatedly. Preserve the pre-edit export and reconcile actual native state after
authorized recovery. Short acceptance tests do not prove every workflow/build.

A subsequent live run also hung during a **separate** FPS-setting request on a
new empty timeline. Separation alone is therefore not a reliable workaround.
Do not repeatedly try rate changes or restart Resolve automatically.

`resolve_lua_duplicate_timeline` offers a different, experimental assembly path:
copy an existing timeline with the desired FPS, then append to the copy. It
retains all existing clips, including an approved opening; it never clears the
source or creates an empty template. Exact destination names must be new. A
saved backup precedes duplication, and readback checks separate timeline/item
identities, equal FPS/AV layout/source bounds/link counts/Fusion counts, and
unchanged source identity/layout. This structural check does not replace visual
review of copied effects. A timeout or verification failure remains uncertain;
inspect before any retry. Live Free 21.1 duplication preserved a 60fps approved
opening, its selected derived video source and circular Fusion composition.
This new action requires a freshly prepared bridge session; reloading only the
editing modules cannot update an older bridge's action allowlist.

Native source-frame getters can truncate a 60fps MKV in-point by one frame even
when the actual cut is correct. Synchronized assembly accepts that specific
reporting discrepancy only if source start/end times and the subframe left
offset independently match the requested frames within 0.0001 frame. It does
not shift the cut or tolerate a real one-frame timing error. Item readback also
returns source times in microseconds and left offset in millionths of a frame;
the original integer getters remain visible. Older module responses still parse.
Failed partial assemblies stay uncertain until inspected; never replay them.

Subject-aware background blur is **not** an MCP feature. A separately authorized
local RVM ONNX experiment is outside the repository/runtime dependency contract;
model download and short previews do not imply full-video processing or quality
approval. Source media must stay intact. Do not substitute a whole-frame blur or
static subject mask for moving foreground isolation.

## Narrow opening ripple cut

`resolve_lua_ripple_cut_opening` makes a new named timeline copy and removes an
exclusive frame interval strictly inside its first linked V1/V2/A1 group. It
requires the source timeline to be active, exact expected timeline/group bounds,
both selected media paths and their independent source in-points. In particular,
a derived camera take may start at source frame zero while master audio and the
screen recording start later; never substitute the original camera offset.

This is an experimental, restricted operation, not a general split/delete tool.
It supports contiguous synchronized groups on exactly two video tracks and one
audio track, with no subtitle tracks or timeline markers. Locked/disabled tracks,
first-clip Fusion compositions, clip markers and unsupported geometry are refused.
The first group must contain plain clips; do not use it for retimed clips, grades,
audio effects or other first-clip processing beyond the copied Inspector properties.
Downstream Fusion compositions remain on their existing items in the copy.

After a saved backup and duplication, only the first three items are replaced by
left/middle/right segments using the selected sources and copied properties. One
native ripple deletion removes the middle linked group. Native readback verifies
the new duration and links, every downstream item's identity, source times,
properties, Fusion count and exact shift, and the unchanged original timeline.
The earlier timeline retains original take selectors; replacement segments use
the currently selected media directly. Always review a short native AV render.

The `ripple_cut` object requires `name`, `start_frame`, `end_frame`,
`expected_timeline_start`, `expected_timeline_end`, `expected_group_end`,
`screen_path`, `camera_path`, `screen_source_start`, and `camera_source_start`.
All timeline positions are absolute frames; `end_frame` is exclusive. Requests
use the usual project guard, confirmation, backup and persistent idempotency key.
Failure may leave a partial destination copy: reconcile it rather than replaying
under a new key. Existing sessions need updated `finishing.lua` and `ripple.lua`
followed by `resolve_lua_reload_modules`; a freshly prepared session includes both.

`resolve_lua_ripple_cut_group` extends the same guarded operation to a selected
interior synchronized group. Add `group_index` (one-based, equal on V1/V2/A1)
and `expected_group_start` to the cut object. The cut must be strictly inside
that group. Earlier items remain at their exact positions; later items retain
their identities and source bounds while shifting by the removed duration.
This preserves already edited openings without rebuilding the timeline.

The selected camera clip may contain the bridge's five-node circle composition.
Its native Fusion export is saved beside the request backup, then imported onto
the two retained camera segments. Node topology and circle geometry are checked
before and after; other selected-clip Fusion graphs are refused. Untouched
compositions remain on their copied timeline items. Source-frame and AV checks
do not replace a native render review: inspect the splice and its camera overlay.
Do not use this restricted operation for animated masks or other processing
beyond the supported circle and copied Inspector properties. The original
timeline and exported composition remain available after any partial failure.

## Local privacy blur

`resolve_lua_privacy_blur` adds a native Fusion rectangle and strong blur to one
existing V1 screen item. Work on a verified timeline copy: the operation requires
the active timeline, exact source path and clip bounds, and refuses an existing
Fusion composition. Separate camera tracks remain above the screen effect.

The `privacy_blur` object contains `expected_clip_start`, `expected_clip_end`,
`start_frame`, `end_frame`, `center_x`, `center_y`, `width`, `height`, and `strength`.
Frame positions are absolute timeline frames with exclusive ends. Rectangle
coordinates are normalized to the image and must stay inside it. Strength is
the native Fusion blur control (20–100), not a promised pixel radius.
Optional `additional_intervals` accepts up to 63 further `{start_frame, end_frame}`
ranges, strictly ordered and separated within that same item. The effect is off
in the gaps. The caller cannot submit arbitrary expressions or script text.

The usual project guard, confirmation, saved backup and persistent idempotency
key apply. Native readback verifies the graph, geometry and effect activation at
every interval boundary, and unchanged timeline/source bounds, links, Inspector
properties and untouched compositions. `resolve_lua_get_item` with
`inspect_privacy=true` returns native geometry, local first/last bounds, interval
count and an ordered interval checksum. The checksum starts at zero and folds
each local start/end with `(checksum * 65599 + frame) % 2147483647`. It is a
compact consistency check, not a cryptographic proof. Keeping the response token
bounded avoids Windows filename limits even for 64 intervals.
A partial failure must be reconciled before any retry under a different key.

Readback proves configuration, not privacy. Inspect a native render at its full
output resolution, including opening/closing animations, scrolling and tooltips.
Use adequate temporal padding and verify camera clarity; do not claim a whole
video was audited from sparse screenshots. Source analysis and private previews
belong outside Git. Fresh sessions include `privacy.lua`; existing protocol-4
sessions can copy the updated `finishing.lua` and `privacy.lua`, then reload
modules without taking over the keyboard or mouse.

For multiple screen items, `resolve_lua_privacy_blur_batch` accepts `privacy_batch`
entries with `item_index`, `expected_media_path` and the same `privacy_blur`
object. Every target is preflighted before the saved backup and first mutation;
duplicate selectors are refused. A single whole-timeline comparison avoids
repeatedly reading every untouched item for each mask. This is not transactional:
a native failure may leave some targets changed. Keep the backup and receipt,
inspect each target, and never blindly repeat the batch under a new key.

## Derived video takes

`resolve_lua_replace_video_take` selects an already imported derived video file
on one existing timeline item. Both original and derived paths must be allowed.
The replacement must match the item duration and timeline FPS. Existing take
selectors or Fusion compositions are refused. The original take is retained;
this does not replace the media-pool source or change other timelines. A saved
backup precedes the operation, and native readback verifies selected source,
item bounds and linked-item identities/bounds. Do not finalize or delete the
original take automatically. Audio remains on the existing linked master track.

This accepts finished media; it does not run matting or install/download models.
Review derived-media contour quality, timing and color before use. A partial
failure remains uncertain and must be inspected before another write.

### Close Resolve on explicit request

`resolve_lua_quit(expected_project_id, idempotency_key, confirm=true)` saves the
current project, exports a backup, acknowledges acceptance, then calls the native
Lua `Resolve:Quit()` API. It refuses a different project or an active render.
This is distinct from `resolve_lua_stop`, which only stops the bridge.

Use a fresh runtime prepared with quit support; module reload cannot update an
already running bridge loop. Reuse the same idempotency key after uncertainty;
never automatically submit a new quit request. `status=accepted` and
`completion_verified=false` mean the request was acknowledged before Quit, not
that the process has exited. Verify process exit independently. No force kill,
keyboard input, or mouse control is used. Native live shutdown remains to be
validated on the next explicitly requested application close.

### Ripple across consecutive synchronized groups

`resolve_lua_preflight_ripple_span` checks a proposed cut without saving or editing.
`resolve_lua_ripple_cut_span` performs the same checks, saves/backs up the project,
then works on a new named timeline copy. Provide 2–20 contiguous groups with
explicit indices, timeline bounds, source paths and source in-points. The cut
must begin inside the first group and end inside the last group.

The supported layout is linked, contiguous V1 screen / V2 camera / A1 audio.
Only affected groups are rebuilt. Boundary camera circles are transferred using
native Fusion export/import. Canonical screen privacy intervals are intersected
with each retained source range and rebased to the new clip's local time; empty
intersections need no mask. Untouched downstream items keep their identities,
properties, source ranges, links and local privacy intervals after the ripple.
Unsupported affected graphs/layouts are refused. Audio items do not expose the
video take-count API and are checked through their media/source geometry.

A failed write can leave a partial copy: inspect the receipt and backup before
any new mutation. Keep a private cut map for subsequent privacy review; this
operation does not complete that review. Integration was verified on a native
60fps two-group cut with screen privacy masks and camera circles; broader layouts
are covered by deterministic tests, not claimed as live-verified.

### Temporarily hide the camera when it obscures screen content

`resolve_lua_camera_visibility` duplicates a timeline and applies explicit hide
intervals to canonical V2 circle compositions. The caller supplies destination
name, expected timeline/clip bounds, camera media paths and absolute exclusive
intervals (up to 128 clips, 64 intervals each). The tool does not detect console
content: the caller must review and prepare that plan separately.

The operation requires an unanimated, fully visible camera blend and a transparent
background. It changes only `Merge1.Blend`, keeps the circle geometry, and checks
visibility immediately before/inside/after each interval. A native layout snapshot
checks source ranges, clip properties, links, enabled state, track/item counts and
timeline duration before/after; the original timeline is preserved. Saves and a
project backup precede changes. Unsupported graphs fail closed. Failed writes
must be reconciled rather than blindly retried.

Use `resolve_lua_get_item(..., inspect_camera_visibility=true)` on V2 for a fresh
canonical-expression, interval-checksum and boundary-value readback. Camera hiding
does not replace screen privacy masks or complete a privacy audit.

### Ripple cuts with camera visibility and batched pauses

`resolve_lua_ripple_cut_batch` removes explicit disjoint intervals inside identified
V1/V2/A1 groups on one new timeline. It accepts up to 128 cuts, at most 64 per
group. A caller must review pauses using both sound and screen content; silence
alone is not evidence that a section can be removed. No automatic pause detector
is included in this tool.

For reviewed editorial assemblies, set `ripple_batch.allow_group_edges` to `true`
to allow cuts touching either group boundary or removing a whole group. This
opt-in allows up to 256 identified groups and 512 separated cuts, still at most
64 per group. Without it, the existing strictly interior limits remain in force.
Zero-length pieces are never inserted, and removing all timeline content is
refused. Every affected group still requires exact media paths, source origins
and timeline bounds; omitted plan groups remain unchanged. A keep list should
first be intersected with the current source timeline, then converted to its
complement. This avoids restoring material already removed in earlier edits.
Batch writes default to a bounded 600-second response wait because native clip
and Fusion transfers can take several minutes; other requests keep their
120-second ceiling. A timeout still means an uncertain outcome: inspect the
same command's late response and receipt, never repeat the cut with a new key.

`resolve_lua_verify_ripple_batch` accepts the same source timeline and batch plan
without confirmation or an idempotency key. It independently reads the existing
destination, checks all retained geometry, source ranges, properties, links and
canonical privacy/camera graphs, and returns `verified` only on agreement. It
does not create, select, save or repair a timeline. This verifies edit mechanics,
not editorial quality or the completeness of a privacy audit.

Single-group, cross-group and batch cuts clip/rebase canonical camera visibility
to retained source frames. Native timeline duplication can omit `Merge1.Blend`
expressions: the bridge explicitly restores them on the copy. Batch cuts also
rebase canonical privacy intervals on split V1 items, keep untouched groups and
links, and check source ranges, properties, duration and graph state after the
ripple. Originals and the pre-write backup are preserved. Noncanonical graphs,
markers or unsupported layouts are refused.

`resolve_lua_verify_ripple_cut` audits an existing single-group cut against its
original, without editing. `resolve_lua_repair_ripple_visibility` restores only
camera visibility on an existing cut copy after verifying layout, source ranges,
properties, links, circle geometry and unaffected privacy masks. It requires the
copy to be current and makes a backup before changing expressions. It cannot
repair arbitrary layout differences or a cut through a V1 privacy composition.
An uncertain cut must first be reconciled against its receipt, backup and native
readbacks; never rerun the cut with a new key to get past a failed verification.
