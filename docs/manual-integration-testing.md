# Ручне інтеграційне тестування

M30 визначає канонічну матрицю live-перевірок. Автоматизовані тести та
GitHub Actions перевіряють Python-код, контракти, bridge simulation й installer,
але не є доказом роботи всередині DaVinci Resolve.

Статуси:

- `verified` — сценарій фактично виконано у вказаному live-середовищі;
- `pending` — сценарій потрібний для покриття, але live-evidence ще немає;
- `blocked` — сценарій неможливо виконати з описаною перевіреною причиною.

## Матриця платформи

| Windows | Python | Resolve edition | Resolve version | Статус |
|---|---|---|---|---|
| 10 build 19045 | 3.12.10 | Free | 21.0.3.7 | `verified` |
| 10 | 3.10 | Free | 21.x | `pending` |
| 10 | 3.11 | Free | 21.x | `pending` |
| 11 | 3.10 | Free | 21.x | `pending` |
| 11 | 3.11 | Free | 21.x | `pending` |
| 11 | 3.12 | Free | 21.x | `pending` |
| 10 | 3.10–3.12 | Studio | 21.x | `pending` |
| 11 | 3.10–3.12 | Studio | 21.x | `pending` |

Verified-рядок фіксує середовище, у якому вже документувалися live M1–M20
операції. Він не означає, що кожна комбінація Python, Windows та edition
перевірена. GitHub Actions coverage для Python 3.10–3.12 залишається
автоматизованою package/bridge-simulation перевіркою, а не live Resolve
evidence.

## Матриця стану Resolve

| Edition | Project state | Timeline state | Очікуваний результат | Статус |
|---|---|---|---|---|
| Free | open | present | project і current timeline доступні | `verified` |
| Free | open | absent | project доступний, current timeline відсутній | `pending` |
| Free | none | not applicable | bridge відповідає без вигаданого project | `pending` |
| Studio | open | present | project і current timeline доступні | `pending` |
| Studio | open | absent | project доступний, current timeline відсутній | `pending` |
| Studio | none | not applicable | bridge відповідає без вигаданого project | `pending` |

`Timeline state = absent` означає відкритий проєкт без timeline.
`not applicable` використовується лише тоді, коли проєкт не відкрито.

## Мінімальний acceptance-сценарій

На кожній запланованій platform/state комбінації:

1. Виконати `installer/install.ps1` двічі.
2. Виконати `installer/verify.ps1 -SkipResolveConnection`.
3. Перезапустити Resolve та підготувати потрібний project/timeline state.
4. Запустити `Workspace → Scripts → Edit → ResolveBridge`.
5. Виконати повний `installer/verify.ps1`.
6. Виконати CLI `status` і `resolve project`.
7. Повторити read-only project operation через MCP.
8. Для write-сценарію окремо зафіксувати backup, readback та idempotent replay.

Stale cached heartbeat не зараховується як нове live-evidence. Якщо bridge
повертає `edition = "unknown"`, edition фіксується як operator-confirmed і не
видається за автоматично визначену capability.

## Evidence contract

Кожний live-запуск має зафіксувати:

- UTC timestamp;
- Windows caption, version і build;
- точну версію Python;
- Resolve product, version та спосіб підтвердження edition;
- agent, bridge і protocol versions;
- початковий project/timeline state;
- command або MCP tool і його exit code/status;
- sanitized result або ключовий readback;
- для write-операцій — backup, safety flags та replay result;
- підсумок `verified`, `pending` або `blocked`;
- відому причину failure без secrets і приватних media paths.

Evidence не повинно містити credentials, raw user configuration, приватні
media paths, повні command arguments або diagnostics bundles без попереднього
ручного перегляду.

## Поточне покриття

### Free marker project creation — automated coverage

`resolve_lua_create_project` has a separate acceptance scope from the existing
marker connection checks below. Automated checks exercise the real generated
Lua 5.1 bundle with native API doubles, marker files, DRP parsing and MCP stdio:

- Original project identity, explicit confirmation, supported session capability,
  name collisions and rendering are checked before creation.
- The old project is saved and backed up before creating and saving the new one;
  response readback matches the new project while the backup matches the old ID.
- Exact-key replay after an MCP client restart retains the original project ID
  and creates no duplicate or extra project switch. Partial creation failures
  remain uncertain rather than being treated as safe automatic retries.
- Marker lifecycle evidence follows the verified new active project after
  creation, including acknowledged stop.

Live creation in installed Resolve: **`pending`**. The earlier native read,
write and reconnect evidence does not certify this newly added operation.
Creating from Project Manager without an open saved bootstrap project is not
supported by this Free marker path. Automated API doubles do not establish
library permissions, project-name behavior or switching UI behavior on every
installed Resolve version.

### Free marker MCP backend — 2026-10-29

- Environment: Windows 10 Education 10.0.19045, host Python 3.12.10,
  Resolve Free 21.1.2.10; agent 0.1.0, native protocol 4, marker wire 1.
  The self-contained managed script was launched from Workspace > Scripts.
  Disk loaders, preference transport and a Lua compiler were not required.
- The installed `davinci-agent-mcp --backend free-marker --runtime <session>`
  completed real MCP stdio `initialize`/`tools/list` with 36 typed tools.
  Fresh native status, project identity and timeline summary matched the
  operator's already-open saved project and expected timeline; all video,
  audio and subtitle item counts were zero. Private names, IDs, paths and
  project exports remain local and are not included in this repository.
- Measured native reads: status 1.734 s, project 1.469 s, timeline summary
  1.531 s. A guarded operation selected the same timeline and opened Fairlight
  in 3.593 s, with a unique `.before.drp` backup, matching project identity,
  native state readback and a completed durable receipt. No recording,
  microphone setting, processing change or media edit was performed.
- After the MCP subprocess exited, a new MCP subprocess reconnected to the
  same still-running native session. Repeating the exact operation/key
  returned the existing verified result in 0.015 s with the same backup and
  response paths; fresh native status then passed in 1.313 s.
- The Workspace menu could still be opened through UI Automation and captured
  while the native loop remained leased. This is a bounded UI responsiveness
  check, not comprehensive Fusion/editor acceptance.
- Result: `verified` for these native read/write/replay/reconnect cases.
  Malformed packets, lease expiry, stop, lost acknowledgements and failure
  recovery have Lua/API-double and host fault-injection coverage; actual
  Resolve process death/restart, exhaustive editor compatibility and all
  advertised operation families remain separate acceptance work.
- Physical input/patch/arm and track/bus EQ/Dynamics/inserts are not native
  backend API capabilities. Their current GUI setup is a separate operator
  task and is not inferred from MCP connectivity. See [backend setup and
  limits](free-mcp-backend.md).

### Finite Free marker transport probe — 2026-10-29

- Environment: Windows 10 Education, version 10.0.19045, build 19045;
  host Python 3.12.10; agent/parser 0.1.0. Resolve reported 21.1.2.10;
  Free edition is operator-confirmed. The self-contained finite menu probe
  is not an assigned production backend/protocol version.
- Initial state: the expected saved project was open. The known-marker check
  was true, the absent-nonce check false, and `bmd.wait` was available.
- Read-only sequence: native `ready.drp` was exported at
  `2026-10-28T22:47:30Z`; the external host then published the fixed nonce
  trigger at `2026-10-28T22:47:52Z`. Native Lua detected it through
  `bmd.fileexists` and exported its correlated acknowledgement at
  `2026-10-28T22:47:52Z`. The existing bounded `read_project_snapshot`
  parser accepted the archive and matched the expected project UUID.
  Private project identifiers and runtime paths are intentionally omitted.
- Result: `verified` for this single fixed-signal native round trip. The loop
  exited through its finite return path. No project/audio changes, recording,
  preferences write, dynamic payload evaluation or write replay occurred;
  mutation backup/confirmation/replay are not applicable to this read-only probe.
- `pending`: typed payload transfer, MCP stdio end-to-end integration,
  persistent backend, reconnect/lease/stop, write receipts/readback/replay and
  UI responsiveness. Existing `dofile`/`loadfile`/`SavePrefs` restrictions
  are unchanged. See the [candidate plan](free-mcp-backend.md).

### Native audio normalization and noise probe — 2026-10-20

- Environment: Windows 10 Education, version 10.0.19045, build 19045;
  Python 3.12.10; agent 0.1.0; experimental Lua snapshot bridge, protocol 4.
  Live API reported product `DaVinci Resolve`, version `21.1.0.17`; Free
  edition is operator-confirmed, not inferred from a successful effect setter.
