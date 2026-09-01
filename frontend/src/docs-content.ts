import type { CommandDoc, DocsLocale, DocsPage, Localized } from "./docs-types";

export const DOCS_LOCALES: DocsLocale[] = ["en", "pt", "es"];

export const DOCS_PAGES: DocsPage[] = [
  {
    slug: "getting-started",
    title: {
      en: "Getting started", pt: "Primeiros passos", es: "Primeros pasos",
    },
    description: {
      en: "Set up Ziggs and test the first guild workflow.",
      pt: "Configure o Ziggs e teste o primeiro fluxo da guilda.",
      es: "Configura Ziggs y prueba el primer flujo de la guild.",
    },
  },
  {
    slug: "guild-setup",
    title: {
      en: "Guild setup", pt: "Configuração da guilda", es: "Configuración de la guild",
    },
    description: {
      en: "Channels, roles, region and command permissions.",
      pt: "Canais, cargos, região e permissões de comandos.",
      es: "Canales, roles, región y permisos de comandos.",
    },
  },
  {
    slug: "commands",
    title: {
      en: "Command reference", pt: "Referência de comandos", es: "Referencia de comandos",
    },
    description: {
      en: "Slash commands, syntax, permissions and copyable examples.",
      pt: "Slash commands, sintaxe, permissões e exemplos copiáveis.",
      es: "Slash commands, sintaxis, permisos y ejemplos copiables.",
    },
  },
  {
    slug: "events",
    title: {
      en: "Events and signups", pt: "Eventos e inscrições", es: "Eventos e inscripciones",
    },
    description: {
      en: "Mass-info, role gates, pings and review flow.",
      pt: "Mass-info, gates de função, pings e fluxo de revisão.",
      es: "Mass-info, gates de función, pings y flujo de revisión.",
    },
  },
  {
    slug: "regear",
    title: {
      en: "Regear", pt: "Regear", es: "Regear",
    },
    description: {
      en: "Submit screenshots and follow the approval workflow.",
      pt: "Envie screenshots e acompanhe o fluxo de aprovação.",
      es: "Envía capturas y sigue el flujo de aprobación.",
    },
  },
  {
    slug: "lootlog",
    title: {
      en: "Lootlog", pt: "Lootlog", es: "Lootlog",
    },
    description: {
      en: "Private uploads, logger weights and reconciliation.",
      pt: "Uploads privados, pesos de loggers e reconciliação.",
      es: "Subidas privadas, pesos de loggers y reconciliación.",
    },
  },
  {
    slug: "troubleshooting",
    title: {
      en: "Troubleshooting", pt: "Solução de problemas", es: "Solución de problemas",
    },
    description: {
      en: "The short checklist for the most common failures.",
      pt: "A checklist curta para as falhas mais comuns.",
      es: "La checklist corta para los fallos más comunes.",
    },
  },
];

const text = (en: string, pt: string, es: string): Localized => ({ en, pt, es });

