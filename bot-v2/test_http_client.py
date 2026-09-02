import asyncio
import os

import aiohttp

import http_client


class _Response:
    status = 200

    async def json(self):
        return {"ok": True}


class _Context:
    async def __aenter__(self):
        return _Response()

    async def __aexit__(self, *_args):
        return False


class _Session:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def request(self, *_args, **_kwargs):
        self.calls += 1
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        return _Context()


async def main() -> None:
    os.environ["BOT_SITE_URL"] = "http://backend.test"
    os.environ["BOT_API_SECRET"] = "test"
    original = http_client.session
    try:
        reset = _Session(aiohttp.ClientConnectionError("reset"))
        http_client.session = lambda: reset
        assert await http_client.post_json(
            "/write", {}, attempts=2, queue_on_failure=False,
        ) == {"ok": True}
        assert reset.calls == 2

        timeout = _Session(asyncio.TimeoutError())
        http_client.session = lambda: timeout
        assert await http_client.post_json(
            "/write", {}, attempts=2, queue_on_failure=False,
        ) is None
        assert timeout.calls == 1

        unavailable = _Session(aiohttp.ClientConnectionError("offline"))
        http_client.session = lambda: unavailable
        try:
            await http_client.post_json(
                "/write", {}, attempts=1, queue_on_failure=False,
                raise_on_unavailable=True,
            )
        except http_client.BackendUnavailable:
            pass
        else:
            raise AssertionError("interactive write must expose backend outage")
    finally:
        http_client.session = original
    print("http client retry policy: ok")


if __name__ == "__main__":
    asyncio.run(main())
