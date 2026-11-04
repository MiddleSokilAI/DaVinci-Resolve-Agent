# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Automatic Project Manager creation through fixed background UIA Invoke/Value
  patterns, with durable once-only dispatch receipts, cross-route key ownership,
  native UUID/library/root verification and persisted Project.db identity checks.
  Cold creation never opens a historical project; uncertain retries only recover
  the original operation. Live Create/native-bootstrap verification is pending.

- Configurable installed `DataRoot` shared by Python and ResolveBridge, verified
  legacy-data migration, compatibility junctions and a 10 GiB output reserve.
- Live storage migration of 1440 files (8.369 GiB) to drive G with per-file
  SHA-256 verification, a fresh bridge heartbeat and successful CLI ping.
- M54 fixed-policy bounded video/audio candidate analysis with deterministic
  technical ranking, redacted source paths and explicit limitations.
- M54.2 segment-level candidate analysis with explicit source ranges up to 300
  seconds, including multiple candidate ranges from one allowlisted file.
- M54.2 live same-file segment smoke on drive G with 7/7 video samples,
  bounded audio, deterministic replay and source-path redaction.
- M54.3 script-aware dialogue take ranking with bounded local transcription,
  reference-text fingerprinting and mandatory human review.
- M54.3 path-redacted request index that returns exact persisted replays without
  rerunning CPU transcription.
- M54.4 immutable approved take sequences with canonical selection/review
  integrity, ordered path-redacted handoff and no timeline write.
- M55.1 allowlisted approved-sequence source binding with exact file identity,
  redacted public receipts and private machine-local paths.
- M55.2 deterministic path-redacted assembly preview with revalidated bound
  sources, local video metadata, sequential source/output ranges and mixed
  FPS/resolution warnings; no Resolve or timeline write is performed.
- M55.3 live timeline frame-mapping preview using the existing documented
  editing-metadata readback, deterministic source-frame bounds and contiguous
  target positions without import or timeline mutation.
- Timeline-only `get_editing_metadata` discovery with zero requested assets,
  retaining bounded FPS, resolution and track metadata.
- M55.4 conservative Media Pool import preview with source-fingerprint
  deduplication, deterministic live snapshot binding and mandatory review for
  ambiguous same-name items; no media import is executed.
- M55.5 explicitly confirmed, backup-backed Media Pool import with exact-plan
  validation, private path resolution, canonical metadata/readback checks and
  durable path-redacted replay receipts; timelines remain unchanged.
- M55.6 exact-plan duplicate-timeline take-sequence application with empty-source
  and target-name guards, local audio-stream detection, bounded V1/A1 batches,
  canonical source/target readback and durable per-step recovery.
- M55.7 deterministic structural sequence QC with fresh M55.6 readback, exact
  V1/A1 alignment and contiguity checks, explicit unverified creative areas and
  immutable human approve/reject review without timeline mutation.
- M55.8 approved-sequence render preview/start/status with exact-plan
  confirmation, deterministic collision-resistant names, allowlisted 1080p/4K
  H.264 profiles and managed non-empty MP4 output verification.
- M55.9 synchronized Windows installer path inventory for every M54/M55 durable
  artifact, including idempotent creation, offline write verification and
  isolated install/rerun/uninstall lifecycle assertions.
- M54 immutable approve/reject review records that never modify Resolve.
- M54 live bounded analysis of the two allowlisted synchronized MKV sources,
  including deterministic replay and source-path redaction; human review remains
  pending because the files are complementary camera/screen sources, not takes.
- M53 immutable `tutorial-clean-v1` CDL preset catalogue and schemas with no
  caller-supplied grade values, node indexes, LUT/DRX paths or expressions.
- M53 read-only Resolve color-environment discovery and M52-bound deterministic
  color-treatment preview; grade application remains gated on live Free proof.
- M53 confirmed duplicate-timeline color application with a managed local
  version, code-owned node-1 CDL, canonical target/version readback and replay.
- M53 duplicate-item remapping by exact media identity after live Resolve
  evidence confirmed TimelineItem IDs change when a timeline is duplicated.
- M53 live apply recovery: 5/5 managed color versions, unchanged M52 source and
  exact replay with stable timeline and backup counts.