export const COMMANDS: CommandDoc[] = [
  {
    id: "avatar",
    command: "/avatar",
    category: "general",
    permission: text("Everyone", "Todos", "Todos"),
    prerequisites: text("The bot must be able to read the current server.", "O bot precisa conseguir ler o servidor atual.", "El bot debe poder leer el servidor actual."),
    description: text("Shows a user's or server's avatar. Without an argument, it uses the person who ran the command.", "Mostra o avatar de um usuário ou servidor. Sem argumento, usa quem executou o comando.", "Muestra el avatar de un usuario o servidor. Sin argumento, usa a quien ejecutó el comando."),
    syntax: ["/avatar", "/avatar [<user>]"],
    examples: [
      { input: "/avatar", result: text("Shows your current avatar.", "Mostra seu avatar atual.", "Muestra tu avatar actual.") },
      { input: "/avatar @Person", result: text("Shows that member's avatar.", "Mostra o avatar dessa pessoa.", "Muestra el avatar de esa persona.") },
    ],
  },
  {
    id: "banner",
    command: "/banner",
    category: "general",
    permission: text("Everyone", "Todos", "Todos"),
    prerequisites: text("The bot must be able to resolve the target user.", "O bot precisa conseguir resolver o usuário alvo.", "El bot debe poder resolver al usuario objetivo."),
    description: text("Shows a user's Discord profile banner.", "Mostra o banner do perfil Discord de um usuário.", "Muestra el banner del perfil de Discord de un usuario."),
    syntax: ["/banner", "/banner [<user>]"],
    examples: [
      { input: "/banner", result: text("Shows your profile banner, if you have one.", "Mostra seu banner, se você tiver um.", "Muestra tu banner, si tienes uno.") },
    ],
  },
  {
    id: "register",
    command: "/register",
    category: "registration",
    permission: text("The configured register permission.", "A permissão configurada para registro.", "El permiso configurado para registro."),
    prerequisites: text("Configure the Albion guild, region and register role first.", "Configure primeiro a guilda Albion, a região e o cargo de registro.", "Configura primero la guild de Albion, la región y el rol de registro."),
    description: text("Links an Albion character to a Discord member and assigns the configured role.", "Vincula um personagem Albion a um membro Discord e atribui o cargo configurado.", "Vincula un personaje de Albion a un miembro de Discord y asigna el rol configurado."),
    syntax: ["/register <character>", "/register <character> [<user>]"],
    examples: [
      { input: "/register Kaelen", result: text("Registers your own Albion character.", "Registra seu próprio personagem Albion.", "Registra tu propio personaje de Albion.") },
      { input: "/register Kaelen @Rivera", result: text("Registers Kaelen for Rivera, if you can register other people.", "Registra Kaelen para Rivera, se você puder registrar outras pessoas.", "Registra Kaelen para Rivera, si puedes registrar a otras personas.") },
    ],
  },
  {
    id: "unregister",
    command: "/unregister",
    category: "registration",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("The member must have an active Ziggs registration.", "O membro precisa ter um registro ativo no Ziggs.", "El miembro debe tener un registro activo en Ziggs."),
    description: text("Disables registrations and removes the related roles when possible.", "Desativa registros e remove os cargos relacionados quando possível.", "Desactiva registros y elimina los roles relacionados cuando es posible."),
    syntax: ["/unregister <user>"],
    examples: [
      { input: "/unregister @Rivera", result: text("Disables Rivera's active character registrations.", "Desativa os registros ativos de personagem de Rivera.", "Desactiva los registros activos de personaje de Rivera.") },
    ],
  },
  {
    id: "balance",
    command: "/balance",
    category: "economy",
    permission: text("Everyone.", "Todos.", "Todos."),
    prerequisites: text("None.", "Nenhum.", "Ninguno."),
    description: text("Shows the current balance for you or another member.", "Mostra o saldo atual seu ou de outro membro.", "Muestra el saldo actual tuyo o de otro miembro."),
    syntax: ["/balance", "/balance [<user>]"],
    examples: [
      { input: "/balance", result: text("Shows your balance in the current guild.", "Mostra seu saldo na guilda atual.", "Muestra tu saldo en la guild actual.") },
    ],
  },
  {
    id: "pay",
    command: "/pay",
    category: "economy",
    permission: text("Everyone, unless disabled or restricted in guild settings.", "Todos, salvo se desativado ou restringido na configuração da guilda.", "Todos, salvo que esté desactivado o restringido en la configuración de la guild."),
    prerequisites: text("You need enough balance and cannot pay yourself or a bot.", "Você precisa ter saldo suficiente e não pode pagar a si mesmo ou um bot.", "Necesitas saldo suficiente y no puedes pagar a ti mismo ni a un bot."),
    description: text("Transfers silver between members. Amounts accept numbers, k, m, b and all/tudo.", "Transfere prata entre membros. Valores aceitam números, k, m, b e all/tudo.", "Transfiere plata entre miembros. Acepta números, k, m, b y all/tudo."),
    syntax: ["/pay <user> <amount>"],
    examples: [
      { input: "/pay @Rivera 100k", result: text("Transfers 100,000 silver if your balance allows it.", "Transfere 100.000 de prata se seu saldo permitir.", "Transfiere 100.000 de plata si tu saldo lo permite.") },
    ],
  },
  {
    id: "addmoney",
    command: "/addmoney",
    category: "economy",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("The target can be a member or a role. Bots are ignored.", "O alvo pode ser membro ou cargo. Bots são ignorados.", "El objetivo puede ser miembro o rol. Los bots se ignoran."),
    description: text("Adds a separate transaction to each selected member.", "Adiciona uma transação separada para cada membro selecionado.", "Añade una transacción separada para cada miembro seleccionado."),
    syntax: ["/addmoney <user-or-role> <amount>"],
    examples: [
      { input: "/addmoney @Rivera 100k", result: text("Adds 100,000 silver and shows an undo transaction ID.", "Adiciona 100.000 de prata e mostra um ID de transação para desfazer.", "Añade 100.000 de plata y muestra un ID de transacción para deshacer.") },
    ],
  },
  {
    id: "removemoney",
    command: "/removemoney",
    category: "economy",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("Removing money from a role requires confirmation.", "Remover dinheiro de um cargo exige confirmação.", "Quitar dinero de un rol requiere confirmación."),
    description: text("Removes a balance. Without an amount, all/tudo, it removes only the current positive balance; an explicit amount may create a negative balance.", "Remove saldo. Sem valor, all/tudo, remove apenas o saldo positivo atual; um valor explícito pode criar saldo negativo.", "Quita saldo. Sin valor, all/tudo, elimina solo el saldo positivo actual; un valor explícito puede crear saldo negativo."),
    syntax: ["/removemoney <user-or-role> [<amount>]"],
    examples: [
      { input: "/removemoney @Rivera all", result: text("Removes Rivera's current positive balance.", "Remove o saldo positivo atual de Rivera.", "Quita el saldo positivo actual de Rivera.") },
    ],
  },
  {
    id: "undo",
    command: "/undo",
    category: "economy",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("Use the transaction ID shown in an economy embed.", "Use o ID da transação mostrado no embed da economia.", "Usa el ID de transacción mostrado en el embed de economía."),
    description: text("Reverses one economy transaction once.", "Reverte uma transação de economia uma vez.", "Revierte una transacción de economía una vez."),
    syntax: ["/undo <transaction-id>"],
    examples: [
      { input: "/undo 1234", result: text("Reverses the delta from transaction 1234.", "Reverte o delta da transação 1234.", "Revierte el delta de la transacción 1234.") },
    ],
  },
  {
    id: "transactions",
    command: "/transactions",
    category: "economy",
    permission: text("Everyone.", "Todos.", "Todos."),
    prerequisites: text("None.", "Nenhum.", "Ninguno."),
    description: text("Shows the full transaction history with pagination; event payouts link to the review thread.", "Mostra o histórico completo de transações com paginação; pagamentos de evento linkam para a thread de revisão.", "Muestra el historial completo de transacciones con paginación; los pagos de evento enlazan al hilo de revisión."),
    syntax: ["/transactions", "/transactions [<user>]"],
    examples: [
      { input: "/transactions", result: text("Shows your transactions five at a time with navigation buttons.", "Mostra suas transações cinco por vez com botões de navegação.", "Muestra tus transacciones de cinco en cinco con botones de navegación.") },
      { input: "/transactions @Rivera", result: text("Shows Rivera's transaction history.", "Mostra o histórico de transações de Rivera.", "Muestra el historial de transacciones de Rivera.") },
    ],
  },
  {
    id: "leaderboard",
    command: "/leaderboard",
    category: "economy",
    permission: text("Everyone.", "Todos.", "Todos."),
    prerequisites: text("None.", "Nenhum.", "Ninguno."),
    description: text("Shows the ten highest balances with private pagination controls for the author.", "Mostra os dez maiores saldos com paginação privada para quem executou.", "Muestra los diez saldos más altos con paginación privada para quien lo ejecutó."),
    syntax: ["/leaderboard"],
    examples: [{ input: "/leaderboard", result: text("Use first, previous, next and last buttons for five minutes.", "Use os botões primeiro, anterior, próximo e último por cinco minutos.", "Usa los botones primero, anterior, siguiente y último durante cinco minutos.") }],
  },
  {
    id: "economystats",
    command: "/economystats",
    category: "economy",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("None.", "Nenhum.", "Ninguno."),
    description: text("Shows the number of members with a balance and the sum of balances.", "Mostra quantos membros têm saldo e a soma dos saldos.", "Muestra cuántos miembros tienen saldo y la suma de los saldos."),
    syntax: ["/economystats"],
    examples: [{ input: "/economystats", result: text("Returns the current economy summary for the guild.", "Retorna o resumo atual da economia da guilda.", "Devuelve el resumen actual de la economía de la guild.") }],
  },
  {
    id: "event-create",
    command: "/event create",
    category: "events",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("Configure the events channel and use UTC.", "Configure o canal de eventos e use UTC.", "Configura el canal de eventos y usa UTC."),
    description: text("Creates a draft by default when details are incomplete; publish, signup, assignment and autofill policies are explicit.", "Cria um draft quando faltam dados; publicação, signup, escalação e autofill são políticas explícitas.", "Crea un borrador cuando faltan datos; publicación, signup, asignación y autofill son políticas explícitas."),
    syntax: ["/event create <utc-time> [objective] [comp] [publish] [signups] [assignment] [autofill]"],
    examples: [{ input: "/event create 21:00", result: text("Creates a private draft with no ping or signup.", "Cria um draft privado sem ping ou signup.", "Crea un borrador privado sin ping ni signup.") }, { input: "/event create 21:00 objective=ZvZ comp=Main publish=true", result: text("Publishes a signup CTA.", "Publica um CTA de signup.", "Publica un CTA de signup.") }],
  },
  {
    id: "event-publish",
    command: "/event publish",
    category: "events",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("There must be a draft event ready to publish.", "É preciso haver um draft pronto para publicar.", "Debe existir un borrador listo para publicar."),
    description: text("Publishes a selected draft after backend validation.", "Publica um draft selecionado após validação do backend.", "Publica un borrador seleccionado tras la validación del backend."),
    syntax: ["/event publish"],
    examples: [{ input: "/event publish", result: text("Select a draft and publish it.", "Selecione um draft e publique-o.", "Selecciona un borrador y publícalo.") }],
  },
  {
    id: "event-delete",
    command: "/event delete",
    category: "events",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("There must be an unfinished event to select.", "É preciso haver um evento não finalizado para selecionar.", "Debe existir un evento no finalizado para seleccionar."),
    description: text("Selects and confirms deletion of an unfinished event.", "Seleciona e confirma a exclusão de um evento não finalizado.", "Selecciona y confirma la eliminación de un evento no finalizado."),
    syntax: ["/event delete"],
    examples: [{ input: "/event delete", result: text("Choose an event, then confirm the deletion.", "Escolha um evento e confirme a exclusão.", "Elige un evento y confirma la eliminación.") }],
  },
  {
    id: "event-edit",
    command: "/event edit",
    category: "events",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("There must be an editable event.", "É preciso haver um evento editável.", "Debe existir un evento editable."),
    description: text("Changes an event objective, time, composition or attendance settings through a selection flow.", "Altera objetivo, horário, composição ou attendance por um fluxo de seleção.", "Cambia objetivo, horario, composición o attendance mediante un flujo de selección."),
    syntax: ["/event edit"],
    examples: [{ input: "/event edit", result: text("Select the event and field, then complete the form or UTC time picker.", "Selecione o evento e o campo, depois complete o formulário ou seletor de horário UTC.", "Selecciona el evento y el campo, luego completa el formulario o selector UTC.") }],
  },
  {
    id: "event-reschedule",
    command: "/event reschedule",
    category: "events",
    permission: text("Administrator by default.", "Admin por padrão.", "Administrador por defecto."),
    prerequisites: text("There must be an event that can be rescheduled.", "É preciso haver um evento que possa ser reagendado.", "Debe existir un evento que pueda reagendarse."),
    description: text("Moves an event to another UTC time.", "Move um evento para outro horário UTC.", "Mueve un evento a otra hora UTC."),
    syntax: ["/event reschedule"],
    examples: [{ input: "/event reschedule", result: text("Select an event and choose its new UTC time.", "Selecione um evento e escolha o novo horário UTC.", "Selecciona un evento y elige su nueva hora UTC.") }],
  },
];

