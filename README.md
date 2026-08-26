# agent-toolchain

`agent-toolchain` — кроссплатформенный Windows/Linux bootstrap и идемпотентный reconciler рабочего окружения OpenCode и связанных CLI-инструментов.

Основной установленный интерфейс — `toolchainctl`. Репозиторий нужен для bootstrap/разработки, но production-команды и skills не должны зависеть от состояния developer checkout.

> До ручного переименования GitHub repository этот код временно находится в `dilukhin/opencode_setup`. Product identity и новый runtime namespace уже `agent-toolchain`.

## Быстрый старт

Bootstrap выполняется один раз или при обновлении самого управляющего core.

Linux:

```bash
./bootstrap_linux.sh
toolchainctl check
toolchainctl apply
toolchainctl check
```

Windows:

```powershell
.\bootstrap_windows.ps1
toolchainctl check
toolchainctl apply
toolchainctl check
```

`bootstrap_*` использует только базовый Python 3.10+ и стандартную библиотеку. Общий bootstrap `venv` больше не создаётся. Python helper tools получают собственные изолированные runtimes.

Старые `setup_linux.sh` и `setup_windows.ps1` больше не являются интерфейсом: они являются hard tombstones, всегда завершаются ошибкой и только указывают перейти на `bootstrap_*` + `toolchainctl`.

## Команды

```text
toolchainctl check   read-only диагностика target state
toolchainctl apply   привести управляемое состояние к target state
```

`check` не создаёт state/runtime/skills, не выполняет package install, clone/pull, chmod или backup. `apply` меняет только доказанно управляемые ресурсы. Неизвестное содержимое не усыновляется автоматически.

Состояния отчёта:

- `up-to-date` — состояние уже было целевым до текущего запуска;
- `configured` — текущий apply успешно изменил управляемый ресурс;
- `missing` / `outdated` — диагностическое состояние, которое apply может исправить;
- `modified/conflict` / `failed` — автоматическое продолжение небезопасно или действие не прошло;
- `info` / `skipped` — нейтральная информация.

## Runtime и state

Linux:

```text
core:          ${XDG_DATA_HOME:-~/.local/share}/agent-toolchain/core
managed tools: ${XDG_DATA_HOME:-~/.local/share}/agent-toolchain/tools/...
public bin:    ~/.local/bin
state:         ${XDG_STATE_HOME:-~/.local/state}/agent-toolchain
```

Windows:

```text
core:          %LOCALAPPDATA%\agent-toolchain\core
managed tools: %LOCALAPPDATA%\agent-toolchain\tools\...
public bin:    %LOCALAPPDATA%\agent-toolchain\bin
state:         %LOCALAPPDATA%\agent-toolchain\state
```

Пути можно переопределять тестовыми/служебными переменными `AGENT_TOOLCHAIN_DATA_DIR`, `AGENT_TOOLCHAIN_BIN_DIR`, `AGENT_TOOLCHAIN_STATE_DIR`.

Bootstrap публикует core атомарно из staging-каталога и создаёт стабильный `toolchainctl`. Существующий core или entrypoint принимается только при точном ownership marker. Повторный bootstrap с тем же fingerprint является no-op; при реальном обновлении предыдущий доказанно управляемый core сохраняется как backup.

## Managed CLI tools

Сейчас через ToolSpec реально управляются два Python CLI:

| Tool | Production command | Pinned ref | Runtime |
|---|---|---|---|
| `ssh_relay` | `ssh_relay` | `1a794f84bb3664fe580716195ee939bbe2295675` | отдельный non-editable Python venv |
| `agent-safe` | `safe` | `95545d20533b2dfa1de7d75a30fa1bbfb1d428e3` | отдельный non-editable Python venv |

Установка Python tool выполняется из `repo@exact-commit`, а не из `~/projects/...`. Health запускается из установленного runtime. Для `ssh_relay` это в том числе `ssh_relay doctor`, который реально импортирует `paramiko`, не выполняя SSH/network соединение.

Developer checkouts `~/projects/ssh_relay` и `~/projects/agent-safe` могут существовать, быть dirty или вообще отсутствовать: это не должно менять production runtime.

### Skills из того же pinned ref

`ssh-relay`, `recovery-mode`, `risk-gate`, `safe-cli`, `unknown-system-safety` получают source из того же exact commit, что соответствующий runtime:

