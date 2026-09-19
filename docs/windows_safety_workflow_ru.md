# Windows: точная цель verifier и предварительная проверка safe CLI

Связано с #58. Правила доставляются существующим `templates/AGENTS.md` в
`<OpenCode config>/agent-toolchain/managed-instructions.md`. Обновление проходит
обычный ownership/reconciliation: check только читает, apply обновляет доказанно
управляемый файл, пользовательский текст глобального AGENTS сохраняется.

`agent-safe` владеет CLI, транзакциями и навыком `safe-cli`; `agent-toolchain`
добавляет общее правило исполнения в собственные инструкции. Tool-owned skills
по-прежнему доставляются из того же exact SHA, что runtime, без локальных правок.

## Перед первой mutation

Проверь разрешение команды в PATH и именно установленный CLI:

```powershell
Get-Command safe
safe --version
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify installed safe' }
safe --help
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect installed safe' }
safe system-change --help
if ($LASTEXITCODE -ne 0) { throw 'Planned safe subcommand is unavailable' }
```

`system-change` здесь — пример выбранной подкоманды. Для другой операции проверяется
её собственный help и наличие нужных file-аргументов. Отсутствующий флаг или старая
форма `safe run` — повод остановить подготовку и сверить контракт. Успешный help
не является авторизацией изменения. После замены runtime/executable проверка повторяется.

## Буквальный текст при подготовке файлов

Файловые аргументы устраняют дополнительный слой передачи сложного кода через argv,
но не восстанавливают `$alice`, если PowerShell уже интерполировал его при записи.
Для JSON и текста скриптов используйте редактор или single-quoted here-string.
Для файлов с не-ASCII символами используйте UTF-8; Windows PowerShell 5.1 требует
BOM для надёжного чтения не-ASCII `.ps1`, если этот файл исполняется через `-File`.

Ниже исполняемый пример **подготовки** verifier. Запускайте его в новом каталоге
артефактов своей задачи: существующий файл намеренно не перезаписывается. Он не
создаёт/не меняет службу и не запускает `safe`.

<!-- executable-example: literal-service-verifier -->
```powershell
$ErrorActionPreference = 'Stop'
$verifierPath = Join-Path $PWD 'verify-service.ps1'
if (Test-Path -LiteralPath $verifierPath) { throw 'Verifier file already exists' }
$verifierText = @'
$ErrorActionPreference = 'Stop'
$target = 'AmneziaWGTunnel$alice'
$service = Get-Service -Name $target -ErrorAction Stop
if ($service.Name -cne $target) { throw 'Unexpected service identity' }
@{ target = $service.Name; status = $service.Status.ToString() } | ConvertTo-Json -Compress
'@
[System.IO.File]::WriteAllText($verifierPath, $verifierText, [System.Text.UTF8Encoding]::new($true))
```
<!-- /executable-example -->

В подготовленный `--verify-command-file` помещается команда явного запуска:

```text
powershell.exe -NoProfile -NonInteractive -File "<полный путь каталога задачи>\verify-service.ps1"
```

Это форма для замены пути, не готовая команда. Путь и его quoting проверяются под
фактической оболочкой исполнения `safe`; file-аргумент сам по себе не делает
произвольный путь безопасным для cmd/PowerShell. Не собирайте такую команду из
недоверенных строк. Для `rollback` и `receipt` применяется тот же принцип.

Для заранее согласованного ожидаемого состояния Running файл `--expected-state-file`
содержит, например:

```json
{"assertions":{"target":"AmneziaWGTunnel$alice","status":"Running"},"declarations":{"operation":"approved service change"}}
```

До разрешённого изменения verifier может закономерно вернуть другое состояние.
После изменения сравниваются наблюдаемые `target` и `status`, а не скопированные
expected assertions. Отсутствующая служба/неожиданная identity дают ошибку. При
false-negative сначала независимое чтение той же службы и журнала транзакции;
не повторяйте изменение ради получения зелёной проверки.

Ошибка предварительного help до mutation — несовпадение инструмента, не инцидент
изменения системы. Если mutation могла начаться, это исключение не применяется:
неизвестный результат требует read-back и штатного recovery-процесса.

## Проверки и ограничения

`tests/test_windows_safety_workflow.py` выполняет приведённый блок подготовки в
PowerShell с намеренно заданной переменной `$alice`. Затем исполняет полученный
verifier с синтетическим `Get-Service`: проверяет сохранение точного имени,
наблюдаемое состояние и отказ при подмене identity/отсутствии службы. Реальные
службы, права, `safe` transactions и журнал при этом не изменяются. На Windows
проверяются Windows PowerShell и PowerShell 7; отсутствие любого из них в Windows
CI является ошибкой. На Linux без PowerShell эта проверка явно пропускается.

Отдельная regression в `test_global_agents_split.py` проверяет обновление прежних
управляемых инструкций новым шаблоном, сохранение пользовательского AGENTS,
read-only check и повторный apply без изменений.

Это проверка доставки правил и PowerShell literal semantics, а не доказательство,
что модель всегда соблюдёт инструкции. Полный живой сервисный сценарий здесь не
выполняется. Секреты не помещаются в командные файлы и не публикуются вместе с ними.
