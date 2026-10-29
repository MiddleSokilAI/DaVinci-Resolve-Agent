# Налаштування MCP-клієнта

## Фактична доступність workflow

Перед вибором workflow викликайте `video_agent_capabilities`. Інструмент є в
Python, static Free-marker та managed Free-marker endpoints і використовує
спільну backend-specific карту workflow. Він не переносить Python receipts у
Lua та не запускає монтаж.

- `verify_connection=false` за замовчуванням читає лише локальні дані. Наявність
  tool у каталозі, heartbeat або watcher `connected` не підтверджують native API.
- `verify_connection=true` додає один свіжий native read поточного проєкту;
  не відкриває проєкти, не запускає Windows UI та не повторює mutation.
- `available` означає готовність викликати workflow з його звичайними input,
  confirmation, project identity, backup та live preflight guards. Це не доказ
  сумісності кожного API чи успішного монтажу конкретних матеріалів.
- `partial` повертає `alternative_tools`, але `supported=false` та
  `available=false`: наприклад, Lua ripple-cut не є реалізацією Python
  `apply_synchronized_pause_compaction` і не приймає його receipts.
- `unverified` вимагає свіжого native read або окремої live acceptance;
  `blocked` повідомляє про disconnect, відсутні media roots, зайнятий runtime,
  pending writes чи невирішений project-open; `unsupported` означає відсутність
  потрібного adapter/tool або несумісну session capability.

Fairlight EQ/Dynamics залишаються `unverified`, доки adapter повідомляє
`live_verification=pending`, навіть за наявності Windows opt-in. Запуск запису
та effect inserts не заявляються як підтримані. Технічний ranking дублів не
доводить редакторську якість, а gain/limiter не є denoise чи de-reverb.

Managed endpoint повертає capability report навіть без active session; він
не відправляє запит у catalogue runtime та не запускає bootstrap. За наявності
active session увесь report прив'язаний до однієї generation. Pending host
project-open блокує заявлену доступність mutations так само, як їх реальний
dispatch. Результат не кешується як гарантія майбутніх writes.

Встановлений локальний агент має відповідати актуальному
`Seiger/DaVinci-Resolve-Agent:main`. Робочі гілки та PR перевіряються в ізольованих
test environments і не встановлюються в робочий агент до merge. Перед оновленням
звіряйте remote SHA, дерево package-файлів і installation provenance; повторно
перевіряйте remote після збірки. Не перезапускайте Resolve або активну marker
session для звичайного оновлення discovery-коду.

## Створення проєкту

`resolve_create_project(name, confirm_create=true)` створює, вибирає та
зберігає новий проєкт у поточній папці поточної бібліотеки Resolve. Назва
повинна бути унікальною без урахування регістру; існуючий проєкт з такою
назвою не відкривається і не перезаписується. Поточний проєкт, якщо він є,
спочатку зберігається та експортується у `.drp`; під час render створення
блокується. За відсутності поточного проєкту `backup_path=null`.

Tool потребує вже запущеного ResolveBridge. Запуск програми та доступність
меню Scripts із Project Manager не гарантуються MCP. Live compatibility
нової операції залишається pending до окремого тесту у встановленій версії.
Успішний replay з тим самим `idempotency_key` повертає receipt без нового
проєкту, backup або перемикання поточного проєкту. Після часткового збою
створений проєкт не видаляється; конфлікт назви блокує повторне створення.

## M54 take-selection tools

`analyze_take_candidates` приймає 2–8 `{candidate_id, path}` об'єктів або 2–8
сегментів `{candidate_id, path, start_seconds, end_seconds}`. Режими не можна
змішувати; один файл можна повторити з різними діапазонами до 300 секунд. Tool
перевіряє paths через локальний allowlist і створює `pending_review` technical
report.
`analyze_scripted_take_candidates` додатково вимагає `reference_text` і лише
bounded segment candidates. Він локально транскрибує тільки задані ranges,
ранжує за fixed weights `65% reference match + 35% technical score` та не
повертає raw reference/transcript text. Обидва analyze tools лишають результат
у `pending_review` і не викликають ResolveBridge.
`get_take_selection` та `list_take_selections` read-only. Окремий
`review_take_selection` записує immutable `approve|reject`, але завжди повертає
`timeline_modified=false`. Довільні weights, decoder flags і команди відсутні.
`get_take_selection_review` повертає canonical review. M54.4 tools
`compose_take_sequence`, `get_take_sequence` і `list_take_sequences` працюють
лише зі схваленими selections. Sequence зберігає порядок, hashes, fingerprints,
ranges і scores, але не paths; `apply_supported=false`, ResolveBridge не
викликається.

