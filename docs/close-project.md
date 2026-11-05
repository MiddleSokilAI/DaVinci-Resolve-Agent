# Close the current project

`resolve_close_project` closes a project while keeping Resolve running. The
canonical MCP tool works through the Python SDK or the managed Lua SDK adapter
under `--backend auto`; the legacy marker-only catalogue does not register it.

Arguments:

- `expected_project_id`: the exact native project UUID, obtained from fresh
  `video_agent_capabilities(verify_connection=true)` evidence.
- `confirm_close`: `true` only after the user asks to close that project.
- `idempotency_key`: a required stable key, reused on all retries of this request.
- `timeout_seconds`: the command timeout, default 30 seconds.

The command verifies native identity and rendering state, saves the project,
exports a managed `.drp` backup, checks the identity and rendering state again,
saves again, then invokes `ProjectManager.CloseProject(project)`. There is no
option to discard unsaved changes. A successful result requires both a true
native acknowledgement and `GetCurrentProject()` returning no project or a
verified empty unsaved `Untitled Project` replacement. The replacement must have
a different canonical UUID, zero timelines, no current timeline, no rendering,
an empty Media Pool root, no subfolders and no saved name in the current project
list. The native identity is checked again. A named, saved or nonempty project
never counts as this placeholder. The
result includes the original identity, `closed`, `saved`, backup path and evidence
scope. It never deletes projects or media and never exits Resolve.

Some Resolve Free installations create that blank replacement after an
acknowledged close. The agent does not save or close it. A later confirmed
`resolve_create_project` may replace the verified blank context without saving
an extra `Untitled Project`. The Lua SDK checks this exception natively before
CreateProject, in addition to the Python checks and the normal native read-set
guard. Backup requirements remain in force for every saved or nonempty project.

For an original `PROJECT_CLOSE_READBACK_FAILED` from the managed Lua SDK route,
the same key may reconcile the original result using getters only. Recovery
requires matching original generation/session/fingerprint, a retained failed
command and backup, and native trace acknowledgements of SaveProject,
ExportProject and exactly one CloseProject. Request bytes and response binding
must match. Fresh native readback must show no project or the verified blank
replacement. Missing acknowledgement or changed content retains the pending
owner; recovery never submits CloseProject again or switches transport.

If the original session has already been marked `resolve_absent`, recovery can
instead verify that Resolve has exited. It requires the same original native
acknowledgements and backup, valid original process identity, two successful
read-only Windows process probes showing no Resolve processes, and unchanged
session ownership. It records `original_native_close_ack_and_process_exit` and
does not launch Resolve or send a native call. Unavailable metadata, a running
process or changed ownership keeps the receipt pending.

Before finalizing host receipts, recovery archives the original error and
ledgers, native trace hashes and the read-only proof. A host crash resumes that
archived outcome without another native call. Completed replay remains
historical evidence. Expected close errors retain their code in MCP instead of
a generic tool failure.

The self-contained Lua bundle in an existing native session is immutable.
Updating installed Python files does not change its native backup policy. Load
the updated Lua policy in a fresh native session; preserve pending receipts and
do not reset dispatch flags to simulate a restart.

A repeated key with identical arguments returns the original result without a
new save, backup, close, UI operation or connection bootstrap. The automatic
endpoint labels that replay `historical_completed_receipt`; it is not proof of
which project is currently open. Different arguments with the same key fail.
A timeout after mutation retains the original SDK evidence and operation gate;
neither a new key nor a transport switch can resubmit the uncertain closure.

In managed mode a durable close intent is published before native dispatch.
After success, normal requests cannot reopen project history or launch Resolve
from an absent process. Background SDK refresh is suspended. The agent can
connect to a project already opened by the user. A freshly verified explicit
`resolve_free_open_project` clears the automatic-open hold; an old receipt replay
cannot clear it. Unknown close outcomes require original receipt recovery.
A close request from Project Manager never opens a project merely to close it.

## Validation scope

Unit tests cover the canonical handler, SDK proxies, the actual Lua 5.1 adapter,
required native backup, method authorization, post-close graph refresh, stable
replay, changed identity, active render, failed save/export, uncertain dispatch
and automatic-open suppression. Editor-free stdio coverage verifies registration.
Live acceptance inside Resolve remains pending until the change is merged and
installed from upstream main; an unmerged branch must not replace the working
agent.
