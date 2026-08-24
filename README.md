# opencode_setup

`opencode_setup` — воспроизводимая настройка и безопасное обновление рабочего окружения OpenCode на Windows и Linux. Обычный setup является идемпотентным reconciler: устанавливает отсутствующие управляемые компоненты, обновляет устаревшие и не уничтожает неизвестные или локально изменённые данные.

## Быстрый старт

Windows:

```powershell
.\setup_windows.ps1 -Check
.\setup_windows.ps1
.\setup_windows.ps1 -Check
```

Linux:

```bash
./setup_linux.sh --check
./setup_linux.sh
./setup_linux.sh --check
```

`--check`/`-Check` ничего не изменяет и показывает состояния `missing`, `up-to-date`, `outdated`, `modified/conflict`.

## Что управляется

| Компонент | Источник истины | Целевое размещение |
|---|---|---|
| RouterAI/OpenCode managed fields | `templates/opencode.jsonc` | `~/.config/opencode/opencode.jsonc` |
| RouterAI credential path для новой установки | runtime discovery / canonical path | `~/.config/opencode/credentials/routerai-api-key.txt` |
| глобальные инструкции OpenCode | `templates/AGENTS.md` | `~/.config/opencode/AGENTS.md` |
| `remote-long-running` | `opencode_setup/skills/remote-long-running` | `~/.agents/skills/remote-long-running` |
| `ssh-relay` | `dilukhin/ssh_relay` | `~/.agents/skills/ssh-relay` |
| `recovery-mode`, `risk-gate`, `safe-cli`, `unknown-system-safety` | `dilukhin/agent-safe` | `~/.agents/skills/<name>` |
| `ssh_relay` checkout | `dilukhin/ssh_relay`, `main` | `~/projects/ssh_relay` |
| `agent-safe` checkout | `dilukhin/agent-safe`, `master` | `~/projects/agent-safe` |
| OpenCode CLI | обнаруженный владеющий менеджер; для fresh install — npm | активный executable в `PATH` |
| `@opencode-ai/plugin` | npm `latest` | OpenCode config directory |

В Windows `~` соответствует `%USERPROFILE%`. Старый `~/projects/stash/opencode.ai` сохраняется как legacy/внешнее размещение credential, но не является canonical path для новой установки.

## Модель install/update

Отдельных режимов `install` и `update` нет:

```text
нет компонента                         -> установить
managed-компонент отстал от источника -> обновить
компонент актуален                    -> ничего не менять
managed-файл изменён вручную          -> conflict, сохранить файл
tracked changes/local commits repo    -> conflict, без reset/clean
только benign untracked metadata      -> сохранить; разрешить чтение source
неизвестный skill                     -> не трогать
```

Dependency repository обновляется только fast-forward. Setup никогда не делает `git reset`, `git clean` или rebase пользовательской работы.

Benign untracked для dependency repos намеренно узкий: `.agent-safety/**` и Markdown-файлы. Они не блокируют использование уже актуального checkout как authoritative source. Если upstream отличается, setup всё равно не делает pull поверх этих файлов автоматически. Любые tracked changes, локальные commits и прочие untracked paths остаются conflict.

## Владение и безопасная миграция

Manifest:

- Windows: `%LOCALAPPDATA%\opencode_setup\state\manifest.json`;
- Linux: `${XDG_STATE_HOME:-$HOME/.local/state}/opencode_setup/manifest.json`.

Setup владеет только явно управляемым содержимым. Весь `.agents/skills` не считается принадлежащим setup; пользовательские и BMAD skills не удаляются.

### Существующий `opencode.jsonc`

Если уже есть совместимый `provider.routerai`, setup сначала разбирает config и сохраняет пользовательские поля, выбранную пользователем model/small_model и дополнительные model entries. Управляемые RouterAI fields добавляются/обновляются минимальным semantic merge с backup перед первой миграцией.

Если существующий config содержит другой provider (например, Qwen) и не использует JSONC-комментарии/trailing commas, RouterAI добавляется соседним provider. Обычный JSON-config без секции `provider` обрабатывается так же: существующие поля сохраняются, а `provider.routerai` добавляется с backup. Такой режим сохраняется в ownership manifest и на последующих apply не навязывает отсутствующие у пользователя top-level `model`/`small_model`.

