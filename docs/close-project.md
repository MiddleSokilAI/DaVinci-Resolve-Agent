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
native acknowledgement and `GetCurrentProject()` returning no project. The
result includes the original identity, `closed`, `saved`, backup path and evidence
scope. It never deletes projects or media and never exits Resolve.

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
