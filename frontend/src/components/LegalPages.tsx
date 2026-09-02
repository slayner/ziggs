import { useLang, type Lang } from "../i18n";

// Páginas legais e institucionais do Ziggs. Texto em PT/EN/ES, baseado no que
// o site coleta de fato (Discord OAuth — identify+guilds, sem email; cookies
// de sessão; dados públicos da API do Albion). Placeholder de email
// (contato@ziggs.xyz) pra preencher quando tiver.
//
// Não é conselho jurídico — é o mínimo que cobre o que LGPD pede.
// Revise com um advogado antes de publicar em produção.

const CONTACT_EMAIL = "contato@ziggs.xyz";

function prose(s: string): { __html: string } {
  // Sanitização mínima: só <p>, <h2>, <h3>, <ul>, <li>, <strong>, <a>, <br>.
  // O texto vem de fonte própria (constantes aqui), não de usuário — não há
  // risco de XSS, mas escapamos <> soltos pra não virar tag acidental.
  const esc = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const html = esc
    .replace(/¨H2¨(.*?)¨\/H2¨/g, "<h2>$1</h2>")
    .replace(/¨H3¨(.*?)¨\/H3¨/g, "<h3>$1</h3>")
    .replace(/¨P¨(.*?)¨\/P¨/g, "<p>$1</p>")
    .replace(/¨UL¨([\s\S]*?)¨\/UL¨/g, "<ul>$1</ul>")
    .replace(/¨LI¨(.*?)¨\/LI¨/g, "<li>$1</li>")
    .replace(/¨B¨(.*?)¨\/B¨/g, "<strong>$1</strong>")
    .replace(/¨A¨(.*?)¨\/A¨/g, '<a href="$1">$1</a>')
    .replace(/¨BR¨/g, "<br />")
    .replace(/¨EMAIL¨/g, `<a href="mailto:${CONTACT_EMAIL}">${CONTACT_EMAIL}</a>`);
  return { __html: html };
}

function LegalLayout({ title, updated, body }: { title: string; updated: string; body: string }) {
  return (
    <div className="legal-page">
      <div className="legal-container">
        <h1>{title}</h1>
        {updated && <p className="legal-updated">{updated}</p>}
        <div className="legal-body" dangerouslySetInnerHTML={prose(body)} />
      </div>
    </div>
  );
}

// ── Termos de Uso ──────────────────────────────────────────────────────────