- Initial state: saved project open, populated 60 fps review timeline.
  Original media and prior timelines retained; work performed on checkpoint
  copies with exact project identity guards. No final-film render authorized.
- Transport: `LuaSnapshotClient.request("set_clip_properties", ...)` with
  validated `native_audio` payloads, `confirm=true`, project ID and distinct
  idempotency keys. This records native bridge acceptance, not a fresh
  end-to-end MCP stdio test of the newly added convenience tools.
- Write evidence: eight bounded `phrase_batch` requests returned `completed`
  with before/after DRP exports and native readback. Receipts reported
  `replayed=false`; a repeat-write replay was not exercised in this run.
  An earlier failed pilot was inspected and reconciled before exact guarded
  cleanup; no blind retry with a fresh key.
- Readback: 157 original audio clips preserved on disabled A1; enabled A2
  contains 2,413 native source-reference regions, including 1,041 normalized
  speech regions. Source time, bounds, gains and routing were checked;
  314 video items and three inspected privacy masks were unchanged.
  The 1,128 measured quiet regions retained unity gain. No replacement PCM
  or external DSP was imported.
- Final balanced-timeline readback recorded at
  **2026-10-20T15:43:53Z** (local evidence file timestamp):
  Edit page, expected checkpoint identity, original full timeline bounds.
  Native normalization/layout/routing: `verified` for this run.
- Five short native before/after render pairs completed and decoded:
  four 18-second passages and one 23.433-second passage. Measured sample lag
  was zero at 48 kHz; sampled RGB frames were identical. After-processing
  4x peak estimates ranged from -8.30 to -6.10 dBFS. The two
  transcript-inferred speaker passages differed by 0.26 LU, versus 3.06 LU
  before treatment. These are representative output measurements, not
  a whole-film render, speaker recognition or subjective listening approval.
- Noise probe, separate copy: native Voice Isolation track state changed to
  enabled/20 and read back successfully. A ten-second native render reached
  `complete` at **2026-10-20T16:19:03Z** (status evidence timestamp), with
  decoded video/audio and duration 10.005333 seconds. Measured background RMS
  changed by **0.0 dB**; background spectral measurements were identical.
  Speech difference was -0.0035 LU, with zero measured sample lag.
  Useful noise suppression: **`pending`, not demonstrated**. Setter acceptance
  does not establish effect processing or license availability.
- Recovery readback at **2026-10-20T16:23:39Z**: balanced checkpoint selected,
  Edit page, A1 disabled/A2 enabled. Cursor restoration returned
  `VERIFY_FAILED`; subsequent independent readback confirmed the correct
  timeline/page but a different cursor position. It was not blindly replayed.
- Fairlight preset enumeration was empty. Direct documented Noise Reduction
  controls were not found in the inspected API. UI effect setup was `blocked`
  by screenshot/activation timeouts; this is not proof that the stock effect
  or all noise reduction is unavailable. Listening QA remains `pending`.
- Sanitized evidence above was extracted from private local receipts,
  native readbacks and decoded QA reports. Raw DRPs, project identifiers,
  media paths, preference exports and recordings are intentionally not
  included in the repository.

The inactive-timeline routing issue was observed during duplication:
`GetIsTrackEnabled` returned false until that timeline was selected.
Audio preflight now selects and verifies its target before routing reads;
simulation regressions cover inactive getters and selection failures.
The generalized selection fix still needs a fresh live pass for every exported
operation; the completed normalization run is not blanket compatibility evidence.

- Resolve 21 Free 21.0.3.7, Windows 10 build 19045, Python 3.12.10,
  project open, timeline present: `verified`;
- Codex project-scoped MCP → `video_agent_status` →
  `resolve_get_project(timeout_seconds=120)` → one-shot ResolveBridge → live
  project readback: `verified`;
- Python 3.10–3.12 package compatibility: автоматизовано в Windows CI;
- Windows 11 live Resolve: `pending`;
- Resolve 21 Studio: `pending`;
- live project absent і timeline absent: `pending`.
- Resolve 21.1.0.17 Free, Windows: production Python bridge `blocked` by
  unavailable Python script launch; experimental Lua console/API and read-only
  background MCP snapshot transport `verified` only within the scope below.

### Resolve Free 21.1 Lua prototype evidence

- UTC: 2026-09-17, console project creation around 07:50, background MCP around
  08:00; Windows 10 build 19045, Python 3.12.10, agent 0.1.0;
- Resolve 21.1.0.17, Free edition operator-confirmed; experimental Lua bridge,
  isolated prototype protocol (not production command protocol 1.0);
- initial state: open saved test project; console API read its name/version;
- console write: SaveProject + successful baseline ExportProject (333762 bytes),
  case-insensitive name collision guard, CreateProject, current-name readback,
  SaveProject; created empty project visible in window title; no media imported;
- project creation: `verified` through GUI console, **not through MCP**;
  idempotent replay of the console command was not run;
- background prototype: MCP stdio initialized, listed three tools, then
  `resolve_lua_ping`, `resolve_lua_get_project`, `resolve_lua_ping` all returned
  `is_error=false`; readback matched the newly created project's name/ID;
- no GUI input between those MCP calls; no project mutation/save by prototype;
- MCP stop returned `stopping`; subsequent one-second ping timed out as expected;
- partial DRP writes are awaited; identity is read from a bounded XML header;
- startup Fusion warnings are not established as the cause of missing Python
  menu items. Lua availability does not imply Python bridge compatibility;
- production Lua editing transport, automatic bootstrap, no-project responses,
  and long-running unattended sessions remain `pending`.

Setup and limits: [experimental Lua bridge](lua-experimental.md).

### Resolve Free 21.1 Lua finishing protocol 3 — partial live verification

- UTC date: 2026-09-17; Windows 10 build 19045, Python 3.12.10, agent 0.1.0,
  Resolve Free 21.1.0.17; manual bootstrap in the existing disposable test project.
- Fresh MCP ping/project identity succeeded. A synthetic three-second 24 fps
  stereo MOV was imported and appended whole to a new isolated timeline.
- Gain -6 dB on audio item 1 and zoom 1.2 on video item 1 both returned successful
  SetProperties/GetProperties readback. Five writes retained before/after DRPs.
- No desktop input between the MCP requests. Source user recordings untouched.
- Captions initially failed immediate verification. The export did not yet show
  subtitles, but the following live attempt returned SUBTITLES_EXIST before any
  new mutation. The earlier inference of no timeline mutation was incorrect;
  exported state alone cannot settle an uncertain write. No duplicate append ran.
  Original receipts/backups and a corrected local review remain available.
- Render setup failed; the diagnostic revision narrowed this to
  RENDER_SETTINGS_FAILED after successful preset/format/mode calls. No new job,
  render start, completion or output file is confirmed. MCP stop acknowledged.
### Resolve Free 21.1 Lua protocol 4 — short finishing acceptance

- UTC date: 2026-09-17; Windows 10 build 19045, Python 3.12.10, agent 0.1.0,
  Resolve Free 21.1.0.17. Fresh manually bootstrapped protocol-4 runtime;
  existing saved disposable project, synthetic three-second 24 fps stereo MOV.
- Real MCP stdio client: empty timeline -> SRT -> whole AV append -> gain -6 dB
  -> zoom 1.2 -> prepare -> start -> status. No desktop input between requests.
- Live API summary: one video/audio/subtitle item; relative timeline 0-72 frames,
  subtitle 12-60. Caption import into existing AV is now rejected before editing.
- Fixed-module reload succeeded and preserved owned render jobs. No arbitrary
  source/path accepted. Existing startup deadline remains unchanged.
- MP4 H.264/AAC decoded: 1920x1080, 72 frames, duration 3.008 s. Decoded frame
  visibly contains the expected burnt caption. Audio RMS delta -6.037 dB.
- First start executed but export acknowledgement failed during rendering;
  later owned status confirmed complete. Retained receipt records that review,
  without resubmitting the job. A bounded wait fixed the short-job acknowledgement;
  a separate validation render passed prepare/start/status end to end.
- Local acceptance.jsonl, DRP backups, receipts and MP4 retained outside git;
  no private paths or project exports published. User recordings untouched.
- After operator-confirmed console closure, fresh MCP ping and native timeline
  summary both succeeded with expected counts/bounds, without desktop input.
