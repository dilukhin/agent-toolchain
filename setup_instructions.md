# Инструкция по настройке Opencode для разработчика

Эта инструкция описывает все необходимые шаги для настройки Opencode с кастомизациями, отличными от чистой установки. Подходит для систем Windows и Debian/Linux.

---

## 1. Установка Opencode

### Windows
```powershell
npm install -g @opencode-ai/cli
```

### Debian/Ubuntu Linux
```bash
sudo npm install -g @opencode-ai/cli
```

Альтернативно — используйте `npx @opencode-ai/cli` без глобальной установки.

---

## 2. Настройка провайдера моделей (RouterAI)

### 2.1. Получение API-ключа
1. Зарегистрируйтесь на https://routerai.ru
2. Получите API-ключ в личном кабинете
3. Сохраните ключ в безопасное место

### 2.2. Создание файла конфигурации
Создайте файл `~/.config/opencode/opencode.jsonc` (или `%APPDATA%\opencode\opencode.jsonc` на Windows):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "routerai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "RouterAI",
      "options": {
        "baseURL": "https://routerai.ru/api/v1",
        "apiKey": "{file:ПУТЬ_К_ФАЙЛУ_С_КЛЮЧОМ}"
      },
      "models": {
        // OpenAI GPT-4o — дорогая универсальная модель
        "openai/gpt-4o": {
          "name": "OpenAI GPT-4o [дорогая универсальная]",
          "description": "Дорогая универсальная модель, запасной вариант для сложных задач."
        },
        // OpenAI GPT-4o mini — быстрая и дешёвая для простых задач
        "openai/gpt-4o-mini": {
          "name": "OpenAI GPT-4o mini [дешёвая быстрая]"
        },
        // DeepSeek Chat — недорогая модель для анализа кода
        "deepseek/deepseek-chat": {
          "name": "DeepSeek Chat [дешёвая code/chat]"
        },
        // Anthropic Claude Opus 4.7 — топовая модель для сложного агентного программирования
        "anthropic/claude-opus-4.7": {
          "name": "Anthropic Claude Opus 4.7 [топ код/агент]"
        },
        // Qwen 3.6 Plus — основная рабочая модель
        "qwen/qwen3.6-plus": {
          "name": "Qwen 3.6 Plus [основная]"
        },
        // DeepSeek V4 Pro — сильная reasoning модель
        "deepseek/deepseek-v4-pro": {
          "name": "DeepSeek V4 Pro [reasoning]"
        },
        // MiniMax M2.7 — дешёвая рабочая модель
        "minimax/minimax-m2.7": {
          "name": "MiniMax M2.7 [дешёвая]"
        },
        // GLM-5 — архитектор/ревьюер
        "z-ai/glm-5": {
          "name": "GLM-5 [архитектор]"
        },
        // Kimi K2.5 — запасная агентная
        "moonshotai/kimi-k2.5": {
          "name": "Kimi K2.5 [агентная]"
        },
        // GPT-5.2-Codex — оптимизированная для инженерии
        "openai/gpt-5.2-codex": {
          "name": "GPT-5.2-Codex [код/агент]"
        },
        // Qwen 3 Coder Next — новая модель для кодинга
        "qwen/qwen3-coder-next": {
          "name": "Qwen 3 Coder Next [код/агент]"
        },
        // Qwen 3.5 122B A10B — мультимодальная reasoning модель
        "qwen/qwen3.5-122b-a10b": {
          "name": "Qwen 3.5 122B A10B [мультимод. reasoning]"
        }
      }
    }
  },
  "model": "opencode/deepseek-v4-flash-free",
  "small_model": "opencode/gpt-5-nano"
}
```

**Важно:** Замените `ПУТЬ_К_ФАЙЛУ_С_КЛЮЧОМ` на реальный путь к файлу с API-ключом. 

API-ключ должен храниться в отдельном файле для безопасности. Рекомендуемое расположение:

**Linux/Debian:**
- Путь: `/home/<username>/projects/stash/opencode.ai/api-key.txt`
- Создание: `mkdir -p ~/projects/stash/opencode.ai`  
- В конфигурации: `{file:/home/<username>/projects/stash/opencode.ai/api-key.txt}`

**Windows:**
- Путь: `C:\Users\<username>\projects\stash\opencode.ai\api-key.txt`
- В конфигурации: `{file:C:\Users\<username>\projects\stash\opencode.ai\api-key.txt}`

Где `<username>` и `<YourUser>` — имя вашей учётной записи.

---

## 3. Установка BMAD-скиллов

BMAD (Бизнес-MODELLing And Development) — набор специальных агентов и навыков для разработки.

### Список установленных скиллов (всего 42):

| № | Название | Описание |
|---|----------|----------|
| 1 | bmad-advanced-elicitation | Углублённый анализ требований (socratic, first principles, red team) |
| 2 | bmad-agent-analyst | Бизнес-аналитик (Mary) |
| 3 | bmad-agent-architect | Системный архитектор (Winston) |
| 4 | bmad-agent-dev | Разработчик (Amelia) |
| 5 | bmad-agent-pm | Продакт-менеджер (John) |
| 6 | bmad-agent-tech-writer | Технический писатель (Paige) |
| 7 | bmad-agent-ux-designer | UX-дизайнер (Sally) |
| 8 | bmad-brainstorming | Мозговой штурм |
| 9 | bmad-check-implementation-readiness | Проверка готовности к реализации |
| 10 | bmad-checkpoint-preview | Ревью изменений с человеком в контуре |
| 11 | bmad-code-review | Адверсариальный ревью кода |
| 12 | bmad-correct-course | Управление изменениями во время спринта |
| 13 | bmad-create-architecture | Создание архитектурных решений |
| 14 | bmad-create-epics-and-stories | Декомпозиция на эпики и истории |
| 15 | bmad-create-prd | Создание PRD (deprecated) |
| 16 | bmad-create-story | Создание истории пользователя |
| 17 | bmad-customize | Кастомизация скиллов |
| 18 | bmad-dev-story | Реализация истории |
| 19 | bmad-document-project | Документирование проекта |
| 20 | bmad-domain-research | Исследование предметной области |
| 21 | bmad-edit-prd | Редактирование PRD (deprecated) |
| 22 | bmad-editorial-review-prose | Лингвистическая редакция текста |
| 23 | bmad-editorial-review-structure | Структурная редакция |
| 24 | bmad-generate-project-context | Генерация project-context.md |
| 25 | bmad-help | Помощь и навигация по BMAD |
| 26 | bmad-index-docs | Создание индекса документов |
| 27 | bmad-investigate | Расследование багов и инцидентов |
| 28 | bmad-market-research | Маркетинговые исследования |
| 29 | bmad-party-mode | Multi-agent conversation |
| 30 | bmad-prd | Создание/обновление PRD |
| 31 | bmad-prfaq | Working Backwards PRFAQ challenge |
| 32 | bmad-product-brief | Создание product brief |
| 33 | bmad-qa-generate-e2e-tests | Генерация E2E тестов |
| 34 | bmad-quick-dev | Быстрая реализация кода |
| 35 | bmad-retrospective | Ретроспектива спринта |
| 36 | bmad-review-adversarial-general | Критическая ревизия |
| 37 | bmad-review-edge-case-hunter | Поиск крайних случаев |
| 38 | bmad-shard-doc | Разбиение больших документов |
| 39 | bmad-spec | Формализация спецификаций |
| 40 | bmad-sprint-planning | Планирование спринта |
| 41 | bmad-sprint-status | Статус спринта |
| 42 | bmad-technical-research | Технические исследования |
| 43 | bmad-ux | UX-планирование |
| 44 | bmad-validate-prd | Валидация PRD (deprecated) |

### Установка скиллов

#### Автоматический способ (рекомендуется):
```bash
cd ~/.config/opencode
npm install @opencode-ai/plugin
```

Проверьте `package.json`:
```json
{
  "dependencies": {
    "@opencode-ai/plugin": "1.15.4"
  }
}
```

#### Ручной способ:
Скачайте каждый скилл из официального репозитория или добавьте через интерфейс Opencode. Скиллы должны находиться в папке `~/.agents/skills/`.

---

## 4. Глобальная память (AGENTS.md)

Создайте файл `~/.config/opencode/AGENTS.md` со следующим содержимым:

```markdown
# Глобальная память

