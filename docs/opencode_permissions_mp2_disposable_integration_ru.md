# OpenCode Permissions MP-2 — disposable exact OpenCode integration

Статус: **IMPLEMENTATION / DISPOSABLE ONLY / NO USER DEPLOYMENT**.

MP-2 проверяет production-shaped P0 целиком на официальном Linux binary текущего `current_target`, выбранного через `dilukhin/opencode_permissions/tests/compatibility/registry.json`.

## Границы

- только временные HOME/config/data/state/workspace;
- применяется фактический MP-1 reconciler из `agent-toolchain`;
- policy/classifier artifacts берутся из актуального checkout `opencode_permissions` и проходят content-bound validation;
- официальный release asset и его SHA-256 берутся из exact compatibility profile;
- production P0 bridge загружается через managed global loader;
- обычный `toolchainctl apply` не вызывается;
- реальная пользовательская OpenCode environment не меняется;
- auditor, workspace trust и state-changing classifier не включаются.

## Acceptance scenarios

Обязательны:

1. `native_allow` — `pwd` завершается без pending permission;
2. `native_deny` — hard DENY `sudo *` не достигает даже безопасного fake executable в temp PATH;
3. `classifier_allow` — exact `/usr/bin/grep <pattern> <existing workspace file>` завершается через production P0 bridge;
4. `residual_ask` — unsupported read-only `/usr/bin/wc -l fixture.txt` остаётся pending `bash` permission и не исполняется автоматически;
5. `classifier_failure` — после загрузки verified bundle disposable adapter удаляется; следующий otherwise-ALLOW grep остаётся pending ASK и не исполняется;
6. `no_auditor` — pilot manifest, classifier profile и единственный managed loader подтверждают отсутствие auditor.

Pending ASK наблюдается через exact OpenCode HTTP `GET /permission`; пользовательский ответ в тесте не посылается.

## Stop conditions

MP-2 не считается PASS при любом из следующих результатов:

- current target не `RUNTIME_REVALIDATED/DEPLOYABLE` на Linux;
- release digest/version не совпадает с profile;
- current target не имеет ровно одного подходящего P0 pilot artifact;
- native DENY достигает fake executable;
- classifier ALLOW требует project-local authorization plugin или developer checkout;
- residual ASK/classifier failure превращаются в execution;
- обнаруживается auditor/workspace trust/state-changing classifier.

До explicit MP-2 PASS пользовательский pilot остаётся запрещён.