const TERMS: Record<Lang, { title: string; updated: string; body: string }> = {
  pt: {
    title: "Termos de Uso",
    updated: "Última atualização: 26 de julho de 2026",
    body: `¨H2¨1. Aceitação dos termos¨/H2¨
¨P¨Ao acessar ou usar o Ziggs (ziggs.xyz), você concorda com estes Termos de Uso. Se não concordar, não use o serviço.¨/P¨

¨H2¨2. Sobre o serviço¨/H2¨
¨P¨O Ziggs é uma plataforma independente para gerenciamento de guildas de Albion Online, oferecendo composições de batalha, eventos, rastreamento de batalhas, regear, crafting e mercado.¨/P¨
¨P¨O Ziggs ¨B¨não é afiliado, associado ou endossado¨/B¨ pela Sandbox Interactive GmbH (Albion Online) ou pela Discord Inc. Todos os nomes, marcas e conteúdos relacionados ao Albion Online pertencem à Sandbox Interactive GmbH.¨/P¨

¨H2¨3. Cadastro e acesso¨/H2¨
¨P¨O acesso ao Ziggs é feito exclusivamente através da autenticação via Discord. Ao entrar, autorizamos a leitura do seu perfil público do Discord (nome de usuário e avatar) e a lista de servidores dos quais você participa.¨/P¨
¨P¨Você é responsável por manter a segurança da sua conta Discord. O Ziggs não armazena sua senha do Discord — usamos OAuth 2.0.¨/P¨

¨H2¨4. Responsabilidades do usuário¨/H2¨
¨UL¨
¨LI¨Usar o serviço de forma lícita e em conformidade com estes Termos;¨/LI¨
¨LI¨Não tentar comprometer, sobrecarregar ou explorar falhas no serviço;¨/LI¨
¨LI¨Não usar bots, scripts ou ferramentas automatizadas para acessar o Ziggs de forma abusiva;¨/LI¨
¨LI¨Não fornecer informações falsas ao vincular seu personagem do Albion Online.¨/LI¨
¨/UL¨

¨H2¨5. Idade mínima¨/H2¨
¨P¨O Ziggs é direcionado a usuários com 13 anos ou mais. Ao usar o serviço, você afirma ter pelo menos 13 anos de idade ou estar acessando com a autorização dos pais ou responsáveis.¨/P¨

¨H2¨6. Propriedade intelectual¨/H2¨
¨P¨O código, design e conteúdo original do Ziggs são de propriedade dos seus autores. Dados de jogo (nomes de itens, jogadores, guildas, batalhas) são públicos e pertencem à Sandbox Interactive GmbH.¨/P¨

¨H2¨7. Limitação de responsabilidade¨/H2¨
¨P¨O Ziggs é fornecido "como está", sem garantias de disponibilidade, precisão ou adequação a um fim específico. Não nos responsabilizamos por perdas decorrentes do uso ou da indisponibilidade do serviço.¨/P¨

¨H2¨8. Suspensão de acesso¨/H2¨
¨P¨Podemos suspender ou encerrar o acesso de usuários que violem estes Termos, a critério exclusivo da administração.¨/P¨

¨H2¨9. Alterações dos termos¨/H2¨
¨P¨Estes Termos podem ser atualizados a qualquer momento. Alterações significativas serão comunicadas através do serviço. O uso continuado após a atualização constitui aceitação.¨/P¨

¨H2¨10. Legislação aplicável¨/H2¨
¨P¨Estes Termos são regidos pelas leis da República Federativa do Brasil. Eventuais disputas serão dirimidas no foro competente.¨/P¨

¨H2¨11. Contato¨/H2¨
¨P¨Dúvidas sobre estes Termos podem ser enviadas para ¨EMAIL¨.¨/P¨`,
  },
  en: {
    title: "Terms of Use",
    updated: "Last updated: July 26, 2026",
    body: `¨H2¨1. Acceptance of terms¨/H2¨
¨P¨By accessing or using Ziggs (ziggs.xyz), you agree to these Terms of Use. If you do not agree, do not use the service.¨/P¨

¨H2¨2. About the service¨/H2¨
¨P¨Ziggs is an independent platform for managing Albion Online guilds, offering battle compositions, events, battle tracking, regear, crafting and market features.¨/P¨
¨P¨Ziggs is ¨B¨not affiliated, associated, or endorsed by¨/B¨ Sandbox Interactive GmbH (Albion Online) or Discord Inc. All names, trademarks, and content related to Albion Online belong to Sandbox Interactive GmbH.¨/P¨

¨H2¨3. Registration and access¨/H2¨
¨P¨Access to Ziggs is done exclusively through Discord authentication. Upon login, we request access to your public Discord profile (username and avatar) and the list of servers you belong to.¨/P¨
¨P¨You are responsible for keeping your Discord account secure. Ziggs does not store your Discord password — we use OAuth 2.0.¨/P¨

¨H2¨4. User responsibilities¨/H2¨
¨UL¨
¨LI¨Use the service lawfully and in compliance with these Terms;¨/LI¨
¨LI¨Not attempt to compromise, overload, or exploit vulnerabilities in the service;¨/LI¨
¨LI¨Not use bots, scripts, or automated tools to access Ziggs in an abusive manner;¨/LI¨
¨LI¨Not provide false information when linking your Albion Online character.¨/LI¨
¨/UL¨

¨H2¨5. Minimum age¨/H2¨
¨P¨Ziggs is intended for users aged 13 or older. By using the service, you represent that you are at least 13 years old or are accessing it with parental or guardian authorization.¨/P¨

¨H2¨6. Intellectual property¨/H2¨
¨P¨The code, design, and original content of Ziggs are owned by its authors. Game data (item names, players, guilds, battles) is public and belongs to Sandbox Interactive GmbH.¨/P¨

¨H2¨7. Limitation of liability¨/H2¨
¨P¨Ziggs is provided "as is", without warranties of availability, accuracy, or fitness for a particular purpose. We are not liable for losses arising from the use or unavailability of the service.¨/P¨

¨H2¨8. Access suspension¨/H2¨
¨P¨We may suspend or terminate access for users who violate these Terms, at the sole discretion of the administration.¨/P¨

¨H2¨9. Changes to the terms¨/H2¨
¨P¨These Terms may be updated at any time. Significant changes will be communicated through the service. Continued use after updates constitutes acceptance.¨/P¨

¨H2¨10. Governing law¨/H2¨
¨P¨These Terms are governed by the laws of the Federative Republic of Brazil. Any disputes shall be resolved in the competent courts.¨/P¨

¨H2¨11. Contact¨/H2¨
¨P¨Questions about these Terms can be sent to ¨EMAIL¨.¨/P¨`,
  },
  es: {
    title: "Términos de Uso",
    updated: "Última actualización: 26 de julio de 2026",
    body: `¨H2¨1. Aceptación de los términos¨/H2¨
¨P¨Al acceder o usar Ziggs (ziggs.xyz), aceptas estos Términos de Uso. Si no estás de acuerdo, no uses el servicio.¨/P¨

¨H2¨2. Sobre el servicio¨/H2¨
¨P¨Ziggs es una plataforma independiente para la gestión de gremios de Albion Online, que ofrece composiciones de batalla, eventos, seguimiento de batallas, regear, crafting y mercado.¨/P¨
¨P¨Ziggs ¨B¨no está afiliado, asociado ni respaldado por¨/B¨ Sandbox Interactive GmbH (Albion Online) ni Discord Inc. Todos los nombres, marcas y contenidos relacionados con Albion Online pertenecen a Sandbox Interactive GmbH.¨/P¨

¨H2¨3. Registro y acceso¨/H2¨
¨P¨El acceso a Ziggs se realiza exclusivamente mediante autenticación con Discord. Al iniciar sesión, solicitamos acceso a tu perfil público de Discord (nombre de usuario y avatar) y la lista de servidores a los que perteneces.¨/P¨
¨P¨Eres responsable de mantener la seguridad de tu cuenta de Discord. Ziggs no almacena tu contraseña de Discord — usamos OAuth 2.0.¨/P¨

¨H2¨4. Responsabilidades del usuario¨/H2¨
¨UL¨
¨LI¨Usar el servicio de forma lícita y en cumplimiento con estos Términos;¨/LI¨
¨LI¨No intentar comprometer, sobrecargar o explotar vulnerabilidades del servicio;¨/LI¨
¨LI¨No usar bots, scripts o herramientas automatizadas para acceder a Ziggs de forma abusiva;¨/LI¨
¨LI¨No proporcionar información falsa al vincular tu personaje de Albion Online.¨/LI¨
¨/UL¨

¨H2¨5. Edad mínima¨/H2¨
¨P¨Ziggs está dirigido a usuarios de 13 años o más. Al usar el servicio, declaras tener al menos 13 años o estar accediendo con autorización de tus padres o tutores.¨/P¨

¨H2¨6. Propiedad intelectual¨/H2¨
¨P¨El código, diseño y contenido original de Ziggs son propiedad de sus autores. Los datos del juego (nombres de ítems, jugadores, gremios, batallas) son públicos y pertenecen a Sandbox Interactive GmbH.¨/P¨

¨H2¨7. Limitación de responsabilidad¨/H2¨
¨P¨Ziggs se proporciona "tal cual", sin garantías de disponibilidad, precisión o idoneidad para un propósito particular. No nos hacemos responsables de pérdidas derivadas del uso o indisponibilidad del servicio.¨/P¨

¨H2¨8. Suspensión de acceso¨/H2¨
¨P¨Podemos suspender o terminar el acceso de usuarios que violen estos Términos, a discreción exclusiva de la administración.¨/P¨

¨H2¨9. Cambios a los términos¨/H2¨
¨P¨Estos Términos pueden actualizarse en cualquier momento. Los cambios significativos se comunicarán a través del servicio. El uso continuado después de la actualización constituye aceptación.¨/P¨

¨H2¨10. Ley aplicable¨/H2¨
¨P¨Estos Términos se rigen por las leyes de la República Federativa de Brasil. Cualquier disputa se resolverá en los tribunales competentes.¨/P¨

¨H2¨11. Contacto¨/H2¨
¨P¨Las dudas sobre estos Términos pueden enviarse a ¨EMAIL¨.¨/P¨`,
  },
};