Управляемая политика обновления OpenCode — `autoupdate: "notify"`: OpenCode сообщает о новой версии, но не меняет installation сам. Менеджер-владелец и рекомендуемая команда обновления показываются setup при инвентаризации CLI.

Если существующий JSONC использует комментарии/trailing commas и для миграции пришлось бы переформатировать файл, setup предпочитает `modified/conflict`, а не уничтожение авторского форматирования.

Если существующий `apiKey` задан не через `{file:...}` (например, inline или другим механизмом), setup не читает и не переносит значение, не создаёт параллельный placeholder и оставляет config как `modified/conflict` для явного решения пользователя.

### Существующий `AGENTS.md`

Произвольный пользовательский `AGENTS.md` не забирается целиком во владение. Setup делает backup и добавляет маркированный managed block. Последующие обновления меняют только этот блок; пользовательский текст вокруг него сохраняется.

`--force`/`-Force` действует только на уже управляемое содержимое и не разрешает destructive операции с dependency repositories или неизвестными файлами.

## RouterAI credential

Для **новой** установки canonical path:

```text
Windows: %USERPROFILE%\.config\opencode\credentials\routerai-api-key.txt
Linux:   ~/.config/opencode/credentials/routerai-api-key.txt
```

Но существующая рабочая установка важнее default path. Если `opencode.jsonc` уже содержит `apiKey: "{file:...}"`, setup считает этот путь фактическим credential path, сохраняет точную текстовую ссылку и сам файл как внешний. Содержимое внешнего credential setup не читает и не классифицирует.

Правила:

- config ссылается на существующий внешний key → сохранить ссылку и байты, содержимое не читать/не печатать;
- config ссылается на отсутствующий внешний key → сообщить missing/conflict, не создавать другой key с другим именем;
- config содержит non-file/inline `apiKey` → сохранить config как conflict, не читать/не выводить значение и не создавать placeholder;
- fresh install → записать canonical credential path в config/manifest, но **не создавать fake/placeholder key-файл**; до ручного provisioning credential остаётся `missing`;
- точный legacy-placeholder, созданный старой версией setup в доказанно managed credential path, распознаётся как `missing`, сохраняется без перезаписи и должен быть заменён пользователем реальным API key;
- внешний credential никогда не проверяется на совпадение с legacy-placeholder;
- credential metadata в manifest содержит provider/mode/path, но не secret и не SHA-256 секрета;
- Linux managed credential получает `0600` только после появления реального файла.

Legacy `~/projects/stash/opencode.ai/api-key.txt` остаётся поддержан для старых прямых вызовов `setup_core.py`, но wrappers для новых установок используют profile credential directory.

## ssh_relay и agent-safe

`ssh_relay` является authoritative source для `ssh-relay/SKILL.md`. Setup проверяет Python dependency/runtime, `ssh_relay.py --version`, `--help` и наличие `job`; реальная SSH-задача не запускается.

`agent-safe` является authoritative source для четырёх skills и устанавливается editable-командой `python -m pip install -e <repo>`, после чего проверяется `python -m agent_safe --help`.

Если default path уже существует, но не является Git working copy, setup **не удаляет и не заменяет его**. Это `modified/conflict`. Сначала пользователь должен определить назначение/ценность каталога; безопасный вариант — переименовать его вручную в backup-name и повторить setup, после чего reconciler сможет clone authoritative repository. Автоматический destructive repair намеренно отсутствует.

## Владение версиями ПО и дубли

Перед изменением OpenCode CLI setup инвентаризирует все физические `opencode` в `PATH`, определяет активный экземпляр, фактическую версию и известный менеджер установки. Если одновременно видны несколько экземпляров, setup сообщает все пути/версии и останавливает автоматическое изменение CLI: пользователь выбирает, какую установку оставить, а какие удалить через их менеджеры или изолировать от общего `PATH`.

Если виден один OpenCode, но дополнительная package-manager установка изолирована от `PATH`, она отмечается как предупреждение и может быть оставлена, если изоляция намеренная. Если фактическая версия active executable расходится с metadata менеджера, setup сообщает об этом отдельно и ничего не исправляет автоматически.

