# opencode_setup

**Воспроизводимая настройка [Opencode](https://opencode.ai) с кастомным провайдером моделей RouterAI, BMAD-скиллами и вспомогательными утилитами.**

## О проекте

Репозиторий содержит конфигурацию, скрипты и промпты для автоматизированной и воспроизводимой настройки [Opencode](https://opencode.ai) — AI-агента для разработки.

В состав входят:

- 🔌 **Кастомный провайдер RouterAI** — 13+ моделей через единый API: Qwen, DeepSeek, GPT, Claude, GLM, Kimi, MiniMax и другие.
- 🤖 **42 BMAD-скилла** — агенты Analyst, Architect, Developer, PM, Tech Writer, UX и навыки для полного цикла разработки: code review, sprint planning, PRD, investigation, brainstorming и т.д.
- 🧠 **Глобальная память** — `AGENTS.md` с правилами агента на русском языке, настройками безопасности, ограничениями и маппингом путей WSL.
- 🛠 **Скрипты автоматизации под Windows и Linux** — PowerShell для Windows, Bash для Debian/Ubuntu.
- ⚡ **Bootstrap-промпт** — готовый первый запрос для opencode, чтобы агент мог выполнить настройку сам.
- 🔧 **Вспомогательные утилиты** — `ssh_relay` и `bundle.py`.

## Файлы

| Файл | Назначение |
|------|------------|
| `setup_windows.ps1` | Автоустановка для Windows через PowerShell |
| `setup_linux.sh` | Автоустановка для Linux/Debian/Ubuntu через Bash |
| `bootstrap_prompt.md` | Промпт для настройки через opencode |
| `setup_instructions.md` | Подробная пошаговая инструкция |
| `config_data.json` | Данные конфигурации: модели, пути и зависимости |
| `SUMMARY.md` | Отчёт об отличиях от чистой установки |

## Быстрый старт

### Windows

```powershell
.\setup_windows.ps1
```

После установки вставьте API-ключ RouterAI в файл:

```text
%USERPROFILE%\projects\stash\opencode.ai\api-key.txt
```

### Linux / Debian / Ubuntu

```bash
chmod +x setup_linux.sh
./setup_linux.sh
```

После установки вставьте API-ключ RouterAI в файл:

```text
~/projects/stash/opencode.ai/api-key.txt
```

### Настройка через opencode

Можно не запускать установочные скрипты вручную. Скопируйте подходящую секцию из `bootstrap_prompt.md` и вставьте её как первый запрос к opencode.

## Требования

- **Node.js 18+** и **npm**
- **Python 3.12+** — опционально, нужен для вспомогательных утилит `ssh_relay` и `bundle.py`
- **Аккаунт [RouterAI](https://routerai.ru)** и API-ключ из личного кабинета

## Что добавлено к чистой установке Opencode

| Отличие | Детали |
|---------|--------|
| Провайдер моделей | RouterAI (`https://routerai.ru/api/v1`), API-ключ хранится во внешнем файле |
| Модели | 13+ моделей: Qwen, DeepSeek, GPT, Claude, GLM, Kimi, MiniMax и другие |
| BMAD-скиллы | 42 скилла: агенты и прикладные навыки для анализа, проектирования, разработки и документации |
| Глобальная память | `AGENTS.md` на русском языке, маппинг WSL-путей, правила безопасности и ограничения |
| Утилиты | [`ssh_relay`](https://github.com/dilukhin/ssh_relay.git), [`bundle.py`](https://github.com/dilukhin/bundle.git) |

## Ссылки

- [Opencode](https://opencode.ai)
- [RouterAI](https://routerai.ru)
- [ssh_relay](https://github.com/dilukhin/ssh_relay.git)
- [bundle.py](https://github.com/dilukhin/bundle.git)
