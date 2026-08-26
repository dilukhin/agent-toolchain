# Рабочая инструкция agent-toolchain

## 1. Штатный цикл

`agent-toolchain` разделяет bootstrap управляющего core и обычное reconciliation окружения.

Bootstrap выполняют только для первичной установки или обновления самого core:

Windows:

```powershell
.\bootstrap_windows.ps1
```

Linux:

```bash
./bootstrap_linux.sh
```

После этого штатный интерфейс только один:

```text
toolchainctl check
toolchainctl apply
toolchainctl check
```

`check` read-only. Он не должен создавать state, runtimes, skills, credentials, backups, clone/pull или менять PATH/permissions.

Старые `setup_windows.ps1` и `setup_linux.sh` выведены из эксплуатации и завершаются ошибкой. Не использовать их как fallback.

## 2. Каталоги

Windows:

```text
Toolchain core:          %LOCALAPPDATA%\agent-toolchain\core
Managed tool runtimes:  %LOCALAPPDATA%\agent-toolchain\tools
Public commands:        %LOCALAPPDATA%\agent-toolchain\bin
State/manifest:         %LOCALAPPDATA%\agent-toolchain\state
OpenCode config:        %USERPROFILE%\.config\opencode
RouterAI credential:    %USERPROFILE%\.config\opencode\credentials\routerai-api-key.txt
Global skills:          %USERPROFILE%\.agents\skills
```

Linux:

```text
Toolchain core:          ${XDG_DATA_HOME:-~/.local/share}/agent-toolchain/core
Managed tool runtimes:  ${XDG_DATA_HOME:-~/.local/share}/agent-toolchain/tools
Public commands:        ~/.local/bin
State/manifest:         ${XDG_STATE_HOME:-~/.local/state}/agent-toolchain
OpenCode config:        ~/.config/opencode
RouterAI credential:    ~/.config/opencode/credentials/routerai-api-key.txt
Global skills:          ~/.agents/skills
```

## 3. One-way миграция opencode_setup

На первой машине с legacy state:

1. `toolchainctl check` обнаруживает старый `opencode_setup` manifest без записи нового state;
2. `toolchainctl apply` копирует legacy state во временный каталог;
3. копия валидируется;
4. новый `agent-toolchain` state публикуется атомарно;
5. исходный `opencode_setup` state остаётся неизменённым как inactive backup;
6. следующие запуски используют новый state.

Если legacy-каталог есть, но его ownership manifest отсутствует/невалиден, automatic adoption запрещён. Уже существующий новый `agent-toolchain` state также обязан быть обычным каталогом с валидным manifest; неизвестный или symlink state не усыновляется.

Миграция выполняется отдельно на каждой машине. Для текущего перехода сначала рабочий Linux ILUKHIN, затем домашний Windows-ноутбук.

## 4. Managed CLI tools

Production helper tool устанавливается не из developer checkout, а из ToolSpec `repo@exact-ref`.

Текущие managed tools:

```text
ssh_relay → isolated venv → ssh_relay
agent-safe → isolated venv → safe
```

Для каждого инструмента:

1. проверяется immutable 40-hex ref;
2. эксклюзивно резервируется final versioned release directory;
3. venv создаётся **сразу по финальному пути**, потому что стандартный Python venv не relocatable;
4. package ставится non-editable из exact Git ref;
5. health выполняется через установленный entrypoint в final runtime;
6. только после успешного health записывается ownership marker;
7. stable public entrypoint создаётся только если target path свободен или доказанно принадлежит agent-toolchain;
8. ownership записывается в `managed_tools`.

Если создание venv, install, health или запись marker завершается ошибкой, удаляется только final release directory, доказанно созданный этим текущим запуском. Ранее существующий, конкурентно появившийся или неизвестный runtime path не удаляется и даёт conflict.

Developer checkout может быть dirty, содержать локальные commits или отсутствовать — production runtime от этого не зависит.

## 5. Pinned skills

Skills helper tools должны соответствовать тому же exact ref, что и runtime.

Для `ssh_relay` и `agent-safe` toolchain получает SKILL.md через временный clean checkout pinned SHA, проверяет фактический `HEAD`, валидирует front matter и сохраняет owned skill bundle. Затем destination reconciles обычным file-ownership механизмом.

Tracking checkout `~/projects/ssh_relay` / `~/projects/agent-safe` не является production source для skill.

## 6. PATH

Linux: `~/.local/bin` должен быть в PATH. Toolchain не редактирует неизвестные shell startup files автоматически; при отсутствии выдаётся manual action.

Windows: `%LOCALAPPDATA%\agent-toolchain\bin` добавляется `toolchainctl apply` в user PATH только при отсутствии. Существующие entries не удаляются и не переставляются. Toolchain записывает ownership только собственного добавленного entry.

Если команда разрешается в чужой executable раньше managed command, это shadowing/conflict, а не повод удалить чужой файл.

## 7. OpenCode configuration

Сохраняются ранее реализованные правила:

- существующий совместимый `opencode.jsonc` изменяется минимальным semantic merge;
- неизвестные пользовательские fields/model entries сохраняются;
- если JSONC formatting нельзя безопасно сохранить, изменение блокируется как conflict;
- inline/non-file RouterAI secret не читается и не переносится;
- существующая `{file:...}` ссылка остаётся authoritative credential path;
- fake key не создаётся;
- пользовательский текст `AGENTS.md` вокруг managed block сохраняется;
- неизвестные/BMAD skills не удаляются.

`--force` действует только на уже доказанно owned content и не разрешает destructive Git operations.

## 8. RouterAI credential

Fresh-install canonical path указан выше, но существующая рабочая `{file:...}` ссылка важнее default.

Правила:

- external credential bytes не читать и не печатать;
- отсутствующий external credential → manual action, без alternate placeholder;
- inline/non-file apiKey → preserve/conflict;
- fresh install → записать path, но не создавать fake file;
- точный legacy placeholder в доказанно managed path может быть распознан как missing, но не перезаписывается автоматически;
- manifest хранит provider/mode/path, но не secret/hash;
- Linux managed credential получает `0600`, bytes сохраняются.

## 9. BMAD

BMAD не является global managed tool. Он project-local и устанавливается отдельными scripts:

```bash
./install_bmad_linux.sh /path/to/project
```

```powershell
.\install_bmad_windows.ps1 C:\path\to\project
```

Version/integrity/skill contract берутся из `config_data.json` и валидируются installer'ом.

## 10. Диагностика конфликтов

При `modified/conflict` сначала определить ownership и фактическое состояние. Не применять `reset`, `clean`, удаление неизвестных runtime/state directories или force replacement как автоматический recovery.

Bootstrap core с корректным marker, но изменённым фактическим payload не считается `up-to-date`: bootstrap fail closed и сохраняет каталог без автоматической замены.

Повторный цикл после исправления причины:

```text
toolchainctl check
→ toolchainctl apply
→ toolchainctl apply
→ toolchainctl check
```

Второй apply должен быть no-op.
