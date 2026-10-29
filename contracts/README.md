# Contracts

`workflow-capabilities.schema.json` separates backend workflow support from
point-in-time availability. Registered tools and partial alternatives cannot
establish a native connection or enable an unsupported workflow apply.

`take-selection.schema.json` defines M54 path-redacted fixed-policy technical
and script-aware dialogue rankings that always require review.
`take-selection-review.schema.json` binds
one immutable human decision to the exact selection.
`take-sequence.schema.json` defines an ordered path-redacted handoff composed
only from canonical approved reviews. It is not a timeline-apply contract.
`take-sequence-binding.schema.json` defines the path-redacted public M55.1
receipt; its machine-local private path artifact is intentionally not an
external contract.
`take-sequence-assembly-preview.schema.json` defines the read-only M55.2 plan:
approved source ranges, sequential output ranges and local video metadata are
path-redacted, while timeline modification and apply remain disabled.
`take-sequence-timeline-preview.schema.json` defines M55.3 live target
FPS/resolution binding, inclusive source-frame bounds and contiguous timeline
positions. It carries no Media Pool asset IDs and therefore cannot enable
apply.
`take-sequence-media-import-preview.schema.json` defines M55.4 unique import
sources, current Media Pool snapshot identity and conservative same-name
collision review. It exposes no source path and cannot execute import.
`take-sequence-media-import-result.schema.json` defines the M55.5 confirmed,
backup-backed, path-redacted Media Pool import receipt and verified asset IDs.
`take-sequence-timeline-apply-preview.schema.json` defines the M55.6 exact
duplicate-timeline V1/A1 plan, live blockers and capability gates.
`take-sequence-timeline-apply-result.schema.json` defines its path-redacted,
per-step durable application receipt and canonical inserted item identities.

`color-preset.schema.json` defines one immutable packaged M53 CDL preset with a
fixed scope and apply policy. `color-treatment-preview.schema.json` binds that
preset to an applied M52 receipt, exact live source snapshot, media-video target
items and documented capability evidence. Neither contract enables a color
write or accepts arbitrary LUT/DRX paths, node indexes, expressions or code.
`color-treatment-result.schema.json` зберігає exact plan, duplicate operation,
fixed color operation, managed-version environment readback і replay receipt.

Protocol version `1.0` accepts the read-only Resolve actions and allowlisted
write actions listed in `command.schema.json`. Write actions have strict
argument shapes and require `create_backup=true`. The bridge performs a minimal
validation pass without third-party dependencies because it runs inside
Resolve.

The JSON Schemas are canonical protocol documentation. From M2 onward, the
external agent validates commands, responses, date-time formats, response
status/error consistency, and capability reports with Draft 2020-12 semantics.

No contract permits arbitrary Python, Lua, PowerShell, shell, or Resolve
expression execution.

M31 adds canonical examples under `contracts/examples/`. `commands.json`
contains exactly one full envelope for every action currently enumerated by
`command.schema.json`; tests reject missing, duplicate, or extra actions and
validate every envelope. `responses.json` covers success and error consistency,
while `capabilities.json` covers boolean and all symbolic capability values.

Example timestamps, IDs, media paths, and results are illustrative contract
fixtures. They are packaged for inspection but must not be copied directly to
the live command queue. Production commands require fresh IDs, current
timestamps, policy-validated paths, and application-generated safety fields.

M7 adds the empty-argument read-only `get_render_environment` command and the
strict `prepare_render_job` write command. The latter accepts only
`custom_name` and requires backup.

M47 adds read-only `get_subtitle_environment` for one canonical timeline. It
returns bounded subtitle tracks/items plus native method/constant availability
without invoking caption generation. No language, model, prompt, arbitrary API
arguments, code execution, or timeline mutation is representable.
The paired `create_subtitles_from_audio` write accepts only the same timeline
ID and literal confirmation. It requires backup and fixes every documented
caption option; success requires newly created bounded subtitle-item readback.
`subtitle-generation.schema.json` defines the durable local-provider receipt:
one source/timeline binding, generated SRT, backend/model/language evidence,
exact segment/item counts and canonical IDs, verified append-anchor placement,
anchor evidence source, plus the two provider step results.

M48 adds `editing-recipe.schema.json` for packaged declarative definitions and
`editing-recipe-run.schema.json` for durable per-step execution receipts. The
initial action enum contains only the verified M39 layout and M40 link
workflows. Recipes cannot encode arbitrary provider calls, MCP dispatch or
executable code. A run receipt binds the SHA-256 of the complete definition,
so changed packaged steps cannot replay a stale result under the same ID.

M8 adds `get_render_job_status` and `start_render_job`; both accept only a safe
opaque `job_id`. Start requires backup and can target only a fixed-policy job
proven by an M7 preparation receipt. Arbitrary render settings, deletion,
stopping, upload, and mass-start commands remain outside the contract.

