import asyncio
from types import SimpleNamespace

import cogs.lootlog_threads as lootlog_threads


async def test_thread_sem_cabecalho_nao_e_confirmada_e_e_reparada() -> None:
    sent, acknowledgements = [], []

    class Thread:
        id = 99
        name = "🪵 Log — Event #7 Teste"

        def __init__(self):
            self.messages = []

        async def send(self, **kwargs):
            sent.append(True)
            if len(sent) == 1:
                raise asyncio.TimeoutError()
            self.messages.append(SimpleNamespace(
                author=SimpleNamespace(id=42), embeds=[kwargs["embed"]],
            ))

        async def history(self, **_kwargs):
            for message in self.messages:
                yield message

    class Channel:
        id = 55
        threads = []

        async def create_thread(self, **_kwargs):
            self.threads.append(thread)
            return thread

    class Guild:
        id = 1

        def get_channel(self, _channel_id):
            return channel

        def get_thread(self, _thread_id):
            return None

    channel = Channel()
    thread = Thread()
    cog = lootlog_threads.LootlogThreads(SimpleNamespace())
    original = (lootlog_threads._guild_command_config, lootlog_threads._get,
                lootlog_threads._post, lootlog_threads.guild_lang_for)

    async def config(_guild_id):
        return {"lootlog_thread_channel_id": "55"}

    async def get(_path):
        return {"create": [{"event_id": 7, "title": "Teste"}], "archive": []}

    async def post(_path, body):
        acknowledgements.append(body)
        return {"ok": True}

    async def lang(_guild_id):
        return "en"

    lootlog_threads._guild_command_config = config
    lootlog_threads._get = get
    lootlog_threads._post = post
    lootlog_threads.guild_lang_for = lang
    try:
        event = {"event_id": 7, "title": "Teste"}
        await cog._create_thread(Guild(), channel, "en", event)
        assert not acknowledgements, "não pode confirmar thread sem cabeçalho"
        await cog._create_thread(Guild(), channel, "en", event)
    finally:
        (lootlog_threads._guild_command_config, lootlog_threads._get,
         lootlog_threads._post, lootlog_threads.guild_lang_for) = original

    assert len(channel.threads) == 1, "tentativa de reparo não pode duplicar a thread"
    assert len(sent) == 2, "a segunda tentativa precisa postar o cabeçalho"
    assert acknowledgements == [{"lootlog_thread_id": "99", "clear_dirty": True}]


if __name__ == "__main__":
    asyncio.run(test_thread_sem_cabecalho_nao_e_confirmada_e_e_reparada())
    print("lootlog thread recovery: ok")
