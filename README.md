# opencode_setup

Воспроизводимая настройка OpenCode для Windows и Linux: RouterAI, безопасное внешнее хранение API-ключа и опциональный project-local BMAD.

## Что устанавливается

| Компонент | Версия и область |
|---|---|
| OpenCode CLI | актуальная npm-версия, global |
| `@opencode-ai/plugin` | `1.15.4`, global OpenCode config |
| RouterAI | 13 проверенных model IDs |
| BMAD | `bmad-method@6.8.0`, optional, project-local |

`@opencode-ai/plugin` предоставляет API для разработки плагинов OpenCode. Он не содержит и не устанавливает BMAD.

BMAD устанавливается только официальным `bmad-method@6.8.0`. В каждый выбранный проект создаются связанные каталоги `_bmad/` и `.agents/skills/` с 44 skills. OpenCode автоматически читает project-local `.agents/skills`; глобальная копия BMAD skills без соответствующего `_bmad` не является полноценной установкой.

## Быстрый старт

Windows:

```powershell
.\setup_windows.ps1
.\install_bmad_windows.ps1 C:\path\to\project  # optional
```

Linux:

```bash
./setup_linux.sh
./install_bmad_linux.sh /path/to/project  # optional
```

Setup создает placeholder API-ключа только при отсутствии файла. Существующие `api-key.txt`, `opencode.jsonc` и `AGENTS.md` сохраняются. На Linux ключу всегда назначаются права `0600`.

После изменения конфигурации полностью перезапустите OpenCode.

## BMAD

- Источник: [официальный npm-пакет](https://www.npmjs.com/package/bmad-method).
- Upstream: [bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD).
- Версия: `6.8.0`.
- Upstream revision пакета: `3bcd6c3cce6e381b759e23185b099081496567a5`.
- Модули: `core`, `bmm`.
- Интеграция: `opencode`.
- Skills: 44, точный список хранится в `config_data.json`.

Installers проверяют опубликованный npm integrity, не заменяют неизвестную или другую версию BMAD и после установки запускают `validate_bmad.js`. Повторный запуск для управляемой версии `6.8.0` использует штатный quick-update официального installer.

## Файлы

| Файл | Назначение |
|---|---|
| `setup_windows.ps1`, `setup_linux.sh` | Базовая настройка OpenCode и RouterAI |
| `install_bmad_windows.ps1`, `install_bmad_linux.sh` | Официальная project-local установка BMAD |
| `validate_bmad.js` | Проверка версии, модулей, IDE и 44 IDs |
| `validate_setup.ps1`, `validate_setup.sh` | Безопасные изолированные проверки setup |
| `.github/workflows/validate.yml` | Полная Windows/Linux CI-проверка |
| `config_data.json` | Машиночитаемый эталон моделей и BMAD |
| `setup_instructions.md` | Подробная инструкция и политика повторной установки |
| `bootstrap_prompt.md` | Короткие промпты для выполнения setup агентом |
| `SUMMARY.md` | Аудит исходных расхождений и принятые решения |

## Требования

- Node.js 18+ и npm для базового setup.
- Node.js 20.12+ для BMAD `6.8.0`.
- Аккаунт RouterAI и API-ключ.
- Доступ к npm registry при установке.
