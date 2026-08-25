# ToolSpec и ownership manifest schema 2

## Статус

Этот документ описывает **реализованный фундамент** toolchain deployment. Он не означает, что `bundle`, `tunnelctl` или `proxy-tools` уже развертываются текущим setup.

Подробная целевая архитектура принадлежит `opencode_setup_toolchain_deployment_design_ru.md` в Sources проекта.

## Manifest schema 2

Текущий ownership manifest содержит обязательные разделы:

```json
{
  "schema": 2,
  "managed_files": {},
  "credentials": {},
  "managed_tools": {},
  "managed_path_entries": {}
}
```

Назначение новых разделов:

- `managed_tools` — ownership установленного runtime, source/ref/version, scope, install method, runtime path, checksum, entrypoints и health metadata;
- `managed_path_entries` — только PATH entries, которыми setup доказанно владеет и которые вправе поддерживать.

На текущем этапе оба раздела могут быть пустыми.

### Миграция v1 → v2

Миграция автоматическая и nondestructive:

1. schema 1 проверяется как известный legacy-формат;
2. существующие `managed_files`, `credentials` и дополнительные top-level metadata сохраняются;
3. в памяти добавляются `managed_tools` и `managed_path_entries`;
4. `-Check`/`--check` сообщает `outdated`, но **не пишет manifest**;
5. обычный apply сохраняет schema 2;
6. следующий apply не должен менять уже сохранённый v2 manifest.

Неизвестная schema, нечитаемый JSON или неверный тип обязательной секции дают `modified/conflict`. Setup не пытается восстанавливать такой manifest destructive-операциями.

## ToolSpec schema 1

`config_data.json` содержит:

```json
"managed_environment": {
  "manifest_schema": 2,
  "tool_spec_schema": 1,
  "tools": {}
}
```

`tools` намеренно пуст до подключения первого реального managed tool.

Один ToolSpec schema 1 описывает:

- `source`: только `git` или `builtin`;
- `runtime`;
- `update_policy`: `latest`, `pinned-tested` или `bundled-with-setup`;
- `entrypoints`;
- `health_contract` как безопасные argv-проверки;
- `platforms`;
- для `git`: `repo`, `ref`, `project_directory`.

Для `pinned-tested` обязательна явная `ref`. Для `git` обязательны repository и project directory. Один публичный entrypoint не может принадлежать двум ToolSpec одновременно.

`package` и `release` намеренно **не объявлены поддерживаемыми source type в schema 1**: их нужно добавлять вместе с точным deploy/update/checksum contract, а не заранее создавать ложное обещание поддержки.

Registry валидируется при каждом запуске setup до mutations. Ошибка schema/spec является conflict и останавливает reconciliation.

## Bootstrap Python runtime

Платформенные wrappers используют отдельный bootstrap Python `venv`, чтобы текущие Python-зависимости setup и helper runtime не устанавливались в системный Python:

- Linux: `${XDG_DATA_HOME:-$HOME/.local/share}/opencode_setup/runtime/python`;
- Windows: `%LOCALAPPDATA%\opencode_setup\runtime\python`;
- путь можно переопределить через `OPENCODE_SETUP_RUNTIME_DIR` (Windows также принимает параметр `-RuntimeDir`).

Обычный apply создаёт отсутствующий runtime во временном соседнем каталоге, проверяет `python` и `pip`, записывает точный ownership-marker и только затем перемещает готовый runtime на целевой путь. `--check`/`-Check` не создаёт runtime: до первого apply он использует базовый Python только для read-only диагностики.

Существующий каталог принимается только при доказанном ownership-marker и рабочем runtime Python. Неизвестный или неполный каталог не усыновляется и не заменяется автоматически. Это сохраняет инвариант `unknown != ours`.

Bootstrap venv — **промежуточный слой изоляции**, а не реализация `managed_tools`: текущие `ssh_relay` и `agent-safe` пока могут исполняться из source checkout, не имеют общего stable-entrypoint reconciliation и не записываются как ToolSpec runtime в manifest. Его задача на этом этапе — убрать зависимость от системного `pip` без преждевременного переписывания helper deployment.

## Что пока не реализовано

Текущий фундамент и bootstrap runtime **не** реализуют:

- generic получение/сборку tool runtime по ToolSpec;
- stable entrypoint reconciliation;
- изменение PATH;
- user/system tool deployment через общий reconciler;
- generic health execution по ToolSpec;
- запись фактического helper runtime в `managed_tools`;
- `bundle`, `tunnelctl`, `proxy-tools` как реальные ToolSpec;
- окончательное разделение installed runtime `ssh_relay`/`agent-safe` и их source checkout.

Следующий этап должен добавить generic reconciler поверх уже существующих ToolSpec + manifest v2, а затем подключить первый инструмент (`bundle`) с pinned-tested ref и изолированным runtime. После стабилизации общего механизма следует оценить миграцию существующих Python helper tools без дублирования уже работающей логики.