- M53 visual acceptance in Resolve 21.0.3 Free confirmed the intended subtle
  contrast increase from the packaged tutorial preset.
- M52 immutable animation-template catalogue and packaged
  `accent-card-v1` Fusion Title with reproducible SHA-256 verification.
- M52 read-only environment/preview tools and confirmed duplicate-timeline
  application with completed-M51 binding, exact plan/receipt replay, project
  backup, append-only insertion and canonical Fusion-item readback.
- Repeatable Windows install, verification and conservative uninstall of the
  managed Resolve title template while preserving a pre-existing user asset.
- Exact-end and playhead-readback guards for Fusion-title insertion after live
  Resolve evidence showed that an out-of-range playhead can clamp to timeline
  start and ripple existing tracks.
- One bounded Resolve-provider retry only when the bridge confirms a transient
  playhead-readback failure occurred before Fusion-title insertion.
- Correct Fusion Transform center keyframes after visual acceptance showed the
  parsed title and Inspector controls but kept the card outside the frame.
- Replace unsuccessful Key Stretcher variants with Blackmagic's recommended
  mirrored Anim Curves wiring on `Dissolve.Mix`; frame-by-frame acceptance
  evidence showed both earlier variants remained fully static.
- Confirm M52 live in Resolve 21.0.3 Free with a fresh mirrored Anim Curves
  title, editable Inspector fields, unchanged M51 source and exact replay with
  stable backup and timeline counts.
- M51 provider-neutral B-roll review plan with completed-M50 binding, bounded
  source/timeline ranges, FPS-aware durations and collision checks.
- Confirmed `apply_broll_plan` on a duplicated baseline timeline using only
  documented track creation and video-only batch insertion, with exact plan
  binding, canonical readback and durable replay.
- M51 read-only live preview in Resolve 21.0.3 Free with completed-M50 binding,
  verified capability gates and no timeline or backup creation.
- M51 confirmed live apply on a disposable duplicate timeline: one 48-frame
  video-only V3 item, unchanged M50 source timeline, three operation backups
  and exact replay without another timeline, item or backup.
- M51 persistence readback aligned with the documented generic timeline-item
  response: asset binding is checked in the immediate insert result, while the
  durable readback verifies canonical item ID, track and exact frame bounds.
- M50 canonical baseline edit QA binding required applied M43 rough cut and M46
  cleaned audio plus optional M47 subtitles and M49 visual treatment to one
  exact timeline.
- Read-only `preview_baseline_edit`, confirmed `start_baseline_render` and
  read-only `get_baseline_render_status` with live identity/bounds checks,
  deterministic output naming and durable replay through M44/M45.
- Explicit blocking of mixed disposable/final timeline receipts before any
  render preparation or project backup.
- M50 live acceptance in Resolve 21.0.3 Free with 6/6 core QA, one completed
  managed 1080p job, validated MP4 output and exact replay without a new job.
- Correct M50 completion aggregation against the canonical M45
  `output.validation.passed` result.
- M49 `resolve_insert_title` over documented standard-title insertion with
  exact timecode, explicit confirmation, project backup, playhead restoration,
  canonical item readback and capability promotion only after live success.
- Provider-neutral `preview_visual_treatment` and `apply_visual_treatment` for
  bounded static zoom/reframing plus standard-title insertion, with capability
  gates, durable per-operation recovery and idempotent replay.
- Explicit Resolve Free safety boundary excluding Studio/AI `SmartReframe`,
  undocumented title-text mutation, Fusion controls, keyframes and tracking.
- Generated-item timeline readback with explicit `source_type` and nullable
  source bounds, preventing inserted standard titles from breaking discovery
  while retaining strict source bounds for Media Pool clips.
- Append-only standard-title safety preflight for integer-FPS timelines, plus
  exact new-item and unchanged-existing-item post-write verification after live
  testing showed that occupied placement can split existing Resolve items.
- Resolve 21 Free live M49 acceptance: occupied placement rejected without a
  backup, one exact append-only generated title at frame 88200, unchanged prior
  item bounds, ZoomX/ZoomY 1.16 readback, and identical replay with backups
  stable at 70.
- M48 packaged declarative editing recipe contracts, bounded discovery and
  read-only capability-aware preview tools.
