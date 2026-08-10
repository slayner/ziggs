"""Crash report: public route limits and safe Discord publishing."""
import asyncio

import httpx
from pydantic import ValidationError

from app.api.routes.companion import (
    _CRASH_REPORT_CHANNEL_ID,
    _crash_log,
    _crash_rate_ok,
    _send_crash_to_discord,
    CrashReportIn,
)


INSTALL = "a" * 32


def _report(**changes) -> CrashReportIn:
    data = {
        "kind": "rust_panic",
        "version": "0.1.10",
        "os": "windows",
        "arch": "x86_64",
        "created_at": "2026-08-09T12:00:00Z",
        "uptime_ms": 1234,
        "process_id": 42,
        "thread": "tokio-runtime-worker",
        "message": "boom",
        "location": "src/lib.rs:10:2",
        "backtrace": "stack",
        "logs": "last lines",
    }
    data.update(changes)
    return CrashReportIn(**data)


def setup_function(_=None):
    _crash_log.clear()


def test_limita_por_install_e_por_ip():
    for _ in range(3):
        assert _crash_rate_ok(INSTALL, "1.2.3.4")
    assert not _crash_rate_ok(INSTALL, "5.6.7.8"), "switching IP doesn't bypass the install bucket"
    assert not _crash_rate_ok("b" * 32, "1.2.3.4"), "switching install doesn't bypass the IP bucket"
    assert _crash_rate_ok("b" * 32, "5.6.7.8")


def test_payload_tem_tetos():
    try:
        _report(message="x" * 4_001)
    except ValidationError:
        pass
    else:
        raise AssertionError("message over the ceiling should be rejected")


def test_discord_recebe_anexo_sem_mencoes():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        seen["body"] = await request.aread()
        return httpx.Response(200, json={"id": "1"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await _send_crash_to_discord(_report(), INSTALL, "secret", client)

    asyncio.run(run())
    request = seen["request"]
    body = seen["body"]
    assert str(request.url) == f"https://discord.com/api/channels/{_CRASH_REPORT_CHANNEL_ID}/messages"
    assert request.headers["authorization"] == "Bot secret"
    assert b'allowed_mentions' in body and b'crash-report.json' in body
    assert b'@everyone' not in body


if __name__ == "__main__":
    test_limita_por_install_e_por_ip()
    test_payload_tem_tetos()
    test_discord_recebe_anexo_sem_mencoes()
    print("companion crash report OK")