## M55 source binding tools

`bind_take_sequence_sources` приймає approved sequence ID і exact список
`{order, path}`. Paths проходять allowlist та звіряються за filename, size і
fingerprint. MCP response завжди має `paths_redacted=true`,
`timeline_modified=false`, `apply_supported=false`.
`get_take_sequence_binding` повертає лише redacted receipt; private local paths
ніколи не виходять через MCP.
`preview_take_sequence_assembly` приймає `binding_id` та bounded
`assembly_name`. Tool повторно перевіряє private sources, читає лише локальні
video metadata та повертає deterministic sequential plan без absolute paths.
Він не імпортує media, не визначає target timeline frames і не викликає
ResolveBridge; `timeline_modified=false`, `apply_supported=false`.
`preview_take_sequence_timeline_mapping` додатково приймає exact live
`timeline_id`. Він викликає наявний documented editing-metadata readback із
`asset_ids=[]`, звіряє timeline identity/FPS/resolution і повертає source-frame
bounds та безперервні target positions. Tool read-only і не створює timeline,
backup, Media Pool item або clip.
`preview_take_sequence_media_import` read-only звіряє unique sources із live
Media Pool і блокує existing-name або source-name collisions.
`apply_take_sequence_media_import` є окремим write-tool без path arguments. Він
вимагає exact preview plan та `confirm_import=true`, повторно перевіряє binding,
mapping і Media Pool, виконує один backup-backed import batch та повертає
path-redacted receipt. `get_take_sequence_media_import` read-only повертає цей
receipt; exact replay не повторює write. Обидва tools не змінюють timeline.
`preview_take_sequence_timeline_apply` приймає лише applied M55.5 receipt і
нову target name. Він блокує non-empty source, existing target name або
непідтверджені capabilities та повертає code-owned V1/A1 placements без paths.
`apply_take_sequence_timeline` додатково вимагає exact plan і
`confirm_apply=true`; caller не передає asset IDs, clipInfo або tracks. Tool
дублює source, вставляє bounded video/audio batches і перевіряє target та
незмінність source. `get_take_sequence_timeline_apply` read-only перевіряє
applied receipt проти актуального Resolve readback.
`inspect_take_sequence_qc` read-only формує M55.7 structural report тільки
після fresh M55.6 readback. `get_take_sequence_qc` повторно перевіряє live
evidence. `review_take_sequence_qc` записує immutable human `approve|reject`,
але має `timeline_modified=false`; `get_take_sequence_qc_review` повертає цей
record. Звіт не заявляє автоматичну перевірку audio processing, subtitle
alignment, color або visual/editorial quality.
`preview_take_sequence_render` вимагає approved M55.7 report, safe output stem
і один із `youtube-1080p-h264-v1|youtube-2160p-h264-v1`. Він read-only і не
повертає output root. `start_take_sequence_render` додатково вимагає exact
`expected_plan_id` та `confirm_render=true`; format/codec/preset/path не є
аргументами MCP. `get_take_sequence_render_status` повторно перевіряє QC
evidence, live job binding і повертає completion та managed MP4 validation.

## M53 color discovery tools

`resolve_get_color_environment` є read-only probe для media video items: він
повертає current local version, node count/labels, LUT readback і presence
документованих color methods. `list_color_presets` та `get_color_preset`
працюють лише з пакетованим каталогом. `preview_color_treatment` прив'язує
allowlisted preset до exact applied M52 receipt і live snapshot. На цьому
підетапі жоден із цих tools не створює version, node, backup або grade.

