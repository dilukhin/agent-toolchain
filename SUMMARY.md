# Аудит воспроизводимости OpenCode setup

Дата актуализации: 2026-08-11.

Документ фиксирует проверенное состояние, а не содержимое исследованных рабочих проектов.

| Утверждение или настройка | Фактическое состояние | Прежнее состояние репозитория | Правка |
|---|---|---|---|
| Число BMAD skills | 44 в двух независимых локальных установках и глобальном каталоге | Документы говорили 42, список содержал 44 | Везде указано 44; validation сверяет точные IDs |
| Источник BMAD | официальный `bmad-method@6.8.0`, modules `core,bmm`, tool `opencode` | Источник и команда не были зафиксированы | Добавлены wrappers официального npm installer |
| Revision BMAD | npm `gitHead` `3bcd6c3cce6e381b759e23185b099081496567a5` | Не указана | Зафиксирована вместе с npm integrity |
| `@opencode-ai/plugin` | SDK для плагинов; пакет не зависит от BMAD | Ошибочно назывался поддержкой/установкой BMAD | Назначение разделено в scripts и docs |
| Область BMAD | project-local `_bmad` и `.agents/skills` | Смешивались global skills и npm plugin | Реализована только корректная project-local установка |
| OpenCode skills paths | project `.agents/skills`, global `~/.agents/skills` и каталоги OpenCode | Windows `node_modules` ошибочно назывался skills path | Пути исправлены; объяснена зависимость BMAD от `_bmad` |
| RouterAI models | 13 точных IDs подтверждены models endpoint | scripts пропускали `z-ai/glm-5-turbo`; Windows prompt терял `openai/` | Все scripts и data синхронизированы |
| GPT/Codex ID | `openai/gpt-5.2-codex` | В одном месте был `gpt-5.2-codex` | Исправлено |
| Windows config | `%USERPROFILE%\.config\opencode` | Использовался `%APPDATA%\opencode` | Приведено к реально читаемому global path OpenCode |
| API key при повторном setup | должен сохраняться | Безусловно заменялся placeholder | Создается только при отсутствии |
| Linux key mode | `0600` | Основной setup не назначал права | `chmod 600` выполняется всегда |
| Пользовательские configs | должны сохраняться | Безусловно перезаписывались | `opencode.jsonc` и `AGENTS.md` создаются только при отсутствии |
| Bootstrap Bash | должен быть исполняемым | Содержал поврежденные `177:` и `138:` | Ручной фрагмент заменен вызовом проверяемых scripts |

## Проверенные источники

- npm metadata `bmad-method@6.8.0` и его signed integrity/provenance;
- upstream `bmad-code-org/BMAD-METHOD` на revision пакета;
- manifests двух независимых локальных BMAD 6.8.0 installations;
- фактически загружаемые OpenCode global skills;
- npm metadata `@opencode-ai/plugin@1.15.4`;
- текущий RouterAI models endpoint;
- OpenCode config schema и документированные external skills paths.

Имена, пути, содержимое и иные детали исследованных рабочих проектов в репозиторий не переносились.
