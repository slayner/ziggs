"""Localização nativa do Discord pra nomes/descrições de comando e opção —
cada membro vê no idioma do PRÓPRIO cliente Discord dele (locale pessoal,
configurado no app dele), independente do bot_language da guilda (aquele é
fixo por servidor e só controla o TEXTO DE RESPOSTA dos comandos — ver i18n.t
e cogs.general.guild_lang). São dois eixos diferentes por limitação do
Discord: nome/descrição de opção é fixado no sync (uma vez, pra todo mundo),
não dá pra variar por servidor."""
import discord
from discord import app_commands

from i18n import CMD_I18N


def loc(default_pt: str, key: str) -> app_commands.locale_str:
    """Atalho pra `app_commands.locale_str(texto_pt, key=...)` — usado nos
    decorators @app_commands.command/describe/rename das cogs."""
    return app_commands.locale_str(default_pt, key=key)


_LOCALE_LANG = {
    discord.Locale.brazil_portuguese: "pt",
    discord.Locale.american_english: "en",
    discord.Locale.british_english: "en",
    discord.Locale.spain_spanish: "es",
    discord.Locale.latin_american_spanish: "es",
}


class ZiggsTranslator(app_commands.Translator):
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContextTypes,
    ) -> str | None:
        lang = _LOCALE_LANG.get(locale)
        # None = devolve o texto padrão (pt, já embutido no código) — cobre
        # tanto locales sem tradução quanto o próprio pt-BR.
        if lang is None or lang == "pt":
            return None
        key = string.extras.get("key")
        if not key:
            return None
        return CMD_I18N.get(key, {}).get(lang)
