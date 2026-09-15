from __future__ import annotations

import asyncio
import logging
import os
import os.path
import re
import tempfile
from functools import partial

from aiogram import F
from aiogram.enums import MessageEntityType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    Message,
)

from gildranews.adapters.rendering import cards as render_post
from gildranews.adapters.rendering.svg_infographic import render_png as render_infographic
from gildranews.adapters.sources import external as external_fetch

LINK_RE = re.compile(r"(?:https?://)?t\.me/(c/)?([\w_]+)/(\d+)", re.IGNORECASE)

from gildranews import config
from gildranews.adapters.ai.provider import build_content_ai
from gildranews.adapters.emoji import catalog as emoji_store
from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import telegram as tg_writer
from gildranews.application import digest as digest_mod
from gildranews.application import warcraft_enrichment
from gildranews.jobs.scheduler import ScheduledJobs
from gildranews.presentation.telegram import drafts
from gildranews.presentation.telegram.notifications import format_status, notify_admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Глушим шумные библиотеки — оставляем только WARNING+
for noisy in (
    "telethon",
    "telethon.network",
    "telethon.network.mtprotosender",
    "aiogram",
    "aiogram.dispatcher",
    "aiogram.event",
    "apscheduler",
    "apscheduler.scheduler",
    "apscheduler.executors.default",
    "httpx",
    "httpcore",
    "google_genai",
    "google_genai.models",
):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("newsbot")

