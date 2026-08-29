# Обновление каталога RouterAI и `agent-toolchain`

## Назначение

Цены и другие объективные сведения RouterAI обновляются отдельно от ручной политики выбора моделей. Краткий операционный документ отвечает на вопросы «что менять», «как запустить» и «что означает статус». Архитектурные причины, решённые проблемы и переносимый шаблон для других внешних данных описаны в `docs/routerai_refresh_status_design_ru.md`.

Ручной источник истины — `templates/routerai_model_policy.json`. Он определяет состав управляемых моделей, человекочитаемые имена, роли, описания и прежние управляемые имена.

Объективный снимок внешнего источника — `templates/routerai_catalog.generated.json`.

Производные области:

- `config_data.json -> models`;
- `templates/opencode.jsonc -> provider.routerai.models`.

## Что можно редактировать вручную

| Область | Правило |
|---|---|
| `templates/routerai_model_policy.json` | Можно. Это ручной source of truth. |
| `templates/routerai_catalog.generated.json` | Нельзя. Только полный RouterAI refresh. |
| `config_data.json -> models` | Нельзя вручную. Пересобирается из policy + сохранённого snapshot. |
| `templates/opencode.jsonc -> provider.routerai.models` | Нельзя вручную. Пересобирается из policy + snapshot. |
| независимые разделы `config_data.json` | Можно по обычным правилам проекта. |

В JSON, где это безопасно, генератор добавляет `_managed_notice`. Он предупреждает о владении, указывает правильный ручной источник, команды и ссылку на подробное архитектурное описание.

## Если нужно изменить ручную политику моделей

После изменения `templates/routerai_model_policy.json` не редактируйте производные области руками. Выполните офлайновую пересборку:

```text
python3 scripts/update_routerai_catalog.py --sync-generated
```

Она:

1. не обращается к RouterAI;
2. использует уже сохранённый `templates/routerai_catalog.generated.json`;
3. пересобирает только `config_data.json -> models` и `templates/opencode.jsonc -> provider.routerai.models`;
4. не изменяет внешний snapshot и время его наблюдения.

Обычный PR с изменением policy допускается CI только если его производные изменения точно воспроизводятся этим режимом. Несинхронизированная policy или произвольная ручная правка generated-area делает CI красным.

## Полное автоматическое обновление внешних данных

`.github/workflows/routerai_catalog.yml` запускается ежедневно и вручную через `workflow_dispatch`.

```text
актуальный main
→ синхронизация automation/routerai-catalog
→ RouterAI GET /api/v1/models
→ нормализация каталога
→ пересчёт цен
→ генерация snapshot/config/template
→ offline verification + regressions
→ automation/routerai-catalog
→ PR
→ полный Windows/Linux validate.yml
→ automation/routerai-status
```

Новые модели RouterAI попадают в snapshot, но не добавляются в ручной управляемый список автоматически. Если выбранная модель исчезла или цена неполна, старая цена не сохраняется: модель остаётся, но получает `цена недоступна`.

## Штатный ручной запуск полного обновления

```text
gh workflow run routerai_catalog.yml --repo dilukhin/agent-toolchain --ref main
```

Это каноническая команда разработчика/сопровождающего. Она запускает тот же полный GitHub Actions workflow, что и расписание. Она отличается от `--sync-generated`: полный refresh обращается к RouterAI и может обновить внешний snapshot/PR/status.

## Локальные режимы генератора

Проверить живой RouterAI без записи:

```text
python3 scripts/update_routerai_catalog.py --check
```

Обновить checkout из живого RouterAI:

```text
python3 scripts/update_routerai_catalog.py --write
```

Офлайново пересобрать только производные области после изменения policy:

```text
python3 scripts/update_routerai_catalog.py --sync-generated
```

Офлайново доказать внутреннюю согласованность:

```text
python3 scripts/update_routerai_catalog.py --verify-generated
```

Для тестов полного генератора без сети можно использовать сохранённый ответ API:

```text
python3 scripts/update_routerai_catalog.py --write --input path/to/models.json
```

## CI-защита владения

`.github/workflows/routerai_owned_diff.yml` проверяет реальный PR-diff семантически.

Обычная ветка:

- не может менять `templates/routerai_catalog.generated.json`;
- может менять policy;
- при изменении policy может включить изменения generated config/template только если они в точности воспроизводятся `--sync-generated`;
- произвольная правка цены, имени, описания или generated notice отклоняется.

`automation/routerai-catalog` является отдельной служебной веткой полного внешнего refresh и имеет соответствующее исключение.

## Канал состояния

Машинно-читаемый статус хранится отдельно:

```text
branch: automation/routerai-status
file:   routerai-refresh-status.json
```

Ветка полностью принадлежит автоматизации. Сам status JSON содержит `_managed_notice` со ссылкой на `docs/routerai_refresh_status_design_ru.md` и командой полного ручного запуска.

Статус различает:

- опубликованный в `main` snapshot;
- последнюю успешную проверку RouterAI даже без изменения цен;
- последнюю попытку независимо от результата;
- кандидата, ожидающего публикации, и его validation.

Новая ошибка не стирает предыдущий успешный результат.

## Что видит пользователь

Управляемые запускаторы OpenCode/Codex best-effort читают локальный кэш статуса и перед запуском показывают русскую сводку. Status channel не является обязательной runtime-зависимостью.

Нормальное состояние:

```text
RouterAI: цены актуальны, каталог проверен 6 ч. назад.
```

После ошибки:

```text
ВНИМАНИЕ: последняя попытка обновить цены RouterAI завершилась ошибкой.
Последняя успешная проверка была 1 дн. назад.
Используются последние опубликованные цены; запуск продолжается.
```

Если в `main` уже опубликован более новый каталог, чем установлен локально:

```text
В main уже опубликованы более новые цены.
Обновить установленный toolchain: toolchainctl update --apply
```

Недоступность GitHub/status означает «новый статус сейчас неизвестен», а не автоматически «цены устарели».

## Получение опубликованных данных пользователем

```text
toolchainctl update --apply
```

Рабочая копия репозитория пользователю для этого не нужна.

## Диагностика ошибок

В status/PR публикуется только санитизированная краткая диагностика: этап, код, безопасное описание и рекомендация. Полный stderr/traceback остаётся в GitHub Actions logs, чтобы не переносить в долговечный статус секреты, authorization headers, приватные пути или большой непроверенный вывод.

Подробные цели, failure model, причины отделения status branch и инструкция по воспроизведению механизма для другого внешнего источника: `docs/routerai_refresh_status_design_ru.md`.
