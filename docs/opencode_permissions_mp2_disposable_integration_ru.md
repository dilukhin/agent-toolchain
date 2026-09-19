# OpenCode Permissions MP-2 — disposable exact OpenCode integration

Статус: **CLOSED / PASS / DISPOSABLE ONLY / NO USER DEPLOYMENT**.

MP-2 проверяет production-shaped P0 целиком на официальном Linux binary текущего `current_target`, выбранного через `dilukhin/opencode_permissions/tests/compatibility/registry.json`.

## Границы

- только временные HOME/config/data/state/workspace;
- применяется фактический MP-1 reconciler из `agent-toolchain`;
- policy/classifier artifacts берутся из актуального checkout `opencode_permissions` и проходят content-bound validation;
- current pilot artifact выбирается через canonical `tools/build_p0_pilot_artifact.py -> build_plan()`, а не по количеству каталогов в `dist/pilot`;
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

## MP-2 finding и MP-0 correction

Первый disposable run на исходном MP-0 artifact выявил реальный integration defect: production bridge вызывал `client.global.health()`, которого нет в v1 SDK exact OpenCode 1.18.29. Native ASK работал, но classifier fail-closed никогда не переходил в ALLOW.

Semantic owner `dilukhin/opencode_permissions` исправил runtime version binding отдельным PR #30. Correction merged в `main` как:

```text
77408e4fc1746cb37e64ab39815f3ff5b6c80784
```

Новый current pilot artifact:

```text
sha256:ce1ae9aedcb65e2e62c4ee38f21d0d535b58338816bd273ad090bc76cc64a9d2
```

Native artifact не изменился:

```text
sha256:b38090e07008fb174607aa2a924cfef1dd26d03bdb339a379b1a770397a8ad84
```

Старый pilot `sha256:fe0587a7...` сохранён immutable как historical evidence и не выбирается current source plan.

## Runtime evidence

Финальный production proof использует:

```text
OpenCode: 1.18.29
compatibility profile: opencode-1.18.29-gate-b
asset: opencode-linux-x64.tar.gz
asset sha256: ea800b7ff56226b70952126c9fc1e2517ca4c4b5682fd9d3f9e87449697a1194
pilot: sha256:ce1ae9aedcb65e2e62c4ee38f21d0d535b58338816bd273ad090bc76cc64a9d2
native: sha256:b38090e07008fb174607aa2a924cfef1dd26d03bdb339a379b1a770397a8ad84
```

Результаты runtime proof:

```text
native_allow        PASS  completed / pending=false
native_deny         PASS  error     / pending=false
classifier_allow    PASS  completed / pending=false
residual_ask        PASS  running   / pending=true
classifier_failure  PASS  running   / pending=true
no_auditor          PASS  false/false
```

Таким образом одновременно подтверждены terminal native ALLOW, terminal hard DENY, реальный production classifier ALLOW, нормальный residual ASK, fail-closed classifier failure и отсутствие auditor.

## Stop conditions

MP-2 не считается PASS при любом из следующих результатов:

- current target не `RUNTIME_REVALIDATED/DEPLOYABLE` на Linux;
- release digest/version не совпадает с profile;
- canonical `build_plan()` не разрешает exact current P0 artifact или committed artifact не совпадает с plan;
- native DENY достигает fake executable;
- classifier ALLOW требует project-local authorization plugin или developer checkout;
- residual ASK/classifier failure превращаются в execution;
- обнаруживается auditor/workspace trust/state-changing classifier.

## Gate closure

MP-2 закрыт после выполнения всех acceptance conditions:

1. финальный PR head `329463339686ced0c846448e0fcdc57d666d1caa`;
2. PR-specific `Validate MP-2 disposable exact OpenCode` #4 — PASS;
3. PR-specific `Validate MP-1 OpenCode Permissions pilot` #25 — PASS;
4. PR-specific `Validate RouterAI generated ownership` #63 — PASS;
5. PR-specific `Validate agent-toolchain` #511 — Linux PASS, Windows PASS;
6. PR #49 squash-merged в `main` с exact-head guard как `5f8c3b58e2b3a02877bd207872fdd4fc198859bb`;
7. targeted post-merge read-back подтвердил:
   - `main=5f8c3b58e2b3a02877bd207872fdd4fc198859bb`;
   - harness blob `4ccdedeef2204de033e09c4be09fc46d72499db8`;
   - MP-2 workflow blob `4096e6cce28a7cef986050ede58c3aac317b4c87`;
8. post-merge `Validate MP-2 disposable exact OpenCode` #5 — PASS; run checkout-нул `agent-toolchain=5f8c3b58e2b3a02877bd207872fdd4fc198859bb` и `opencode_permissions=77408e4fc1746cb37e64ab39815f3ff5b6c80784`, затем повторно подтвердил все шесть runtime scenarios;
9. post-merge `Validate agent-toolchain` #512 — Linux PASS, Windows PASS.

Результат: **MP-2 CLOSED / PASS**.

MP-2 не разрешает автоматическое изменение реальной пользовательской среды. MP-3 остаётся отдельным первым user opt-in этапом. Auditor, workspace trust и state-changing classifier остаются вне scope текущего этапа.
