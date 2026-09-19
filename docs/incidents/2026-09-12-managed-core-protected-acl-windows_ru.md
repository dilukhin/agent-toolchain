# Инцидент: protected ACL у managed core на Windows

Статус: operationally resolved, recurrence cause not established  
Дата инцидента: 2026-09-12  
Host: DIMA-HP  
Компонент: installed managed core `agent-toolchain`  
Связанный контекст: transitional YC guard deployment после merge PR #52

## Краткое описание

После установки transitional YC guard команда

```text
toolchainctl yc-guard check
```

не могла запуститься. Launcher `toolchainctl.cmd` корректно разрешался из ожидаемого каталога и Python работал, но Python не мог открыть:

```text
%LOCALAPPDATA%\agent-toolchain\core\toolchainctl.py
```

с `[Errno 13] Permission denied`.

Проблема возникала до запуска YC guard и не была связана с YC credentials, cloud API, Python runtime или PATH resolution.

## Подтверждённый root cause

Elevated read-only inspection установил:

- `core` принадлежал `BUILTIN\Administrators`;
- ACL каталога был protected: `AreAccessRulesProtected: True`;
- inheritance от родительского `%LOCALAPPDATA%\agent-toolchain` был отключён;
- ACL разрешал доступ Owner Rights, SYSTEM и Administrators;
- родительский каталог содержал явный `DIMA-HP\Dima:(F)`, но эта ACE не наследовалась в `core`;
- в обычном non-elevated UAC token пользователь `Dima` не мог использовать Administrators ACE.

Это и было непосредственной причиной отказа доступа.

## Почему объект не был сразу изменён

До elevated inspection было неизвестно, является ли каталог:

- intact managed core;
- damaged managed core;
- foreign/unowned directory.

Поэтому применялся инвариант `unknown != ours`: не выполнялись удаление, overwrite, bootstrap/reconciliation или ручное изменение ACL без доказательства ownership.

## Разрешённое восстановление

После явного разрешения пользователя inheritance был включён на `core` через elevated `icacls` как контролируемая agent-safe transaction.

При восстановлении:

- существующие explicit ACE были сохранены;
- от родителя были унаследованы `DIMA-HP\Dima:(F)` и `DIMA-HP\CodexSandboxUsers:(RX)`;
- итоговый `AreAccessRulesProtected` стал `False` для `core` и `toolchainctl.py`;
- операция имела expected-state verification и SDDL-based rollback path.

Transaction id:

```text
20260913-163748-12bd6aa5
```

## Post-remediation verification

После восстановления:

- `Get-Acl core` проходит;
- `Get-Acl toolchainctl.py` проходит;
- ownership marker читается;
- marker объявляет `owner: agent-toolchain`, schema `1`;
- все 22 marker payload entries существуют;
- все 22 payload entries совпадают с записанными SHA-256;
- missing: 0;
- mismatched: 0.

Итоговая классификация:

```text
ordinary, intact managed core
```

Каталог не оказался damaged или foreign.

## Признаки возможного повторения

При сходном инциденте сначала проверить read-only:

1. `where.exe toolchainctl`;
2. доступность launcher и Python;
3. `Get-Item %LOCALAPPDATA%\agent-toolchain\core`;
4. `Get-Acl` / `icacls` для `core`;
5. `AreAccessRulesProtected`;
6. owner каталога;
7. доступность ownership marker.

Характерная комбинация этого инцидента:

```text
parent accessible
+ core exists
+ core ACL unreadable to normal user
+ protected ACL
+ inheritance disabled
+ normal-user launcher fails before toolchainctl starts
```

## Safety / recovery rule

Повторение этих симптомов само по себе не разрешает автоматический ACL repair.

До mutation необходимо определить ownership/type каталога. Если ownership нельзя доказать, сохраняется правило:

```text
unknown != ours
```

Не использовать удаление, overwrite или blind bootstrap как способ восстановления.

## Что пока НЕ доказано

Не установлено, что актуальный installer/reconciler систематически создаёт такой protected ACL. Поэтому этот incident сам по себе не является основанием для изменения installation algorithm.

Продолжение ведётся в [issue #53](https://github.com/dilukhin/agent-toolchain/issues/53).

Добавлен изолированный Windows regression старого способа публикации:
`tempfile.mkdtemp()` с режимом `0700` на Python 3.13, затем rename в `core`.
Проверка сопоставляет чтение администратором с запуском отдельным обычным
пользователем. Новый staging наследует ACL родителя; существующие ACL не меняются.
[Контракт, проверки и ограничения](../core_access_validation_ru.md).

Даже успешное воспроизведение этого механизма не устанавливает происхождение
ACL на DIMA-HP: для исторической установки нет точного Python/build evidence.

## Historical evidence

Первичный pre-remediation verification:

`YC_GUARD_INSTALLATION_VERIFICATION_2026-09-12.md`

Root-cause и remediation report:

`AGENT_TOOLCHAIN_ACL_ROOT_CAUSE_2026-09-12.md`

В локальном отчёте также зафиксированы вспомогательные read/repair/verify/rollback scripts и expected-state JSON. Они являются incident evidence, а не частью production runtime.
