# Architecture

## Scoped Windows Fairlight controls

The Free marker channel remains a typed native data transport. Physical input
selection and the Fairlight controls absent from the SDK use separate fixed
host adapters; callers cannot supply scripts, coordinates, selectors or keys.
`mcp_server/fairlight.py` registers seven tools backed by `FairlightUIService`
and `WindowsFairlightAdapter`, both gated by `--enable-windows-recording-ui`.

The service fences project identity and exact track name/index against fresh
native reads, requires mono tracks for input patches, and shares mailbox
ownership with the existing input-device service. Before a first mutation it
checks stopped transport and reuses the existing guarded native save/export/page
operation for a project backup. A pending GUI receipt prevents a replacement
write. Recovery with the same key invokes only the corresponding getter and
retains original and reconciliation evidence separately.

The shipped PowerShell driver accepts six semantic JSON verbs. Core helpers bind
the Resolve process, project, selected timeline, stopped transport and verified
per-monitor DPI context. UIA identifies accessible controls; scoped GDI/local
Windows OCR and positive raster profiles identify painted mixer/track controls.
Unknown or ambiguous geometry/state fails closed. The patch module accepts only
exact accessible table/list source and destination assignment evidence. It
never infers a route from a source choice. EQ/Dynamics and patch windows are
reopened; track arm is reacquired. All global transport targets are excluded.

New Fairlight controls remain `live_pending` until manual Windows acceptance.
`available` expresses host opt-in/platform only. Reopened state does not prove
DSP audio quality or disk persistence. Save-menu dispatch is reported separately
from read-only recovery (`save_status`), and `persistence_verified` remains false.
No operation starts recording; inserts/other effects remain outside this adapter.

## M54 technical take-selection boundary

```text
allowlisted full-file or uniformly bounded segment candidates
 └─ fixed bounded decoder sampling
      ├─ technical video/audio metrics
      ├─ deterministic ranking + limitations
      └─ pending_review selection artifact
           └─ immutable human approve/reject (no timeline write)
```

The analyzer is provider-neutral and local. ResolveBridge is not involved.
Source paths are validated before decoding and omitted from durable reports.
Scoring weights, sample limits and observations are code-owned; MCP cannot pass
decoder arguments or executable content. Segment candidates carry explicit
source ranges of at most 300 seconds; repeated ranges may reference one file,
while mixed full-file/segment requests are rejected.

Script-aware M54.3 adds a separate path: fixed-policy local transcription sees
only each bounded audio range, compares normalized word order against one
caller-provided reference and combines that score with technical measurements.
Durable artifacts contain only hashes and aggregate metrics, not source paths,
reference text or raw transcript text. The result still stops at human review.

M54.4 composes canonical approved reviews into one ordered immutable sequence.
The sequence carries selection/review hashes, candidate fingerprints, source
ranges and bounded scores, but no source path. It is a provider-neutral handoff
with `timeline_modified=false` and `apply_supported=false`; M55 must define any
future confirmed application against already verified provider primitives.

M55.1 resolves the portable/path-redacted handoff through a two-artifact local
binding. Public MCP state contains only sequence/file identities; a private
runtime artifact holds allowlisted absolute paths and is available only to
in-process orchestration. Neither artifact imports media or enables apply.

M55.2 consumes that private boundary only in process. It revalidates file
identity, reads duration/FPS/resolution through PyAV and emits a deterministic,
path-redacted sequence in seconds. Output ranges are contiguous and preserve
approved order and source ranges. Mixed FPS/resolution becomes an explicit
warning. No provider is contacted; target-timeline frame mapping remains a
future capability-gated orchestration step.

M55.3 performs that first live binding through the existing provider-neutral
`editing_metadata(timeline_id, [])` call. The Resolve adapter uses only the
already documented `Timeline.GetSetting`, `GetTrackCount` and `GetName`
surface; an empty asset request skips Media Pool traversal. Core converts
inclusive source bounds at source FPS and contiguous output positions at the
verified target FPS. The result remains preview-only and contains no imported
asset identity, so apply is deliberately unsupported.

M55.4 joins the M55.3 mapping with bounded `media_pool_items()` discovery.
Source fingerprints deduplicate repeated sequence ranges, while Media Pool
items remain identified only by canonical asset ID, name and logical folder.
Because that readback intentionally exposes no path or file fingerprint, a
same-name match is ambiguous and forces review instead of silent reuse. The
preview hashes the sorted Media Pool snapshot and performs no import.

M55.5 keeps filesystem paths inside the private binding boundary. The
provider-neutral importer recomputes the reviewed plan, calls the existing
backup-backed Resolve import gateway once, verifies editing metadata and Media
Pool readback, then persists a path-redacted receipt. Its deterministic receipt
and provider idempotency keys make interrupted retries safe; no timeline method
is called.

M55.6 joins that receipt with a freshly recomputed M55.3 mapping and private
audio-stream inspection. Core requires an empty source timeline and an unused
target name, then orchestrates only existing provider-neutral duplicate, track
and batch-range insertion boundaries. Resolve alone maps V1/A1 placements to
documented `DuplicateTimeline` and `AppendToTimeline` clipInfo. Sanitized
per-step receipts allow recovery without storing source or backup paths, and
final readback proves both the exact target items and unchanged source.

M55.7 consumes only the canonical M55.6 receipt through its live-verifying
reader. It recomputes V1/A1 counts, ranges, common origin and video contiguity,
then stores a deterministic path-redacted report. Audio processing, subtitles,
color and aesthetic judgement remain explicit unverified areas. A separate
immutable human decision binds to the exact report hash and performs no
provider write; receipts from older pipelines are not treated as evidence for
this different timeline.

