# Инструкция по настройке и обновлению OpenCode

## 1. Рабочий цикл

Setup не разделяется на install/update. Перед изменением окружения выполняйте check, затем reconcile и повторный check.

Windows:

```powershell
.\setup_windows.ps1 -Check
.\setup_windows.ps1
.\setup_windows.ps1 -Check
```

Linux:

```bash
./setup_linux.sh --check
./setup_linux.sh
./setup_linux.sh --check
```

Состояния:

- `missing` — компонент отсутствует;
- `up-to-date` — соответствует текущей политике/source;
- `outdated` — безопасное обновление требуется;
- `modified/conflict` — автоматическое изменение небезопасно.

`--check`/`-Check` не создаёт каталоги, manifest, credentials/placeholders, backups, не делает clone/pull/chmod/package install.

## 2. Каталоги по умолчанию

Windows:

```text
OpenCode config:          %USERPROFILE%\.config\opencode
Canonical RouterAI key:  %USERPROFILE%\.config\opencode\credentials\routerai-api-key.txt
Legacy credential dir:    %USERPROFILE%\projects\stash\opencode.ai
Global skills:            %USERPROFILE%\.agents\skills
Projects:                 %USERPROFILE%\projects
State/manifest:           %LOCALAPPDATA%\opencode_setup\state
```

Linux:

```text
OpenCode config:          ~/.config/opencode
Canonical RouterAI key:  ~/.config/opencode/credentials/routerai-api-key.txt
Legacy credential dir:    ~/projects/stash/opencode.ai
Global skills:            ~/.agents/skills
Projects:                 ~/projects
State/manifest:           ${XDG_STATE_HOME:-~/.local/state}/opencode_setup
```

Canonical credential path относится только к fresh install. Если существующий `opencode.jsonc` уже ссылается через `{file:...}` на другой key, этот фактический путь и точная текстовая ссылка сохраняются.

## 3. Владение и manifest

`manifest.json` хранит managed content. Для обычных owned-файлов фиксируются path/source/SHA-256 установленного содержимого. Для RouterAI credential сохраняются только `provider`, `mode`, `path`: содержимое и SHA-256 секрета в manifest не записываются.

Owned/managed:

- managed fields `opencode.jsonc` после безопасного принятия/migration;
- whole generated `AGENTS.md` либо маркированный managed block в уже существующем пользовательском файле;
- `remote-long-running/SKILL.md`;
- external authoritative skill copies после установки.

Unmanaged:

- остальные global skills;
- BMAD project-local skills;
- пользовательский текст вокруг managed block `AGENTS.md`;
- внешний credential, на который уже ссылается пользовательский config.

## 4. Существующий `opencode.jsonc`

Setup сначала пытается распознать совместимый `provider.routerai`.

Безопасная миграция:

1. разобрать JSON/JSONC;
2. обнаружить существующий `options.apiKey`;
3. если это `{file:...}`, сохранить точную ссылку и использовать этот файл как фактический credential;
4. сохранить неизвестные top-level settings, выбранные пользователем `model`/`small_model` и дополнительные model entries;
5. добавить/обновить только managed RouterAI fields;
6. сделать backup до первой семантической миграции;
7. записать ownership metadata.

Если файл содержит comments/trailing commas и для merge потребовалась бы перезапись с потерей форматирования, setup выдаёт `modified/conflict`. То же относится к config без распознаваемого `provider.routerai`. Полный произвольный файл не перезаписывается.

Если существующий `apiKey` задан не через `{file:...}` (например, inline или другим механизмом), setup не читает и не переносит значение, не создаёт параллельный placeholder и оставляет config как `modified/conflict`.

## 5. RouterAI credential

Приоритет выбора credential:

```text
existing opencode.jsonc {file:...}
        ↓
credential metadata из manifest
        ↓
canonical profile path для fresh install
```

Поведение:

- referenced external file существует → `up-to-date`, точная ссылка и байты сохраняются, содержимое не читается и не выводится;
- referenced external file отсутствует → missing/conflict, **никакого второго placeholder в другом месте**;
- existing non-file/inline `apiKey` → conflict, config сохраняется, значение не читается/не выводится, placeholder не создаётся;
- fresh install → canonical path записывается в config/manifest, но key-файл не создаётся; до ручного provisioning состояние credential остаётся `missing`;
- точный placeholder, созданный предыдущей версией `opencode_setup` в доказанно managed credential path, считается `missing`, сохраняется без перезаписи и должен быть вручную заменён реальным RouterAI API key;
- внешний credential не читается даже для проверки на legacy-placeholder;
- Linux managed credential получает `0600` после появления реального файла;
- старый `stash/api-key.txt` остаётся legacy compatibility для старых прямых `setup_core.py` вызовов.

Setup не создаёт fake secret, чтобы наличие файла не выглядело доказательством настроенного RouterAI. API key нельзя выводить в diagnostics/logs.

## 6. Глобальный `AGENTS.md`

Если файла нет — создаётся штатный compact template.

Если уже есть произвольный пользовательский файл — setup:

- делает backup;
- сохраняет существующий текст;
- добавляет блок между:

```text
<!-- opencode_setup:managed:start -->
...
<!-- opencode_setup:managed:end -->
```

В последующих версиях обновляется только этот блок. Если пользователь вручную изменил сам managed block, обычный setup сообщает conflict. `--force` может заменить только управляемый блок после backup, не окружающий пользовательский текст.