- Follow-up closed-console five-minute synthetic AV test: accepted start before
  dispatch, awaiting_status while exports were unavailable, then native complete.
  H.264/AAC MP4 1920x1080, 300.010667 s, 15,768,358 bytes. Repeated start
  returned replayed acceptance without another dispatch. This replaces the
  earlier ten-second wait; acceptance does not certify execution or completion.
- Not verified: continuous render progress, live cleanup preview, persistent-loop soak
  and other Resolve versions. No desktop input occurred during the long test.
- Automated tests cover validation, owned-job correlation, replay, output decoding,
  mailbox contention and cleanup boundaries; these do not replace live API proof.

### Resolve Free 21.1 Lua editing protocol 2

- UTC: 2026-09-17 approximately 08:37; same Windows 10 / Python 3.12.10 /
  operator-confirmed Free 21.1.0.17 environment above; agent 0.1.0;
- isolated Lua session, protocol 2; saved disposable test project with existing
  timelines; fixture: generated 48-frame 24 fps video, no user recordings;
- MCP import, create empty timeline, append source frames 12–35: `is_error=false`;
  each required explicit confirmation and the exact project ID;
- all three operations produced pre-edit DRP backups and successful post-edit
  exports; Lua checked the API result, new item IDs and imported source identity;
- repeat of each identical idempotency key returned `replayed=true`; only three
  backups; independent DRP inspection found exactly one appended video item,
  source in-point 12, timeline duration field 23 (different source/timeline FPS);
- wrong-project create request returned `PROJECT_CHANGED` before a backup/edit;
- no desktop input between MCP edits; bridge stopped after the test;
- `verified` only for this bounded video fixture, current project defaults and
  completed-request replay. Source end-frame conversion, whole-clip, audio and
  mixed AV live coverage remain `pending`; no rendering or source edits occurred.
- unit coverage additionally checks uncertain-outcome blocking across client
  restarts, mismatch rejection, media roots, input bounds and missing backups.

Новий рядок можна перевести у `verified` лише після фактичного live-запуску з
evidence за цим контрактом.

### M33 Codex MCP acceptance evidence

- UTC heartbeat: `2026-07-31T14:05:14.819536Z`;
- platform: Windows 10 build 19045, Python 3.12.10;
- Resolve: DaVinci Resolve 21.0.3.7 Free, edition operator-confirmed;
- versions: agent 0.1.0, bridge 0.1.0, protocol 1.0;
- initial state: project open, current timeline present;
- MCP server/tool: `davinci-resolve-agent.resolve_get_project`,
  `timeout_seconds=120`, status `completed`;
- sanitized readback: project name `DaVinci Agent M4 Test`;
- safety: read-only operation, no backup and no project mutation expected;
- result: `verified` for the full
  `Codex → MCP → AgentApplication → filesystem transport → ResolveBridge → Resolve`
  path;
- limitation: ResolveBridge was started once from the Resolve menu while the
  MCP tool waited; background bridge startup was not tested or claimed.

### M34 guarded rough-cut apply evidence

Статус: `pending`. Автоматичні тести підтверджують SHA-256 approval gate,
preview, idempotent receipt та блокування непідтверджених API. Вони не є
доказом live timeline mutation.

Перед live write потрібен окремий явний дозвіл оператора, бо перевірка
`timeline.duplicate` створює новий timeline і `.drp` backup. Після запуску
ResolveBridge ручний сценарій має зафіксувати:

1. свіжий `video_agent_status(max_age_seconds=120)`;
2. preview approved M5 plan: статус `blocked`, без command у filesystem queue;
3. окремий disposable plan лише з підтвердженою `timeline.duplicate`;
4. backup path, ID створеної копії, readback original та copy;
5. повтор apply з тим самим idempotency receipt без другого backup або копії;
6. фактичний список API, що лишилися `blocked` для M5 plan.

Без цього evidence `timeline.duplicate` і M34 live apply не позначаються як
`verified`.

### M35 persistent bridge and editing metadata evidence

- UTC heartbeat: `2026-07-31T18:26:04.019624Z`;
- platform: Windows 10 build 19045, Python 3.12.10;
- Resolve: DaVinci Resolve 21.0.3.7 Free, edition operator-confirmed;
- lifecycle: manual menu start, `persistent`, 0.5-second polling;
- UI: operator-confirmed responsive while the bridge loop was active;
- sequential commands without another menu invocation: cached status, `ping`,
  current project and workspace snapshot;
- MCP metadata readback: timeline `M7 Render Source Test`, one video track,
  one audio track, two explicitly requested MKV assets;
- sanitized asset results: `447272` frames at `60.0` FPS and `447300` frames
  at `60.0` FPS;
- safety: metadata operation read-only, no backup and no project mutation;
- clean shutdown: `resolve_stop_bridge` returned `status=stopping`, persisted
  lifecycle state became `stopped`;
- result: `verified` on this exact environment; other Resolve versions and
  editions remain pending.

### M36 bounded timeline track preparation evidence

- UTC execution: `2026-07-31T18:44:06Z` through `2026-07-31T18:44:07Z`;
- platform: Windows 10 build 19045, Python 3.12.10;
- Resolve: DaVinci Resolve 21.0.3.7 Free, edition operator-confirmed;
- target: disposable `M36 Track Preparation Probe`, canonical timeline ID
  `454dd6ad-0114-425f-ab51-53364fcc0c32`;
- initial readback: one video and one audio track;
- operation: `ensure_timeline_tracks(video=2, audio=1)` added exactly one video
  track after exporting a separate `.drp` backup;
- result readback and independent `get_editing_metadata`: two video and one
  audio track;
- replay: the same idempotency key returned the same result and backup path,
  without another added track or backup;
- durable capability: `timeline.track.create=true` after verified execution;
- result: `verified` on this exact environment; other Resolve versions and
  editions remain pending.

### M37 target timeline frame-rate evidence

- UTC readback: `2026-08-01T08:49:18Z`;
- platform: Windows 10 build 19045, Python 3.12.10;
- Resolve: DaVinci Resolve 21.0.3.7 Free, edition operator-confirmed;
- target: `M36 Track Preparation Probe`, canonical timeline ID
  `454dd6ad-0114-425f-ab51-53364fcc0c32`;
- bounded timeline result: two video tracks, one audio track, `24.0` FPS;
- bounded source result: `447300` frames at `60.0` FPS;
- safety: read-only MCP operation, no new `.drp` backup, latest backup remained
  the M36 write from `2026-07-31T18:44:07Z`;
- result: `verified` and confirms that source FPS cannot be assumed to equal
  target timeline FPS; other Resolve versions and editions remain pending.

### M38 synchronized pair assembly evidence

- UTC execution: `2026-08-01T09:06:13Z` through `2026-08-01T09:07:04Z`;
- platform: Windows 10 build 19045, Python 3.12.10;
- Resolve: DaVinci Resolve 21.0.3.7 Free, edition operator-confirmed;
- approved M5 synchronization input: `webcam_offset_ms=0`, correlation `1.0`;
- target: one new `M38 Synchronized Pair Test`, canonical timeline ID
  `5edf4ca3-8db0-44ef-a74f-40fbe08c51ec`, target FPS `24.0`;
- source metadata: screen `447300` and webcam `447272` frames at `60.0` FPS;
- track preparation: 1V/1A → 2V/1A;
- readback: screen video on V1, screen audio on A1, webcam video on V2, all at
  timeline start; three distinct canonical TimelineItem IDs;
- Resolve clamped requested inclusive source ends by one to two frames to the
  actual stream extents; bounded readback recorded `447297` and `447270`;
- safety: five `.drp` backups for five initial primitive writes; replay returned
  the identical SHA-256 receipt with no second timeline, item, or backup;
- workflow audit: two successful `editing` records, neither containing inputs
  nor results;
- result: `verified` on this exact environment; positive/negative non-zero
  offsets remain simulation-tested, and other versions/editions remain pending.

### M39 synchronized webcam picture-in-picture evidence

- UTC execution: `2026-08-01T14:21:06Z`;
- platform: Windows 10 build 19045, Python 3.12.10;
- Resolve: DaVinci Resolve 21.0.3.7 Free, edition operator-confirmed;
- source: applied M38 receipt `09e1cdece4e1d2b8f848059422691bec6cabb9952cf351706148d9e26f048b2a`;
- target: webcam TimelineItem `2eb5e9ef-42ac-4b23-982d-04340b6197f2` on V2
  of `M38 Synchronized Pair Test`;