M55.8 consumes only an approved M55.7 review and its freshly revalidated
report. A provider-neutral workflow creates a deterministic render plan, then
orchestrates the existing backup-backed prepare/status/start gateways. The MCP
boundary exposes only an allowlisted profile and safe filename stem; codec,
format, output root and Resolve preset remain code-owned. Durable receipts
redact configured paths, while dynamic status may return the verified managed
output path needed by the caller.

## M53 color discovery and preview

```text
packaged CDL preset
 ├─ schema-validated immutable catalogue
 ├─ applied M52 receipt + exact live timeline snapshot
 ├─ read-only documented color graph probe
 └─ deterministic preview (no Resolve write)
```

Core owns preset identity, receipt binding and target selection. ResolveBridge
only inspects documented TimelineItem/Graph APIs. Generated titles and audio
are excluded; arbitrary CDL values, LUT/DRX files, node selection and scripts
never cross the MCP boundary. A future apply slice requires separate live Free
evidence and reviewed duplicate-timeline semantics.

## M0 foundation

M0 establishes repository tooling and stable extension points:

```text
CLI
 └─ agent (configuration and platform paths)
     ├─ providers (editor capability adapters)
     ├─ transports (local communication adapters)
     └─ bridges (scripts running inside an editor)
```

The core package does not import Resolve modules. Paths are resolved from
platform environment variables at runtime, and packaged defaults contain only
portable placeholders.

## M1 bridge proof

M1 adds a read-only proof inside `bridges/resolve`:

- an internal script visible under `Workspace → Scripts → Edit`;
- the Resolve/Fusion context injected into the internal menu script, with the
  officially documented `DaVinciResolveScript.scriptapp("Resolve")` entry as a
  fallback;
- `ping`, heartbeat, current project name, timelines, and current timeline name;
- capability reporting that leaves untested operations as `unknown`;
- a filesystem transport with an explicit read-only action allowlist.

Bridge запускається вручну, після чого обслуговує queue в обмеженому
persistent lifecycle: максимум одна команда за ітерацію, heartbeat і state
перед/після команди, інтервал 0.5 секунди та allowlisted `stop_bridge` для
clean shutdown. Це не додає зовнішнього запуску Resolve або arbitrary code.
Responsive UI, repeated queue processing and clean stop підтверджено live у
Resolve 21 Free 21.0.3.7; інші версії та editions не вважаються перевіреними.

Live testing on Resolve 21 Free 21.0.3.7 established that the internal host
context works, while importing `DaVinciResolveScript` directly from that menu
script raises `ModuleNotFoundError`. The bridge records failures before retrying
with updated code, so this behavior remains observable rather than implicit.

## M2 external agent

M2 keeps transport mechanics and editor semantics separate:

```text
CLI
 └─ ResolveProviderClient
     └─ FilesystemCommandClient
         ├─ canonical JSON Schema validation
         ├─ atomic command enqueue
         ├─ bounded response wait
         └─ structured protocol/bridge/timeout errors
```

The CLI maps fixed subcommands to provider methods. It does not accept arbitrary
action names. `ResolveProviderClient` validates the shape of action-specific
results, while `FilesystemCommandClient` owns only provider-neutral envelopes
and transport behavior.

```text
ResolveBridge.py
  ├─ documented Resolve read-only API
  ├─ state/bridge.json
  ├─ commands/*.json → processing/*.json
  ├─ responses/*.json
  ├─ failed/*.json
  └─ logs/bridge.jsonl
```

## M3 MCP adapter

M3 adds a local stdio adapter without exposing transport internals:

```text
MCP client
 └─ mcp_server (fixed read-only tools)
     └─ AgentApplication
         └─ ResolveProviderClient
             └─ provider-neutral CommandClient
```

The MCP package does not import `agent.paths`, `transports`, or the Resolve
bridge. It accepts no arbitrary action names and exposes only the four M3 tools
listed in the specification. Blocking application calls run in worker threads
so the MCP event loop remains responsive.

## M4 safe editing

Write requests retain the same boundaries:

```text
MCP write tool
 └─ AgentApplication
     ├─ MediaPolicy (absolute path + allowed root validation)
     └─ ResolveProviderClient
         └─ validated command with create_backup=true
             └─ ResolveBridge
                 ├─ bridge-side media-policy validation
                 ├─ SaveProject + ExportProject
                 ├─ one documented Resolve mutation
                 └─ idempotency receipt
```

The bridge advertises write capabilities as `unknown` until a documented
operation succeeds in the running Resolve edition. Project restore remains an
explicit user action; the bridge never replaces an open project automatically.

Live validation on Resolve 21 Free 21.0.3.7 confirmed `ImportMedia`,
`CreateEmptyTimeline`, `AppendToTimeline`, and timeline `AddMarker`. Replaying
the same four idempotency keys returned the original results without creating
additional backups or duplicate edits.

## M5 rough-cut draft

M5 adds analysis and planning without expanding the Resolve mutation surface:

```text
MCP create_rough_cut
 └─ AgentApplication
     ├─ MediaPolicy (read-only path validation)
     └─ RoughCutPlanner
         ├─ PCM WAV envelope analysis
         ├─ audio-correlation synchronization
         ├─ long-pause detection
         ├─ canonical rough-cut plan validation
         └─ runtime/plans/<plan_id>.json
```