INITIAL_SOURCES = [
    "openai_fan",
    "neuraldvig",
    "ai_newz",
    "gpt_news",
    "iSimplify",
    "tips_ai",
    "NeuralShit",
    "gptpublic",
]
async def run_bot() -> None:
    cfg = config.load()
    os.makedirs("data", exist_ok=True)
    await db.init()

    bot = tg_writer.make_bot(cfg.bot_token)
    dp = tg_writer.make_dispatcher(cfg.admin_user_id, cfg.target_channel)
    content_ai = build_content_ai(cfg)
    _notify_admin = partial(notify_admin, bot, cfg)

    mtproto_client = None
    tele_client = None
    tg_reader = None
    pipeline = None
    if cfg.telegram_reader_enabled or cfg.mtproto_publisher_enabled:
        try:
            from gildranews.adapters.publishing import mtproto as mtproto_writer
        except ModuleNotFoundError as exc:
            if exc.name != "telethon":
                raise
            log.warning(
                "Telethon не установлен; бот запущен только с Bot API. "
                "Для MTProto-публикации установите Telethon."
            )
        else:
            session_path = f"{mtproto_writer.SESSION_NAME}.session"
            if os.path.exists(session_path):
                candidate = mtproto_writer.make_client(cfg.tg_api_id, cfg.tg_api_hash)
                await candidate.connect()
                if await candidate.is_user_authorized():
                    mtproto_client = candidate
                    me = await mtproto_client.get_me()
                    log.info("MTProto авторизован как %s (id=%s)", me.first_name, me.id)
                    if cfg.mtproto_publisher_enabled:
                        tg_writer.configure_mtproto_publisher(mtproto_client)
                        log.info("MTProto включён только как транспорт публикации")
                else:
                    await candidate.disconnect()
                    log.warning(
                        "%s не авторизован; интерактивный вход отключён в сервисе. "
                        "Бот продолжит работу только с Bot API.",
                        session_path,
                    )
            else:
                log.warning(
                    "Telethon настроен, но %s не найден; "
                    "бот запущен только с Bot API без чтения Telegram-каналов.",
                    session_path,
                )

    if cfg.telegram_reader_enabled and mtproto_client is not None:
        from gildranews.adapters.sources import telegram as telegram_reader
        from gildranews.application import process_news as telegram_pipeline
        from gildranews.presentation.telegram.realtime import register_realtime_handler

        tele_client = mtproto_client
        tg_reader = telegram_reader
        pipeline = telegram_pipeline
        await db.seed_sources(INITIAL_SOURCES)
        sources = await db.list_sources()
        for src in sources:
            await tg_reader.ensure_joined(tele_client, src)
        register_realtime_handler(
            tele_client, bot, cfg, _notify_admin, content_ai,
        )
    elif cfg.telegram_reader_enabled:
        log.warning("Telegram-reader включён, но MTProto-сессия недоступна")
    else:
        log.info("Telegram-reader отключён: чужие Telegram-каналы не читаются")

    # ------- Команды бота -------
    def _is_admin(message: Message) -> bool:
        return cfg.admin_user_id != 0 and (
            message.from_user is not None and message.from_user.id == cfg.admin_user_id
        )

    @dp.message(Command("run"))
    async def cmd_run(message: Message) -> None:
        if not _is_admin(message):
            return
        if tele_client is None or pipeline is None:
            await message.answer(
                "Telegram-reader отключён. Используйте RSS/web-источники "
                "или пришлите поддерживаемую внешнюю ссылку."
            )
            return
        await message.answer("Запускаю прогон…")
        result = await pipeline.run_once(
            tele_client, bot, cfg, on_result=_notify_admin, news_filter=content_ai,
        )
        if result.get("error"):
            await message.answer(
                f"Готово с ошибкой.\nПолучено: {result['fetched']}, "
                f"отобрано: {result['selected']}, опубликовано: {result['published']}\n"
                f"Ошибка: {result['error']}"
            )
        else:
            await message.answer(
                f"Готово.\nПолучено: {result['fetched']}, "
                f"отобрано: {result['selected']}, опубликовано: {result['published']}"
            )

    @dp.message(Command("digest"))
    async def cmd_digest(message: Message) -> None:
        if not _is_admin(message):
            return
        await message.answer("Собираю еженедельный дайджест…")
        result = await digest_mod.build_and_publish(bot, cfg, content_ai=content_ai)
        if result.get("published"):
            await message.answer(
                f"✅ Дайджест опубликован. Пунктов: {result.get('posts_count', 0)}"
            )
        else:
            await message.answer(f"⚠️ Не удалось: {result.get('reason')}")

    @dp.message(Command("test"))
    async def cmd_test(message: Message) -> None:
        if not _is_admin(message):
            return
        if tele_client is None or tg_reader is None:
            await message.answer(
                "Telegram-reader отключён: команда /test для чужих "
                "Telegram-каналов недоступна."
            )
            return
        parts = (message.text or "").split(maxsplit=1)
        src_list = await db.list_sources()
        if not src_list:
            await message.answer("Нет источников.")
            return

        if len(parts) > 1:
            target = parts[1].strip().lstrip("@").lower()
            candidates = [target]
        else:
            candidates = src_list

        post = None
        used = None
        for ch in candidates:
            post = await tg_reader.fetch_latest_one(tele_client, ch)
            if post:
                used = ch
                break

        if not post:
            await message.answer(
                f"Не нашёл текстовых постов в {', '.join('@' + c for c in candidates)}."
            )
            return

        await message.answer(
            f"Беру пост https://t.me/{used}/{post.message_id} "
            f"(медиа: {len(post.media_messages)}), рерайт через {cfg.app_server_model if cfg.ai_provider == 'app_server' else cfg.gemini_model}…"
        )

        try:
            rewrite = await content_ai.rewrite(post.text)
        except Exception as e:
            log.exception("AI rewrite failed")
            await message.answer(f"AI-сервис: {type(e).__name__}: {e}")
            return

        if not rewrite:
            await message.answer("AI-сервис вернул некорректный ответ.")
            return

        emap = emoji_store.load()
        override = emoji_store.detect_override(
            emap, f"{rewrite.title}\n{rewrite.body}",
        )
        enrichment = warcraft_enrichment.WarcraftEnrichment()
        if cfg.emoji_autocreate_enabled and rewrite.references:
            try:
                async with asyncio.timeout(cfg.emoji_upload_timeout_seconds):
                    enrichment = await warcraft_enrichment.enrich(
                        bot, cfg, rewrite.references,
                    )
            except Exception:
                log.warning("Warcraft enrichment failed for /test", exc_info=True)
        text = tg_writer.format_post(
            rewrite.title, rewrite.body,
            emoji_theme=override or "",
            emoji_map=emap,
            hashtag_key=rewrite.hashtag,
            inline_links=enrichment.inline_links,
            custom_emojis=enrichment.emojis,
        )
        with tempfile.TemporaryDirectory(prefix="newsbot_test_") as tmpdir:
            media_files = await tg_reader.download_post_media(tele_client, post, tmpdir)
            target_msg_id = await tg_writer.publish(bot, cfg.target_channel, text, media_files)
        if target_msg_id:
            await db.mark_seen(post.channel, post.message_id)
            # Записываем в published_posts чтобы дайджест мог сослаться
            await db.record_published(
                post.channel, post.message_id, rewrite.title, rewrite.body,
                target_message_id=target_msg_id,
            )
            await message.answer(
                f"Опубликовано в {cfg.target_channel} (медиа: {len(media_files)})."
            )
        else:
            await message.answer("Ошибка публикации (см. логи).")

    @dp.message(F.text.regexp(LINK_RE))
    async def cmd_link(message: Message) -> None:
        if not _is_admin(message):
            return
        if tele_client is None or tg_reader is None or pipeline is None:
            await message.answer(
                "Telegram-reader отключён: ссылки на посты чужих "
                "Telegram-каналов не обрабатываются."
            )
            return
        m = LINK_RE.search(message.text or "")
        if not m:
            return
        is_private = bool(m.group(1))
        chan_raw = m.group(2)
        msg_id = int(m.group(3))

        if is_private:
            await message.answer(
                "Приватные каналы (t.me/c/...) пока не поддерживаются. "
                "Пришлите ссылку из публичного канала."
            )
            return

        channel = chan_raw.lower()
        await message.answer(f"Беру пост https://t.me/{channel}/{msg_id} …")

        post = await tg_reader.fetch_post_by_link(tele_client, channel, msg_id)
        if not post:
            await message.answer(
                "Не получил пост: канал может быть приватным, поста нет или у userbot нет доступа."
            )
            return

        try:
            result = await pipeline.process_post(
                tele_client, bot, cfg, post, force=True, news_filter=content_ai,
            )
        except Exception as e:
            log.exception("force process_post failed")
            await message.answer(f"⚠️ Ошибка: {type(e).__name__}: {e}")
            return

        # Краткий ответ на исходное сообщение + полное уведомление
        await message.answer(format_status(result))
        await _notify_admin(result)

    # ------- X / Reddit / GitHub: парсинг ссылки, превью с кнопками -------
    # Состояние ожидания инструкции на правку хранится в db.pending_edits —
    # выживает при рестарте, чистится `_scheduled_cleanup`.

    @dp.message(F.text.regexp(external_fetch.EXTERNAL_RE))
    async def cmd_external_url(message: Message) -> None:
        if not _is_admin(message):
            return
        m = external_fetch.EXTERNAL_RE.search(message.text or "")
        if not m:
            return
        url = m.group(0)
        await message.answer(f"Беру пост из {url}…")

        post = await external_fetch.fetch(url)
        if not post:
            await message.answer(
                "Не получилось вытянуть пост. Возможно, он удалён, приватный, "
                "или сервис парсинга временно недоступен."
            )
            return

        if post.source == "github":
            rewrite = await content_ai.summarize_github(post.text)
        else:
            rewrite = await content_ai.translate(post.text)
        if not rewrite:
            await message.answer(
                "AI-сервис не справился с обработкой. Попробуйте ещё раз."
            )
            return

        enrichment = warcraft_enrichment.WarcraftEnrichment()
        if cfg.emoji_autocreate_enabled and rewrite.references:
            try:
                async with asyncio.timeout(cfg.emoji_upload_timeout_seconds):
                    enrichment = await warcraft_enrichment.enrich(
                        bot, cfg, rewrite.references,
                    )
            except Exception:
                log.warning("Warcraft enrichment failed for manual link", exc_info=True)

        # Если в посте есть фото/видео — берём ИХ, без рендера скриншота
        if post.media_type in ("photo", "video") and post.media_url:
            draft_id = await db.create_draft(
                source_url=post.source_url,
                title=rewrite.title,
                body=rewrite.body,
                image_url=post.media_url,
                original_text=post.text,
                media_type=post.media_type,
                hashtag=rewrite.hashtag,
                inline_links=enrichment.inline_links,
                custom_emojis=enrichment.emojis,
            )
        else:
            # Текстовый пост (X/Reddit) или GitHub-репо — рендерим карточку
            draft_id = await db.create_draft(
                source_url=post.source_url,
                title=rewrite.title,
                body=rewrite.body,
                image_url=None,
                original_text=post.text,
                media_type="photo",
                hashtag=rewrite.hashtag,
                inline_links=enrichment.inline_links,
                custom_emojis=enrichment.emojis,
            )
            screenshot_path: str | None = None
            try:
                screenshot_path = render_post.screenshot_path_for(draft_id)
                if rewrite.infographic is not None and await asyncio.to_thread(
                    render_infographic, rewrite.infographic, screenshot_path,
                ):
                    pass
                elif post.source == "twitter":
                    # Подкачиваем аватарку автора (если есть)
                    avatar_path = None
                    avatar_url = post.meta.get("avatar_url") or ""
                    if avatar_url:
                        avatar_path = render_post.screenshot_path_for(draft_id) + ".avatar"
                        if not await external_fetch.download_avatar(avatar_url, avatar_path):
                            avatar_path = None
                    render_post.render_tweet(
                        text=post.text,
                        author_name=post.author or post.screen_name or "X",
                        screen_name=post.screen_name or "x",
                        dest_path=screenshot_path,
                        avatar_path=avatar_path,
                        verified=bool(post.meta.get("verified")),
                    )
                    if avatar_path and os.path.exists(avatar_path):
                        try:
                            os.remove(avatar_path)
                        except OSError:
                            pass
                elif post.source == "github":
                    render_post.render_github(
                        full_name=post.title or "",
                        description=post.meta.get("description") or "",
                        language=post.meta.get("language") or "",
                        stars=post.meta.get("stars") or 0,
                        forks=post.meta.get("forks") or 0,
                        topics=post.meta.get("topics") or [],
                        dest_path=screenshot_path,
                    )
                else:  # reddit
                    render_post.render_reddit(
                        title=post.title or "",
                        body=(post.text[len(post.title) + 2:]
                              if post.title and post.text.startswith(post.title) else ""),
                        subreddit=post.subreddit,
                        author=post.author or "",
                        dest_path=screenshot_path,
                    )
            except Exception:
                log.exception("Не удалось отрендерить карточку")
                screenshot_path = None
            if screenshot_path:
                await db.set_draft_image(draft_id, screenshot_path)
        await drafts.send_preview(bot, message.chat.id, draft_id)

    @dp.callback_query(F.data.startswith("pub:"))
    async def cb_publish(callback: CallbackQuery) -> None:
        if cfg.admin_user_id and (not callback.from_user or callback.from_user.id != cfg.admin_user_id):
            await callback.answer("⛔ Только для админа", show_alert=True)
            return
        try:
            draft_id = int(callback.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Битый callback")
            return
        draft = await db.get_draft(draft_id)
        if not draft:
            await callback.answer("Черновик не найден или уже опубликован", show_alert=True)
            return

        text = drafts.format_draft_text(draft)
        media = drafts.photo_argument(draft["image_url"])
        media_type = draft.get("media_type") or "photo"
        target_msg_id: int | None = None
        requested_custom_emoji = tg_writer.without_custom_emojis(text) != text
        used_unicode_fallback = False

        async def _send_draft(formatted_text: str):
            if media:
                if media_type == "video":
                    return await bot.send_video(
                        chat_id=cfg.target_channel, video=media, caption=formatted_text,
                    )
                return await bot.send_photo(
                    chat_id=cfg.target_channel, photo=media, caption=formatted_text,
                )
            return await bot.send_message(
                chat_id=cfg.target_channel,
                text=formatted_text,
                disable_web_page_preview=True,
            )

        try:
            sent = await _send_draft(text)
            target_msg_id = sent.message_id
        except TelegramAPIError as e:
            fallback_text = tg_writer.without_custom_emojis(text)
            if fallback_text != text:
                try:
                    sent = await _send_draft(fallback_text)
                    target_msg_id = sent.message_id
                    used_unicode_fallback = True
                except TelegramAPIError as fallback_error:
                    log.exception("publish from draft failed without Custom Emoji")
                    await callback.answer(
                        f"Ошибка: {fallback_error}", show_alert=True,
                    )
                    return
            else:
                log.exception("publish from draft failed")
                await callback.answer(f"Ошибка: {e}", show_alert=True)
                return

        if requested_custom_emoji:
            try:
                delivered = not used_unicode_fallback and tg_writer.message_has_custom_emoji(sent)
                await db.set_service_state(
                    "fragment_integration",
                    "healthy" if delivered else "faulty",
                    "" if delivered else "Telegram did not preserve Custom Emoji",
                )
            except Exception:
                log.warning("Could not persist Fragment health", exc_info=True)

        # Зафиксировать в published_posts для дедупа и дайджеста
        await db.record_published(
            channel="manual", message_id=draft_id,
            title=draft["title"], body=draft["body"],
            target_message_id=target_msg_id,
        )
        # Удаляем черновик и временный скриншот (если был локальный файл)
        if draft.get("image_url"):
            ref = draft["image_url"]
            if not ref.startswith(("http://", "https://")):
                render_post.cleanup_screenshot(ref)
        await db.delete_draft(draft_id)
        # Убрать кнопки на превью
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
        await callback.answer("Опубликовано")
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"✅ Опубликовано в {cfg.target_channel}",
        )

    @dp.callback_query(F.data.startswith("edit:"))
    async def cb_edit(callback: CallbackQuery) -> None:
        if cfg.admin_user_id and (not callback.from_user or callback.from_user.id != cfg.admin_user_id):
            await callback.answer("⛔ Только для админа", show_alert=True)
            return
        try:
            draft_id = int(callback.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Битый callback")
            return
        if not await db.get_draft(draft_id):
            await callback.answer("Черновик не найден", show_alert=True)
            return
        await db.set_pending_edit(callback.from_user.id, draft_id)
        await callback.answer()
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=(
                "✏️ Опишите правку отдельным сообщением.\n"
                "Например: «перевод неточный, replicate ≠ повторить», "
                "«сделай title короче», «добавь акцент на цену»."
            ),
        )

    @dp.callback_query(F.data.startswith("cancel:"))
    async def cb_cancel(callback: CallbackQuery) -> None:
        if cfg.admin_user_id and (not callback.from_user or callback.from_user.id != cfg.admin_user_id):
            await callback.answer("⛔ Только для админа", show_alert=True)
            return
        try:
            draft_id = int(callback.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Битый callback")
            return
        draft = await db.get_draft(draft_id)
        if draft:
            # Чистим скриншот, если он локальный
            if draft.get("image_url"):
                ref = draft["image_url"]
                if not ref.startswith(("http://", "https://")):
                    render_post.cleanup_screenshot(ref)
            await db.delete_draft(draft_id)
        # Если юзер в ожидании правки этого черновика — сбросить
        if callback.from_user:
            pending = await db.get_pending_edit(callback.from_user.id)
            if pending == draft_id:
                await db.clear_pending_edit(callback.from_user.id)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
        await callback.answer("Отменено")
        await bot.send_message(callback.from_user.id, "❌ Черновик отменён.")

    @dp.callback_query(F.data.startswith("orig:"))
    async def cb_toggle_original(callback: CallbackQuery) -> None:
        if cfg.admin_user_id and (not callback.from_user or callback.from_user.id != cfg.admin_user_id):
            await callback.answer("⛔ Только для админа", show_alert=True)
            return
        try:
            draft_id = int(callback.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Битый callback")
            return
        draft = await db.get_draft(draft_id)
        if not draft:
            await callback.answer("Черновик не найден", show_alert=True)
            return
        new_value = not draft.get("include_original", False)
        await db.set_draft_include_original(draft_id, new_value)

        # Перестроим текст и клавиатуру (auto-override эмодзи учтётся)
        draft["include_original"] = new_value
        new_text = drafts.format_draft_text(draft)
        new_kb = drafts.draft_keyboard(draft_id, include_original=new_value)
        edited = False
        try:
            # У сообщений с фото/видео меняем caption, у текстовых — text
            if callback.message.photo or callback.message.video or callback.message.animation:
                await callback.message.edit_caption(caption=new_text, reply_markup=new_kb)
            else:
                await callback.message.edit_text(
                    text=new_text, reply_markup=new_kb, disable_web_page_preview=True,
                )
            edited = True
        except TelegramAPIError as e:
            log.warning("orig toggle: edit failed, fallback to resend: %s", e)
        await callback.answer("Добавлено" if new_value else "Убрано")
        if not edited:
            await drafts.send_preview(bot, callback.from_user.id, draft_id)

    # Хендлер для текста-инструкции на правку. Должен сработать ПЕРЕД дефолтными
    # обработчиками, но ПОСЛЕ команд и хендлеров ссылок (которые матчатся regexp-ом).
    @dp.message(F.text & ~F.text.startswith("/"))
    async def edit_feedback_handler(message: Message) -> None:
        if not _is_admin(message):
            return
        uid = message.from_user.id if message.from_user else 0
        draft_id = await db.get_pending_edit(uid)
        if draft_id is None:
            return
        # Если сообщение само содержит t.me/x/reddit ссылку — пусть его обработают
        # другие хендлеры (этот вызывается после).
        text = (message.text or "").strip()
        if external_fetch.EXTERNAL_RE.search(text) or LINK_RE.search(text):
            return
        draft = await db.get_draft(draft_id)
        if not draft:
            await db.clear_pending_edit(uid)
            await message.answer("Черновик не найден.")
            return
        # Сразу очищаем pending, чтобы повторное сообщение не ушло на повторную правку.
        # Если Gemini упадёт — восстановим.
        await db.clear_pending_edit(uid)
        await message.answer("Применяю правку…")
        src_url = draft.get("source_url") or ""
        if src_url.startswith(("https://github.com", "http://github.com")):
            rewrite = await content_ai.summarize_github(
                draft["original_text"], edit_instruction=text,
            )
        else:
            rewrite = await content_ai.translate(
                draft["original_text"], edit_instruction=text,
            )
        if not rewrite:
            await db.set_pending_edit(uid, draft_id)  # вернуть состояние
            await message.answer("AI-сервис не справился. Попробуйте описать правку иначе.")
            return
        await db.update_draft(draft_id, rewrite.title, rewrite.body, rewrite.hashtag)
        enrichment = warcraft_enrichment.WarcraftEnrichment()
        if cfg.emoji_autocreate_enabled and rewrite.references:
            try:
                async with asyncio.timeout(cfg.emoji_upload_timeout_seconds):
                    enrichment = await warcraft_enrichment.enrich(
                        bot, cfg, rewrite.references,
                    )
            except Exception:
                log.warning("Warcraft enrichment failed after draft edit", exc_info=True)
        await db.set_draft_enrichment(
            draft_id, enrichment.inline_links, enrichment.emojis,
        )
        await drafts.send_preview(bot, message.chat.id, draft_id)

    @dp.message(Command("cancel"))
    async def cmd_cancel(message: Message) -> None:
        if not _is_admin(message):
            return
        uid = message.from_user.id if message.from_user else 0
        pending = await db.get_pending_edit(uid)
        if pending is not None:
            await db.clear_pending_edit(uid)
            await message.answer("Правка отменена.")
        else:
            await message.answer("Нечего отменять.")

    @dp.message(Command("emojiid"))
    async def cmd_emoji_id(message: Message) -> None:
        if not _is_admin(message):
            return
        target = message.reply_to_message or message
        entities = list(target.entities or []) + list(target.caption_entities or [])
        ids: list[str] = []
        for ent in entities:
            if ent.type == MessageEntityType.CUSTOM_EMOJI and ent.custom_emoji_id:
                ids.append(ent.custom_emoji_id)
        if not ids:
            await message.answer(
                "В сообщении нет премиум-эмодзи.\n\n"
                "Как использовать:\n"
                "1. Перешлите сюда сообщение с премиум-эмодзи (нужен Telegram Premium у отправителя)\n"
                "2. Ответьте на пересланное /emojiid\n"
                "Или пришлите /emojiid с премиум-эмодзи в тексте."
            )
            return
        unique_ids = list(dict.fromkeys(ids))
        lines = ["Найдено premium emoji:"]
        for i, eid in enumerate(unique_ids, 1):
            lines.append(f"{i}. <code>{eid}</code>")
        lines.append(
            "\nАвтоматический Warcraft-реестр сам сохраняет созданные ID в SQLite. "
            "Эта команда оставлена для диагностики сторонних наборов."
        )
        await message.answer("\n".join(lines))

    # /add с автоподпиской
    @dp.message(Command("add"))
    async def cmd_add_with_join(message: Message) -> None:
        if not _is_admin(message):
            return
        if tele_client is None or tg_reader is None:
            await message.answer(
                "Telegram-reader отключён. Добавление Telegram-каналов недоступно."
            )
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: <code>/add @channel</code>")
            return
        added = await db.add_source(parts[1])
        if not added:
            await message.answer("Уже есть в списке или некорректный ввод.")
            return
        joined = await tg_reader.ensure_joined(tele_client, parts[1])
        if joined:
            await message.answer(f"Добавлен и подписан: <code>{parts[1]}</code>")
        else:
            await message.answer(
                f"Добавлен: <code>{parts[1]}</code>\n"
                f"⚠️ Не удалось подписаться — real-time не будет работать. "
                f"Backup-поллинг каждые {cfg.interval_minutes} мин подхватит."
            )

    jobs = ScheduledJobs(tele_client, bot, cfg, _notify_admin, content_ai)
    jobs.start()

    try:
        await dp.start_polling(bot, handle_signals=True)
    finally:
        await jobs.stop()
        # Закрываем всё, что держит ресурсы/сокеты
        try:
            if mtproto_client is not None:
                await mtproto_client.disconnect()
        except Exception:
            log.warning("Telethon disconnect raised", exc_info=True)
        try:
            await bot.session.close()
        except Exception:
            log.warning("aiogram bot.session.close raised", exc_info=True)
        try:
            await external_fetch.close_http()
        except Exception:
            log.warning("close_http raised", exc_info=True)
        try:
            await content_ai.aclose()
        except Exception:
            log.warning("content_ai.aclose raised", exc_info=True)
        try:
            await db.close()
        except Exception:
            log.warning("db.close raised", exc_info=True)