// ── Política de Privacidade ────────────────────────────────────────────────

const PRIVACY: Record<Lang, { title: string; updated: string; body: string }> = {
  pt: {
    title: "Política de Privacidade",
    updated: "Última atualização: 26 de julho de 2026",
    body: `¨H2¨1. Introdução¨/H2¨
¨P¨Esta Política descreve como o Ziggs (ziggs.xyz) coleta, usa e protege seus dados pessoais, em conformidade com a Lei Geral de Proteção de Dados (Lei 13.709/2018 — LGPD).¨/P¨

¨H2¨2. Dados que coletamos¨/H2¨
¨H3¨2.1 Dados de autenticação (Discord OAuth)¨/H3¨
¨UL¨
¨LI¨ID da sua conta Discord (número identificador);¨/LI¨
¨LI¨Nome de usuário e nome global do Discord;¨/LI¨
¨LI¨Avatar do Discord;¨/LI¨
¨LI¨Lista de servidores dos quais você participa (para identificar quais guildas você pode gerenciar).¨/LI¨
¨/UL¨
¨P¨¨B¨Não solicitamos nem armazenamos seu email.¨/B¨ Não pedimos acesso a suas mensagens, conexões ou outros dados do Discord.¨/P¨

¨H3¨2.2 Dados de jogo (Albion Online)¨/H3¨
¨P¨Quando você vincula seu personagem do Albion Online ao seu registro, armazenamos o nome do personagem e dados públicos obtidos da API do Albion (batalhas, kills, fama, estatísticas). Esses dados são públicos e acessíveis a qualquer pessoa através da API oficial do jogo.¨/P¨

¨H3¨2.3 Dados de uso¨/H3¨
¨P¨Coletamos automaticamente dados técnicos como endereço IP, tipo de navegador e páginas visitadas, necessários para o funcionamento e segurança do serviço.¨/P¨

¨H2¨3. Base legal¨/H2¨
¨P¨O tratamento dos seus dados se baseia em:¨/P¨
¨UL¨
¨LI¨¨B¨Consentimento¨/B¨ — ao autenticar via Discord e ao aceitar esta Política;¨/LI¨
¨LI¨¨B¨Legítimo interesse¨/B¨ — para segurança do serviço, prevenção de fraude e abuso;¨/LI¨
¨LI¨¨B¨Execução de contrato¨/B¨ — para fornecer as funcionalidades que você solicita.¨/LI¨
¨/UL¨

¨H2¨4. Como usamos seus dados¨/H2¨
¨UL¨
¨LI¨Autenticar e identificar usuários;¨/LI¨
¨LI¨Vincular personagens do Albion Online às contas;¨/LI¨
¨LI¨Exibir perfis de jogadores e guildas com dados públicos do jogo;¨/LI¨
¨LI¨Gerenciar eventos, composições e regears de guildas;¨/LI¨
¨LI¨Prevenir abuso e garantir a segurança do serviço.¨/LI¨
¨/UL¨

¨H2¨5. Compartilhamento¨/H2¨
¨P¨Não vendemos nem alugamos seus dados. Compartilhamos dados apenas nas seguintes situações:¨/P¨
¨UL¨
¨LI¨¨B¨Discord Inc.¨/B¨ — para autenticação (OAuth 2.0);¨/LI¨
¨LI¨¨B¨Sandbox Interactive GmbH (Albion Online API)¨/B¨ — para buscar dados públicos de jogo;¨/LI¨
¨LI¨¨B¨Google (AdSense)¨/B¨ — para exibição de anúncios. Consulte nossa ¨A¨/cookies¨/A¨.¨/LI¨
¨/UL¨

¨H2¨6. Cookies¨/H2¨
¨P¨Usamos cookies de sessão (necessários para o login). Detalhes na nossa ¨A¨/cookies¨/A¨.¨/P¨

¨H2¨7. Retenção¨/H2¨
¨P¨Mantemos seus dados pelo tempo necessário para fornecer o serviço. Dados de uso são mantidos por no máximo 90 dias. Dados de conta (Discord ID, personagens vinculados) são mantidos enquanto sua conta estiver ativa e podem ser excluídos mediante solicitação.¨/P¨

¨H2¨8. Seus direitos (LGPD)¨/H2¨
¨P¨Você pode exercer os seguintes direitos a qualquer momento:¨/P¨
¨UL¨
¨LI¨Acesso aos seus dados;¨/LI¨
¨LI¨Correção de dados incorretos;¨/LI¨
¨LI¨Exclusão dos seus dados ("direito ao esquecimento");¨/LI¨
¨LI¨Portabilidade dos dados;¨/LI¨
¨LI¨Revogação do consentimento.¨/LI¨
¨/UL¨
¨P¨Para exercer qualquer direito, escreva para ¨EMAIL¨.¨/P¨

¨H2¨9. Segurança¨/H2¨
¨P¨Adotamos medidas técnicas e organizacionais para proteger seus dados, incluindo criptografia em trânsito (TLS) e tokens de sessão assinados. Nenhuma medida é infalível, mas buscamos proteger seus dados contra acesso não autorizado.¨/P¨

¨H2¨10. Menores¨/H2¨
¨P¨O Ziggs não é direcionado a menores de 13 anos. Não coletamos deliberadamente dados de menores. Se acredita que um menor forneceu dados sem autorização dos pais, contate-nos para removê-los.¨/P¨

¨H2¨11. Alterações¨/H2¨
¨P¨Esta Política pode ser atualizada a qualquer momento. Alterações significativas serão comunicadas através do serviço.¨/P¨

¨H2¨12. Contato¨/H2¨
¨P¨Para dúvidas sobre privacidade, escreva para ¨EMAIL¨.¨/P¨`,
  },
  en: {
    title: "Privacy Policy",
    updated: "Last updated: July 26, 2026",
    body: `¨H2¨1. Introduction¨/H2¨
¨P¨This Policy describes how Ziggs (ziggs.xyz) collects, uses, and protects your personal data, in compliance with the Brazilian General Data Protection Law (Law 13.709/2018 — LGPD).¨/P¨

¨H2¨2. Data we collect¨/H2¨
¨H3¨2.1 Authentication data (Discord OAuth)¨/H3¨
¨UL¨
¨LI¨Your Discord account ID (numeric identifier);¨/LI¨
¨LI¨Discord username and global name;¨/LI¨
¨LI¨Discord avatar;¨/LI¨
¨LI¨List of servers you belong to (to identify which guilds you can manage).¨/LI¨
¨/UL¨
¨P¨¨B¨We do not request or store your email.¨/B¨ We do not request access to your messages, connections, or other Discord data.¨/P¨

¨H3¨2.2 Game data (Albion Online)¨/H3¨
¨P¨When you link your Albion Online character to your account, we store the character name and public data obtained from the Albion API (battles, kills, fame, statistics). This data is public and accessible to anyone through the game's official API.¨/P¨

¨H3¨2.3 Usage data¨/H3¨
¨P¨We automatically collect technical data such as IP address, browser type, and pages visited, necessary for the operation and security of the service.¨/P¨

¨H2¨3. Legal basis¨/H2¨
¨P¨The processing of your data is based on:¨/P¨
¨UL¨
¨LI¨¨B¨Consent¨/B¨ — when authenticating via Discord and accepting this Policy;¨/LI¨
¨LI¨¨B¨Legitimate interest¨/B¨ — for service security, fraud prevention, and abuse prevention;¨/LI¨
¨LI¨¨B¨Contract performance¨/B¨ — to provide the features you request.¨/LI¨
¨/UL¨

¨H2¨4. How we use your data¨/H2¨
¨UL¨
¨LI¨Authenticate and identify users;¨/LI¨
¨LI¨Link Albion Online characters to accounts;¨/LI¨
¨LI¨Display player and guild profiles with public game data;¨/LI¨
¨LI¨Manage guild events, compositions, and regears;¨/LI¨
¨LI¨Prevent abuse and ensure service security.¨/LI¨
¨/UL¨

¨H2¨5. Sharing¨/H2¨
¨P¨We do not sell or rent your data. We share data only in the following situations:¨/P¨
¨UL¨
¨LI¨¨B¨Discord Inc.¨/B¨ — for authentication (OAuth 2.0);¨/LI¨
¨LI¨¨B¨Sandbox Interactive GmbH (Albion Online API)¨/B¨ — to fetch public game data;¨/LI¨
¨LI¨¨B¨Google (AdSense)¨/B¨ — for ad display. See our ¨A¨/cookies¨/A¨.¨/LI¨
¨/UL¨

¨H2¨6. Cookies¨/H2¨
¨P¨We use session cookies (necessary for login). Details in our ¨A¨/cookies¨/A¨.¨/P¨

¨H2¨7. Retention¨/H2¨
¨P¨We keep your data for as long as necessary to provide the service. Usage data is kept for a maximum of 90 days. Account data (Discord ID, linked characters) is kept while your account is active and can be deleted upon request.¨/P¨

¨H2¨8. Your rights (LGPD)¨/H2¨
¨P¨You may exercise the following rights at any time:¨/P¨
¨UL¨
¨LI¨Access to your data;¨/LI¨
¨LI¨Correction of inaccurate data;¨/LI¨
¨LI¨Deletion of your data ("right to be forgotten");¨/LI¨
¨LI¨Data portability;¨/LI¨
¨LI¨Withdrawal of consent.¨/LI¨
¨/UL¨
¨P¨To exercise any right, write to ¨EMAIL¨.¨/P¨

¨H2¨9. Security¨/H2¨
¨P¨We adopt technical and organizational measures to protect your data, including encryption in transit (TLS) and signed session tokens. No measure is foolproof, but we strive to protect your data against unauthorized access.¨/P¨

¨H2¨10. Minors¨/H2¨
¨P¨Ziggs is not directed at children under 13. We do not knowingly collect data from minors. If you believe a minor has provided data without parental authorization, contact us to remove it.¨/P¨

¨H2¨11. Changes¨/H2¨
¨P¨This Policy may be updated at any time. Significant changes will be communicated through the service.¨/P¨

¨H2¨12. Contact¨/H2¨
¨P¨For privacy questions, write to ¨EMAIL¨.¨/P¨`,
  },
  es: {
    title: "Política de Privacidad",
    updated: "Última actualización: 26 de julio de 2026",
    body: `¨H2¨1. Introducción¨/H2¨
¨P¨Esta Política describe cómo Ziggs (ziggs.xyz) recopila, usa y protege sus datos personales, en cumplimiento de la Ley General de Protección de Datos de Brasil (Ley 13.709/2018 — LGPD).¨/P¨

¨H2¨2. Datos que recopilamos¨/H2¨
¨H3¨2.1 Datos de autenticación (Discord OAuth)¨/H3¨
¨UL¨
¨LI¨El ID de tu cuenta de Discord (identificador numérico);¨/LI¨
¨LI¨Nombre de usuario y nombre global de Discord;¨/LI¨
¨LI¨Avatar de Discord;¨/LI¨
¨LI¨Lista de servidores a los que perteneces (para identificar qué gremios puedes gestionar).¨/LI¨
¨/UL¨
¨P¨¨B¨No solicitamos ni almacenamos tu correo electrónico.¨/B¨ No pedimos acceso a tus mensajes, conexiones u otros datos de Discord.¨/P¨

¨H3¨2.2 Datos del juego (Albion Online)¨/H3¨
¨P¨Cuando vinculas tu personaje de Albion Online a tu cuenta, almacenamos el nombre del personaje y datos públicos obtenidos de la API de Albion (batallas, kills, fama, estadísticas). Estos datos son públicos y accesibles para cualquier persona a través de la API oficial del juego.¨/P¨

¨H3¨2.3 Datos de uso¨/H3¨
¨P¨Recopilamos automáticamente datos técnicos como dirección IP, tipo de navegador y páginas visitadas, necesarios para el funcionamiento y seguridad del servicio.¨/P¨

¨H2¨3. Base legal¨/H2¨
¨P¨El tratamiento de tus datos se basa en:¨/P¨
¨UL¨
¨LI¨¨B¨Consentimiento¨/B¨ — al autenticarte mediante Discord y aceptar esta Política;¨/LI¨
¨LI¨¨B¨Interés legítimo¨/B¨ — para la seguridad del servicio, prevención de fraude y abuso;¨/LI¨
¨LI¨¨B¨Ejecución de contrato¨/B¨ — para proporcionar las funcionalidades que solicitas.¨/LI¨
¨/UL¨

¨H2¨4. Cómo usamos tus datos¨/H2¨
¨UL¨
¨LI¨Autenticar e identificar usuarios;¨/LI¨
¨LI¨Vincular personajes de Albion Online a las cuentas;¨/LI¨
¨LI¨Mostrar perfiles de jugadores y gremios con datos públicos del juego;¨/LI¨
¨LI¨Gestionar eventos, composiciones y regears de gremios;¨/LI¨
¨LI¨Prevenir abuso y garantizar la seguridad del servicio.¨/LI¨
¨/UL¨

¨H2¨5. Compartición¨/H2¨
¨P¨No vendemos ni alquilamos tus datos. Compartimos datos solo en las siguientes situaciones:¨/P¨
¨UL¨
¨LI¨¨B¨Discord Inc.¨/B¨ — para autenticación (OAuth 2.0);¨/LI¨
¨LI¨¨B¨Sandbox Interactive GmbH (API de Albion Online)¨/B¨ — para obtener datos públicos del juego;¨/LI¨
¨LI¨¨B¨Google (AdSense)¨/B¨ — para mostrar anuncios. Consulta nuestra ¨A¨/cookies¨/A¨.¨/LI¨
¨/UL¨

¨H2¨6. Cookies¨/H2¨
¨P¨Usamos cookies de sesión (necesarias para el inicio de sesión). Detalles en nuestra ¨A¨/cookies¨/A¨.¨/P¨

¨H2¨7. Retención¨/H2¨
¨P¨Mantenemos tus datos durante el tiempo necesario para proporcionar el servicio. Los datos de uso se conservan un máximo de 90 días. Los datos de la cuenta (ID de Discord, personajes vinculados) se mantienen mientras la cuenta esté activa y pueden eliminarse previa solicitud.¨/P¨

¨H2¨8. Tus derechos (LGPD)¨/H2¨
¨P¨Puedes ejercer los siguientes derechos en cualquier momento:¨/P¨
¨UL¨
¨LI¨Acceso a tus datos;¨/LI¨
¨LI¨Corrección de datos incorrectos;¨/LI¨
¨LI¨Eliminación de tus datos ("derecho al olvido");¨/LI¨
¨LI¨Portabilidad de datos;¨/LI¨
¨LI¨Revocación del consentimiento.¨/LI¨
¨/UL¨
¨P¨Para ejercer cualquier derecho, escribe a ¨EMAIL¨.¨/P¨

¨H2¨9. Seguridad¨/H2¨
¨P¨Adoptamos medidas técnicas y organizativas para proteger tus datos, incluyendo cifrado en tránsito (TLS) y tokens de sesión firmados. Ninguna medida es infalible, pero nos esforzamos por proteger tus datos contra accesos no autorizados.¨/P¨

¨H2¨10. Menores¨/H2¨
¨P¨Ziggs no está dirigido a menores de 13 años. No recopilamos deliberadamente datos de menores. Si crees que un menor ha proporcionado datos sin autorización de los padres, contáctanos para eliminarlos.¨/P¨

¨H2¨11. Cambios¨/H2¨
¨P¨Esta Política puede actualizarse en cualquier momento. Los cambios significativos se comunicarán a través del servicio.¨/P¨

¨H2¨12. Contacto¨/H2¨
¨P¨Para dudas sobre privacidad, escribe a ¨EMAIL¨.¨/P¨`,
  },
};