The plan is deterministic for the same inputs and parameters. Its contract
requires `pending_review`, `approved=false`, and `apply_supported=false`.
Neither the Resolve provider nor the bridge is called while creating it.
Video decoding is intentionally outside the core analyzer and remains a future
provider responsibility.

## M6 audio workflow

M6 keeps audio processing outside the Resolve provider:

```text
MCP clean_dialogue_audio
 └─ AgentApplication
     ├─ MediaPolicy (read-only source validation)
     └─ DialogueAudioWorkflow
         └─ PcmWavAudioProvider
             ├─ before analysis
             ├─ deterministic gain + peak guard
             ├─ derived PCM WAV
             └─ canonical before/after report
```

The reference backend supports uncompressed 16-bit PCM WAV and uses no shell
or editor runtime. It measures RMS dBFS rather than claiming standards-compliant
LUFS. Original assets are preserved, derived assets use a durable user video
directory, and reports remain under the local runtime directory.

## M7 delivery discovery

The first M7 slice is read-only:

```text
CLI / MCP resolve_get_render_options
 └─ AgentApplication
     └─ ResolveProviderClient
         └─ get_render_environment command
             └─ ResolveBridge
                 ├─ GetRenderFormats
                 ├─ GetRenderCodecs
                 ├─ GetCurrentRenderFormatAndCodec
                 ├─ GetRenderPresetList
                 └─ GetRenderJobList
```

Successful discovery records `render.discovery=true`, but leaves
`render.configure` and `render.start` unknown. No render setting or queue state
is changed by this slice.

The write slice remains intentionally narrow:

```text
MCP resolve_prepare_render_job
 └─ validate custom_name
     └─ backup project
         └─ fixed YouTube 1080p + MP4/H264 settings
             └─ AddRenderJob
```

The bridge derives the target directory from `USERPROFILE`; clients cannot
provide a path, codec, preset, filter, upload target, or executable command.
Successful preparation verifies `render.configure`, while `render.start`
remains unknown and unavailable.
The bridge temporarily opens the documented Deliver page for `AddRenderJob`
and restores the prior Resolve page afterward.
Live validation on Resolve 21 Free 21.0.3.7 confirmed one 1920x1080 MP4/H.264
job, one mandatory backup, and replay of the stored result without adding a
second job or starting rendering.

## M8 safe render execution

M8 separates status from start:

```text
MCP resolve_get_render_job_status(job_id)
 └─ GetRenderJobList + GetRenderJobStatus + IsRenderingInProgress

MCP resolve_start_render_job(job_id)
 └─ validate M7 preparation receipt
     └─ verify fixed live queue metadata and no active render
         └─ backup project + durable start reservation
             └─ StartRendering([job_id], False)
```

The start action cannot accept a path, preset, codec, upload destination, job
list, or all-jobs flag. A same-key replay returns its stored result; a different
key cannot restart a job with an existing `state/render-starts` record.
`render.start=true` is persisted only after Resolve accepts a live start.
Read-only status was live-validated on Resolve 21 Free 21.0.3.7 for the M7
1920x1080 MP4/H.264 job in `Ready` state with zero completion.
M11 subsequently live-validated the guarded start; the capability is persisted
as `true` only after Resolve accepts `StartRendering`.

## M9 verified export profiles

M9 keeps render configuration finite:

```text
resolve_get_render_options
 └─ GetRenderResolutions("MP4", "H264")

resolve_prepare_render_job(custom_name, profile)
 ├─ youtube-1080p-h264-v1 -> YouTube - 1080p -> 1920x1080
 └─ youtube-2160p-h264-v1 -> YouTube - 2160p -> 3840x2160
```

The bridge verifies the built-in preset and exact resolution before
`AddRenderJob`, explicitly applies documented dimensions, and stores the
selected profile in the preparation receipt. M8 start policy derives its
expected preset and dimensions from that receipt. Arbitrary dimensions,
presets, codecs, paths, and upload targets are not representable.

Resolve 21 Free 21.0.3.7 live discovery returned 3840x2160 for MP4/H.264 and
the `YouTube - 2160p` preset. Live write validation then added one matching
3840x2160 job after backup; replay returned the same job and backup without
adding a duplicate or starting rendering.

## M10 safe ranged clip insertion

```text
MCP resolve_insert_clip
 └─ validate IDs, ordered source frames, position, and track
     └─ resolve timeline + media asset
         └─ verify track exists and is unlocked
             └─ backup project
                 └─ AppendToTimeline([{fixed clipInfo}])
                     └─ TimelineItem bounds and track readback
```

The application contract is provider-neutral: `position_frames` is relative
to the timeline start. Only one video-only or audio-only range can be inserted
per command. Existing items are not moved, trimmed, split, disabled,
transformed, or deleted. Stable receipts prevent replay duplicates.

Live validation on Resolve 21 Free 21.0.3.7 inserted source frames 0..240
separately on V1 and A1 at the same position. TimelineItem readback reported
86400..86496 for both, confirming Resolve's 60 fps source to 24 fps timeline
conversion. Replays returned the original item IDs and backups; preflight
remained exactly one video item and one audio item.

## M11 render smoke test

```text
MCP resolve_verify_render_output(job_id)
 └─ documented live render-job status
     └─ require managed TargetDir and safe MP4 filename
         └─ pathlib file existence and non-zero size check
```

M11 uses the guarded M8 start operation against a dedicated 1080p job on the
four-second M10 timeline. Output verification is provider-neutral and
read-only: it reports completion, managed-path membership, existence, and
file size. It does not claim container validity, expected duration, audio
presence, or visual quality because media decoding is outside this milestone.

