# opencode_setup

`opencode_setup` — воспроизводимая настройка и безопасное обновление рабочего окружения OpenCode на Windows и Linux. Обычный setup является идемпотентным reconciler: устанавливает отсутствующие управляемые компоненты, обновляет устаревшие и не уничтожает неизвестные или локально изменённые данные.

## Быстрый старт

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

`--check`/`-Check` ничего не изменяет и показывает состояние компонентов: `missing`, `up-to-date`, `outdated`, `modified/conflict`.

## Что управляется

| Компонент | Источник истины | Целевое размещение |
|---|---|---|
| RouterAI/OpenCode config | `templates/opencode.jsonc` | `~/.config/opencode/opencode.jsonc` |
| глобальный `AGENTS.md` | `templates/AGENTS.md` | `~/.config/opencode/AGENTS.md` |
| `remote-long-running` | `opencode_setup/skills/remote-long-running` | `~/.agents/skills/remote-long-running` |
| `ssh-relay` | `dilukhin/ssh_relay` | `~/.agents/skills/ssh-relay` |
| `recovery-mode`, `risk-gate`, `safe-cli`, `unknown-system-safety` | `dilukhin/agent-safe` | `~/.agents/skills/<name>` |
| локальная рабочая копия `ssh_relay` | `dilukhin/ssh_relay`, branch `main` | `~/projects/ssh_relay` |
| локальная рабочая копия `agent-safe` | `dilukhin/agent-safe`, branch `master` | `~/projects/agent-safe` |
| OpenCode CLI / plugin | npm | global CLI / OpenCode config |

В Windows `~` в таблице соответствует `%USERPROFILE%`. Глобальный каталог skills — `%USERPROFILE%\.agents\skills`; в Linux — `$HOME/.agents/skills`.

## Модель install/update

Отдельных режимов `install` и `update` нет. Один и тот же setup определяет состояние автоматически:

```text
нет компонента                         -> установить
managed-компонент отстал от источника -> обновить
компонент актуален                    -> ничего не менять
managed-файл изменён вручную          -> conflict, сохранить файл
dependency repo dirty                 -> conflict, без reset/clean
неизвестный skill                     -> не трогать
```

Clean dependency repository обновляется только fast-forward. Setup никогда не делает `git reset` или `git clean` в `ssh_relay`/`agent-safe`.

## Владение и конфликты

Setup владеет только явно перечисленными файлами. После успешной установки их путь, source и SHA-256 фиксируются в компактном `manifest.json`:

- Windows: `%LOCALAPPDATA%\opencode_setup\state\manifest.json`;
- Linux: `${XDG_STATE_HOME:-$HOME/.local/state}/opencode_setup/manifest.json`.

Весь `.agents/skills` не считается принадлежащим setup. Пользовательские, BMAD и другие неизвестные skills не удаляются и не перезаписываются.

Если tracked managed-файл изменён вручную, обычный setup сообщает `modified/conflict` и сохраняет его. `--force`/`-Force` разрешён только для уже tracked managed-файлов: перед заменой создаётся backup в state directory. Он не разрешает destructive reset репозиториев и не даёт права перезаписывать неизвестный файл.

## ssh_relay и agent-safe

`ssh_relay` является authoritative source для `ssh-relay/SKILL.md`. Setup клонирует или fast-forward обновляет его рабочую копию, устанавливает `paramiko` штатным `pip`, проверяет `ssh_relay.py --version`, `--help` и наличие `job`. Реальная удалённая задача setup не запускается.

`agent-safe` является authoritative source для четырёх skills. Рабочая копия устанавливается editable-командой `python -m pip install -e <repo>` и проверяется через `python -m agent_safe --help`. Setup синхронизирует только его authoritative skill-файлы и не дублирует их тексты.

## remote-long-running и глобальный AGENTS.md

`remote-long-running` загружается по необходимости для длительных сборок, CMake/CTest, интеграционных/нагрузочных тестов и других процессов, которые могут пережить transport timeout. Для удалённых длительных процессов он маршрутизирует работу через `ssh_relay job`; большие file transfers — через специализированные `upload`/`download` команды relay.

Глобальный `AGENTS.md` намеренно короткий: общие правила секретов и маршрутизация на `ssh-relay`, `remote-long-running` и agent-safe skills. Подробности навыков туда не копируются.

## RouterAI и API key

Сохраняется текущая рабочая RouterAI-конфигурация с 13 model IDs и default `opencode/deepseek-v4-flash-free`. Setup не выполняет полный пересмотр RouterAI.

API key хранится отдельно в `~/projects/stash/opencode.ai/api-key.txt`. Если файла нет, создаётся placeholder. Если файл существует, его байты никогда не заменяются placeholder и содержимое не выводится. В Linux применяются права `0600`.

`opencode.jsonc` после принятия setup в ownership обновляется по manifest. Неизвестный существующий config не перезаписывается автоматически: это `modified/conflict`, пока пользователь явно не мигрирует его.

## BMAD

Project-local BMAD остаётся отдельной, уже воспроизводимой установкой `bmad-method@6.8.0`:

```powershell
.\install_bmad_windows.ps1 C:\path\to\project
```

```bash
./install_bmad_linux.sh /path/to/project
```

BMAD создаёт `<project>/_bmad` и `<project>/.agents/skills`. Обычный глобальный setup эти skills не удаляет и не перезаписывает.

## Проверка разработки

Новый setup сначала тестируется только во временном HOME/APPDATA:

```powershell
.\validate_setup.ps1
```

```bash
./validate_setup.sh
```

Полный CI также запускает project-local BMAD validation. Тесты покрывают clean install, идемпотентность, update managed skill, сохранение unknown/BMAD skills и API key, конфликт ручной правки, `--force` с backup, clean/dirty dependency repos и read-only `--check`.

## Основные файлы

| Файл | Назначение |
|---|---|
| `setup_core.py`, `setup_lib.py`, `setup_runtime.py` | общий reconciler, ownership/git и runtime checks Windows/Linux |
| `setup_windows.ps1`, `setup_linux.sh` | платформенные wrappers |
| `templates/` | owned OpenCode config и короткий `AGENTS.md` |
| `skills/remote-long-running/SKILL.md` | общий skill длительных операций |
| `config_data.json` | модели, BMAD и декларация managed environment |
| `validate_setup.ps1`, `validate_setup.sh` | изолированные проверки |
| `setup_instructions.md` | подробная эксплуатационная инструкция |
| `bootstrap_prompt.md` | короткий вход для агента |
