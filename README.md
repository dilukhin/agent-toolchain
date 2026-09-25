# agent-toolchain

`agent-toolchain` — кроссплатформенный Windows/Linux bootstrap и идемпотентный reconciler рабочего окружения OpenCode и связанных CLI-инструментов.

Основной установленный интерфейс — `toolchainctl`. Репозиторий нужен для bootstrap/разработки, но production-команды и skills не должны зависеть от состояния developer checkout.

## Быстрый старт

Bootstrap выполняется один раз для первоначальной установки управляющего core.

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

После первоначальной установки свежий управляющий core можно получать без обновления developer checkout:

```text
toolchainctl update
toolchainctl apply
```

или одной командой:

```text
toolchainctl update --apply
```

`bootstrap_*` использует базовый Python 3.10+ и стандартную библиотеку. Для доказательства source SHA чистого checkout bootstrap дополнительно читает локальный Git; без доказанного SHA установка получает обозначение `local`. Установленному core Git для определения версии не нужен. Общий bootstrap `venv` больше не создаётся. Python helper tools получают собственные изолированные runtimes.

Старые `setup_linux.sh` и `setup_windows.ps1` больше не являются интерфейсом: они являются hard tombstones, всегда завершаются ошибкой и только указывают перейти на `bootstrap_*` + `toolchainctl`.

## Команды

```text
toolchainctl --version       показать версию и build identity запущенного core
toolchainctl check           read-only диагностика target state
toolchainctl apply           привести управляемое состояние к target state
toolchainctl diff opencode-config  показать read-only managed-target diff OpenCode config с редактированием чувствительных значений
toolchainctl agents inspect    показать read-only inventory источников определений OpenCode agents без вывода prompt/description/permission patterns\ntoolchainctl agents adopt-model <role> --expected-sha <sha256>  принять ownership только поля model существующего Markdown-agent\ntoolchainctl adopt opencode-config --expected-sha <sha256>  явно принять проверенный legacy-drift payload как базу semantic ownership
toolchainctl update          обновить установленный управляющий core из актуального main
toolchainctl update --apply  обновить core и затем применить новый target state
```

`--version` использует semantic version управляющего core `0.1.0` и идентичность именно запущенного установленного payload. Для production core с доказанным `source_ref` формат — `toolchainctl 0.1.0.<8hex>`, где `<8hex>` — первые восемь символов полного source SHA из managed-core marker. Старый или локально опубликованный core без доказанного Git SHA явно помечается как `local.<fingerprint8>` (либо `dev` без marker), поэтому версия не подменяется текущим checkout или удалённым `main`.

Обычные `check`/`apply`/`updates`/`update` и ранние ошибки оставляют identity один раз в stderr. Для `opencode-proxied` и `codex-proxied` собственная версия доступна через `--wrapper-version`, структурированные метаданные — через `--health-json`; `--version` по-прежнему передаётся дочернему CLI. [Контракт диагностической identity и provenance](docs/diagnostic_identity_ru.md).

`check` не создаёт state/runtime/skills, не выполняет package install, clone/pull, chmod или backup. `apply` меняет только доказанно управляемые ресурсы. Неизвестное содержимое не усыновляется автоматически.

