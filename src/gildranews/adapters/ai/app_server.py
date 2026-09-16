from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import OrderedDict

import httpx

MAX_RESPONSE_BYTES = 8 << 20
RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

log = logging.getLogger(__name__)


class AppServerError(RuntimeError):
    """A safe, non-secret-bearing App Server failure."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def parse_agui_sse(body: str) -> str:
    """Return the last non-empty assistant message from an AG-UI event stream."""
    messages: OrderedDict[str, list[str]] = OrderedDict()
    anonymous: list[str] = []
    current_id = ""
    data_lines: list[str] = []

    def consume() -> None:
        nonlocal current_id
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        data_lines.clear()
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppServerError(
                "App Server вернул некорректное SSE-событие",
                retryable=True,
            ) from exc
        event_type = event.get("type")
        if event_type == "RUN_ERROR":
            raise AppServerError(
                "App Server завершил запрос событием RUN_ERROR",
                retryable=True,
            )
        if event_type == "TEXT_MESSAGE_START":
            if event.get("role") in (None, "", "assistant"):
                current_id = str(event.get("messageId") or "")
                if current_id:
                    messages.setdefault(current_id, [])
        elif event_type == "TEXT_MESSAGE_CONTENT":
            message_id = str(event.get("messageId") or current_id)
            delta = str(event.get("delta") or "")
            if message_id:
                messages.setdefault(message_id, []).append(delta)
            else:
                anonymous.append(delta)
        elif event_type == "TEXT_MESSAGE_END":
            ended_id = str(event.get("messageId") or current_id)
            if ended_id == current_id:
                current_id = ""

    for line in body.splitlines():
        if not line:
            consume()
        elif line.startswith(":"):
            continue
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())
    consume()

    for chunks in reversed(messages.values()):
        candidate = "".join(chunks).strip()
        if candidate:
            return candidate
    candidate = "".join(anonymous).strip()
    if not candidate:
        raise AppServerError("App Server вернул пустой ответ", retryable=True)
    return candidate


class AppServerClient:
    def __init__(
        self,
        endpoint: str,
        token: str,
        model: str = "gpt-5.6-luna",
        reasoning_effort: str = "xhigh",
        timeout_seconds: float = 240,
        http_client: httpx.AsyncClient | None = None,
        retry_delays: tuple[float, ...] = (1.0, 3.0),
    ) -> None:
        self._endpoint = endpoint
        self._token = token
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._retry_delays = retry_delays
        self._request_lock = asyncio.Lock()

    async def complete(self, system: str, user: str) -> str:
        async with self._request_lock:
            return await self._complete_with_retries(system, user)

    async def _complete_with_retries(self, system: str, user: str) -> str:
        for attempt in range(len(self._retry_delays) + 1):
            try:
                return await self._complete_once(system, user)
            except AppServerError as exc:
                if not exc.retryable or attempt >= len(self._retry_delays):
                    raise
                delay = self._retry_delays[attempt]
                log.warning(
                    "Temporary App Server failure; retrying in %.1fs (attempt %d/%d): %s",
                    delay,
                    attempt + 2,
                    len(self._retry_delays) + 1,
                    exc,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def _complete_once(self, system: str, user: str) -> str:
        request_id = uuid.uuid4().hex
        payload = {
            "threadId": f"gildranews-thread-{request_id}",
            "runId": f"gildranews-run-{request_id}",
            "state": {},
            "messages": [
                {"id": f"{request_id}-system", "role": "system", "content": system},
                {"id": f"{request_id}-user", "role": "user", "content": user},
            ],
            "tools": [],
            "context": [],
            "forwardedProps": {
                "openbotAgentModel": self._model,
                "openbotAgentReasoningEffort": self._reasoning_effort,
            },
        }
        headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
        if self._token:
            headers["X-OpenBot-Agent-Token"] = self._token
        try:
            async with self._http.stream(
                "POST", self._endpoint, headers=headers, json=payload,
            ) as response:
                if response.status_code >= 400:
                    raise AppServerError(
                        f"App Server вернул HTTP {response.status_code}",
                        retryable=response.status_code in RETRYABLE_HTTP_STATUSES,
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise AppServerError("Ответ App Server превысил лимит 8 MiB")
                    chunks.append(chunk)
        except AppServerError:
            raise
        except httpx.HTTPError as exc:
            raise AppServerError(
                "Не удалось связаться с App Server",
                retryable=True,
            ) from exc
        try:
            body = b"".join(chunks).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise AppServerError(
                "App Server вернул ответ в неверной кодировке",
                retryable=True,
            ) from exc
        return parse_agui_sse(body)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
