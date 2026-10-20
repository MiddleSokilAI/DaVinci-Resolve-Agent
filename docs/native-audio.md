# Experimental native Resolve audio controls

These Lua MCP operations use Resolve's installed scripting API. They do not
generate replacement PCM files or perform external signal processing.

- `resolve_lua_open_audio_page`: select an exact timeline and open Edit or
  Fairlight. Verifies the selected timeline identity and returned page.
- `resolve_lua_native_audio`: use an existing, uniquely named Fairlight preset,
  normalize exact audio items, or run the bounded phrase pilot described below.

All writes require the project UUID, confirmation and a stable idempotency key.
The shared transport saves/exports a backup before mutation. Work on a duplicate
timeline. A failed write may leave a partial copy; inspect its receipt and backup,
and never replay it with a new key.

## Normalization and presets

`native_audio.operation="normalize"` requires `track_index`, `items` containing
exact `index/start_frame/end_frame`, a `normalization_mode` supported by
`Timeline.GetNormalizeAudioModes()`, `target_loudness` (-24..-14 LKFS), and
`target_peak` (-12..-1 dBFS). The bridge calls `NormalizeAudioLevel` with independent
item levels. Resolve determines the meaning of the selected mode and targets;
these settings are not a separate true-peak limiter. Gain and layout readback
are checked, but actual output peaks must be measured separately.

`operation="preset"` requires an exact `preset_name` returned by
`Resolve.GetFairlightPresets()`. It uses
`Project.ApplyFairlightPresetToCurrentTimeline()`. The API does not expose
creation of a new preset or direct compressor/limiter parameters. A preset must
be authored and reviewed in Fairlight first. A preset that changes track/item
layout causes a failed postcondition; the checkpoint remains available.

Native API success is not proof of perceptual balance. The MCP result explicitly
marks audio QA pending. Whole mixed-clip normalization cannot independently
balance a microphone and TTS within that clip.

## Bounded phrase pilot

`operation="phrase_pilot"` accepts one exact `item_index/start_frame/end_frame`
and 1..128 contiguous `regions` with `start_frame/end_frame/normalize`. It requires
one enabled audio track, unity source clip gain and 60 fps source media. It copies
source references onto a muted A2, partitions only the selected source item,
then uses Resolve's ITU-R BS.1770-4 independent normalization at -19 LKFS.
The read-back gain must remain within +/-4 dB. A1 is retained and disabled only
after coverage/normalization checks pass, then A2 is enabled. A failure during
insertion or normalization leaves A1 enabled and the partial A2 muted.

Choose phrase boundaries inside measured silence with speech handles. No fades
are automatically inserted. Regions with simultaneous voices are still a mixture,
not separate speakers. Before scaling up, render short native previews and verify
speaker loudness, output peaks, noise, joins and sample/frame alignment. The pilot
is not a full-timeline leveling command or a listening-QA substitute.


## Full phrase batches

`operation="phrase_batch"` accepts `source_count`, `first_item`, `last_item`
(up to 20 source clips per batch), `expected_a2_count`, ordered `partitions`,
and `finalize`. Each partition has the same exact bounds and regions as the
pilot. Every region additionally requires a finite `max_gain_db` (-24..4),
calculated from measured source peaks before submission. Normalization still
runs inside Resolve. Native clip gain is bounded to -4..+4 dB, with a lower
upper limit when the measured peak cap requires it. A negative cap also reduces
an otherwise unnormalized region; quiet regions with positive caps stay at unity.
This is not a compressor or limiter and does not separate overlapping voices.
Use `timeout_seconds=600` for phrase batches; native insertion, normalization,
backup and result export may take several minutes. A client timeout does not
prove that a mutation failed.

The adapter opens Edit before changing track enable state. Intermediate batches
leave original A1 enabled and new A2 muted. Only the final batch verifies full
contiguous coverage and switches to A2. Original clip edge fades are copied;
internal phrase splits use measured quiet boundaries without added fades.
Source time and subframe left offset are checked to 0.0001 frame: on the tested
Resolve build, `GetSourceStartFrame()` can truncate 65.1 seconds to 3905 instead
of 3906 at 60 fps. Integer-only comparison must not drive source edits.

A failed batch must be reconciled, never replayed with a fresh key blindly.
`discard_failed_batch` deletes only A2 after matching every supplied exact item
and confirming it is muted, unlinked, and all gains are still zero. A1 remains
intact. This cleanup is for a failed, unnormalized insertion, not completed work.

The October 20 pilot had zero measured sample lag, unchanged quiet-region RMS,
and identical sampled video frames. The two transcript-inferred speaker regions
changed from a 3.06 LU difference to 0.36 LU. This was measured QA, not listening
approval. Full-timeline application and representative render QA are tracked in
the task report; do not infer them from the pilot result.


Full application was subsequently verified on all five sessions: 157 source
clips, 2413 contiguous regions and 1041 normalized speech regions. All five short
native A/B previews had zero measured sample lag and identical sampled video
frames. Output 4x peak estimates in those previews ranged from -8.30 to -6.10
dBFS. The final control passage's speaker difference was 0.26 LU. These are
representative measurements, not a full-film render or subjective listening QA.

## Experimental voice isolation probe

`operation="voice_isolation"` accepts `track_index` (1..64), `enabled`,
`amount` (0..40 for a gentle pilot), and an exact `expected_state` containing
`isEnabled` and `amount`. Run on a duplicate timeline. The adapter selects the
target, checks track routing, calls the native setter, reads back the state,
and verifies that clip layout and gains have not changed.

State acceptance is **not** proof of audible processing or license availability.
On the tested Resolve 21.1.0.17 installation, a native ten-second VI20 preview
completed successfully, but measured background RMS changed by 0.0 dB and
background spectral measurements were identical to the balanced baseline.
Speech loudness changed by only -0.0035 LU, with zero measured sample lag.
This pilot did not establish useful noise suppression; do not advertise it as
verified cleanup. No subjective listening approval was obtained.

Balanced-timeline duplication supports up to 10,000 items and compares routing,
clip enabled state, and audio gains. On the tested build, querying track enabled
state on an inactive timeline returned false even for enabled tracks; the
duplicate verifier therefore selects each timeline before reading its routing.
