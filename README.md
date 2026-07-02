# LLM Council — MCP-сервер

MCP-сервер (Model Context Protocol), который позволяет Claude Code / Claude
Desktop спрашивать не одну модель, а **совет из нескольких LLM**. Вопрос
параллельно уходит нескольким моделям, они анонимно ранжируют ответы друг друга,
а модель-председатель (chairman) синтезирует финальный ответ.

На выходе — один, лучше проверенный ответ. При этом весь ход обсуждения
(ответы моделей, peer-ранжирования, сигнал уверенности) доступен для разбора.

![llm-council в работе: вопрос уходит совету из нескольких LLM (Stage 1), модели анонимно ранжируют ответы друг друга как Response A–F (Stage 2), при расхождении поднимается сигнал низкой уверенности, затем председатель из другой семьи моделей синтезирует финал с атрибуцией [A]/[B] (Stage 3)](docs/demo.gif)

---

## Быстрый старт (≈3 минуты)

**Требования:** [uv](https://docs.astral.sh/uv/) (`brew install uv`), Python ≥ 3.10,
ключ [OpenRouter](https://openrouter.ai/keys).

```bash
# 1. Получить код
git clone https://github.com/azalio/llm-council.git
cd llm-council

# 2. Поставить зависимости (MCP-сервер ставится сюда же)
uv sync

# 3. Прописать доступы
cp .env.example .env
#    дальше отредактировать .env — задать ключ:
#      OPENROUTER_API_KEY=...

# 4. Проверить, что сервер стартует (Ctrl-C чтобы остановить)
uv run mcp_server/server.py
```

Дальше зарегистрировать сервер в MCP-клиенте (ниже) и спросить:
*«Спроси совет: …»*.

> Сервер читает `.env` из корня проекта, поэтому после заполнения `.env`
> токен в конфиге MCP-клиента повторять не нужно.

---

## Регистрация в MCP-клиенте

### Claude Code

Одной командой (user scope):

```bash
claude mcp add llm-council -- uv --directory /path/to/llm-council run mcp_server/server.py
```

…либо вручную в `~/.claude.json` / проектный `.mcp.json`:

```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uv",
      "args": ["--directory", "/path/to/llm-council", "run", "mcp_server/server.py"]
    }
  }
}
```

### Claude Desktop

Добавить в `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uv",
      "args": ["--directory", "/path/to/llm-council", "run", "mcp_server/server.py"]
    }
  }
}
```

Если не хочешь использовать `.env`, передай доступы прямо в конфиге:

```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uv",
      "args": ["--directory", "/path/to/llm-council", "run", "mcp_server/server.py"],
      "env": { "OPENROUTER_API_KEY": "sk-or-v1-..." }
    }
  }
}
```

`/path/to/llm-council` заменить на свой путь до клонированного репозитория.

### Codex

Добавить в `~/.codex/config.toml`:

```toml
[mcp_servers.llm-council]
command = "uv"
args = ["--directory", "/path/to/llm-council", "run", "mcp_server/server.py"]
startup_timeout_sec = 60
tool_timeout_sec = 3600          # совет идёт долго — дефолтного таймаута Codex не хватит

[mcp_servers.llm-council.env]
OPENROUTER_API_KEY = "sk-or-v1-..."
```

Без `tool_timeout_sec` Codex оборвёт вызов раньше, чем совет успеет ответить.
Альтернатива апу таймаута — не блокировать вызов и опрашивать результат через
`start_council_async` + `poll_council_task` (см. «Инструменты»).

### OpenCode

Добавить запись в блок `"mcp"` файла `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "llm-council": {
      "type": "local",
      "command": ["uv", "--directory", "/path/to/llm-council", "run", "mcp_server/server.py"],
      "enabled": true,
      "environment": { "OPENROUTER_API_KEY": "sk-or-v1-..." }
    }
  }
}
```

`command` в OpenCode — это массив (исполняемый файл + аргументы), доступы передаются
через `environment`. Если в репозитории заполнен `.env`, ключи в `environment` можно
не дублировать.

---

## Инструменты

| Инструмент | Описание |
|------|-------------|
| `ask_council` | Задать вопрос, получить синтезированный ответ председателя. Возвращает `conversation_id` — передай его обратно для многоходового диалога. |
| `start_council_async` / `poll_council_task` | Запустить совет в фоне и опрашивать результат — для клиентов с коротким таймаутом вызова (например, Codex). `start_council_async` возвращает `task_id`, дальше `poll_council_task(task_id)` каждые 15–30 с, пока `status` не станет `done`/`error`. |
| `list_conversations` | Список сохранённых обсуждений совета |
| `get_conversation` | Полный ход обсуждения по конкретному `conversation_id` |
| `get_available_models` | Список моделей совета и председателя в текущем конфиге |
| `get_council_metrics` | Process-local KPI (success rate, деградация, перцентили латентности по стадиям) в JSON |

### Параметры `ask_council`

- `mode="auto"` (по умолчанию) — оркестратор сам выбирает **quick**
  (только председатель для простых вопросов), **standard** (обычный совет из
  3 стадий) или **deep** (плюс раунд критики/правок). `thorough=true` —
  устаревший алиас для deep.
- `conversation_id="..."` — продолжить предыдущий диалог (многоходовость).
- `clarify_when_unclear=true` — на неоднозначный первый вопрос вернуть один
  уточняющий вопрос, не запуская весь совет.
- `include_debug=true` — добавить к ответу диагностику (тайминги стадий,
  число упавших моделей).
- `bypass_cache=true` — пропустить кэш ответов первого хода.

Долгие вызовы `ask_council` шлют MCP progress + heartbeat, так что клиент видит
активность, а не «зависание». Если у клиента короткий таймаут вызова инструмента
(как у Codex по умолчанию) — либо подними его в конфиге (`tool_timeout_sec`), либо
используй `start_council_async` + `poll_council_task` вместо `ask_council`.

---

## Конфигурация

| Переменная | Обязательна | По умолчанию | Назначение |
|----------|----------|---------|---------|
| `API_PROVIDER` | нет | `openrouter` | единственный зарегистрированный провайдер |
| `OPENROUTER_API_KEY` | да | — | ключ OpenRouter |
| `COUNCIL_TIMEOUT_SECONDS` | нет | `3600` | бюджет времени на полный совет |
| `LLM_COUNCIL_ROOT` | нет | корень репо | корень для SQLite-хранилища `data/` |

Модели совета и председатель заданы в `backend/config.py`. Председатель должен
быть из другого provider-family, чем каждый активный член совета — иначе импорт
конфига падает на старте (fail-fast).

`API_PROVIDER` резолвится через небольшой provider registry
(`backend/providers/registry.py`), а не через захардкоженный `if`: сейчас в
нём зарегистрирован только `openrouter`. Чтобы добавить нового провайдера —
реализуйте `backend/providers/<name>.py` по протоколу `Provider`
(`backend/providers/base.py`: `build_request`, `parse_response`,
`resolve_auth`) и зарегистрируйте загрузчик в `PROVIDER_REGISTRY`.

---

## Опционально: веб-UI

FastAPI-бэкенд + React-фронтенд показывают тот же совет с наглядным разбором
(ответы моделей, сырые ранжирования, баннеры уверенности):

```bash
./start.sh         # backend на :8001, frontend на :5173
```

---

## Основано на

Форк [karpathy/llm-council](https://github.com/karpathy/llm-council) с
добавленными MCP-сервером, многоходовыми диалогами, режимами quick/deep,
адаптивным роутингом и кэшем ответов.

## Лицензия

[MIT](LICENSE)
