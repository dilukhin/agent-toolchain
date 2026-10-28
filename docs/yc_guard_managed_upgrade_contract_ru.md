# YC guard: контракт штатного обновления действующего выпуска

Статус: **G2 security design / candidate for implementation**. Этот документ определяет инварианты и приёмку. Реализация и приёмка на машине выполняются отдельно. Не изменять semantics `opencode_permissions` в `agent-toolchain`.

## 1. Граница ответственности и цели

- `opencode_permissions` владеет canonical policy, builder, содержимым артефакта.
- `agent-toolchain` владеет только скачиванием **точного** исходного SHA, валидацией, управляемыми путями, выпуском, переключением, состоянием и восстановлением.
- `agent-safe` отвечает за рискованное исполнение на машине; команду обновления необходимо маршрутизировать через соответствующее разрешение и `safe`, а не через blanket shell allow.
- Выполнить только известный переход старого действующего YC guard к новому точному проверенному артефакту, без автоматического изменения облака и без обхода `yc.cmd`.
- Старый versioned release и legacy `%LOCALAPPDATA%\\LanFabric\\yc-guard` не удалять и не перезаписывать.
- Семантика `allow_mutations` (включая временное узкое исключение для старой ВМ) остаётся в canonical policy. Не добавлять автоматическую авторизацию новой ВМ, SG mutation или универсальные approval flags.

## 2. Явный интерфейс

- `toolchainctl yc-guard check`: строго read-only, показывает `active artifact_id`, `source_ref`, `effective_yc`, `ownership`, `integrity`, `upgrade_state`; не объявляет успех при частично завершённой транзакции, mismatch или unknown.
- `toolchainctl yc-guard apply`: установочный/идемпотентный режим, **никогда не повышает** уже действующий выпуск незаметно. Для известного старого active и новой pinned policy сообщает `YC_UPGRADE_REQUIRED` **до публикации нового выпуска либо изменения файлов/PATH**.
- `toolchainctl yc-guard upgrade`: единственная штатная операция для `old-active → new-active`; версии закреплены полными `source_ref` + `artifact_id`. Никаких произвольных `--source-ref`, `--artifact-id`, `--force` и принятия чужого state. Тестовый source-root допустим только как скрытый fixture-аргумент для изолированных испытаний.
- `toolchainctl yc-guard recover --to-old` и `toolchainctl yc-guard recover --to-new`: только для зарегистрированной незавершённой транзакции, после точного read-only разбора обоих известных вариантов; выбор должен быть явным. Успешный завершённый переход допускает отдельно разрешённый `rollback` к сохранённому предыдущему выпуску.
- `disable` НЕ считать rollback: он удаляет managed entrypoint и включает другую legacy-семантику через PATH.

## 3. Preflight — без изменений состояния

Проверить **все** нижеследующие условия до записи в управляемые пути:

1. Исходное состояние `phase=active`, ровно известная старая пара `source_ref/artifact_id`, точные owner/component/schema. Нельзя считать `state.json` самодостаточным доказательством прав.
2. Managed `yc.cmd` — обычный owned файл в точном managed bin; его SHA совпадает со state; effective `yc` разрешается именно в этот файл и managed bin не затенён. Не менять PATH.
3. Old release находится под owned `tools/yc-guard/sha256-...`; marker, deployment.json, runtime, manifest, file hashes, entrypoint script, downstream `yc.exe` и его SHA проверены. Проверить соответствие marker/state/deployment и известным pinned old constants. Не доверять произвольному path из state как разрешению на запись.
4. Отсутствуют чужие файлы/символьные ссылки в критических owned путях, незарегистрированная транзакция, незнакомая версия state, конфликтующая блокировка и неизвестный промежуточный режим.
5. Целевой релиз загружен только из точного нового SHA; canonical builder и независимый validator подтверждают фиксированный `artifact_id` и все пять payload bytes; не использовать developer checkout для production.
6. Межпроцессная блокировка для изменяющих операций. Если lock невозможно безопасно получить, отказ без удаления чужого/stale lock.

Любой конфликт: read-only diagnosis, без blind retry, destructive cleanup, переусыновления владения или подмены бинарника. Downloader ограничен прежними limits/timeouts. Ошибка подготовки не должна повредить старый active.

## 4. Транзакция обновления

