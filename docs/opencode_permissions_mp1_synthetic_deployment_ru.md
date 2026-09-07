# OpenCode Permissions MP-1 — synthetic managed deployment

Статус: **CLOSED / PASS / SYNTHETIC ONLY / NO USER DEPLOYMENT**.

Этот slice реализует MP-1 из `dilukhin/opencode_permissions/docs/minimal_managed_pilot_design_ru.md`.

## Scope

MP-1 проверяет только managed deployment semantics в изолированных `config/data/state` каталогах:

- validation exact P0 pilot artifact;
- validation exact native permission artifact;
- exact installed OpenCode version/platform binding;
- immutable content-addressed runtime cache;
- global local plugin loader в `<config>/plugins/opencode-permissions-p0.js`;
- exact native `permission` subtree installation;
- effective read-back;
- repeat apply no-op;
- owner-aware disable/rollback;
- unknown/modified plugin conflict;
- config drift conflict;
- artifact tamper fail closed;
- unsafe recovery permission fail closed до первой mutation;
- resume из `prepared` после прерванной config write;
- fail-closed JSONC boundary без потери пользовательского файла.

Обычный `toolchainctl apply` этот модуль **не вызывает**. MP-1 не меняет реальную пользовательскую OpenCode environment.

## Ownership boundary

`opencode_permissions` владеет:

- semantic permission policy;
- classifier/profile semantics;
- production bridge;
- content-bound artifact manifests and bytes.

`agent-toolchain` владеет только:

- placement;
- immutable runtime materialization;
- ownership/recovery state;
- safe activation/deactivation ordering;
- effective read-back.

MP-1 не переписывает semantic contents artifact.

## Activation ordering

До первой mutation выполняется artifact/version/ownership preflight.

После preflight:

```text
verified artifact source
  -> immutable runtime cache
  -> recovery state phase=prepared
  -> exact native permission subtree
  -> global loader
  -> state phase=active
  -> effective read-back
```

Loader публикуется последним, поэтому classifier не становится активным раньше native policy.

## Disable / rollback ordering

Перед mutation проверяются config и loader fingerprints.

После полного preflight:

```text
remove exact owned loader
  -> restore previous permission subtree
  -> remove pilot state
```

Content-addressed runtime cache может остаться: он не активен без loader.

Unknown/modified loader или config drift блокируют rollback до mutation; blind overwrite/delete не выполняется.

## Recovery state

`<state>/opencode-permissions-pilot.json` содержит только deployment metadata и прежнее значение `permission`.

Он не сохраняет весь OpenCode config и не копирует credentials/secrets. Перед первой записью прежний `permission` валидируется как OpenCode action tree с действиями только `allow`, `ask`, `deny`; произвольный JSON отклоняется с `RECOVERY_PERMISSION_INVALID`.

Фазы:

- `prepared`;
- `active`.

`prepared` допускает безопасное продолжение после остановки между state publication, native config write и loader publication. Неожиданное исключение публичного facade не выходит как сырой runtime exception: оно преобразуется в контролируемый `PilotDeploymentError`, сохраняя исходную причину для диагностики.

## JSONC boundary MP-1

Изолированный MP-1 component изменяет только plain JSON representation `opencode.jsonc`.

Если файл содержит JSONC comments/trailing commas и не разбирается стандартным JSON parser, MP-1 fail closed с `CONFIG_NOT_SAFE_FOR_PILOT_MERGE`.

Полноценное объединение с owner-aware JSONC reconciler относится к wiring перед MP-3 и не требуется для synthetic gate.

## Exact integration evidence

Отдельный Linux test получает exact artifact bytes по immutable source commit:

```text
dilukhin/opencode_permissions
commit: eb808259607490aab2971c557501f7222bd46646
OpenCode: 1.18.29
pilot:
  sha256:fe0587a7c2dea7756bc1e697aa17a79f0c52be45bd55ac145efd7b473badce42
native:
  sha256:b38090e07008fb174607aa2a924cfef1dd26d03bdb339a379b1a770397a8ad84
```

Test выполняет только временные filesystem mutations.

## Gate closure

MP-1 закрыт после выполнения всех acceptance conditions:

1. финальный PR head `6b295c6ee0789a95083d85a7646826b2fd4b35d7`;
2. `Validate MP-1 OpenCode Permissions pilot` #14 — Ubuntu synthetic PASS, Windows synthetic PASS, Linux exact-artifact PASS;
3. `Validate agent-toolchain` #500 — Linux PASS, Windows PASS;
4. `Validate RouterAI generated ownership` #61 — PASS;
5. PR #47 squash-merged в `main` как `057d8590c2c431571c7d2a659506748dddc564f7`;
6. targeted read-back из `main` подтвердил public facade blob `e97be02b333a130f0086b79d235f46d48929525f` и transaction core blob `3e712617eabb767ca1fb9873c53140e1532fbaf8`;
7. post-merge `Validate agent-toolchain` #501 — PASS;
8. post-merge `Validate MP-1 OpenCode Permissions pilot` #15 — PASS;
9. перед закрытием gate upstream latest OpenCode оставался `v1.18.29`.

Результат: **MP-1 CLOSED / PASS**.

Даже после MP-1 PASS реальный пользовательский pilot ещё запрещён: сначала требуется MP-2 disposable exact OpenCode integration. Auditor, workspace trust и state-changing classifier остаются вне scope текущего этапа.