Этот файл — моя глобальная память. Я читаю его в начале каждой сессии и могу дополнять в процессе работы. Здесь хранятся общие факты, настройки, ссылки на проекты и инструменты, которые должны быть доступны во всех сессиях.

## Формат

- Каждая запись — краткий факт.
- Можно группировать по категориям.

## Сохранённые факты

### Маппинг Linux-путей → Windows (WSL)
- `/mnt/c/Users/<username>/...` → `c:\Users\<username>\...`
- `/mnt/d/...` → `d:\...`
- Общее правило: `/mnt/<буква_диска>/...` → `<буква_диска>:\...`

## Проекты

### ssh_relay
- **Назначение:** локальный SSH-relay для выполнения коротких неинтерактивных команд через одну или несколько именованных SSH-сессий с парольной/ключевой аутентификацией и опциональным sudo.
- **Расположение:** `~/projects/ssh_relay/` (оригинальный репозиторий)
- **GitHub:** https://github.com/dilukhin/ssh_relay.git
- **Файл:** `ssh_relay.py` (там же)
- **Версия:** 0.5.0
- **Зависимости:** Python 3.12+, paramiko
- **Команды:**
  - `py ssh_relay.py daemon --host HOST --user USER [--port PORT] [-i KEY] [--known-hosts PATH] [--command-timeout SECONDS] [--enable-sudo]` — запуск daemon
  - `py ssh_relay.py daemon --name NAME --host HOST --user USER ...` — именованная сессия
  - `py ssh_relay.py exec [--name NAME] "COMMAND"` — выполнить команду
  - `py ssh_relay.py sudo-exec [--name NAME] "COMMAND"` — выполнить через sudo
  - `py ssh_relay.py download [--name NAME] [--overwrite] [--create-dirs] REMOTE_PATH LOCAL_PATH` — скачать файл
  - `py ssh_relay.py upload [--name NAME] [--overwrite] [--create-dirs] LOCAL_PATH REMOTE_PATH` — загрузить файл
  - `py ssh_relay.py status [--name NAME] [--all]` — проверить статус
  - `py ssh_relay.py list` — список всех известных сессий
  - `py ssh_relay.py stop [--name NAME] [--all]` — остановить relay