`apply_color_treatment` є окремим write tool: він вимагає exact `plan_id` та
`confirm_apply=true`, створює duplicate timeline і застосовує тільки packaged
`tutorial-clean-v1`. Caller не передає CDL values, node index, version name,
LUT/DRX path або Resolve expression.

## Межі MCP

MCP-сервер працює локально через `stdio` і не відкриває мережевий порт.
Read-only інструменти:

- `video_agent_status`;
- `resolve_get_project`;
- `resolve_list_timelines`;
- `resolve_get_timeline`;
- `resolve_list_timeline_items`;
- `resolve_list_media_pool_items`;
- `resolve_get_subtitle_environment`;
- `resolve_get_workspace_snapshot`;
- `resolve_get_render_options`;
- `resolve_get_render_job_status`;
- `resolve_verify_render_output`.
- `list_editing_recipes`.
- `get_editing_recipe`.
- `preview_editing_recipe`.
- `preview_visual_treatment`.
- `preview_baseline_edit`.
- `get_baseline_render_status`.
- `preview_broll_plan`.
- `preview_take_sequence_render`.
- `get_take_sequence_render_status`.

Write-інструменти M4:

- `resolve_import_media`;
- `resolve_create_timeline`;
- `resolve_ensure_timeline_tracks`;
- `resolve_duplicate_timeline`;
- `resolve_set_current_timeline`;
- `resolve_append_clip`;
- `resolve_insert_clip`;
- `resolve_set_clip_enabled`;
- `resolve_set_clips_linked`;
- `resolve_set_clip_transform`;
- `resolve_insert_title`;
- `resolve_delete_clip`;
- `resolve_add_marker`.
- `resolve_create_subtitles_from_audio`.
- `generate_subtitles`.
- `resolve_prepare_render_job`.
- `resolve_start_render_job`.
- `run_editing_recipe`.
- `apply_visual_treatment`.
- `start_baseline_render`.
- `apply_broll_plan`.
- `start_take_sequence_render`.

Локальні rough-cut інструменти:

- `create_rough_cut`.
- `approve_rough_cut`.
- `get_rough_cut_plan`.
- `list_rough_cut_plans`.
- `sync_screen_and_webcam`.
- `compose_webcam_picture_in_picture`.
- `link_synchronized_screen_pair`.
- `preview_synchronized_pause_compaction`.
- `apply_synchronized_pause_compaction`.
- `finalize_synchronized_pause_compaction`.
- `prepare_finalized_timeline_render`.
- `start_finalized_timeline_render`.
- `get_finalized_timeline_render_status`.
- `preview_rough_cut_apply`.
- `apply_rough_cut`.

Локальний audio-інструмент M6:

- `clean_dialogue_audio`.
- `get_audio_report`.
- `list_audio_reports`.
- `prepare_finalized_timeline_audio`.
- `start_finalized_timeline_audio`.
- `get_finalized_timeline_audio_status`.

MCP-адаптер звертається до application service. Він не працює з transport
runtime безпосередньо та не приймає довільних назв команд, Python, Lua,
PowerShell або shell-коду.

`create_rough_cut` валідовує локальні файли, аналізує окремі PCM WAV-доріжки
та зберігає `pending_review` план. Він не ставить команду bridge, не відкриває
проєкт Resolve й не застосовує запропоновані операції. Формат і обмеження
описано в [rough-cut.md](rough-cut.md).

`approve_rough_cut` локально фіксує явне `confirm_review=true` для одного
canonical `plan_id`. Окремий approval record прив’язаний до SHA-256 draft і
має `apply_supported=false`. Tool не викликає Resolve bridge та не застосовує
запропоновані операції.

`list_rough_cut_plans` повертає до 100 summaries без source media paths.
`get_rough_cut_plan` повторно валідовує один draft, optional approval і
SHA-256-зв’язок між ними. Обидва tools read-only, працюють без bridge та
повертають `apply_supported=false`.

`clean_dialogue_audio` приймає allowlisted 16-bit PCM WAV, створює derived WAV
і before/after report. Інструмент не викликає Resolve bridge та не приймає
довільних filter graph, команд або коду. Докладніше:
[audio-workflow.md](audio-workflow.md).