// ── Política de Cookies ────────────────────────────────────────────────────

const COOKIES: Record<Lang, { title: string; updated: string; body: string }> = {
  pt: {
    title: "Política de Cookies",
    updated: "Última atualização: 26 de julho de 2026",
    body: `¨H2¨1. O que são cookies¨/H2¨
¨P¨Cookies são pequenos arquivos de texto armazenados no seu navegador quando você visita um site. Eles permitem que o site lembre informações sobre sua visita.¨/P¨

¨H2¨2. Cookies que usamos¨/H2¨
¨H3¨Cookies necessários¨/H3¨
¨UL¨
¨LI¨¨B¨ziggs_session¨/B¨ — cookie de sessão assinado, necessário para manter você autenticado. Sem ele, você seria deslogado a cada navegação. Não pode ser desativado.¨/LI¨
¨/UL¨

¨H3¨Anúncios do Google (AdSense)¨/H3¨
¨P¨Os anúncios são exibidos pelo Google AdSense, que pode definir cookies (incluindo o cookie DoubleClick) para veicular anúncios personalizados com base nas suas visitas a este e outros sites. Você pode desativar a personalização nas configurações de anúncios do Google.¨/P¨

¨H2¨3. Gerenciamento¨/H2¨
¨P¨Você pode gerenciar ou excluir cookies a qualquer momento nas configurações do seu navegador:¨/P¨
¨UL¨
¨LI¨¨A¨https://support.google.com/chrome/answer/95647¨/A¨ (Google Chrome);¨/LI¨
¨LI¨¨A¨https://support.mozilla.org/pt-BR/kb/limpar-cookies-dados-sites-firefox¨/A¨ (Mozilla Firefox);¨/LI¨
¨LI¨¨A¨https://support.apple.com/pt-br/guide/safari/sfri11471/mac¨/A¨ (Safari).¨/LI¨
¨/UL¨
¨H2¨4. Contato¨/H2¨
¨P¨Dúvidas sobre cookies podem ser enviadas para ¨EMAIL¨.¨/P`,
  },
  en: {
    title: "Cookie Policy",
    updated: "Last updated: July 26, 2026",
    body: `¨H2¨1. What are cookies¨/H2¨
¨P¨Cookies are small text files stored in your browser when you visit a website. They allow the site to remember information about your visit.¨/P¨

¨H2¨2. Cookies we use¨/H2¨
¨H3¨Necessary cookies¨/H3¨
¨UL¨
¨LI¨¨B¨ziggs_session¨/B¨ — signed session cookie, necessary to keep you authenticated. Without it, you would be logged out on every navigation. Cannot be disabled.¨/LI¨
¨/UL¨

¨H3¨Google ads (AdSense)¨/H3¨
¨P¨Ads are served by Google AdSense, which may set cookies (including the DoubleClick cookie) to serve personalized ads based on your visits to this and other sites. You can opt out of personalization in your Google Ads settings.¨/P¨

¨H2¨3. Management¨/H2¨
¨P¨You can manage or delete cookies at any time in your browser settings:¨/P¨
¨UL¨
¨LI¨¨A¨https://support.google.com/chrome/answer/95647¨/A¨ (Google Chrome);¨/LI¨
¨LI¨¨A¨https://support.mozilla.org/en-US/kb/clear-cookies-and-site-data-firefox¨/A¨ (Mozilla Firefox);¨/LI¨
¨LI¨¨A¨https://support.apple.com/guide/safari/sfri11471/mac¨/A¨ (Safari).¨/LI¨
¨/UL¨
¨H2¨4. Contact¨/H2¨
¨P¨Questions about cookies can be sent to ¨EMAIL¨.¨/P`,
  },
  es: {
    title: "Política de Cookies",
    updated: "Última actualización: 26 de julio de 2026",
    body: `¨H2¨1. ¿Qué son las cookies?¨/H2¨
¨P¨Las cookies son pequeños archivos de texto almacenados en tu navegador cuando visitas un sitio web. Permiten que el sitio recuerde información sobre tu visita.¨/P¨

¨H2¨2. Cookies que usamos¨/H2¨
¨H3¨Cookies necesarias¨/H3¨
¨UL¨
¨LI¨¨B¨ziggs_session¨/B¨ — cookie de sesión firmado, necesario para mantener tu autenticación. Sin él, serías desconectado en cada navegación. No se puede desactivar.¨/LI¨
¨/UL¨

¨H3¨Anuncios de Google (AdSense)¨/H3¨
¨P¨Los anuncios se muestran mediante Google AdSense, que puede establecer cookies (incluido el cookie DoubleClick) para mostrar anuncios personalizados según tus visitas a este y otros sitios. Puedes desactivar la personalización en la configuración de anuncios de Google.¨/P¨

¨H2¨3. Gestión¨/H2¨
¨P¨Puedes gestionar o eliminar cookies en cualquier momento en la configuración de tu navegador:¨/P¨
¨UL¨
¨LI¨¨A¨https://support.google.com/chrome/answer/95647¨/A¨ (Google Chrome);¨/LI¨
¨LI¨¨A¨https://support.mozilla.org/es/kb/eliminar-cookies-datos-sitios-firefox¨/A¨ (Mozilla Firefox);¨/LI¨
¨LI¨¨A¨https://support.apple.com/es/guide/safari/sfri11471/mac¨/A¨ (Safari).¨/LI¨
¨/UL¨
¨H2¨4. Contacto¨/H2¨
¨P¨Las dudas sobre cookies pueden enviarse a ¨EMAIL¨.¨/P¨¨`,
  },
};