- Confirmed `run_editing_recipe` execution for the allowlisted synchronized
  tutorial layout, with atomic per-step progress, idempotent replay and no
  arbitrary actions, arguments, code, or MCP dispatch.
- Resolve 21 Free live MCP validation of `tutorial-layout-v1`, including ready
  preview, two applied steps, identical replay and no additional project backup.
- M47 read-only subtitle environment discovery for one canonical timeline,
  including bounded subtitle tracks/items and documented native auto-caption
  method/constant availability without invoking an AI operation.
- MCP `resolve_get_subtitle_environment` with `subtitle.read` capability
  evidence while `subtitle.auto_caption` remains unknown until a successful
  confirmed live write.
- Backup-backed `resolve_create_subtitles_from_audio` with explicit
  confirmation, fixed automatic-language/default-preset policy, idempotency,
  and mandatory bounded subtitle readback.
- Local pinned faster-whisper transcription, deterministic UTF-8 SRT output,
  receipt-bound Resolve import/append workflow, verified append-anchor
  placement, strict canonical-item readback, and a durable M47
  subtitle-generation receipt.
- M46 streaming PCM analysis/processing for multi-gigabyte WAV inputs without
  loading the complete sample array into memory.
- Versioned `pcm-dialogue-limit-v2` preset with deterministic limiting,
  clipping removal, and strict RMS/peak target validation.
- Durable `apply_finalized_timeline_audio` workflow that imports one validated
  report asset, creates A2, inserts the exact extraction range, disables only
  source A1 items, and confirms linked source video remains enabled.
- Resolve 21 Free 21.0.3.7 live validation of a 178906-frame A2 replacement,
  exact source A1 disable readback, preserved V1 enable state, and replay with
  no second write, backup, or duplicate item.
- M46 guarded full-timeline audio extraction with the fixed Resolve `Audio Only`
  preset, managed WAV output, durable prepare/start replay, and strict 16-bit
  48 kHz uncompressed PCM validation.
- MCP prepare/start/status tools for M46 audio extraction bound to one applied
  M43 finalization receipt without exposing arbitrary render settings.
- M45 guarded `start_finalized_timeline_render` workflow bound to one applied
  M44 receipt, explicit confirmation, exact live job preflight, one-start
  durable replay, and no overwrite of an existing managed output.
- Read-only `get_finalized_timeline_render_status` with live queue identity and
  managed non-empty MP4 verification.
- Resolve 21 Free 21.0.3.7 live M45 completion of the exact M44 job at 100%,
  validation of a 2,094,070,933-byte managed MP4, and replay without another
  render start or backup.
- M44 `prepare_finalized_timeline_render` workflow that binds one fixed 1080p
  or 4K MP4/H.264 job to an applied M43 timeline with explicit confirmation,
  durable replay, and no automatic render start.
- Optional explicit `timeline_id` for `prepare_render_job`, with documented
  Resolve timeline selection and timeline identity in the provider readback.
- Resolve 21 Free 21.0.3.7 live M44 preparation of one Ready 1080p MP4/H.264
  job for the exact M43 timeline, one backup, no render start, and replay
  without another job or backup.
- M43 `finalize_synchronized_pause_compaction` workflow that propagates the
  approved M39 webcam PIP and M40 screen links to every M42 segment with two
  bounded backed-up batches, durable replay, and exact readback verification.
- Provider-neutral `set_clip_link_groups` and `set_clip_transforms` commands
  mapped only to documented Resolve timeline-link and item-property APIs.
- Resolve 21 Free 21.0.3.7 live M43 finalization of two screen link groups and
  two webcam PIP segments with six-item identity readback, exactly two new
  backups, and mutation-free replay.
- M42 confirmed `apply_synchronized_pause_compaction` workflow that rebuilds
  approved kept ranges on a new V1/A1/V2 timeline with a durable receipt,
  explicit confirmation, and final canonical-ID readback.
- Bounded `insert_clips` provider command mapped to the documented batched
  `MediaPool.AppendToTimeline([{clipInfo}, ...])` API with one project backup,
  track/lock validation, and idempotent replay.
- Resolve 21 Free 21.0.3.7 live M42 apply with one new timeline, six persisted
  items, three write-step backups, preserved M38 source items, and replay with
  no additional timeline or backup.
