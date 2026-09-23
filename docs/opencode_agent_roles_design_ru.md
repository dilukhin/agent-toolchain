# OpenCode agent roles: ownership, inventory и целевой набор

Статус: design для issue #82. Реализация ролей выполняется поэтапно; первый этап — только read-only inventory.

## 1. Причина

После перехода managed routing на GPT-6 `agent-toolchain` доказанно владеет только известными semantic model paths в OpenCode config. Фактический ILUKHIN при этом показывает дополнительные effective permissions для встроенных и custom agents.

Совпадение имени роли не доказывает ownership её `description`, prompt/system, mode, permission/tools или отдельного Markdown-файла. Поэтому новые поля нельзя усыновлять задним числом и нельзя перезаписывать только потому, что `agent.<name>.model` уже принадлежит toolchain.

Инвариант: `unknown != ours`.

## 2. Фактически наблюдаемое состояние ILUKHIN

По пользовательскому `opencode agent list`:

- `astra-reviewer` — subagent; edit/bash/task запрещены;
- `sol-specialist` — subagent; edit/bash/task запрещены;
- `luna` — subagent; edit/bash запрещены, отдельный полный task deny не доказан;
- `luna-safe-worker` — subagent; edit запрещён; bash в основном запрещён, но `safe *` разрешён; task/web запрещены;
- `explore` — subagent, но произвольный bash разрешён, поэтому строгий read-only не доказан;
- `general` — исполняющая роль с широкими разрешениями.

`agent list` не доказывает источник definition, prompt/description contents и фактическое решение модели о делегировании.

## 3. Документированные OpenCode layers

Для текущего OpenCode учитываются как минимум:

1. remote organizational config;
2. global config `~/.config/opencode/opencode.json[c]`;
3. global Markdown agents `~/.config/opencode/agents/`;
4. `OPENCODE_CONFIG`;
5. project config `opencode.json[c]` от cwd вверх к ближайшему Git root;
6. project `.opencode/agents/`;
7. `OPENCODE_CONFIG_DIR`;
8. `OPENCODE_CONFIG_CONTENT`;
9. system managed config (`/etc/opencode`, `%ProgramData%\\opencode`, macOS managed location).

Project/custom/managed layers могут переопределять глобальные определения. Точное effective состояние подтверждается runtime evidence (`opencode agent list`, `opencode debug config` или эквивалент конкретной версии), а не одним global config.

## 4. Phase 1: read-only inventory

Команда:

```text
toolchainctl agents inspect [--project PATH] [--json]
```

Контракт:

- не создаёт state/directories/backups;
- не пишет config/manifest;
- не запускает OpenCode;
- не обращается к сети;
- перечисляет только известные локальные source candidates;
- показывает path, hash, layer, safe `model`/`mode`, наличие description/prompt/permission/tools;
- не выводит contents description/prompt;
- не выводит permission patterns;
- не выводит inline config payload;
- remote organizational config отмечает как `not-inspected`;
- коллизии одинаковых agent names между sources показываются явно;
- обнаружение source не означает ownership или effective winner.

Phase 1 не меняет существующие agents.

## 5. Целевой набор аналитических ролей

| Роль | Модель | Назначение | Mutation |
|---|---|---|---|
| `explore` | GPT-6 Luna | навигация по исходникам, поиск мест реализации | deny |
| `luna` | GPT-6 Luna | извлечение фактов из выбранных артефактов | deny |
| `docs-researcher` | GPT-6 Luna | публичная документация/API/version research | deny local mutation |
| `sol-specialist` | GPT-6 Sol | глубокая диагностика и проект решения | deny |
| `code-reviewer` | GPT-6 Sol | независимое обычное code review | deny |
| `evidence-auditor` | GPT-6 Sol | проверка claims/evidence/SHA/platform coverage | deny |
| `astra-reviewer` | GPT-6 Astra | критические ownership/privilege/secrets/recovery/architecture границы | deny |

Read-only profile строится fail-closed: broad deny сначала, затем только необходимые читатели. `ask` не считается read-only границей.

## 6. Целевой набор исполняющих ролей

| Роль | Модель | Назначение |
|---|---|---|
| `build` | GPT-6 Sol | основной координатор и интегратор |
| `general` | GPT-6 Sol | самостоятельная сложная подзадача |
| `code-worker` | GPT-6 Luna | bounded небольшая правка по готовому плану |
| `luna-safe-worker` | GPT-6 Luna | точная операция через существующие agent-safe/opencode_permissions contracts |
| `test-runner` | GPT-6 Luna | согласованный запуск тестов/сборок без самостоятельного исправления проверяемого кода |

Роль и privilege — разные решения. Более сильная модель не получает автоматически более широких системных полномочий.

## 7. Делегирование

Начальная policy:

- поиск/извлечение фактов → Luna;
- bounded механическая реализация с точным scope → `code-worker`;
- причинный анализ, сложная реализация, интеграция → Sol;
- обычное независимое review → отдельный Sol;
- рискованные границы и спорная архитектура → Astra;
- существенная задача перед закрытием → `evidence-auditor`, если стоимость проверки оправдана риском.

`description` и prompt помогают модели выбрать роль, но не гарантируют обязательное делегирование или денежный лимит. Это проверяется сценариями.

## 8. Ownership будущих managed roles

До реализации Phase 2 нужно определить ресурсную модель.

Предпочтительно:

- новые роли публиковать как отдельные managed resources с собственным manifest evidence, а не объявлять весь `opencode.jsonc` принадлежащим toolchain;
- существующий foreign Markdown-agent с тем же ID → conflict/preserve;
- существующий unowned JSON agent field с отличающимся значением → preserve + conflict/explicit decision, не adoption;
- managed field изменён пользователем → semantic drift + review перед repair;
- неизвестные fields внутри agent spec сохранять;
- project-local overrides не переписывать;
- удаление роли допускается только для доказанно owned resource;
- секреты и machine paths не включать в prompts/manifest.

Нельзя расширять нынешний `semantic-paths-v1` простым добавлением `description`/permission в desired template без migration contract для уже существующих state.

## 9. Permission contract

Read-only agent:

- edit/write/patch: deny;
- arbitrary bash/shell: deny;
- task: deny для leaf roles;
- unknown custom/MCP tools: deny по умолчанию;
- external directories: deny, кроме явно необходимых read sources;
- secrets: synthetic negative fixtures через read/grep/other accessible channels;
- web: только роли, которым он необходим, с очищенным вопросом.

Исполнитель:

- получает минимально необходимый набор mutation tools;
- scope, expected result, verification и stop conditions задаются отдельно;
- `safe *` сам по себе не является доказательством точного prepared-operation binding;
- privilege/recovery остаются в agent-safe/opencode_permissions, второй механизм не создаётся.

## 10. Phase 2 acceptance

Перед публикацией managed definitions:

- Phase 1 inventory на реальной машине;
- определён source winner/коллизии для всех существующих role IDs;
- unit fixtures fresh/existing/foreign/project override;
- check read-only;
- apply + repeat no-op;
- Windows/Linux;
- actual OpenCode load/effective permissions для поддерживаемой версии;
- negative permission smoke в disposable environment;
- никаких реальных рискованных mutations только ради теста.

## 11. Phase 3: quality/cost validation

Отдельно измерять:

- accepted-result rate;
- retries/escalations;
- missed significant findings;
- token/credit cost на принятую задачу;
- delegation accuracy;
- scope/permission violations.

Не оптимизировать только число дешёвых вызовов.