- **Session-файлы:** `%LOCALAPPDATA%\ssh_relay\sessions\<name>.json` (Windows) или `~/.local/share/ssh_relay/sessions/<name>.json` (Linux)
- **Правила использования:**
  - Не использовать прямой SSH, не запрашивать пароль
  - Перед работой проверить статус и базовую команду
  - Не запускать интерактивные команды, редакторы, top, less, passwd
  - Не передавать каталоги (только обычные файлы), не использовать рекурсивное копирование
  - Лимиты: 4МиБ вывод команды, 64МиБ файл, 120с команда, 300с transfer
  - Если relay не запущен — сообщить пользователю и попросить запустить daemon вручную

### bundle
- **Назначение:** сборщик файлов в единый Markdown-бандл (base64 для бинарных файлов).
- **Расположение:** `~/projects/bundle/`
- **Скрипт:** `bundle.py`
- **GitHub:** https://github.com/dilukhin/bundle.git
- **Как делать бандл:**
  - `python bundle.py <root_dir> -p "<patterns>" -o <output.md>` — создать бандл
  - `python bundle.py <root_dir> -p "<patterns>" -a <output.md>` — добавить в существующий
  - Для точных путей (с `/` в шаблоне) файлы резолвятся относительно root
  - Для glob-масок (без `/`) — fnmatch по имени файла
  - Бинарные файлы (PDF, DOCX, XLSX, DOC) автоматически кодируются в base64
  - Поддерживает `--ignore`, `--encoding`, `--paths-only`, `--no-binary-backup`, `--patterns-file`

## Важные настройки окружения

### Кодировка кириллицы (PowerShell)
PowerShell 5.1 по умолчанию выводит кириллицу в CP866, из-за чего opencode отображает кракозябры.
**Фикс:** перед каждым вызовом bash-команд, которые могут выводить кириллицу, добавлять в начало команды:
```powershell
$env:PYTHONIOENCODING = 'utf-8'; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8;
```
- `$env:PYTHONIOENCODING` — чтобы Python выводил UTF-8
- `[Console]::OutputEncoding` — чтобы PowerShell читал UTF-8 от native-команд