- M41 read-only `preview_synchronized_pause_compaction` workflow that converts
  approved millisecond cuts into bounded kept V1/A1/V2 source-frame ranges
  without undocumented split or trim APIs.
- Resolve 21 Free 21.0.3.7 live preview of one 560 ms cut, two kept intervals,
  six planned placements, and no project backup or mutation.
- Unique bridge state temporary files, bounded Windows `PermissionError`
  retries, and deferred heartbeat publication after repeated live `bridge.json`
  replacement sharing violations.
- M40 provider-neutral `link_synchronized_screen_pair` workflow bound to one
  applied M38 receipt, explicit confirmation, mutual canonical-ID readback,
  durable receipt, and stable replay.
- Resolve 21 Free 21.0.3.7 live verification of documented screen V1/A1
  linking, `clip.link=true`, and replay without a second backup.
- M39 provider-neutral `compose_webcam_picture_in_picture` workflow over one
  completed M38 receipt, normalized frame coordinates, bounded 10–50% zoom,
  explicit confirmation, exact Resolve property readback, and durable replay.
- Bounded target timeline width/height discovery through documented named
  `Timeline.GetSetting` calls; no raw settings snapshot is exposed.
- Resolve 21 Free 21.0.3.7 live verification of a 1920×1080 webcam layout with
  exact Pan/Tilt/Zoom readback, one backup, and zero replay writes.
- M38 provider-neutral `sync_screen_and_webcam` workflow for a new V1/A1/V2
  timeline using live target FPS and bounded signed synchronization offset.
- Durable step-level progress receipt, derived primitive idempotency keys,
  capability gate, explicit confirmation, and final TimelineItem readback.
- Privacy-preserving `editing` workflow audit category without asset IDs or
  operation results in audit records.
- Resolve 21 Free 21.0.3.7 live verification of one 24 FPS timeline, exact
  V1/A1/V2 item mapping, five initial backups, and zero replay writes.
- M37 documented target timeline frame-rate discovery in bounded editing
  metadata, without exposing raw timeline settings.
- Resolve 21 Free 21.0.3.7 live verification of a 24 FPS target timeline and
  60 FPS source asset with no backup or project mutation.
- M36 bounded, backup-backed timeline track preparation through documented
  `GetTrackCount` and `AddTrack`, with 1–8 targets and stereo audio additions.
- Exact per-track readback, durable capability evidence, and idempotent replay
  without duplicate tracks or a second backup.
- Resolve 21 Free 21.0.3.7 live verification of a disposable timeline changing
  from 1V/1A to 2V/1A, independent metadata readback, and replay safety.
- M35 manually launched persistent Resolve bridge with bounded polling,
  heartbeat refresh, one allowlisted command per iteration, and clean stop.
- Read-only editing metadata discovery limited to requested asset IDs,
  `Frames`, `FPS`, and target timeline track counts.
- Resolve 21 Free 21.0.3.7 live verification of responsive persistent polling,
  sequential commands, bounded metadata for both synchronized MKV assets, and
  clean MCP stop without project mutation.
- M34 guarded rough-cut apply preview and approved-plan validation with
  timeline-copy targeting, capability blocking, idempotent receipts, and no
  fabricated mapping for unsupported pause removal.
- M33 portable project-scoped Codex MCP configuration with write-tool approval
  and a live end-to-end acceptance procedure.
- Automated validation that the Codex configuration starts the installed STDIO
  server and exposes representative status, Resolve, and render tools.
- Live Codex MCP verification of cached bridge status and current Resolve
  project readback through the one-shot internal bridge.
- M0 repository foundation.
- Python package skeleton and version CLI.
- Windows installation, verification, and uninstallation scripts.
- Default configuration and initial automated tests.
- M1 one-shot internal Resolve bridge with heartbeat and read-only state.
- Allowlisted filesystem queue for `ping` and documented read-only queries.
- CLI bridge status and current-project commands.
- Resolve bridge installation, backup, verification, and restoration behavior.
- Internal Resolve/Fusion context discovery verified on Resolve 21 Free
  21.0.3.7, with a documented module fallback.
