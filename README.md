<div align="center">

# 🤖 RuNeuroNews Bot

### Telegram-агрегатор новостей про ИИ с AI-фильтрацией и рерайтом

Парсит десятки каналов через Telethon, отбирает важное через Gemini,
переписывает в живом стиле, публикует в целевой канал —
с premium-эмодзи, медиа, дедупликацией и еженедельным дайджестом.

<br>

[![Python](https://img.shields.io/badge/python-3.12-blue.svg?logo=python&logoColor=white)](https://www.python.org)
[![Docker](https://img.shields.io/badge/Docker-OrbStack-2496ED?logo=docker&logoColor=white)](https://www.docker.com)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-009999?logo=telegram&logoColor=white)](https://aiogram.dev)
[![Telethon](https://img.shields.io/badge/Telethon-1.36+-26A5E4?logo=telegram&logoColor=white)](https://docs.telethon.dev)
[![Gemini](https://img.shields.io/badge/Gemini-3.1%20Flash-8E75B2?logo=googlegemini&logoColor=white)](https://ai.google.dev)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#-лицензия)

</div>

---

## 📋 Содержание

- [Что умеет](#-что-умеет)
- [Архитектура](#-архитектура)
- [Быстрый старт](#-быстрый-старт)
- [Команды бота](#-команды-бота)
- [Ручная публикация по ссылке](#-ручная-публикация-по-ссылке)
- [Premium-эмодзи](#-premium-эмодзи)
- [Еженедельный дайджест](#-еженедельный-дайджест)
- [Структура проекта](#-структура-проекта)
- [Конфигурация](#️-конфигурация)
- [Технологии](#-технологии)
- [Безопасность](#-безопасность)

---

## ✨ Что умеет

| | |
|---|---|
| **🛰️ Real-time мониторинг** | Telethon NewMessage events на любом числе публичных Telegram-каналов |
| **🧠 AI-фильтр** | Gemini оценивает каждый пост: «важно для аудитории?» + «есть уникальный факт?» + важность 1–3 |
| **✍️ Качественный рерайт** | Не пересказ, а пересборка: меняется структура, заменяется канцелярит на живые глаголы |
| **📰 Нативные статьи** | Каждая публикация отправляется как Telegram Rich Message: заголовки, медиа, абзацы, таблицы ключевых чисел и центрированная подписка |
| **🚫 Семантический дедуп** | Gemini сверяет с заголовками за 24 ч — одну новость из 5 каналов опубликует только раз |
| **🔗 Импорт по ссылке** | Пришлите боту t.me / x.com / reddit.com / github.com — превью с 4 кнопками |
| **🎨 Свои карточки** | Pillow рендерит фирменные cards для X / Reddit / GitHub когда нет родного фото |
| **🏷️ Premium-эмодзи** | 37 эмодзи с regex-override по контексту: упоминание «Claude» → лого Anthropic |
| **📅 Дайджест** | Раз в неделю — обзор главных постов канала с кликабельными ссылками |
| **🗂️ 6 хэштегов** | Gemini сам выбирает: #новости #руководство #советы #полезное #обсуждения #дайджест |
| **📊 Тематические лого** | Auto-detect Python/Go/Rust/JS, OpenAI/Anthropic/Google, GitHub/Reddit и т.д. |

---

## 🏗️ Архитектура

```mermaid
flowchart LR
    subgraph Sources["📡 Источники"]
        S1["Telegram-каналы"]
        S2["X / Twitter"]
        S3["Reddit"]
        S4["GitHub"]
    end

    subgraph Bot["🤖 newsbot"]
        TR["Telethon<br/>userbot"]
        EXT["external_fetch<br/>FxTwitter / Reddit JSON"]
        AI["Gemini<br/>filter + rewrite"]
        RP["Pillow<br/>render_post"]
        DB[("SQLite<br/>WAL")]
        TG["aiogram<br/>Bot API"]
    end

    subgraph Output["📰 Канал"]
        CH["@runeuronews"]
    end

    S1 --> TR --> AI
    S2 --> EXT --> AI
    S3 --> EXT --> AI
    S4 --> EXT --> AI
    AI --> DB
    AI --> RP
    RP --> TG
    AI --> TG
    TG --> CH
```

### Поток обработки одного поста

```mermaid
sequenceDiagram
    participant Src as Канал-источник
    participant TR as Telethon
    participant DB as SQLite
    participant AI as Gemini
    participant Bot as Bot API
    participant CH as Канал

    Src->>TR: NewMessage event
    TR->>DB: claim_message (atomic INSERT OR IGNORE)
    DB-->>TR: новый
    TR->>DB: recent_published_titles(24h)
    DB-->>TR: list of 50 titles
    TR->>AI: filter_and_rewrite(post, titles)
    Note over AI: 1) Дубль?<br/>2) Two-question test<br/>3) Importance 1-3<br/>4) Rewrite
    AI-->>TR: {is_news, title, body, hashtag, emoji_theme}
    TR->>Bot: publish с premium-emoji + хэштег
    Bot->>CH: Сообщение
    Bot-->>TR: target_message_id
    TR->>DB: record_published
```

---

## 🚀 Быстрый старт

> Требуется OrbStack или Docker Desktop. Telegram Premium у владельца бота — желателен.

### 1. Клонировать репо

```bash
git clone https://github.com/Gildra-Foundation/news.git
cd news
```

### 2. Заполнить `.env`

```bash
cp .env.example .env
```

Что вписать:

| Поле | Где взять |
|---|---|
| `TELEGRAM_READER_ENABLED` | `false` для RSS/web; `true` включает Telethon-reader |
| `MTPROTO_PUBLISHER_ENABLED` | `true` публикует через Premium-аккаунт; чужие каналы не читает |
| `TG_API_ID`, `TG_API_HASH` | Нужны для любого включённого MTProto-режима |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → /newbot |
| `TARGET_CHANNEL` | `@название_канала` — бот должен быть **админом** |
| `ADMIN_USER_ID` | узнаете на шаге 4 |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` (быстро/бесплатно) или `gemini-2.5-pro` |
| `RSS_ENABLED` | `true` включает автономный RSS-поллинг |
| `RSS_FEED_URLS` | RSS-ленты через запятую; по умолчанию Wowhead |

Опциональные ключи GetXAPI, RedditAPIs и Scrape.do удобно вводить без отображения
в терминале и без ручного редактирования `.env`:

```bash
gildranews-api-keys --enable-paid
```

Команда сохраняет `.env` с правами `0600`. Без `--enable-paid` она только
обновляет ключи; платные маршруты остаются выключенными.

### 3. Авторизация MTProto (опционально)

Чтобы Premium Custom Emoji отображались в канале без Fragment-улучшения имени,
включите `MTPROTO_PUBLISHER_ENABLED=true`. Эта сессия используется только для
резервной отправки, если Telegram отклонит нативную Rich Message. Основной путь
публикации статей использует Bot API `sendRichMessage`. Чтение Telegram-источников управляется отдельно через
`TELEGRAM_READER_ENABLED` и по умолчанию выключено.

Создайте пользовательскую сессию один раз:

```bash
docker compose run --rm newsbot python -m gildranews.init_session
```

Введите номер и код из служебного чата Telegram в приложении. Telegram может не
предлагать SMS. Сессия запишется в `data/userbot.session` и не должна покидать сервер.

### 4. Запуск

```bash
docker compose up -d
docker compose logs -f
```

Напишите боту `/start` в личке — он вернёт ваш `user_id`. Впишите в `.env`, перезапустите:

```bash
docker compose up -d --force-recreate
```

Готово. Real-time-обработчик ловит новые посты, поллинг каждые 30 мин страхует.

---

## 💬 Команды бота

> Все команды доступны **только админу** (по `ADMIN_USER_ID`).

| Команда | Что делает |
|---|---|
| `/start` | Список команд + ваш user_id |
| `/sources` | Текущие источники |
| `/add @канал` | Добавить источник + автоподписка userbot-а |
| `/remove @канал` | Удалить источник |
| `/run` | Прогон поллинга прямо сейчас |
| `/test [@канал]` | Опубликовать последний пост — для проверки стиля |
| `/digest` | Собрать и опубликовать недельный дайджест |
| `/status` | Итоги последнего прогона |
| `/emojiid` | Извлечь ID premium-эмодзи из пересланного сообщения |
| `/emojis` | Состояние Warcraft Custom Emoji и Fragment-интеграции |
| `/emoji_retry ID` | Вернуть неудачную загрузку в очередь |
| `/emoji_disable ID` | Отключить проблемную загрузку |
| `/cancel` | Отменить ожидание правки |

---

## 🔗 Ручная публикация по ссылке

Просто пришлите боту любую из ссылок:

| Сервис | Формат |
|---|---|
| 📢 Telegram | `t.me/канал/ID` |
| 𝕏 Twitter / X | `x.com/.../status/ID` или `twitter.com/...` |
| 👽 Reddit | `reddit.com/r/.../comments/ID/...` |
| 🐙 GitHub | `github.com/owner/repo` |

Бот:
1. Скачает контент (GetXAPI/RedditAPIs, если явно включены; затем бесплатные
   FxTwitter/Reddit JSON; при блокировке — явно включённый Scrape.do через ParsesUnix)
2. Переведёт + переформулирует через Gemini
3. Отрендерит карточку через Pillow если нет родного фото
4. Покажет **превью с 4 кнопками**:

| Кнопка | Действие |
|---|---|
| ✏️ Редактировать | Бот ждёт текстовую инструкцию правки, Gemini переделает |
| ✅ Опубликовать | Шлёт в канал + пишет в БД для будущего дедупа |
| 🔗 Оригинал в посте | Toggle: добавить курсивно-подчёркнутую ссылку в финал поста |
| ❌ Отменить | Удалить черновик и скриншот |

---

## 🏷️ Premium-эмодзи

Luna выделяет до трёх сущностей Warcraft: класс, специализацию, заклинание,
предмет, косметику, средство передвижения, рейд, подземелье, босса и другие.
Бот проверяет точное совпадение и ветку игры, скачивает оригинальную иконку,
приводит её к статическому WEBP 100×100 и сохраняет созданный
`custom_emoji_id` в SQLite.

В пост попадает не более двух Custom Emoji. При неоднозначном совпадении,
ошибке загрузки или отказе Telegram публикация продолжается с обычным
Unicode-эмодзи. Загрузка повторяется через очередь с ограничением 10 новых
эмодзи в сутки и не более 190 элементов в одном наборе.

Для использования Custom Emoji ботом в канале к боту должен быть привязан
дополнительный коллекционный username с Fragment. После привязки включите:

```ini
EMOJI_AUTOCREATE_ENABLED=true
```

Технические ограничения файлов и наборов описаны в
[Telegram Bot API](https://core.telegram.org/bots/api#addstickertoset) и
[руководстве Telegram по стикерам](https://core.telegram.org/stickers).

---

## 📅 Еженедельный дайджест

```mermaid
gantt
    title Цикл недели
    dateFormat HH:mm
    axisFormat %a
    section Бот
    Real-time + поллинг :a1, 00:00, 7d
    section Воскресенье
    Дайджест в 18:00 UTC :crit, 18:00, 0.5d
```

Раз в воскресенье в 18:00 UTC бот:
1. Достаёт все посты с `target_message_id` за последние 7 дней
2. Шлёт в Gemini → структурированный JSON `{intro, sections: [{name, items}]}`
3. Собирает HTML с кликабельными ссылками вида `https://t.me/runeuronews/<id>`
4. Публикует **с обложкой** [`assets/digest_cover.jpg`](assets/digest_cover.jpg) и хэштегом `#дайджест@runeuronews`

Или вручную — `/digest` в личке.

---

## 📁 Структура проекта

```
.
├── src/gildranews/
│   ├── domain/                # Общие модели без зависимостей от SDK
│   ├── application/           # Pipeline, дайджест и контракты внешних компонентов
│   ├── adapters/
│   │   ├── ai/                # AI-провайдеры; сейчас Gemini, далее ChatGPT Server
│   │   ├── sources/           # Telegram, X, Reddit и GitHub
│   │   ├── publishing/        # Публикация через Telegram Bot API
│   │   ├── persistence/       # SQLite
│   │   ├── rendering/         # Карточки и инфографика Pillow
│   │   ├── emoji/             # Совместимость со старым каталогом emoji
│   │   └── warcraft/          # Иконки и Telegram Custom Emoji registry
│   ├── presentation/telegram/ # Команды, callbacks, уведомления и real-time события
│   ├── jobs/                  # Планировщик, cleanup и недельный дайджест
│   ├── config.py              # Типизированная конфигурация окружения
│   └── main.py                # Минимальная точка входа
├── tests/                     # Unit и integration-тесты
├── pyproject.toml             # Метаданные, зависимости и инструменты качества
├── Dockerfile                 # python:3.12-slim + fonts-dejavu
├── docker-compose.yml         # mem_limit 512m, cpus 1.0, restart unless-stopped
├── assets/
│   └── digest_cover.jpg       # Обложка для дайджеста
└── data/                      # Создаётся при первом запуске, не хранится в Git
    ├── userbot.session        # Telethon-сессия
    ├── newsbot.db             # SQLite с WAL
    ├── emoji_icons/           # Нормализованные игровые иконки 100×100
    └── screenshots/           # Временные карточки X/Reddit/GitHub
```

Локальная установка для разработки:

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
```

---

## ⚙️ Конфигурация

### .env переменные

```ini
# Telegram
TG_API_ID=12345678
TG_API_HASH=abcd1234...
BOT_TOKEN=1234:ABCdef...
TARGET_CHANNEL=@runeuronews
ADMIN_USER_ID=123456789

# Gemini
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-3.1-flash-lite

# Опциональные платные маршруты (без enabled=true запросов не будет)
GETXAPI_KEY=...
GETXAPI_ENABLED=false
REDDITAPIS_KEY=...
REDDITAPIS_ENABLED=false
REDDIT_MAX_POSTS_PER_DAY=2
X_MAX_POSTS_PER_DAY=2
SCRAPE_DO_TOKEN=...
SCRAPE_DO_ENABLED=false

# Расписание
LOOKBACK_MINUTES=45         # окно поллинга
INTERVAL_MINUTES=30         # период safety-net
MAX_POSTS_PER_RUN=3

# Warcraft Custom Emoji после привязки Fragment username
EMOJI_AUTOCREATE_ENABLED=true
EMOJI_MAX_NEW_PER_DAY=10
EMOJI_UPLOAD_TIMEOUT_SECONDS=15
SUBSCRIBE_EMOJI_ID=5280756831252167912
FOREVER_GUIDE_MESSAGE_ID=44
FOREVER_GUIDE_REFRESH_MINUTES=30
FOREVER_RELEASE_DATE=2026-11-04
```

Реестр игровых сущностей, хэшей изображений, очереди и `custom_emoji_id`
хранится в таблицах `warcraft_entities` и `telegram_emoji_assets` базы
`data/newsbot.db`; ручной файл `data/emojis.json` для этого конвейера не нужен.

Отпечатки сюжетов и суточные квоты Reddit/X также хранятся в SQLite. Один и
тот же сюжет из разных источников резервируется атомарно, а перезапуск бота не
обнуляет лимит в два поста Reddit и два поста X за сутки.

Если задан `FOREVER_GUIDE_MESSAGE_ID`, бот каждые 30 минут обновляет закреплённое
оглавление ссылками на новые публикации о WoW: Forever. Редактирование идёт через
MTProto, поэтому Premium Emoji сохраняются, а в дату `FOREVER_RELEASE_DATE`
автоматическое пополнение прекращается.

---

## 🧰 Технологии

| Слой | Стек |
|---|---|
| **Чтение каналов** | Telethon 1.36+ (MTProto userbot) |
| **Публикация и команды** | aiogram 3.x (Bot API) |
| **AI** | google-genai (Gemini 3.1 / 2.5) с structured output через pydantic |
| **Internet-fetch** | httpx с persistent connection pool, FxTwitter API |
| **Рендер карточек** | Pillow с font-cache, шрифт DejaVu Sans (Unicode + кириллица) |
| **Хранилище** | aiosqlite + WAL + memory-mapped 64MB |
| **Расписание** | APScheduler — interval 30 min + cron weekly |
| **Деплой** | Docker (python:3.12-slim ~370MB) + OrbStack |
| **Лимиты** | mem 512MB, CPU 1.0, лог-ротация 10MB×5 |

### Производительность

| Метрика | Значение |
|---|---|
| RAM в idle | ~150 MiB |
| CPU в idle | <0.05% |
| Один пост: fetch → filter → publish | ~3–5 сек |
| Telethon-сессия persistent, httpx с keep-alive, Gemini-клиент кэширован |

---

## 🛡️ Безопасность

- ✅ `.env` в `.gitignore` — секреты не попадают в репо
- ✅ `*.session` — Telethon-сессия даёт **полный доступ к аккаунту**, никому не передавайте
- ✅ `data/` — БД и временные файлы тоже игнорируются git-ом
- ✅ Все команды бота — только для `ADMIN_USER_ID`
- ✅ SQLite в WAL-режиме, безопасные транзакции
- ✅ FloodWait и rate-limit обрабатываются с retry
- ✅ `mem_limit` 512MB — при утечке OOM-killer прибьёт контейнер, `restart: unless-stopped` поднимет

---

## 🧱 Построено с использованием

| Репозиторий | Что даёт |
|---|---|
| **[BotForge / telegram-skills](https://github.com/Zulut30/telegram-skills)** | Skill pack для AI-ассистентов (Claude Code, Cursor, Codex), который превращает LLM в senior Telegram-bot-инженера: модульная архитектура, Bot API 9.6, rate-limits, Docker, миграции — всё по канону, без монолитов |
| **[premium-telegram-emoji](https://github.com/Zulut30/premium-telegram-emoji)** | Каталог premium-эмодзи Telegram с custom-emoji-id, готовыми HTML-сниппетами и гайдом «как заставить бот отправлять анимированные эмодзи в каналы» |

Оба репозитория поддерживает [@Zulut30](https://github.com/Zulut30).

---

## 📝 Лицензия

MIT — делайте что хотите, но без гарантий.

---

<div align="center">

**⭐ Понравилось? Ставьте звезду — это лучший feedback.**

Made with ❤️ for content curators who hate spam.

</div>
