# LLM Council MCP-сервер

Отдаёт систему обсуждения [LLM Council](../README.md) в Claude Code, Claude
Desktop и другие MCP-клиенты по stdio (FastMCP).

**Установка, конфигурация и регистрация в клиенте — в [корневом README](../README.md).**
Кратко: `uv sync`, заполнить `.env`, затем `uv run mcp_server/server.py`
(или зарегистрировать через `claude mcp add`).

## Инструменты

| Инструмент | Описание |
|------|-------------|
| `ask_council` | Задать вопрос, получить ответ председателя + `conversation_id`. Параметры: `mode`, `conversation_id`, `clarify_when_unclear`, `include_debug`, `bypass_cache`. Блокирующий вызов — для короткого таймаута клиента см. `start_council_async`. |
| `start_council_async` | Запустить обсуждение в фоне, сразу вернуть `task_id`. Для клиентов с коротким таймаутом на вызов тула. |
| `poll_council_task` | Опросить статус фонового обсуждения по `task_id` (раз в 15-30 секунд). |
| `get_council_eta` | Оценка ожидаемого времени ответа по durable-статистике прошлых прогонов, до запуска совета. |
| `list_conversations` | Список сохранённых обсуждений совета |
| `get_conversation` | Полный ход обсуждения по конкретному `conversation_id` |
| `get_available_models` | Модели совета и председатель в текущем конфиге |
| `get_council_metrics` | Process-local KPI и перцентили латентности по стадиям в JSON |

`ask_council` шлёт MCP progress + heartbeat на границах стадий и поддерживает
отмену (при abort частичный ответ ассистента не сохраняется).

Аудит актуальности этого списка и качества описаний тулов —
`python scripts/audit_mcp_surface.py` (см. `tests/test_mcp_surface.py`).

## Архитектура

Сервер импортирует бэкенд напрямую, поэтому поведение совпадает с веб-UI:

- `backend/council.py` — оркестрация совета (стадии 0–3, quick/deep, роутинг)
- `backend/openrouter.py` — клиент провайдера OpenRouter
- `backend/storage.py` — хранение диалогов в SQLite
- `backend/metrics.py` — rolling KPI в памяти
- `backend/config.py` — конфигурация моделей и провайдера