// ── Sobre ──────────────────────────────────────────────────────────────────

const ABOUT: Record<Lang, { title: string; body: string }> = {
  pt: {
    title: "Sobre o Ziggs",
    body: `¨H2¨O que é o Ziggs¨/H2¨
¨P¨O Ziggs é uma plataforma gratuita para gerenciamento de guildas de Albion Online. Oferece ferramentas para composições de batalha, eventos, rastreamento de batalhas, regear, crafting e mercado — tudo integrado ao Discord.¨/P¨

¨H2¨Para quem¨/H2¨
¨P¨O Ziggs foi criado por jogadores de Albion Online para jogadores de Albion Online. É voltado para guildas que organizam CTAs, zvZs e querem manter o controle de presença, regear e divisão de loot de forma transparente.¨/P¨

¨H2¨Recursos¨/H2¨
¨UL¨
¨LI¨Composições de batalha (comps) com builds detalhadas;¨/LI¨
¨LI¨Eventos com inscrições, escalação e autofill;¨/LI¨
¨LI¨Rastreamento automático de batalhas e perfis de jogadores;¨/LI¨
¨LI¨Rankings de highscores (PvP, coleta, crafting);¨/LI¨
¨LI¨Regear com reconhecimento de prints;¨/LI¨
¨LI¨Divisão de loot com conciliação de loggers;¨/LI¨
¨LI¨Mercado com preços atualizados continuamente;¨/LI¨
¨LI¨Companion desktop para otimização de rota e damage meter.¨/LI¨
¨/UL¨

¨H2¨Tecnologia¨/H2¨
¨P¨O Ziggs é construído com FastAPI (backend), React + TypeScript (frontend) e Tauri + Rust (companion). O código é de autoria própria e não usa dados proprietários da Sandbox Interactive.¨/P¨

¨H2¨Créditos de dados¨/H2¨
¨P¨Os preços de mercado e a cotação de ouro são fornecidos pelo ¨A¨https://www.albion-online-data.com¨/A¨ (Albion Online Data Project), um projeto comunitário open-source que coleta dados de jogo via packet sniffing. Agradecemos à comunidade AODP por manter essa infraestrutura pública.¨/P¨

¨H2¨Contato¨/H2¨
¨P¨Para dúvidas, sugestões ou parcerias, escreva para ¨EMAIL¨.¨/P¨`,
  },
  en: {
    title: "About Ziggs",
    body: `¨H2¨What is Ziggs¨/H2¨
¨P¨Ziggs is a free platform for managing Albion Online guilds. It offers tools for battle compositions, events, battle tracking, regear, crafting, and market — all integrated with Discord.¨/P¨

¨H2¨Who it's for¨/H2¨
¨P¨Ziggs was created by Albion Online players for Albion Online players. It's aimed at guilds that organize CTAs, ZvZs, and want to keep transparent control of attendance, regear, and loot split.¨/P¨

¨H2¨Features¨/H2¨
¨UL¨
¨LI¨Battle compositions (comps) with detailed builds;¨/LI¨
¨LI¨Events with signups, escalation, and autofill;¨/LI¨
¨LI¨Automatic battle tracking and player profiles;¨/LI¨
¨LI¨Highscore rankings (PvP, gathering, crafting);¨/LI¨
¨LI¨Regear with screenshot recognition;¨/LI¨
¨LI¨Loot split with logger reconciliation;¨/LI¨
¨LI¨Market with continuously updated prices;¨/LI¨
¨LI¨Desktop companion for route optimization and damage meter.¨/LI¨
¨/UL¨

¨H2¨Technology¨/H2¨
¨P¨Ziggs is built with FastAPI (backend), React + TypeScript (frontend), and Tauri + Rust (companion). The code is our own and does not use proprietary data from Sandbox Interactive.¨/P¨

¨H2¨Data credits¨/H2¨
¨P¨Market prices and gold rates are provided by the ¨A¨https://www.albion-online-data.com¨/A¨ (Albion Online Data Project), a community-driven open-source project that collects game data via packet sniffing. We thank the AODP community for maintaining this public infrastructure.¨/P¨

¨H2¨Contact¨/H2¨
¨P¨For questions, suggestions, or partnerships, write to ¨EMAIL¨.¨/P¨`,
  },
  es: {
    title: "Sobre Ziggs",
    body: `¨H2¨¿Qué es Ziggs?¨/H2¨
¨P¨Ziggs es una plataforma gratuita para la gestión de gremios de Albion Online. Ofrece herramientas para composiciones de batalla, eventos, seguimiento de batallas, regear, crafting y mercado — todo integrado con Discord.¨/P¨

¨H2¨Para quién¨/H2¨
¨P¨Ziggs fue creado por jugadores de Albion Online para jugadores de Albion Online. Está dirigido a gremios que organizan CTAs, ZvZs y quieren mantener un control transparente de asistencia, regear y división de loot.¨/P¨

¨H2¨Características¨/H2¨
¨UL¨
¨LI¨Composiciones de batalla (comps) con builds detalladas;¨/LI¨
¨LI¨Eventos con inscripciones, escalación y autofill;¨/LI¨
¨LI¨Seguimiento automático de batallas y perfiles de jugadores;¨/LI¨
¨LI¨Rankings de highscores (PvP, recolección, crafting);¨/LI¨
¨LI¨Regear con reconocimiento de capturas;¨/LI¨
¨LI¨División de loot con conciliación de loggers;¨/LI¨
¨LI¨Mercado con precios actualizados continuamente;¨/LI¨
¨LI¨Companion de escritorio para optimización de ruta y damage meter.¨/LI¨
¨/UL¨

¨H2¨Tecnología¨/H2¨
¨P¨Ziggs está construido con FastAPI (backend), React + TypeScript (frontend) y Tauri + Rust (companion). El código es de autoría propia y no usa datos propietarios de Sandbox Interactive.¨/P¨

¨H2¨Créditos de datos¨/H2¨
¨P¨Los precios del mercado y la cotización de oro son proporcionados por ¨A¨https://www.albion-online-data.com¨/A¨ (Albion Online Data Project), un proyecto comunitario open-source que recopila datos del juego mediante packet sniffing. Agradecemos a la comunidad AODP por mantener esta infraestructura pública.¨/P¨

¨H2¨Contacto¨/H2¨
¨P¨Para dudas, sugerencias o colaboraciones, escribe a ¨EMAIL¨.¨/P¨`,
  },
};

