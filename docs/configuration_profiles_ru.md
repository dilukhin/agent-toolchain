# Профили и локальная конфигурация `agent-toolchain`

## Граница конфигурации

Desired state собирается в фиксированном порядке:

```text
public product/core
→ explicit distribution/profile policy
→ machine/user local override
```

`config_data.json` содержит только публичную базу продукта. Она не выбирает RouterAI, авторские модели, `ssh_relay`/`agent-safe`, каталоги `~/projects` или локальный proxy endpoint.

Профили репозитория находятся в `templates/profiles/`. Профиль `dilukhin` сохраняет прежнюю авторскую политику: RouterAI, curated models/defaults, pinned `ssh_relay`/`agent-safe`, связанные skills и прежние location hints. Профиль не должен содержать secrets.

## Выбор профиля

Новая установка без доказанного прежнего managed state использует профиль `generic`.

Явный профиль:

```text
toolchainctl check --profile dilukhin
toolchainctl apply --profile dilukhin
```

Альтернатива для автоматизированного запуска:

```text
AGENT_TOOLCHAIN_PROFILE=dilukhin toolchainctl check
```

После успешного `apply` выбранный профиль записывается в локальный ownership manifest как `configuration_profile`. `check` это поле только читает.

Существующий managed state прежнего `opencode_setup`/раннего `agent-toolchain` без поля профиля переводится в `dilukhin` только при однозначном ownership evidence: прежние управляемые RouterAI metadata, известные managed tools или известные source labels. Иное непустое состояние без записанного профиля даёт `modified/conflict`; reconciler не угадывает профиль.

Автоматический переход с записанного author/alternate profile на другой профиль не выполняется. Из `generic` можно явно перейти на профиль, потому что generic base не владеет author-specific resources.

## Локальный override

Локальный JSON накладывается после профиля:

```text
toolchainctl check --profile dilukhin --local-config ~/.config/agent-toolchain/local.json
toolchainctl apply --profile dilukhin --local-config ~/.config/agent-toolchain/local.json
```

или:

```text
AGENT_TOOLCHAIN_LOCAL_CONFIG=~/.config/agent-toolchain/local.json toolchainctl check
```

Формат — JSON object. `profile_schema: 1` допустим, но не обязателен. `null` удаляет выбранный ключ из предыдущего слоя. Вложенные objects объединяются рекурсивно, остальные значения заменяются целиком.

Пример локального слоя:

```json
{
  "profile_schema": 1,
  "managed_environment": {
    "tools": {
      "agent-safe": null
    }
  },
  "machine": {
    "proxy_url": "socks5://127.0.0.1:1080"
  }
}
```

Этот файл не копируется в Git и его содержимое не записывается в manifest. Секреты по-прежнему должны храниться во внешних credential files/secret stores, а не в repository profile или manifest.

## Пути

Платформенные значения из `platform_specific` профиля/локального override применяются только для выбранной платформы. Переменные окружения `OPENCODE_CONFIG_DIR`, `OPENCODE_CREDENTIAL_DIR`, `OPENCODE_SKILLS_DIR`, `OPENCODE_STASH_DIR`, `OPENCODE_PROJECTS_DIR` имеют приоритет как machine-local override.

У generic profile нет default `~/projects`/`%USERPROFILE%\\projects`: legacy source-checkout paths передаются во внутренний reconciler как неиспользуемые state-local paths. Production helper runtimes по-прежнему устанавливаются через ToolSpec exact refs и не зависят от developer checkout.

## Проверяемые свойства

- `check` не записывает profile selection, local override или manifest;
- повторный `apply` с тем же profile/local override является no-op;
- generic fresh install не требует RouterAI credential и не добавляет RouterAI provider/models;
- author migration сохраняет прежний RouterAI/provider/tool policy;
- неизвестный managed state без доказанного profile ownership блокирует автоматическую mutation;
- local override имеет deterministic precedence и не попадает в Git/manifest.
