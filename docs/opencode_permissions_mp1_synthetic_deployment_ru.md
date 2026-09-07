# OpenCode Permissions MP-1 — synthetic managed deployment

Статус: **IMPLEMENTATION / SYNTHETIC ONLY / NO USER DEPLOYMENT**.

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
- artifact tamper fail closed.

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

Он не сохраняет весь OpenCode config и не копирует credentials/secrets.

Фазы:

- `prepared`;
- `active`.

`prepared` допускает безопасное продолжение после остановки между state publication, native config write и loader publication.

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

## Acceptance gate

MP-1 считается PASS только после:

1. synthetic regressions PASS на Linux и Windows;
2. exact-artifact integration PASS на Linux;
3. PR CI PASS;
4. merge в `main`;
5. post-merge read-back/CI PASS.

Даже после MP-1 PASS реальный пользовательский pilot ещё запрещён: сначала требуется MP-2 disposable exact OpenCode integration.