- bounded metadata: target resolution 1920×1080;
- normalized input: size `25%`, center X/Y `82%/82%`;
- exact readback: Pan `614.4`, Tilt `-345.6`, ZoomX/ZoomY `0.25`, ZoomGang
  enabled; previous values were Pan/Tilt `0/0`, ZoomX/ZoomY `1.0`;
- safety: one `.drp` backup, canonical SHA-256 layout receipt
  `2f196e85b811f3c9bb14ba7203b5a01f77f4316405e54dcf1b63c97f6d597894`;
- replay: backup count remained 37 before and after, with the identical receipt;
- result: `verified` on this exact environment; masks, crop, borders, Fusion,
  and other Resolve versions/editions remain outside verified scope.

### M40 synchronized screen link evidence

- UTC execution: `2026-08-01T14:25:08Z` through `2026-08-01T14:30:38Z`;
- platform: Windows 10 build 19045, Python 3.12.10;
- Resolve: DaVinci Resolve 21.0.3.7 Free, edition operator-confirmed;
- source: applied M38 receipt `09e1cdece4e1d2b8f848059422691bec6cabb9952cf351706148d9e26f048b2a`;
- target: screen video V1 `c24dbed9-da23-4333-8796-047c3d865642` and screen
  audio A1 `606cfc81-73a7-4a66-a2d7-cc57bd7c63e6`;
- primitive probe: previous links were empty, then each item reported the
  other's canonical ID and capability changed to `clip.link=true`;
- workflow: applied receipt
  `9a17e91ea9650d5ed4e3e15b78e577d03fbb252c0de0c444b0e9a78b94ab99de`;
- safety: one `.drp` backup for the primitive probe and one for the first
  workflow invocation; webcam V2 and all other timeline items were untouched;
- replay: backup count remained 39 before and after, with the identical receipt;
- result: `verified` on this exact environment; future move/trim/split behavior
  over linked items remains outside verified scope.

### M41 pause compaction preview evidence

- UTC execution: `2026-08-01T14:36Z`;
- platform: Windows 10 build 19045, Python 3.12.10;
- Resolve: DaVinci Resolve 21.0.3.7 Free, edition operator-confirmed;
- inputs: approved M5 plan
  `c0d5d8a6bb6ce60fd085faceb9ad8a9a69290dac1cec07a15bf2c064dd548793`
  and applied M38 receipt
  `09e1cdece4e1d2b8f848059422691bec6cabb9952cf351706148d9e26f048b2a`;
- metadata: target 24 FPS, both source assets 60 FPS, bounded source duration
  7,455,000 ms;
- cut: half-open `320..880 ms`, removed duration 560 ms;
- output preview: 7,454,440 ms / 178,907 target frames, two kept intervals,
  six exact V1/A1/V2 placements;
- readiness: no unsupported future apply capabilities reported;
- safety: backup count remained 39 before and after; no timeline or item was
  created, modified, or deleted;
- bridge reliability: after installing the M41 bridge, 2,000 concurrent state
  reads completed and the bridge remained `ready`; the following online
  `verify.ps1` reported a 0.3 s heartbeat age;
- result: `verified` read-only preview on this exact environment; actual
  compacted timeline creation was verified separately in M42.

### M42 pause compaction apply evidence

- UTC execution: `2026-08-01T16:09Z`;
- platform: Windows 10 build 19045, Python 3.12.10;
- Resolve: DaVinci Resolve 21.0.3.7 Free, edition operator-confirmed;
- inputs: M41 plan
  `c0d5d8a6bb6ce60fd085faceb9ad8a9a69290dac1cec07a15bf2c064dd548793`
  and applied M38 receipt
  `09e1cdece4e1d2b8f848059422691bec6cabb9952cf351706148d9e26f048b2a`;
- preview: `apply_supported=true`, no unsupported capabilities, one 560 ms cut,
  two kept intervals, six placements, and 178,907 target frames;
- target: `M42 Pause Compaction Apply`, canonical timeline ID
  `7e430372-841d-4f6f-99ac-8647f6d75a31`;
- apply receipt:
  `b645865e2743554f2f20e5cc3109b72e7a86b6017aef620fd779509f859fe33d`;
- result: create timeline, ensure tracks, and one six-placement batch insert all
  reported `applied`; final `list_timeline_items` found 6/6 canonical IDs;
- source safety: project timeline count changed 7 to 8, while all 3/3 canonical
  M38 source items remained present;
- backups: 39 to 42, exactly one distinct `.drp` for each of the three write
  steps; immediate replay remained at 8 timelines and 42 backups;
- frame readback: both kept segments began at target offsets 0 and 8 frames;
  the second segment used source frame 53, confirming a non-zero ranged insert;
- result: `verified` on this exact environment. Screen video/audio segment
  linking and webcam picture-in-picture propagation are not part of M42.

### M43 compacted timeline finalization evidence

- UTC execution date: `2026-08-01`;
- M43 receipt:
  `2daf23ae6a33d75f015cc9d13decca545af8410ec744979c6e3f163ccb87df32`;
- target: M42 timeline `7e430372-841d-4f6f-99ac-8647f6d75a31`;
- result: two independent screen V1/A1 link groups, two webcam PIP transforms,
  and 6/6 canonical item identities verified;
- backups: 42 to 44; immediate replay remained at 44.

### M44 finalized render preparation evidence

- UTC execution: `2026-08-01T16:59Z`;
- input: applied M43 receipt
  `2daf23ae6a33d75f015cc9d13decca545af8410ec744979c6e3f163ccb87df32`;
- M44 receipt:
  `5d03b799bdaa588b20e080f7b59dbc9ae47e617ec6a1c6f50bfbedf0c3b730c5`;
- prepared job: `926b8633-a03b-4a25-a572-f1d18680c3e5`;
- queue binding: `M42 Pause Compaction Apply`, canonical timeline ID
  `7e430372-841d-4f6f-99ac-8647f6d75a31`;
- profile: `youtube-1080p-h264-v1`, MP4/H.264, 1920×1080;
- status: `Ready`, zero percent, `rendering_in_progress=false`;
- backups: 44 to 45; immediate replay remained at 45 and returned the same
  receipt without adding another job;
- result: `verified` preparation only. Render was intentionally not started.

### M45 finalized render execution evidence

- UTC completion: `2026-08-01T18:00Z`;
- input: applied M44 receipt
  `5d03b799bdaa588b20e080f7b59dbc9ae47e617ec6a1c6f50bfbedf0c3b730c5`;
- M45 receipt:
  `c14ef11f8e90071cc0ad6d4db88cefba798095289683f664e1cf95c769b44f74`;
- job: `926b8633-a03b-4a25-a572-f1d18680c3e5`, exact M42 timeline
  `7e430372-841d-4f6f-99ac-8647f6d75a31`;
- terminal readback: `Complete`, 100%, `rendering_in_progress=false`,
  `TimeTakenToRenderInMs=2462603`;
- output: managed `M44 Finalized Timeline Test.mp4`, 2,094,070,933 bytes;
- validation: completed, managed path, and non-empty checks all passed;
- backups: start created backup 45 to 46; immediate and post-completion replay
  returned the same receipt and remained at 46 without another render start;
- result: `verified` on Resolve 21 Free 21.0.3.7 in Windows 10.

### M46 cleaned-audio integration evidence

- UTC execution: `2026-08-01T18:58Z` through `2026-08-01T19:27Z`;
- extraction receipt:
  `5c54b2eba322013e6149897be2bca4da65dff507a94dbc81e9f74c3bd80262d5`;
- job `1cb90cf4-7d70-4711-a815-a13bc7c8e920`: built-in `Audio Only`,
  Wave/lpcm, 16-bit/48 kHz stereo, `Complete`/100%, 261793 ms;
- extracted WAV: 357812000 sample frames, 7,454,417 ms,
  1,431,249,588 bytes, uncompressed PCM;
- streaming v2 report:
  `091a64d764c7406db37d4b186c9e93f3f6692a3b7f86483582990551dee38cfa`;
- processing readback: RMS `-24.746` → `-20.001 dBFS`, peak `0.0` →
  `-1.0 dBFS`, clipped samples `10` → `0`, `target_met=true`;
- integration receipt:
  `f9f037abe3ac934725960c1e9f88cb2e521c623460e4d954463d3b37af5483ab`;