## 7. Dependency repositories

### Общая политика

- отсутствует → clone ожидаемой branch;
- tracked tree clean, без local commits, HEAD совпадает с origin → no-op;
- tracked tree clean, без local commits, origin впереди и нет мешающих untracked → `git pull --ff-only`;
- tracked modifications → conflict;
- local commits → conflict;
- non-benign untracked → conflict;
- path существует, но не Git working copy → conflict;
- никогда `reset`, `clean`, destructive checkout или rebase.

Benign untracked разрешены **только** как локальные metadata, которые не являются частью source:

- `.agent-safety/**`;
- `*.md`.

Если repository уже совпадает с origin, такие файлы не мешают использовать source/skills. Если origin отличается, setup не делает pull поверх них автоматически и сообщает conflict.

### ssh_relay

Источник `https://github.com/dilukhin/ssh_relay.git`, branch `main`.

```text
Windows: %USERPROFILE%\projects\ssh_relay
Linux:   ~/projects/ssh_relay
```

После получения usable source проверяются `paramiko`, `ssh_relay.py --version`, `--help`, наличие `job`. Daemon/remote job setup не запускает.

### agent-safe

Источник `https://github.com/dilukhin/agent-safe.git`, branch `master`.

```text
Windows: %USERPROFILE%\projects\agent-safe
Linux:   ~/projects/agent-safe
```

Runtime: `python -m pip install -e <repo>` + `python -m agent_safe --help`.

Если default path уже существует, но **не является Git working copy**, setup его не удаляет и не клонирует поверх. Сначала вручную выясните назначение каталога. Если его нужно сохранить, безопасный recovery:

1. закрыть процессы, которые могут его использовать;
2. переименовать каталог в backup-name, например `agent-safe.pre-opencode-setup`;
3. не удалять backup до проверки новой установки;
4. повторить `setup -Check`, затем setup;
5. проверить новый authoritative checkout/runtime/skills;
6. только после этого разбирать/удалять backup вручную.

Автоматического destructive `--repair-dependencies` намеренно нет.

## 8. Skills

Перед установкой проверяются YAML front matter, `name` и `description`.

Managed global skills:

```text
ssh-relay
remote-long-running
recovery-mode
risk-gate
safe-cli
unknown-system-safety
```

Другие каталоги `.agents/skills`, включая BMAD, не очищаются.

## 9. Npm-компоненты OpenCode

`opencode-ai` и `@opencode-ai/plugin` имеют runtime policy `latest`:

- npm registry недоступен → conflict;
- current == latest → настоящий no-op;
- current != latest → install exact resolved latest и post-install validation.

Так plugin не остаётся на старом hardcoded pin только потому, что `opencode_setup` давно не обновлялся.

## 10. BMAD

BMAD имеет отдельную политику `pinned-tested`.

Штатный pin этой версии проекта — `bmad-method@6.11.0`. Для него зафиксированы npm integrity, upstream release commit и ожидаемый контракт **49 skills**; install/reinstall проверяется на Windows и Linux. Для BMAD 6.11 нужны Node.js 20.12+, Python 3.11+ и `uv`; installers проверяют эти prerequisites до изменения проекта. Следующая upstream-версия не становится штатной автоматически до такого же обновления integrity/contract и прохождения validators.

```powershell
.\install_bmad_windows.ps1 C:\path\to\project
```

```bash
./install_bmad_linux.sh /path/to/project
```

## 11. `--force`

`Force` применяется только к уже managed content. Перед заменой создаётся backup в `<state>/backups/<timestamp>/...`.

Он **не**:

- забирает во владение arbitrary config;
- чинит non-git dependency directory;
- сбрасывает local commits;
- делает reset/clean;
- разрешает удаление unknown/BMAD skills.

## 12. Разработческая проверка

```powershell
.\validate_setup.ps1 -TestBmad
py -3 .\tests\run_migration_ci.py
```

```bash
./validate_setup.sh --bmad
python3 ./tests/run_migration_ci.py
```

Migration regression tests покрывают:

1. existing external `{file:...}` credential без инспекции содержимого;
2. сохранение exact key reference/bytes и отсутствие параллельного placeholder;
3. existing inline/non-file credential conflict без утечки/миграции;
4. fresh canonical profile credential path без fake key-файла;
5. legacy managed placeholder как `missing` и переход к `up-to-date` после ручного provisioning;
6. safe RouterAI merge с пользовательскими settings/models;
7. существующий AGENTS + managed block;
8. benign `.agent-safety/**`/Markdown untracked;
9. arbitrary untracked conflict.

Полные validators дополнительно проверяют clean install, repeated setup, read-only check, fast-forward dependency update, local managed conflicts/backup, local commits, все 6 managed skills и BMAD install/reinstall.

## 13. Целевой реальный результат

После устранения действительно ручных конфликтов:

```text
setup --check
setup
setup --check
setup
setup --check
```

Второй обычный setup должен быть no-op. External key должен остаться на исходном пути, пользовательские config/AGENTS данные — сохраниться, unknown/BMAD skills — остаться, dependency repositories — не подвергаться destructive Git operations. Если RouterAI credential ещё не provisioned, повторный setup остаётся byte-idempotent, но check продолжает сообщать `missing`, пока пользователь не создаст реальный key-файл по указанному пути.
