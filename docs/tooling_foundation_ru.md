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

Один ToolSpec описывает:

- `source`: `git`, `builtin`, `package` или `release`;
- `runtime`;
- `update_policy`: `latest`, `pinned-tested` или `bundled-with-setup`;
- `entrypoints`;
- `health_contract` как безопасные argv-проверки;
- `platforms`;
- для соответствующих source: `repo`, `ref`, `project_directory`.

Для `pinned-tested` обязательна явная `ref`. Для `git` обязательны repository и project directory. Один публичный entrypoint не может принадлежать двум ToolSpec одновременно.

Registry валидируется при каждом запуске setup до mutations. Ошибка schema/spec является conflict и останавливает reconciliation.

## Что пока не реализовано

Этот changeset **не** реализует:

- получение/сборку tool runtime;
- stable entrypoint reconciliation;
- изменение PATH;
- user/system tool deployment;
- health execution;
- запись фактического runtime в `managed_tools`;
- `bundle`, `tunnelctl`, `proxy-tools` как реальные ToolSpec.

Следующий этап должен добавить generic reconciler поверх уже существующих ToolSpec + manifest v2, а затем подключить первый инструмент (`bundle`) с pinned-tested ref и изолированным runtime.
