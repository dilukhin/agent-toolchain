# ToolSpec, installed runtime и ownership manifest schema 2

## Статус

Документ описывает реализованный фундамент `agent-toolchain` на переходной ветке переименования `opencode_setup → agent-toolchain`.

Реализованы:

- stdlib-only bootstrap управляющего core без общего Python venv;
- `toolchainctl check/apply`;
- manifest schema 2;
- ToolSpec schema 1;
- первый production deployer `git + pinned-tested + python-venv`;
- отдельные runtime для `ssh_relay` и `agent-safe`;
- stable public entrypoints `ssh_relay` и `safe`;
- exact-ref skill reconciliation из того же commit, что runtime;
- Windows user PATH ownership и Linux PATH diagnostics;
- one-way import legacy `opencode_setup` state.

Не реализованы пока `go-binary` deployment для `tunnelctl`, deployment `bundle` и `proxy-tools`.

## Manifest schema 2

Ownership manifest содержит обязательные разделы:

```json
{
  "schema": 2,
  "managed_files": {},
  "credentials": {},
  "managed_tools": {},
  "managed_path_entries": {}
}
```

### `managed_files`

Управляемые файлы: path/source/SHA-256 установленного содержимого. Сюда относятся OpenCode managed files и опубликованные skills.

Для pinned external skill `source` содержит точный tool/ref/path, например:

```text
tool:ssh_relay@<40-hex-sha>:opencode/skills/ssh-relay/SKILL.md
```

### `credentials`

Хранит только metadata credential (`provider`, `mode`, `path`). Secret и его hash в manifest не записываются.

### `managed_tools`

Фиксирует доказанное владение installed runtime:

- owner;
- source/repository;
- exact source ref;
- runtime family;
- runtime path;
- public entrypoints и их targets;
- health contract;
- supported platforms.

Developer checkout в ownership installed runtime не входит.

### `managed_path_entries`

Содержит только PATH entries, которые toolchain сам добавил и которыми вправе управлять. Уже существующий совпадающий PATH entry не присваивается задним числом.

### Migration schema 1 → 2

Известный schema 1 мигрируется в памяти без destructive действий. `check` только сообщает необходимость migration. `apply` сохраняет schema 2 после успешного reconciliation. Неизвестная schema или неверные типы секций дают conflict.

## ToolSpec schema 1

ToolSpec описывает production contract инструмента:

- `source`;
- `repo` / immutable `ref` для Git source;
- `runtime`;
- `update_policy`;
- `entrypoints`;
- `health_contract`;
- `platforms`;
- `project_directory` как metadata source/developer project, а не runtime location.

Для production Python tools текущий deployer принимает только:

```text
source=git
runtime=python-venv
update_policy=pinned-tested
ref=<exact 40-hex commit>
```

Unsupported combinations fail closed.

## Bootstrap core

`bootstrap_linux.sh` / `bootstrap_windows.ps1` используют базовый Python 3.10+ и `bootstrap_core.py`.

Цель bootstrap — установить/обновить сам управляющий core:

```text
repository checkout
      ↓ fingerprint + compile validation
staging core
      ↓ atomic publish
agent-toolchain/core
      ↓
stable toolchainctl entrypoint
```

Общий bootstrap venv отсутствует. Bootstrap не устанавливает Paramiko или другие helper dependencies.

Core принимается только при точном `.agent-toolchain-managed-core.json`. Чужой core path или чужой `toolchainctl` не перезаписывается. Preflight entrypoint выполняется до core mutation. Повторный bootstrap неизменного fingerprint — no-op; backup создаётся только при реальном обновлении доказанно managed core.

## Python managed runtime

Для `ssh_relay` и `agent-safe` lifecycle:

```text
ToolSpec repo@exact-ref
   ↓
temporary isolated venv
   ↓ pip install git+repo@exact-ref (non-editable)
health внутри staging runtime
   ↓
versioned release directory
   ↓
stable managed entrypoint
   ↓
manifest.managed_tools
```

Release path включает exact ref, поэтому новая версия публикуется рядом с предыдущей, а не модифицирует source checkout.

Health выполняется через реальный установленный entrypoint. Для `ssh_relay` `doctor` реально импортирует `paramiko` без network/SSH, что предотвращает прежний false-positive `--version`/`--help` health.

Если базовый Python не имеет `venv/ensurepip`, `check` сообщает prerequisite read-only, а `apply` прекращается до создания runtime directory.

## Stable entrypoint

Linux public command — symlink в `~/.local/bin` на command внутри versioned venv.

Windows public command — owned `.cmd` в `%LOCALAPPDATA%\agent-toolchain\bin`, вызывающий command внутри versioned venv.

Если public path уже существует и ownership не доказан, toolchain сохраняет его и сообщает conflict. PATH shadowing диагностируется отдельно.

## Skills и source/runtime consistency

External helper skills больше не берутся из tracking checkout.

Для каждого ToolSpec skill paths связываются с tool metadata, затем:

1. во временном каталоге инициализируется Git repository;
2. fetch выполняется по exact ToolSpec SHA;
3. checkout detached `FETCH_HEAD`;
4. `rev-parse HEAD` обязан совпасть с pinned SHA;
5. SKILL.md проходит validation;
6. source checkout удаляется;
7. validated payload публикуется как owned versioned skill bundle;
8. destination reconciles через `managed_files`.

Таким образом runtime и skill происходят из одного exact commit.

Монолитный legacy `setup_core` пока сохраняется для уже проверенной OpenCode/npm/config policy. При штатном вызове из `toolchainctl` внутренний adapter отключает только старую helper-repository/skill секцию, чтобы developer tracking checkout не становился production dependency. Прямой вызов `setup_core` остаётся внутренним regression contract, не пользовательским интерфейсом.

## PATH

### Windows

`toolchainctl apply` добавляет `%LOCALAPPDATA%\agent-toolchain\bin` в user PATH только если entry отсутствует, не меняя порядок и другие entries. Собственное изменение записывается в manifest.

### Linux

Target — `~/.local/bin`. Toolchain не редактирует произвольные `.profile`, `.bashrc`, `.zshrc` и т.п.; отсутствие target в текущем PATH выдаёт manual action и не создаёт ownership metadata.

## One-way state migration

Если нового `agent-toolchain` state нет, но известный legacy `opencode_setup` state содержит валидный manifest:

- `check` использует legacy state только для read-only диагностики;
- `apply` copytree в уникальный temporary path;
- копия manifest валидируется;
- temporary атомарно публикуется как новый state;
- legacy оригинал остаётся нетронутым;
- после появления нового state он становится единственным production state.

Это migration bridge для перехода двух машин, а не долгосрочный compatibility namespace.

## Retired interface

`setup_linux.sh` / `setup_windows.ps1` больше не выполняют reconciliation. На переходный период они оставлены только как hard tombstones, которые немедленно завершаются кодом 2 и указывают `bootstrap_*` + `toolchainctl`. После миграции последней legacy-машины их можно удалить физически.

## Следующие расширения

Следующий архитектурный шаг после завершения rename/migration двух машин:

1. `tunnelctl` как `go-binary` ToolSpec consumer;
2. затем `bundle`;
3. затем `proxy-tools`.

Новые runtime families должны расширять общий ToolSpec/deployer и health/ownership contracts, а не создавать отдельные ad-hoc install scripts.
