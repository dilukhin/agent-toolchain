# Transitional YC guard: installation/reconciliation gate

## Статус

Этот компонент — временный рабочий deployment для разблокирования LanFabric. Он не закрывает полный authorization-binding gate.

Semantic policy остаётся в `dilukhin/opencode_permissions`. `agent-toolchain` владеет только exact-source acquisition, artifact validation, installation, ownership/reconciliation и stable entrypoint.

Pinned source:

`dilukhin/opencode_permissions@7922d612f244882aae3d843a64393b1363b593d9`

## Модель установки

`toolchainctl yc-guard apply`:

1. не ищет `yc.exe` по диску;
2. читает только уже существующий `%LOCALAPPDATA%\LanFabric\yc-guard\state.json`;
3. требует ровно одну абсолютную ссылку на `yc.exe` в этом state;
4. проверяет существование и SHA-256 executable, но не запускает его;
5. скачивает exact pinned source `opencode_permissions`;
6. запускает canonical `build_yc_transitional_artifact.py`;
7. независимо валидирует content-addressed manifest и каждый bundle file;
8. публикует versioned runtime в `%LOCALAPPDATA%\agent-toolchain\tools\yc-guard\sha256-...`;
9. публикует owned `%LOCALAPPDATA%\agent-toolchain\bin\yc.cmd`;
10. делает effective PATH read-back.

Старый `%LOCALAPPDATA%\LanFabric\yc-guard` не меняется и остаётся fallback/backup.

Если managed bin отсутствует в текущем PATH или текущий `yc` находится в более раннем PATH entry, apply завершается conflict **до** публикации runtime/entrypoint.

## Runtime

Новый `yc.cmd` вызывает только versioned `yc_transitional_entry.py`. Runtime на каждом вызове:

- проверяет manifest и digest файлов canonical artifact;
- проверяет pinned downstream `yc.exe` по SHA-256;
- не выполняет PATH lookup downstream YC;
- строит exact `parsed-simple/v1` fact из argv;
- вызывает canonical `yc_transitional_adapter` / `classifier_yc`;
- передаёт downstream executor только решение `ALLOW`.

`ASK_USER` и `DENY` в transitional версии блокируются без исполнения. Это достаточно для текущего LanFabric deployment, которому нужны canonical read-only операции и exact start/stop `epd42hrnss08t2440g90`.

Exit codes:

- 77 — DENY;
- 78 — ASK_USER (approval transport в transitional версии не реализован);
- 79 — integrity/runtime fail-closed.

## Команды

Read-only:

`toolchainctl yc-guard check`

Install/reconcile:

`toolchainctl yc-guard apply`

Safe fallback:

`toolchainctl yc-guard disable`

Disable удаляет только доказанно owned `yc.cmd`; versioned runtime остаётся cached. Legacy LanFabric guard не удаляется.

## Acceptance перед LanFabric

После merge и обновления установленного agent-toolchain:

1. `toolchainctl yc-guard check` подтверждает legacy state и downstream reference;
2. `toolchainctl yc-guard apply` завершается `effective_readback=PASS`;
3. новый shell: `Get-Command yc` указывает на `agent-toolchain\bin\yc.cmd`;
4. read-only smoke `yc compute instance list` успешен;
5. wrong ID test не выполняет cloud mutation и возвращает ASK_USER;
6. credential/guard-control test не выполняет downstream и возвращает DENY;
7. только после этих пунктов допускается отдельный exact start/stop smoke для `epd42hrnss08t2440g90`.

Реальные cloud mutations не входят в CI и не выполняются при installation/reconciliation.