- exact placement: A2 item `f62bb0e6-c2c2-46db-b0b2-01c970e3c63f`,
  timeline frames `86400..265306`, duration 178906 frames;
- both canonical A1 items reported `enabled=false`; their linked V1 peers
  reported `enabled=true` before and after the explicit safety operation;
- backups: successful extraction prepare/start used 48→50; integration import,
  track creation, insertion, two A1 disables, and two V1 confirmations ended at
  57; immediate replay remained at 57 with one A2 item;
- result: `verified` on Resolve 21 Free 21.0.3.7 in Windows 10. RMS dBFS remains
  a reference metric, not a LUFS/EBU R128 delivery claim.

### M47 local transcription and subtitle evidence

- UTC execution date: `2026-08-01`;
- environment: Resolve 21 Free 21.0.3.7, Windows 10, Python 3.12.10;
- read-only native discovery: `CreateSubtitlesFromAudio` and all required
  constants present; no initial subtitle track on M42;
- native write probe on disposable M34: Resolve returned `False`; structured
  `AUTO_CAPTION_UNAVAILABLE`, backup preserved, capability stayed unverified;
- SRT behavior probe: one local SRT imported and appended to `Subtitle 1`,
  canonical item `fba921bf-b914-482a-98c7-299761e0c160` read back;
- local backend: pinned faster-whisper 1.2.1, `small`, CPU/int8, Ukrainian;
- 60-second speech probe: 4 segments, `language=uk`, probability `1.0`;
- applied receipt:
  `811611522f48183ede4aa456864015a74af4dd859bc4488c3fff8eb88e93ffc5`;
- target: disposable M34 timeline
  `07fce28c-00ee-487d-9935-ff701405d48d`;
- readback: subtitle count `1 → 5`, all four generated Ukrainian texts with
  canonical IDs and frame bounds; import and append each created one `.drp`
  backup;
- exact append-placement recovery on M10 timeline
  `f98c1e47-fa2e-4d7d-bcdf-e402f91d0a02`: receipt
  `62c2fd44eba89aa5b9575645f169593dbe01742195340b6a8e477d8bbeaae3ad`,
  pre-write append frame `86496`, first transcript offset `183` frames, and
  expected/actual first subtitle frame `86679`;
- crash-recovery replay reused the existing import/append provider receipts:
  subtitle items stayed `4 → 4`, backups stayed `64 → 64`, and the same four
  canonical TimelineItem IDs were independently read back;
- result: local transcription → deterministic SRT → Resolve subtitle apply
  is `verified`; native Resolve AI captioning is unsupported in this Free
  environment.

### M48 declarative editing recipe evidence

- UTC execution date: `2026-08-02`;
- environment: Resolve 21 Free 21.0.3.7, Windows 10, Python 3.12.10;
- MCP recipe: `tutorial-layout-v1` over canonical M38 receipt
  `09e1cdece4e1d2b8f848059422691bec6cabb9952cf351706148d9e26f048b2a`;
- discovery returned one packaged recipe; preview returned `ready` with exact
  ordered actions `compose_webcam_picture_in_picture` then
  `link_synchronized_screen_pair`;
- applied recipe receipt:
  `bc4119ed1f0c0f940bf252d700c35d9db62058a9f8bb2f20d62519f3dd6dd8fc`,
  bound to recipe definition SHA-256
  `8cd6bb498197621d00088d557b01efda4aafcc4fac532b08273d26fba1602215`;
- both steps returned `applied`; immediate replay returned an identical receipt;
- underlying M39/M40 receipts were reused, so Resolve backups stayed `64 → 64`;
- result: packaged recipe discovery, read-only preview, confirmed execution and
  idempotent replay are `verified` through the MCP server on this environment.

### M49 standard-title and static reframing acceptance

1. Перезапусти встановлений `ResolveBridge` після оновлення його source.
2. На disposable timeline вибери один video item і виклич
   `preview_visual_treatment` з малим статичним zoom та одним title `Text`.
3. До першої title-вставки preview має показати `title.insert` як unverified.
4. Виклич `resolve_insert_title` із `confirm_insert=true` на безпечному
   timecode; перевір canonical item, backup і повернення playhead.
5. Повтори preview: `title.insert` має бути verified і `ready=true`.
6. Виклич `apply_visual_treatment(confirm_apply=true)`, звір transform/title
   у Resolve, потім повтори exact request і переконайся, що receipt і кількість
   backups не змінилися.

Зафіксуй Resolve edition/version, timeline/item IDs, timecode, receipt ID,
backup count до/після та exact readback. Не використовуй `SmartReframe`, Fusion
controls або production timeline для першої acceptance-перевірки.

### M49 live evidence

- UTC execution date: `2026-08-01`;
- environment: Resolve 21 Free 21.0.3.7, Windows 10, Python 3.12.10;
- disposable timeline: `M34 Disposable Duplicate Probe`, ID
  `07fce28c-00ee-487d-9935-ff701405d48d`;
- occupied `01:00:02:00` placement returned `TITLE_APPEND_ONLY`; item map and
  backup count stayed unchanged at 68;
- confirmed visual-treatment receipt:
  `bd62161bf24bdcd05cc5ca58bb1727a647a63f592a1f13ffd9a8c30e64790877`;
- transform readback: `ZoomGang=true`, `ZoomX=1.16`, `ZoomY=1.16`;
- standard `Text` requested at `01:01:15:00`, absolute frame 88200; exactly one
  generated video item `fbe0c09d-fa5e-432e-9701-8379ea10348b` appeared at
  frames `88200..88320`, with null source bounds and duration 120 frames;
- all prior video item start/end/duration/track snapshots remained unchanged;
- confirmed apply created two backups `68 → 70`; exact replay returned the same
  receipt and item map with backups stable at 70;
- result: append-only standard-title insertion plus static zoom/reframing is
  `verified` on this Resolve 21 Free environment. Smart Reframe, title-text
  mutation, Fusion controls and animated templates remain outside M49.

### M50 baseline end-to-end acceptance

Статус: `verified` у DaVinci Resolve 21.0.3 Free 2 серпня 2026 року. Bridge M50
не змінює, тому достатньо вже запущеної сумісної сесії ResolveBridge.

1. На одному disposable final timeline підготуй applied M43 і M46 receipt.
   M47/M49 передавай лише як опційні enhancement receipt з того самого
   timeline. Не використовуй M47/M49 receipt з M34, якщо M43/M46 належать M42.
2. Виклич `preview_baseline_edit` з M43/M46 та, за наявності, M47/M49 receipt
   IDs. Очікується
   `status=ready`, усі checks `passed`, без backup або render job.
3. Окремо підтвердь `start_baseline_render(confirm_render=true)` з новим
   `custom_name` та allowlisted 1080p/4K profile.
4. Зафіксуй M50, M44 і M45 receipt IDs, deterministic render name, job ID та
   backup count.
5. Опитуй `get_baseline_render_status`, доки він не поверне `complete`; звір
   `JobStatus=Complete`, managed path, ненульовий MP4 і повторно успішний QA.
6. Повтори exact start request: receipt, job ID та backup count не повинні
   змінитися.

Live acceptance має зафіксувати Resolve edition/version, timeline ID,
обов'язкові й передані опційні input receipt IDs, профіль, output size та
точний результат replay. Для поточного M42 acceptance застосовуй M49 лише з
static transform без append-only title; M47 не додавай, доки SRT placement на
непорожньому timeline не буде вирівняно й окремо підтверджено.

Фактичний M50 acceptance виконано на `M42 Pause Compaction Apply`
(`7e430372-841d-4f6f-99ac-8647f6d75a31`) лише з core M43/M46:

- QA: 6 passed, 0 failed;
- M50 receipt: `e291c710c569f1cdb8fc4d1cda30c5c75e898217534623a0528125924221c78a`;
- M44 receipt: `39a13fd725f6472fda51f05ddc0b43abfe48cb5ce15917dd6227fc147229224a`;
- M45 receipt: `c91e318cf83c56a5be9b84526330ef57a5180b9e188a697af113c6305eb48f2e`;
- profile: `youtube-1080p-h264-v1`, 1920×1080, H.264/MP4, 24 fps, AAC;
- job ID: `5997af88-9c3a-4d92-9f7b-858385f82e15`;
- output: `M50 Baseline Acceptance-e291c710c569.mp4`, 2 094 880 932 bytes;
- output SHA-256: `84d870ffdcf2ee2a908cc02e57d772544e13acad05bb3189856ac6579cc4eff6`;
- M44/M45 створили два safety backups; exact replay зберіг усі receipt і job
  ID без нового job.

