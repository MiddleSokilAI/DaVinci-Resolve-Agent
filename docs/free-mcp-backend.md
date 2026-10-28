# Candidate Free MCP backend

`experimental_free_marker` is a proposed backend identifier, not an implemented
MCP server, CLI flag or production protocol. The existing Python/file-loader
and resident Lua routes retain their own compatibility limits.

## Measured primitive

On the tested Windows Free 21.1.2.10 installation, a self-contained Lua script
launched from Scripts confirmed a known marker existed and an absent nonce
marker did not. It exported `ready`, then cooperatively waited for a fixed
external trigger. The host published that trigger only after readiness; the
script detected it with `bmd.fileexists` and exported a correlated acknowledgement.
The existing bounded project-snapshot parser validated the acknowledgement's
project identity. The finite loop returned normally. No project/audio mutation,
recording, file loader, preferences write or payload-code evaluation was used.

This verifies one read-only native command signal and response, on that exact
installation. It does not verify typed payload transfer, arbitrary operations,
MCP stdio dispatch, a persistent connection, restart/reconnect, mutation/replay
or UI responsiveness during a resident loop. See the [canonical evidence entry](manual-integration-testing.md#finite-free-marker-transport-probe--2026-10-29).

## Candidate architecture

The menu script would be self-contained, with fixed allowlisted handlers and
a bounded data decoder. A host-side typed client would serialize each request
into a private marker slot; the script would read only predefined marker names
through `bmd.fileexists`. Markers would carry schema-validated data, never Lua,
Python, shell text or an instruction to evaluate a supplied expression.

Native exports would provide request-correlated responses, with bounded parsing
and explicit project identity checks. Exporting a project can be expensive;
latency, archive size and retention must be measured before relying on this for
routine discovery or long editing sessions. Successful export is not evidence
that every advertised Resolve operation is supported.

The marker serialization and message schema are still to be designed and tested;
the single fixed trigger probe must not be promoted into a permanent protocol.

## Smallest implementation slice

1. Introduce an explicit connection/backend boundary and select this mode only
   when its native capability probe passes. Keep cached state separate from a
   fresh request/acknowledgement. Do not fake a Python-bridge heartbeat.
2. Serialize a single request slot with owner/lease information. Define session
   identity, request nonce, expiry and integrity checks. Publish a complete
   marker set before its ready/commit marker; reject stale, partial, malformed
   or wrong-session requests. Reconcile an uncertain write before reuse.
3. Freeze a small bounded schema: fixed action IDs, field types, counts and
   payload limits. Start with read-only project identity and diagnostics.
   A checksum must not be described as authentication for a shared directory.
4. Add MCP dispatch through the typed application boundary and explicit
   capability/liveness reporting. Advertise only implemented operations and
   configure the supported server in the actual client/workspace scope.
5. Before adding mutations, retain confirmation, exact project/timeline guards,
   unique backups, durable receipts, readback and stable idempotency keys.
   An acknowledgement must distinguish acceptance from verified completion.

## Acceptance gates

- Real MCP `initialize`/`tools/list` and a read-only tool call through the new
  backend, correlated to native export and the expected project identity.
- Single-slot contention, duplicate delivery, stale nonce, expired/partial
  markers, invalid schema/size/integrity, wrong project and export failure.
- Client disconnect/reconnect, process death, lease reconciliation, explicit
  stop and restart without silently replaying a pending mutation.
- For later writes: backup/readback, exact-key replay without duplicate edits,
  and rejection before mutation when confirmation or capabilities are missing.
- Live bounds for latency, storage, cancellation and UI responsiveness; a
  passing Lua/API-double test or one finite signal is insufficient for these.

Physical microphone selection, input patching, record arming and track/bus
EQ, Dynamics and effects remain separate unsupported package capabilities.
A working backend must report those limits instead of declaring recording ready.
