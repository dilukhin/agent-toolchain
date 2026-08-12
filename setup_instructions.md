# Инструкция по настройке и обновлению OpenCode

## 1. Рабочий цикл

Setup не разделяется на install/update. Перед изменением окружения сначала выполняйте проверку, затем обычный reconcile и повторную проверку.

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

- `missing` — managed-компонент отсутствует;
- `up-to-date` — соответствует источнику;
- `outdated` — source/repository содержит более новое managed-состояние;
- `modified/conflict` — безопасное автоматическое изменение запрещено.

`--check`/`-Check` не создаёт каталоги, manifest, placeholder, backup, не делает `chmod`, clone/pull или package install.

## 2. Каталоги по умолчанию

Windows:

```text
OpenCode config: %USERPROFILE%\.config\opencode
RouterAI key:    %USERPROFILE%\projects\stash\opencode.ai\api-key.txt
Global skills:   %USERPROFILE%\.agents\skills
Projects:        %USERPROFILE%\projects
State/manifest:  %LOCALAPPDATA%\opencode_setup\state
```

Linux:

```text
OpenCode config: ~/.config/opencode
RouterAI key:    ~/projects/stash/opencode.ai/api-key.txt
Global skills:   ~/.agents/skills
Projects:        ~/projects
State/manifest:  ${XDG_STATE_HOME:-~/.local/state}/opencode_setup
```

Для изолированной проверки wrappers позволяют переопределить эти пути параметрами/переменными окружения. Рабочие validators всегда используют временный root.

## 3. Владение файлами

`manifest.json` хранит только managed-файлы и для каждого фиксирует destination, source и SHA-256 установленного содержимого. Это не package manager и не индекс всего HOME.

Owned `opencode_setup`:

- `opencode.jsonc` после безопасного принятия в ownership;
- глобальный `AGENTS.md`;
- `remote-long-running/SKILL.md`.

External authoritative sources:

- `ssh-relay/SKILL.md` — `dilukhin/ssh_relay`;
- `recovery-mode`, `risk-gate`, `safe-cli`, `unknown-system-safety` — `dilukhin/agent-safe`.

Unmanaged:

- любые другие глобальные skills;
- BMAD project-local skills;
- пользовательские файлы, не записанные в manifest.

Unmanaged-файлы не удаляются. Совпадающий побайтово с authoritative source файл может быть принят в ownership без изменения его байтов.

## 4. Конфликты и `--force`

Если managed-файл после установки изменён вручную, setup сравнивает текущий SHA-256 с manifest и останавливает обновление этого файла:

```text
modified/conflict  skill remote-long-running  managed file was modified locally; preserved
```

Для сознательного возврата к authoritative версии допустим:

```powershell
.\setup_windows.ps1 -Force
```

```bash
./setup_linux.sh --force
```

`Force` действует только на файл, который уже принадлежит manifest. Перед заменой создаётся backup в `<state>/backups/<timestamp>/...`. Он не применяется к unknown-файлам и не делает reset/clean dependency repositories.

## 5. Dependency repositories

### ssh_relay

Источник: `https://github.com/dilukhin/ssh_relay.git`, branch `main`.

Default checkout:

```text
Windows: %USERPROFILE%\projects\ssh_relay
Linux:   ~/projects/ssh_relay
```

Правила:

- отсутствует → clone указанной branch;
- clean и совпадает с origin → no-op;
- clean и origin впереди → `git pull --ff-only`;
- dirty/untracked, другой origin или branch → conflict; никаких `reset`/`clean`.

После получения источника setup штатно устанавливает `paramiko` при необходимости и проверяет `ssh_relay.py --version`, `ssh_relay.py --help`, наличие `job`. Remote daemon/job setup не запускает.

### agent-safe

Источник: `https://github.com/dilukhin/agent-safe.git`, branch `master`.

Default checkout:

```text
Windows: %USERPROFILE%\projects\agent-safe
Linux:   ~/projects/agent-safe
```

Политика repository та же. Runtime устанавливается штатным editable `pip install -e <repo>` и проверяется через `python -m agent_safe --help`. `opencode-bootstrap --apply` не нужен: централизованный setup сам владеет глобальным config/AGENTS и синхронизирует authoritative skills без второй конкурирующей записи этих файлов.

## 6. Skills

Перед установкой каждого managed skill проверяется `SKILL.md`:

- YAML front matter присутствует и закрыт;
- `name` совпадает с именем каталога и имеет lowercase kebab-case;
- `description` непустой и ограниченной длины.

Managed global skills:

```text
ssh-relay
remote-long-running
recovery-mode
risk-gate
safe-cli
unknown-system-safety
```

Иные каталоги в `.agents/skills` не перечисляются как owned и не очищаются.

## 7. RouterAI, `opencode.jsonc` и API key

Setup сохраняет существующую рабочую модель RouterAI: 13 model IDs, default `opencode/deepseek-v4-flash-free`, small model `opencode/gpt-5-nano`.

Секрет хранится во внешнем `api-key.txt`:

- отсутствует → создать placeholder;
- существует → не читать для сравнения и не менять байты;
- никогда не печатать содержимое;
- Linux → права `0600` при обычном setup.

Generated `opencode.jsonc` становится managed после записи manifest. Тогда upstream template можно безопасно обновлять, пока пользователь не изменил installed-файл вручную. Неизвестный существующий `opencode.jsonc`, не совпадающий с template и не tracked в manifest, сохраняется и даёт conflict вместо полного перезаписывания.

## 8. BMAD

BMAD остаётся project-local и не является частью global skill reconciliation. Для подтверждённой версии `bmad-method@6.8.0` используются существующие wrappers:

```powershell
.\install_bmad_windows.ps1 C:\path\to\project
```

```bash
./install_bmad_linux.sh /path/to/project
```

Они устанавливают `_bmad` и 44 skills в `<project>/.agents/skills`. Глобальный setup не удаляет такие каталоги и не заявляет BMAD global ownership.

## 9. Безопасная разработческая проверка

Не проверяйте новую реализацию первым запуском на рабочем HOME/APPDATA. Используйте:

```powershell
.\validate_setup.ps1
```

```bash
./validate_setup.sh
```

Validators создают временные dependency remotes/worktrees и временный HOME-equivalent. Проверяются:

1. clean install;
2. повторный запуск без лишних изменений;
3. update authoritative managed skill;
4. сохранение unknown skill;
5. сохранение BMAD-like skill;
6. сохранение локально изменённого managed skill;
7. `--force` только с backup owned-файла;
8. побайтовое сохранение existing API key;
9. clean dependency repo fast-forward;
10. dirty dependency repo без reset/clean;
11. `--check` без изменений;
12. короткий `AGENTS.md`;
13. наличие всех шести managed global skills;
14. front matter skills;
15. PowerShell/Bash/Python syntax и отсутствие очевидных секретов.

GitHub Actions дополнительно выполняет полную Windows/Linux проверку и project-local BMAD install/reinstall в temporary target.

## 10. Примеры результата

Первый install может показать `missing` для репозиториев и managed files — обычный setup их создаст. После успешного reconcile повторный `--check` должен показывать только `up-to-date`.

При update clean `ssh_relay`/`agent-safe` fast-forward обновляется, затем меняются только соответствующие managed skills. При dirty repository setup сообщает conflict и оставляет локальные изменения нетронутыми.

После применения реального окружения рекомендуемая последовательность всегда одна:

```text
setup --check
setup
setup --check
```