1. **PREPARE**: создать новый **неизменяемый** versioned release в owned пути; целиком проверить marker, deployment, runtime. Старый shim/state не трогать.
2. **CHECKPOINT**: сохранить побайтовые snapshot старых `state` и `yc.cmd`, их SHA, точные old/new identities, ожидаемые пути и хеши в отдельном owned transaction area. Сохранять atomically, завершить запись до переключения; никаких секретов и чужих файлов. Основа для восстановления должна переживать завершение процесса.
3. **JOURNAL**: создать журнал `upgrade` с фазами `prepared`, `entry_switched`, `state_switched`, `verified`, а также точными ожидаемыми old/new hashes; журнал необходим для распознавания частично завершённой операции. Фиксировать изменения фаз согласованно с доступным фактическим read-back, не полагаться только на записанное название фазы.
4. **SWITCH**: атомарно заменить один owned `yc.cmd` на новый (тем же томом, через existing `_atomic_write`); новое содержимое ссылается только на новый immutable release. Затем атомарно заменить managed state на `phase=active` нового выпуска. **Обе записи не атомарны вместе**, поэтому частичный результат обязателен для распознавания. Прерывание процесса может оставить old/new mismatch; не объявлять success.
5. **VERIFY**: read-back двух файлов, pinned identity, release и downstream, текущего `PATH` в свежем окружении; без `yc` cloud mutation. Только после успешной проверки завершить транзакцию. Сохранить однократную точку возврата к старому выпуску.
6. Никакой очистки старого релиза, legacy guard, user PATH или непроверенных staged файлов. Cleanup только известных временных файлов с подтверждённым ownership и в отдельном разрешённом режиме; отказ при неожиданном содержимом.

## 5. Состояния и восстановление

| Наблюдаемое состояние | Решение |
|---|---|
| old state + old shim, журнал `prepared` | Старый guard остаётся действующим. Явное `recover --to-old` завершает отмену подготовленной транзакции; не переключать автоматически |
| old state + new shim | Известное частичное переключение. `check` сообщает conflict/pending, `recover` требует явного направления; проверить оба pinned выпуска до любых записей |
| new state + new shim | Если журнал есть, `check` сообщает pending verification. После точной проверки возможен явный `recover --to-new` (завершение) или `--to-old` (откат) |
| new state + old shim, иные неизвестные пары | Fail-closed и read-only diagnosis до выбора recovery; не выполнять слепую замену |
| изменённые hashes, чужой file owner, неизвестный source, PATH shadow | `modified/conflict`, никаких изменений |
| new active + полный checkpoint после успешного upgrade | Отдельный явный `rollback` восстанавливает exact old state/shim, проверяет результат и сохраняет новый release для диагностики |

`recover` и `rollback` являются отдельными state-changing действиями; вызывать только в разрешённой среде под `safe`. Не считать возврат облачных побочных эффектов возможным: rollback меняет только локальную guard policy. Техническое обещание — восстановление после **прерывания процесса** в испытанных точках; не заявлять доказанную устойчивость к физическому отключению питания Windows без отдельной проверки durability.

## 6. Negative regression / acceptance

Tests выполняются исключительно на synthetic fixtures во временных каталогах, без настоящего `yc.exe` и облачных операций.

- Успешное `old-active → new-active`, повторный `upgrade` → no-op; `check` подтверждает точный новый active и effective entrypoint.
- Во всех точках остановки (до journal, после journal, после shim, после state, перед/после verify) повторный `check` корректно отличает active/pending/conflict; явный `recover` либо восстанавливает old, либо завершает new, без loss/overwrite чужого файла.
- Проверить неиспользование cloud executor при установке, check, upgrade, recover, rollback.
- Rejection: неизвестный active artifact, state/marker mismatch, повреждённые payload/entrypoint/downstream, не-owned shim, symlink, PATH shadow, двойной/stale lock, неподтверждённый source SHA, повторная/чужая транзакция, невосстановимый snapshot.
- `apply` при старом active возвращает upgrade-required без локальной публикации/изменений. Предыдущее обычное повторное `apply` и `disable` не ломаются.
- Обе точные старые start/stop остаются прежними; новые read-only команды разрешены; дополнительные флаги `ASK_USER`; sensitive `DENY`.
- Windows/Linux targeted CI и полный CI `agent-toolchain` успешны; отдельная приёмка Windows/DIMA-HP выполняется только на G3–G4.
- После неизвестного результата mutation остановить цепочку, сохранить evidence и выполнить read-only diagnosis, не повторять автоматически.

## 7. Область реализации