Resolve 21 Free 21.0.3.7 completed the live smoke job in 3262 ms. The
verification reported `CompletionPercentage=100`, `JobStatus=Complete`, no
render in progress, and a managed non-empty MP4 of 804100 bytes.

## M12 reversible clip enable state

```text
MCP resolve_set_clip_enabled(timeline_id, timeline_item_id, enabled)
 └─ enumerate video/audio TimelineItems by documented unique ID
     └─ verify item methods and unlocked track
         └─ backup project
             └─ SetClipEnabled(bool)
                 └─ GetClipEnabled readback + idempotency receipt
```

The action changes one boolean property only. It cannot address subtitle
items, accept arbitrary property names, move or trim an item, or delete
timeline content. A same-key replay returns the original result and backup.
Resolve 21 Free 21.0.3.7 live validation disabled the M10 V1 item, read back
`enabled=false`, replayed the same receipt and backup without another edit,
then restored the original `enabled=true` state with a separate backup.
Capability `clip.enable` is persisted as `true` only after a successful call.

## M13 safe current timeline selection

```text
MCP resolve_set_current_timeline(timeline_id)
 └─ resolve one existing timeline by documented unique ID
     └─ backup project
         └─ Project.SetCurrentTimeline(timeline)
             └─ GetCurrentTimeline ID readback + idempotency receipt
```

The action cannot accept a timeline name or index, create or delete timelines,
or change timeline settings. Its response contains both the previous timeline
summary and the selected timeline summary. Capability `timeline.select`
is persisted as `true` only after live validation succeeds.
M13 also adds documented unique IDs to read-only timeline discovery; this
closes the selection loop for pre-existing timelines without consulting old
write receipts.

Resolve 21 Free 21.0.3.7 live validation discovered all three timeline IDs,
selected M7 from M10, replayed the same result and backup without another
selection, and restored M10 with a separate backup.
After every successful write, the bridge refreshes its complete live state
before publishing the final heartbeat. This prevents cached project or
timeline metadata from describing the pre-write state.
The control M10 → M7 → M10 round trip confirmed that each cached heartbeat
immediately matched the completed selection without another bridge run.

## M14 bounded clip transform

```text
MCP resolve_set_clip_transform(timeline_id, timeline_item_id, fields)
 └─ validate the fixed provider-neutral transform subset
     └─ resolve one video TimelineItem by documented unique ID
         └─ verify unlocked track and timeline-dependent position bounds
             └─ backup project
                 └─ TimelineItem.SetProperty(fixed dictionary)
                     └─ GetProperty readback + idempotency receipt
```

The external contract exposes only optional position X/Y, uniform zoom,
rotation degrees, and opacity percent, with at least one value required. The
Resolve provider owns the fixed mapping to Pan, Tilt, ZoomGang, ZoomX, ZoomY,
RotationAngle, and Opacity. Clients cannot supply a Resolve property key,
expression, keyframe, or code. Position is additionally bounded against four
times the current timeline resolution.

Resolve 21 Free 21.0.3.7 live validation changed opacity from 100 to 90 with
exact `GetProperty` readback and a project backup. Same-key replay returned
the identical result and backup. A separate command then restored opacity
from 90 to 100 with its own backup. Capability `clip.transform` is persisted
as `true` after the successful call.

## M15 confirmed non-ripple clip deletion

```text
MCP resolve_delete_clip(timeline_id, timeline_item_id, confirm_delete=true)
 └─ destructive MCP annotation + application confirmation
     └─ command safety allow_destructive=true + create_backup=true
         └─ resolve one unlocked video/audio TimelineItem by ID
             └─ backup project
                 └─ Timeline.DeleteClips([item], False)
                     └─ verify item ID is absent + idempotency receipt
```

The public contract cannot express ripple deletion, multiple item IDs, or
linked-item expansion. All non-delete commands remain constrained to
`allow_destructive=false`.

Resolve 21 Free 21.0.3.7 live validation inserted a dedicated 24-frame video
item after the existing M10 content, deleted exactly that item with
`ripple=false`, and confirmed its ID was absent. Same-key replay returned the
identical result and backup. Final read-only preflight returned to one video
item and one audio item, proving the original M10 pair remained. Capability
`clip.delete` is persisted as `true`.

## M16 timeline item discovery

```text
MCP resolve_list_timeline_items(timeline_id)
 └─ validate one bounded timeline ID
     └─ resolve the existing timeline
         └─ enumerate documented video/audio tracks
             └─ validate item IDs, track readback, and frame metadata
                 └─ return provider-neutral item summaries
```

M16 supplies the canonical item IDs needed by enable, transform, and delete
operations without consulting historical write receipts. It is read-only and
does not expose Resolve objects, arbitrary properties, subtitles, or Fusion
state.

Resolve 21 Free 21.0.3.7 live validation discovered the synchronized M10 video
and audio items with distinct canonical IDs. Both reported duration 96,
timeline bounds 86400..86496, and source bounds 0..240. No project backup was
created, and capability `clip.read` was persisted as `true`.

## M17 Media Pool item discovery

```text
MCP resolve_list_media_pool_items()
 └─ require current project and Media Pool root
     └─ recursively enumerate documented folders
         └─ validate folder IDs and guard cycles/size
             └─ read MediaPoolItem ID and name
                 └─ return provider-neutral logical placement
```

M17 supplies Media Pool IDs for append/insert operations without consulting import
receipts. It intentionally omits filesystem paths, raw clip properties,
metadata dictionaries, and Resolve objects. Because documented `GetClipList`
also returns timeline entries in the live project, the provider-neutral
contract does not claim that every result is source media.

