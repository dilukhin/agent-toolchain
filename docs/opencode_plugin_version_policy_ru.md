# Политика версии OpenCode Plugin API

## Что это за два разных компонента

`OpenCode CLI` и npm-пакет `@opencode-ai/plugin` — разные ресурсы.

- `OpenCode CLI` — исполняемая команда `opencode`. Она может принадлежать Chocolatey, npm, curl-install, Scoop или Homebrew.
- `@opencode-ai/plugin` — библиотека Plugin API, используемая конфигурацией и локальными TypeScript/JavaScript plugins. `agent-toolchain` устанавливает её в `node_modules` каталога OpenCode config через `npm install --prefix <config-dir> ...`; это не создаёт второй `opencode.exe` и не является второй установкой CLI.

Для Windows стандартный config dir — `%USERPROFILE%\.config\opencode`, поэтому управляемый package обычно находится по пути:

```text
%USERPROFILE%\.config\opencode\node_modules\@opencode-ai\plugin\package.json
```

## Почему версия Plugin API должна совпадать с CLI

Upstream OpenCode рекомендует использовать версию `@opencode-ai/plugin`, совместимую с релизом OpenCode, под который пишется или загружается plugin. Plugin API остаётся изменяемым контрактом, поэтому независимое следование `@opencode-ai/plugin@latest` при более старом standalone CLI создаёт ненужный compatibility risk.

Пример неправильного состояния:

```text
OpenCode CLI:          1.18.18 (Chocolatey)
@opencode-ai/plugin:   1.18.25
```

Целевое состояние для такого standalone CLI:

```text
OpenCode CLI:          1.18.18
@opencode-ai/plugin:   1.18.18
```

## Политика agent-toolchain

В `config_data.json` исторически записана политика `@opencode-ai/plugin = latest`. Для этого конкретного пакета она интерпретируется с учётом ownership активного OpenCode:

1. Если активный OpenCode управляется npm, reconciler сначала приводит npm CLI к npm latest, после чего plugin также разрешается через npm latest. Это сохраняет прежний npm-managed upgrade path.
2. Если активный OpenCode принадлежит Chocolatey, curl-install, Scoop, Homebrew или другому standalone manager, `@opencode-ai/plugin` должен точно совпасть с версией активного CLI.
3. Перед install/update выполняется bounded read-only `npm view @opencode-ai/plugin@<cli-version> version --json`, чтобы убедиться, что точная версия опубликована.
4. Если exact package нельзя подтвердить, mutation не выполняется.
5. `check` остаётся read-only; `apply` меняет только локальный Plugin API package, если его версия расходится с целевой.

Таким образом `plugin = latest` означает не «самый новый Plugin API независимо от CLI», а «актуальная Plugin API для активного способа установки OpenCode».

## Диагностика параллельных CLI

Локальный `@opencode-ai/plugin` в config `node_modules` не считается параллельной установкой CLI. Для поиска настоящих дубликатов `agent-toolchain` отдельно инвентаризирует исполняемые `opencode` в PATH и зарегистрированные package managers. При обнаружении нескольких исполняемых экземпляров automatic CLI mutation останавливается fail-closed.

На Windows дополнительная ручная read-only проверка:

```bat
where opencode
npm list -g --depth=0 opencode-ai
choco list --local-only --exact opencode
```

Наличие `@opencode-ai/plugin` под `%USERPROFILE%\.config\opencode\node_modules` в этот список не входит, поскольку пакет не публикует второй managed OpenCode CLI.
