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
    "unexpected_error": {
        "pt": "⚠️ Algo deu errado ao executar o comando. Tente de novo em instantes — se continuar, avise um admin.",
        "en": "⚠️ Something went wrong running the command. Try again in a moment — if it keeps happening, tell an admin.",
        "es": "⚠️ Algo salió mal al ejecutar el comando. Inténtalo de nuevo en un momento — si persiste, avisa a un admin.",
    },
    "backend_unavailable": {
        "pt": "⚠️ O backend está indisponível no momento. Tente novamente em instantes.",
        "en": "⚠️ The backend is currently unavailable. Try again in a moment.",
        "es": "⚠️ El backend no está disponible en este momento. Inténtalo de nuevo en unos instantes.",
    },
    "cooldown_wait": {
        "pt": "⏳ Calma! Tente de novo em {seconds}s.",
        "en": "⏳ Slow down! Try again in {seconds}s.",
        "es": "⏳ ¡Calma! Inténtalo de nuevo en {seconds}s.",
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
        "pt": "Personagem não encontrado no Albion Online. Verifique o nick exato — se o nick foi renomeado pela SBI, tente o nick antigo.",
        "en": "Character not found on Albion Online. Check the exact nickname — if the nick was renamed by SBI, try the old nickname.",
        "es": "Personaje no encontrado en Albion Online. Verifica el nick exacto — si el nick fue renomeado por SBI, intenta el nick antiguo.",
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
    "reason_human_revoked": {
        "pt": "O registro foi removido por uma decisão no Discord. Execute /register novamente para reativá-lo.",
        "en": "The registration was removed by a Discord decision. Run /register again to reactivate it.",
        "es": "El registro fue eliminado por una decisión en Discord. Ejecuta /register de nuevo para reactivarlo.",
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
        "pt": "Informe seu nick do Albion, ex: `/register nick:SeuNick`.\nPra registrar outra pessoa, use o parâmetro `usuario`.",
        "en": "Provide your Albion nickname, e.g.: `/register nick:YourNick`.\nTo register someone else, use the `usuario` parameter.",
        "es": "Indica tu nick de Albion, ej: `/register nick:TuNick`.\nPara registrar a otra persona, usa el parámetro `usuario`.",
    },
    "processing": {
        "pt": "⏳ Processando…",
        "en": "⏳ Processing…",
        "es": "⏳ Procesando…",
    },
    "register_queued": {
        "pt": "⏳ {who} na fila de verificação — {nick}. Assim que a API do Albion responder, você recebe uma mensagem direta com o resultado.",
        "en": "⏳ {who} queued for verification — {nick}. As soon as the Albion API responds, you'll get a DM with the result.",
        "es": "⏳ {who} en cola de verificación — {nick}. En cuanto la API de Albion responda, recibirás un mensaje directo con el resultado.",
    },
    "register_background_giveup": {
        "pt": "⚠️ O registro de {nick} ({target}) não foi concluído — a API do Albion seguiu instável por tempo demais. Tente /register novamente mais tarde.",
        "en": "⚠️ The registration for {nick} ({target}) couldn't be completed — the Albion API stayed unstable for too long. Try /register again later.",
        "es": "⚠️ El registro de {nick} ({target}) no se completó — la API de Albion siguió inestable por demasiado tiempo. Intenta /register de nuevo más tarde.",
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
    "remove_negative_warn": {
        "pt": "\n⚠️ Saldo negativo (empréstimo/punição)",
        "en": "\n⚠️ Negative balance (loan/penalty)",
        "es": "\n⚠️ Saldo negativo (préstamo/penalización)",
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
    # /bank — gestão do banco da guilda
    "bank_title": {"pt": "Banco da guilda", "en": "Guild bank", "es": "Banco del gremio"},
    "bank_balance_display": {
        "pt": "🏦 Saldo: **{balance}**",
        "en": "🏦 Balance: **{balance}**",
        "es": "🏦 Saldo: **{balance}**",
    },
    "bank_fetch_fail": {
        "pt": "⚠️ Não consegui consultar o saldo do banco agora.",
        "en": "⚠️ Couldn't check the bank balance right now.",
        "es": "⚠️ No pude consultar el saldo del banco ahora.",
    },
    "bank_fail": {
        "pt": "⚠️ Não consegui ajustar o banco agora.",
        "en": "⚠️ Couldn't adjust the bank right now.",
        "es": "⚠️ No pude ajustar el banco ahora.",
    },
    "bank_invalid_action": {
        "pt": "❌ Ação inválida. Use `view`, `add` ou `remove`.",
        "en": "❌ Invalid action. Use `view`, `add`, or `remove`.",
        "es": "❌ Acción inválida. Usa `view`, `add` o `remove`.",
    },
    "bank_add_success": {
        "pt": "{actor} adicionou **{amount}** ao banco da guilda (saldo: `{balance}`)",
        "en": "{actor} added **{amount}** to the guild bank (balance: `{balance}`)",
        "es": "{actor} agregó **{amount}** al banco del gremio (saldo: `{balance}`)",
    },
    "bank_remove_success": {
        "pt": "{actor} removeu **{amount}** do banco da guilda (saldo: `{balance}`)",
        "en": "{actor} removed **{amount}** from the guild bank (balance: `{balance}`)",
        "es": "{actor} retiró **{amount}** del banco del gremio (saldo: `{balance}`)",
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

    # cogs/economy.py — /transactions
    "tx_fetch_fail": {
        "pt": "Não consegui consultar o histórico de transações agora.",
        "en": "Couldn't fetch the transaction history right now.",
        "es": "No pude consultar el historial de transacciones ahora.",
    },
    "tx_empty": {
        "pt": "Nenhuma transação registrada.",
        "en": "No transactions recorded.",
        "es": "Sin transacciones registradas.",
    },
    "tx_empty_page": {
        "pt": "*Nada para mostrar nesta página.*",
        "en": "*Nothing to show on this page.*",
        "es": "*Nada para mostrar en esta página.*",
    },
    "tx_only_author": {
        "pt": "Apenas quem usou o comando pode controlar a paginação.",
        "en": "Only whoever used the command can control the pagination.",
        "es": "Solo quien usó el comando puede controlar la paginación.",
    },
    "tx_title": {
        "pt": "Transações — {user}",
        "en": "Transactions — {user}",
        "es": "Transacciones — {user}",
    },
    "tx_heading": {
        "pt": "Transações de {user}",
        "en": "Transactions for {user}",
        "es": "Transacciones de {user}",
    },
    "tx_page_footer": {
        "pt": "Página {page}/{max_page} · {total} transações",
        "en": "Page {page}/{max_page} · {total} transactions",
        "es": "Página {page}/{max_page} · {total} transacciones",
    },
    "tx_summary": {
        "pt": "Saldo atual: **{balance}** | Total recebido: **{total_earned}**",
        "en": "Current balance: **{balance}** | Total earned: **{total_earned}**",
        "es": "Saldo actual: **{balance}** | Total recibido: **{total_earned}**",
    },
    "tx_system": {"pt": "Sistema", "en": "System", "es": "Sistema"},
    "tx_undone": {"pt": "revertida", "en": "reverted", "es": "revertida"},
    "tx_action_pay_in": {
        "pt": "{actor} enviou {amount} para {target}",
        "en": "{actor} sent {amount} to {target}",
        "es": "{actor} envió {amount} a {target}",
    },
    "tx_action_pay_out": {
        "pt": "{target} enviou {amount} para {counterparty}",
        "en": "{target} sent {amount} to {counterparty}",
        "es": "{target} envió {amount} a {counterparty}",
    },
    "tx_action_add": {
        "pt": "{actor} adicionou {amount} ao saldo de {target}",
        "en": "{actor} added {amount} to {target}'s balance",
        "es": "{actor} añadió {amount} al saldo de {target}",
    },
    "tx_action_remove": {
        "pt": "{actor} removeu {amount} do saldo de {target}",
        "en": "{actor} removed {amount} from {target}'s balance",
        "es": "{actor} retiró {amount} del saldo de {target}",
    },
    "tx_action_forfeit": {
        "pt": "Saldo de {target} foi confiscado",
        "en": "{target}'s balance was forfeited",
        "es": "El saldo de {target} fue confiscado",
    },
    "tx_action_event_payout": {
        "pt": "{actor} registrou pagamento de evento {event} para {target}",
        "en": "{actor} recorded the event payout {event} for {target}",
        "es": "{actor} registró el pago de evento {event} para {target}",
    },
    "tx_action_event_deficit": {
        "pt": "{actor} registrou déficit de evento {event} para {target}",
        "en": "{actor} recorded the event deficit {event} for {target}",
        "es": "{actor} registró el déficit de evento {event} para {target}",
    },
    "tx_kind_unknown": {
        "pt": "Ação: {kind}",
        "en": "Action: {kind}",
        "es": "Acción: {kind}",
    },

    # cogs/events.py — mass-info + inscrição em eventos
    "massinfo_title": {
        "pt": "⚔️ MASS INFO",
        "en": "⚔️ MASS INFO",
        "es": "⚔️ MASS INFO",
    },
    "massinfo_empty": {
        "pt": "...",
        "en": "...",
        "es": "...",
    },
    "massinfo_summary": {
        "pt": "{running} em andamento · {scheduled} agendado(s)",
        "en": "{running} running · {scheduled} scheduled",
        "es": "{running} en curso · {scheduled} programado(s)",
    },
    "massinfo_massing_now": {
        "pt": "MASSANDO AGORA",
        "en": "MASSING NOW",
        "es": "MASEANDO AHORA",
    },
    "massinfo_massing_soon": {
        "pt": "EM BREVE",
        "en": "STARTING SOON",
        "es": "PRONTO",
    },
    "signup_fetch_fail": {
        "pt": "⚠️ Não consegui consultar as funções disponíveis agora.",
        "en": "⚠️ Couldn't check available functions right now.",
        "es": "⚠️ No pude consultar las funciones disponibles ahora.",
    },
    "signup_already_registered": {
        "pt": "Você já está inscrito com: **{functions}**. O que deseja fazer?",
        "en": "You're already signed up with: **{functions}**. What do you want to do?",
        "es": "Ya estás inscrito con: **{functions}**. ¿Qué deseas hacer?",
    },
    "signup_already_registered_no_comp": {
        "pt": "Você já confirmou presença neste CTA. A administração ainda não definiu uma composição — quando liberarem as funções, você receberá uma DM pra escolher suas roles.",
        "en": "You already confirmed attendance for this CTA. Admins haven't set a composition yet — once they release functions, you'll get a DM to pick your roles.",
        "es": "Ya confirmaste presencia en este CTA. La administración aún no definió composición — cuando liberen las funciones, recibirás un DM para elegir tus roles.",
    },
    "signup_change_btn": {"pt": "🔄 Alterar funções", "en": "🔄 Change functions", "es": "🔄 Cambiar funciones"},
    "signup_remove_btn": {"pt": "🗑️ Retirar meu nome", "en": "🗑️ Remove my name", "es": "🗑️ Retirar mi nombre"},
    "signup_removed": {
        "pt": "✅ Sua inscrição foi removida.",
        "en": "✅ Your signup was removed.",
        "es": "✅ Tu inscripción fue eliminada.",
    },
    "signup_no_slots": {
        "pt": "⚠️ Não há vagas abertas pra você no momento — tente de novo mais perto do horário.",
        "en": "⚠️ There are no open slots for you right now — try again closer to the event's start.",
        "es": "⚠️ No hay vacantes abiertas para ti por el momento — intenta de nuevo más cerca del horario.",
    },
    "signup_no_role": {
        "pt": "⚠️ Você não tem o cargo necessário pra nenhuma das funções disponíveis.",
        "en": "⚠️ You don't have the required role for any of the available functions.",
        "es": "⚠️ No tienes el rol necesario para ninguna de las funciones disponibles.",
    },
    "signup_pick_category_prompt": {
        "pt": "Escolha uma categoria de função:",
        "en": "Pick a function category:",
        "es": "Elige una categoría de función:",
    },
    "signup_pick_category_ph": {"pt": "Categoria…", "en": "Category…", "es": "Categoría…"},
    "signup_pick_function_prompt": {
        "pt": "Escolha **uma** função:",
        "en": "Pick **one** function:",
        "es": "Elige **una** función:",
    },
    "signup_pick_function_ph": {
        "pt": "🕐 {cat} — função…",
        "en": "🕐 {cat} — function…",
        "es": "🕐 {cat} — función…",
    },
    "signup_fn_prev": {"pt": "◀️ Anteriores", "en": "◀️ Previous", "es": "◀️ Anteriores"},
    "signup_fn_next": {"pt": "▶️ Próximos", "en": "▶️ Next", "es": "▶️ Siguientes"},
    "signup_add_more": {"pt": "➕ Adicionar outra", "en": "➕ Add another", "es": "➕ Añadir otra"},
    "signup_remove_roles": {"pt": "➖ Retirar roles", "en": "➖ Remove roles", "es": "➖ Quitar roles"},
    "signup_choose_roles": {"pt": "Escolher roles", "en": "Choose roles", "es": "Elegir roles"},
    "signup_roles_dm_title": {
        "pt": "Atualize suas roles · Evento #{eid}",
        "en": "Update your roles · Event #{eid}",
        "es": "Actualiza tus roles · Evento #{eid}",
    },
    "signup_roles_dm_defined": {
        "pt": "Você confirmou presença quando este evento ainda não tinha composição. Agora a administração definiu a comp e precisa saber onde pode escalar você. Sua inscrição continua ativa: clique abaixo e marque **todas** as roles que você faz.",
        "en": "You confirmed attendance before this event had a composition. Admins have now set the comp and need to know where they can assign you. Your signup is still active: click below and select **every** role you can play.",
        "es": "Confirmaste tu presencia cuando este evento aún no tenía composición. La administración ya definió la comp y necesita saber dónde puede asignarte. Tu inscripción sigue activa: haz clic abajo y marca **todos** los roles que puedes jugar.",
    },
    "signup_roles_dm_changed": {
        "pt": "Você já estava inscrito, mas a administração trocou a composição. Sua presença continua confirmada; apenas as roles anteriores foram limpas porque pertenciam à comp antiga. Clique abaixo e marque **todas** as roles que você faz na nova comp.",
        "en": "You were already signed up, but admins changed the composition. Your attendance is still confirmed; only your previous roles were cleared because they belonged to the old comp. Click below and select **every** role you can play in the new comp.",
        "es": "Ya estabas inscrito, pero la administración cambió la composición. Tu presencia sigue confirmada; solo se limpiaron los roles anteriores porque pertenecían a la comp antigua. Haz clic abajo y marca **todos** los roles que puedes jugar en la nueva comp.",
    },
    "signup_roles_dm_released": {
        "pt": "Você já confirmou presença e a administração acaba de liberar a escolha de roles desta composição. Clique abaixo e marque **todas** as roles que você faz; isso ajudará a administração a montar a escalação.",
        "en": "You already confirmed attendance, and admins have now opened role selection for this composition. Click below and select **every** role you can play; this will help admins build the roster.",
        "es": "Ya confirmaste tu presencia y la administración acaba de habilitar la selección de roles para esta composición. Haz clic abajo y marca **todos** los roles que puedes jugar; esto ayudará a montar la formación.",
    },
    "signup_roles_dm_event": {"pt": "Evento", "en": "Event", "es": "Evento"},
    "signup_roles_dm_comp": {"pt": "Composição", "en": "Composition", "es": "Composición"},
    "signup_roles_dm_time": {"pt": "Horário", "en": "Time", "es": "Horario"},
    "signup_roles_dm_footer": {
        "pt": "Este PM será apagado automaticamente quando o evento entrar em revisão.",
        "en": "This DM will be deleted automatically when the event enters review.",
        "es": "Este MD se eliminará automáticamente cuando el evento entre en revisión.",
    },
    "signup_presence_success": {
        "pt": "✅ Presença confirmada. Você receberá outra mensagem quando for hora de escolher as roles.",
        "en": "✅ Presence confirmed. You will receive another message when it is time to choose roles.",
        "es": "✅ Presencia confirmada. Recibirás otro mensaje cuando sea hora de elegir roles.",
    },
    "signup_admin_assign_prompt": {
        "pt": "Confirme sua presença agora. Você poderá escolher as roles quando a composição estiver pronta.",
        "en": "Confirm your attendance now. You can choose roles when the composition is ready.",
        "es": "Confirma tu asistencia ahora. Podrás elegir roles cuando la composición esté lista.",
    },
    "signup_confirm_presence": {
        "pt": "✅ Confirmar presença",
        "en": "✅ Confirm presence",
        "es": "✅ Confirmar presencia",
    },
    "signup_remove_roles_prompt": {"pt": "Selecione as roles para retirar do seu perfil:", "en": "Select roles to remove from your profile:", "es": "Selecciona roles para quitar de tu perfil:"},
    "signup_remove_roles_ph": {"pt": "Roles a retirar…", "en": "Roles to remove…", "es": "Roles a quitar…"},
    "signup_back_to_review": {"pt": "↩️ Voltar", "en": "↩️ Back", "es": "↩️ Volver"},
    "signup_chosen_header": {
        "pt": "Suas roles · {n} selecionada(s)",
        "en": "Your roles · {n} selected",
        "es": "Tus roles · {n} seleccionada(s)",
    },
    "signup_roles_field": {"pt": "Roles", "en": "Roles", "es": "Roles"},
    "signup_minimum_footer": {
        "pt": "Mínimo exigido: {n}. Não há limite máximo.",
        "en": "Required minimum: {n}. There is no maximum.",
        "es": "Mínimo requerido: {n}. No hay límite máximo.",
    },
    "signup_review_prompt": {
        "pt": "Quer **confirmar** essas ou **adicionar mais**?",
        "en": "Want to **confirm** these or **add more**?",
        "es": "¿**Confirmar** estas o **añadir más**?",
    },
    "signup_none_yet": {"pt": "nenhuma ainda", "en": "none yet", "es": "ninguna aún"},
    "signup_done_btn": {"pt": "✅ Confirmar", "en": "✅ Confirm", "es": "✅ Confirmar"},
    "signup_back_to_categories": {"pt": "◀️ Voltar às categorias", "en": "◀️ Back to categories", "es": "◀️ Volver a categorías"},
    "signup_min_builds_needed": {
        "pt": "❌ Escolha ao menos {n} roles.",
        "en": "❌ Pick at least {n} roles.",
        "es": "❌ Elige al menos {n} roles.",
    },
    "signup_fail": {
        "pt": "⚠️ Não consegui salvar sua inscrição agora.",
        "en": "⚠️ Couldn't save your signup right now.",
        "es": "⚠️ No pude guardar tu inscripción ahora.",
    },
    "signup_success": {
        "pt": "✅ Inscrito com: **{functions}**",
        "en": "✅ Signed up with: **{functions}**",
        "es": "✅ Inscrito con: **{functions}**",
    },

    # cogs/regears.py — ingeste de screenshots de morte
    "regDeepLink": {
        "pt": "{status} Regear aberto no site: {url}",
        "en": "{status} Regear opened on the site: {url}",
        "es": "{status} Regear abierto en el sitio: {url}",
    },

    # nodes.py
    "nodes_title": {"pt": "🌿  𝐍𝐎𝐃𝐄𝐒", "en": "🌿  NODES", "es": "🌿  NODOS"},

    # energy_control.py
    "energy_control_title": {"pt": "⚡  Controle de Energia", "en": "⚡  Energy Control", "es": "⚡  Control de Energía"},
    "energy_control_empty": {
        "pt": "Nenhum jogador com energia abaixo do limite.",
        "en": "No players with energy below the threshold.",
        "es": "Ningún jugador con energía por debajo del límite.",
    },
    "nodes_empty": {
        "pt": "Nenhum node programado. Use o botão **Adicionar Node**.",
        "en": "No nodes scheduled. Use the **Add Node** button.",
        "es": "Ningún nodo programado. Usa el botón **Añadir Nodo**.",
    },
    "nodes_no_calendar": {
        "pt": "Calendário de nodes não configurado neste servidor (site → Config → Canal de Nodes).",
        "en": "Nodes calendar not configured on this server (site → Config → Nodes Channel).",
        "es": "Calendario de nodos no configurado en este servidor (sitio → Config → Canal de Nodos).",
    },
    "nodes_no_defs": {
        "pt": "Nenhum tipo de node definido. A staff define no site (Config → Nodes).",
        "en": "No node types defined. Staff sets them on the site (Config → Nodes).",
        "es": "Ningún tipo de nodo definido. El staff los define en el sitio (Config → Nodos).",
    },
    "nodes_add_btn": {"pt": "Adicionar Node", "en": "Add Node", "es": "Añadir Nodo"},
    "nodes_remove_btn": {"pt": "Remover", "en": "Remove", "es": "Quitar"},
    "nodes_pick_type": {"pt": "Escolha o tipo de node", "en": "Pick the node type", "es": "Elige el tipo de nodo"},
    "nodes_pick_map": {"pt": "Escolha o mapa", "en": "Pick the map", "es": "Elige el mapa"},
    "nodes_pick_time": {"pt": "Escolha o horário (UTC)", "en": "Pick the time (UTC)", "es": "Elige la hora (UTC)"},
    "nodes_pick_time_range": {
        "pt": "🕐 Faixa de 25min (pág {page}/{pages})…",
        "en": "🕐 25-min slot (page {page}/{pages})…",
        "es": "🕐 Franja de 25min (pág {page}/{pages})…",
    },
    "nodes_pick_time_minute": {"pt": "🕐 Minuto exato (UTC)…", "en": "🕐 Exact minute (UTC)…", "es": "🕐 Minuto exacto (UTC)…"},
    "nodes_time_prev": {"pt": "◀️ Horários anteriores", "en": "◀️ Earlier", "es": "◀️ Horarios anteriores"},
    "nodes_time_next": {"pt": "▶️ Próximos horários", "en": "▶️ Later", "es": "▶️ Horarios siguientes"},
    "nodes_back": {"pt": "← Voltar", "en": "← Back", "es": "← Volver"},
    "nodes_cancel": {"pt": "Cancelar", "en": "Cancel", "es": "Cancelar"},
    "nodes_confirm": {"pt": "Confirmar", "en": "Confirm", "es": "Confirmar"},
    "nodes_added": {
        "pt": "✅ Node **{node}** em **{mapa}** às **{hora}** UTC adicionado.",
        "en": "✅ Node **{node}** at **{mapa}** at **{hora}** UTC added.",
        "es": "✅ Nodo **{node}** en **{mapa}** a las **{hora}** UTC añadido.",
    },
    "nodes_add_fail": {
        "pt": "❌ Não deu pra adicionar: {reason}",
        "en": "❌ Couldn't add: {reason}",
        "es": "❌ No se pudo añadir: {reason}",
    },
    "nodes_pick_remove": {"pt": "Selecione o node para remover", "en": "Select the node to remove", "es": "Selecciona el nodo a quitar"},
    "nodes_remove_empty": {"pt": "Nada pra remover.", "en": "Nothing to remove.", "es": "Nada que quitar."},
    "nodes_removed": {"pt": "✅ Node removido.", "en": "✅ Node removed.", "es": "✅ Nodo quitado."},
    "nodes_remove_fail": {"pt": "❌ Não consegui remover.", "en": "❌ Couldn't remove.", "es": "❌ No pude quitar."},

    # ── Embed de evento (8 estados) ───────────────────────────────────────────
    # Vira um field em negrito (não embed.title): "**EVENTO | data UTC | #n**".
    "ev_title": {"pt": "EVENTO", "en": "EVENT", "es": "EVENTO"},
    # Nome da thread criada na sala de revisão ao entrar em review.
    "ev_thread_title": {"pt": "📑 Evento #{n} — Revisão", "en": "📑 Event #{n} — Review", "es": "📑 Evento #{n} — Revisión"},
    "ev_regear_thread_title": {"pt": "🛠️ Regear — Evento #{n} {title}", "en": "🛠️ Regear — Event #{n} {title}", "es": "🛠️ Regear — Evento #{n} {title}"},
    "ev_regear_thread_header": {"pt": "Poste aqui as **prints de morte** do evento #{n}. A staff avalia o regear no site.", "en": "Post your **death screenshots** for event #{n} here. Staff reviews regears on the site.", "es": "Publica aquí las **capturas de muerte** del evento #{n}. El staff revisa el regear en el sitio."},
    "ev_lootlog_thread_title": {"pt": "🪵 Log — Evento #{n} {title}", "en": "🪵 Log — Event #{n} {title}", "es": "🪵 Log — Evento #{n} {title}"},
    "ev_lootlog_thread_header": {"pt": "Clique em **📤 Enviar log** abaixo e anexe o **.csv do lootlogger** do evento #{n} no formulário que abrir. O arquivo é privado (só o bot lê) — ele envia pro site e a **% de cada logger** aparece aqui no embed.", "en": "Click **📤 Send log** below and attach your **lootlogger .csv** for event #{n} in the form that opens. The file is private (only the bot reads it) — it goes to the site and each logger's **%** shows up here in the embed.", "es": "Haz clic en **📤 Enviar log** abajo y adjunta el **.csv del lootlogger** del evento #{n} en el formulario que se abra. El archivo es privado (solo el bot lo lee) — lo envía al sitio y el **% de cada logger** aparece aquí en el embed."},
    "ev_lootlog_standings_title": {"pt": "📊 Loggers ({n})", "en": "📊 Loggers ({n})", "es": "📊 Loggers ({n})"},
    "ev_lootlog_standings_empty": {"pt": "*Aguardando 2+ loggers p/ corroboração.*", "en": "*Waiting for 2+ loggers to corroborate.*", "es": "*Esperando 2+ loggers para corroborar.*"},
    "ev_lootlog_audit_new": {"pt": "nova log", "en": "new log", "es": "log nueva"},
    "ev_lootlog_audit_update": {"pt": "↻ atualizou log", "en": "↻ updated log", "es": "↻ actualizó log"},
    "ev_lootlog_submit_btn": {"pt": "📤 Enviar log", "en": "📤 Send log", "es": "📤 Enviar log"},
    "ev_lootlog_modal_title": {"pt": "Enviar log do evento", "en": "Send event log", "es": "Enviar log del evento"},
    "ev_lootlog_modal_label": {"pt": "Arquivo do lootlogger (.txt ou .csv)", "en": "Lootlogger file (.txt or .csv)", "es": "Archivo del lootlogger (.txt o .csv)"},
    "ev_lootlog_modal_desc": {"pt": "Anexe o arquivo exportado pelo lootlogger deste evento (sem editar).", "en": "Attach the file exported by the lootlogger for this event (unedited).", "es": "Adjunta el archivo exportado por el lootlogger de este evento (sin editar)."},
    "ev_lootlog_no_file": {"pt": "❌ Nenhum arquivo enviado.", "en": "❌ No file sent.", "es": "❌ No se envió archivo."},
    "ev_lootlog_ingest_err": {"pt": "❌ Erro ao processar seu log. Tente reenviar; se persistir, avise a staff.", "en": "❌ Error processing your log. Try resending; if it persists, alert staff.", "es": "❌ Error al procesar tu log. Reenvía; si persiste, avisa al staff."},
    "ev_lootlog_modal_err": {"pt": "❌ Não consegui abrir o formulário de envio. Avise a staff.", "en": "❌ Couldn't open the upload form. Alert staff.", "es": "❌ No se pudo abrir el formulario. Avisa al staff."},
    "ev_lootlog_read_err": {"pt": "❌ Não consegui ler o arquivo anexado. Tente novamente.", "en": "❌ Couldn't read the attached file. Try again.", "es": "❌ No se pudo leer el archivo adjunto. Intenta de nuevo."},
    "ev_lootlog_ingest_fail": {"pt": "❌ Falha ao enviar o log ao site. Avise a staff (erro registrado).", "en": "❌ Failed to send the log to the site. Alert staff (error logged).", "es": "❌ Falló el envío al sitio. Avisa al staff (error registrado)."},
    "ev_lootlog_thanks": {"pt": "✅ Log recebido — {n} linhas. Obrigado!", "en": "✅ Log received — {n} rows. Thanks!", "es": "✅ Log recibido — {n} filas. ¡Gracias!"},
    "ev_pipeline": {"pt": "Pipeline", "en": "Pipeline", "es": "Pipeline"},
    "ev_state_scheduled": {"pt": "Agendado", "en": "Scheduled", "es": "Agendado"},
    "ev_state_in_progress": {"pt": "Andamento", "en": "In progress", "es": "En curso"},
    "ev_state_review": {"pt": "Revisão", "en": "Review", "es": "Revisión"},
    "ev_state_finalized": {"pt": "Finalizado", "en": "Finalized", "es": "Finalizado"},
    "ev_state_cancelled": {"pt": "Cancelado", "en": "Cancelled", "es": "Cancelado"},
    "ev_state_deleted": {"pt": "Excluído", "en": "Deleted", "es": "Excluido"},
    "ev_no_participants": {"pt": "*Ninguém foi detectado na call.*", "en": "*Nobody detected in the call.*", "es": "*Nadie fue detectado en la call.*"},
    "ev_callout": {"pt": "📢 Dar callout", "en": "📢 Callout", "es": "📢 Hacer callout"},
    "ev_finalize": {"pt": "✅ Finalizar", "en": "✅ Finalize", "es": "✅ Finalizar"},
    "ev_edit_pct": {"pt": "✏️ Alterar %", "en": "✏️ Change %", "es": "✏️ Cambiar %"},
    "ev_remove_participant": {"pt": "🫷 Remover", "en": "🫷 Remove", "es": "🫷 Quitar"},
    "ev_set_split": {"pt": "💰 Definir split", "en": "💰 Set split", "es": "💰 Definir split"},
    "ev_add_participant": {"pt": "➕ Adicionar", "en": "➕ Add", "es": "➕ Añadir"},
    "ev_pick_member": {"pt": "Selecione o membro para adicionar", "en": "Select the member to add", "es": "Selecciona el miembro a añadir"},
    "ev_no_candidates": {"pt": "Todos os membros já são participantes.", "en": "Every member is already a participant.", "es": "Todos los miembros ya son participantes."},
    "ev_already_participant": {"pt": "Esse membro já é participante do evento.", "en": "That member is already a participant.", "es": "Ese miembro ya es participante del evento."},
    "ev_loggers": {"pt": "🪵 Loggers ({n})", "en": "🪵 Loggers ({n})", "es": "🪵 Loggers ({n})"},
    "ev_split_nodes_intro": {"pt": "Selecione os nodes que foram capturados; na próxima tela informe o valor vendido de cada um (até 5).", "en": "Select the captured nodes; next screen asks the sold value of each (up to 5).", "es": "Selecciona los nodos capturados; la próxima pantalla pide el valor vendido de cada uno (hasta 5)."},
    "ev_split_nodes_pick": {"pt": "Marque os nodes capturados", "en": "Mark captured nodes", "es": "Marca los nodos capturados"},
    "ev_split_nodes_confirm": {"pt": "✅ Informar valores", "en": "✅ Enter values", "es": "✅ Introducir valores"},
    "ev_split_nodes_skip": {"pt": "⏭️ Nenhum capturado", "en": "⏭️ None captured", "es": "⏭️ Ninguno capturado"},
    "ev_split_nodes_values": {"pt": "Valor vendido dos nodes", "en": "Sold value of nodes", "es": "Valor vendido de los nodos"},
    "ev_pick_participant": {"pt": "Selecione o participante", "en": "Select the participant", "es": "Selecciona el participante"},
    "ev_pick_participant_remove": {"pt": "Selecione quem remover", "en": "Select who to remove", "es": "Selecciona a quién quitar"},
    "ev_enter_percent": {"pt": "Percentual (0-100)", "en": "Percent (0-100)", "es": "Porcentaje (0-100)"},
    "ev_enter_tab_value": {"pt": "Valor da tab (prata)", "en": "Tab value (silver)", "es": "Valor de la tab (plata)"},
    "ev_nodes_title": {"pt": "🌿 Nodes próximos (±30min) · {n} total", "en": "🌿 Nearby nodes (±30min) · {n} total", "es": "🌿 Nodos cercanos (±30min) · {n} total"},
    "ev_fetch_fail": {"pt": "❌ Falha ao buscar o evento.", "en": "❌ Failed to fetch the event.", "es": "❌ No pude obtener el evento."},
    "ev_update_fail": {"pt": "❌ Não deu pra atualizar.", "en": "❌ Couldn't update.", "es": "❌ No pude actualizar."},
    "ev_done": {"pt": "✅ Evento finalizado.", "en": "✅ Event finalized.", "es": "✅ Evento finalizado."},
    "ev_only_manage": {"pt": "Só quem gerencia eventos pode fazer isso.", "en": "Only event managers can do this.", "es": "Solo los gestores de eventos pueden hacer esto."},

    # cogs/event_cmd.py — /event criar/deletar/editar/adiar
    "ev_pick_comp": {"pt": "Escolha a comp do evento:", "en": "Pick the event's comp:", "es": "Elige la comp del evento:"},
    "ev_pick_event": {"pt": "Selecione o evento:", "en": "Select the event:", "es": "Selecciona el evento:"},
    "ev_pick_field": {"pt": "O que deseja editar?", "en": "What do you want to edit?", "es": "¿Qué deseas editar?"},
    "ev_cancel": {"pt": "Cancelar", "en": "Cancel", "es": "Cancelar"},
    "ev_cancelled": {"pt": "❌ Cancelado.", "en": "❌ Cancelled.", "es": "❌ Cancelado."},
    "ev_no_comp": {"pt": "— Sem comp —", "en": "— No comp —", "es": "— Sin comp —"},
    "ev_no_events": {"pt": "Nenhum evento disponível (apenas eventos não finalizados aparecem aqui).", "en": "No events available (only non-finalized events show up here).", "es": "Ningún evento disponible (solo eventos no finalizados aparecen aquí)."},
    "ev_create_done": {"pt": "✅ Evento #{eid} criado às **{hora}** UTC.", "en": "✅ Event #{eid} created at **{hora}** UTC.", "es": "✅ Evento #{eid} creado a las **{hora}** UTC."},
    "ev_create_fail": {"pt": "❌ Não deu pra criar o evento.", "en": "❌ Couldn't create the event.", "es": "❌ No se pudo crear el evento."},
    "massinfo_more_events": {"pt": "Ver outro evento…", "en": "View another event…", "es": "Ver otro evento…"},
    "ev_delete_confirm": {"pt": "Deletar este evento?\n**{ev}**", "en": "Delete this event?\n**{ev}**", "es": "¿Eliminar este evento?\n**{ev}**"},
    "ev_delete_btn": {"pt": "🗑️ Deletar", "en": "🗑️ Delete", "es": "🗑️ Eliminar"},
    "ev_delete_done": {"pt": "✅ Evento #{eid} deletado.", "en": "✅ Event #{eid} deleted.", "es": "✅ Evento #{eid} eliminado."},
    "ev_field_objetivo": {"pt": "Objetivo", "en": "Objective", "es": "Objetivo"},
    "ev_field_horario": {"pt": "Horário", "en": "Time", "es": "Horario"},
    "ev_field_comp": {"pt": "Comp", "en": "Comp", "es": "Comp"},
    "ev_field_attendance": {"pt": "Pontos de attendance", "en": "Attendance points", "es": "Puntos de asistencia"},
    "ev_time_input_label": {"pt": "Novo horário", "en": "New time", "es": "Nuevo horario"},
    "ev_time_input_placeholder": {
        "pt": "21h, 21:30 BRT ou 24/07/2026 21h",
        "en": "21h, 21:30 BRT, or 2026-07-24 21:00",
        "es": "21h, 21:30 BRT o 24/07/2026 21h",
    },
    "ev_edit_done_field": {"pt": "✅ {field} atualizado(s).", "en": "✅ {field} updated.", "es": "✅ {field} actualizado."},
    "ev_edit_changed": {
        "pt": "{field} alterado para **{value}**.",
        "en": "{field} changed to **{value}**.",
        "es": "{field} cambiado a **{value}**.",
    },
    "ev_edit_history": {
        "pt": "**Alterações desta edição:**",
        "en": "**Changes in this edit session:**",
        "es": "**Cambios de esta edición:**",
    },
    "ev_edit_history_line": {
        "pt": "**{field}:** {before} → {after}",
        "en": "**{field}:** {before} → {after}",
        "es": "**{field}:** {before} → {after}",
    },
    "ev_reschedule_done": {"pt": "✅ Evento #{eid} adiado pra **{hora}** UTC.", "en": "✅ Event #{eid} rescheduled to **{hora}** UTC.", "es": "✅ Evento #{eid} aplazado a las **{hora}** UTC."},
    "ev_comp_changed_summary": {
        "pt": "✅ Comp alterada pra **{comp}**. {n} inscrição(ões) preservada(s) — os jogadores receberão um PM para escolher as roles.",
        "en": "✅ Comp changed to **{comp}**. {n} signup(s) preserved — players will receive a DM to choose their roles.",
        "es": "✅ Comp cambiada a **{comp}**. {n} inscripción(es) preservada(s) — los jugadores recibirán un MD para elegir sus roles.",
    },
    "ev_comp_changed_dm": {
        "pt": "⚠️ A comp do evento #{eid} ({title}) foi alterada pra **{comp}**. Sua inscrição continua ativa; escolha agora as roles que você faz.",
        "en": "⚠️ The comp for event #{eid} ({title}) changed to **{comp}**. Your signup is still active; choose the roles you can play.",
        "es": "⚠️ La comp del evento #{eid} ({title}) cambió a **{comp}**. Tu inscripción sigue activa; elige los roles que puedes jugar.",
    },

    # audit_log.py
    "logs_entity": {"pt": "Entidade", "en": "Entity", "es": "Entidad"},
    "logs_actor": {"pt": "Autor", "en": "Actor", "es": "Autor"},
    "logs_system": {"pt": "Sistema", "en": "System", "es": "Sistema"},
    "logs_changes": {"pt": "Alterações", "en": "Changes", "es": "Cambios"},
    "logs_note": {"pt": "Nota", "en": "Note", "es": "Nota"},

    # event_cmd.py — estava por engano no CMD_I18N (dict do Translator), onde
    # t() nunca acha: o usuário via a chave crua "ev_bad_time" no chat.
    "ev_bad_time": {
        "pt": "Horário inválido — use 21h, 21:30 BRT, 11:30 CEST ou uma data completa.",
        "en": "Invalid time — use 21h, 21:30 BRT, 11:30 CEST, or a full date.",
        "es": "Hora inválida — usa 21h, 21:30 BRT, 11:30 CEST o una fecha completa.",
    },
    "signup_success_self": {
        "pt": "✅ Preferência registrada: **{functions}**. A vaga será confirmada na escalação.",
        "en": "✅ Preference recorded: **{functions}**. The slot will be confirmed in the roster.",
        "es": "✅ Preferencia registrada: **{functions}**. La plaza se confirmará en la escalación.",
    },
    "signup_success_hybrid": {
        "pt": "✅ Preferência registrada: **{functions}**. A administração confirma a build.",
        "en": "✅ Preference recorded: **{functions}**. Admins will confirm the build.",
        "es": "✅ Preferencia registrada: **{functions}**. La administración confirmará la build.",
    },
    "signup_admin_assign_success": {
        "pt": "✅ Presença registrada. A administração confirmará sua build.",
        "en": "✅ Presence registered. Admins will confirm your build.",
        "es": "✅ Presencia registrada. La administración confirmará tu build.",
    },

    # members.py — /profile /attendance /lowattendance
    "profile_usage": {
        "pt": "Informe o nick de um jogador do Albion, ex: `/profile jogador:SeuNick`.",
        "en": "Provide an Albion player's nickname, e.g.: `/profile jogador:YourNick`.",
        "es": "Indica el nick de un jugador de Albion, ej: `/profile jogador:TuNick`.",
    },
    "profile_api_error": {
        "pt": "⚠️ Não foi possível buscar dados na API do Albion após várias tentativas. Tente novamente mais tarde.",
        "en": "⚠️ Could not fetch data from the Albion API after several attempts. Try again later.",
        "es": "⚠️ No se pudieron obtener datos de la API de Albion tras varios intentos. Inténtalo de nuevo más tarde.",
    },
    "profile_retrying": {
        "pt": "🔄 A API do Albion está instável — tentando novamente (tentativa {attempt})…",
        "en": "🔄 Albion's API is unstable — retrying (attempt {attempt})…",
        "es": "🔄 La API de Albion está inestable — reintentando (intento {attempt})…",
    },
    "profile_region_prompt": {
        "pt": "🌍 O nick **{name}** existe em mais de uma região. Escolha um servidor (ou aguarde 10s para selecionar automaticamente o mais ativo):",
        "en": "🌍 The nickname **{name}** exists in more than one region. Choose a server (or wait 10s to auto-select the most active one):",
        "es": "🌍 El nick **{name}** existe en más de una región. Elige un servidor (o espera 10s para seleccionar automáticamente el más activo):",
    },
    "profile_region_auto": {
        "pt": "⏱️ Tempo esgotado — selecionando automaticamente: **{region}**",
        "en": "⏱️ Time's up — auto-selecting: **{region}**",
        "es": "⏱️ Tiempo agotado — seleccionando automáticamente: **{region}**",
    },
    "profile_not_found": {
        "pt": "❔ Jogador **{name}** não encontrado.",
        "en": "❔ Player **{name}** not found.",
        "es": "❔ Jugador **{name}** no encontrado.",
    },
    "profile_not_registered": {
        "pt": "Esse usuário não está registrado no bot. Peça pra ele usar `/register`.",
        "en": "That user isn't registered with the bot. Ask them to use `/register`.",
        "es": "Ese usuario no está registrado en el bot. Pídele que use `/register`.",
    },
    "att_lifetime": {"pt": "📅 Histórico Total", "en": "📅 Lifetime", "es": "📅 Historial Total"},
    "att_lifetime_val": {
        "pt": "Eventos da guild: **{total}**\nVocê participou: **{user}**  ({pct:.1f}%)",
        "en": "Guild events: **{total}**\nYou attended: **{user}**  ({pct:.1f}%)",
        "es": "Eventos del gremio: **{total}**\nAsististe: **{user}**  ({pct:.1f}%)",
    },
    "att_no_events": {"pt": "*Nenhum evento registrado ainda.*", "en": "*No events recorded yet.*", "es": "*Aún no hay eventos registrados.*"},
    "att_7d": {"pt": "🗓️ Últimos 7 dias", "en": "🗓️ Last 7 days", "es": "🗓️ Últimos 7 días"},
    "att_7d_val": {
        "pt": "Total: **{total}**\nVocê participou: **{user}**  ({pct:.1f}%)",
        "en": "Total: **{total}**\nYou attended: **{user}**  ({pct:.1f}%)",
        "es": "Total: **{total}**\nAsististe: **{user}**  ({pct:.1f}%)",
    },
    "att_no_events_7d": {"pt": "*Nenhum evento neste período.*", "en": "*No events in this period.*", "es": "*Sin eventos en este período.*"},
    "att_rank": {"pt": "🏆 Ranking", "en": "🏆 Ranking", "es": "🏆 Ranking"},
    "att_last": {"pt": "🕐 Último evento", "en": "🕐 Last event", "es": "🕐 Último evento"},
    "att_no_data": {"pt": "*Sem dados*", "en": "*No data*", "es": "*Sin datos*"},
    "att_never": {"pt": "*nunca*", "en": "*never*", "es": "*nunca*"},
    "att_footer": {
        "pt": "Considera participante válido (não irregular) como evento atendido",
        "en": "Counts valid participants (non-irregular) as attended",
        "es": "Cuenta participantes válidos (no irregulares) como asistidos",
    },
    "event_word": {"pt": "evento", "en": "event", "es": "evento"},
    "events_word": {"pt": "eventos", "en": "events", "es": "eventos"},
    "lowatt_title": {
        "pt": "Low Attendance — últimos 7 dias",
        "en": "Low Attendance — last 7 days",
        "es": "Low Attendance — últimos 7 días",
    },
    "lowatt_desc": {
        "pt": "Total de eventos no período: **{total}**\nMembros analisados: **{analyzed}**  ·  Filtrados (cargo < 7 dias): **{filtered}**",
        "en": "Events in period: **{total}**\nMembers analyzed: **{analyzed}**  ·  Filtered (role < 7 days): **{filtered}**",
        "es": "Eventos en el período: **{total}**\nMiembros analizados: **{analyzed}**  ·  Filtrados (rol < 7 días): **{filtered}**",
    },
    "lowatt_empty": {"pt": "*Nenhum membro elegível para análise.*", "en": "*No eligible members to analyze.*", "es": "*Ningún miembro elegible para analizar.*"},
    "lowatt_ranking": {"pt": "Ranking", "en": "Ranking", "es": "Ranking"},

    # cogs/massinfo_access.py — verificação recorrente de acesso ao mass-info
    "massinfo_access_title": {
        "pt": "Acesso ao mass-info sem registro",
        "en": "Mass-info access without registration",
        "es": "Acceso al mass-info sin registro",
    },
    "massinfo_access_empty": {
        "pt": "Todos os usuários com acesso ao canal mass-info estão registrados.",
        "en": "Everyone with access to the mass-info channel is registered.",
        "es": "Todos los usuarios con acceso al canal mass-info están registrados.",
    },
    "massinfo_access_desc": {
        "pt": "{count} usuário(s) com acesso ao canal mass-info **não** estão registrados:",
        "en": "{count} user(s) with access to the mass-info channel are **not** registered:",
        "es": "{count} usuario(s) con acceso al canal mass-info **no** están registrados:",
    },
    "massinfo_access_field": {
        "pt": "Não registrados",
        "en": "Unregistered",
        "es": "No registrados",
    },
    "massinfo_access_actions_title": {
        "pt": "O que fazer",
        "en": "What to do",
        "es": "Qué hacer",
    },
    "massinfo_access_actions_body": {
        "pt": "Use `/register usuario:<pessoa>` para registrar cada um, ou `/bypass usuario:<pessoa>` para remover alguém deste anúncio sem registrar.",
        "en": "Use `/register user:<person>` to register each one, or `/bypass user:<person>` to remove someone from this announcement without registering.",
        "es": "Usa `/register usuario:<persona>` para registrar a cada uno, o `/bypass usuario:<persona>` para quitar a alguien de este anuncio sin registrarlo.",
    },
    "bypass_added": {
        "pt": "✅ {mention} foi removido do anúncio recorrente de não-registrados.",
        "en": "✅ {mention} was removed from the unregistered access announcement.",
        "es": "✅ {mention} fue quitado del anuncio recurrente de no registrados.",
    },
    "ev_no_in_progress": {
        "pt": "Nenhum evento em andamento.",
        "en": "No in-progress events.",
        "es": "Ningún evento en curso.",
    },
    "ev_finalize_done": {
        "pt": "✅ Evento **{ev}** finalizado — movido para revisão.",
        "en": "✅ Event **{ev}** finalized — moved to review.",
        "es": "✅ Evento **{ev}** finalizado — movido a revisión.",
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
# português embutido no @app_commands.command/describe é o default de pt-BR;
# locales sem mapeamento dedicado (fr/de/…) caem no "en" (fallback inglês).
# Ver localization.py.
CMD_I18N: dict[str, dict[str, str]] = {
    "opt_name_alvo": {"pt": "alvo", "en": "user", "es": "usuario"},
    "opt_name_quantia": {"pt": "quantia", "en": "amount", "es": "cantidad"},

    "cmd_desc_avatar": {"pt": "Mostra o avatar de um usuário ou servidor", "en": "Shows a user's or server's avatar", "es": "Muestra el avatar de un usuario o servidor"},
    "cmd_desc_banner": {"pt": "Mostra o banner de um usuário ou servidor", "en": "Shows a user's or server's banner", "es": "Muestra el banner de un usuario o servidor"},
    "opt_desc_avatar_banner_alvo": {
        "pt": "ID de servidor, @menção, ID/nome de usuário ou apelido (padrão: você mesmo)",
        "en": "Server ID, @mention, user ID/name, or nickname (default: yourself)",
        "es": "ID de servidor, @mención, ID/nombre de usuario o apodo (por defecto: tú mismo)",
    },

    "cmd_desc_register": {
        "pt": "Vincula um nick do Albion a uma conta Discord e libera o cargo",
        "en": "Links an Albion nickname to a Discord account and unlocks the role",
        "es": "Vincula un nick de Albion a una cuenta de Discord y libera el rol",
    },
    "opt_desc_register": {
        "pt": "Seu nick do Albion — ou, pra registrar outra pessoa, nick + usuário do Discord (qualquer ordem)",
        "en": "Your Albion nickname — or, to register someone else, nickname + Discord user (any order)",
        "es": "Tu nick de Albion — o, para registrar a otra persona, nick + usuario de Discord (cualquier orden)",
    },
    "opt_desc_register_nick": {
        "pt": "Nick do Albion para registrar",
        "en": "Albion nickname to register",
        "es": "Nick de Albion para registrar",
    },
    "opt_desc_register_usuario": {
        "pt": "Usuário do Discord para registrar (em branco = você mesmo)",
        "en": "Discord user to register (blank = yourself)",
        "es": "Usuario de Discord para registrar (vacío = tú mismo)",
    },
    "cmd_desc_unregister": {"pt": "Remove o registro e o cargo de um membro", "en": "Removes a member's registration and role", "es": "Elimina el registro y el rol de un miembro"},
    "opt_desc_unregister_alvo": {
        "pt": "Menção, ID, nome de usuário no Discord, ou nick no Albion do membro",
        "en": "Mention, ID, Discord username, or the member's Albion nickname",
        "es": "Mención, ID, nombre de usuario de Discord, o nick de Albion del miembro",
    },

    "cmd_desc_balance": {
        "pt": "Mostra o saldo de um usuário (o seu, se ninguém for informado)",
        "en": "Shows a user's balance (yours, if none given)",
        "es": "Muestra el saldo de un usuario (el tuyo, si no se indica ninguno)",
    },
    "opt_desc_balance_alvo": {"pt": "Membro do Discord (padrão: você mesmo)", "en": "Discord member (default: yourself)", "es": "Miembro de Discord (por defecto: tú mismo)"},

    "cmd_desc_pay": {"pt": "Transfere prata do seu saldo para outro usuário", "en": "Transfers silver from your balance to another user", "es": "Transfiere plata de tu saldo a otro usuario"},
    "opt_desc_pay_alvo": {"pt": "Membro do Discord que vai receber", "en": "Discord member who will receive it", "es": "Miembro de Discord que recibirá"},
    "opt_desc_pay_quantia": {
        "pt": "Quanto enviar (ex: 100k, 1.5m, 2,500,000) ou `all`/`tudo`",
        "en": "How much to send (e.g.: 100k, 1.5m, 2,500,000) or `all`/`tudo`",
        "es": "Cuánto enviar (ej: 100k, 1.5m, 2,500,000) o `all`/`tudo`",
    },

    "cmd_desc_addmoney": {"pt": "Adiciona prata ao saldo de um usuário", "en": "Adds silver to a user's balance", "es": "Agrega plata al saldo de un usuario"},
    "opt_desc_addmoney_alvo": {"pt": "Menções de usuários/cargos ou nick", "en": "User/role mentions or nickname", "es": "Menciones de usuarios/roles o apodo"},
    "opt_desc_addmoney_quantia": {"pt": "Quanto adicionar (ex: 100k, 1.5m)", "en": "How much to add (e.g.: 100k, 1.5m)", "es": "Cuánto agregar (ej: 100k, 1.5m)"},

    "cmd_desc_removemoney": {
        "pt": "Remove prata do saldo de um usuário (sem valor = remove tudo)",
        "en": "Removes silver from a user's balance (no value = removes everything)",
        "es": "Remueve plata del saldo de un usuario (sin valor = remueve todo)",
    },
    "opt_desc_removemoney_alvo": {"pt": "Membro do Discord", "en": "Discord member", "es": "Miembro de Discord"},
    "opt_desc_removemoney_quantia": {
        "pt": "Quanto remover (em branco ou `all`/`tudo` = remove tudo)",
        "en": "How much to remove (blank or `all`/`tudo` = removes everything)",
        "es": "Cuánto remover (vacío o `all`/`tudo` = remueve todo)",
    },

    "cmd_desc_leaderboard": {"pt": "Ranking dos usuários pelo saldo atual de prata", "en": "Ranking of users by current silver balance", "es": "Ranking de usuarios por saldo actual de plata"},
    "cmd_desc_economystats": {"pt": "Mostra um snapshot da economia do servidor", "en": "Shows a snapshot of the server's economy", "es": "Muestra un resumen de la economía del servidor"},
    "cmd_desc_guildbank": {"pt": "Mostra o saldo do banco da guilda", "en": "Shows the guild bank balance", "es": "Muestra el saldo del banco del gremio"},
    "cmd_desc_addguildmoney": {"pt": "Adiciona prata ao banco da guilda", "en": "Adds silver to the guild bank", "es": "Añade plata al banco del gremio"},
    "cmd_desc_removeguildmoney": {"pt": "Remove prata do banco da guilda", "en": "Removes silver from the guild bank", "es": "Quita plata del banco del gremio"},
    "opt_name_acao": {"pt": "acao", "en": "action", "es": "accion"},
    "opt_name_motivo": {"pt": "motivo", "en": "reason", "es": "motivo"},
    "opt_desc_addguildmoney_quantia": {
        "pt": "Quanto adicionar (ex: 100k, 1.5m)",
        "en": "How much to add (e.g.: 100k, 1.5m)",
        "es": "Cuánto añadir (ej: 100k, 1.5m)",
    },
    "opt_desc_removeguildmoney_quantia": {
        "pt": "Quanto remover (ex: 100k, 1.5m)",
        "en": "How much to remove (e.g.: 100k, 1.5m)",
        "es": "Cuánto quitar (ej: 100k, 1.5m)",
    },
    "opt_desc_bank_motivo": {
        "pt": "Motivo curto do ajuste (opcional)",
        "en": "Short reason for the adjustment (optional)",
        "es": "Motivo breve del ajuste (opcional)",
    },

    "cmd_desc_undo": {"pt": "Reverte uma transação de economia pelo ID", "en": "Reverts an economy transaction by its ID", "es": "Revierte una transacción de economía por su ID"},
    "opt_desc_undo_id": {
        "pt": "ID da transação a reverter (veja o rodapé do embed original)",
        "en": "Transaction ID to revert (see the original embed's footer)",
        "es": "ID de la transacción a revertir (ver el pie del embed original)",
    },

    "cmd_desc_transactions": {
        "pt": "Mostra o histórico de transações com paginação",
        "en": "Shows your transaction history with pagination",
        "es": "Muestra el historial de transacciones con paginación",
    },
    "opt_desc_transactions_alvo": {
        "pt": "Usuário para verificar (padrão: você mesmo)",
        "en": "User to check (default: yourself)",
        "es": "Usuario a verificar (por defecto: tú mismo)",
    },

    "cmd_group_event": {
        "pt": "Gerencia eventos (CTAs): criar, deletar, editar e adiar",
        "en": "Manage events (CTAs): create, delete, edit and reschedule",
        "es": "Gestiona eventos (CTAs): crear, eliminar, editar y aplazar",
    },
    "cmd_desc_event_criar": {
        "pt": "Cria um evento com inscrições; a comp pode ser definida depois",
        "en": "Create an event with signups; the comp can be set later",
        "es": "Crea un evento con inscripciones; la comp puede definirse después",
    },
    "cmd_desc_event_deletar": {
        "pt": "Deleta um evento ainda não finalizado",
        "en": "Delete a not-yet-finalized event",
        "es": "Elimina un evento aún no finalizado",
    },
    "cmd_desc_event_editar": {
        "pt": "Edita objetivo, horário, comp ou pontos de attendance de um evento",
        "en": "Edit objective, time, comp or attendance points of an event",
        "es": "Edita objetivo, hora, comp o puntos de asistencia de un evento",
    },
    "cmd_desc_event_adiar": {
        "pt": "Adia um evento (novo horário UTC)",
        "en": "Reschedule an event (new UTC time)",
        "es": "Aplaza un evento (nueva hora UTC)",
    },
    "cmd_desc_event_finalizar": {
        "pt": "Finaliza um evento em andamento (passa para revisão)",
        "en": "Finalize an in-progress event (moves to review)",
        "es": "Finaliza un evento en curso (pasa a revisión)",
    },
    "opt_name_event_objetivo": {"pt": "objetivo", "en": "objective", "es": "objetivo"},
    "opt_desc_event_objetivo": {
        "pt": "Objetivo do evento (o nome/título dele)",
        "en": "The event's objective (its name/title)",
        "es": "El objetivo del evento (su nombre/título)",
    },
    "opt_name_event_comp": {"pt": "comp", "en": "comp", "es": "comp"},
    "opt_desc_event_comp": {
        "pt": "Comp opcional; pode ser definida ou trocada depois",
        "en": "Optional comp; it can be set or changed later",
        "es": "Comp opcional; puede definirse o cambiarse después",
    },
    "opt_name_event_time": {"pt": "horario", "en": "time", "es": "hora"},
    "opt_desc_event_time": {
        "pt": "21h, 21:30 BRT, 11:30 CEST ou data completa",
        "en": "21h, 21:30 BRT, 11:30 CEST, or a full date",
        "es": "21h, 21:30 BRT, 11:30 CEST o fecha completa",
    },

    "cmd_desc_profile": {
        "pt": "Mostra o perfil de um jogador do Albion (fama, guilda, saldo e attendance)",
        "en": "Shows an Albion player's profile (fame, guild, balance and attendance)",
        "es": "Muestra el perfil de un jugador de Albion (fama, gremio, saldo y asistencia)",
    },
    "opt_desc_profile_jogador": {
        "pt": "Nick do jogador (em branco = o seu nick cadastrado)",
        "en": "Player's nickname (blank = your registered nick)",
        "es": "Nick del jugador (en blanco = tu nick registrado)",
    },
    "opt_name_profile_jogador": {"pt": "jogador", "en": "player", "es": "jugador"},

    "cmd_desc_attendance": {
        "pt": "Mostra estatísticas de participação em eventos CTA",
        "en": "Shows attendance stats for CTA events",
        "es": "Muestra estadísticas de participación en eventos CTA",
    },
    "opt_desc_attendance_target": {
        "pt": "Usuário (em branco = você)",
        "en": "User (blank = yourself)",
        "es": "Usuario (en blanco = tú)",
    },

    "cmd_desc_lowattendance": {
        "pt": "Lista membros com menor participação nos últimos 7 dias",
        "en": "Lists members with lowest attendance in the last 7 days",
        "es": "Lista miembros con menor participación en los últimos 7 días",
    },

    "cmd_desc_bypass": {
        "pt": "Remove um usuário do anúncio recorrente de não-registrados com acesso ao mass-info",
        "en": "Removes a user from the recurring unregistered-access announcement",
        "es": "Quita un usuario del anuncio recurrente de no registrados con acceso al mass-info",
    },
    "opt_desc_bypass_usuario": {
        "pt": "Usuário a remover do anúncio (menção, ID ou nome)",
        "en": "User to remove from the announcement (mention, ID, or name)",
        "es": "Usuario a quitar del anuncio (mención, ID o nombre)",
    },
}