Resolve 21 Free 21.0.3.7 live validation discovered five items in the `Master`
folder, including the M10 source MKV with canonical ID
`e2c01761-f786-4b2c-866e-b682d2806eca`. No project backup was created, and
capability `media.read` was persisted as `true`.

## M18 single-run workspace snapshot

```text
CLI/MCP snapshot
 └─ one allowlisted get_workspace_snapshot command
     ├─ bridge and project state
     ├─ timeline list, current timeline, and current items
     ├─ bounded Media Pool item discovery
     └─ render discovery
```

M18 composes only existing read-only bridge functions and fails atomically if
a required section cannot be collected. It accepts no user-defined action
list, creates no backup, and performs no write. At the time of M18 it reduced
diagnostics to one invocation of the then one-shot bridge; M35 changes the
lifecycle separately without turning snapshot into a batch dispatcher.

The local Resolve 21 Scripting README describes external command-line access
as part of the DaVinci Resolve Studio scripting package. Since the supported
target is Resolve 21 Free and direct external import is not available on the
validated machine, the architecture retains the internal menu script.

## M19 bounded clip linking

```text
MCP resolve_set_clips_linked(timeline_id, item_ids, linked)
 └─ validate 2..16 unique canonical IDs
     └─ resolve every video/audio TimelineItem
         └─ reject any locked track
             └─ export project backup
                 └─ SetClipsLinked(items, linked)
                     └─ verify each selected pair with GetLinkedItems()
```

M19 provides explicit synchronization-group control without introducing a
generic timeline mutation surface. Idempotency receipts prevent duplicate
backups on same-key replay. Link and unlink are non-destructive, but still
modify project state and therefore always require a backup.

The local Resolve 21 API does not document direct TimelineItem move, trim, or
split methods. Those specification surfaces remain intentionally unimplemented
instead of being simulated through delete-and-reinsert operations that could
change linking, transitions, effects, or source timing.

## M20 safe timeline duplication

```text
MCP resolve_duplicate_timeline(source_id, new_name)
 └─ resolve source by canonical timeline ID
     └─ reject an existing target name
         └─ export project backup
             └─ Timeline.DuplicateTimeline(new_name)
                 └─ verify distinct ID, exact name, and project membership
```

M20 provides an isolated editing target for future reviewed workflows. It
does not select the duplicate, rename or mutate the source, or infer a name.
Stable idempotency receipts prevent repeated calls from creating additional
copies or backups. The bridge records the current timeline before duplication
and restores it through documented project APIs if Resolve changes selection.

## M21 explicit rough-cut approval

```text
MCP approve_rough_cut(plan_id, confirm_review=true)
 └─ derive plans/<canonical-id>.json
     └─ validate immutable rough-cut draft
         └─ compute canonical SHA-256
             └─ atomically persist separate approval record
                 └─ apply_supported=false
```

M21 keeps planning and review provider-neutral. It never accepts a filesystem
path, never mutates the deterministic draft, and never queues a Resolve
command. Existing approval records are returned idempotently only while their
stored hash matches the current valid draft; post-approval changes are
rejected.

## M22 rough-cut plan inspection

```text
MCP list_rough_cut_plans(limit)
 └─ scan canonical <plan_id>.json files only
     └─ validate draft and optional hash-bound approval
         └─ return bounded summaries without media paths

MCP get_rough_cut_plan(plan_id)
 └─ validate canonical ID, draft, approval, and SHA-256
     └─ return effective review state without writes
```

M22 makes persisted review state discoverable after client or process restarts.
It does not silently skip invalid canonical artifacts: contract or hash
failures are explicit. Unknown non-canonical JSON filenames and approval files
are not treated as plans.

## M23 audio report inspection

```text
MCP list_audio_reports(limit)
 └─ scan canonical <report_id>.json files only
     └─ validate each audio-report contract and filename identity
         └─ return bounded summaries without filesystem paths

MCP get_audio_report(report_id)
 └─ validate canonical ID, report contract, and filename identity
     └─ return the stored before/after report without media processing
```

M23 makes completed audio processing results discoverable after client or
process restarts. Both tools are local and read-only: they do not instantiate
the audio processor, queue a bridge command, or modify an artifact. Invalid
canonical reports fail explicitly; unrelated non-canonical JSON files are
ignored by listing.

## M24 local diagnostics bundle

```text
CLI davinci-agent diagnostics
 └─ load packaged or local config
     ├─ recursively redact known secret-bearing keys
     ├─ replace user-directory prefixes with environment placeholders
     ├─ summarize cached bridge versions and capabilities
     ├─ collect metadata for at most 20 recent failed commands
     └─ collect bounded sanitized excerpts from at most 5 logs
         └─ validate and atomically write diagnostics/*.json
```

The diagnostics service is provider-neutral and never queues a bridge command.
It accepts no output path and does not include media, render outputs, project
backups, raw command arguments, responses, or idempotency keys. Symlinked
runtime files are ignored. Automatic redaction is a safety layer rather than a
guarantee, so documentation requires human review before sharing a bundle.

## M25 filesystem transport audit

```text
FilesystemCommandClient.request(...)
 └─ validate command contract
     └─ atomically create logs/audit/<command_id>.json (submitting)
         └─ atomically publish command envelope
             └─ update audit state (pending)
                 ├─ validated response → success + duration
                 ├─ bridge error → safe error code + retryable
                 ├─ protocol error → BRIDGE_PROTOCOL_ERROR
                 └─ deadline → timeout + COMMAND_TIMEOUT
```