Для существующей однозначной установки её менеджер сохраняется владельцем. Например, Chocolatey-установка не вызывает установку второй npm-копии. Официальная standalone-установка из install script в `~/.opencode/bin` или `~/.local/bin` распознаётся как `curl`-installation и не требует наличия npm; рекомендуемая команда обновления для неё — `opencode upgrade --method curl`. Для нового компьютера без OpenCode текущий bootstrap по-прежнему использует npm.

В отчёте показывается рекомендуемая команда обновления, когда она известна: например `choco upgrade opencode -y` или `npm install -g opencode-ai@latest`.

Та же read-only проверка дублей применяется к Git, Python, Node.js/npm и `uv`. Для этих общих инструментов наличие нескольких версий пока является предупреждением, а не автоматическим конфликтом: несколько Python или version-manager shims могут быть намеренными.

Подробная политика: [`docs/software_ownership_policy_ru.md`](docs/software_ownership_policy_ru.md).

## OpenCode plugin versions

Если npm доступен, `@opencode-ai/plugin` сохраняет прежнюю политику npm `latest`: актуальная версия является no-op, устаревшая обновляется и проверяется после install. Для уже установленного standalone OpenCode отсутствие npm не считается конфликтом: OpenCode умеет устанавливать config-scoped dependency через свой Bun runtime при загрузке config, поэтому setup не требует Node/npm только ради принятия такой установки.

## remote-long-running и глобальный AGENTS.md

`remote-long-running` применяется для длительных сборок, CMake/CTest, integration/load tests и других процессов, которые могут пережить transport timeout. Для remote long jobs используется `ssh_relay job`, для больших transfers — relay `upload`/`download`.

## BMAD

BMAD остаётся project-local и имеет отдельную политику **pinned-tested**. Штатный pin этой версии проекта — `bmad-method@6.11.0` с проверенным npm integrity, upstream release commit и контрактом **49 skills**. Для BMAD 6.11 также проверяются runtime prerequisites: Node.js 20.12+, Python 3.11+ и `uv`. Новая upstream-версия не становится штатной автоматически, пока не обновлены integrity/expected contract и не пройдены Windows/Linux install/reinstall validators.

```powershell
.\install_bmad_windows.ps1 C:\path\to\project
```

```bash
./install_bmad_linux.sh /path/to/project
```

BMAD создаёт `<project>/_bmad` и `<project>/.agents/skills`. Глобальный setup эти skills не удаляет и не перезаписывает.

## Проверка разработки

```powershell
.\validate_setup.ps1 -TestBmad
```

```bash
./validate_setup.sh --bmad
```

Regression suite `tests/test_setup_*.py` проверяет сценарии, найденные на реальных существующих машинах: external `{file:...}` credential без инспекции содержимого, сохранение exact file reference, inline credential conflict, fresh canonical credential path без fake key-файла, legacy managed placeholder как `missing`, sibling-provider Qwen migration, многократную идемпотентность manifest/config, managed block AGENTS, benign/unsafe untracked dependency paths, agent-safe egg-info self-healing и ownership/duplicate-policy OpenCode.

GitHub Actions выполняет regression suite и полные Windows/Linux validators.

## Основные файлы

| Файл | Назначение |
|---|---|
| `setup_core.py` | orchestration и credential discovery |
| `setup_lib.py` | ownership, JSONC/AGENTS migration, Git reconciliation |
| `setup_migration.py` | совместимость/idempotency migration ownership и `autoupdate` policy |
| `setup_runtime.py` | OpenCode/npm/Python runtime checks и ownership manager policy |
| `setup_inventory.py` | read-only inventory executable/дублирующихся установок |
| `setup_windows.ps1`, `setup_linux.sh` | платформенные wrappers |
| `templates/` | RouterAI managed fields и OpenCode instructions |
| `skills/remote-long-running/SKILL.md` | общий skill длительных операций |
| `config_data.json` | модели, version policies, BMAD и managed environment |
| `tests/test_setup_*.py` | migration/runtime regression tests |
| `validate_setup.ps1`, `validate_setup.sh` | изолированные full validators |
| `setup_instructions.md` | подробная эксплуатационная инструкция |
| `bootstrap_prompt.md` | короткий вход для агента |
