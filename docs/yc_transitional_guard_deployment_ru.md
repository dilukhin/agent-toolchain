# Transitional YC guard: installation/reconciliation gate

## Статус

Этот компонент — временный рабочий deployment для разблокирования LanFabric. Он не закрывает полный authorization-binding gate.

Semantic policy остаётся в `dilukhin/opencode_permissions`. `agent-toolchain` владеет только exact-source acquisition, artifact validation, installation, ownership/reconciliation и stable entrypoint.

Pinned source:

Старый выпуск: `dilukhin/opencode_permissions@7922d612f244882aae3d843a64393b1363b593d9`, артефакт `sha256:149ca1892d90f493da59d527999bdafbfb16ff4d7d53b3989de4f0e439c5e0ef`.

Новый выпуск: `dilukhin/opencode_permissions@e363d99333248f99d2934837a259955decb4d1fa`, артефакт `sha256:61bf7e809a86e1117e2e6886e571d8a835601837fdc3479f78988af6ac1d05d5`.

## Модель установки

`toolchainctl yc-guard apply`:

1. не ищет `yc.exe` по диску;
2. читает только уже существующий `%LOCALAPPDATA%\LanFabric\yc-guard\state.json`;
3. требует ровно одну абсолютную ссылку на `yc.exe` в этом state;
4. проверяет существование и SHA-256 executable, но не запускает его;
5. скачивает exact pinned source `opencode_permissions`;
6. запускает canonical `build_yc_transitional_artifact.py`;
7. независимо валидирует content-addressed manifest и каждый bundle file;
8. публикует versioned runtime в `%LOCALAPPDATA%\agent-toolchain\tools\yc-guard\sha256-...`;
9. публикует owned `%LOCALAPPDATA%\agent-toolchain\bin\yc.cmd`;
10. делает effective PATH read-back.

Старый `%LOCALAPPDATA%\LanFabric\yc-guard` не меняется и остаётся fallback/backup.

Если managed bin отсутствует в текущем PATH, apply завершается conflict **до** публикации runtime/entrypoint.

Для уже установленного historical LanFabric guard предусмотрена узкая migration semantics: если текущий `yc` разрешается **ровно** из `%LOCALAPPDATA%\LanFabric\yc-guard\bin`, а `agent-toolchain\bin` доказан как owned user-PATH entry в ownership manifest, apply может переставить только owned managed bin непосредственно перед exact legacy guard bin. Legacy PATH entry сохраняется как fallback, остальные PATH entries не переставляются и не удаляются. Mutation применяется и к user PATH, и к текущему process PATH, после чего выполняется повторный preflight и обычный effective read-back.

Unknown shadow path, отсутствие ownership record, отсутствие/дублирование одной из exact PATH entries или любой неоднозначный PATH state дают fail-closed conflict. Generic `toolchainctl apply` по-прежнему не reorder'ит существующие PATH entries; это исключение принадлежит только ownership-aware migration transitional YC guard.

## Runtime

Новый `yc.cmd` вызывает только versioned `yc_transitional_entry.py`. Runtime на каждом вызове:

- проверяет manifest и digest файлов canonical artifact;
- проверяет pinned downstream `yc.exe` по SHA-256;
- не выполняет PATH lookup downstream YC;
- строит exact `parsed-simple/v1` fact из argv;
- вызывает canonical `yc_transitional_adapter` / `classifier_yc`;
- передаёт downstream executor только решение `ALLOW`.

`ASK_USER` и `DENY` в transitional версии блокируются без исполнения. Это достаточно для текущего LanFabric deployment, которому нужны canonical read-only операции и exact start/stop `epd42hrnss08t2440g90`.

Exit codes:

- 77 — DENY;
- 78 — ASK_USER (approval transport в transitional версии не реализован);
- 79 — integrity/runtime fail-closed.

## Команды

Read-only:

`toolchainctl yc-guard check`

Для `active` deployment эта команда также проверяет целостность owned `yc.cmd` и фактическое разрешение `yc` в текущем `PATH`. Успех означает, что effective-команда — именно `%LOCALAPPDATA%\\agent-toolchain\\bin\\yc.cmd`. Затенение legacy/чужим `yc`, отсутствие managed entrypoint или изменение его содержимого дают `modified/conflict` и ненулевой код. Проверка не меняет PATH, state, entrypoint или legacy guard.

Install/reconcile:

`toolchainctl yc-guard apply`

При наличии действующего проверенного старого выпуска `apply` возвращает `YC_UPGRADE_REQUIRED` до загрузки исходников и публикации нового release. Его нельзя использовать как upgrade. На уже проверенном новом выпуске с завершённым журналом повторный `apply` — no-op. Другие существующие состояния не перезаписываются.

Safe fallback:

`toolchainctl yc-guard disable`

Disable удаляет только доказанно owned `yc.cmd`; versioned runtime остаётся cached. Legacy LanFabric guard не удаляется. Если migration ранее подняла `agent-toolchain\bin` перед legacy PATH entry, порядок PATH не откатывается: после удаления managed `yc.cmd` lookup естественно проходит дальше к сохранённому legacy shim.