- M2 provider-neutral filesystem command client with atomic enqueue and timeout.
- Canonical Draft 2020-12 JSON Schema validation in the external agent.
- Typed Resolve provider client and live CLI commands for ping, bridge info,
  capabilities, project, timelines, and current timeline.
- Stable CLI exit codes for operational errors and timeouts.
- M3 local stdio MCP server over a provider-neutral application service.
- Read-only MCP tools for bridge status, current project, timelines, and current
  timeline.
- MCP client configuration and one-shot bridge usage documentation.
- M4 allowlisted media import, timeline creation, clip append, and marker tools.
- Mandatory `.drp` project exports before write operations.
- External and bridge-side media-root validation.
- Stable idempotency receipts and documented manual rollback strategy.
- Resolve 21 Free 21.0.3.7 live verification of all four M4 write operations
  and replay without duplicate backups.
- M5 provider-neutral PCM WAV synchronization and long-pause analysis.
- Deterministic rough-cut draft plans with a mandatory human review gate.
- Local `create_rough_cut` MCP tool that never applies edits to Resolve.
- Installer-managed runtime plan storage preserved on uninstall by default.
- M6 standard-library 16-bit PCM WAV dialogue analysis.
- Deterministic RMS-level preset with peak guard and preserved source assets.
- Canonical before/after audio reports with target validation.
- Local `clean_dialogue_audio` MCP tool independent from Resolve.
- M7 read-only discovery of documented Resolve render formats, codecs, presets,
  current format/codec, and queued jobs.
- M7 fixed MP4/H264 render-job preparation with backup, idempotency, and a
  durable non-configurable output directory.
- Resolve 21 Free 21.0.3.7 live verification of 1080p render-job preparation,
  queue discovery, backup creation, and idempotent replay without rendering.
- M8 read-only render-job status and guarded start for agent-prepared jobs.
- Durable per-job start records, live queue policy checks, mandatory backup,
  and replay protection before documented Resolve rendering calls.
- Resolve 21 Free 21.0.3.7 live verification of read-only `Ready` job status
  without starting or changing the render queue.
- M9 documented MP4/H.264 resolution discovery and fixed 1080p/2160p YouTube
  export profile allowlist.
- Resolve 21 Free 21.0.3.7 live confirmation of 3840x2160 MP4/H.264 and the
  built-in `YouTube - 2160p` preset without changing the render queue.
- Resolve 21 Free 21.0.3.7 live verification of backup-backed 4K job
  preparation and idempotent replay without duplicate jobs or rendering.
- M10 safe ranged clip insertion with timeline-relative positioning, explicit
  video/audio track selection, project backup, and idempotent replay.
- Documented TimelineItem readback for actual source bounds, timeline bounds,
  and track placement after insertion.
- Resolve 21 Free 21.0.3.7 live verification of synchronized video/audio ranged
  insertion, 60-to-24 fps frame conversion, backups, and duplicate-free replay.
- M11 read-only verification of completed render status, managed MP4 output
  location, file existence, and non-zero size.
- Resolve 21 Free 21.0.3.7 live verification of guarded 1080p render start,
  completion, and the resulting managed non-empty MP4.
- M12 backup-backed TimelineItem enable/disable operation with locked-track
  protection, documented state readback, and idempotent replay.
- Resolve 21 Free 21.0.3.7 live verification of clip disable, duplicate-free
  replay, and restoration of the original enabled state.
- M13 backup-backed current timeline selection by ID with previous/current
  readback and idempotent replay.
- Canonical timeline IDs in read-only timeline list and current-timeline
  responses.
- Resolve 21 Free 21.0.3.7 live verification of timeline ID discovery,
  selection, duplicate-free replay, and restoration of the original timeline.
- Post-write live bridge-state refresh so cached timeline metadata matches the
  completed Resolve operation.
- Live confirmation that selection heartbeats immediately track an M10 to M7
  to M10 round trip without an extra bridge invocation.
- M14 backup-backed bounded transform updates for one unlocked video
  TimelineItem with exact property readback and idempotent replay.
- Provider-neutral position, uniform zoom, rotation, and opacity fields with
  fixed validation and no arbitrary Resolve property access or expressions.
- Resolve 21 Free 21.0.3.7 live verification of opacity write/readback,
  duplicate-free replay, and restoration of the original value.
