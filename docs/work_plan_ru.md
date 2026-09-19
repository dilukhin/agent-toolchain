# План работ agent-toolchain

Дата: 2026-09-19. План принят пользователем после инвентаризации issues.
Фактическое выполнение определяется по актуальным main, коду, tests/validators и CI.
Открытый PR не считается реализацией main. Перед каждым этапом перечитать его issue
и проверить относящиеся PR: параллельные диалоги могут изменить состояние.

## Очередь и текущая точка

| Этап | Issue | Результат | Состояние |
|---|---|---|---|
| 0 | #54, #53, #46, #28, #45, #29 | Закрыть дубликат; актуализировать остаток работы и зависимости | Выполнено: #54 закрыт как duplicate #53; описания остальных уточнены |
| 1 | [#58](https://github.com/dilukhin/agent-toolchain/issues/58) | Доставить правила Windows literal verifier и CLI preflight; проверить пример и обновление managed instructions | Выполнено: [PR #62](https://github.com/dilukhin/agent-toolchain/pull/62), merge 6b900b581a360ec5e8d0a9b2a3c55d9fd1be1737; Windows/Linux CI success |
| 2 | [#53](https://github.com/dilukhin/agent-toolchain/issues/53) | Проверить доступность опубликованного core/marker/entrypoint обычному пользователю | Выполнено: [PR #64](https://github.com/dilukhin/agent-toolchain/pull/64); Windows/Linux CI и реальный standard-user regression прошли |
| 3 | [#46](https://github.com/dilukhin/agent-toolchain/issues/46) | Завершить identity обычных команд, ранних ошибок и proxy-tools | Реализовано: stderr identity, wrapper version/JSON health, exact provenance и автономный runtime; Windows/Linux CI — обязательный gate слияния |
| 4 | [#45](https://github.com/dilukhin/agent-toolchain/issues/45) | Реестр доверенных рабочих каталогов и read-only provider | Следующий этап после приёмки #46; upstream consumer contract существует |
| 5 | [#28](https://github.com/dilukhin/agent-toolchain/issues/28) | Operational alerts/staleness и anomaly guard RouterAI | Частично: status/observability уже реализованы PR #31 |
| 6 | [#29](https://github.com/dilukhin/agent-toolchain/issues/29) | Общая конфигурация, явные профили, локальные настройки и безопасная миграция | Дизайн и реализация после #28 |
| Отдельно | [#61](https://github.com/dilukhin/agent-toolchain/issues/61) | Явный Linux opt-in CLI P0, status/metrics/disable поверх существующего reconciler | Добавлена параллельным диалогом 2026-09-19; реализацию CLI согласовать по времени с #46/#45 |
| 7 | [#60](https://github.com/dilukhin/agent-toolchain/issues/60) | Готовность ScopedKB, затем добровольное подключение через ToolSpec | Readiness можно проверять независимо; установка отложена до доказанного контракта |

Это рабочая последовательность, не утверждение, что каждая предыдущая функция
технически необходима каждой следующей. Она сокращает пересечение изменений в
bootstrap, CLI, конфигурации и миграциях.

## Этап 2: доступность core (#53)

Реализация и ограничения: [проверка доступности core](core_access_validation_ru.md).
Следующий этап после приёмки и merge — #46. Повышенный запуск bootstrap больше
не сообщает успех проверки обычного пользователя; неизвестные ACL не исправляются.

1. Прочитать [документ ACL-инцидента](incidents/2026-09-12-managed-core-protected-acl-windows_ru.md),
   bootstrap_core.py, wrappers и tests/test_bootstrap_core_path.py.
2. В изолированном Windows окружении получить воспроизводимый сценарий публикации
   с последующим чтением и запуском обычным пользователем. Elevated-user read
   не доказывает доступ non-elevated пользователя.
3. Добавить минимальную проверку после публикации: файлы, marker, generated
   entrypoint и фактический запущенный core. Успех установки не объявляется,
   если проверка не пройдена.
4. Сохранить unknown != ours: не исправлять неизвестные ACL, не удалять чужой core,
   не использовать цикл overwrite/reset/repair.
5. Доказать positive/negative сценарии, повторную установку без изменений и Linux.
   Отсутствующие platform/integration проверки явно фиксировать.

Восстановление ACL на DIMA-HP не является доказанным исправлением installer.
Непосредственная причина отказа доступа известна; происхождение protected ACL
не установлено. Ветка fix/core-post-install-access-check на момент инвентаризации
не содержала собственных commits сверх main.

## Этап 3: диагностическая identity (#46)

Реализация: [контракт diagnostic identity](diagnostic_identity_ru.md).
Сохранены `toolchainctl --version` и semver `0.1.0`; общий модуль добавляет stderr
identity обычных команд и ранних ошибок. Proxy предоставляет `--wrapper-version`
и `--health-json`, сохраняя child `--version` и stdout. Чистый checkout проверяется
по Git blobs до публикации; self-update сохраняет exact-archive caller contract.
Не доказанный SHA остаётся local/dev. Proxy получает immutable snapshot provenance,
полный source SHA остаётся в metadata/manifest. Runtime не читает developer checkout.

Приёмка: общий regression runner и public proxy fixtures на Windows/Linux,
существующие #53, managed-helper, MP-1/MP-2 и RouterAI gates на финальном PR head.
После merge/read-back следующий этап — #45; реальные устройства/облако не изменяются.

## Этап 4: доверенные каталоги (#45)

Использовать действующие upstream документы:
- [producer contract](https://github.com/dilukhin/opencode_permissions/blob/main/docs/trusted_workspace_producer_contract_ru.md);
- [consumer contract](https://github.com/dilukhin/opencode_permissions/blob/main/docs/trusted_workspace_fact_design_ru.md).

Реализовать add/list/remove, canonical registry, atomic write/read-back и read-only
provider. Повторный exact add с теми же scopes — no-op; дубли внутри registry —
conflict; другие scopes — explicit update. Изменившаяся object identity не
сохраняет доверие автоматически. Проверить Windows/Linux и corrupt state.

Первый trust-conditioned ALLOW — отдельный этап opencode_permissions после
приёмки producer, без скрытого расширения разрешений в этой задаче.

## Этап 5: RouterAI (#28)

Два последовательных PR:
1. Единое идемпотентное operational уведомление failure/recovery и независимое
   обнаружение длительного отсутствия успешного полного refresh.
2. Проверка аномалий цен/числа моделей/массовой потери цен и компактный review report.

Использовать существующий status channel; observed_at не считать heartbeat.
Пороги обосновать историческими данными. Разделить warning и blocking.
Сохранить last-known-good, отсутствие timestamp-only commits в main/generated data,
удаление недоступных цен и cache-read semantics. Ограничение watchdog внутри
GitHub Actions при глобальной недоступности Actions документировать явно.

## Этап 6: профили (#29)

Сначала документ решений о слоях product/profile/local override, precedence,
ownership и миграции. Затем реализация на актуальном main.

Старая feature/config-profiles-29 содержит commit
83bbf5f136635c146fe7eebfd550ba1a895f5b6f; при инвентаризации отставала на 105 commits.
Это справочный материал, не готовый к merge changeset. Не переносить старые pins,
устаревшие RouterAI paths/guards или прежнее ownership глобального AGENTS.

Приёмка: fresh generic, явный авторский/иной профиль, отключённый RouterAI/tools,
локальный proxy endpoint, миграция доказанно managed состояния, сохранность
пользовательских настроек, check read-only и повторный apply no-op.

## Этап 7: ScopedKB (#60)

Readiness — самостоятельный результат: полезная возможность, packaging/data,
Python version, доступный источник, provenance, health и ownership.
Согласовать с [scopedkb#15](https://github.com/dilukhin/scopedkb/issues/15).
doctor/status без работающей требуемой функции не доказывают production readiness.

Подключение только после готовности и явного выбора компонента. Использовать
существующий ToolSpec/python-venv механизм. Не создавать общую writable KB,
не мигрировать пользовательские знания скрыто и не включать телеметрию.
Результат «подключение отложено» допустим; ложный healthy runtime — нет.

## Новая задача MP-3 (#61)

[#61](https://github.com/dilukhin/agent-toolchain/issues/61) создана параллельным
диалогом после исходной инвентаризации. Она добавляет явный Linux opt-in интерфейс
P0: enable/status/metrics/disable поверх существующего pilot reconciler.
Workspace trust #45 в неё не входит. Обычный apply/update не активирует pilot;
реальное включение на пользовательской машине остаётся отдельной задачей.

Сначала короткий дизайн CLI, immutable bundle, canonical paths, effective layers,
version drift при disable и семантики агрегированных метрик; затем fixtures и
существующие MP-1/MP-2 gates для нового кода. Уже выполненный MP-2 с метриками
не повторять только из-за старого closure-документа. Доказательства upstream
и точная проверенная пара перечислены в issue и перед реализацией проверяются заново.

Дизайн можно вести отдельно сейчас; реализацию изменений toolchainctl выполнять
последовательно с #46/#45, согласовав порядок с ведущим MP-3 диалогом. #61 не
является разрешением автоматически включать P0, менять policy ALLOW или YC guard.

## Отдельная очередь и параллельность

- [PR #34](https://github.com/dilukhin/agent-toolchain/pull/34) — отдельно проверить
  данные каталога и состояние проверок. При инвентаризации ручной Windows/Linux
  run для 97769bbefaec29dfd1514570c2a6fa28d51d2f88 был success, PR-event проверки —
  action_required. Это не доказательство полной готовности к merge.
- #53 и #46 реализовать последовательно из-за общего bootstrap/identity слоя.
- #28 и #29 реализовать последовательно из-за RouterAI/config/migration.
- Readiness #60 можно вести независимо от основной очереди.
- Новые непересекающиеся проверки допустимы параллельно; незамерженную реализацию
  другой ветки не принимать за production contract.

## Завершение этапа

Implementation → targeted checks → итоговый diff → PR → review/CI → исправления
→ merge → GitHub read-back → обновление issue и этого плана.

Windows/Linux — first-class. Реальные устройства/облако не изменять без
относящейся к ним отдельной задачи. При недоступной проверке подготовить точное
ограниченное задание и не объявлять соответствующий сценарий подтверждённым.
