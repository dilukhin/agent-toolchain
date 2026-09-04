# Global AGENTS ownership и миграция

## Цель

`~/.config/opencode/AGENTS.md` (Windows: `%USERPROFILE%\.config\opencode\AGENTS.md`) считается пользовательским/агентским документом. `agent-toolchain` не владеет файлом целиком и не должен считать изменение текста вне своей зоны конфликтом.

Управляемые инструкции разделяются на две части:

1. маленький bootstrap-блок внутри глобального `AGENTS.md`;
2. отдельный полностью managed-файл `agent-toolchain/managed-instructions.md` в том же OpenCode config tree.

Целевой layout:

```text
<OpenCode config>/AGENTS.md
<OpenCode config>/agent-toolchain/managed-instructions.md
```

Bootstrap-блок содержит критический guard, стабильную ссылку на managed-файл и правило не редактировать только сам блок. Пользовательские/машинные/агентские инструкции размещаются вне managed-маркеров.

## Ownership

`global AGENTS.md`:

- ownership только bootstrap-блока;
- surrounding text не хэшируется как managed content;
- изменение surrounding text — нормальное состояние, не `modified/conflict`;
- изменение managed bootstrap-блока — `modified/conflict`;
- manifest хранит `path`, `source`, `mode=bootstrap-block-v1`, `block_sha256`.

`OpenCode managed instructions`:

- весь файл принадлежит `agent-toolchain`;
- обычный `check` read-only;
- обычный `apply` создаёт/обновляет только доказанно managed файл;
- локально изменённый managed-файл сохраняется и даёт `modified/conflict`;
- явный `--force` может заменить только этот доказанно managed файл после backup.

## Bootstrap markers

Новая product identity:

```text
<!-- agent-toolchain:managed:start:v1 -->
...
<!-- agent-toolchain:managed:end:v1 -->
```

Legacy markers `opencode_setup:managed:*` распознаются только как источник one-way migration.

## One-way migration

### Existing block ownership

Если manifest доказывает ownership legacy managed-блока и фактический block hash совпадает с сохранённым `block_sha256`, `apply`:

1. делает backup `AGENTS.md`;
2. заменяет только legacy block на новый bootstrap block;
3. сохраняет surrounding user text byte-for-byte;
4. создаёт/проверяет `agent-toolchain/managed-instructions.md`;
5. переводит manifest в `bootstrap-block-v1`.

Если legacy managed block был локально изменён, обычный `apply` не перезаписывает его.

### Legacy whole-file ownership, файл не изменён

Если текущий hash файла совпадает с hash из валидного manifest, весь старый payload доказанно owned. `apply` может безопасно заменить его новым bootstrap-документом после backup и записать block ownership.

### Legacy whole-file ownership, есть локальные добавления

Автоматическая миграция разрешена только когда предыдущий manifest hash соответствует известному historical managed payload и этот historical payload присутствует в текущем файле ровно один раз и без изменений. Текст до/после него считается surrounding user text и сохраняется.

Это покрывает текущий ILUKHIN-case: прежний managed `AGENTS.md` + добавленная инструкция `host-safety.md`.

Если historical payload не удаётся доказанно выделить (замены/удаления внутри него, неизвестная предыдущая версия, несколько совпадений), состояние остаётся `modified/conflict`. `--force` не должен превращать неоднозначный legacy whole-file в автоматическую destructive migration.

### Existing user file without ownership

Если `AGENTS.md` существует, не содержит conflicting managed markers и не имеет ownership metadata, `apply` делает backup и добавляет bootstrap block, не меняя существующий текст.

## Порядок apply

Перед mutation строится read-only migration plan для `AGENTS.md`. Если он неоднозначен — writes не выполняются.

После успешного preflight сначала reconciliate полностью managed `managed-instructions.md`; только если он безопасно `up-to-date/configured`, меняется bootstrap block. Это исключает появление ссылки на unresolved/foreign managed instructions.

## Acceptance

Обязательные regressions:

- fresh user `AGENTS.md` + bootstrap append;
- user text added after migration остаётся `up-to-date` и byte-preserved;
- migration existing legacy block с surrounding text;
- migration unmodified legacy whole-file;
- migration ILUKHIN whole-file + appended `host-safety` rule с сохранением строки;
- ambiguous modified legacy whole-file fail closed без mutation;
- modified bootstrap block conflict;
- modified managed instructions conflict;
- `check` strictly read-only;
- repeat `apply` no-op;
- Windows + Linux.