# Global OpenCode Agent Rules

* Do not loop on the same tool call or the same file. Read the same file at most once unless the user explicitly asks to reread it.
* Do not read zero-length files. Mention them as empty and continue.
* Do not scan `node_modules`, `.git`, build directories, generated output directories, caches, logs, or temporary files unless explicitly requested.
* Stop discovery after 3 consecutive tool actions that add no new information. Summarize what is known and proceed.
* Prefer a useful partial result over exhausting tokens, or balance.
* For broad analysis tasks, first create a short plan and a bounded file list, then read only representative files.
* Save progress to the requested output file early when the task produces a document.
* Never expose secrets, tokens, passwords, API keys, private URLs, or authorization headers.
* Отвечай на русском, по существу, без воды, структурированно. Язык общения, комментариев и документации — русский.
```

---

## 5. Дополнительные утилиты

Предыдущие настройки включают ссылки на два вспомогательных инструмента. Установите их отдельно при необходимости.

### 5.1. ssh_relay

**Назначение:** Безопасное удалённое выполнение команд через SSH-relay.

**Установка:**
```bash
# Клонируйте репозиторий
git clone https://github.com/dilukhin/ssh_relay.git ~/projects/ssh_relay
cd ~/projects/ssh_relay

# Установка зависимостей (Python 3.12+)
pip install paramiko
```

**GitHub:** https://github.com/dilukhin/ssh_relay.git

**Использование:**
- Запуск daemon: `python ssh_relay.py daemon --host <host> --user <user> --name <session_name>`
- Выполнение команды: `python ssh_relay.py exec --name <session_name> "ls -la"`

### 5.2. bundle.py

**Назначение:** Сбор исходного кода в Markdown-документ для передачи ИИ.

**Установка:**
```bash
git clone https://github.com/dilukhin/bundle.git ~/projects/bundle
chmod +x ~/projects/bundle/bundle.py
```

**Использование:**
```bash
python ~/projects/bundle/bundle.py ./project -p "*.cpp,*.h,*.md" -o output.md
```

---

## 6. Платформенные различия

### Windows-specific настройки
1. Кодировка PowerShell требует фиксов (см. раздел 4)
2. Путь к конфигурации: `%APPDATA%\opencode\` (обычно `C:\Users\<username>\AppData\Roaming\opencode\`)
3. Используйте `py` вместо `python` если установлен py launcher
4. API-ключ: `C:\Users\<username>\projects\stash\opencode.ai\api-key.txt`

### Linux/Debian-specific настройки  
1. Путь к конфигурации: `~/.config/opencode/`
2. Для кириллицы в терминале установите UTF-8: `export LANG=en_US.UTF-8`
3. Сессионные файлы ssh_relay: `~/.local/share/ssh_relay/sessions/`
4. API-ключ: `/home/<username>/projects/stash/opencode.ai/api-key.txt`

---

## 7. Рекомендации по использованию

### Модели для разных задач
- **Быстрые простые задачи:** `openai/gpt-4o-mini`, `minimax/minimax-m2.7`
- **Анализ кода, обычные правки:** `deepseek/deepseek-chat`, `qwen/qwen3.6-plus`
- **Architectural decisions, сложный refactoring:** `anthropic/claude-opus-4.7`, `z-ai/glm-5`
- **Reasoning-intensive tasks:** `deepseek/deepseek-v4-pro`, `qwen/qwen3.5-122b-a10b`

### Краткий чеклист проверки настройки
- [ ] Opencode установлен и запускается
- [ ] API-ключ RouterAI доступен в файле
- [ ] Конфигурация содержит все модели
- [ ] BMAD-скиллы установлены в `~/.agents/skills/` (или на Windows `%APPDATA%\opencode\node_modules`)
- [ ] AGENTS.md создан с глобальной памятью
- [ ] ssh_relay и bundle.py (опционально) установлены и работают

---

## 8. Ссылки

- Официальная документация: https://opencode.ai
- BMAD Framework: https://bmad.dev
- RouterAI API: https://routerai.ru
- Bundle repository: https://github.com/dilukhin/bundle

---

*Инструкция подготовлена для версии Opencode с кастомными настройками.*