`list_audio_reports` повертає до 100 summaries без source/derived paths.
`get_audio_report` приймає canonical `report_id` і повторно валідовує
збережений report. Обидва tools read-only, не викликають bridge та не
запускають повторну обробку аудіо.

M46 audio extraction tools приймають лише applied M43 receipt і безпечний
filename stem. Профіль завжди `audio-only-pcm-wav-v1`; prepare та start мають
окремі confirmation flags, а status tool лише читає job і перевіряє managed
16-bit/48 kHz PCM WAV. Довільні Resolve render settings не експонуються.

У default MCP composition усі сім локальних rough-cut/audio tools проходять
через workflow audit. Records не містять tool arguments, plan/report IDs,
paths, results або exception messages. Докладніше:
[workflow-audit.md](workflow-audit.md).

## Codex як перший підтримуваний клієнт

Репозиторій містить project-scoped `.codex/config.toml`. Він запускає MCP
через executable у локальній `.venv`, не містить імені користувача, літери
диска або абсолютного шляху. Codex завантажує цю конфігурацію лише для
trusted project.

1. Виконай `installer\install.ps1`.
2. Відкрий корінь репозиторію як trusted project у Codex.
3. Перезапусти Codex після першого встановлення або зміни MCP-конфігурації.
4. Перевір у списку MCP servers наявність `davinci-resolve-agent`.

Для read-only tools підтвердження не потрібне. Project config використовує
approval mode `writes`, тому Codex запитує підтвердження для tools, які MCP
сервер не позначив read-only. Tool timeout становить 330 секунд: цього
достатньо для максимального bridge timeout у 300 секунд і завершення STDIO
відповіді.

### Наскрізний acceptance-тест у Codex

1. Попроси Codex викликати `video_agent_status`. Ця перевірка не ставить
   команду bridge.
2. Відкрий потрібний проєкт у Resolve.
3. Попроси Codex викликати `resolve_get_project` з `timeout_seconds=120`.
4. Поки tool очікує, один раз запусти
   `Workspace → Scripts → Edit → ResolveBridge`.
5. Звір назву проєкту у відповіді Codex з реально відкритим проєктом.

Успішний результат доводить весь ланцюжок
`Codex → MCP → AgentApplication → filesystem transport → ResolveBridge → Resolve`.
Він не доводить фоновий або автоматичний запуск bridge.

## Інші MCP-клієнти

Спочатку виконай `installer\install.ps1`. Якщо клієнт не підтримує
project-scoped Codex config, вкажи в його локальній конфігурації абсолютний
шлях до створеного executable. Не записуй власний шлях у файли репозиторію:

```json
{
  "mcpServers": {
    "davinci-resolve-agent": {
      "command": "<repository-path>\\.venv\\Scripts\\davinci-agent-mcp.exe",
      "args": []
    }
  }
}
```

Заміни `<repository-path>` на локальний шлях до checkout на конкретному
комп'ютері та перезапусти MCP-клієнт. Цей JSON є узагальненим прикладом;
точний файл і формат залежать від конкретного клієнта.

## Виконання Resolve-запитів

`video_agent_status` повертає кешований стан bridge одразу.
`create_rough_cut`, `approve_rough_cut`, `get_rough_cut_plan`,
`list_rough_cut_plans`, `clean_dialogue_audio`, `get_audio_report` і
`list_audio_reports` також працюють без bridge.
Resolve-інструменти ставлять команду в локальну чергу. Поки клієнт очікує
відповідь:

1. відкрий потрібний проєкт у DaVinci Resolve;
2. запусти `Workspace → Scripts → Edit → ResolveBridge`;
3. дочекайся відповіді MCP-клієнта.

Після ручного старту `resolve_stop_bridge` завершує persistent bridge cleanly.
Він не приймає параметрів і не змінює Resolve project, але свідомо зупиняє
локальний service, тому має write annotation. Після відповіді `stopping`
`video_agent_status` зрештою показує `stopped`, що не є healthy state.