M9 extends `prepare_render_job` with an optional allowlisted `profile`.
M44 additionally permits an optional bounded `timeline_id`, allowing a
workflow to bind job creation to a canonical finalized timeline instead of UI
selection state. The response reports the selected timeline identity.
M45 adds a durable finalized-render execution contract. It stores only the
canonical M44 receipt binding and one guarded start result; live progress and
output verification remain read-only responses rather than mutable receipts.
Omitting it preserves the 1080p default. The only accepted values are
`youtube-1080p-h264-v1` and `youtube-2160p-h264-v1`; raw width, height, preset,
codec, quality, and output path remain outside the command contract.

M10 adds `insert_clip` with strict timeline/asset IDs, ordered source-frame
bounds, a non-negative timeline-relative position, `video|audio` track type,
and bounded track index. It requires backup. The contract does not expose
move, trim, split, delete, ripple, transform, or arbitrary clipInfo fields.

M36 adds `ensure_timeline_tracks` with one timeline ID and integer video/audio
targets from 1 through 8. It requires backup and exposes no deletion, track
renaming, arbitrary subtype, or raw `AddTrack` options; new audio tracks use
the fixed `stereo` subtype.

M38 adds `synchronized-pair-result.schema.json` for the local higher-level
workflow rather than a new bridge action. It records bounded inputs, the new
timeline, target FPS, three fixed placements, five allowlisted primitive
results and final readback. `in_progress` receipts make partial execution
resumable; the contract contains canonical asset IDs but no source paths.

M39 adds `picture-in-picture-result.schema.json`. It binds a bounded normalized
webcam layout to one completed M38 receipt, stores target timeline resolution,
the derived Pan/Tilt/Zoom transform and the allowlisted primitive result. It
does not add a bridge command or expose raw Resolve properties.

M40 adds `synchronized-link-result.schema.json`. It binds exactly one screen
video/audio pair from an applied M38 receipt to the existing allowlisted
`set_clips_linked` primitive and records mutual canonical-ID readback. It
cannot address webcam or arbitrary timeline items.

M41 adds `pause-compaction-preview.schema.json`. It binds an approved M5 plan
to one applied M38 receipt and records bounded cuts, kept intervals, target
duration, and exact future V1/A1/V2 insert arguments. The preview is read-only
and reports apply readiness from verified live capabilities.

M42 adds bounded `insert_clips` command arguments and
`pause-compaction-result.schema.json`. The command accepts only one timeline ID
and 1 to 3003 fixed placement objects; arbitrary Resolve `clipInfo` fields are
not exposed. The result persists the exact preview hash, three step results,
new timeline identity, placement count, and final readback.

`rough-cut-plan.schema.json` is a separate M5 artifact contract rather than a
bridge command. It requires a pending human review and explicitly states that
applying the draft is unsupported in M5.

`rough-cut-approval.schema.json` is the separate M21 review record. It binds
one explicit approval to the canonical draft SHA-256 and keeps
`apply_supported=false`. Approval never changes the deterministic draft.

`audio-report.schema.json` defines the M6 before/after measurements, fixed
versioned preset parameters, source-preservation flag, derived asset, and
target validation. It accepts the original peak-guard v1 and deterministic
limiter v2 while remaining independent from the Resolve command protocol.

`finalized-audio-extraction.schema.json` defines the durable M46 receipt for
one fixed `Audio Only` WAV prepare/start sequence bound to an applied M43
timeline. It stores no arbitrary render settings and does not claim that the
audio has already been cleaned or integrated back into the timeline.

`finalized-audio-integration.schema.json` defines the durable M46 apply receipt.
It binds one completed extraction to one validated limiter-v2 report, records
the fixed A2 insertion and the exact source A1/V1 enable-state operations, and
does not accept caller-supplied timeline item IDs or arbitrary processing data.

`visual-treatment-result.schema.json` defines the durable M49 receipt for one
normalized static transform batch and bounded standard-title insertions. The
bridge command accepts only installed title name, exact timecode and explicit
confirmation; title text, Fusion controls, Smart Reframe and arbitrary
properties are outside the contract.

`animation-template.schema.json` defines an immutable packaged M52 template
manifest. `animation-template-preview.schema.json` binds one read-only plan to
an applied M51 receipt, exact source snapshot, installed asset hash and fixed
capabilities. `animation-template-result.schema.json` records only timeline
duplication and one allowlisted insertion. No contract accepts Fusion code,
nodes, controls, expressions, scripts or arbitrary template files.

`take-sequence-qc.schema.json` defines the M55.7 deterministic structural
report bound to one freshly verified M55.6 receipt. It proves exact V1/A1
alignment and contiguity while explicitly listing creative areas it cannot
verify. `take-sequence-qc-review.schema.json` stores one immutable human
approve/reject decision; neither contract modifies Resolve or exposes paths.

The three `take-sequence-render-*.schema.json` contracts define the M55.8
approved render preview, durable prepare/start receipt and dynamic verified
status. They allow only packaged 1080p/4K H.264 profiles and never accept a
caller-supplied output path, codec, Resolve preset or arbitrary settings.