`toolchainctl agents inspect [--project PATH] [--json]` также строго read-only и не обращается к сети: он перечисляет известные global/custom/project/inline/system-managed config layers и Markdown-agent sources, показывает только безопасные metadata (`model`, `mode`, наличие `description`/prompt/permission/tools`, path/hash) и отмечает коллизии имён. Содержимое prompt/description, permission patterns и inline config не выводятся; remote organizational config и фактический runtime merge остаются отдельными источниками evidence.

При конфликте локально изменённого `OpenCode config` итоговая рекомендация указывает точный путь и предлагает `toolchainctl diff opencode-config`. Команда ничего не меняет: показывает записанный и текущий SHA-256, JSON-пути управляемых различий и unified diff текущего config с target, который был бы получен безопасным merge. Значения ключей, похожих на credentials/tokens/passwords/secrets, редактируются. Историческое содержимое предыдущего config намеренно не сохраняется, поэтому точный diff «с прошлого apply» по одному hash восстановить нельзя. Если managed-target semantic diff пуст, конфликт означает только drift ownership/hash; после проверки `toolchainctl apply --force` сначала создаст backup и примет совместимый config без изменения пользовательских полей.

`toolchainctl workspace-trust add|update|remove|list` управляет явным постоянным доверием к точному рабочему каталогу для `build`, `test`, `static_check`, `git_read`. Например: `toolchainctl workspace-trust add /absolute/project --scope build --scope test`. `list` читает реестр без изменений; другие команды требуют уже подготовленное через `apply` состояние. Текущие разрешения OpenCode эта возможность не расширяет: подключение к политике проходит отдельную приёмку в `opencode_permissions`. [Команды, хранение и границы доверия](docs/workspace_trust_ru.md).

`update` запрашивает точный SHA актуального `dilukhin/agent-toolchain@main`, скачивает архив именно этого SHA и публикует его штатным bootstrap-механизмом. Перед заменой повторно проверяется ownership и fingerprint фактического установленного core; локально изменённый или неизвестный core сохраняется и блокирует автоматическое обновление. Developer checkout при этом не читается и не изменяется.

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

Bootstrap публикует core атомарно из staging-каталога и создаёт стабильный `toolchainctl`. Существующий core или entrypoint принимается только при точном ownership marker. Повторный bootstrap с теми же fingerprint и source_ref является no-op; при обновлении payload или provenance предыдущий доказанно управляемый core сохраняется как backup.

## Managed CLI tools

Через ToolSpec управляются Python CLI, встроенные proxy-tools и `tunnelctl`:

| Tool | Production command | Production source | Runtime |
|---|---|---|---|
| `ssh_relay` | `ssh_relay` | `main` (`follow-branch`) | отдельный non-editable Python venv |
| `agent-safe` | `safe` | `master` (`follow-branch`) | отдельный non-editable Python venv |
| `tunnelctl` | `tunnelctl` | точный проверенный commit (`pinned-tested`) | собранный из исходников Go binary |
| `proxy-tools` | `opencode-proxied`, `codex-proxied` | текущий core | встроенный runtime |

В начале одного reconciliation-run production branch разрешается ровно один раз в точный 40-hex commit SHA. Этот SHA становится immutable execution identity для runtime и принадлежащих tool skills. Если branch изменился после resolution, новый commit относится к следующему запуску.

Установка Python tool выполняется из `repo@exact-commit`, а не из `~/projects/...`. Health запускается из установленного runtime. Для `ssh_relay` это в том числе `ssh_relay doctor`, который реально импортирует `paramiko`, не выполняя SSH/network соединение.

Developer checkouts `~/projects/ssh_relay` и `~/projects/agent-safe` могут существовать, быть dirty или вообще отсутствовать: это не должно менять production runtime.

`tunnelctl` собирается из временной чистой копии точного commit и публикуется как отдельный versioned binary. Для первого `apply` нужны Git, Go 1.22+ и OpenSSH client; они не устанавливаются автоматически. Проверка выполняет `tunnelctl --version` из установленного binary и `ssh -V`, не запускает туннель и не меняет его автозапуск. Изменение уже запущенного процесса не производится; запуск и переключение управляются отдельными командами `tunnelctl`.

### Skills из того же exact ref

`ssh-relay`, `recovery-mode`, `risk-gate`, `safe-cli`, `unknown-system-safety` получают source из того же exact commit, что соответствующий runtime:

```text
ToolSpec repo@ref
  ├─ package → isolated runtime
  └─ SKILL.md → owned exact-ref skill bundle → ~/.agents/skills/<name>/SKILL.md
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

- текущая managed routing policy использует прямой OpenAI/Codex provider: глобальный `model=openai/gpt-6-sol`, `small_model=openai/gpt-6-luna`; `general/build/plan` и `sol-specialist` используют Sol, `explore/luna/luna-safe-worker` — Luna, `astra-reviewer` — Astra;
- `explore` получает managed read-only policy без shell/web/edit/task; новые `docs-researcher`, `code-reviewer`, `evidence-auditor`, `code-worker`, `test-runner` публикуются отдельными owned Markdown-agent resources и не перезаписывают одноимённый чужой файл;
- для существующих `luna`, `luna-safe-worker`, `sol-specialist`, `astra-reviewer` prompt/description/permission остаются user-owned; после exact-SHA `agents adopt-model` toolchain владеет только frontmatter `model`, поэтому будущая смена модели не перезаписывает остальной файл;
- RouterAI provider, каталог и ссылка на credential сохраняются для явного выбора, но не используются ни одним управляемым глобальным маршрутом или управляемой рабочей ролью;
- `~/.config/opencode/opencode.jsonc` изменяется семантическим merge только когда это безопасно; для routing и других стабильных managed fields manifest хранит evidence конкретных JSON-путей, поэтому изменение пользовательского поля вне ownership не делает весь config конфликтным;
- прежний whole-file `merged-json` ownership мигрирует в semantic paths только при точном совпадении записанного SHA; неизвестный drift не усыновляется даже через `--force`;
- если legacy whole-file SHA уже разошёлся, автоматическая миграция остаётся fail-closed; после `toolchainctl diff opencode-config` пользователь может явно подтвердить конкретный текущий payload командой `toolchainctl adopt opencode-config --expected-sha <current-sha256>`. Команда проверяет exact SHA, делает backup, сохраняет неизвестные поля и отличающиеся routing overrides, а ownership записывает только для известных semantic paths;
- пользовательские неизвестные поля/models и неизвестные роли сохраняются; JSON policy управляет только явно перечисленными semantic agent fields, а существующие Markdown agents получают ownership только frontmatter `model` после exact-SHA adoption; prompt/description/permission не усыновляются;
- project-local OpenCode config и отдельные пользовательские agent-файлы могут иметь более высокий приоритет; agent-toolchain не сканирует и не переписывает произвольные проекты;
- JSONC с форматированием, которое нельзя сохранить безопасно, даёт conflict;
- `AGENTS.md` использует управляемый блок и не забирает произвольный пользовательский текст;
- неизвестные global skills не удаляются.

Fresh-install RouterAI credential path:

```text
Linux:   ~/.config/opencode/credentials/routerai-api-key.txt
Windows: %USERPROFILE%\.config\opencode\credentials\routerai-api-key.txt
```

Если существующий config уже ссылается на другой `{file:...}`, этот путь считается фактическим и сохраняется. Содержимое external credential не читается и не печатается. Fake/placeholder key для fresh install не создаётся. Linux-файл, которым toolchain доказанно управляет, получает mode `0600` без изменения байтов.

### Каталог моделей RouterAI

Объективные сведения о моделях RouterAI, включая цены, обновляются отдельным ежедневным GitHub Actions-процессом из публичного API и проходят через PR/CI перед попаданием в `main`. Роли моделей (`основная`, `архитектор`, `код/агент` и т. п.) остаются ручной политикой и автоматически не выбираются.

После появления свежего снимка в `main` клиент получает его через `toolchainctl update` и применяет новые известные управляемые подписи через `toolchainctl apply`. Пользовательские нестандартные названия моделей сохраняются.

Подробности: [`docs/routerai_catalog_updates_ru.md`](docs/routerai_catalog_updates_ru.md), [`setup_instructions.md`](setup_instructions.md) и [`docs/software_ownership_policy_ru.md`](docs/software_ownership_policy_ru.md).

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

Python managed-tool deployer поддерживает `git + python-venv` с policy `follow-branch` и `pinned-tested`; Go binary deployer поддерживает проверенный `tunnelctl` по `pinned-tested` exact commit.

Пока **не** подключены как реальные managed tools:

- `bundle`;

Для `bundle` следующий этап должен расширять общий ToolSpec/reconciler, а не добавлять отдельный install path. Выпуск готовых Windows/Linux артефактов `tunnelctl` с контрольными суммами остаётся последующим улучшением, которое позволит убрать зависимость клиента от Go.

## Безопасность

Основные инварианты:

- `unknown != ours`;
- `check` read-only;
- apply idempotent;
- никаких `git reset --hard`, `git clean`, force-update пользовательских checkout;
- source checkout отделён от installed runtime;
- installed production ref immutable; moving production branch сначала разрешается в exact SHA;
- secrets не записываются в manifest и не выводятся;
- Windows и Linux считаются first-class платформами.

Реализованный ToolSpec/manifest слой описан в [`docs/tooling_foundation_ru.md`](docs/tooling_foundation_ru.md).

Проверка core после bootstrap, поведение при повышенных правах и ограничения:
[доступность опубликованного core](docs/core_access_validation_ru.md).
