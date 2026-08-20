import asyncio

from fastapi import FastAPI
import httpx

from app import spa


def test_spa_uses_the_current_index_after_frontend_deploy(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    index = dist / "index.html"
    index.write_text("<body>old-build</body>", encoding="utf-8")
    monkeypatch.setattr(spa, "_DIST", dist)

    app = FastAPI()
    spa.install(app)
    index.write_text("<body>new-build</body>", encoding="utf-8")

    async def request() -> str:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return (await client.get("/e/" + "a" * 32)).text

    assert "new-build" in asyncio.run(request())