The initial record is mandatory and is written before queue publication. Later
updates are atomic replacements of the same command-scoped record, avoiding
shared JSONL append races between CLI and MCP processes. The record schema has
no fields capable of storing arguments, results, idempotency keys, paths, or
error messages. M25 covers Resolve filesystem transport commands only; local
workflow audit remains a separate application-layer concern.

## M26 local workflow audit

```text
MCP local tool
 └─ AgentApplication._run_local_workflow(operation, callback)
     └─ atomically create logs/workflow/<operation_id>.json (running)
         ├─ callback result → success + duration
         └─ exception → fixed error class + retryable
```

The production MCP composition root injects `WorkflowAuditLog`; tests and
future adapters can inject a compatible auditor or omit it. The allowlist has
exactly seven rough-cut/audio operations. Audit begins before media policy,
artifact validation, processing, or inspection and records no arguments,
artifact IDs, paths, results, or exception messages. Invalid operation names
cannot invoke the callback.

## M27 Windows CI matrix

```text
GitHub Actions / windows-latest
 ├─ Python 3.10 → editable install → pytest → CLI version
 ├─ Python 3.11 → editable install → pytest → CLI version
 ├─ Python 3.12 → editable install → pytest → CLI version
 └─ Python 3.12 quality
     ├─ Ruff
     ├─ mypy
     └─ installer PowerShell syntax parse
```

CI validates the provider-neutral package and bridge simulation only. Live
Resolve integration remains manual because hosted runners do not provide
Resolve, a project, or its internal scripting context. Workflow permissions
are read-only, checkout credentials are not persisted, and there are no
secrets, deployment, commit, push, or external scripting steps.

## M28 isolated installer lifecycle

```text
GitHub Actions / windows-latest / Python 3.12
 └─ create unique temporary sandbox
     ├─ copy tracked checkout files
     ├─ redirect APPDATA, LOCALAPPDATA, USERPROFILE, and pip cache
     ├─ seed pre-existing config and ResolveBridge
     ├─ install → import → CLI version
     ├─ install again → preservation checks
     └─ uninstall with explicit removal flags
         ├─ restore pre-existing ResolveBridge
         └─ preserve out-of-scope sentinel
```

The smoke-test owns and removes only its GUID-named directory under the system
temporary root. It exercises the real installer scripts from an isolated
repository copy, so the developer checkout and profile remain outside their
scope. It intentionally does not run live heartbeat verification because
hosted CI has no Resolve internal scripting context.

## M29 offline installer verification

```text
verify.ps1 -SkipResolveConnection
 ├─ supported system and venv Python
 ├─ package and MCP imports
 ├─ packaged defaults and machine-local TOML
 ├─ CLI and MCP executables
 ├─ required directories and bridge hash
 └─ unique create/write/delete probe per managed directory
```

Offline verification is an explicit mode, not a successful fake connection.
The default `verify.ps1` path performs the same local checks and then still
requires a fresh bridge state, healthy CLI status, and reported ping
capability. Permission probes use unique names, exclusive creation, and
mandatory cleanup; they do not inspect or replace user content.

## M30 manual integration evidence matrix

```text
Live evidence
 ├─ platform: Windows version/build + Python + Resolve edition/version
 ├─ state: project open/none + timeline present/absent/not applicable
 ├─ operation: CLI or MCP + status/readback
 ├─ write safety: backup + flags + idempotent replay
 └─ result: verified / pending / blocked
```

The canonical matrix is separate from CI. A hosted bridge simulation, stale
cached heartbeat, or operator assumption cannot move a live row to `verified`.
Resolve edition may be recorded as operator-confirmed when the documented API
does not report it, but that provenance must remain explicit. Evidence excludes
credentials, private media paths, raw configuration, and unsanitized bundles.

## M31 canonical protocol examples

```text
command.schema.json action enum
 └─ exact set equality with contracts/examples/commands.json
     ├─ validate each full envelope
     ├─ unique command and idempotency IDs
     ├─ backup=true for write actions
     └─ destructive=true only for confirmed delete

response/capability examples
 └─ validate success, error, boolean, and symbolic states
```

Examples ship as package data for documentation and downstream contract tests.
They remain inert fixtures: no adapter reads them as runtime commands, and no
example permits arbitrary execution or bypasses the application service.

## M32 wheel package boundary

```text
wheel build
 └─ check_wheel.py
     ├─ safe ZIP member paths
     ├─ required runtime modules and resources
     ├─ no tests/docs/installer/runtime content
     ├─ Name, Version, and Requires-Python metadata
     └─ CLI and MCP console entry points
         └─ replace editable CI install with wheel
             └─ verify from outside checkout
```

The package job is verification-only. It has read-only workflow permissions,
does not upload or publish the wheel, and runs independently from live Resolve.
The checker uses only the standard library plus the local package version.

## M35-M36 interactive track preparation

```text
manually launched ResolveBridge
 └─ bounded persistent polling
     └─ MCP resolve_ensure_timeline_tracks(timeline_id, video, audio)
         ├─ validate targets 1..8
         ├─ export project backup
         ├─ add only missing tracks
         └─ verify every count increment
```

The persistent bridge remains a single-command allowlisted dispatcher inside
Resolve; it does not expose a generic batch or code execution surface. M36
adds only the provider-neutral ability to ensure minimum video/audio track
counts. Resolve-specific `AddTrack` calls stay in the bridge, and new audio
tracks use the fixed `stereo` subtype. Existing tracks are never removed or
retyped, while stable receipts prevent duplicate work on replay.

