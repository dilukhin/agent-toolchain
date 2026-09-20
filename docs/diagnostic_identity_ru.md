# Диагностическая identity core и proxy-tools

Контракт issue [#46](https://github.com/dilukhin/agent-toolchain/issues/46).
Semantic version core остаётся `0.1.0`; единственный источник — `core_identity.py`.

## Каналы и команды

`toolchainctl --version` печатает в stdout одну строку:
`toolchainctl 0.1.0.<8hex>`. Это первые восемь символов полного `source_ref`
из marker запущенного установленного core. Git и сеть при чтении версии не нужны.

Обычные `check`, `apply`, `updates`, `update` печатают ту же identity один раз
в stderr до разбора аргументов и работы со state. Поэтому parser/state/update
ошибки также содержат версию. Чистые `--help`/`-h` не получают баннер.
Внутренний `--bootstrap-access-check` сохраняет отдельный JSON-протокол без баннера.
Bootstrap пишет в stderr identity исполняемого bootstrap-кода до preflight;
запуск из source без marker честно показывает `dev`.

Для каждой из `opencode-proxied` и `codex-proxied`:

| Вызов | Результат |
|---|---|
| `--wrapper-version` | Собственная строка `<command>-proxied 0.1.0.<8hex>` в stdout |
| `--health-json` | JSON в stdout: `tool`, `identity`, `scope=wrapper-runtime`, `network_checked=false` |
| `--health` | Прежний текстовый health, совместимый с ToolSpec |
| `--version` и обычные аргументы | Passthrough дочернему CLI; одна identity wrapper в stderr |

Собственные флаги распознаются только как единственный аргумент. Они не запускают
child и не проверяют SOCKS/сеть. JSON health описывает загрузившийся wrapper runtime,
а не готовность child, сети или провайдера. Ранние `no executable found` и SOCKS
preflight errors получают identity до ошибки. Wrapper не добавляет данные в
stdout child; exit codes и transport сохраняются.

«Один раз» относится к одному процессу: `update --apply` может последовательно
показать старый core, bootstrap и новый core, каждый со своей identity.

## Provenance при публикации

Полный 40-hex `source_ref` остаётся в `.agent-toolchain-managed-core.json`.

- Обычный bootstrap из чистого Git checkout проверяет HEAD, корень репозитория,
  отсутствие tracked/untracked изменений и соответствие каждого публикуемого файла
  blob-объекту HEAD. Для текста допускается стандартное CRLF/LF преобразование.
  Скрытый через `assume-unchanged` изменённый payload не получает SHA. Повторно
  проверяются fingerprint и HEAD; локальный Git выполняется только для чтения.
- `toolchainctl update` сохраняет существующий контракт: разрешает upstream main
  в точный SHA, скачивает архив этого SHA и передаёт его bootstrap через внутреннюю
  переменную `AGENT_TOOLCHAIN_UPDATE_REF`. Для архива без `.git` bootstrap доверяет
  этому caller contract. Это не пользовательский способ аттестации произвольного
  архива: ручная подстановка переменной не доказывает происхождение его содержимого.
  Неправильный формат SHA или несовпадение с доступным checkout блокируют публикацию.
- Dirty checkout, копия без `.git` и без exact-archive caller, либо недоступный Git
  не получают недоказанный SHA. Установленный core имеет `local.<fingerprint8>`.
  Отсутствующий marker означает `dev`, повреждённый/чужой/нечитаемый — `unknown`.

Чистый локальный Git доказывает соответствие payload конкретному commit, но сам
по себе не доказывает принадлежность commit upstream main. Диагностические
метаданные не заменяют ownership/integrity validators и не являются подписью.

No-op публикации требует совпадения fingerprint **и** source_ref. Изменение SHA
при тех же байтах payload публикует новые метаданные штатным механизмом с backup;
повтор той же пары — no-op. Неизвестный core или ACL автоматически не исправляются.

## Независимый runtime proxy-tools

При публикации builtin release в него копируются `core_identity.py` и snapshot
`core_identity.json` из устанавливающего core. Snapshot входит в существующий
payload hash. Полный ref также записывается в `core_identity` managed-tool marker
и manifest. Внешнее поле ToolSpec `source_ref` для builtin сохраняет прежний смысл.

Новый source_ref создаёт отдельный immutable release даже при неизменном коде
proxy. Уже опубликованный release не редактируется; обновление core само по себе
не меняет identity старого wrapper. Следующий apply публикует целевой release и
переключает доказанно managed launchers. Proxy читает только собственный snapshot:
удаление исходного checkout или перемещение core не меняет его identity.

## Проверки

`tests/test_diagnostic_identity.py` проверяет форматы, обычные команды и ранние
ошибки, независимость установленного core/proxy, полные refs marker/manifest,
чистый/dirty/скопированный source и повторную публикацию provenance.
`tests/test_proxy_public_integration.py` запускает public entrypoints с fake child:
проверяет child `--version`, чистый stdout и SOCKS failure. Все fixture-пути
временные; реальные устройства, облако и пользовательские ACL не изменяются.

Общий regression runner включает эти тесты на Windows/Linux. Обязательные gates
сохраняются: managed-helper exact-ref integration, Windows standard-user core access,
OpenCode/BMAD, MP-1/MP-2 и RouterAI generated-state/ownership checks.
