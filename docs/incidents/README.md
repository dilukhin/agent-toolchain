# Инциденты agent-toolchain

Здесь хранятся краткие технические отчёты о подтверждённых operational incidents, полезные для диагностики повторных проявлений.

Incident-документы не являются автоматически нормативной policy и не доказывают наличие системного дефекта installer/reconciler. Для изменения runtime или installation algorithm требуется отдельное evidence и regression coverage.

## 2026

- [2026-09-12: protected ACL у managed core на Windows](2026-09-12-managed-core-protected-acl-windows_ru.md) — доступ к intact managed core был заблокирован protected ACL с отключённым inheritance; operationally resolved, систематическая воспроизводимость installer defect не доказана.
- [2026-11-22: `bootstrap_core.py --help` опубликовал локальный core](2026-11-22-bootstrap-core-help-installed-local-core_ru.md) — отсутствие проверки argv и игнорирование аргументов wrapper’ами; локальный выпуск временно сохранён, предложен аудит других mutating CLI.
