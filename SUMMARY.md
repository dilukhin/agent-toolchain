# Отчёт о настройке Opencode

## Дата создания отчёта
2026-07-01

## Проанализировано

### Текущие настройки Opencode (отличия от чистой установки)

#### 1. Provider и модели

**Провайдер:** RouterAI (кастомный)
- npm package: @ai-sdk/openai-compatible
- Base URL: https://routerai.ru/api/v1
- API ключ хранится во внешнем файле: `/home/<username>/projects/stash/opencode.ai/api-key.txt` (Linux) или `C:\Users\<username>\projects\stash\opencode.ai\api-key.txt` (Windows)

**Зарегистрированные модели (13 шт):**

| Модель | Назначение | Цена (₽/1M токенов) |
|--------|------------|---------------------|
| openai/gpt-4o | Дорогая универсальная | 264 / 1056 |
| openai/gpt-4o-mini | Дешёвая быстрая | 13 / 55 |
| deepseek/deepseek-chat | Code/chat | 29 / 82 |
| anthropic/claude-opus-4.7 | Топ код/агент | 462 / 2314 |
| qwen/qwen3.6-plus | Основная рабочая | 30 / 180 |
| deepseek/deepseek-v4-pro | Reasoning | 61 / 122 |
| minimax/minimax-m2.7 | Дешёвая рабочая | 25 / 111 |
| z-ai/glm-5 | Архитектор/ревьюер | 55 / 177 |
| moonshotai/kimi-k2.5 | Агентная fallback | 37 / 175 |
| z-ai/glm-5-turbo | Дорогой fallback | 111 / 370 |
| openai/gpt-5.2-codex | Код/агент | 162 / 1296 |
| qwen/qwen3-coder-next | Код/агент (новая) | 10 / 74 |
| qwen/qwen3.5-122b-a10b | Мультимодальная reasoning | 25 / 200 |

По умолчанию: `opencode/deepseek-v4-flash-free`
Small model: `opencode/gpt-5-nano`

#### 2. BMAD-скиллы (42 шт.)

Установлен пакет: `@opencode-ai/plugin` версии 1.15.4

**Категории агентов:**
- Business Analyst: bmad-agent-analyst (Mary)
- System Architect: bmad-agent-architect (Winston)
- Developer: bmad-agent-dev (Amelia)
- Product Manager: bmad-agent-pm (John)
- Technical Writer: bmad-agent-tech-writer (Paige)
- UX Designer: bmad-agent-ux-designer (Sally)

**Основные навыки:**
- Requirements & Specifications: advanced-elicitation, prd, prfaq, spec, product-brief
- Planning & Management: sprint-planning, sprint-status, correct-course, retrospective
- Development: quick-dev, dev-story, create-story, create-architecture
- Quality Assurance: code-review, review-adversarial-general, edge-case-hunter, qa-generate-e2e-tests
- Research: domain-research, market-research, technical-research, investigate
- Documentation: document-project, generate-project-context, index-docs, editorial-review-*
- Collaboration: party-mode, brainstorming, help, customize

#### 3. Глобальная память (AGENTS.md)

Содержит:
- Маппинг путей WSL ↔ Windows
- Описание вспомогательных инструментов (ssh_relay, bundle)
- Правила работы агента на русском языке
- Ограничения по безопасности (не экспонировать секреты)

#### 4. Вспомогательные инструменты

**ssh_relay (v0.5.0)**
- GitHub: https://github.com/dilukhin/ssh_relay.git
- Python скрипт для SSH-relay управления командами  
- Зависимость: paramiko
- Сеансы: %LOCALAPPDATA%\ssh_relay\sessions\<name>.json (Windows) или ~/.local/share/ssh_relay/sessions/ (Linux)

**bundle.py**
- GitHub: https://github.com/dilukhin/bundle.git
- Скрипт сборки файлов в Markdown
- Поддерживает base64 для бинарных файлов

#### 5. Платформенные особенности

Windows-specific:
- PowerShell кодировка требует фиксов для кириллицы
- Config directory: %APPDATA%\opencode
- Agents directory: %USERPROFILE%\.agents

---

## Созданные файлы настройки

Расположение: `D:\projects\opencode_setup\`

| Файл | Размер | Описание |
|------|--------|----------|
| README.md | 6.4 KB | Краткое руководство по использованию |
| setup_instructions.md | 18.3 KB | Подробная инструкция для ручной настройки |
| bootstrap_prompt.md | ~7 KB | Промпт для автоматической настройки через opencode |
| setup_windows.ps1 | 4.7 KB | PowerShell скрипт для Windows |
| setup_linux.sh | 3.3 KB | Bash скрипт для Linux/Debian |
| config_data.json | 6.1 KB | Структурированные данные конфигурации |

---

## Ключевые отличия от стандартной настройки

1. **RouterAI провайдер вместо OpenCode default** — возможность использовать множество моделей из разных провайдеров через один API с прозрачным ценообразованием

2. **Внешнее хранение API-ключей** — безопасность через `{file:PATH}` синтаксис

3. **42 BMAD-скилла** — полноценный набор ролей и процессов разработки ПО

4. **Глобальная память проекта** — кросс-сессийное состояние агента

5. **Платформенные адаптации** — поддержка как Windows, так и Linux

6. **Русскоязычные правила** — работа с кириллицей и инструкции на русском

---

## Что потребуется настроить новому разработчику

### Обязательно

1. **Аккаунт RouterAI** — зарегистрироваться на https://routerai.ru
2. **API-ключ** — создать и сохранить в файле согласно путям для ОС
3. **Установка CLI** — глобальный пакет @opencode-ai/cli

### По желанию/потребности

1. **BMAD-скиллы** — если используются workflows BMAD
2. **ssh_relay** — для удалённых серверов
3. **bundle.py** — для передачи кода в ИИ

---

## Рекомендации по развёртыванию

**Автоматический способ:**
1. Запустите opencode (любым доступным способом)
2. Вставьте промпт из `bootstrap_prompt.md` как первый запрос
3. Агент автоматически создаст все конфигурации
4. Добавьте API-ключ в созданный файл `api-key.txt`

**Ручной способ:**
1. Используйте соответствующий скрипт (`setup_windows.ps1` или `setup_linux.sh`)
2. Проверьте наличие Node.js 18+ и npm
3. После установки добавьте API-ключ вручную
4. Протестируйте работу командой: `opencode "Привет!"`
5. Для Linux установите bash-скрипт исполняемым: `chmod +x setup_linux.sh`

---

## Контакты и ресурсы

- Документация Opencode: https://opencode.ai
- BMAD: https://bmad.dev
- RouterAI: https://routerai.ru
- Bundle: https://github.com/dilukhin/bundle.git

---

*Отчёт подготовлен автоматически 2026-07-01.*
