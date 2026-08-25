# Управляемый entrypoint ssh_relay

## Проблема

`opencode_setup` устанавливает Python-зависимости `ssh_relay` в собственный изолированный Python runtime. При этом исходный `~/projects/ssh_relay/ssh_relay.py` на Linux имеет shebang `#!/usr/bin/env python3` и при прямом запуске `./ssh_relay.py` использует Python из пользовательского `PATH`, а не Python runtime `opencode_setup`.

Поэтому успешная проверка `paramiko` внутри managed runtime не доказывает работоспособность прямого запуска source-файла.

## Текущий контракт

После reconciliation `opencode_setup` различает:

- `ssh_relay runtime` — наличие зависимостей в managed Python;
- `ssh_relay runtime launcher` — launcher внутри managed Python runtime;
- `ssh_relay entrypoint` — стабильный пользовательский entrypoint;
- `ssh_relay health` — запуск `--version` и `--help` через managed entrypoint;
- `ssh_relay command resolution` — какой `ssh_relay` фактически разрешается через `PATH`;
- `ssh_relay source launcher` — диагностическое предупреждение, если прямой Linux source launcher использует другой Python без `paramiko`.

Linux user entrypoint по умолчанию:

```text
~/.local/bin/ssh_relay
```

Он является symlink на launcher внутри управляемого Python runtime. Существующий чужой файл или symlink в этом пути автоматически не заменяется.

Windows user entrypoint по умолчанию:

```text
%LOCALAPPDATA%\opencode_setup\bin\ssh_relay.cmd
```

Он запускает launcher внутри управляемого Python runtime. Существующий файл с другим содержимым автоматически не заменяется.

Если managed bin directory не находится в `PATH`, setup не переписывает `PATH` автоматически на этом этапе, а выводит явное `MANUAL ACTION REQUIRED` с каталогом, который требуется добавить, и абсолютным путём рабочего entrypoint.

## Что запускать

Штатный пользовательский запуск:

```text
ssh_relay daemon ...
ssh_relay status ...
ssh_relay exec ...
```

Прямой `./ssh_relay.py` остаётся source/developer запуском и не считается managed runtime `opencode_setup`.

## Ограничение

Полная унификация `ssh_relay` через общий `ToolSpec`, `managed_tools` и generic owned-PATH reconciliation остаётся отдельным архитектурным этапом. Текущий changeset закрывает ложную health-индикацию и предоставляет стабильный launcher без установки зависимостей в системный Python.
