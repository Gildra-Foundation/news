from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sqlite3

from aiohttp import web

from gildranews import config
from gildranews.adapters.ai.provider import build_content_ai
from gildranews.adapters.persistence import site_news, sqlite

log = logging.getLogger(__name__)
_WORKER_KEY = web.AppKey("article_worker", asyncio.Task)


def _error(code: str, message: str, status: int) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


def create_app(token: str) -> web.Application:
    if not token.strip():
        raise ValueError("NEWS_API_TOKEN is required")

    @web.middleware
    async def authorize(request: web.Request, handler):
        if request.path != "/health":
            supplied = request.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                return _error("unauthorized", "Bearer token required", 401)
        try:
            return await handler(request)
        except web.HTTPException as exc:
            return _error("http_error", exc.reason, exc.status)
        except Exception:
            log.exception("News API request failed")
            return _error("internal_error", "Request failed", 500)

    app = web.Application(middlewares=[authorize], client_max_size=1024)

    async def health(_request: web.Request):
        task = _request.app.get(_WORKER_KEY)
        if task is not None and task.done():
            return _error("worker_unavailable", "Article worker stopped", 503)
        connection = await sqlite._get_conn()
        async with connection.execute("SELECT 1") as cursor:
            await cursor.fetchone()
        return web.json_response({"status": "ok"})

    async def news_list(request: web.Request):
        try:
            after_id = int(request.query.get("after_id", "0"))
            limit = int(request.query.get("limit", "20"))
            result = await site_news.list_articles(after_id, limit)
        except ValueError:
            return _error("invalid_pagination", "after_id >= 0 and 1 <= limit <= 100 required", 400)
        return web.json_response(result)

    async def news_detail(request: web.Request):
        try:
            article_id = int(request.match_info["id"])
            if article_id < 1:
                raise ValueError
        except ValueError:
            return _error("invalid_id", "Positive integer ID required", 400)
        article = await site_news.get_article(article_id)
        if article is None:
            return _error("not_found", "Article not found", 404)
        return web.json_response(article)

    app.router.add_get("/health", health)
    app.router.add_get("/v1/news", news_list)
    app.router.add_get("/v1/news/{id}", news_detail)
    return app


async def article_worker(content_ai, *, lease_seconds: int = 900) -> None:
    while True:
        job = None
        try:
            job = await site_news.claim_job(lease_seconds)
            if job is None:
                await asyncio.sleep(10)
                continue
            article = await content_ai.make_website_article(job["source"]["text"])
            await site_news.complete_job(job, article)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - worker boundary, error text may contain secrets
            # Do not log model output, provider bodies, retained sources, or credentials.
            log.warning("Website generation failed type=%s", type(exc).__name__)
            if job is not None:
                try:
                    await site_news.fail_job(job, type(exc).__name__)
                except sqlite3.Error:
                    log.warning("Website retry could not be recorded; lease recovery will retry")
            await asyncio.sleep(10)


def run() -> None:
    cfg = config.load()
    if not cfg.website_news_enabled:
        raise RuntimeError("Enable WEBSITE_NEWS_ENABLED to run the website worker")
    token = config._secret("NEWS_API_TOKEN", "NEWS_API_TOKEN_FILE", "/app/data/news_api_token")
    app = create_app(token)

    async def lifecycle(_app):
        await sqlite.init()
        content_ai = build_content_ai(cfg)
        task = asyncio.create_task(article_worker(content_ai, lease_seconds=cfg.ai_timeout_seconds * 8 + 120))
        _app[_WORKER_KEY] = task
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await content_ai.aclose()
            await sqlite.close()

    app.cleanup_ctx.append(lifecycle)
    web.run_app(app, host=os.getenv("NEWS_API_BIND", "127.0.0.1"),
                port=config._bounded_int("NEWS_API_PORT", 8088, minimum=1, maximum=65535),
                access_log=None)


if __name__ == "__main__":
    run()