## Управляемый переход действующего выпуска (кандидат G2)

`toolchainctl yc-guard upgrade` требует строго старый active release с совпадающими state, shim, release marker, deployment, canonical manifest, payload SHA/размерами, downstream и effective PATH. Источник нового выпуска закреплён полным Git SHA и content-bound artifact ID; вызывающий не может передать branch или другой source. Уже активный проверенный новый выпуск с завершённым журналом даёт no-op.

Перед заменой сохраняются отдельные файлы точных байтов старого и нового state/shim, их SHA-256 и journal в owned state directory. Новый release остаётся отдельно; старый immutable release сохраняется. Межпроцессная блокировка использует owned lock file и OS-level nonblocking lock. Непонятный lock file не удаляется. Записи shim и state — **два отдельных** атомарных `os.replace`, между ними журнал остаётся `pending`. В этом состоянии `yc-guard check` отказывает; один успешный вызов команды без проверки активного состояния не считается завершением перехода.

`toolchainctl yc-guard recover --to-old` и `--to-new` допускаются только для распознанного `pending`/`rollback_pending`: обе закреплённые версии и checkpoint должны проходить проверку, наблюдаемые state и shim обязаны совпадать с одним из двух сохранённых вариантов. В остальных случаях остановиться и провести read-only диагностику. `toolchainctl yc-guard rollback` применим только после `complete` и проверки точного нового active state; он возвращает предыдущий **managed** выпуск, а не отключает guard. Удаление checkpoint/journal автоматически не выполняется. `disable` при наличии журнала отказывает.

`yc-guard check` остаётся read-only: отказывает при journal `pending`, неизвестной блокировке или несовпадении state/shim/release/manifest/downstream; для `complete` проверяет новый вариант, для `rolled_back` — прежний. Изменения PATH и legacy LanFabric guard не входят в upgrade. Допускаются только synthetic fixtures; реальный переход на DIMA-HP — отдельное задание G3 после Web-review G2, CI и доказанного recovery.

### Контроль активации при исполнении (G2 R2)

Новый immutable release содержит setup-owned `deployment.json` с точными путями managed data/bin/state/journal/lock/entrypoint и legacy-root. Release marker закрепляет SHA-256 deployment и исполняемого `yc_transitional_entry.py`; canonical artifact и pinned downstream проверяются отдельно. Новый runtime выводит пути из verified release, проверяет ownership marker установленного core и полный payload fingerprint **перед** загрузкой setup-модуля. Путь Python сравнивается с независимым managed launcher `toolchainctl` и полным шаблоном `yc.cmd`, не берётся из содержимого проверяемого `yc.cmd` или state.

Перед чтением активации runtime берёт ту же OS-блокировку, что setup upgrade/recover/rollback, но **не создаёт lock/state при чтении**. Он удерживает её и при проверке checkpoint, и во время дочерней команды. Занятая/отсутствующая/чужая блокировка, прямой вызов release runtime до завершения, частичный или чужой journal, drift state/shim/manifest/marker дают fail-closed код 79 без downstream. Для `mode=upgrade` новые разрешения действуют только при journal `complete` и точном новом active state/shim относительно проверенного old/new checkpoint; после `rolled_back` сохранённый новый release нельзя запустить напрямую для расширения прав. Старый immutable runtime не меняется: уже выполняющиеся старые команды R2 не останавливает.

Первая чистая установка нового выпуска не требует фиктивного старого snapshot: `apply` создаёт **отдельный** `mode=initial` checkpoint лишь нового state/shim и `pending` journal **до** публикации managed state/shim; после побайтовой проверки завершается `complete`. Пока запись отсутствует либо `pending`, новый runtime отказывает. Прерванное initial состояние допускает только явное `recover --to-new`, если отсутствующие/имеющиеся managed файлы доказанно совпадают с новым checkpoint или точным `prepared` state; `recover --to-old` запрещён. Для old-active режим initial не используется. Нераспознанные остатки/чужие файлы не удалять и не переобозначать.

Результаты synthetic failpoints доказывают поведение при прерывании процесса, **не** устойчивость к физическому отключению питания; directory metadata fsync на Windows отдельно не доказан. Проверка G2 и CI обязаны пройти Web security-review до любого rollout.

## Acceptance перед LanFabric

После merge и обновления установленного agent-toolchain:

1. `toolchainctl yc-guard check` подтверждает legacy state, downstream reference и что effective `yc` — owned managed entrypoint;
2. `toolchainctl yc-guard apply` завершается `effective_readback=PASS`;
3. новый shell: `Get-Command yc` указывает на `agent-toolchain\bin\yc.cmd`;
4. read-only smoke `yc compute instance list` успешен;
5. wrong ID test не выполняет cloud mutation и возвращает ASK_USER;
6. credential/guard-control test не выполняет downstream и возвращает DENY;
7. только после этих пунктов допускается отдельный exact start/stop smoke для `epd42hrnss08t2440g90`.

Реальные cloud mutations не входят в CI и не выполняются при installation/reconciliation.