M37 extends the same bounded read model with the target timeline frame rate.
This keeps millisecond-to-frame conversion in the future provider-neutral
composition layer grounded in live metadata rather than a project-default or
source-FPS assumption. Only the named `timelineFrameRate` setting is queried;
raw timeline settings remain outside the contract.

## M38 synchronized pair assembly

```text
MCP sync_screen_and_webcam
 └─ capability + explicit-confirmation gate
     └─ durable in-progress receipt
         ├─ create new timeline
         ├─ read target/source FPS and durations
         ├─ ensure V1/A1/V2
         ├─ insert screen V1 + screen A1 + webcam V2
         └─ verify the three canonical TimelineItem IDs
```

M38 stays provider-neutral by depending on a small gateway protocol rather
than Resolve objects. The Resolve provider continues to own each primitive.
Progress is persisted after every step, while per-step idempotency receipts in
the transport protect the gap between a successful provider write and the
next local progress write. The workflow creates a new timeline instead of
modifying an existing edit and never applies pause removal.

## M39 synchronized webcam layout

```text
MCP compose_webcam_picture_in_picture
 └─ load and validate one applied M38 receipt
     ├─ recover canonical timeline, webcam asset, and webcam TimelineItem IDs
     ├─ read named target timeline width/height settings
     ├─ map normalized frame center and bounded size to Pan/Tilt/Zoom
     └─ set the webcam transform and verify exact property readback
```

The application owns normalized coordinates and the durable layout receipt;
the Resolve provider owns the documented property mapping and project backup.
This boundary remains usable by future editor providers. M39 does not claim
visual masking, cropping, borders, Fusion composition, or source-aspect-aware
collision avoidance.

## M40 synchronized screen link

```text
MCP link_synchronized_screen_pair
 └─ load and validate one applied M38 receipt
     ├─ recover exactly the screen V1 and screen A1 canonical item IDs
     ├─ require verified clip.link capability and explicit confirmation
     ├─ call the provider-neutral set_clips_linked primitive
     └─ verify each selected item reports the other canonical peer ID
```

The workflow does not infer clips from track position or names. Its durable
receipt and the transport idempotency receipt protect replay at both workflow
and provider boundaries. The existing Resolve adapter remains solely
responsible for documented API calls, lock checks, backup, and live readback.

## M41 pause compaction preview

```text
MCP preview_synchronized_pause_compaction
 ├─ validate approved M5 plan SHA-256
 ├─ bind it to one applied M38 receipt by offset and source names
 ├─ read bounded target/source FPS and durations
 ├─ complement proposed cuts into kept half-open time intervals
 └─ map every kept interval to future V1/A1/V2 source and record frames
```

Resolve 21 exposes no documented split or trim TimelineItem operation. M41
therefore plans a non-destructive rebuild through the existing documented
insert primitive instead of emulating unsupported edits. The preview performs
no writes and reports readiness only from live verified capabilities.

## M42 confirmed pause compaction

```text
MCP apply_synchronized_pause_compaction(confirm_apply=true)
 ├─ recompute and hash current M41 preview
 ├─ durable receipt: create timeline → ensure V1/A1/V2
 ├─ one backed-up insert_clips batch for every kept range
 └─ verify returned IDs/bounds → list_timeline_items persistence readback
```

The batch primitive accepts provider-neutral placement fields only. The Resolve
adapter alone maps them to the documented list form of `AppendToTimeline`.
Step-level workflow keys and the bridge command receipt protect replay after a
transport interruption. The source M38 timeline remains unchanged; a failed
apply can leave only the new disposable target timeline, with project backups
available before each write step.

## M43 compacted timeline finalization

```text
MCP finalize_synchronized_pause_compaction(confirm_finalize=true)
 ├─ bind applied M42 + M39 + M40 receipts to one M38 pair
 ├─ derive per-segment screen V1/A1 and webcam V2 IDs
 ├─ one backup + set_clip_link_groups for all screen pairs
 ├─ one backup + set_clip_transforms for all webcam segments
 └─ verify mutual links, exact transform and canonical item identity
```

The two batch primitives keep backup count constant as cut count grows. The
Resolve adapter maps only to documented `Timeline.SetClipsLinked`,
`TimelineItem.SetProperty`, `GetProperty`, and `GetLinkedItems` methods.

## M44 finalized render preparation

```text
MCP prepare_finalized_timeline_render(confirm_prepare=true)
 ├─ validate one applied M43 receipt
 ├─ derive exact finalized timeline ID
 ├─ select timeline through Project.SetCurrentTimeline
 ├─ one backup + fixed prepare_render_job
 └─ verify timeline/profile/resolution and started=false
```

M44 creates no output file and does not start rendering. Its durable receipt
and the provider command receipt make interrupted preparation replay-safe.

## M45 finalized render execution

```text
MCP start_finalized_timeline_render(confirm_render=true)
 ├─ load applied M44 receipt
 ├─ verify exact Ready queue job and absent output
 ├─ one guarded start_render_job + durable M45 receipt
 └─ replay returns receipt without another start

MCP get_finalized_timeline_render_status
 ├─ revalidate M45 and M44 identity
 ├─ read GetRenderJobStatus / IsRenderingInProgress
 └─ verify managed non-empty MP4 when completion reaches 100%
```

## M46 finalized audio extraction