### M51 B-roll acceptance

Статус: `verified` у DaVinci Resolve 21.0.3 Free 2 серпня 2026 року. M51 не
змінює ResolveBridge і використовує вже verified
`timeline.duplicate`, `timeline.track.create`, `clip.range_insert` та
`clip.read`.

1. Імпортуй короткий disposable B-roll video через `resolve_import_media` і
   зафіксуй canonical `asset_id` та metadata.
2. Виклич `preview_broll_plan` із completed M50 receipt, новою унікальною
   назвою timeline й одним коротким placement на V3. Перевір `plan_id`, source
   range, FPS-aware duration, timeline bounds і `apply_supported=true`.
3. Переконайся, що preview не створив timeline або backup.
4. Окремо підтвердь `apply_broll_plan` з exact `expected_plan_id` і
   `confirm_apply=true`.
5. Перевір новий duplicate timeline, video-only item на V3, canonical
   source/timeline bounds, незмінний M50 source timeline та safety backups.
6. Повтори exact apply: receipt, target timeline ID, item IDs і backup count не
   повинні змінитися.

Live acceptance має використовувати лише тестовий B-roll, який користувач
явно дозволив вставити. Автоматичний пошук або вибір медіа не входить у M51.

Read-only preview verified у Resolve 21.0.3 Free:

- M50 source: `M42 Pause Compaction Apply`
  (`7e430372-841d-4f6f-99ac-8647f6d75a31`);
- asset: `M10 Short Render Test`
  (`00b0e5ca-0db5-4398-bf28-97eb932a1fc4`), 1527 frames @ 24 fps;
- placement: source `0–47`, position `240`, V3, duration 48 timeline frames;
- plan ID: `7d6fb2369c9d6b24b3a71d857f9199927203afb0c701650ecc031394a7809b5e`;
- `apply_supported=true`, backup і новий timeline не створювалися.

Confirmed apply та exact replay verified:

- receipt: `a7a0343d4af6cb10507e5241e0476bcf28d787080aea58020d2fc67750190c7f`,
  status `applied`;
- duplicate timeline: `M51 B-roll Apply Test`
  (`019fc876-775c-429a-95d6-8b4ca741a802`);
- inserted item: `97003e7d-8b07-4477-94ee-cc0b3f17a4ef`, video V3, source
  `0–47`, timeline `86640–86687`;
- три provider operations створили три `.drp` safety backups;
- exact replay зберіг backup count `75 → 75` і timeline count `9 → 9`;
- source M50 timeline залишився з 7 items і без item на V3;
- generic timeline readback не містить `asset_id`: binding підтверджено
  immediate insert result, persistence — canonical item ID та exact bounds.

### M52 packaged animation-template acceptance

Статус: `passed` у Resolve 21.0.3 Free 2 серпня 2026 року. Автоматичні contract,
bridge simulation, MCP stdio та installer lifecycle тести доповнені live
parser, insertion, visual та replay evidence.

1. Запусти `install.ps1`, `verify.ps1 -SkipResolveConnection`, повністю
   перезапусти Resolve і один раз запусти persistent `ResolveBridge`.
2. На exact applied M51 receipt виклич `get_broll_status`, потім raw
   `resolve_get_animation_template_environment`; очікуй live source identity,
   installed/hash match і documented method presence без backup.
3. Виклич `preview_animation_template` з новою назвою timeline та timecode
   рівно на source timeline end. Зафіксуй `plan_id`; timeline/backup count не
   повинні змінитися.
4. Лише після явного підтвердження виклич `apply_animation_template` з exact
   plan ID. Перевір duplicate timeline, рівно один новий generated video item,
   `fusion_comp_count >= 1`, незмінність source items і operation backups.
5. Відкрий title у Resolve та візуально перевір прозорий фон, accent card,
   появу/зникнення анімації й доступні Title/Subtitle поля Inspector.
6. Повтори exact apply: receipt/timeline/item/backup count мають лишитися
   незмінними.

Не передавай через MCP template files, Fusion nodes/controls, expressions або
текст. Якщо parser чи insertion повертає failure, зафіксуй environment і
залиш capability unsupported/unknown замість обходу через довільний код.

Перший bounded probe 2 серпня 2026 року дав корисний negative evidence:

- Resolve 21.0.3 Free побачив installed template і повернув Fusion item з
  `fusion_comp_count >= 1` та duration 120 frames;
- запит `03:04:15:00` був на 14 frames після source end `265306`; Resolve
  повернув success для playhead selection, але вставив item на frame `86400`
  і ripple-зсунув existing tracks на 120 frames;
- bridge виявив п'ять змінених existing video items та завершив команду
  `ANIMATION_TEMPLATE_READBACK_FAILED`;
- зміни залишилися лише в disposable `M52 Animation Probe`
  (`6bb5ec22-c941-4329-b640-924c7594e840`); source M51 timeline не змінився;
- capability не була promoted; контракт виправлено на exact-end placement і
  playhead readback до insertion.

Повторний exact-end probe та high-level apply підтвердили insertion, canonical
readback і durable replay. Візуальний acceptance підтвердив card та Inspector
поля, але спершу card був поза кадром, а після виправлення координат з'являвся
різко. Заміна output binding `KeyStretcherMod.Value` на штатний `Result` не
оживила двовимірну point-криву. Наступна scalar-версія через Transform `Blend`
також залишилася статичною: покадровий аналіз наданого 60 fps запису показав
повну появу card за один кадр на `7.083 с` і незмінну opacity далі. Packaged
asset переведено на рекомендований Blackmagic для Fusion Titles Anim Curves:
`Dissolve.Mix` отримує `LUTLookup.Value`, а документований `Mirror` формує
fade-in/fade-out. Новий item `c4050675-00c3-47f7-a987-d0b84f227c5b` на timeline
`M52 Anim Curves Acceptance` пройшов візуальну перевірку. Exact replay повернув
той самий receipt `b0c0835108038962748065e6cf5c04778b99f0a7f6e04feef4f1befed69f1d76`,
item і timeline; backups лишилися `87`, timelines — `14`.

### M54 technical take-selection smoke test

1. Переконайся, що два candidate files входять до `media.allowed_roots`.
2. Виклич `analyze_take_candidates` з двома унікальними IDs. Очікуй bounded
   video/audio metrics, deterministic ranking, `pending_review` і відсутність
   абсолютних paths у result.
3. Повтори exact analysis: `selection_id` та report мають бути ідентичними.
4. Виклич `review_take_selection` лише після людського перегляду. `approve`
   вимагає candidate ID, `reject` — null; `timeline_modified` завжди false.
5. Повтор exact review має повернути той самий record; інше рішення для того
   самого selection блокується.

Для M54.2 передай той самий allowlisted файл двічі з різними candidate IDs та
парами `start_seconds`/`end_seconds` до 300 секунд. Очікуй report version `1.1`,
exact ranges біля кандидатів, sampling лише в цих ranges, однаковий selection
ID при replay і відсутність absolute path. Не виконуй review, якщо це лише
механічний smoke test, а не людське порівняння дублів.

Live smoke 2 серпня 2026 року на двох allowlisted synchronized MKV sources
повернув selection
`2548a854ab96a6b27321a979d46365dce0cf909068ba356005686fe4e4dad679`.
Exact replay, `get`, `list`, 7/7 video samples, bounded audio і path redaction
підтверджено. Review не записувався, бо camera/screen sources не є альтернативними
дублями й потребують іншої creative інтерпретації.

M54.2 same-file segment smoke використав ranges `60–70` і `90–100` секунд із
allowlisted MKV на G. Report `bf51e004d…` має version `1.1`, 7/7 video samples
для обох кандидатів, 480000/479968 bounded audio samples, redacted path і
ідентичний exact replay. Artifact збережено в configured runtime на G; review
та будь-які timeline writes не виконувалися.

Для M54.3 виклич `analyze_scripted_take_candidates` з двома bounded ranges і
коротким exact `reference_text`. Очікуй report version `1.2`, policy
`script-aware-take-v1`, dialogue precision/recall/F1 і composite score. У JSON
не повинно бути raw reference, transcript або absolute path. Exact replay має
повернути той самий ID; review і timeline writes під час mechanics smoke не
виконуй.

