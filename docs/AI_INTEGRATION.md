# Luna, EditorTeam и SVG-инфографика

По умолчанию бот использует серверный `agent-codex` через AG-UI. Модель и
уровень рассуждения передаются серверу, но OAuth-сессия ChatGPT остаётся внутри
App Server и никогда не монтируется в контейнер бота.

## Настройки

```dotenv
AI_PROVIDER=app_server
APP_SERVER_URL=http://agent-codex:4202/ag-ui
APP_SERVER_TOKEN=
APP_SERVER_MODEL=gpt-5.6-luna
APP_SERVER_REASONING_EFFORT=xhigh
AI_TIMEOUT_SECONDS=240

# Manacost EditorTeam. Пустое значение отключает второй редакторский проход.
EDITOR_URL=http://editor-gateway:8080/v2/edit
EDITOR_TOKEN=
```

`APP_SERVER_TOKEN` должен совпадать с внутренним токеном `agent-codex`.
Секрет передаётся только заголовком `X-OpenBot-Agent-Token`. Если EditorTeam
защищён прокси, `EDITOR_TOKEN` передаётся как Bearer-токен.

Для временного возврата на старый адаптер:

```dotenv
AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
```

## Docker-сеть Manacost

`agent-codex` и `editor-gateway` не публикуются наружу. Подключите бота к уже
существующей внутренней сети их Compose-проекта:

```bash
MANACOST_DOCKER_NETWORK=<имя-compose-проекта>_app \
docker compose -f docker-compose.yml -f docker-compose.manacost.yml up -d --build
```

Точное имя видно в `docker network ls`. Не публикуйте порты AG-UI и EditorTeam
на `0.0.0.0`.

## Контроль качества

- App Server обязан вернуть JSON, соответствующий ожидаемой схеме.
- Перевод сохраняет все URL, числа и inline-code исходника; иначе черновик
  отклоняется до публикации.
- EditorTeam работает в профиле `news`. Его версия принимается только при
  `accepted=true` и `checks_complete=true`; иначе остаётся текст Luna.
- Luna может вернуть спецификацию из 2–4 точных фактов. Значения сверяются с
  исходником, после чего бот строит самодостаточный SVG без внешних ресурсов и
  конвертирует его в PNG через `rsvg-convert` для Telegram.

## OAuth-вариант

`openai-oauth` можно запустить как отдельный локальный OpenAI-совместимый
прокси, однако это неофициальный компонент. В текущей схеме он не нужен:
`agent-codex` уже владеет серверной ChatGPT-сессией. Не монтируйте
`~/.codex/auth.json` в `newsbot` и не передавайте OAuth refresh token через
`.env` бота.
