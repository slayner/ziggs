"""Traduções das strings visíveis aos usuários do bot — pt/en/es, escolhidas
por servidor (Guild.settings.bot_language no site, default "pt"). Ver
cogs.general.guild_lang()."""

DEFAULT_LANG = "pt"

T: dict[str, dict[str, str]] = {
    "cmd_disabled": {
        "pt": "Este comando está desativado neste servidor.",
        "en": "This command is disabled on this server.",
        "es": "Este comando está desactivado en este servidor.",
    },
    "no_permission": {
        "pt": "Você não tem permissão para usar este comando.",
        "en": "You don't have permission to use this command.",
        "es": "No tienes permiso para usar este comando.",
    },
    "not_found_target": {
        "pt": "❌ Não encontrei `{alvo}`.",
        "en": "❌ Couldn't find `{alvo}`.",
        "es": "❌ No encontré `{alvo}`.",
    },
    "guild_no_icon": {
        "pt": "**{name}** não tem ícone.",
        "en": "**{name}** has no icon.",
        "es": "**{name}** no tiene ícono.",
    },
    "no_banner": {
        "pt": "**{name}** não tem banner.",
        "en": "**{name}** has no banner.",
        "es": "**{name}** no tiene banner.",
    },
    "global_avatar_link": {
        "pt": "[Avatar global]({url})",
        "en": "[Global avatar]({url})",
        "es": "[Avatar global]({url})",
    },

    # registration.py
    "reason_no_albion_guild": {
        "pt": "A guilda do Albion deste servidor ainda não foi configurada no site (Config → Guilda de Albion).",
        "en": "This server's Albion guild hasn't been configured on the site yet (Config → Albion Guild).",
        "es": "El gremio de Albion de este servidor aún no fue configurado en el sitio (Config → Gremio de Albion).",
    },
    "reason_no_role_configured": {
        "pt": "O cargo de registro ainda não foi configurado no site (Config → Cargo de Registro).",
        "en": "The registration role hasn't been configured on the site yet (Config → Register Role).",
        "es": "El rol de registro aún no fue configurado en el sitio (Config → Rol de Registro).",
    },
    "reason_not_found": {
        "pt": "Personagem não encontrado no Albion Online. Verifique o nick exato.",
        "en": "Character not found on Albion Online. Check the exact nickname.",
        "es": "Personaje no encontrado en Albion Online. Verifica el nick exacto.",
    },
    "reason_not_in_guild": {
        "pt": "Esse personagem não está na guilda configurada (nem numa guilda aliada permitida) para este servidor.",
        "en": "This character isn't in the configured guild (nor in an allowed allied guild) for this server.",
        "es": "Ese personaje no está en el gremio configurado (ni en un gremio aliado permitido) para este servidor.",
    },
    "reason_ally_not_allowed": {
        "pt": "Esse personagem é de uma guilda aliada, mas essa guilda não está na lista de aliados permitidos.",
        "en": "This character belongs to an allied guild, but that guild isn't on the allowed allies list.",
        "es": "Ese personaje es de un gremio aliado, pero ese gremio no está en la lista de aliados permitidos.",
    },
    "reason_already_registered": {
        "pt": "Esse personagem já está registrado por outra pessoa neste servidor.",
        "en": "This character is already registered by someone else on this server.",
        "es": "Ese personaje ya está registrado por otra persona en este servidor.",
    },
    "register_generic_fail": {
        "pt": "Não foi possível concluir o registro.",
        "en": "Couldn't complete the registration.",
        "es": "No fue posible completar el registro.",
    },
    "user_not_found_in_server": {
        "pt": "❌ Não encontrei o usuário `{discord_raw}` neste servidor.",
        "en": "❌ Couldn't find the user `{discord_raw}` in this server.",
        "es": "❌ No encontré al usuario `{discord_raw}` en este servidor.",
    },
    "retry_later": {
        "pt": "⚠️ Não foi possível verificar agora — tente de novo em alguns instantes.",
        "en": "⚠️ Couldn't verify right now — try again in a moment.",
        "es": "⚠️ No fue posible verificar ahora — inténtalo de nuevo en un momento.",
    },
    "role_missing": {
        "pt": "✅ Personagem verificado, mas o cargo configurado não existe mais neste servidor — avise a staff.",
        "en": "✅ Character verified, but the configured role no longer exists on this server — let staff know.",
        "es": "✅ Personaje verificado, pero el rol configurado ya no existe en este servidor — avisa al staff.",
    },
    "role_forbidden": {
        "pt": "✅ Personagem verificado, mas faltam permissões pro bot atribuir o cargo (cargo do bot precisa ficar acima dele).",
        "en": "✅ Character verified, but the bot lacks permission to assign the role (the bot's role must be above it).",
        "es": "✅ Personaje verificado, pero al bot le faltan permisos para asignar el rol (el rol del bot debe estar por encima).",
    },
    "register_success": {
        "pt": "✅ {who} vinculado a **{nick}** — cargo {role} liberado.",
        "en": "✅ {who} linked to **{nick}** — {role} role unlocked.",
        "es": "✅ {who} vinculado a **{nick}** — rol {role} liberado.",
    },
    "register_who_self": {
        "pt": "Você foi",
        "en": "You were",
        "es": "Fuiste",
    },
    "register_who_other": {
        "pt": "{mention} foi",
        "en": "{mention} was",
        "es": "{mention} fue",
    },
    "register_usage": {
        "pt": "Informe seu nick do Albion, ex: `/register register:SeuNick`.\nPra registrar outra pessoa, informe o nick e o usuário do Discord juntos (em qualquer ordem).",
        "en": "Provide your Albion nickname, e.g.: `/register register:YourNick`.\nTo register someone else, provide the nickname and the Discord user together (in any order).",
        "es": "Indica tu nick de Albion, ej: `/register register:TuNick`.\nPara registrar a otra persona, indica el nick y el usuario de Discord juntos (en cualquier orden).",
    },
    "processing": {
        "pt": "⏳ Processando…",
        "en": "⏳ Processing…",
        "es": "⏳ Procesando…",
    },
    "register_retrying": {
        "pt": "🔄 A API do Albion Online está instável — colocamos sua verificação na fila e vamos tentar de novo automaticamente (tentativa {attempt})…",
        "en": "🔄 Albion Online's API is unstable — your verification was queued and we'll automatically try again (attempt {attempt})…",
        "es": "🔄 La API de Albion Online está inestable — pusimos tu verificación en cola y vamos a intentar de nuevo automáticamente (intento {attempt})…",
    },
    "register_queued_background": {
        "pt": "⏳ A API do Albion Online continua instável. Seu registro segue na fila em segundo plano — assim que resolver, você recebe uma mensagem direta com o resultado, sem precisar rodar o comando de novo.",
        "en": "⏳ Albion Online's API is still unstable. Your registration stays queued in the background — once it resolves, you'll get a DM with the result, no need to run the command again.",
        "es": "⏳ La API de Albion Online sigue inestable. Tu registro sigue en cola en segundo plano — en cuanto se resuelva, recibirás un mensaje directo con el resultado, sin necesidad de ejecutar el comando de nuevo.",
    },
    "register_disambiguate_prompt": {
        "pt": "Não consegui saber qual dos dois é o nick do Albion e qual é o usuário do Discord. Escolha:",
        "en": "I couldn't tell which of the two is the Albion nickname and which is the Discord user. Choose:",
        "es": "No pude saber cuál de los dos es el nick de Albion y cuál es el usuario de Discord. Elige:",
    },
    "unregister_success": {
        "pt": "✅ Registro de `{alvo}` removido — a tag é removida imediatamente.",
        "en": "✅ Registration for `{alvo}` removed — the role is removed immediately.",
        "es": "✅ Registro de `{alvo}` eliminado — el rol se elimina de inmediato.",
    },
    "unregister_roles_removed": {
        "pt": " Cargo(s) removido(s): {roles}",
        "en": " Role(s) removed: {roles}",
        "es": " Rol(es) eliminado(s): {roles}",
    },
    "unregister_not_found": {
        "pt": "ℹ️ Não encontrei nenhum registro ativo pra `{alvo}`.",
        "en": "ℹ️ Couldn't find any active registration for `{alvo}`.",
        "es": "ℹ️ No encontré ningún registro activo para `{alvo}`.",
    },

    # economy.py
    "balance_display": {
        "pt": "💰 Saldo: **{balance}**",
        "en": "💰 Balance: **{balance}**",
        "es": "💰 Saldo: **{balance}**",
    },
    "balance_fetch_fail": {
        "pt": "⚠️ Não consegui consultar o saldo agora.",
        "en": "⚠️ Couldn't check the balance right now.",
        "es": "⚠️ No pude consultar el saldo ahora.",
    },
    "pay_self": {
        "pt": "❌ Você não pode pagar a si mesmo.",
        "en": "❌ You can't pay yourself.",
        "es": "❌ No puedes pagarte a ti mismo.",
    },
    "pay_bot": {
        "pt": "❌ Você não pode pagar a um bot.",
        "en": "❌ You can't pay a bot.",
        "es": "❌ No puedes pagarle a un bot.",
    },
    "pay_no_balance": {
        "pt": "❌ Você não tem saldo positivo para enviar (atual: `{balance}`).",
        "en": "❌ You don't have a positive balance to send (current: `{balance}`).",
        "es": "❌ No tienes saldo positivo para enviar (actual: `{balance}`).",
    },
    "invalid_amount_full": {
        "pt": "❌ Valor inválido. Aceito: `1500000`, `2,500,000`, `1.5m`, `150k`, `1.000.000` ou `all`/`tudo`.",
        "en": "❌ Invalid amount. Accepted: `1500000`, `2,500,000`, `1.5m`, `150k`, `1.000.000` or `all`/`tudo`.",
        "es": "❌ Valor inválido. Acepto: `1500000`, `2,500,000`, `1.5m`, `150k`, `1.000.000` o `all`/`tudo`.",
    },
    "invalid_amount": {
        "pt": "❌ Valor inválido.",
        "en": "❌ Invalid amount.",
        "es": "❌ Valor inválido.",
    },
    "pay_process_fail": {
        "pt": "⚠️ Não consegui processar o pagamento agora.",
        "en": "⚠️ Couldn't process the payment right now.",
        "es": "⚠️ No pude procesar el pago ahora.",
    },
    "pay_insufficient": {
        "pt": "❌ Saldo insuficiente. Você tem apenas `{balance}`.",
        "en": "❌ Insufficient balance. You only have `{balance}`.",
        "es": "❌ Saldo insuficiente. Solo tienes `{balance}`.",
    },
    "pay_success": {
        "pt": "{sender} enviou **{amount}** para {target}",
        "en": "{sender} sent **{amount}** to {target}",
        "es": "{sender} envió **{amount}** a {target}",
    },
    "add_fail": {
        "pt": "⚠️ Não consegui adicionar o saldo agora.",
        "en": "⚠️ Couldn't add to the balance right now.",
        "es": "⚠️ No pude agregar al saldo ahora.",
    },
    "add_success": {
        "pt": "{actor} adicionou **{amount}** ao saldo de {target}",
        "en": "{actor} added **{amount}** to {target}'s balance",
        "es": "{actor} agregó **{amount}** al saldo de {target}",
    },
    "add_success_multi": {
        "pt": "{actor} adicionou **{amount}** ao saldo de {count} usuário(s): {targets}",
        "en": "{actor} added **{amount}** to {count} user(s)' balance: {targets}",
        "es": "{actor} agregó **{amount}** al saldo de {count} usuario(s): {targets}",
    },
    "confirm_addmoney_title": {
        "pt": "Confirmar adição em massa?",
        "en": "Confirm bulk addition?",
        "es": "¿Confirmar adición masiva?",
    },
    "confirm_addmoney_desc": {
        "pt": "Adicionar **{amount}** ao saldo de {count} usuário(s):\n{targets}",
        "en": "Add **{amount}** to {count} user(s)' balance:\n{targets}",
        "es": "Agregar **{amount}** al saldo de {count} usuario(s):\n{targets}",
    },
    "confirm_only_author": {
        "pt": "Apenas quem usou o comando pode confirmar ou cancelar.",
        "en": "Only whoever ran the command can confirm or cancel.",
        "es": "Solo quien usó el comando puede confirmar o cancelar.",
    },
    "confirm_btn": {"pt": "Confirmar", "en": "Confirm", "es": "Confirmar"},
    "cancel_btn": {"pt": "Cancelar", "en": "Cancel", "es": "Cancelar"},
    "prefix_cancelled": {
        "pt": "❌ Cancelado.",
        "en": "❌ Cancelled.",
        "es": "❌ Cancelado.",
    },
    "prefix_no_targets": {
        "pt": "❌ Não encontrei nenhum usuário ou cargo mencionado na mensagem.",
        "en": "❌ Couldn't find any mentioned user or role in the message.",
        "es": "❌ No encontré ningún usuario o rol mencionado en el mensaje.",
    },
    "prefix_no_amount": {
        "pt": "❌ Não encontrei um valor válido na mensagem (ex: 100k, 1.5m, 2,500,000).",
        "en": "❌ Couldn't find a valid amount in the message (e.g.: 100k, 1.5m, 2,500,000).",
        "es": "❌ No encontré un valor válido en el mensaje (ej: 100k, 1.5m, 2,500,000).",
    },
    "remove_no_balance": {
        "pt": "⚠️ {target} não tem saldo positivo a remover (atual: `{balance}`).",
        "en": "⚠️ {target} has no positive balance to remove (current: `{balance}`).",
        "es": "⚠️ {target} no tiene saldo positivo para remover (actual: `{balance}`).",
    },
    "remove_fail": {
        "pt": "⚠️ Não consegui remover o saldo agora.",
        "en": "⚠️ Couldn't remove from the balance right now.",
        "es": "⚠️ No pude remover el saldo ahora.",
    },
    "remove_success": {
        "pt": "{actor} removeu **{amount}** do saldo de {target}",
        "en": "{actor} removed **{amount}** from {target}'s balance",
        "es": "{actor} removió **{amount}** del saldo de {target}",
    },
    "remove_success_multi": {
        "pt": "{actor} removeu um total de **{amount}** de {count} usuário(s): {targets}",
        "en": "{actor} removed a total of **{amount}** from {count} user(s): {targets}",
        "es": "{actor} removió un total de **{amount}** de {count} usuario(s): {targets}",
    },
    "remove_multi_skipped": {
        "pt": "\n⚠️ Sem saldo positivo pra remover (pulado): {targets}",
        "en": "\n⚠️ No positive balance to remove (skipped): {targets}",
        "es": "\n⚠️ Sin saldo positivo para remover (omitido): {targets}",
    },
    "remove_multi_all_skipped": {
        "pt": "⚠️ Nenhum dos alvos tinha saldo positivo pra remover.",
        "en": "⚠️ None of the targets had a positive balance to remove.",
        "es": "⚠️ Ninguno de los objetivos tenía saldo positivo para remover.",
    },
    "confirm_removemoney_title": {
        "pt": "Confirmar remoção em massa?",
        "en": "Confirm bulk removal?",
        "es": "¿Confirmar remoción masiva?",
    },
    "confirm_removemoney_desc": {
        "pt": "Remover **{amount}** do saldo de {count} usuário(s):\n{targets}",
        "en": "Remove **{amount}** from {count} user(s)' balance:\n{targets}",
        "es": "Remover **{amount}** del saldo de {count} usuario(s):\n{targets}",
    },
    "confirm_removemoney_desc_all": {
        "pt": "Remover TODO o saldo de {count} usuário(s):\n{targets}",
        "en": "Remove ALL balance from {count} user(s):\n{targets}",
        "es": "Remover TODO el saldo de {count} usuario(s):\n{targets}",
    },
    "remove_negative_warn": {
        "pt": "\n⚠️ Saldo negativo (empréstimo/punição)",
        "en": "\n⚠️ Negative balance (loan/penalty)",
        "es": "\n⚠️ Saldo negativo (préstamo/penalización)",
    },
    "tx_footer": {
        "pt": "{id}",
        "en": "{id}",
        "es": "{id}",
    },
    "tx_footer_multi": {
        "pt": "{ids}",
        "en": "{ids}",
        "es": "{ids}",
    },
    "undo_fail": {
        "pt": "⚠️ Não consegui reverter a transação agora.",
        "en": "⚠️ Couldn't revert the transaction right now.",
        "es": "⚠️ No pude revertir la transacción ahora.",
    },
    "undo_not_found": {
        "pt": "❌ Não encontrei nenhuma transação com o ID `{id}`.",
        "en": "❌ Couldn't find any transaction with ID `{id}`.",
        "es": "❌ No encontré ninguna transacción con el ID `{id}`.",
    },
    "undo_already_undone": {
        "pt": "⚠️ A transação `{id}` já foi revertida antes.",
        "en": "⚠️ Transaction `{id}` was already reverted before.",
        "es": "⚠️ La transacción `{id}` ya fue revertida antes.",
    },
    "undo_success": {
        "pt": "↩️ Transação `{id}` revertida — **{amount}** devolvido(s).",
        "en": "↩️ Transaction `{id}` reverted — **{amount}** returned.",
        "es": "↩️ Transacción `{id}` revertida — **{amount}** devuelto(s).",
    },
    "stats_fail": {
        "pt": "⚠️ Não consegui consultar as estatísticas agora.",
        "en": "⚠️ Couldn't check the stats right now.",
        "es": "⚠️ No pude consultar las estadísticas ahora.",
    },
    "stats_title": {
        "pt": "Estatísticas econômicas",
        "en": "Economy stats",
        "es": "Estadísticas económicas",
    },
    "stats_users_field": {
        "pt": "👥 Usuários com saldo",
        "en": "👥 Users with balance",
        "es": "👥 Usuarios con saldo",
    },
    "stats_total_field": {
        "pt": "📊 Total em circulação",
        "en": "📊 Total in circulation",
        "es": "📊 Total en circulación",
    },
    "leaderboard_fail": {
        "pt": "⚠️ Não consegui consultar o leaderboard agora.",
        "en": "⚠️ Couldn't check the leaderboard right now.",
        "es": "⚠️ No pude consultar el leaderboard ahora.",
    },
    "leaderboard_empty": {
        "pt": "Ninguém tem saldo ainda — leaderboard vazio.",
        "en": "Nobody has a balance yet — empty leaderboard.",
        "es": "Nadie tiene saldo todavía — leaderboard vacío.",
    },
    "leaderboard_only_author": {
        "pt": "Apenas quem usou o comando pode controlar a paginação.",
        "en": "Only whoever used the command can control the pagination.",
        "es": "Solo quien usó el comando puede controlar la paginación.",
    },
    "leaderboard_empty_page": {
        "pt": "*Nada para mostrar nesta página.*",
        "en": "*Nothing to show on this page.*",
        "es": "*Nada para mostrar en esta página.*",
    },
    "leaderboard_page_footer": {
        "pt": "Página {page}/{max_page}",
        "en": "Page {page}/{max_page}",
        "es": "Página {page}/{max_page}",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    entry = T.get(key)
    if entry is None:
        return key
    s = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    return s.format(**kwargs) if kwargs else s


# Nomes/descrições de comando e opção — localizados via Translator do Discord,
# por LOCALE DO CLIENTE de cada membro (não pelo bot_language da guilda: são
# fixos no sync, não dá pra variar por servidor). Só "en"/"es": o texto
# português já embutido no @app_commands.command/describe é o default (pt-BR
# e qualquer locale sem tradução caem nele). Ver localization.py.
CMD_I18N: dict[str, dict[str, str]] = {
    "opt_name_alvo": {"en": "user", "es": "usuario"},
    "opt_name_quantia": {"en": "amount", "es": "cantidad"},

    "cmd_desc_avatar": {"en": "Shows a user's or server's avatar", "es": "Muestra el avatar de un usuario o servidor"},
    "cmd_desc_banner": {"en": "Shows a user's or server's banner", "es": "Muestra el banner de un usuario o servidor"},
    "opt_desc_avatar_banner_alvo": {
        "en": "Server ID, @mention, user ID/name, or nickname (default: yourself)",
        "es": "ID de servidor, @mención, ID/nombre de usuario o apodo (por defecto: tú mismo)",
    },

    "cmd_desc_register": {
        "en": "Links an Albion nickname to a Discord account and unlocks the role",
        "es": "Vincula un nick de Albion a una cuenta de Discord y libera el rol",
    },
    "opt_desc_register": {
        "en": "Your Albion nickname — or, to register someone else, nickname + Discord user (any order)",
        "es": "Tu nick de Albion — o, para registrar a otra persona, nick + usuario de Discord (cualquier orden)",
    },
    "cmd_desc_unregister": {"en": "Removes a member's registration and role", "es": "Elimina el registro y el rol de un miembro"},
    "opt_desc_unregister_alvo": {
        "en": "Mention, ID, Discord username, or the member's Albion nickname",
        "es": "Mención, ID, nombre de usuario de Discord, o nick de Albion del miembro",
    },

    "cmd_desc_balance": {
        "en": "Shows a user's balance (yours, if none given)",
        "es": "Muestra el saldo de un usuario (el tuyo, si no se indica ninguno)",
    },
    "opt_desc_balance_alvo": {"en": "@mention, ID, or name (default: yourself)", "es": "@mención, ID o nombre (por defecto: tú mismo)"},

    "cmd_desc_pay": {"en": "Transfers silver from your balance to another user", "es": "Transfiere plata de tu saldo a otro usuario"},
    "opt_desc_pay_alvo": {"en": "Who will receive it", "es": "Quién va a recibir"},
    "opt_desc_pay_quantia": {
        "en": "How much to send (e.g.: 100k, 1.5m, 2,500,000) or `all`/`tudo`",
        "es": "Cuánto enviar (ej: 100k, 1.5m, 2,500,000) o `all`/`tudo`",
    },

    "cmd_desc_addmoney": {"en": "Adds silver to a user's balance", "es": "Agrega plata al saldo de un usuario"},
    "opt_desc_addmoney_alvo": {"en": "Target user", "es": "Usuario objetivo"},
    "opt_desc_addmoney_quantia": {"en": "How much to add (e.g.: 100k, 1.5m)", "es": "Cuánto agregar (ej: 100k, 1.5m)"},

    "cmd_desc_removemoney": {
        "en": "Removes silver from a user's balance (no value = removes everything)",
        "es": "Remueve plata del saldo de un usuario (sin valor = remueve todo)",
    },
    "opt_desc_removemoney_alvo": {"en": "Target user", "es": "Usuario objetivo"},
    "opt_desc_removemoney_quantia": {
        "en": "How much to remove (blank or `all`/`tudo` = removes everything)",
        "es": "Cuánto remover (vacío o `all`/`tudo` = remueve todo)",
    },

    "cmd_desc_leaderboard": {"en": "Ranking of users by current silver balance", "es": "Ranking de usuarios por saldo actual de plata"},
    "cmd_desc_economystats": {"en": "Shows a snapshot of the server's economy", "es": "Muestra un resumen de la economía del servidor"},

    "cmd_desc_undo": {"en": "Reverts an economy transaction by its ID", "es": "Revierte una transacción de economía por su ID"},
    "opt_desc_undo_id": {
        "en": "Transaction ID to revert (see the original embed's footer)",
        "es": "ID de la transacción a revertir (ver el pie del embed original)",
    },
}