Live M54.3 mechanics smoke повернув selection `84baa10d…`: reference range має
F1 `1.0`/score `92.922`, другий range — F1 `0.153846`/score `37.955`; redaction
і `pending_review` підтверджено. Перший CPU analysis може тривати кілька хвилин,
а exact replay має читатися з local request index без повторної транскрипції.
На перевіреній машині indexed first run зайняв `224.57` секунди, replay в
окремому процесі — `1.05` секунди.

Для M54.4 спочатку виконай людський `approve` кожного selection. Передай їхні
IDs у потрібному порядку до `compose_take_sequence`. Очікуй `approved_plan`,
canonical selection/review hashes, точні ranges, `timeline_modified=false` та
`apply_supported=false`. Exact replay має повернути той самий sequence ID;
unreviewed/rejected selection повинен блокувати compose. Mechanics smoke не
потребує ResolveBridge.

### M55.1 approved-sequence source binding

1. Використай лише sequence, створений із реальних людських `approve` reviews.
2. Для кожного entry передай `{order, path}` у `bind_take_sequence_sources`.
3. Очікуй exact filename/size/fingerprint validation, `paths_redacted=true`,
   `timeline_modified=false` та `apply_supported=false`.
4. Перевір, що public receipt не містить absolute path, а private artifact
   лежить лише в configured runtime на G.
5. Повтор exact bind має повернути той самий binding ID. Змінений файл,
   неправильний order або path поза allowlist повинні блокуватися.

Live M55.1 bind лишається pending до справжнього M54 approve/sequence; mechanics
тести не створюють review від імені користувача та не викликають ResolveBridge.

### M55.2 read-only assembly preview

1. Використай лише canonical M55.1 `binding_id`.
2. Виклич `preview_take_sequence_assembly` з bounded `assembly_name`.
3. Перевір approved order, exact source ranges і безперервні output ranges від
   `0.0` до `total_duration_seconds`.
4. Переконайся, що response не містить local paths, має
   `timeline_modified=false` та `apply_supported=false`.
5. Для sources з різними FPS/resolution очікуй explicit warnings.
6. Змінений після bind файл або range поза media duration повинен блокувати
   preview до будь-якого provider call.

Live M55.2 preview лишається pending разом із M55.1: поточний M54.3 selection
ще не має людського approve. Mechanics тести не створюють approval і не
викликають ResolveBridge.

### M55.3 live target timeline frame mapping

1. Запусти persistent ResolveBridge і отримай exact target `timeline_id`.
2. Перевір `resolve_get_editing_metadata(timeline_id, [])`: очікуй live FPS,
   resolution, track counts і `assets=[]` без backup.
3. Виклич `preview_take_sequence_timeline_mapping` з canonical M55.1 binding,
   тим самим assembly name і target timeline ID.
4. Перевір, що source bounds inclusive, наступний `position_frames` дорівнює
   попередньому `timeline_end_position_frames`, а output duration відповідає
   останній позиції.
5. Очікуй `live_metadata_verified=true`, `paths_redacted=true`,
   `timeline_modified=false`, `apply_supported=false`.

Live high-level M55.3 лишається pending до людського M54 approve і M55.1
binding. Timeline-only low-level readback можна перевіряти окремо: він нічого
не імпортує та не змінює.

### M55.4 conservative Media Pool import preview

1. Виклич `preview_take_sequence_media_import` з canonical binding, assembly
   name і exact target timeline ID.
2. Перевір `media_pool_verified=true`, stable snapshot SHA-256 і відсутність
   source paths.
3. Повторні ranges одного fingerprint повинні утворити один source з кількома
   ordered `orders`.
4. Source без same-name item має action `import`; existing same-name item —
   лише `review_name_collision`, `requires_review=true`, `import_ready=false`.
5. Повторний preview на незміненому Media Pool має повернути exact plan ID.
6. Переконайся, що item/folder count, backup count і timelines не змінилися.

Live M55.4 high-level preview лишається pending до canonical M54 approve/M55.1
binding та оновленого M55.3 bridge readback. Tool не виконує майбутній import.

### M55.5 confirmed Media Pool import

1. Використай лише M55.4 plan з `import_ready=true` і збережи exact `plan_id`.
2. Зафіксуй кількість Media Pool items, timelines і project backups.
3. Виклич `apply_take_sequence_media_import` з тими самими binding, assembly,
   timeline, `expected_plan_id` та `confirm_import=true`.
4. Очікуй `status=applied`, `backup_created=true`, `paths_redacted=true`,
   `media_pool_modified=true`, `timeline_modified=false` і verified asset IDs.
5. Перевір, що Media Pool має рівно імпортовані unique sources, а timelines не
   змінилися. Receipt не повинен містити source або backup paths.
6. Повтори exact apply: receipt, item count і backup count не мають змінитися.
7. Змінений plan, collision, неповний import або недостатня source duration
   повинні блокувати завершення без timeline write.

Live M55.5 apply лишається pending до справжнього людського M54 approve,
canonical sequence/binding і окремого підтвердження write. Не створюй approve
від імені користувача лише заради smoke test.

### M55.6 confirmed duplicate-timeline sequence apply

1. Використай applied M55.5 receipt і залиш exact source timeline порожнім.
2. Зафіксуй source item count, timeline list і backup count; обери нову назву.
3. Виклич `preview_take_sequence_timeline_apply`. Очікуй
   `target_name_available=true`, порожні `blockers`, verified V1 placements і
   A1 placements лише для sources із локальним audio stream.
4. Переконайся, що plan не містить paths, raw clipInfo або caller-defined
   asset/track IDs. Preview не повинен змінити timeline/backup counts.
5. Після окремого підтвердження виклич `apply_take_sequence_timeline` з тим
   самим M55.5 receipt, target name, exact `expected_plan_id` і
   `confirm_apply=true`.
6. Очікуй новий duplicate timeline, canonical V1/A1 items, sanitized operation
   results, `source_unchanged=true` та `paths_redacted=true`. Source timeline
   повинен лишитися порожнім.
7. Повтори exact apply: target ID, receipt, item count і backup count не мають
   змінитися. `get_take_sequence_timeline_apply` повинен пройти live readback.
8. Non-empty source, existing target name, changed mapping, missing capability
   або readback mismatch повинні блокувати apply/recovery.

Live M55.6 лишається pending до справжнього M54 approve → M55.1 binding →
M55.5 import і окремого підтвердження timeline write. Mechanics tests не
створюють approval від імені користувача.

### M55.7 structural sequence QC

1. Використай лише canonical applied M55.6 receipt після успішного live get.
2. Виклич `inspect_take_sequence_qc`; очікуй `ready_for_review`, exact video та
   audio counts, `gap_count=0`, `manual_review_required=true` і redacted paths.
3. Переконайся, що `unverified_areas` містить audio processing, subtitles,
   color та visual/editorial quality: structural pass не підміняє ці gates.
4. Переглянь exact target timeline у Resolve. Лише користувач може викликати
   `review_take_sequence_qc` з `approve` або `reject`.
5. Повтори `get_take_sequence_qc` і `get_take_sequence_qc_review`: вони мають
   пройти fresh M55.6 readback. Зміна source/target мусить блокувати результат.
6. Перевір, що M55.7 не створив timeline, backup, Media Pool item або render.

Live M55.7 pending до canonical M55.6 apply та реального людського перегляду.
Mechanics tests не створюють approval від імені користувача.

### M55.8 approved managed render

1. Використай лише canonical M55.7 report з реальним immutable `approve`.
2. Виклич `preview_take_sequence_render` із safe `custom_name` та потрібним
   allowlisted 1080p або 2160p profile. Очікуй `apply_supported=true`, exact
   target timeline, deterministic suffixed filename і відсутність paths.
3. Переконайся, що preview не змінив backup/job/file counts.
4. Лише після окремого підтвердження виклич `start_take_sequence_render` з тим
   самим input, exact `expected_plan_id` та `confirm_render=true`.
5. Очікуй один prepared job та один start. Exact replay не повинен створювати
   новий backup, job або start.
6. Після завершення виклич `get_take_sequence_render_status`. Очікуй
   `status=complete`, live `JobStatus=Complete`, 100% і
   `output.validation.passed=true` для непорожнього MP4 у configured render root.
7. Reject review, змінена timeline/QC evidence, capability drift, existing
   output або live job identity drift повинні блокувати render/status.

Live M55.8 pending до canonical M55.6 → M55.7 human approve. Не створюй approve
від імені користувача заради smoke test і не рендер без окремого підтвердження.