Ожидаемые файлы: `setup_yc_transitional_guard.py`, `tests/test_yc_transitional_guard_deployment.py` (или отдельный focused test-module), `docs/yc_transitional_guard_deployment_ru.md`. `yc_transitional_entry.py` и `toolchainctl.py` менять лишь при доказанной необходимости; canonical policy / `opencode_permissions` не трогать.

**G2 не закрывается этим документом**: нужны реализация, security review точного diff, тесты и отдельный PR. Только затем bounded G3 deployment.

## 8. Security decision SEC-G2-04: runtime activation gate (2026-10-28)

**Подтверждённый дефект G2-R1:** в `pending` после atomic replacement `yc.cmd` новый `yc_transitional_entry.py` уже принимает вызов и допускает дополнительные `allow_read`, хотя `toolchainctl yc-guard check` выдаёт конфликт. Проверки только в setup и только в `yc.cmd` недостаточно: прямой вызов нового runtime не должен обходить запрет.

### Нормативное решение

1. Исполняемый модуль `agent-toolchain/yc_transitional_entry.py` для **нового выпуска** обязан проверять доверенное состояние активации **до передачи команде исполнителю**, независимо от способа запуска (через `yc.cmd` или прямой запуск модуля). Изменение этого файла в R2 разрешено; canonical `opencode_permissions` / classifiers не изменяются.
2. Для выпуска, полученного через `upgrade`, `ALLOW` может исполниться лишь при journal `phase=complete` и совпадении связанной с выпуском записи active state, точных `source_ref/artifact_id`, путей, checkpoint bytes/hashes и active `yc.cmd` с ожидаемым новым вариантом. Сам по себе `complete` либо caller/env flag недостаточен. `pending`, `rollback_pending`, `rolled_back`, отсутствие/повреждение журнала, любой drift или недоступное доказательство — **fail-closed**, без downstream-вызова.
3. Проверка и исполнение должны происходить под **той же межпроцессной OS-блокировкой**, что `upgrade/recover/rollback`. Runtime берёт её до проверки активации и удерживает до окончания downstream процесса. При занятой/неизвестной блокировке — отказ без обхода; никаких временных разблокировок между проверкой и запуском.
4. Runtime получает сведения об exact state/journal/entrypoint путях из проверенного setup-owned deployment context, а не из `argv`, пользовательского флага, произвольной среды либо PATH. Изменения deployment/marker проверяются на целостность и совместимость. Не создавать управляемые state-файлы во время read-only проверки активации.
5. Требуется учесть **чистую первую установку** нового выпуска: нельзя оставить прежний `apply` с неработоспособным новым runtime. **Принято:** для первой установки нового выпуска сохраняется штатный `apply`, но создаётся отдельная запись активации `mode=initial` с ожидаемыми байтами/хешами нового `state` и `yc.cmd` и `phase=complete`, записываемая только после завершения обеих проверенных публикаций. Отсутствие журнала, `pending`, неясная история установки или несовпадение ожидаемых байтов не должны давать runtime `ALLOW`. Не создавать вымышленную старую контрольную точку; recovery первой установки разрешает только явное безопасное завершение `to-new` после проверки exact new state/shim, без автоматического удаления чужого файла. Обновление существующего old-active всегда использует отдельный `mode=upgrade` и полноценный old/new checkpoint; переключать старый active через `mode=initial` запрещено.
6. Старый неизменяемый runtime уже не имеет этого механизма. Он может сохранять прежние точно разрешённые операции до/во время переключения; G2-R2 не должен заявлять блокировку **всех** старых выполняющихся команд. Требуется закрыть окно получения **новых** разрешений до финального завершения upgrade.
7. При `recover --to-old`/rollback новый runtime обязан отказывать. При `recover --to-new` новые разрешения могут появиться только после полного `complete` и повторной runtime-проверки. Ошибки проверки активации не должны отправлять вызов в legacy wrapper.
8. Границы испытаний: имитировать отказ после journal, после shim, после state, перед complete; прямой запуск runtime; concurrent invocation во время удержания upgrade lock; гонку проверки с rollback; corrupted/missing journal, checkpoint, shim, state, release; восстановление old/new. В каждом pending-сценарии `downstream_calls=0`, в complete с точной new identity только ожидаемая команда исполняется. Никаких реальных cloud-команд.

**Ограничение:** эти тесты не доказывают устойчивость к физическому отключению питания, злонамеренной замене всех файлов владельца или завершению уже запущенной старой команды. G2 остаётся открытым до патча R2, полного suite, CI и Web security-review.