Типовий timeout становить 30 секунд. Аргумент `timeout_seconds` приймає значення
понад `0` і не більше `300`. Persistent lifecycle починається лише після
ручного запуску bridge. Responsive UI, кілька послідовних команд і clean stop
перевірено у Resolve 21 Free 21.0.3.7.

`resolve_get_workspace_snapshot` збирає основну діагностику одним запитом:
bridge metadata, поточний проєкт, timelines, current timeline та його items,
Media Pool і render discovery. Tool не приймає масив команд, не створює backup
і не змінює lifecycle bridge.

`resolve_get_render_options` є discovery-only інструментом M7. Він читає
документовані Resolve formats, codecs, presets, поточні значення та render
queue. Він не конфігурує й не запускає render.

`resolve_prepare_render_job` приймає лише безпечний filename stem у
`custom_name` та необов'язковий `profile`. Він створює `.drp` backup,
застосовує фіксований `youtube-1080p-h264-v1` або
`youtube-2160p-h264-v1`, додає MP4/H264 job у queue та повертає
`started=false`. Default — 1080p. Output directory, raw dimensions, codec і
Resolve preset не задаються через MCP.

`resolve_get_render_job_status` читає документований status одного `job_id`.
`resolve_start_render_job` стартує лише job, який був створений
`resolve_prepare_render_job`, досі відповідає фіксованому 1080p MP4/H264
контракту й не має попереднього start-record. Tool не приймає output path,
codec, preset, список job або upload target. Перед стартом створюється backup.

`resolve_verify_render_output` повторно читає status і зіставляє
`TargetDir`/`OutputFilename` із керованою директорією. Успішна перевірка
вимагає `CompletionPercentage=100`, наявного MP4 і ненульового розміру.
Інструмент не декодує контейнер і не перевіряє зображення, звук або тривалість.

`resolve_insert_clip` вставляє один bounded source range на вказаний
`video|audio` track. `position_frames` є offset від початку timeline, а не
абсолютним Resolve frame. Tool не приймає довільний `clipInfo`, ripple,
delete, move, transform або код. Перед вставкою створюється `.drp` backup.

`resolve_set_clip_enabled` змінює лише enabled-state одного TimelineItem.
Аргументи: `timeline_id`, `timeline_item_id`, `enabled`. Bridge сканує
video/audio tracks через документований `GetItemListInTrack`, відхиляє
locked track, створює backup і перевіряє результат через `GetClipEnabled`.
Tool не приймає track index, довільну властивість або Resolve expression.

`resolve_set_current_timeline` приймає один `timeline_id`, створює backup,
вибирає вже наявний timeline через документований `SetCurrentTimeline` і
перевіряє результат через `GetCurrentTimeline`. Відповідь містить попередній
та новий timeline. Tool не приймає назву або index і не створює timeline.
`resolve_list_timelines` і `resolve_get_timeline` повертають потрібні
канонічні IDs разом із назвами.

`resolve_set_clip_transform` змінює лише дозволені transform-поля одного
video TimelineItem: `position_x`, `position_y`, рівномірний `zoom`,
`rotation_degrees` та `opacity_percent`. Щонайменше одне поле обов'язкове.
Bridge відхиляє locked track, значення поза фіксованими межами та позицію поза
динамічною межею ±4 розміри поточного timeline. Мапінг на `Pan`, `Tilt`,
`ZoomGang`, `ZoomX`, `ZoomY`, `RotationAngle` і `Opacity` залишається всередині
Resolve provider; raw property names, expressions і keyframes через MCP
не приймаються. Opacity write/readback, replay без другого backup і відновлення
початкового значення перевірено у Resolve 21 Free 21.0.3.7.

`resolve_delete_clip` є єдиним destructive MCP tool. Він видаляє рівно один
video/audio TimelineItem без ripple і лише коли `confirm_delete=true`.
Application, transport schema та bridge незалежно перевіряють підтвердження,
destructive safety flag і обов'язковий backup. Масив IDs, linked-item expansion
та ripple-параметр зовнішньому клієнту не доступні. Видалення окремого
disposable item, replay без другого backup і незмінені первинні video/audio
counts перевірено у Resolve 21 Free 21.0.3.7.

