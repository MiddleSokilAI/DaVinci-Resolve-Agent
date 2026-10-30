# Automatic Resolve connection

The standard `davinci-agent-mcp` command defaults to `--backend auto`. It exposes
the union of the main workflow and managed Free tool catalogues: 156 distinct
tools. Discovery and offline workflows do not launch Resolve.

For a native operation, the agent starts its hidden per-user watcher, launches
the official Resolve executable when needed, and connects the fixed managed
bridge. It can reopen an exact saved project from a previously verified project
open receipt, including its UUID, database and folder. It does not guess a
project when that history is absent. An already open project is preserved.

No manual console, script launch, protocol selection or per-operation MCP
reconnection is needed. Existing clients of the canonical filesystem queue can
use the worker without replacing their cached tool catalogue. Client discovery
is still necessary to learn newly registered tool names.

## Configuration

Use the installed `davinci-agent-mcp` executable as the stdio command, with no
backend arguments. An existing managed profile can be supplied with
`--managed-profile <absolute-profile.json>`. The default profile lives at
`<data-root>/config/managed-free/profile.json`; its runtime matches the configured
agent runtime. Initial media scope comes from existing local media configuration.
An explicitly disabled profile stays disabled.

`--enable-windows-recording-ui` enables the existing fixed Windows audio-control
adapters. It does not start recording. Those adapters retain their own preflight,
confirmation and live-acceptance restrictions. Legacy `--backend python` and
`--backend free-marker` remain available for compatibility and diagnostics.

## Native transport

The worker probes Resolve's bundled external Python SDK once per OS process
identity. If it returns a Resolve object, the canonical command runs through
that SDK after matching its project UUID to fresh Lua evidence from the owned
native session. A mismatched binding never claims or executes the command.
Otherwise, the same `ResolveBridge.py` command handlers run on the host
against opaque SDK object proxies in the native Lua bridge. This gives the main
workflows their original command, receipt, backup and readback semantics on Free.

The Lua channel accepts only tagged JSON values, object references and an explicit
SDK method allowlist. It cannot evaluate incoming source or run arbitrary methods.
Input uses bounded marker packets; output uses the versioned name header of
temporary exported UI presets. No layout preset is loaded or applied. Owned
temporary preset names are removed after export. Opaque layout bytes are never
interpreted. Files remain local to the owned runtime.

## Write and recovery guarantees

- One coordinator lock covers each complete native operation.
- A durable key belongs to its original request, transport and native generation.
  SDK ownership uses a versioned record rejected by older marker clients too;
  switching tool families cannot bypass the original receipt, even in one session.
- Native Lua verifies a successful project backup before content mutations and
  rechecks observed SDK values immediately before mutation.
- A completed operation returns its stored result without another SDK call.
  Optional state/discovery refresh failures preserve a known canonical result.
- A failed guard publication before SDK dispatch stores a terminal rejection and
  releases its owned guard; unrelated subsequent commands remain usable.
- A timeout or failure after write dispatch retains its evidence and gate.
  It never retries the write through another transport or a new key.
- Rotation refuses occupied SDK slots and uncertain write records. Lock files
  are persistent OS advisory-lock files; their presence is not a stale-lock signal.

Known clean native exits can create a fresh managed session automatically.
Ambiguous writes and uncertain script dispatch remain blocked for inspection;
automatic connection does not turn an unknown outcome into permission to repeat.

The automatic facade validates tool names and arguments before bootstrap. Its
media policy is loaded lazily from existing approved local roots and published
to the selected profile's runtime, including custom runtime locations.

## Capability evidence and validation

`video_agent_capabilities(verify_connection=true)` reports fresh native
availability across both catalogues. SDK bindings are observed from actual
objects, cached by product/version, and combined with the existing per-operation
preflights. Registration and a cached heartbeat alone do not prove availability.
Resolve edition restrictions and missing SDK methods remain restrictions.

Live acceptance on Windows / Resolve Free 21.1.2.10 covered the complete hybrid
bundle: project identity, timeline/media/item reads, creation of an isolated test
project with a real backup, timeline creation and replay without another SDK
call. The real worker also imported a generated PNG, appended it to the scratch
timeline and changed its position/zoom/opacity with native readback. The automatic
MCP facade confirmed its connection and reported 33 available workflows in that
test project; UI adapters and legacy media roots were deliberately unconfigured.
The original project was restored and the installed upstream-main bridge
reconnected. Studio external-SDK routing is covered with API doubles; it has not
been exercised against a Studio installation. This is not a claim that every
editing workflow has been visually reviewed or every Resolve version validated.
