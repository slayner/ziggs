"""Regressão da paginação das batalhas recentes servida pelo cache."""
import asyncio
from types import SimpleNamespace

from app.api.routes.battles import list_battles


def test_segunda_pagina_usa_janela_do_cache():
    async def run():
        rows = [
            {"public_id": f"battle-{i}", "region": "americas"}
            for i in range(15)
        ]

        class Db:
            async def get(self, _model, key):
                assert key == "recent_battles"
                return SimpleNamespace(payload={"rows": rows, "counts": {"americas": 25}})

        response = await list_battles(
            limit=10, offset=10, min_players=5, min_kills=5,
            regions="americas", db=Db(),
        )
        assert response["total"] == 25
        assert [battle["public_id"] for battle in response["battles"]] == [
            f"battle-{i}" for i in range(10, 15)
        ]

    asyncio.run(run())


if __name__ == "__main__":
    test_segunda_pagina_usa_janela_do_cache()
    print("battles cache pagination: ok")