```text
MCP prepare_finalized_timeline_audio(confirm_prepare=true)
 ├─ bind one applied M43 timeline
 ├─ load fixed Resolve Audio Only preset
 ├─ force ExportVideo=false, 16-bit and 48 kHz
 └─ add one Ready job in managed audio-sources

MCP start_finalized_timeline_audio(confirm_render=true)
 ├─ verify exact Ready job and absent WAV
 └─ start once through the existing guarded render primitive

MCP get_finalized_timeline_audio_status
 ├─ read exact live job status
 └─ parse RIFF/WAVE and require uncompressed 16-bit/48 kHz PCM

local clean_dialogue_audio(pcm-dialogue-limit-v2)
 ├─ stream analysis and processing in bounded chunks
 ├─ apply nominal RMS gain plus deterministic peak limiter
 └─ require target_met before integration

MCP apply_finalized_timeline_audio(confirm_apply=true)
 ├─ bind completed extraction and validated v2 report
 ├─ import one derived WAV and ensure stereo A2
 ├─ insert exact MarkIn..MarkOut duration at timeline start
 ├─ disable only canonical source A1 items
 └─ confirm linked source video remains enabled
```

M46 keeps extraction, processing, and integration as separate confirmed stages
so a render or loudness failure cannot partially replace active dialogue. The
local backend invokes no shell decoder and preserves both source and derived
WAV files. The integration receipt is durable and replay-safe.

## M47 subtitle discovery

```text
MCP resolve_get_subtitle_environment(timeline_id)
 ├─ resolve the exact canonical timeline
 ├─ enumerate bounded subtitle tracks/items
 ├─ inspect documented auto-caption method/constants
 └─ report verified=false without invoking the AI write

MCP resolve_create_subtitles_from_audio(confirm_create=true)
 ├─ require exact timeline + documented API surface
 ├─ export one project backup
 ├─ invoke fixed AUTO/default/42/single-line/zero-gap policy
 └─ require newly created bounded subtitle-item readback
```

The first M47 slice deliberately separates API-surface discovery from feature
support. Resolve's local documentation identifies caption creation as a
Studio/AI scripting API and states that unsupported calls may return `False`.
Therefore method presence verifies only the read path and cannot promote
`subtitle.auto_caption` from `unknown`. The guarded write slice implements the
required confirmation, project backup, fixed caption policy and live readback.
It reports a safe unsupported error if Resolve 21 Free rejects the documented
call; a separate local transcription provider remains the fallback boundary.

The live M47 fallback is provider-neutral outside the Resolve adapter:

```text
MCP generate_subtitles(source_file, timeline_id, confirm_apply=true)
 ├─ validate source under media.allowed_roots
 ├─ faster-whisper small / Ukrainian / CPU int8
 ├─ atomically render deterministic UTF-8 SRT
 ├─ safe import_media + receipt-bound append_subtitle_file
 ├─ verify append-frame + transcript-offset placement
 └─ require exact new canonical subtitle IDs + durable receipt
```

The transcription provider invokes Python APIs directly and exposes no shell
or executable arguments. Model cache, SRT output and receipts use platform
directories; no user- or drive-specific path is embedded in the repository.
The bridge records the timeline end before documented `AppendToTimeline`; this
is the SRT placement anchor observed in Resolve 21 Free. It does not claim
playhead-based or arbitrary mid-timeline subtitle insertion.

## M48 declarative editing recipes

```text
packaged JSON recipe
 ├─ Draft 2020-12 contract validation
 ├─ fixed action/capability declaration
 ├─ read-only input/default/capability preview
 └─ confirmed application action dispatcher
      ├─ compose_webcam_picture_in_picture
      └─ link_synchronized_screen_pair
```

Recipes are provider-neutral orchestration data above `AgentApplication`; they
never enter the Resolve bridge as a batch or executable expression. The M48
dispatcher has a code-owned action allowlist and fixed argument mapping. A
SHA-256 run receipt binds the complete recipe definition and persists each
applied step atomically, so interruption can resume only pending work while
completed underlying workflow receipts replay.

## Future providers

Resolve-specific imports and object handling remain within the Resolve adapter.
An FFmpeg or Premiere provider can implement the same provider-neutral contracts
without changing the agent core.

## M49 visual treatment boundary

```text
MCP preview_visual_treatment
 └─ normalize transforms/titles + inspect clip.transform/title.insert

MCP apply_visual_treatment(confirm_apply=true)
 ├─ one backed-up set_clip_transforms batch when requested
 ├─ one backed-up insert_title per requested standard title
 ├─ persist every completed operation atomically
 └─ replay only pending operations
```

The core knows only provider-neutral transforms and installed title names. The
Resolve adapter alone maps transforms to documented TimelineItem properties and
maps title insertion to `Timeline.InsertTitleIntoTimeline`. Smart Reframe,
Fusion input mutation and animation/keyframe authoring are outside M49.

## M52 packaged animation templates

```text
packaged manifest + audited .setting
 ├─ catalogue validates schema and SHA-256
 ├─ installer owns one documented user-template target
 ├─ preview binds applied M51 receipt + live source snapshot
 └─ confirmed apply
      ├─ duplicate source timeline
      ├─ fixed InsertFusionTitleIntoTimeline call
      ├─ unchanged-item and Fusion-comp readback
      └─ durable receipt/replay
```

The provider-neutral core handles template identity, plan binding and receipts.
Only the Resolve adapter knows the installed Resolve title name or invokes the
documented Fusion-title insertion method. The MCP boundary never carries a
Fusion graph, expression, node/control name, external file path or executable
content. Text customization remains a manual Resolve Inspector operation in
this milestone.
