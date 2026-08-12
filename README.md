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
| RouterAI credential для новой установки | runtime discovery / canonical path | `~/.config/opencode/credentials/routerai-api-key.txt` |
| глобальные инструкции OpenCode | `templates/AGENTS.md` | `~/.config/opencode/AGENTS.md` |
| `remote-long-running` | `opencode_setup/skills/remote-long-running` | `~/.agents/skills/remote-long-running` |
| `ssh-relay` | `dilukhin/ssh_relay` | `~/.agents/skills/ssh-relay` |
| `recovery-mode`, `risk-gate`, `safe-cli`, `unknown-system-safety` | `dilukhin/agent-safe` | `~/.agents/skills/<name>` |
| `ssh_relay` checkout | `dilukhin/ssh_relay`, `main` | `~/projects/ssh_relay` |
| `agent-safe` checkout | `dilukhin/agent-safe`, `master` | `~/projects/agent-safe` |
| OpenCode CLI / plugin | npm | global CLI / OpenCode config |

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

Если существующий JSONC использует комментарии/trailing commas и для миграции пришлось бы переформатировать файл, setup предпочитает `modified/conflict`, а не уничтожение авторского форматирования. Произвольный config без распознаваемого `provider.routerai` также сохраняется как conflict.

### Существующий `AGENTS.md`

Произвольный пользовательский `AGENTS.md` не забирается целиком во владение. Setup делает backup и добавляет маркированный managed block. Последующие обновления меняют только этот блок; пользовательский текст вокруг него сохраняется.

`--force`/`-Force` действует только на уже управляемое содержимое и не разрешает destructive операции с dependency repositories или неизвестными файлами.

## RouterAI credential

Для **новой** установки canonical path:

```text
Windows: %USERPROFILE%\.config\opencode\credentials\routerai-api-key.txt
Linux:   ~/.config/opencode/credentials/routerai-api-key.txt
```

Но существующая рабочая установка важнее default path. Если `opencode.jsonc` уже содержит `apiKey: "{file:...}"`, setup считает этот путь фактическим credential path, сохраняет файл как внешний и не создаёт параллельный placeholder в canonical/legacy location.

Правила:

- config ссылается на существующий внешний key → сохранить путь и байты, содержимое не читать/не печатать;
- config ссылается на отсутствующий key → сообщить missing/conflict, не создавать другой key с другим именем;
- fresh install → создать canonical credential placeholder и ссылку на него;
- credential metadata в manifest содержит provider/mode/path, но не secret и не SHA-256 секрета;
- Linux managed credential получает `0600`.

Legacy `~/projects/stash/opencode.ai/api-key.txt` остаётся поддержан для старых прямых вызовов `setup_core.py`, но wrappers для новых установок используют profile credential directory.

## ssh_relay и agent-safe

`ssh_relay` является authoritative source для `ssh-relay/SKILL.md`. Setup проверяет Python dependency/runtime, `ssh_relay.py --version`, `--help` и наличие `job`; реальная SSH-задача не запускается.

`agent-safe` является authoritative source для четырёх skills и устанавливается editable-командой `python -m pip install -e <repo>`, после чего проверяется `python -m agent_safe --help`.

Если default path уже существует, но не является Git working copy, setup **не удаляет и не заменяет его**. Это `modified/conflict`. Сначала пользователь должен определить назначение/ценность каталога; безопасный вариант — переименовать его вручную в backup-name и повторить setup, после чего reconciler сможет clone authoritative repository. Автоматический destructive repair намеренно отсутствует.

## OpenCode npm versions

OpenCode CLI (`opencode-ai`) и `@opencode-ai/plugin` используют npm `latest` при каждом reconcile: актуальная версия является no-op, устаревшая обновляется и проверяется после install. Ошибка доступа к registry является conflict, а не поводом гадать версию.

## remote-long-running и глобальный AGENTS.md

`remote-long-running` применяется для длительных сборок, CMake/CTest, integration/load tests и других процессов, которые могут пережить transport timeout. Для remote long jobs используется `ssh_relay job`, для больших transfers — relay `upload`/`download`.

## BMAD

BMAD остаётся project-local и имеет отдельную политику **pinned-tested**. Сейчас проект сохраняет проверенный pin `bmad-method@6.8.0`; более новая upstream-версия не становится штатной автоматически, пока не обновлены integrity/expected contract и не пройдены Windows/Linux install/reinstall validators.

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

Дополнительный regression suite `tests/test_setup_migration.py` проверяет сценарии, найденные на реальной существующей машине: external `{file:...}` credential без лишнего placeholder, fresh canonical credential, safe merge RouterAI config, managed block AGENTS и benign/unsafe untracked dependency paths.

GitHub Actions выполняет regression suite и полные Windows/Linux validators.

## Основные файлы

| Файл | Назначение |
|---|---|
| `setup_core.py` | orchestration и credential discovery |
| `setup_lib.py` | ownership, JSONC/AGENTS migration, Git reconciliation |
| `setup_migration.py` | совместимость/idempotency migration ownership |
| `setup_runtime.py` | npm/Python runtime checks |
| `setup_windows.ps1`, `setup_linux.sh` | платформенные wrappers |
| `templates/` | RouterAI managed fields и OpenCode instructions |
| `skills/remote-long-running/SKILL.md` | общий skill длительных операций |
| `config_data.json` | модели, version policies, BMAD и managed environment |
| `tests/test_setup_migration.py` | migration regression tests |
| `validate_setup.ps1`, `validate_setup.sh` | изолированные full validators |
| `setup_instructions.md` | подробная эксплуатационная инструкция |
| `bootstrap_prompt.md` | короткий вход для агента |
