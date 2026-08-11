# Инструкция по настройке OpenCode

## 1. Базовая установка

Windows:

```powershell
.\setup_windows.ps1
```

По умолчанию используются:

- config: `%USERPROFILE%\.config\opencode`;
- API key: `%USERPROFILE%\projects\stash\opencode.ai\api-key.txt`.

Для изолированного target или теста пути можно задать параметрами:

```powershell
.\setup_windows.ps1 -ConfigDir C:\temp\oc-config -StashDir C:\temp\oc-stash -SkipPackageInstall
```

Linux:

```bash
./setup_linux.sh
```

По умолчанию используются `~/.config/opencode` и `~/projects/stash/opencode.ai/api-key.txt`. Для изолированного запуска доступны `OPENCODE_CONFIG_DIR`, `OPENCODE_STASH_DIR` и `OPENCODE_SETUP_SKIP_NPM=1`.

Скрипты:

- устанавливают OpenCode CLI и `@opencode-ai/plugin@1.15.4`;
- создают RouterAI config только при его отсутствии;
- создают `AGENTS.md` только при его отсутствии;
- создают placeholder API-ключа только при его отсутствии;
- всегда задают Linux API key права `0600`;
- не выводят содержимое API-ключа.

Существующие конфиги намеренно сохраняются. Автоматический merge пользовательского `opencode.jsonc` небезопасен, поэтому необходимые обновления такого файла выполняются вручную после сравнения.

## 2. RouterAI

Эталон содержит 13 model IDs, подтвержденных текущим RouterAI models endpoint:

```text
openai/gpt-4o
openai/gpt-4o-mini
deepseek/deepseek-chat
anthropic/claude-opus-4.7
qwen/qwen3.6-plus
deepseek/deepseek-v4-pro
minimax/minimax-m2.7
z-ai/glm-5
moonshotai/kimi-k2.5
z-ai/glm-5-turbo
openai/gpt-5.2-codex
qwen/qwen3-coder-next
qwen/qwen3.5-122b-a10b
```

Defaults:

- model: `opencode/deepseek-v4-flash-free`;
- small model: `opencode/gpt-5-nano`.

API key подключается синтаксисом `{file:absolute-path}` и не хранится в Git.

## 3. BMAD

BMAD опционален и устанавливается отдельно в каждый проект:

```powershell
.\install_bmad_windows.ps1 C:\path\to\project
```

```bash
./install_bmad_linux.sh /path/to/project
```

Оба wrapper запускают эквивалент официальной команды:

```text
npx --yes bmad-method@6.8.0 install --directory <project> --modules bmm --tools opencode --yes
```

Результат:

- `<project>/_bmad` содержит runtime, конфигурацию и manifest;
- `<project>/.agents/skills` содержит 44 OpenCode-compatible skills;
- `<project>/.opencode/commands` может содержать сгенерированные команды интеграции;
- OpenCode обнаруживает `.agents/skills` автоматически.

`~/.agents/skills` является внешним глобальным каталогом skills, который OpenCode также автоматически читает. Для BMAD 6.8.0 одного глобального каталога недостаточно: workflows ссылаются на `<project>/_bmad`. Поэтому этот репозиторий не копирует BMAD глобально.

### Безопасность повторной установки

- Перед запуском проверяется npm integrity фиксированной версии.
- Если найден BMAD другой версии, установка останавливается без изменений.
- Если найдены `bmad-*` skills без управляемого manifest, установка останавливается без изменений.
- Для существующей управляемой версии 6.8.0 официальный installer выполняет quick-update и сохраняет свои customization-файлы.
- После установки `validate_bmad.js` сравнивает manifest и файловую систему с 44 IDs из `config_data.json`.

## 4. Проверка

```powershell
.\validate_setup.ps1
```

```bash
./validate_setup.sh
```

Проверки выполняются во временном target и не используют рабочую конфигурацию. Полная BMAD-проверка требует доступа к npm registry и Node.js 20.12+.

GitHub Actions запускает обе проверки полностью на `windows-latest` и `ubuntu-latest`. Это является эталонной Linux runtime-проверкой, включая права `0600`.

Ручная BMAD validation:

```text
node validate_bmad.js <project-path>
```

После установки или изменения config полностью завершите и заново запустите OpenCode: конфигурация и skills загружаются при старте.