```text
ToolSpec repo@ref
  ├─ package → isolated runtime
  └─ SKILL.md → owned pinned skill bundle → ~/.agents/skills/<name>/SKILL.md
```

Для получения skill используется временный clean checkout exact SHA; фактический `HEAD` проверяется до публикации. В ownership manifest source label содержит точный tool/ref/path. Tracking checkout пользователя не является authoritative production source.

## PATH

Linux target public bin — `~/.local/bin`. `agent-toolchain` не редактирует неизвестные shell startup-файлы автоматически: если каталог отсутствует в текущем `PATH`, выводится manual action.

Windows target — `%LOCALAPPDATA%\agent-toolchain\bin`. `toolchainctl apply` может добавить этот каталог в user PATH без перестановки/удаления других entries и записывает ownership в `managed_path_entries`. Уже существующий совпадающий PATH entry используется, но не объявляется принадлежащим `agent-toolchain` задним числом.

Чужой `ssh_relay`, `safe` или `toolchainctl` в целевом public path не перезаписывается. Shadowing через PATH диагностируется отдельно.

## Ownership manifest и one-way миграция

Manifest schema 2 содержит:

```json
{
  "schema": 2,
  "managed_files": {},
  "credentials": {},
  "managed_tools": {},
  "managed_path_entries": {}
}
```

Для перехода с прежнего `opencode_setup` действует одноразовая миграция:

1. если новый state уже существует — используется только он;
2. если нового state нет, но есть известный legacy state с валидным manifest, `toolchainctl check` читает его без записи;
3. первый `toolchainctl apply` копирует legacy state во временный каталог, валидирует копию и атомарно публикует новый `agent-toolchain` state;
4. исходный legacy state остаётся неизменённым как inactive backup до отдельного cleanup;
5. неизвестный legacy-каталог без доказанного manifest не усыновляется.

Это one-way migration, а не compatibility mode: после появления нового state production reconciliation работает с namespace `agent-toolchain`.

## OpenCode config, credentials и global instructions

`toolchainctl` сохраняет ранее реализованные безопасные политики OpenCode:

- `~/.config/opencode/opencode.jsonc` изменяется семантическим merge только когда это безопасно;
- пользовательские неизвестные поля/models сохраняются;
- JSONC с форматированием, которое нельзя сохранить безопасно, даёт conflict;
- `AGENTS.md` использует управляемый блок и не забирает произвольный пользовательский текст;
- неизвестные global skills не удаляются.

Fresh-install RouterAI credential path:

```text
Linux:   ~/.config/opencode/credentials/routerai-api-key.txt
Windows: %USERPROFILE%\.config\opencode\credentials\routerai-api-key.txt
```

Если существующий config уже ссылается на другой `{file:...}`, этот путь считается фактическим и сохраняется. Содержимое external credential не читается и не печатается. Fake/placeholder key для fresh install не создаётся. Linux-файл, которым toolchain доказанно управляет, получает mode `0600` без изменения байтов.

Подробности: [`setup_instructions.md`](setup_instructions.md) и [`docs/software_ownership_policy_ru.md`](docs/software_ownership_policy_ru.md).

## BMAD

BMAD остаётся project-local и устанавливается отдельно:

Linux:

```bash
./install_bmad_linux.sh /path/to/project
```

Windows:

```powershell
.\install_bmad_windows.ps1 C:\path\to\project
```

Текущий pin хранится в `config_data.json`; installer проверяет prerequisites, npm integrity и post-install contract.

## Что пока не реализовано

Текущий managed-tool deployer поддерживает первый production runtime family `git + pinned-tested + python-venv`.

Пока **не** подключены как реальные managed tools:

- `tunnelctl` (`go-binary`);
- `bundle`;
- `proxy-tools`.

Для них следующий этап должен расширять общий ToolSpec/reconciler, а не добавлять отдельные ad-hoc install paths.

## Безопасность

Основные инварианты:

- `unknown != ours`;
- `check` read-only;
- apply idempotent;
- никаких `git reset --hard`, `git clean`, force-update пользовательских checkout;
- source checkout отделён от installed runtime;
- production ref immutable/pinned-tested;
- secrets не записываются в manifest и не выводятся;
- Windows и Linux считаются first-class платформами.

Реализованный ToolSpec/manifest слой описан в [`docs/tooling_foundation_ru.md`](docs/tooling_foundation_ru.md).
