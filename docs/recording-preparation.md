# Preparing a microphone recording through MCP

Ask the assistant for a concrete preparation task, for example:

> Find or create the mono track "Voice RAW" in my voice timeline. Keep Fairlight
> open and move the playhead to 01:00:00:00. Do not start recording or process audio.

The assistant first reads the current project identity and inspects the named
track. It reuses a matching mono track or appends one new mono track. An existing
stereo track or duplicate name is a conflict. Existing clips are preserved;
inspect their count before choosing where to record a new take.

## Available tools

| Tool | Purpose |
| --- | --- |
| `resolve_lua_inspect_audio_track` | Find one exact named track; read its index, subtype, clip count and project sample rate when available. Missing tracks are explicit. |
| `resolve_lua_ensure_mono_audio_track` | Reuse a matching mono track or append/name one, using the exact expected audio-track count. |
| `resolve_lua_seek_timeline` | Seek to `HH:MM:SS:FF`, preserving the current supported page unless `page="fairlight"` or `"edit"` is requested. |
| `resolve_lua_set_recording_media_root` | Set project media location to an existing allowed directory. This affects the whole project media root; Resolve may create subfolders. |
| `resolve_free_windows_recording_status` | With Windows UI opt-in, read the actual main Record, Play and Stop controls. |
| `resolve_free_select_audio_input` | With Windows UI opt-in, select the microphone in Preferences and reopen Preferences to verify it. |
| `resolve_free_get_audio_processing` | Read the master EQ or Dynamics switch on one exact track or bus. |
| `resolve_free_set_audio_eq` | Enable or bypass the master EQ switch; reopen its window to verify. |
| `resolve_free_set_audio_dynamics` | Enable or bypass the master Dynamics switch; reopen to verify. |
| `resolve_free_get_audio_input_patch` | Read the actual source name/channel assigned to one mono track. |
| `resolve_free_patch_audio_input` | Assign one physical input channel to one mono track and reopen Patch I/O to verify. |
| `resolve_free_get_track_record_arm` | Read one track's R switch without touching global Record. |
| `resolve_free_set_track_record_arm` | Set one track's R switch and reacquire its row to verify. |

Native writes require `expected_project_id`, `confirm=true` and a stable
`idempotency_key`. They save/export a project backup before changing state.
After a timeout use **identical arguments and key**: the track may already exist.
Do not create another track by retrying with a new key.

New native operations require a freshly prepared session advertising
`recording_api_v1`. Updating Python alone does not update an old immutable native
menu script. Follow the new-session steps in the [Free guide](free-mcp-backend.md).

The seven new Fairlight tools are **implemented, live acceptance pending**.
Their contracts, guards, receipt recovery and synthetic UI models are tested
offline. They have not yet been accepted against the real Windows interface.
Do not treat registration or passing automated tests as proof of working audio.

## Enable the Windows audio controls

The marker data channel remains independent of GUI control. A separate, fixed
Windows UI adapter is disabled by default. Add the opt-in argument to your MCP
server configuration and refresh the connection:

```toml
args = ["--backend", "free-marker", "--runtime", "C:/DaVinciAgent/runtime/session-002", "--enable-windows-recording-ui"]
```

The Fairlight profile targets the English Resolve **21.1** interface on Windows.
Painted controls use local Windows OCR and scoped screen pixels; no screenshot
is sent to a cloud service. Missing English OCR support, unknown geometry,
ambiguous labels or unreadable state cause a refusal rather than a guessed click.
Keep
Resolve visible and leave the mouse/keyboard idle during a settings operation.
Open the required timeline in Fairlight and show the relevant mixer channel or
track header. Inspectors navigate owned dialogs but do not switch timelines.
Close any existing Preferences, EQ, Dynamics or Patch I/O dialog first; the
adapter refuses existing dialogs to preserve your pending edits.

For example:

> Check that Resolve is stopped. Select the input named exactly
> "Microphone (USB Audio Device)" and verify the saved selection. Do not record.

Use the actual device name. A device change can clear track input patches; the
response flags that repatching may be necessary. Selecting an already-active
device avoids reselecting or saving it and verifies its current value instead.

