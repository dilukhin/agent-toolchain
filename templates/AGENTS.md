# agent-toolchain managed OpenCode instructions

> This file is managed by `agent-toolchain`. Do not edit it directly. Put machine-specific or user-specific persistent instructions in `../AGENTS.md` outside the `agent-toolchain:managed` markers.

- Never expose secrets, tokens, passwords, API keys, or credential files.
- Do not scan `.git`, `node_modules`, build output, caches, or logs without a reason.
- When work uses `ssh_relay`, load the `ssh-relay` skill first.
- Before builds, CMake, CTest, integration/load tests, long scripts, or other long-running operations, load `remote-long-running`.
- Before risky state-changing actions or work in an unfamiliar subsystem, load the relevant agent-safe skill: `risk-gate`, `safe-cli`, `unknown-system-safety`, or `recovery-mode`.
- Do not preload specialized skills unless the current task needs them.

## Проверка CLI и команд Windows перед изменениями

- Перед первым изменением через `safe` в задаче проверь именно установленный CLI: `safe --version`, `safe --help`, затем `safe <нужная-подкоманда> --help`. После смены executable/runtime повтори проверку. Help подтверждает синтаксис, но не разрешение на действие; не угадывай старые команды вроде `safe run`.
- Для PowerShell verify/rollback/receipt с `$`, обратной кавычкой, вложенными кавычками, JSON или сложным кодом используй поддержанные установленным CLI `--expected-state-file`, `--verify-command-file`, `--rollback-command-file`, `--receipt-command-file`. Файлы содержат UTF-8; команда в command-file должна явно запускать нужный интерпретатор. Формируй буквальный текст через single-quoted строки/here-strings или файловый редактор, не через интерполируемые double-quoted строки.
- Перед mutation проверь содержимое подготовленных файлов и точную цель: например, `AmneziaWGTunnel$alice` должна сохранить буквальное `$alice`. Сам `--*-file` не исправляет текст, уже искажённый оболочкой при создании файла.
- Verify получает фактическую identity цели и состояние независимо от expected-state; не подставляй ожидаемое значение вместо наблюдаемого. При неожиданном результате сначала read-only проверка той же точной цели и состояния транзакции; не повторяй mutation вслепую.
- Ошибка usage во время предварительного `--help`, до первого изменения, означает несовпадение CLI contract и не требует system rollback. Если изменение уже могло начаться или результат неизвестен, сначала проверь actual state/journal и следуй `recovery-mode`.
- Командные файлы не предназначены для секретов: не добавляй туда credentials и не копируй полный чувствительный текст команд в журнал, receipt или отчёт. Эти правила не ослабляют Linux/YC safety contracts.

## Делегирование и стоимость моделей

- Для быстрого read-only поиска по кодовой базе используй `explore`; он не должен выполнять shell-команды или изменять файлы.
- Для извлечения фактов из выбранных локальных артефактов используй `luna`; это дешёвая leaf-role, не предназначенная для дальнейшего делегирования.
- Для публичной документации, API и проверки версий используй `docs-researcher`.
- Небольшую bounded правку по уже выбранному плану можно поручать `code-worker`; он не запускает shell и не расширяет scope.
- Сложный причинный анализ без mutation поручай `sol-specialist`; сложную самостоятельную реализацию — `general` или основному `build`.
- Обычное независимое review изменений поручай `code-reviewer`; проверку достаточности фактических доказательств — `evidence-auditor`.
- `astra-reviewer` используй для дорогого критического review только когда затронуты ownership, privilege boundary, secrets, recovery/data loss или спорная архитектура с высокой ценой ошибки.
- `luna-safe-worker` предназначен только для точно ограниченной операции через существующие agent-safe/opencode_permissions contracts; не используй его как универсальный shell-agent.
- `test-runner` запускает только явно назначенные проверки и не исправляет код или тесты. Его shell остаётся approval-gated.
- Не эскалируй модель только из-за размера diff: выбор модели определяется сложностью анализа, а полномочия — риском действия. Если дешёвая роль не может доказать вывод, верни конкретный пробел и передай анализ Sol/Astra вместо повторных догадок.