// ── Contato ────────────────────────────────────────────────────────────────

const CONTACT: Record<Lang, { title: string; body: string }> = {
  pt: {
    title: "Contato",
    body: `¨H2¨Fale conosco¨/H2¨
¨P¨Para dúvidas, sugestões, denúncias de abuso ou questões legais, envie um email para ¨EMAIL¨.¨/P¨

¨H2¨Assuntos comuns¨/H2¨
¨UL¨
¨LI¨Dúvidas sobre uso da plataforma;¨/LI¨
¨LI¨Sugestões de novos recursos;¨/LI¨
¨LI¨Denúncias de uso abusivo ou fraude;¨/LI¨
¨LI¨Solicitações de exercício de direitos (LGPD);¨/LI¨
¨LI¨Parcerias e divulgação.¨/LI¨
¨/UL¨

¨H2¨Resposta¨/H2¨
¨P¨Respondemos em até 7 dias úteis. Para questões urgentes, indique "URGENTE" no assunto do email.¨/P¨`,
  },
  en: {
    title: "Contact",
    body: `¨H2¨Get in touch¨/H2¨
¨P¨For questions, suggestions, abuse reports, or legal matters, send an email to ¨EMAIL¨.¨/P¨

¨H2¨Common topics¨/H2¨
¨UL¨
¨LI¨Questions about using the platform;¨/LI¨
¨LI¨Feature suggestions;¨/LI¨
¨LI¨Reports of abusive use or fraud;¨/LI¨
¨LI¨Requests to exercise your rights (LGPD);¨/LI¨
¨LI¨Partnerships and promotion.¨/LI¨
¨/UL¨

¨H2¨Response time¨/H2¨
¨P¨We reply within 7 business days. For urgent matters, include "URGENT" in the email subject.¨/P¨`,
  },
  es: {
    title: "Contacto",
    body: `¨H2¨Contáctanos¨/H2¨
¨P¨Para dudas, sugerencias, denuncias de abuso o asuntos legales, envía un email a ¨EMAIL¨.¨/P¨

¨H2¨Temas comunes¨/H2¨
¨UL¨
¨LI¨Dudas sobre el uso de la plataforma;¨/LI¨
¨LI¨Sugerencias de nuevas funciones;¨/LI¨
¨LI¨Denuncias de uso abusivo o fraude;¨/LI¨
¨LI¨Solicitudes para ejercer tus derechos (LGPD);¨/LI¨
¨LI¨Colaboraciones y difusión.¨/LI¨
¨/UL¨

¨H2¨Tiempo de respuesta¨/H2¨
¨P¨Respondemos en un máximo de 7 días hábiles. Para asuntos urgentes, indica "URGENTE" en el asunto del email.¨/P¨`,
  },
};

// ── Componentes exportados ──────────────────────────────────────────────────

export function TermsPage() {
  const { lang } = useLang();
  const d = TERMS[lang];
  return <LegalLayout title={d.title} updated={d.updated} body={d.body} />;
}

export function PrivacyPage() {
  const { lang } = useLang();
  const d = PRIVACY[lang];
  return <LegalLayout title={d.title} updated={d.updated} body={d.body} />;
}

export function CookiesPage() {
  const { lang } = useLang();
  const d = COOKIES[lang];
  return <LegalLayout title={d.title} updated={d.updated} body={d.body} />;
}

export function AboutPage() {
  const { lang } = useLang();
  const d = ABOUT[lang];
  return <LegalLayout title={d.title} updated="" body={d.body} />;
}

export function ContactPage() {
  const { lang } = useLang();
  const d = CONTACT[lang];
  return <LegalLayout title={d.title} updated="" body={d.body} />;
}