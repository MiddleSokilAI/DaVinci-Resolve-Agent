# DaVinci Resolve Agent: докладна довідка

Ця сторінка зберігає повний опис можливостей, попередніх етапів і технічних
маршрутів із колишнього README. Для першого підключення користуйся
[основною інструкцією](../README.md); актуальні межі сумісності наведено в
[матриці інтеграції](manual-integration-testing.md).

DaVinci Resolve Agent — це розширюваний локальний фреймворк автоматизації
відеоредакторів. Перший провайдер працює з DaVinci Resolve 21 Free у Windows,
але ядро не залежить від конкретного редактора.

**Resolve Free:** реалізовано opt-in backend
[`free-marker`](free-mcp-backend.md) для основного MCP entrypoint. Він
передає типізовані команди self-contained Lua-скрипту з меню Scripts і не
потребує `dofile`, `loadfile` чи виконання переданого коду. Offline-тести
Python/Lua 5.1 з native API doubles проходять. У Windows Free **21.1.2.10**
наживо перевірено MCP reads, guarded write з backup/readback та reconnect із
idempotent replay. Точний обсяг перевірки й решта acceptance cases наведені в
[manual matrix](manual-integration-testing.md#free-marker-mcp-backend--2026-10-29).

Історичний [експериментальний Lua MCP-міст](lua-experimental.md) у Windows
Free **21.1.0.17** підтвердив імпорт, монтаж, гучність, масштаб, SRT і рендер MP4.
Ці результати не переносяться автоматично на новий backend або іншу версію
Resolve. Основний Python backend залишається типовим маршрутом без змін.

## Підключення MCP та межі підтримки

| Маршрут | Сервер | Умова роботи |
|---|---|---|
| Основний Python | `davinci-agent-mcp` або `--backend python` | Запущений Python `ResolveBridge`, filesystem queue і його capabilities |
| Free marker, opt-in | `davinci-agent-mcp --backend free-marker --runtime ABSOLUTE_PATH` | Новий marker runtime та одноразовий запуск його self-contained скрипту через меню Scripts; обмежені live read/write/reconnect cases перевірено |
| Експериментальний Lua | `python -m mcp_server.lua_experimental --runtime ABSOLUTE_PATH` | Сумісне середовище з уже запущеним файловим resident Lua-мостом; окремий набір tools |
| One-shot console | [Compatibility PR #1](https://github.com/Seiger/DaVinci-Resolve-Agent/pull/1) | Одна вручну передана команда; окремий compatibility маршрут без автономного MCP-з'єднання |

У консолі й меню Scripts Windows Free **21.1.2.10** спостерігали `dofile=nil`
і `loadfile=nil`; у menu probe також `SavePrefs=nil`, а штатний `ExportProject`
працював. Тому старий файловий bootstrap не є робочим рішенням для цієї
інсталяції. Новий marker backend вбудовує фіксовані модулі в один menu script,
читає лише bounded JSON-дані через `bmd.fileexists` і повертає correlated
нативні `.drp` responses. Підтвердження зв'язку вимагає свіжої відповіді Resolve.

Підготуй **новий, ще не створений** runtime та встанови його menu script:

```powershell
Set-Location 'C:/path/DaVinci-Resolve-Agent'
& 'C:/path/DaVinci-Resolve-Agent/.venv/Scripts/python.exe' `
    -m mcp_server.free_marker `
    --runtime 'C:/path/runtime/marker-session-001' `
    --prepare --install-script
```

З відкритим збереженим проєктом запусти **Workspace > Scripts > Utility > Resolve
Agent Marker** один раз; у деяких меню `Utility` опущено. Підтримується саме
запуск із меню. MCP не запускає Resolve або скрипт автоматично; Fusion
`Execute` і console bootstrap для цього backend не використовуються.

Сервер запускає реальний MCP-клієнт за конфігом нижче. Еквівалентна команда для
stdio-клієнта:

```powershell
& 'C:/path/DaVinci-Resolve-Agent/.venv/Scripts/davinci-agent-mcp.exe' `
    --backend free-marker --runtime 'C:/path/runtime/marker-session-001'
```

Lease за замовчуванням становить **600 секунд** і поновлюється, поки MCP працює;
для явного підтвердженого stop є `resolve_lua_stop`. Після виходу native loop
runtime вважається consumed: підготуй нову папку, встанови новий menu script і
зміни runtime в конфігу. Повторний запуск старого runtime заборонено. Після
таймауту write використай лише той самий tool, exact arguments та idempotency
key для отримання початкового ACK без повторної публікації команди.
`resolve_free_pending_requests` показує локальні pending receipts і lifecycle
exports. Порядок reconnect/recovery описано в
[Free backend](free-mcp-backend.md).

Backend використовує наявні типізовані `resolve_lua_*` tools; він не замінює
весь набір Python MCP. JSON envelope обмежено **4096 UTF-8 байтами**. Нативні
project exports мають витрати часу й диска; довільний Lua endpoint, передача
коду та `reload_modules` не надаються.

### Codex: user scope або конкретний проєкт

Репозиторний `.codex/config.toml` діє у довіреному проєкті; він не реєструє
сервер автоматично для іншої робочої папки чи checkout.
За [офіційною документацією Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
для user scope використовуй `~/.codex/config.toml`; для project scope —
`.codex/config.toml` потрібного проєкту. Заміни абсолютні paths у прикладі на
власні. `command`, `cwd` та `--runtime` повинні відповідати підготовленій
інсталяції. Не запускай другий stdio server для того самого runtime паралельно.

```toml
[mcp_servers.davinci-resolve-free]
command = 'C:/path/DaVinci-Resolve-Agent/.venv/Scripts/davinci-agent-mcp.exe'
args = ["--backend", "free-marker", "--runtime", "C:/path/runtime/marker-session-001"]
cwd = 'C:/path/DaVinci-Resolve-Agent'
startup_timeout_sec = 20
tool_timeout_sec = 150
default_tools_approval_mode = "writes"
required = false
```

Для основного Python backend використовуй той самий absolute executable і
`cwd` без marker arguments; для сумісного resident Lua-маршруту є
[окрема документація](lua-experimental.md). Сам конфіг не запускає
внутрішній міст. Перевір MCP `initialize`/`tools/list`, потім
`resolve_free_connection_status` для свіжого native round-trip або
`resolve_lua_get_project`. `video_agent_status` читає кеш Python heartbeat;
він не описує marker-сеанс. Успішний stdio handshake або lease самі по собі
не доводять зв'язку з Resolve.

**Fairlight:** цей пакет поки не підтримує вибір фізичного input device,
patch input, record arm і перевірене вимкнення track/bus EQ, Dynamics та
всіх effects. Для цих app settings потрібне окреме перевірене керування GUI;
підготовлена монодоріжка чи успішна команда MCP не означає готовність до запису.
Додатковий [capture diagnostic](../scripts/capture-resolve-window.ps1) потребує
явної згоди оператора на UIA/GDI capture і лише знімає визначене вікно Resolve;
це не публічний MCP UI tool і не загальний спосіб керування застосунком.

Вбудована інтеграція Blackmagic з AI assistants — окремий маршрут Studio:
див. [офіційний анонс 21.1](https://www.blackmagicdesign.com/media/release/20260908-03)
та встановлений *DaVinci Resolve 21.1 Reference Manual*, розділ
*Using DaVinci Resolve with AI Assistants*, с. 4318 (`File > Setup AI Assistants`).
Її доступність у Free та робота цього пакета через неї не заявляються.

Канонічна межа v1 та послідовність наступних етапів зафіксовані в
[roadmap](roadmap.md).
Окремий опис M47 наведено в
[документації транскрипції](transcription.md).
Опис M48 наведено в
[документації editing recipes](editing-recipes.md).
Опис M49 наведено в
[документації visual treatment](visual-treatment.md).
Опис M50 наведено в
[документації baseline edit](baseline-edit.md).
Опис M51 наведено в
[документації B-roll](broll.md).
Опис M52 наведено в
[документації анімаційних шаблонів](animation-templates.md).
Поточний discovery/preview M53 описано в
[документації кольорокорекції](color-correction.md).
Поточний M54 описано в
[документації вибору дублів](take-selection.md).
Поточний M55 описано в
[документації creative v1 acceptance](v1-acceptance.md).
Конфігуроване винесення всіх великих даних на окремий диск описано в
[документації storage layout](storage.md).

Проєкт продовжує **Milestone M54: аналіз і розумний вибір дублів**. Аналізатор
працює як із 2–8 повними файлами, так і з 2–8 явно обмеженими сегментами до
300 секунд; один файл можна порівнювати в різних діапазонах. Він повертає
deterministic technical ranking, вимагає immutable human approve/reject review
і не змінює Resolve timeline.

M54.3 додає окремий script-aware режим для dialogue takes: локальна
faster-whisper транскрипція обмежується заданими ranges, а ranking поєднує
reference-text match із technical score. Еталонний текст і transcript не
зберігаються відкрито; результат усе одно потребує людського review.

M54.4 збирає 1–100 уже схвалених selections у deterministic approved sequence.
Sequence є path-redacted монтажним handoff для M55, не викликає ResolveBridge,
не змінює timeline і навмисно має `apply_supported=false`.

Проєкт почав **M55: creative v1 acceptance**. Перший slice безпечно прив'язує
approved sequence entries до exact allowlisted source-файлів: public receipt
редагує paths, а private machine-local binding зберігається лише в configured
runtime. Подальші slices уже реалізують окремо підтверджені import та duplicate
timeline apply; render залишається наступною окремою межею.

M55.2 додає read-only `preview_take_sequence_assembly`: він повторно перевіряє
private binding, читає локальні FPS/роздільність/тривалість через PyAV і
розраховує послідовні source/output ranges. Результат не містить paths,
попереджає про неоднорідні формати та не викликає ResolveBridge. Перетворення
секунд у frames конкретного timeline, import і apply залишаються наступними
окремо gated етапами.

M55.3 додає read-only `preview_take_sequence_timeline_mapping`. Він читає
FPS, resolution і track counts exact live timeline через уже перевірений
`resolve_get_editing_metadata` з порожнім списком asset IDs, після чого
перетворює M55.2 source ranges на inclusive source-frame bounds і безперервні
timeline positions. Import, duplicate timeline та вставка кліпів не виконуються;
`apply_supported=false`.

M55.4 додає read-only `preview_take_sequence_media_import`. Він звіряє
поточний bounded Media Pool snapshot з unique source fingerprints M55.3,
дедуплікує повторні ranges одного файла і планує import лише за відсутності
same-name item. Однакова назва не вважається доказом тотожності: такий source
отримує `review_name_collision`, а `import_ready=false`. Media Pool і timeline
не змінюються.

M55.5 додає write-tool `apply_take_sequence_media_import`. Він не приймає
paths: exact reviewed M55.4 `plan_id` повторно обчислюється, private binding
розв'язується лише всередині процесу, а один batch import виконується тільки з
`confirm_import=true` і штатним project backup. Після import агент звіряє
duration/FPS та Media Pool readback і зберігає path-redacted durable receipt;
exact replay не створює повторних items або backups. Timeline не змінюється.

M55.6 додає `preview_take_sequence_timeline_apply` та підтверджений
`apply_take_sequence_timeline`. Workflow приймає лише M55.5 receipt, нову
назву timeline й exact reviewed plan. Source timeline повинен бути порожнім і
ніколи не редагується: агент створює duplicate, вставляє approved video ranges
на V1 та лише фактично наявні audio streams на A1, після чого перевіряє всі
items і незмінність source. Операції мають окремі backups/idempotency keys;
durable receipt дозволяє продовжити interrupted apply без повторення завершених
кроків і не містить local paths.

M55.7 додає structural quality gate: `inspect_take_sequence_qc` виконує свіже
M55.6 readback і доводить точний V1/A1 склад, спільний timeline origin,
безперервність video та незмінність source. Звіт чесно залишає dialogue audio
processing, subtitle alignment, color і візуально-редакторську якість поза
автоматичним доказом та вимагає immutable `approve|reject` human review. Цей
етап не змінює Resolve і не підміняє старими несумісними receipts перевірки
іншої timeline.

M55.8 додає confirmed delivery для схваленої M55.7 timeline. Read-only preview
приймає лише allowlisted 1080p або 2160p H.264 profile, перевіряє live render
capabilities і формує collision-resistant output name. Start вимагає exact
`plan_id` та `confirm_render=true`, використовує наявні backup-backed render
primitives і не дозволяє caller-defined codec/settings/path. Status повторно
перевіряє QC approval, live job identity та непорожній MP4 тільки в configured
managed render root.

M55.9 закриває repeatable installer inventory: `install.ps1` і `verify.ps1`
тепер використовують той самий canonical набір M54/M55 runtime directories,
включно з bindings, imports, timeline receipts, QC та renders. Ізольований
Windows lifecycle у тимчасовому profile перевіряє install, повторний install,
offline verification і uninstall без доступу до реальних media/Resolve data.

Проєкт завершив **Milestone M53: кольорокорекція та visual QC**. Перший slice
додає immutable allowlisted CDL preset, read-only перевірку documented color
graph API та deterministic preview, прив'язаний до exact applied M52 receipt.
Live discovery у Resolve 21 Free підтверджено. Confirmed apply реалізований
лише на duplicate timeline з новою local color version і fixed node-1 CDL;
його live write, exact replay і помірне візуальне підсилення контрасту
підтверджені у Resolve 21.0.3 Free.

Завершений **Milestone M52: пакетовані анімаційні шаблони** додає один
allowlisted Fusion Title `accent-card-v1`, SHA-256 перевірку під час
інсталяції, read-only preview від completed M51 receipt і confirmed apply лише
до duplicate timeline. MCP не приймає Fusion code, node/control names,
expressions або довільний текст; title/subtitle редагуються вручну в Resolve
Inspector. Structural insertion, mirrored Anim Curves, Inspector controls і
exact replay підтверджені live у Resolve 21.0.3 Free.

M49 preview/apply workflow поєднує лише документовані статичні clip transforms і
вставку встановленого стандартного title за exact timecode. Він не викликає
Studio-only Smart Reframe, не змінює Fusion controls і не приймає довільних
Resolve properties. M48 пакетований
allowlisted recipe можна list/get/preview без змін Resolve і виконати лише з
явним confirmation, live capability gates та durable покроковим receipt.
Live M49 acceptance у Resolve 21 Free 21.0.3.7 підтвердив append-only guard,
рівно один generated title на exact frame, незмінність попередніх clips,
статичний zoom readback і replay без додаткових backups.
M51 read-only preview, confirmed duplicate-timeline apply та exact replay
перевірено у Resolve 21.0.3 Free: B-roll вставлено на V3, source M50 timeline
не змінився, а replay не створив нового timeline або backup.
M47 read-only discovery
перевіряє документований Resolve auto-caption API та bounded subtitle items, а
окремий confirmed write-tool запускає лише fixed-policy `AUTO` captioning після
backup і вимагає subtitle readback. M46 контрольовано експортує повну аудіодоріжку
finalized timeline у PCM WAV, потоково обробляє її та повертає валідований
результат на A2, вимикаючи лише пов'язані source items на A1. Проєкт
установлює внутрішній скрипт Resolve, перевіряє канонічні JSON-контракти,
обмінюється командами через локальний файловий транспорт і надає фіксовані
read-only та безпечні write-інструменти через stdio. M5 також створює локальні
чернетки rough cut із синхронізацією та аналізом пауз. M34 додає preview та
контрольоване застосування лише повністю підтриманого approved plan до копії
timeline; поточні `remove_pauses` плани чесно блокуються як unsupported.
Milestone M35: interactive editing session переводить вручну запущений bridge
у постійну інтерактивну сесію, а M36
додає backup-backed підготовку 1–8 video/audio доріжок перед точним placement.
Нові audio tracks створюються лише як `stereo`; видалення або зміна типу
наявних доріжок не підтримуються. Створення V2, точний readback 2V/1A і replay
без другої зміни перевірено у Resolve 21 Free 21.0.3.7.
M37 додає до bounded editing metadata фактичний FPS цільового timeline, щоб
майбутня синхронна розкладка переводила мілісекунди у frames без припущень.
Live readback у Resolve 21 Free 21.0.3.7 підтвердив 24 FPS для target timeline
і 60 FPS для source asset без backup або зміни проєкту.
M38 додає provider-neutral workflow `sync_screen_and_webcam`: він створює
новий timeline, готує V1/A1/V2 та розміщує screen video, screen audio і webcam
video з signed offset. Кожен write має окремий backup та derived idempotency
key, а progress receipt дозволяє безпечно продовжити перервану операцію.
M39 додає `compose_webcam_picture_in_picture`: workflow бере лише завершений
receipt M38, читає live resolution timeline і переводить нормалізовані
координати кадру в документовані `Pan`/`Tilt`/`ZoomX`/`ZoomY`. Маски, crop,
Fusion і довільні Resolve properties не входять до цього етапу.
Live-перевірка у Resolve 21 Free 21.0.3.7 підтвердила 1920×1080 metadata,
Pan/Tilt `614.4/-345.6`, Zoom `0.25`, один backup і replay без нової зміни.
M40 додає `link_synchronized_screen_pair`: workflow бере canonical screen V1
і A1 тільки з applied M38 receipt, зв’язує їх документованим Resolve API та
перевіряє взаємні IDs. Live-перевірка підтвердила capability у Resolve 21 Free,
а replay не створив нового backup.
M41 додає read-only `preview_synchronized_pause_compaction`: approved M5 cuts
перетворюються на kept source-frame ranges для нового V1/A1/V2 timeline без
недокументованих split/trim API. Live preview підтвердив один cut 560 ms,
шість placements і відсутність backup.
M42 додає підтверджений `apply_synchronized_pause_compaction`: він повторно
перевіряє preview, створює лише новий timeline, готує V1/A1/V2 і вставляє всі
kept ranges одним bounded batch-викликом із backup та durable replay receipt.
Live-перевірка у Resolve 21 Free створила 6/6 items у новому timeline, зберегла
вихідні 3/3 items, а replay не створив додаткових timeline або backup.
M43 додає `finalize_synchronized_pause_compaction`: workflow переносить уже
схвалені M39 webcam PIP і M40 screen V1/A1 links на кожен сегмент M42. Дві
bounded batch-команди створюють рівно по одному backup незалежно від кількості
сегментів; durable receipt та exact readback роблять повтор безпечним.
Live-перевірка у Resolve 21 Free 21.0.3.7 підтвердила 2 незалежні link-групи,
PIP на 2 webcam segments, 6/6 canonical items, рівно 2 backups і replay без
нового запису.
M44 додає `prepare_finalized_timeline_render`: tool приймає лише applied M43
receipt, явно вибирає його timeline і створює один allowlisted 1080p або 4K
MP4/H.264 job. Окреме `confirm_prepare=true` не запускає render; durable receipt
і provider idempotency блокують дублювання job під час replay.
Live-перевірка у Resolve 21 Free 21.0.3.7 підготувала 1080p job саме для M42
timeline, залишила його у `Ready`, створила один backup і не додала нового job
або backup під час replay.
M45 додає підтверджений `start_finalized_timeline_render` і read-only
`get_finalized_timeline_render_status`. Старт приймає лише applied M44 receipt,
перевіряє exact job/timeline/profile, `Ready` state та відсутність старого
output. Status tool не стартує job і перевіряє completed managed MP4.
Live-перевірка у Resolve 21 Free 21.0.3.7 завершила exact M44 job зі статусом
`Complete`/100%, підтвердила managed MP4 розміром 2 094 070 933 байти та replay
без повторного старту або нового backup.
M46 extraction додає `prepare_finalized_timeline_audio`,
`start_finalized_timeline_audio` і `get_finalized_timeline_audio_status`.
Workflow прив'язаний до applied M43 receipt, використовує лише вбудований
`Audio Only` preset і керовану директорію `audio-sources`. Готовий результат
приймається лише як незжатий 16-bit/48 kHz PCM WAV; довільні render settings
не приймаються.
`pcm-dialogue-limit-v2` додає детермінований hard limiter до nominal RMS gain,
а `apply_finalized_timeline_audio` приймає лише completed extraction і
`target_met=true` report, створює A2, вставляє exact full-timeline WAV та
вимикає канонічні A1 items. Live-перевірка у Resolve 21 Free 21.0.3.7
підтвердила 178906-frame A2 placement, збережені enabled V1 items і replay без
нового backup або дубліката.
M6 створює похідний PCM WAV і канонічний before/after report, не змінюючи
оригінал. Розширене редагування, довільна конфігурація рендеру й декодування
медіаконтейнерів ще не реалізовані.
Перший підетап M7 додає read-only discovery render formats, codecs, presets і
queue.
Після успішної live-перевірки discovery M7 також готує один фіксований
MP4/H264 job, але ніколи не запускає render автоматично.
Discovery та ідемпотентну підготовку такого job перевірено у Resolve 21 Free
21.0.3.7 на таймлайні з відео й аудіо.
M8 додає read-only статус і контрольований запуск лише такого
agent-підготовленого job. Повторний запуск блокується receipt і окремим
durable start-record. Read-only статус перевірено у Resolve 21 Free 21.0.3.7;
live-start підтверджено під час M11 на короткому timeline.
M9 додає офіційне resolution discovery та другий фіксований профіль
`youtube-2160p-h264-v1`. Наявність MP4/H.264 3840×2160 і preset
`YouTube - 2160p`, а також backup-backed ідемпотентну підготовку 4K job
підтверджено у Resolve 21 Free 21.0.3.7.
M10 додає backup-backed вставку обмеженого source range на конкретний
video/audio track. Позиція задається як offset від початку timeline; наявні
items не пересуваються, не обрізаються й не видаляються. Video/audio вставку,
frame-rate conversion і replay без дублів перевірено у Resolve 21 Free
21.0.3.7.
M11 виконує контрольований 1080p render короткого таймлайна та додає
read-only перевірку результату. Вона приймає лише `job_id`, звіряє керовану
output-директорію, 100% завершення й ненульовий розмір MP4. Декодування,
перевірка тривалості та візуального вмісту на цьому етапі не виконуються.
Live-тест у Resolve 21 Free 21.0.3.7 завершив чотирисекундний job за
3262 мс і підтвердив MP4 розміром 804100 байт.
M12 додає backup-backed увімкнення або вимкнення одного TimelineItem за
його ID. Bridge знаходить item лише у video/audio tracks, перевіряє lock-state,
викликає документований `SetClipEnabled(Bool)` і читає результат через
`GetClipEnabled()`. Вимкнення, replay без другого backup і відновлення
початкового стану перевірено у Resolve 21 Free 21.0.3.7.
M13 додає вибір наявного timeline за ID через документований
`Project.SetCurrentTimeline`. Bridge створює backup, повертає попередній і
фактичний поточний timeline та підтримує replay без повторного backup.
Read-only список і current timeline тепер повертають канонічний `timeline_id`,
тому вибір не залежить від старих receipts, назви або index.
ID discovery, перемикання M10 → M7, replay без другого backup і повернення
M7 → M10 перевірено у Resolve 21 Free 21.0.3.7.
M14 додає backup-backed зміну обмеженого набору властивостей одного video
TimelineItem: позиції X/Y, рівномірного zoom, кута повороту та opacity. Зовнішній
контракт не приймає довільних назв Resolve-властивостей або expressions.
Bridge перевіряє межі, lock-state доріжки й фактичні значення через
`GetProperty`. Зміну opacity 100 → 90, replay без другого backup і відновлення
90 → 100 перевірено у Resolve 21 Free 21.0.3.7.
M15 додає видалення рівно одного TimelineItem через документований
`Timeline.DeleteClips`. Операція завжди non-ripple, вимагає одночасно
`confirm_delete=true`, destructive safety flag і `.drp` backup. Live-сумісність
підтверджено у Resolve 21 Free 21.0.3.7 на окремому disposable item: delete
readback, replay без другого backup і повернення preflight до одного video та
одного audio item.
M16 закриває addressability loop для editing primitives: read-only discovery
повертає IDs усіх video/audio TimelineItem вибраного timeline разом із track,
timeline/source bounds і duration. Інструмент не читає довільні properties і
не змінює проєкт. Discovery синхронної M10 пари з окремими video/audio IDs,
однаковими timeline/source bounds і без нового backup перевірено у Resolve 21
Free 21.0.3.7.
M17 додає read-only рекурсивний перелік Media Pool items із канонічними
`asset_id`, назвами та логічними folder IDs/paths. Він не повертає файлові
шляхи, raw clip properties або Resolve object handles. Folder API також
повертає timeline entries, тому контракт чесно не називає всі результати
source assets. У Resolve 21 Free 21.0.3.7 знайдено п'ять items у `Master`,
включно з потрібним MKV asset `e2c01761-…`, без нового backup.
M18 додає фіксований read-only snapshot поточного workspace: одним запитом
повертаються bridge metadata, проєкт, timelines, current timeline та його
items, Media Pool і render discovery. Команда не приймає перелік довільних
дій, не створює backup і не запускає постійний цикл. Це скорочує повну
діагностику до одного ручного запуску ResolveBridge.
M19 додає backup-backed link/unlink для групи з 2–16 TimelineItem через
документовані `SetClipsLinked` і `GetLinkedItems`. Bridge приймає лише
канонічні IDs, перевіряє lock-state кожної доріжки, фактичний link-state та
idempotent replay. Переміщення, trim і split не реалізовані, бо відповідних
документованих TimelineItem API у локальній документації Resolve 21 немає.
M20 додає backup-backed дублювання одного timeline за ID через документований
`Timeline.DuplicateTimeline(name)`. Bridge відхиляє конфлікт імен, перевіряє
новий ID і появу копії у project timeline list, не перемикає current timeline
та не змінює оригінал. Це створює безпечну основу для майбутнього застосування
reviewed rough-cut до окремої копії.
M21 додає локальне явне схвалення draft-плану через окремий approval record.
Draft залишається незмінним, approval прив’язаний до SHA-256 його канонічного
вмісту, а повторне схвалення є idempotent. `apply_supported` залишається
`false`: схвалення не запускає ResolveBridge і не застосовує монтаж.
M22 додає read-only перелік bounded plan summaries і повторне відкриття одного
draft разом із чинним approval. Summary не містить source media paths, а
детальний запит приймає лише canonical `plan_id` і повторно перевіряє
контракти та SHA-256.

## Вимоги

- Windows 10 або Windows 11;
- Python 3.10, 3.11 або 3.12;
- Git;
- доступ до налаштованого Python package index під час першого встановлення.

## Встановлення

У PowerShell:

```powershell
git clone <repository-url>
cd DaVinci-Resolve-Agent
.\installer\install.ps1
```

Інсталятор створює:

- `.venv` у репозиторії;
- `%APPDATA%\DaVinciResolveAgent\config.toml`;
- `%LOCALAPPDATA%\DaVinciResolveAgent\runtime`;
- `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Edit\ResolveBridge.py`.

Після встановлення:

1. За потреби виконай offline preflight без запущеного Resolve:

```powershell
.\installer\verify.ps1 -SkipResolveConnection
```

2. Перезапусти DaVinci Resolve.
3. Відкрий проєкт і перейди на сторінку **Edit** у нижній панелі Resolve.
4. На сторінці Edit відкрий `Workspace → Scripts` і запусти `ResolveBridge`.
   З іншої сторінки Resolve може показувати його в підменю `Edit`.
5. Повернися до PowerShell і виконай повну перевірку:

```powershell
.\installer\verify.ps1
```

## CLI

```powershell
.\.venv\Scripts\davinci-agent.exe --version
.\.venv\Scripts\davinci-agent.exe status
.\.venv\Scripts\davinci-agent.exe ping
.\.venv\Scripts\davinci-agent.exe resolve bridge
.\.venv\Scripts\davinci-agent.exe resolve capabilities
.\.venv\Scripts\davinci-agent.exe resolve project
.\.venv\Scripts\davinci-agent.exe resolve timelines
.\.venv\Scripts\davinci-agent.exe resolve timeline
.\.venv\Scripts\davinci-agent.exe resolve snapshot --json
.\.venv\Scripts\davinci-agent.exe resolve render-options
.\.venv\Scripts\davinci-agent.exe diagnostics
```

`status` читає останній збережений heartbeat. `diagnostics` локально створює
sanitized JSON із версіями, конфігурацією, cached capabilities, metadata
failed commands і bounded log excerpts. Ці дві команди не потребують запуску
ResolveBridge. Інші команди ставлять перевірений запит у чергу та типово
очікують до 30 секунд. Запусти
`Workspace → Scripts → Edit → ResolveBridge` один раз: bridge обслуговує
allowlisted queue, доки не отримає `resolve_stop_bridge`. Час очікування можна
змінити через `--timeout-seconds`. Responsive UI, послідовні команди без
повторного запуску меню та clean stop перевірено у Resolve 21 Free 21.0.3.7.

Diagnostics bundle зберігається у фіксованій runtime-директорії, не містить
медіа, backups, raw commands/responses або відомих secret-полів. Перед
передаванням третій стороні його слід переглянути вручну. Деталі:
[локальна діагностика](diagnostics.md).

Кожна валідована Resolve-команда через filesystem transport створює окремий
canonical audit record до enqueue та атомарно оновлює його після відповіді або
timeout. Audit не містить arguments, media paths, idempotency keys, response
payloads чи error messages. Деталі: [audit logging](audit-logging.md).

Локальні rough-cut/editing/audio/delivery operations також створюють workflow
records зі станом `running`, `success` або `error`. Вони не містять arguments,
plan/report IDs, file paths, contents, results чи exception messages. Деталі:
[workflow audit](workflow-audit.md).

GitHub Actions перевіряє повний test suite на Windows із Python 3.10, 3.11 і
3.12. Окрема quality job запускає Ruff, mypy та read-only syntax parse
installer PowerShell scripts. M28 також запускає install → повторний install →
uninstall у повністю тимчасовому профілі Windows, перевіряє збереження
локальної конфігурації, backup/restore попереднього bridge та недоторканність
стороннього sentinel-файлу. M29 запускає в цій самій sandbox offline
`verify.ps1`, включно з перевіркою локального TOML і write/delete probes у
керованих директоріях. Workflow має лише `contents: read` і не виконує
deploy, commit, push або live Resolve automation. Деталі:
[безперервна інтеграція](continuous-integration.md).

M32 окремо збирає wheel, перевіряє його archive paths, metadata, entry points,
runtime package boundary і packaged resources. Після цього CI замінює editable
install на wheel та запускає CLI поза repository directory. Wheel не
публікується й не завантажується як artifact.

Live Resolve compatibility ведеться окремою platform/state матрицею.
Підтверджено Windows 10 build 19045, Python 3.12.10 і Resolve 21 Free
21.0.3.7 з відкритим проєктом та timeline. Windows 11, Studio, project absent
і timeline absent не видаються за перевірені та залишаються `pending`.
Деталі: [ручне інтеграційне тестування](manual-integration-testing.md).

Кожна з 24 allowlisted Resolve actions має рівно один повний command example,
який автоматично валідовується канонічною schema. Окремі fixtures покривають
success/error responses і всі capability value kinds. Статичні приклади
призначені для документації та тестів — їх не можна напряму ставити у live
чергу. Деталі: [протокол](protocol.md) і [contracts](../contracts/README.md).

Коди завершення CLI:

- `0` — успіх;
- `1` — помилка bridge, контракту, конфігурації або протоколу;
- `2` — некоректний виклик CLI;
- `3` — вичерпано час очікування команди.

## MCP

Локальний MCP-сервер запускається через stdio:

```powershell
.\.venv\Scripts\davinci-agent-mcp.exe
```

Для Codex репозиторій уже містить переносиму project-scoped конфігурацію
`.codex/config.toml`. Після `installer\install.ps1` відкрий цей checkout як
trusted project і перезапусти Codex. Read-only tools виконуються без
підтвердження, а write-tools запитують його. Наскрізна перевірка описана в
[документації MCP](mcp.md).

Read-only інструменти:

- `video_agent_status`;
- `resolve_get_project`;
- `resolve_list_timelines`;
- `resolve_get_timeline`.
- `resolve_list_timeline_items`.
- `resolve_list_media_pool_items`.
- `resolve_get_editing_metadata`.
- `resolve_get_subtitle_environment`.
- `resolve_get_workspace_snapshot`.
- `resolve_get_render_options`.
- `resolve_get_render_job_status`.
- `resolve_verify_render_output`.
- `list_editing_recipes`.
- `get_editing_recipe`.
- `preview_editing_recipe`.
- `preview_visual_treatment`.

Безпечні write-інструменти:

- `resolve_stop_bridge`;
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
- `prepare_finalized_timeline_audio`.
- `start_finalized_timeline_audio`.
- `get_finalized_timeline_audio_status`.
- `apply_finalized_timeline_audio`.

Recipe M48 `tutorial-layout-v1` декларативно компонує вже перевірені
`compose_webcam_picture_in_picture` та `link_synchronized_screen_pair`.
Довільні action lists, MCP tool names, kwargs або executable code через recipe
не приймаються. Деталі: [editing recipes](editing-recipes.md).

Локальний audio-інструмент M6:

- `clean_dialogue_audio`.
- `get_audio_report`.
- `list_audio_reports`.

Для Resolve-запитів потрібно один раз вручну запустити `ResolveBridge` з меню
Resolve. Після цього bridge обслуговує наступні allowlisted команди, доки
`resolve_stop_bridge` не завершить його cleanly. Перед кожною write-операцією
bridge експортує
проєкт у `.drp`; імпорт дозволений лише з `media.allowed_roots`. Приклад
конфігурації клієнта наведено в [документації MCP](mcp.md), а відновлення —
в [rollback strategy](rollback.md).

`create_rough_cut` не звертається до Resolve і не змінює проєкт. Для
детермінованого аналізу йому потрібні окремі нестиснені 16-bit PCM WAV-доріжки.
Результат завжди має статус `pending_review` та зберігається в локальній
runtime-директорії. Деталі наведено в
[документації rough cut](rough-cut.md).

`approve_rough_cut` приймає лише canonical `plan_id` і
`confirm_review=true`. Він повторно валідовує draft, фіксує його SHA-256 та
створює окремий approval record у runtime `plans`. Tool не змінює draft,
не ставить bridge-команду й завжди повертає `apply_supported=false`.

`list_rough_cut_plans` повертає до 100 summaries без source media paths.
`get_rough_cut_plan` приймає canonical ID і повертає валідований draft,
matching approval або `null`, effective status та `apply_supported=false`.
Обидва інструменти read-only і не використовують ResolveBridge.

`sync_screen_and_webcam` приймає нову назву timeline, canonical IDs screen і
webcam assets, signed `webcam_offset_ms` у межах ±30 секунд та
`confirm_sync=true`. Workflow створює новий timeline, забезпечує 2V/1A,
конвертує offset за live target FPS і вставляє screen video на V1, screen audio
на A1 та webcam video на V2. Webcam audio, trim, split і pause removal не
виконуються. Результат перевіряється через bounded TimelineItem readback;
фактичні stream extents після можливого Resolve clamp є авторитетними.

`compose_webcam_picture_in_picture` приймає canonical receipt ID завершеного
M38, `size_percent` від 10 до 50, координати центру `center_x_percent` і
`center_y_percent` від 0 до 100 та `confirm_layout=true`. Нуль координат —
лівий/верхній край кадру. Workflow читає live width/height timeline, застосовує
transform лише до canonical webcam item V2 і вимагає exact property readback.
Default 25/82/82 дає компактну розкладку праворуч унизу; кругла маска,
рамка, crop і Fusion поки не реалізовані.

`link_synchronized_screen_pair` приймає лише canonical applied receipt M38 і
`confirm_link=true`. Workflow витягує з нього рівно screen video V1 та screen
audio A1, викликає bounded `set_clips_linked` і вимагає, щоб кожен item у
readback містив ID іншого. Webcam V2, інші clips і доріжки не змінюються.

`preview_synchronized_pause_compaction` приймає approved `plan_id`, applied
M38 receipt і нову назву target timeline. Tool read-only звіряє approval hash,
sync offset, назви source assets і live FPS, після чого повертає cuts, kept
intervals та точні майбутні insert operations у source/target frame domains.
Він не створює timeline, clips або backup і повертає `apply_supported`
відповідно до live verified capabilities.

`apply_synchronized_pause_compaction` має ті самі plan/receipt/name inputs і
обов'язковий `confirm_apply=true`. Він повторно формує та хешує preview, створює
новий timeline, забезпечує V1/A1/V2, а всі placements передає провайдеру одним
bounded `insert_clips`. Перед кожною з трьох write-операцій створюється backup;
provider і workflow receipts роблять replay і відновлення після переривання
ідемпотентними. Вихідний M38 timeline не змінюється.

`clean_dialogue_audio` працює без Resolve та приймає allowlisted 16-bit PCM
WAV. V1 виконує детерміноване RMS leveling із peak guard, а версійований v2 —
nominal gain із hard limiter. Обидва потоково обробляють файл, зберігають
оригінал і створюють derived WAV. RMS dBFS не заявляється як LUFS. Деталі:
[audio workflow](audio-workflow.md).

`list_audio_reports` повертає до 100 summaries без source/derived paths.
`get_audio_report` приймає canonical `report_id` і повертає повторно
валідований before/after report. Обидва інструменти read-only, не обробляють
медіа та не використовують ResolveBridge.

`resolve_prepare_render_job` використовує лише allowlisted профілі
`youtube-1080p-h264-v1` і `youtube-2160p-h264-v1`, створює `.drp` backup,
пише output у
`<DataRoot>\media\renders` і додає job у queue.
1080p залишається default. Довільні dimensions, preset, codec і path не
приймаються. Rendering залишається незапущеним.

`resolve_start_render_job` приймає лише `job_id`, який має відповідати receipt
від `resolve_prepare_render_job` і незміненому job у live queue. Перед стартом
створюється `.drp` backup. Один job не можна повторно запустити іншим
`idempotency_key`; довільний або вручну створений job bridge відхиляє.

`resolve_verify_render_output` повторно читає live status job і локально
перевіряє, що завершений MP4 існує в
`<DataRoot>\media\renders` та має ненульовий розмір.
Інструмент read-only і не запускає render.

`resolve_insert_clip` приймає asset/timeline IDs, source frame bounds,
timeline-relative `position_frames`, `track_type` (`video` або `audio`) і
`track_index`. Bridge перевіряє track та lock-state, створює backup і повертає
фактичні bounds із TimelineItem.

`resolve_set_clip_enabled` приймає лише `timeline_id`, `timeline_item_id` і
boolean `enabled`. Перед зміною bridge перевіряє існування item і lock-state
й створює `.drp` backup. Tool не переміщує, не обрізає, не розділяє і не
видаляє кліп.

`resolve_set_clips_linked` приймає `timeline_id`, від 2 до 16 унікальних
`timeline_item_ids` і boolean `linked`. Bridge перевіряє наявність кожного
item, lock-state його video/audio track, створює `.drp` backup і звіряє
фактичні зв’язки через `GetLinkedItems`. Довільне групування, один item або
масове редагування всього timeline не підтримуються.

`resolve_set_current_timeline` приймає лише ID наявного timeline. Bridge
перевіряє його існування, створює `.drp` backup, викликає
`SetCurrentTimeline` і звіряє фактичний current timeline. Tool не створює,
не перейменовує і не видаляє timelines.

`resolve_duplicate_timeline` приймає ID наявного timeline та унікальну нову
назву. Перед викликом `DuplicateTimeline` bridge створює `.drp` backup, а
після нього звіряє нові ID/назву і наявність копії у проєкті. Оригінальний і
current timeline залишаються незмінними; якщо Resolve тимчасово перемкнув
current timeline, bridge відновлює попередній і перевіряє результат.

`resolve_ensure_timeline_tracks` приймає ID timeline та цільові кількості
video/audio доріжок від 1 до 8. Bridge лише додає відсутні доріжки через
документований `AddTrack`, створює нові audio tracks як `stereo`, після кожної
операції звіряє `GetTrackCount` і ніколи не видаляє наявні доріжки.

`resolve_set_clip_transform` приймає `timeline_id`, `timeline_item_id` та
щонайменше одне з полів `position_x`, `position_y`, `zoom`,
`rotation_degrees`, `opacity_percent`. Bridge працює лише з video item на
незаблокованій доріжці, створює `.drp` backup і звіряє записані значення.
Tool не приймає raw Resolve property names, keyframes, expressions або
довільний код.

`resolve_insert_title` приймає exact timeline, bounded ім'я встановленого
standard title, timecode `HH:MM:SS:FF` і `confirm_insert=true`. Bridge створює
backup, тимчасово переміщує playhead, викликає документований title insertion,
відновлює playhead та звіряє canonical item. Зміна тексту, Fusion controls і
Smart Reframe у M49 не підтримуються. Title дозволений лише на integer-FPS
timeline і лише на вільному хвості, не раніше `GetEndFrame`; bridge відхиляє
overlap до backup/write і перевіряє незмінність усіх попередніх video items.
Для статичних пакетних zoom/reframing та
title insertion використовуй `preview_visual_treatment`, а потім той самий plan
через `apply_visual_treatment(confirm_apply=true)`.

`resolve_delete_clip` приймає лише IDs одного TimelineItem і явне
`confirm_delete=true`. MCP позначає його destructive; transport встановлює
`allow_destructive=true`, bridge створює backup, викликає non-ripple delete і
перевіряє, що item більше не повертається з timeline. Tool не підтримує масове
або ripple-видалення.

`resolve_list_timeline_items` приймає `timeline_id` і повертає канонічні
`timeline_item_id` для video/audio tracks, їхні назви, track index,
timeline/source frame bounds та duration. Поле `source_type` розрізняє media і
generated items; для standard title source bounds дорівнюють `null`, оскільки
він не має Media Pool source. Tool read-only, не створює backup і
не повертає raw Resolve objects або довільні clip properties.

`resolve_list_media_pool_items` без аргументів рекурсивно перелічує поточний
Media Pool і повертає `asset_id`, назву, `folder_id` та логічний folder path. Tool
read-only, не створює backup, не читає файлові шляхи та не викликає
`GetClipProperty`.

`resolve_get_editing_metadata` приймає один `timeline_id` та 0–100 унікальних
`asset_id`. Він повертає кількість video/audio tracks, FPS і resolution
цільового timeline; для переданих asset також повертає `Frames` та `FPS`.
Порожній список є timeline-only readback і не обходить Media Pool;
raw property snapshot і файлові шляхи
не повертаються. Capability стає підтвердженою лише після успішного live
readback.

`resolve_get_workspace_snapshot` без аргументів збирає всі основні read-only
розділи одним bridge-запитом. Він не є універсальним batch dispatcher:
користувач не може передати назви команд, код або Resolve expressions.
Внутрішній скрипт запускається вручну та не додає зовнішнього керування
Resolve. Встановлена документація Resolve 21
описує зовнішній Scripting API як API Resolve Studio, тому для Resolve 21 Free
проєкт не заявляє непідтверджений автоматичний зовнішній запуск.

## Розробка

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy .
```

Докладніше дивись у документації про
[встановлення у Windows](installation-windows.md),
[архітектуру](architecture.md), [MCP](mcp.md) та
[rough cut](rough-cut.md), [audio workflow](audio-workflow.md),
[editing recipes](editing-recipes.md),
[visual treatment](visual-treatment.md),
[baseline edit](baseline-edit.md),
[rollback](rollback.md).
Основні вимоги продукту зафіксовані в
[SPECIFICATION.md](../SPECIFICATION.md).

## Видалення

Інтерактивно:

```powershell
.\installer\uninstall.ps1
```

Без запитань:

```powershell
.\installer\uninstall.ps1 `
    -PreserveConfig $true `
    -PreserveLogs $true `
    -PreserveBackups $true `
    -PreservePlans $true `
    -PreserveAudioReports $true `
    -PreserveProcessedAudio $true `
    -PreserveRenderOutput $true
```

Скрипт видаляє лише `.venv` цього репозиторію та каталоги застосунку, створені в
`APPDATA` і `LOCALAPPDATA`. Він не видаляє репозиторій, медіафайли чи проєкти
Resolve.