- M15 confirmed deletion of exactly one TimelineItem through a destructive MCP
  tool, mandatory backup, non-ripple behavior, readback, and replay protection.
- Action-specific destructive safety envelopes that keep every other command
  constrained to `allow_destructive=false`.
- Resolve 21 Free 21.0.3.7 live verification of isolated non-ripple deletion,
  duplicate-free replay, and preservation of the original video/audio pair.
- M16 read-only timeline item discovery with canonical IDs, track placement,
  timeline/source frame bounds, and duration metadata.
- Provider-neutral item addressability for enable, transform, and delete tools
  without reliance on historical write receipts.
- Resolve 21 Free 21.0.3.7 live discovery of synchronized video/audio item IDs
  and matching frame metadata without creating a project backup.
- M17 read-only recursive Media Pool item discovery with canonical item and
  folder IDs plus logical folder paths.
- Bounded folder traversal without raw clip properties, filesystem paths, or
  Resolve object handles.
- Resolve 21 Free 21.0.3.7 live discovery of the M10 source ID among five
  Media Pool items without creating a project backup.
- M18 fixed read-only workspace snapshot for bridge, project, timeline,
  TimelineItem, Media Pool, and render discovery in one bridge command.
- CLI and MCP snapshot entry points without arbitrary batching, project
  backups, persistent polling, or external Resolve scripting assumptions.
- M19 backup-backed link/unlink for 2 to 16 explicitly addressed timeline
  items through documented Resolve link APIs.
- Per-item track-lock validation, link-state readback, and idempotent replay
  without exposing undocumented move, trim, or split operations.
- M20 backup-backed duplication of one explicitly addressed timeline through
  documented `Timeline.DuplicateTimeline`.
- Timeline-name conflict protection, canonical duplicate readback, project
  list verification, and idempotent replay without changing the current timeline.
- M21 explicit local approval for one canonical rough-cut draft with mandatory
  `confirm_review=true`.
- Immutable draft preservation, SHA-256-bound approval records, tamper
  detection, and idempotent replay while plan application remains unsupported.
- M22 read-only rough-cut plan listing and canonical plan-detail retrieval.
- Bounded path-free summaries plus validated approval/hash inspection without
  changing local artifacts or contacting Resolve.
- M23 read-only audio report listing and canonical report-detail retrieval.
- Bounded path-free audio summaries with contract validation and no media
  processing or Resolve bridge call.
- M24 bounded local diagnostics bundles with sanitized configuration, cached
  capabilities, failed-command metadata, log excerpts, and version data.
- Fixed managed diagnostics output with no media, backups, raw queue payloads,
  arbitrary output paths, or Resolve bridge invocation.
- M25 canonical per-command filesystem transport audit records.
- Atomic submitting, pending, success, error, and timeout states without
  arguments, media paths, idempotency keys, response payloads, or messages.
- M26 application-level audit for seven local rough-cut and audio operations.
- Atomic workflow lifecycle records with fixed error classification and no
  arguments, artifact IDs, paths, results, contents, or exception messages.
- M27 GitHub Actions Windows test matrix for Python 3.10, 3.11, and 3.12.
- Read-only CI quality checks for Ruff, mypy, installed CLI, and installer
  PowerShell syntax without Resolve, secrets, deployment, commit, or push.
- Bounded retry for transient Windows `PermissionError` during atomic audit
  replacement and simulated command/response publication.
- M28 isolated Windows installer lifecycle smoke test.
- Repeat-install configuration preservation, previous-bridge backup/restore,
  fixed-scope uninstall, and out-of-scope sentinel verification in CI.
- M29 explicit offline installer verification without a fabricated Resolve
  heartbeat.
- Machine-local TOML validation and write/delete permission probes for every
  installer-managed application directory.
- M30 canonical manual Resolve integration platform and state matrices.
- Evidence requirements that separate verified live runs, pending coverage,
  automated CI, stale cached state, and operator-confirmed edition.
- M31 packaged canonical command, response, and capability examples.
- Exact automated coverage of every allowlisted action with schema-validated
  envelopes and safety invariants.
- M32 isolated Windows wheel build, boundary inspection, and non-editable
  installation verification.
- Wheel metadata, console entry-point, safe archive-path, packaged-resource,
  and repository-only content checks without publication or upload.