The adapter checks the Resolve process/project window, stopped transport,
foreground, cursor stability and actual UI element before clicking. It verifies
input selection after closing/reopening Preferences. Callers cannot provide
scripts, selectors, coordinates or keystrokes.

If the input operation is interrupted, repeat the original tool, arguments and
key. Recovery inspects the current input without selecting or saving anything.
It can reconcile the desired input state; a mismatch remains uncertain and
requires inspection. A recovered state does not establish who changed it.

## Connect and prepare a track

First inspect the named track with `resolve_lua_inspect_audio_track`. Use its
native index as `expected_track_index` for every Windows track operation. A
duplicate name, a moved track or an unknown subtype is a conflict. Track indexes
and source channels are bounded to 1..64. Patch currently supports **mono tracks
only**; bus patches and stereo channel splitting are not implemented.

Read the full source label and channel in Patch I/O. Use the source name without
its numeric channel prefix, rather than guessing from a shortened mixer label.
For example, after identifying the real input, a client can request:

```json
{
  "timeline_name": "Voice",
  "track_name": "Voice RAW",
  "expected_track_index": 2,
  "source_name": "Microphone (USB Audio Device)",
  "source_channel": 1,
  "expected_project_id": "<fresh native project id>",
  "idempotency_key": "voice-input-001",
  "confirm": true
}
```

This is the input to `resolve_free_patch_audio_input`. Replace all example names
and the project ID with actual inspected values. Patch readback comes from the
destination assignment in a reopened list view. Merely finding the desired
source in the choices does not verify a connection.

To bypass processing, call `resolve_free_set_audio_eq` and
`resolve_free_set_audio_dynamics` with `enabled=false`, the timeline,
`target_name="Voice RAW"`, `scope="track"`, its exact index, fresh project ID,
`confirm=true` and **a distinct stable key for each change**. Repeat for
`target_name="Bus 1"`, `scope="bus"`, omitting `expected_track_index`.
These tools change only each module's master switch; band values and dynamics
parameters are preserved.

Finally, `resolve_free_set_track_record_arm` with `armed=true` prepares the exact
track's **R** switch. It does not arm or disarm other tracks, press the global
Record control, start playback or capture audio. Use `armed=false` to disarm it.

Each first Windows mutation checks stopped transport, saves/exports a native
project backup and opens the requested timeline in Fairlight. The UI action is
serialized against native mailbox requests, then checked against fresh native
project and track identity. For a changed setting, the adapter invokes Save
Project through its owned File menu after successful UI readback. This is
reported as `save_status="ui_save_invoked"`, not proof of persistence after
reopening the project from disk. `persistence_verified` remains false. A matching
no-op does not toggle the control and reports `save_status="not_needed"`.

## Recover an interrupted change

Some MCP SDK versions return a generic tool error without the internal exception
text. Use `resolve_free_pending_requests` and the original receipt to inspect the
outcome. A generic error does not establish that no change occurred.

Repeat the original tool with **identical arguments and idempotency key**.
A completed receipt returns its original result. A pending receipt invokes only
the corresponding getter; it never repeats a toggle, patch or arm action.
If the current state matches the requested state, the result is marked
`state_reconciled=true`. Otherwise the receipt stays pending and the tool reports
a mismatch. Inspect the project and its backup before deciding how to recover.
A new key or a new runtime is not a recovery procedure.

Reconciliation proves the observed state, not who changed it or whether the
original save succeeded. It reports `save_status="not_reverified"` and does not
invoke Save Project. A failed save or
lost project identity can leave a pending receipt even when the control changed.
Original UI evidence and later reconciliation evidence are retained separately.

## Preparation is not a completed sound check

Insert-effect bypass, Voice Isolation, Dialogue Leveler, Ducker, the 32-bit float
recording switch and soft-fade settings are outside these tools. An EQ or Dynamics
master bypass does not disable other processing. Check those controls separately
before a take, along with the live-pending Windows operations above.

No tool here starts recording or captures microphone audio. Connection success,
a mono track and stopped transport do not prove clean input. Responses retain
`recording_readiness="not_assessed"` or `recording_ready=null`. A missing sample
rate is unknown, not an assumed 48 kHz; unqueried track counts are also unknown.
Confirm the patch, bypass states and actual voice level separately. Verify the
WAV format and sound quality from a later recorded sample.
