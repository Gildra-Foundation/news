from __future__ import annotations

import pytest
from telethon import errors

from gildranews.init_session import authorize_with_qr


class _QrLogin:
    def __init__(self) -> None:
        self.url = "tg://login?token=first"
        self.wait_count = 0
        self.recreate_count = 0

    async def wait(self) -> None:
        self.wait_count += 1
        if self.wait_count == 1:
            raise TimeoutError
        raise errors.SessionPasswordNeededError(request=None)

    async def recreate(self) -> None:
        self.recreate_count += 1
        self.url = "tg://login?token=second"


class _Client:
    def __init__(self, qr: _QrLogin) -> None:
        self.qr = qr
        self.password: str | None = None

    async def qr_login(self) -> _QrLogin:
        return self.qr

    async def sign_in(self, *, password: str) -> None:
        self.password = password


@pytest.mark.asyncio
async def test_qr_authorization_refreshes_expired_code_and_completes_2fa() -> None:
    qr = _QrLogin()
    client = _Client(qr)
    rendered_urls: list[str] = []

    await authorize_with_qr(
        client,
        render_qr=rendered_urls.append,
        read_password=lambda: "new-cloud-password",
    )

    assert rendered_urls == [
        "tg://login?token=first",
        "tg://login?token=second",
    ]
    assert qr.recreate_count == 1
    assert client.password == "new-cloud-password"
