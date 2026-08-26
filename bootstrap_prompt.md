# Bootstrap prompt

Используй этот prompt только для запуска готового `agent-toolchain`; не воспроизводи reconciliation вручную и не читай/не выводи secrets.

```text
Получи или безопасно обнови рабочую копию dilukhin/agent-toolchain. Не делай reset/clean при локальных изменениях.

1. Если toolchainctl ещё не установлен или нужно обновить управляющий core, запусти один раз: Windows `bootstrap_windows.ps1`, Linux `bootstrap_linux.sh`.
2. Запусти `toolchainctl check`.
3. Сообщи найденные `modified/conflict` и `failed`. Не уничтожай local changes, unknown skills или legacy state.
4. Если нет блокирующего конфликта, запусти `toolchainctl apply`.
5. Повтори `toolchainctl apply`, затем `toolchainctl check`; второй apply должен быть no-op.
6. Проверь resolution штатных команд (`toolchainctl`, `ssh_relay`, `safe`) и их safe health (`ssh_relay doctor`, `safe --help`).
7. Сообщи итоговые состояния и оставшиеся manual actions. Не показывай содержимое credential files.

Старые setup_linux.sh/setup_windows.ps1 выведены из эксплуатации и не являются fallback.
Не запускай реальную удалённую ssh_relay-задачу для health. BMAD устанавливай только отдельно, если дан явный путь проекта.
```