export function localized(value: Localized, lang: DocsLocale): string {
  return value[lang] || value.en;
}

export const GUIDE_COPY: Record<string, { body: Localized; steps: Record<DocsLocale, string[]> }> = {
  events: {
    body: text(
      "Events are coordinated from the site and surfaced by the bot through one editable mass-info message.",
      "Os eventos são coordenados no site e publicados pelo bot em uma mensagem única de mass-info.",
      "Los eventos se coordinan desde el sitio y el bot los publica en un único mensaje de mass-info.",
    ),
    steps: {
      en: ["Configure the events channel and create an event with /event create using UTC.", "Members click the CTA button, choose an eligible category and select one or more functions.", "Role gates and party release rules decide which options are visible; staff with events.manage can bypass gates.", "The bot updates the same message when the event changes and sends only the configured ping triggers.", "When the event reaches review, the bot creates the review thread for participants, split and node capture."],
      pt: ["Configure o canal de eventos e crie um evento com /event create usando UTC.", "Os membros clicam no botão do CTA, escolhem uma categoria elegível e selecionam uma ou mais funções.", "Gates de cargo e regras de liberação das parties decidem quais opções aparecem; staff com events.manage ignora os gates.", "O bot atualiza a mesma mensagem quando o evento muda e envia apenas os pings configurados.", "Quando o evento entra em review, o bot cria a thread de revisão para participantes, split e captura de nodes."],
      es: ["Configura el canal de eventos y crea un evento con /event create usando UTC.", "Los miembros pulsan el botón del CTA, eligen una categoría elegible y seleccionan una o más funciones.", "Los gates de rol y las reglas de liberación de parties deciden qué opciones aparecen; el staff con events.manage ignora los gates.", "El bot actualiza el mismo mensaje cuando cambia el evento y solo envía los pings configurados.", "Cuando el evento entra en review, el bot crea el hilo de revisión para participantes, split y captura de nodes."],
    },
  },
  regear: {
    body: text(
      "Regear turns a screenshot into a trackable request. The bot watches configured channels and connects the result to the site.",
      "O regear transforma um screenshot em um pedido rastreável. O bot monitora os canais configurados e conecta o resultado ao site.",
      "Regear convierte una captura en una solicitud rastreable. El bot vigila los canales configurados y conecta el resultado con el sitio.",
    ),
    steps: {
      en: ["Configure the regear channel or the event regear thread channel.", "Post a PNG, JPG, JPEG or WEBP screenshot of the loot/death screen.", "The bot adds a processing reaction and sends the image to the backend OCR pipeline.", "Use the returned link to review recognized items, prices, eligibility and quantities on the site.", "If recognition is incomplete, fix the request manually instead of reposting the same message."],
      pt: ["Configure o canal de regear ou o canal de threads de regear dos eventos.", "Envie um screenshot PNG, JPG, JPEG ou WEBP da tela de loot/morte.", "O bot adiciona uma reação de processamento e envia a imagem para o OCR do backend.", "Use o link retornado para revisar itens reconhecidos, preços, cobertura e quantidades no site.", "Se o reconhecimento estiver incompleto, corrija o pedido manualmente em vez de repostar a mesma mensagem."],
      es: ["Configura el canal de regear o el canal de hilos de regear de los eventos.", "Publica una captura PNG, JPG, JPEG o WEBP de la pantalla de loot/muerte.", "El bot añade una reacción de procesamiento y envía la imagen al OCR del backend.", "Usa el enlace devuelto para revisar objetos reconocidos, precios, cobertura y cantidades en el sitio.", "Si el reconocimiento está incompleto, corrige la solicitud manualmente en vez de volver a publicar el mismo mensaje."],
    },
  },
  lootlog: {
    body: text(
      "Lootlogs are uploaded privately through the event thread. The public thread shows status, not the member's raw file.",
      "Lootlogs são enviados de forma privada pela thread do evento. A thread pública mostra o status, não o arquivo bruto do membro.",
      "Los lootlogs se suben de forma privada desde el hilo del evento. El hilo público muestra el estado, no el archivo bruto del miembro.",
    ),
    steps: {
      en: ["Configure the lootlog thread channel and let the bot create the event thread when the CTA starts.", "Click Upload log, attach a CSV or TXT file up to 15 MB and submit it through the private modal.", "The backend resolves the event from the thread; the user does not need to type a guild or event ID.", "The review embed shows logger weights, overlap and reconciliation status as submissions arrive.", "For Companion auto-submit, enable it only after login and let the worker submit when the event enters review."],
      pt: ["Configure o canal de threads de lootlog e deixe o bot criar a thread quando o CTA começar.", "Clique em Enviar log, anexe um CSV ou TXT de até 15 MB e envie pelo modal privado.", "O backend resolve o evento pela thread; o usuário não precisa informar guilda ou event ID.", "O embed de revisão mostra pesos de loggers, sobreposição e status da reconciliação conforme os envios chegam.", "Para auto-submit do Companion, habilite somente após o login e deixe o worker enviar quando o evento entrar em review."],
      es: ["Configura el canal de hilos de lootlog y deja que el bot cree el hilo cuando empiece el CTA.", "Pulsa Subir log, adjunta un CSV o TXT de hasta 15 MB y envíalo mediante el modal privado.", "El backend resuelve el evento por el hilo; el usuario no necesita indicar guild ni event ID.", "El embed de revisión muestra pesos de loggers, solapamiento y estado de reconciliación mientras llegan los envíos.", "Para el auto-submit del Companion, actívalo después de iniciar sesión y deja que el worker envíe cuando el evento entre en review."],
    },
  },
  troubleshooting: {
    body: text(
      "Most failures are configuration or permission mismatches. Check the layer that owns the behavior before restarting the bot.",
      "A maioria das falhas é de configuração ou permissão. Confira a camada responsável pelo comportamento antes de reiniciar o bot.",
      "La mayoría de los fallos son de configuración o permisos. Revisa la capa responsable antes de reiniciar el bot.",
    ),
    steps: {
      en: ["If the bot does not answer, check that it is online, can read the channel and the command is enabled in guild settings.", "If a role is not assigned, check the bot role hierarchy and the Albion guild/region configuration.", "If an event button is missing, check event gates, configured channels and whether the event state allows the action.", "If Albion data is delayed, wait for the public API retry instead of creating duplicate registrations or requests.", "If a CSV or screenshot fails, verify the file format/size and use the site request link for manual correction."],
      pt: ["Se o bot não responde, confira se está online, pode ler o canal e o comando está habilitado na guilda.", "Se um cargo não é atribuído, confira a hierarquia de cargos e a configuração da guilda/região Albion.", "Se um botão de evento sumiu, confira gates, canais configurados e se o estado do evento permite a ação.", "Se os dados do Albion estão atrasados, aguarde a retentativa da API pública em vez de criar registros ou pedidos duplicados.", "Se um CSV ou screenshot falhar, confirme formato/tamanho e use o link do pedido no site para corrigir manualmente."],
      es: ["Si el bot no responde, comprueba que esté online, pueda leer el canal y el comando esté habilitado en la guild.", "Si no se asigna un rol, comprueba la jerarquía de roles y la configuración de guild/región de Albion.", "Si falta un botón de evento, revisa gates, canales configurados y si el estado del evento permite la acción.", "Si los datos de Albion están retrasados, espera el reintento de la API pública en vez de crear registros o solicitudes duplicadas.", "Si falla un CSV o una captura, confirma formato/tamaño y usa el enlace de la solicitud en el sitio para corregirla manualmente."],
    },
  },
};