`resolve_list_timeline_items` приймає один `timeline_id` і read-only перелічує
video/audio tracks через документовані `GetTrackCount` та
`GetItemListInTrack`. Для кожного item повертаються ID, назва, track,
timeline/source frame bounds і duration. Subtitle items, raw Resolve
properties, Fusion compositions та object handles не повертаються.
Live discovery у Resolve 21 Free 21.0.3.7 повернув один V1 та один A1 item із
duration 96, timeline bounds 86400..86496 і source bounds 0..240 без створення
project backup.

`resolve_list_media_pool_items` не приймає аргументів і рекурсивно обходить
поточний Media Pool через документовані Folder APIs. Response містить лише
канонічний asset ID, назву, folder ID і логічний folder path. Файлові шляхи,
raw `GetClipProperty` snapshots, metadata та Resolve object handles не
повертаються. `GetClipList()` може включати timeline entries, тому response
називає результати Media Pool items, а не гарантує source-media kind. Обхід
обмежено 1000 folders і 10000 items.
Live discovery у Resolve 21 Free 21.0.3.7 повернув п'ять items у `Master`,
зокрема source MKV із відомим `asset_id`, і не створив project backup.

`resolve_get_subtitle_environment` приймає один canonical `timeline_id` і
повертає bounded subtitle tracks/items через документовані
`GetTrackCount("subtitle")`, `GetTrackName` та `GetItemListInTrack`. Окремий
блок `auto_caption` лише перевіряє присутність документованого
`CreateSubtitlesFromAudio` і потрібних constants. Tool не запускає AI,
не створює backup і не змінює timeline. `verified=false` не дозволяє трактувати
наявність методу як підтверджену підтримку Resolve 21 Free.

`resolve_create_subtitles_from_audio` вимагає canonical `timeline_id` і
`confirm_create=true`. Він створює `.drp` backup, застосовує тільки fixed
`AUTO`/default/42 characters/single-line/zero-gap policy та вважає операцію
успішною лише після появи нових subtitle items у bounded readback. Модель,
prompt, raw settings і довільний код не приймаються.

`generate_subtitles` є робочим M47 fallback для Resolve Free. Tool приймає
дозволений локальний `source_file`, canonical `timeline_id` і
`confirm_apply=true`; модель та inference policy не задаються caller-ом. Він
локально створює SRT, виконує backup-backed import/append і вимагає exact count
та canonical IDs у незалежному subtitle readback. Placement перевіряється
відносно `append_frame` — кінця таймлайна перед документованим
`AppendToTimeline`; довільне вставлення в середину timeline не заявляється.
Replay applied receipt не повторює transcription, import або append.

M48 tools `list_editing_recipes`, `get_editing_recipe` та
`preview_editing_recipe` є read-only. Preview нормалізує defaults і повертає
точні steps та missing live capabilities. `run_editing_recipe` вимагає
`confirm_execute=true` й виконує лише packaged allowlisted actions із fixed
argument mapping. Поточний `tutorial-layout-v1` компонує M39 PIP та M40 screen
link для одного canonical M38 receipt. Recipe не може передати shell, Python,
Lua, PowerShell, Resolve expression, MCP tool name або довільний action.

`resolve_get_editing_metadata` є read-only підготовкою до точного placement.
Він приймає canonical `timeline_id` і від 0 до 100 явних `asset_ids`, читає
лише bounded `MediaPoolItem.GetClipProperty("Frames")` та
`GetClipProperty("FPS")`, а також `Timeline.GetTrackCount("video"|"audio")`
і named settings `timelineFrameRate`, `timelineResolutionWidth`,
`timelineResolutionHeight`. Відповідь повертає нормалізовані FPS і resolution
цільового timeline, кількість його
tracks, а для assets — `duration_frames: int` і `frame_rate: float`. Raw
timeline settings і clip-property snapshots,
файлові шляхи, Resolve handles і будь-які write-операції не входять до
контракту; backup не створюється. Live readback у Resolve 21 Free 21.0.3.7
підтвердив обидва синхронні MKV assets як 60 FPS із різними frame counts.
Порожній `asset_ids=[]` повертає лише timeline metadata й не обходить Media
Pool; цей режим використовується M55.3 до майбутнього safe import.

