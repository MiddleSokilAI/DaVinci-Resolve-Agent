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

Native writes require `expected_project_id`, `confirm=true` and a stable
`idempotency_key`. They save/export a project backup before changing state.
After a timeout use **identical arguments and key**: the track may already exist.
Do not create another track by retrying with a new key.

New native operations require a freshly prepared session advertising
`recording_api_v1`. Updating Python alone does not update an old immutable native
menu script. Follow the new-session steps in the [Free guide](free-mcp-backend.md).

## Enable Windows input selection

The marker data channel remains independent of GUI control. A separate, fixed
Windows UI adapter is disabled by default. Add the opt-in argument to your MCP
server configuration and refresh the connection:

```toml
args = ["--backend", "free-marker", "--runtime", "C:/DaVinciAgent/runtime/session-002", "--enable-windows-recording-ui"]
```

This adapter currently targets the English Resolve interface on Windows. Keep
Resolve visible and leave the mouse/keyboard idle during a settings operation.
It refuses an already-open Preferences dialog to preserve your pending edits.

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

## Preparation is not a completed sound check

Input **patching**, record **arm**, track/bus **EQ**, **Dynamics** and insert-effect
bypass remain unsupported by these tools. No documented native setter was found,
and the current UI adapter does not provide verified setters for those controls.
Check them in Fairlight before a take. The same applies to the 32-bit float
recording switch and soft-fade settings.

No tool here starts recording or captures microphone audio. Connection success,
a mono track and stopped transport do not prove clean input. Responses retain
`recording_readiness="not_assessed"` or `recording_ready=null`. A missing sample
rate is unknown, not an assumed 48 kHz; unqueried track counts are also unknown.
Confirm the patch, bypass states and actual voice level separately. Verify the
WAV format and sound quality from a later recorded sample.
