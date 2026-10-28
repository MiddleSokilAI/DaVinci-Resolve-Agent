# Console compatibility without disk Lua loaders

Some Resolve Lua console contexts expose the injected `resolve` API and
`loadstring`, but neither `dofile` nor `loadfile`. A resident file-mailbox bridge
cannot start there. Do not change scripting or security preferences to enable it.

`providers.resolve.lua_console.console_command(runtime)` prepares a single
console command from a request published by the existing typed
`LuaSnapshotClient.request` method. The caller must submit the generated command
to the internal Lua console while that client is waiting. Only use a trusted,
newly prepared local runtime; its generated modules and mailbox are executable
inputs and must not come from a third party.

The command embeds the fixed session modules and an immutable request snapshot.
No disk loader is used inside Resolve. Existing argument validation, project
identity guards, expiry checks, backup exports, write receipts and response
exports still apply. Repeated request IDs are ignored within the same Resolve
process and session. After a restart, reconcile receipts and prepare a new
runtime; do not resubmit an old command to recover an uncertain write.

This is one-shot console mode, not a persistent connection. The supported actions
are `ping`, `get_current_project`, `create_timeline`, `set_clip_properties` and
`get_timeline_summary`. Render jobs and operations requiring resident shared
job state are excluded. Unsupported bridge templates fail before command
generation, including when the duplicate-request guard cannot be located.

The existing file-based transport also supports a chunk-local `loadfile`
fallback when only `dofile` is missing. It does not modify interpreter globals
and fails explicitly if neither loader exists.

See [the manual integration matrix](manual-integration-testing.md#console-mode-without-disk-lua-loaders--2026-10-29)
for the measured subset. Hardware microphone selection, input patching,
record arming and EQ/Dynamics/effect verification are outside this adapter.
Console transport success must not be described as a recording-ready session.