`preview_take_sequence_media_import` повторно обчислює M55.3 mapping і читає
bounded Media Pool identities. Повторні sequence ranges одного fingerprint
стають одним import source. Якщо в Media Pool немає same-name item, action —
`import`; якщо є — `review_name_collision`, бо name без path/fingerprint не
доводить identity. Tool не приймає asset override, не імпортує media і завжди
має `apply_supported=false`.

`resolve_set_clips_linked` приймає один timeline ID, від 2 до 16 унікальних
TimelineItem IDs та `linked=true|false`. Інструмент працює лише з явно
адресованими video/audio items, відхиляє locked tracks, створює backup і
перевіряє link-state кожного item. Replay з тим самим `idempotency_key` не
створює повторного backup.

`resolve_duplicate_timeline` приймає `timeline_id` джерела та нову унікальну
назву. Tool створює `.drp` backup, викликає документований
`DuplicateTimeline`, перевіряє окремий canonical ID та project membership.
Поточний timeline не перемикається; replay із тим самим `idempotency_key` не
створює додаткової копії.

`resolve_ensure_timeline_tracks` приймає `timeline_id` та цільові
`video_track_count`/`audio_track_count` у межах 1–8. Він створює backup, додає
лише відсутні tracks через `Timeline.AddTrack` і перевіряє кожен increment
через `GetTrackCount`. Нові audio tracks мають фіксований subtype `stereo`;
наявні tracks не видаляються й не перетворюються. Replay з тим самим
`idempotency_key` повертає receipt без повторної зміни.

## Безпечне редагування

Усі write-tools створюють `.drp` backup до зміни та підтримують необов'язковий
`idempotency_key`. Для `resolve_import_media` кожен шлях має бути абсолютним,
існувати й належати до `media.allowed_roots` у локальному `config.toml`.

MCP annotations позначають `resolve_delete_clip` як destructive, а решту
write-tools — як non-destructive. Автоматичного відновлення проєкту немає;
стратегія ручного відновлення описана в [rollback.md](rollback.md).

`sync_screen_and_webcam` є non-destructive write workflow над наявними MCP
primitives. Він вимагає `confirm_sync=true`, двох різних canonical asset IDs,
нової назви timeline та offset від -30000 до 30000 мс. Позитивний offset
розміщує webcam пізніше, негативний — screen пізніше. Мілісекунди округлюються
до найближчого target timeline frame; source bounds залишаються у frames
відповідного asset. `timeout_seconds` застосовується до кожного primitive.

`compose_webcam_picture_in_picture` працює лише з canonical applied receipt
від `sync_screen_and_webcam`. Він приймає bounded `size_percent` 10–50,
`center_x_percent`/`center_y_percent` 0–100 і `confirm_layout=true`. Координати
нормалізовано від лівого верхнього кута кадру; application service переводить
їх у документовані Resolve Pan/Tilt за live resolution timeline. Workflow
змінює лише webcam V2, створює один backup-backed transform і перевіряє exact
Pan/Tilt/Zoom readback. Raw property names, crop, masks, Fusion, keyframes і
довільний код не приймаються.

`link_synchronized_screen_pair` приймає тільки applied SHA-256 receipt від
`sync_screen_and_webcam` та `confirm_link=true`. Application service дістає з
receipt canonical IDs screen V1/A1 і не дозволяє caller передати довільні
TimelineItem IDs. Workflow вимагає verified `clip.link`, створює один
backup-backed link, перевіряє взаємні IDs через `GetLinkedItems()` і зберігає
durable receipt. Webcam item та інші clips не входять до операції.

`preview_synchronized_pause_compaction` є read-only підготовкою до apply. Він
приймає approved rough-cut plan, applied M38 receipt і bounded нову назву
timeline. Tool звіряє approval SHA-256, sync offset, source filenames і live
metadata, перетворює ordered non-overlapping cuts у kept intervals та повертає
майбутні V1/A1/V2 placement аргументи. Час plan залишається в ms, source
bounds переводяться за FPS кожного asset, а record positions — за FPS target
timeline. Жоден timeline, clip або backup не створюється.