### M53 color discovery/preview

Статус: live discovery/preview passed; confirmed apply implementation ready.

1. Після install/restart запусти persistent `ResolveBridge` і залиш поточним
   exact applied M52 timeline.
2. Виклич `resolve_get_color_environment` з M52 timeline ID. Очікуй лише media
   video items, current local version, `node_count >= 1`, node labels/LUT і
   method-presence flags; backup count не повинен змінитися.
3. Виклич `list_color_presets`, `get_color_preset` для
   `tutorial-clean-v1`, потім `preview_color_treatment` з M52 receipt ID і
   новою target timeline name.
4. Перевір deterministic plan ID, exact source snapshot, media-only targets і
   незмінні timeline/backup counts. Поточний evidence: plan `79c1730e…`, 5
   targets, timelines `14 → 14`, backups `87 → 87`.
5. Лише після окремого підтвердження виклич `apply_color_treatment` з exact
   plan ID. Очікуй один duplicate timeline, один color-operation backup, local
   version `DaVinci Agent Tutorial Clean v1` на всіх 5 media items і незмінний
   M52 source.
6. Візуально порівняй source/target, потім повтори exact apply: receipt,
   timeline count і backup count не повинні змінитися.

Live structural/replay evidence: receipt `1f79bf7e…`, target
`939ab59d-2eb6-4ed3-9860-2e4bc65a9bf6`, 5/5 managed versions, source 5/5
`Version 1`, target 5/5 `DaVinci Agent Tutorial Clean v1`, timelines
`15 → 15`, backups `89 → 89`. Ручний visual acceptance підтвердив помірне
підсилення контрасту; M53 завершено.

Якщо documented color methods недоступні у Resolve 21 Free, збережи raw
environment evidence та залиш apply unsupported; не використовуй Console,
скриптові обходи або довільні LUT/DRX paths.


### Lua synchronized assembly

Use non-sensitive test media with matching source/timeline FPS. Assemble two
linked video tracks and one master audio track with explicit source ranges.
Verify native track, source, timeline bounds and linked counterparts; review
playback and lip sync. Separately test whole clips with nonzero camera delay,
chronological placement, and source-end/container padding. Check several
speech-bearing windows to detect drift, without claiming full-stream proof.
Preserve backups and uncertain receipts. Keep all source-specific cut maps,
paths, project archives and reports outside the repository.

### Circular PiP acceptance

On a separate short review timeline, keep screen V1, camera V2 and one master
audio track linked. Back up, apply `resolve_lua_circle_mask` only to the PiP item,
then frame it with equal ZoomX/ZoomY. Render and check equal pixel diameter in
both axes, transparent corners, no square background, face/hair motion within
the circle, and unobscured screen content. Full-camera items remain rectangular.
Verify an existing Fusion composition is refused unchanged. Inspect a failed
write before any retry. Corrected equal dimensions passed a short native render
after recovery from a timeline-settings hang. Unit tests alone do not certify
visual behavior.
To reconcile partial creation, read the unique timeline state first; only use
`resolve_lua_set_empty_timeline_fps` if all tracks have no clips. Never recreate
a timeline solely because a request timed out.

### Derived take acceptance

Use an imported processed clip with exactly the target item's frame count and
frame rate. Replace only that item via `resolve_lua_replace_video_take`; verify
the original take remains, the selected take points to the derived media, and
all item/link bounds remain identical. Read back untouched clips and the master
audio before and after. Render across the boundary to the following clip and
check sync, foreground edges, color and duration. Refuse mismatched lengths,
existing take selectors and existing Fusion compositions before mutation.

Live acceptance confirmed an exact-length derived take, retained original take,
unchanged linked master audio and neighboring composition, and a native render.
A shorter replacement was rejected before mutation. Private review media and
readback artifacts are deliberately excluded from the repository.

### Recording preparation API — 2026-10-29

Environment: Windows 10 build 19045, Resolve **Free 21.1.2.10** (edition
operator-confirmed), Python **3.12.10**, agent **0.1.0**, marker protocol **1** /
Lua protocol **4**, fresh `recording_api_v1` bundle. UTC run: approximately
2026-10-29 03:50–03:57. Native recording module SHA-256:
`d19d24028fc5e37a892bdcfaa9ba9a53aef9305fcc8c78245ee6d0475d057df2`.
Native bundle SHA-256:
`15dee3fa71eff3f9e56f44b7eb7537f381c3769ccb26b18d97d6d7416e0afabb`.
Private projects, DRP backups, runtime paths and complete device names remain
outside the repository.

| Operation | Live result | Limits |
| --- | --- | --- |
| MCP stdio tool discovery and fresh native project read | `verified` | 43 typed tools; correct saved project identity |
| Inspect existing mono/stereo tracks, empty clips and 48 kHz | `verified` | Real Lua arrays contain numeric `__flags` metadata; ignored explicitly without accepting unknown keys |
| Reuse an existing named mono track and exact-key replay | `verified` | Backup and native readback retained; no duplicate track |
| Create a real mono track in a separate empty test timeline | `verified` | Track count/name/mono subtype/zero clips verified, then replayed without another append |
| Seek and return to the original Fairlight timeline | `verified` | Exact 01:00:00:00 readback; original page restored |
| Set the same existing allowed project media root | `verified` | Documented settings API and exact native readback; not a recorded-file path/format test |
| Read Windows main transport state | `verified` | Stop on, Play/Record off; no microphone capture |
| Windows input-device selection and reopened verification | `pending` | Attempt refused before selection because Windows returned no foreground window. No device change or saved Preferences modification was accepted |
| Physical input patch, record arm, track/bus processing controls | `unsupported` | No verified setter in this delivery |

Native write calls required exact project identity, confirmation and stable
idempotency keys; each produced its local before/response exports. The original
voice timeline retained **zero audio/video items** after acceptance, and the
final main transport state was stopped with Record off. The separate empty test
timeline remains as a visible test artifact. No recording, normalization or
processing operation was invoked.

Windows UI tests currently establish the request boundary and fail-closed
behavior; mocked checks are not presented as live input-selection acceptance.
Unlock the interactive session, bring Resolve forward, then repeat the bounded
current-device/no-op check before marking that row verified. Keep the actual
input patch and signal/processing checks separate from MCP liveness.

### Scoped Fairlight UI controls — 2026-10-29, live pending

The recording API extension adds seven typed MCP tools for track/bus master EQ
and Dynamics, mono input patching and individual track arm. This entry supersedes
the earlier `unsupported` classification for those implemented controls; it
does **not** upgrade their live status to `verified`.

| Operation | Status | Offline evidence / live acceptance still required |
| --- | --- | --- |
| MCP tool discovery | Offline verified | 50 tools through the real stdio client with native API doubles; no live Resolve call in this extension |
| Track/bus master EQ | `pending` | Typed boolean, exact target fencing, physical checkbox action and reopened state; real DSP/persistence acceptance still required |
| Track/bus master Dynamics | `pending` | Local OCR and positive toggle patterns with synthetic raster fixtures; real rendered layout/state acceptance still required |
| Mono track input patch | `pending` | Strict list/table source channel and destination assignment models, single-row selection and reopened assignment; actual Resolve list accessibility still required |
| Track R arm | `pending` | Native name/index fence, scoped row geometry and positive R-state fixtures; actual row/action acceptance still required |
| Exact-key recovery | Offline verified | Completed replay; uncertain changes invoke getters only; mismatches retain pending receipts and original UI evidence |
| Other effects/inserts and audio quality | `unsupported` / not assessed | No insert bypass, audio capture or listening test |

Live Windows UI testing was explicitly deferred by the operator. No live UI
gesture, screenshot capture, microphone recording or audio-processing change was
performed for this extension. Target profile: English Resolve 21.1 on Windows;
unknown layout, missing local OCR, lost foreground, moving cursor, ambiguous
target or non-stopped transport must fail closed. `available=true` means the
host opted in on Windows, not that this profile has passed live acceptance.

Before changing any row above to `verified`, collect the canonical sanitized
evidence: exact OS/Python/Resolve edition/build and commit, project/track identity
fences, before/after master/patch/R state, reopened readback, saved-project
persistence, no-op and uncertain recovery, plus final Stop on / Play and Record
off. Exercise mixed-DPI placement and refuse a wrong/ambiguous target before
mutation. Keep private screen media, device names and project backups local.