`apply_synchronized_pause_compaction` вимагає `confirm_apply=true` і не приймає
довільних команд або `clipInfo`. Tool повторно обчислює M41 preview, створює
новий timeline, готує V1/A1/V2 та виконує один bounded batch insert для всіх
kept ranges. Workflow зберігає durable progress receipt, а фінальний readback
має знайти кожен canonical TimelineItem ID з тими самими track/source/timeline
bounds. Source M38 timeline не змінюється.

`finalize_synchronized_pause_compaction` вимагає три applied SHA-256 receipts:
M42 compaction, M39 PIP та M40 link, а також `confirm_finalize=true`. Caller не
передає TimelineItem IDs або raw properties: workflow виводить їх із receipts,
робить два bounded batch writes і звіряє mutual links, exact transform та
canonical item identity. Replay завершеного receipt не виконує нових writes.

`prepare_finalized_timeline_render` приймає applied M43 receipt, `custom_name`,
allowlisted `profile` і `confirm_prepare=true`. Tool сам дістає timeline ID з
receipt, створює один backed-up MP4/H.264 job і перевіряє timeline/profile/
resolution readback. Він завжди повертає `started=false` і не запускає render.

`start_finalized_timeline_render` вимагає applied M44 receipt і
`confirm_render=true`. Tool перевіряє точну live queue identity, `Ready` state,
capabilities та відсутність існуючого output перед одним guarded start.
`get_finalized_timeline_render_status` не має write-шляху: він повертає live
progress і managed MP4 validation для M45 receipt.

`apply_finalized_timeline_audio` вимагає canonical M46 extraction receipt,
canonical `pcm-dialogue-limit-v2` report і `confirm_apply=true`. Caller не
передає timeline, track, asset або item IDs: workflow виводить їх із receipts,
звіряє extraction range з WAV duration, вставляє один processed item на A2,
вимикає лише source A1 та підтверджує linked source video як enabled. Durable
receipt робить replay без повторних writes.

`resolve_insert_title` вставляє лише встановлений standard title за exact
timecode після `confirm_insert=true` і backup. Tool не приймає текст, шрифт,
Fusion controls або довільні властивості. Placement є append-only: integer-FPS
timecode має бути не раніше поточного timeline end, а existing video item bounds
після insertion повинні лишитися незмінними.

`preview_visual_treatment` є read-only: він нормалізує bounded static transforms
і title insertions та повертає `clip.transform`/`title.insert` gates.
`apply_visual_treatment` вимагає ідентичний plan і `confirm_apply=true`, виконує
один transform batch та окремі confirmed title inserts із durable replay.
`SmartReframe`, keyframes, tracking і animated titles не входять до M49.

`preview_broll_plan` приймає completed M50 receipt, нову назву timeline та
явні video-only placements із `asset_id`, source bounds, relative
`position_frames`, V3–V8 і `purpose`. Він читає bounded metadata, перевіряє
FPS-aware duration, timeline bounds, overlap/collision та повертає deterministic
`plan_id` без write-команд.

`apply_broll_plan` вимагає ті самі inputs, exact `expected_plan_id` і
`confirm_apply=true`. Tool дублює baseline timeline, забезпечує video tracks,
виконує один bounded batch insert і звіряє canonical item readback. Source M50
timeline та аудіо не змінюються; replay applied receipt не виконує нових writes.

`list_animation_templates` і `get_animation_template` повертають лише
пакетовані M52 manifests. `preview_animation_template` прив'язує allowlisted
template та exact timecode до applied M51 receipt і live source snapshot.
`apply_animation_template` вимагає exact plan і confirmation, дублює M51
timeline та викликає fixed `resolve_insert_animation_template`. Caller не може
передати Fusion code, expressions, nodes, controls, `.setting` або текст.

Raw environment tool звіряє documented method presence та встановлений asset
hash. Raw insert працює лише на exact timeline end, перевіряє selected
timecode, створює backup, відновлює playhead і вимагає
рівно один новий item із Fusion composition та незмінні existing video items.
