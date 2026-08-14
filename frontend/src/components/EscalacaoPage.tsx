import { useEffect, useRef, useState, Fragment } from "react";
import { api, type Me, type EscalationOut, type EscalationRole, type RegearItem } from "../api";
import { useT, useLang, itemLocalName, type Lang } from "../i18n";
import { itemRenderUrl, ITEM_BY_ID } from "../data/albion-items";

interface Props { guildId: string; eventId: number; active?: boolean }

interface FlexPick {
  slotId: number;
  userId: number;
  userName: string | null;
  roles: EscalationRole[];
}

const NA = "—";

// Payload de drag&drop: "<origem>:<userId>" — a origem diz o que um drop
// "perdido" (fora de um slot válido) deve fazer. "slot" = arrastando pra FORA
// de um slot (inclusive de volta pro painel de inscritos, que é a forma
// deliberada de desalocar). "enlisted" = pegando alguém já escalado mas ainda
// flexível direto da lista de inscritos pra realocar em outro slot — se o
// drop não acertar um slot, não desaloca à toa (pedido explícito: soltar em
// qualquer lugar tirava o jogador da escala sem querer).
type DragOrigin = "slot" | "enlisted";
function dragPayload(origin: DragOrigin, userId: number): string {
  return `${origin}:${userId}`;
}
function parseDragPayload(raw: string): { origin: DragOrigin; userId: number } | null {
  const m = /^(slot|enlisted):(\d+)$/.exec(raw);
  if (!m) return null;
  return { origin: m[1] as DragOrigin, userId: Number(m[2]) };
}

// Ordem dos tipos de equipamento na coluna Build (arma primeiro).
const BUILD_ORDER = ["weapon", "offhand", "helmet", "armor", "boots", "cape", "food"];

// Colapso responsivo por cobertura: a página é centrada com o painel colado à
// direita de price (sem gap). Quando a página encolhe e o bloco não cabe, o
// painel vira overlay e "cobre" as colunas da direita; uma coluna só colapsa
// quando está 100% coberta (left da coluna >= left do painel). build é a parede:
// o painel nunca passa da direita de build. Índice 0-based do <th>: #(0) role(1)
// player(2) build(3) style(4) obs(5) price(6).
const COLLAPSE_ORDER = ["obs", "style"] as const;
const TH_INDEX: Record<string, number> = { build: 3, style: 4, obs: 5 };

type BuildImg = { item_id: string; name: string; quality: number; alt: boolean; slot: string };
type BuildGroup = { slotType: string; items: BuildImg[] };

// Grupos de itens da build por tipo (primário + alts). Vem de build_items — é onde
// o CompBuilder guarda a build (os campos de string única offhand/helmet/... não
// são populados). Renderiza imagens; alts ficam menores dentro do mesmo grupo.
// Ordena primário antes das alts (por índice) p/ não depender da ordem de
// build_items — se vier alt antes do primário, o alt ficava "fora do lugar".
function buildGroups(r: EscalationRole | undefined): BuildGroup[] {
  if (!r || !r.build_items?.length) return [];
  const groups: BuildGroup[] = [];
  for (const slotType of BUILD_ORDER) {
    const items: BuildImg[] = [];
    for (const bi of r.build_items) {
      if (bi.slot === slotType) items.push({ item_id: bi.item_id, name: bi.name, quality: bi.quality, alt: false, slot: bi.slot });
      else if (bi.slot.startsWith(slotType + "_alt_")) items.push({ item_id: bi.item_id, name: bi.name, quality: bi.quality, alt: true, slot: bi.slot });
    }
    if (!items.length) continue;
    items.sort((a, b) => {
      if (a.alt !== b.alt) return a.alt ? 1 : -1;
      const ia = parseInt(a.slot.match(/_alt_(\d+)$/)?.[1] ?? "0", 10);
      const ib = parseInt(b.slot.match(/_alt_(\d+)$/)?.[1] ?? "0", 10);
      return ia - ib;
    });
    groups.push({ slotType, items });
  }
  return groups;
}

function weaponOf(r: EscalationRole | undefined): RegearItem | undefined {
  return r?.build_items.find(bi => bi.slot === "weapon");
}

function localName(bi: { item_id: string; name: string }, lang: Lang): string {
  const it = ITEM_BY_ID.get(bi.item_id);
  return it ? itemLocalName(it, lang) : bi.name;
}

// --- Skills (expansão da bracket de jogador escalado) ---
function spellUrl(id: string): string {
  return `/render/spell/${encodeURIComponent(id)}`;
}

const GEAR_GROUP_ORDER = ["helmet", "armor", "boots", "cape", "offhand", "food"];
const SLOT_ORDER = ["Q", "W", "E", "R", "passive", "active"];
const slotRank = (s: string) => { const i = SLOT_ORDER.indexOf(s); return i < 0 ? 99 : i; };

// Agrupa gear_spells por item (helmet, helmet_alt_0, ...). Cada grupo vira um
// bloco com as skills escolhidas daquele item (incluindo alternativos).
function gearSpellGroups(role: EscalationRole): { groupKey: string; spells: { slotName: string; spellId: string }[] }[] {
  const map = new Map<string, { slotName: string; spellId: string }[]>();
  for (const [key, val] of Object.entries(role.gear_spells ?? {})) {
    if (!val) continue;
    const idx = key.lastIndexOf("_");
    if (idx <= 0) continue;
    const groupKey = key.slice(0, idx);
    const slotName = key.slice(idx + 1);
    (map.get(groupKey) ?? map.set(groupKey, []).get(groupKey)!).push({ slotName, spellId: val });
  }
  return [...map.entries()].map(([groupKey, spells]) => {
    spells.sort((a, b) => slotRank(a.slotName) - slotRank(b.slotName));
    return { groupKey, spells };
  }).sort((a, b) => {
    const baseA = a.groupKey.split("_alt_")[0], baseB = b.groupKey.split("_alt_")[0];
    const ca = GEAR_GROUP_ORDER.indexOf(baseA), cb = GEAR_GROUP_ORDER.indexOf(baseB);
    if ((ca < 0 ? 99 : ca) !== (cb < 0 ? 99 : cb)) return (ca < 0 ? 99 : ca) - (cb < 0 ? 99 : cb);
    return a.groupKey.localeCompare(b.groupKey);
  });
}

function weaponSpellsOf(role: EscalationRole): { slotName: string; spellId: string }[] {
  const out: { slotName: string; spellId: string }[] = [];
  if (role.q_spell) out.push({ slotName: "Q", spellId: role.q_spell });
  if (role.w_spell) out.push({ slotName: "W", spellId: role.w_spell });
  if (role.passive_spell) out.push({ slotName: "passive", spellId: role.passive_spell });
  return out;
}

// Render único da build (grupos por tipo, imagens, alts menores). Reusado na
// célula Build (slot de 1 role ou flex já escolhida) e no modal flex (uma build
// por botão) e na pilha "OU" (slot flex sem ninguém escalado).
function BuildImgs({ r, lang }: { r: EscalationRole; lang: Lang }) {
  const groups = buildGroups(r);
  if (groups.length === 0) return <span className="cs-empty">{NA}</span>;
  return (
    <div className="cs-build">
      {groups.map((g, i) => {
        // Sem separador entre arma e offhand — formam uma unidade (mão principal
        // + secundária). Nos demais pares mantém o divisor vertical.
        const prev = groups[i - 1];
        const showSep = i > 0 && !(prev.slotType === "weapon" && g.slotType === "offhand");
        return (
          <Fragment key={i}>
            {showSep && <span className="cs-build-sep" />}
            <span className="cs-build-group">
              {g.items.map((it, j) => (
                <img key={it.item_id + j} className={"cs-build-img" + (it.alt ? " alt" : "")}
                  src={itemRenderUrl(it.item_id, it.quality || 1)} title={localName(it, lang)} alt="" />
              ))}
            </span>
          </Fragment>
        );
      })}
    </div>
  );
}

function SpellImg({ spellId, slotName }: { spellId: string; slotName: string }) {
  return (
    <img
      className="esc-spell-img"
      src={spellUrl(spellId)}
      title={`${slotName}: ${spellId}`}
      alt={spellId}
      onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden"; }}
    />
  );
}

// Bloco de skills renderizado inline (na expansão da linha do próprio usuário).
function SkillsDetail({ role, lang }: { role: EscalationRole; lang: Lang }) {
  const weapon = weaponSpellsOf(role);
  const gear = gearSpellGroups(role);
  return (
    <div>
      <div className="cs-detail-skill-group">
        <span className="cs-detail-skill-sub">{role.weapon_name || "Arma"}</span>
        <div className="esc-skills-row">
          {weapon.length === 0 ? <span className="hint">{NA}</span>
            : weapon.map(s => <SpellImg key={s.spellId} spellId={s.spellId} slotName={s.slotName} />)}
        </div>
      </div>
      {gear.map(g => {
        const item = role.build_items.find(bi => bi.slot === g.groupKey);
        const label = item ? localName(item, lang) : g.groupKey;
        const isAlt = g.groupKey.includes("_alt_");
        return (
          <div key={g.groupKey} className="cs-detail-skill-group">
            <span className="cs-detail-skill-sub">{label}{isAlt ? " · alt" : ""}</span>
            <div className="esc-skills-row">
              {g.spells.map(s => <SpellImg key={g.groupKey + s.spellId + s.slotName} spellId={s.spellId} slotName={s.slotName} />)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function EscalacaoPage({ guildId, eventId, active = true }: Props) {
  const t = useT();
  const { lang } = useLang();
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [data, setData] = useState<EscalationOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [flexPick, setFlexPick] = useState<FlexPick | null>(null);
  const [popoverSlot, setPopoverSlot] = useState<number | null>(null);
  const [autoFillBusy, setAutoFillBusy] = useState(false);
  const [undoRunId, setUndoRunId] = useState<string | null>(null);
  const autoTried = useRef(false);
  const lastEnlistedKey = useRef<string>("");

  useEffect(() => { api.me().then(m => setMe(m)); }, []);

  const load = () => {
    api.escalacao(guildId, eventId).then(d => { setData(d); setErr(null); })
      .catch(async e => {
        const msg = String((e as Error)?.message ?? e);
        if (!autoTried.current && /não encontrada|sem acesso/i.test(msg)) {
          autoTried.current = true;
          try {
            const dg = await api.myDiscordGuilds();
            const found = dg.find(x => x.id === String(guildId));
            if (found) {
              await api.selectGuild(found.id, found.name, found.icon);
              window.location.reload();
              return;
            }
          } catch { /* cai pro erro abaixo */ }
        }
        setErr(msg);
      });
  };

  useEffect(() => {
    if (me === undefined || me === null) return;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me, guildId, eventId]);

  // Polling: checa por novos signups a cada 4s e atualiza a lista de
  // inscritos ao vivo (sem precisar recarregar a página) — sempre ligado,
  // independente do autofill. Só troca o estado quando o fingerprint dos
  // inscritos muda, pra não re-renderizar (e não atrapalhar um drag&drop em
  // curso) quando o poll não trouxe nada de novo.
  useEffect(() => {
    // ponytail: !active pausa o poll de 4s quando a página tá escondida
    // (keep-alive no App) — sem isso, alternar abas deixaria o poll rodando
    // em background batendo no backend a cada 4s pra uma tela que ninguém vê.
    if (!data || !active) return;
    const iv = setInterval(() => {
      api.escalacao(guildId, eventId).then(d => {
        const key = d.enlisted.map(s => `${s.user_id}:${s.functions.join(",")}`).sort().join("|")
          + `|comp:${d.event.comp_id ?? 0}|state:${d.event.state}`;
        if (key !== lastEnlistedKey.current) {
          lastEnlistedKey.current = key;
          setData(d);
        }
      }).catch(() => {});
    }, 4000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guildId, eventId, !!data]);

  // ── Colapso responsivo por cobertura (painel overlay) ────────────────────────
  // Dois modos conforme a página cabe ou não:
  //  • WIDE  (cabe): [planilha | painel] em fluxo, centrado, painel colado à direita
  //    de price — gap 0, sem cobertura, sem colapso.
  //  • NARROW (não cabe): painel vira overlay cobrindo da direita. Uma coluna só
  //    colapsa quando está 100% coberta (left da coluna >= left do painel). build é
  //    a parede: o painel nunca passa da direita de build (clamp). Se mesmo assim
  //    não couber, a página rola horizontal (overflow-x: auto) — build nunca some.
  // Hooks antes dos early-returns (Rules of Hooks).
  const tableRef = useRef<HTMLTableElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  useEffect(() => {
    const tbl = tableRef.current;            // .comp-sheet
    const wrap = tbl?.parentElement;          // .sheet-wrap
    const left = wrap?.parentElement;         // .comp-left
    const row = left?.parentElement;          // .esc-layout
    const panel = panelRef.current;           // .comp-right
    if (!tbl || !row || !panel) return;
    let raf = 0;
    const PANEL_W = 320;
    const recompute = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const rowRect = row.getBoundingClientRect();
        const avail = row.clientWidth;
        const fits = tbl.offsetWidth + PANEL_W <= avail + 1;
        row.classList.toggle("esc-narrow", !fits);
        // Posiciona o painel direto no DOM (sem flash ao trocar de modo).
        if (fits) {
          panel.style.left = ""; panel.style.right = "";     // WIDE: flex posiciona
        } else {
          const ths = tbl.tHead?.rows[0]?.cells;
          const buildRight = ths && ths[TH_INDEX.build]
            ? ths[TH_INDEX.build].getBoundingClientRect().right - rowRect.left : 0;
          // Parede: painel nunca passa da direita de build.
          const panelLeft = Math.max(avail - PANEL_W, buildRight);
          panel.style.left = panelLeft + "px"; panel.style.right = "auto";
        }
        setHidden(prev => {
          if (fits) {
            // WIDE: sem cobertura. Restaura colunas ocultas se ainda couberem.
            if (prev.size === 0) return prev;
            const restore = COLLAPSE_ORDER.filter(k => prev.has(k)).slice(-1)[0];
            if (restore) {
              tbl.classList.remove(`hide-${restore}`);
              if (tbl.offsetWidth + PANEL_W <= avail + 1) {
                const t = new Set(prev); t.delete(restore); return t;
              }
              tbl.classList.add(`hide-${restore}`);
            }
            return prev;
          }
          // NARROW: colapso por 100% de cobertura.
          const ths = tbl.tHead?.rows[0]?.cells;
          if (!ths) return prev;
          const colLeft = (key: string) => {
            const th = ths[TH_INDEX[key]];
            return th ? th.getBoundingClientRect().left - rowRect.left : Infinity;
          };
          const buildRight = ths[TH_INDEX.build]
            ? ths[TH_INDEX.build].getBoundingClientRect().right - rowRect.left : 0;
          const panelLeft = Math.max(avail - PANEL_W, buildRight);
          // Colapso: primeira coluna (da direita) ainda visível e 100% coberta.
          for (const k of COLLAPSE_ORDER) {
            if (prev.has(k)) continue;
            if (colLeft(k) >= panelLeft) return new Set(prev).add(k);
            break;
          }
          // Restauro: a mais à esquerda das ocultas deixou de ser 100% coberta.
          if (prev.size > 0) {
            const restore = COLLAPSE_ORDER.filter(k => prev.has(k)).slice(-1)[0];
            if (restore) {
              tbl.classList.remove(`hide-${restore}`);
              if (colLeft(restore) < panelLeft) {
                const t = new Set(prev); t.delete(restore); return t;
              }
              tbl.classList.add(`hide-${restore}`);
            }
          }
          return prev;
        });
      });
    };
    recompute();
    const ro = new ResizeObserver(recompute);
    ro.observe(row); ro.observe(tbl);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, [data, hidden]);

  if (me === undefined) return null;

  // Login gate: deslogado abre o link → pede Discord e volta pra esta página.
  if (me === null) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    return (
      <div className="login-gate">
        <i className="ti ti-lock login-gate-icon" />
        <p className="login-gate-title">{t("guildOnly")}</p>
        <p className="login-gate-sub">{t("loginRequired")}</p>
        <a className="btn btn-discord" href={`/auth/discord/login?next=${next}`} style={{ maxWidth: 280 }}>
          <i className="ti ti-brand-discord" /> {t("loginDiscord")}
        </a>
      </div>
    );
  }

  if (err) {
    return (
      <div className="login-gate">
        <i className="ti ti-shield-x login-gate-icon" />
        <p className="login-gate-title">{t("escNoAccess")}</p>
        <p className="login-gate-sub" style={{ maxWidth: 460 }}>{err}</p>
      </div>
    );
  }

  if (!data) {
    return <div className="login-gate"><p className="login-gate-sub">…</p></div>;
  }

  const canManage = data.can_manage;
  const assignedBySlot = new Map(
    data.assignments.flatMap(a => a.slot_id == null ? [] : [[a.slot_id, a] as [number, typeof a]])
  );
  // Slot onde o próprio usuário logado foi escalado (só ele vê a linha destacar
  // e expandir com a build/skills que precisa usar).
  const mySlotId = me ? (data.assignments.find(a => a.slot_id != null && String(a.user_id) === me.id)?.slot_id ?? null) : null;

  const tableCls = "comp-sheet" + [...hidden].map(k => ` hide-${k}`).join("");
  const assignedUserIds = new Set(data.assignments.map(a => a.user_id));
  // user_id -> functions escolhidas (pra avisar bypass)
  const enlistedMap = new Map(data.enlisted.map(s => [s.user_id, s]));
  // Slots vazios (sem assignment) — usado pra saber se um escalado ainda pode
  // preencher outro role (amarelo) ou se está "completo" (verde).
  const emptySlots = data.parties.flatMap(p => p.slots).filter(s => !assignedBySlot.get(s.id));
  const emptySlotRoleNames = new Set(emptySlots.flatMap(s => s.roles.map(r => r.name)));
  // user_id -> slot_id atual (pra arrastar de dentro do slot)
  const slotByUserId = new Map<number, number>();
  for (const a of data.assignments) { if (a.slot_id != null) slotByUserId.set(a.user_id, a.slot_id); }

  // Candidatos por slot: inscritos NÃO escalados cujas funções batem com alguma
  // role do slot. Já pré-computado uma vez por render — o popover só filtra por
  // slot_id. Slot flex (várias roles) lista quem bate com qualquer uma.
  const candidatesBySlot = new Map<number, typeof data.enlisted>();
  for (const slot of data.parties.flatMap(p => p.slots)) {
    const roleNames = new Set(slot.roles.map(r => r.name));
    const cands = data.enlisted.filter(s =>
      !assignedUserIds.has(s.user_id) && s.functions.some(f => roleNames.has(f))
    );
    if (cands.length) candidatesBySlot.set(slot.id, cands);
  }

  // Um escalado é "flexível" (amarelo) se alguma das suas funções casa com
  // um role de algum slot vazio. Senão é "completo" (verde). Declarado ANTES
  // do enlistedRank (que o chama) — const arrow fica em TDZ até aqui.
  const isFlexible = (userId: number): boolean => {
    const sig = enlistedMap.get(userId);
    if (!sig || sig.functions.length === 0) return false;
    return sig.functions.some(f => emptySlotRoleNames.has(f));
  };

  // Ordem da lista de inscritos: não escalados > amarelos (flex) > verdes
  // (completos) > tiraram o signup. O 4º grupo não vem de EventSignup —
  // remove_signup apaga a inscrição mas deixa o EventAssignment, então são
  // assignments órfãos (escalados sem inscrição); dedup por user_id.
  const enlistedRank = (uid: number): number =>
    !assignedUserIds.has(uid) ? 0 : isFlexible(uid) ? 1 : 2;
  const sortedEnlisted = [...data.enlisted].sort(
    (a, b) => enlistedRank(a.user_id) - enlistedRank(b.user_id)
  );
  const withdrawn = data.assignments
    .filter(a => !enlistedMap.has(a.user_id))
    .filter((a, i, arr) => arr.findIndex(x => x.user_id === a.user_id) === i);

  const doAssign = (slotId: number, userId: number, userName: string | null, gameRoleId: number) => {
    api.assignEscalacao(guildId, eventId, { slot_id: slotId, user_id: userId, user_name: userName, game_role_id: gameRoleId })
      .then(load).catch(e => alert(String((e as Error)?.message ?? e)));
  };

  // Atribui um signup a um slot. Slot de 1 role: direto. Slot flex (>1 role):
  // abre o modal de escolha já existente. Reusado pelo drag&drop e pelo popover
  // de candidatos por slot (clicar = atribuir, sem precisar arrastar).
  const assignSignupToSlot = (slotId: number, userId: number) => {
    const signup = enlistedMap.get(userId);
    const slot = data.parties.flatMap(p => p.slots).find(s => s.id === slotId);
    if (!slot) return;
    if (slot.roles.length === 1) {
      doAssign(slotId, userId, signup?.user_name ?? null, slot.roles[0].id);
    } else if (slot.roles.length > 1) {
      setFlexPick({ slotId, userId, userName: signup?.user_name ?? null, roles: slot.roles });
    }
  };

  const onDropSlot = (slotId: number, e: React.DragEvent) => {
    if (!canManage) return;
    e.preventDefault();
    const parsed = parseDragPayload(e.dataTransfer.getData("text/plain"));
    if (!parsed) return;
    assignSignupToSlot(slotId, parsed.userId);
  };

  return (
    <div style={{ padding: "16px 20px" }}>
      {data.parties.length === 0 && (
        <div className="login-gate">
          <i className="ti ti-layout-grid-off login-gate-icon" />
          <p className="login-gate-title">{t("escNoComp")}</p>
        </div>
      )}

      {data.parties.length > 0 && (
        <div className="comp-layout esc-layout">
          <div className="comp-left">
            <div className="sheet-wrap">
              <table ref={tableRef} className={tableCls}>
                <colgroup>
                  <col style={{ width: 36 }} />
                  <col style={{ minWidth: 150 }} />
                  <col style={{ minWidth: 130 }} />
                  <col style={{ minWidth: 200 }} />
                  <col style={{ minWidth: 130 }} />
                  <col style={{ minWidth: 140 }} />
                </colgroup>
                <thead>
                  <tr>
                    <th className="cs-rn">#</th>
                    <th className="cs-ph">{t("colRoleName")}</th>
                    <th className="cs-ph">{t("colPlayer")}</th>
                    <th className="cs-ph">{t("colBuild")}</th>
                    <th className="cs-ph">{t("colCombatStyle")}</th>
                    <th className="cs-ph">{t("colObs")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.parties.map(p => (
                    <PartyRows
                      key={p.id}
                      name={p.name || `Party ${p.position + 1}`}
                      slots={p.slots}
                      assignedBySlot={assignedBySlot}
                      canManage={canManage}
                      onDropSlot={onDropSlot}
                      onUnassignUser={(userId) => api.unassignUser(guildId, eventId, userId).then(load)}
                      mySlotId={mySlotId}
                      t={t}
                      lang={lang}
                      candidatesBySlot={candidatesBySlot}
                      popoverSlot={popoverSlot}
                      setPopoverSlot={setPopoverSlot}
                      onPickCandidate={(slotId, userId) => { setPopoverSlot(null); assignSignupToSlot(slotId, userId); }}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="comp-right" ref={panelRef}>
            <div className="card esc-panel">
              <div className="esc-header">
                <i className="ti ti-users esc-header-icon" />
                <div className="esc-header-text">
                  <div className="esc-title">{data.event.title || t("escTitle")}</div>
                  <div className="hint">{data.event.comp_name || "—"} · {data.event.state} · {data.event.seriousness}</div>
                </div>
                {!canManage && <span className="badge esc-readonly"><i className="ti ti-eye" /> {t("escReadonly")}</span>}
              </div>
              <div className="esc-enlisted-head">
                <i className="ti ti-list-check" /> {t("escEnlisted")} <span className="esc-count">{data.enlisted.length}</span>
                {canManage && (
                  <button
                    className="btn esc-autofill"
                    disabled={autoFillBusy}
                    onClick={async () => {
                      setAutoFillBusy(true);
                      try {
                        const preview = await api.previewAutofillEscalacao(guildId, eventId);
                        if (!preview.assignments.length) {
                          alert(t("escAutofillEmpty"));
                          return;
                        }
                        const lines = preview.assignments.map(a => `${a.user_name || "—"} → ${a.game_role_name}`).join("\n");
                        if (confirm(`${t("escAutofillPreview")}\n\n${lines}`)) {
                          const result = await api.autofillEscalacao(guildId, eventId);
                          setUndoRunId(result.run_id);
                          load();
                        }
                      } catch (e) {
                        alert(String((e as Error)?.message ?? e));
                      } finally {
                        setAutoFillBusy(false);
                      }
                    }}
                    title={t("escAutofill")}
                  >
                    <i className="ti ti-wand" />
                    {t("escAutofill")}
                  </button>
                )}
                {canManage && undoRunId && (
                  <button
                    className="btn"
                    disabled={autoFillBusy}
                    onClick={async () => {
                      setAutoFillBusy(true);
                      try {
                        await api.undoAutofillEscalacao(guildId, eventId, undoRunId);
                        setUndoRunId(null);
                        load();
                      } catch (e) {
                        alert(String((e as Error)?.message ?? e));
                      } finally {
                        setAutoFillBusy(false);
                      }
                    }}
                  >
                    <i className="ti ti-arrow-back-up" /> {t("escAutofillUndo")}
                  </button>
                )}
                {canManage && ["scheduled", "in_progress"].includes(data.event.state) && (
                  <button
                    className={"btn esc-gatelock" + (data.event.functions_released ? " esc-gatelock-on" : "")}
                    onClick={() => api.releaseFunctions(eventId, !data.event.functions_released, guildId)
                      .then(load)
                      .catch(e => alert(String((e as Error)?.message ?? e)))}
                    title={data.event.functions_released ? t("functionsReleasedOn" as never) : t("releaseFunctionsBtn" as never)}
                  >
                    <i className={"ti " + (data.event.functions_released ? "ti-lock-open" : "ti-lock")} />
                    {data.event.functions_released ? t("functionsReleasedOn" as never) : t("releaseFunctionsBtn" as never)}
                  </button>
                )}
              </div>
              <div
                className="esc-enlisted-list"
                onDragOver={canManage ? (e) => e.preventDefault() : undefined}
                onDrop={canManage ? (e) => {
                  const parsed = parseDragPayload(e.dataTransfer.getData("text/plain"));
                  // Só desaloca quando a origem é um SLOT (devolver pro pool é
                  // deliberado). Arrastar um flexível da própria lista de inscritos
                  // e soltar aqui de novo (sem acertar um slot) não faz nada.
                  if (parsed && parsed.origin === "slot") api.unassignUser(guildId, eventId, parsed.userId).then(load);
                } : undefined}
              >
                {data.enlisted.length === 0 && withdrawn.length === 0 && <div className="hint esc-empty">{t("escEmpty")}</div>}
                {sortedEnlisted.map(s => {
                  const placed = assignedUserIds.has(s.user_id);
                  const flexible = placed && isFlexible(s.user_id);
                  const cls = placed
                    ? (flexible ? " esc-signup-flexible" : " esc-signup-complete")
                    : "";
                  return (
                    <div
                      key={s.user_id}
                      className={"esc-signup" + cls}
                      draggable={canManage && (!placed || flexible)}
                      onDragStart={(e) => { if (canManage && (!placed || flexible)) { e.dataTransfer.setData("text/plain", dragPayload("enlisted", s.user_id)); e.dataTransfer.effectAllowed = "move"; } }}
                      // Amarelos (flex) são arrastáveis — um drag mal-disparado vira
                      // clique comum e removia sem querer. Verdes não são arrastáveis;
                      // antes saíam com clique único, o que removia ao clicar pra "ver".
                      // Agora ambos saem só no duplo clique (intencional).
                      onDoubleClick={placed && canManage ? () => api.unassignUser(guildId, eventId, s.user_id).then(load) : undefined}
                      title={placed ? t("escUnassignDblClick") : (canManage ? t("escDragHint") : "")}
                    >
                      {canManage && (!placed || flexible) && <i className="ti ti-grip-horizontal esc-signup-grip" />}
                      <span className="esc-signup-name">{s.user_name || String(s.user_id)}</span>
                      {s.functions.length > 0 && (
                        <span
                          className="esc-signup-fns"
                          title={s.functions.join(", ")}
                        >
                          <span className="esc-role-bracket">
                            {s.functions.slice(0, 2).map(f => (
                              <span key={f} className="esc-role-chip">{f}</span>
                            ))}
                            {s.functions.length > 2 && <span className="esc-role-more">+{s.functions.length - 2}</span>}
                          </span>
                          {s.functions.length > 2 && (
                            <span className="esc-role-tooltip">{s.functions.map(f => <span key={f} className="esc-role-chip">{f}</span>)}</span>
                          )}
                        </span>
                      )}
                      {placed && <i className={"ti " + (flexible ? "ti-alert-circle" : "ti-check") + " esc-signup-check"} />}
                    </div>
                  );
                })}
                {withdrawn.map(a => (
                  <div
                    key={`w-${a.user_id}`}
                    className="esc-signup esc-signup-withdrawn"
                    onDoubleClick={canManage ? () => api.unassignUser(guildId, eventId, a.user_id).then(load) : undefined}
                    title={canManage ? t("escWithdrewHint") : ""}
                  >
                    <span className="esc-signup-name">{a.user_name || String(a.user_id)}</span>
                    <span className="badge esc-signup-wbadge">{t("escWithdrew")}</span>
                    {canManage && <i className="ti ti-user-minus esc-signup-check" />}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {flexPick && (
        <div onClick={() => setFlexPick(null)} style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 50,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <div className="card" onClick={e => e.stopPropagation()} style={{ width: 320 }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>{t("escFlexPick")}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {flexPick.roles.map(r => (
                <button key={r.id} className="btn flexpick-btn" style={{ justifyContent: "flex-start", alignItems: "stretch", flexDirection: "column", gap: 4 }}
                  onClick={() => { doAssign(flexPick.slotId, flexPick.userId, flexPick.userName, r.id); setFlexPick(null); }}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <span className="badge" style={{ background: r.color || "var(--surface-2)", color: r.color ? "#fff" : "var(--muted)", fontSize: 10 }}>●</span>
                    {r.name}
                  </span>
                  <BuildImgs r={r} lang={lang} />
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

interface PartyRowsProps {
  name: string;
  slots: EscalationOut["parties"][number]["slots"];
  assignedBySlot: Map<number, EscalationOut["assignments"][number]>;
  canManage: boolean;
  onDropSlot: (slotId: number, e: React.DragEvent) => void;
  onUnassignUser: (userId: number) => void;
  mySlotId: number | null;
  t: (k: never) => string;
  lang: Lang;
  candidatesBySlot: Map<number, EscalationOut["enlisted"]>;
  popoverSlot: number | null;
  setPopoverSlot: (id: number | null) => void;
  onPickCandidate: (slotId: number, userId: number) => void;
}

function PartyRows({
  name, slots, assignedBySlot, canManage,
  onDropSlot, onUnassignUser, mySlotId, t, lang,
  candidatesBySlot, popoverSlot, setPopoverSlot, onPickCandidate,
}: PartyRowsProps) {
  const rows: React.ReactNode[] = [];
  let n = 0;
  for (const slot of slots) {
    n++;
    const a = assignedBySlot.get(slot.id);
    const chosen = a?.game_role_id ? slot.roles.find(r => r.id === a.game_role_id) : undefined;
    // Build/style/notes: slot de 1 role mostra preview sempre; slot flex (várias
    // roles) só mostra depois de atribuído e escolhida a função flex (chosen).
    const previewRole = slot.roles.length === 1 ? slot.roles[0] : chosen;
    const dropProps = canManage
      ? { onDragOver: (e: React.DragEvent) => e.preventDefault(), onDrop: (e: React.DragEvent) => onDropSlot(slot.id, e) }
      : {};
    const wItem = weaponOf(previewRole);
    const groups = buildGroups(previewRole);
    // Slot flex (>1 role) sem ninguém escalado: empilha todas as builds com
    // divisor "OU" no meio. Com jogador escalado, só a build escolhida.
    const isFlex = slot.roles.length > 1;
    const showAllFlex = isFlex && !chosen;
    // Linha do próprio usuário escalado — destaca e expande a build/skills.
    const isMine = mySlotId === slot.id;
    rows.push(
      <tr key={slot.id} {...dropProps} className={isMine ? "cs-myrow" : undefined}
        style={{ background: a ? (isMine ? "var(--gold-soft)" : "var(--info-soft)") : "var(--surface)" }}>
        <td className="cs-rn">{n}</td>
        <td className="cs-cell cs-role-cell">
          {wItem && (
            <img className="cs-role-weapon" src={itemRenderUrl(wItem.item_id, wItem.quality || 1)}
              title={localName(wItem, lang)} alt="" />
          )}
          {chosen
            ? <span className="ct" style={{ color: chosen.color || "var(--text)" }}>{chosen.name}</span>
            : slot.roles.length > 1
              ? <span>{slot.roles.map(r => <span key={r.id} className="ct" style={{ color: r.color || "var(--muted)" }}>{r.name}</span>)}</span>
              : <span className="cs-lbl">{slot.label || slot.fn || slot.roles[0]?.name || NA}</span>}
        </td>
        <td className={"cs-cell" + (popoverSlot === slot.id ? " cs-cell-open" : "")}>
          {a ? (
            <span
              className="ct cs-player-drag"
              draggable={canManage}
              onDragStart={(e) => { if (canManage) { e.dataTransfer.setData("text/plain", dragPayload("slot", a.user_id)); e.dataTransfer.effectAllowed = "move"; } }}
              onDoubleClick={canManage ? () => onUnassignUser(a.user_id) : undefined}
              title={canManage ? t("escDragMove" as never) : ""}
            >
              <i className="ti ti-grip-horizontal" style={{ fontSize: 12, opacity: .6 }} />
              <span className="cs-player-name">{a.user_name || String(a.user_id)}</span>
            </span>
          ) : canManage ? (
            <SlotCandidatePicker
              candidates={candidatesBySlot.get(slot.id) ?? []}
              open={popoverSlot === slot.id}
              onToggle={() => setPopoverSlot(popoverSlot === slot.id ? null : slot.id)}
              onPick={(uid) => onPickCandidate(slot.id, uid)}
              dropHint={t("escDropHint" as never)}
            />
          ) : <span className="cs-empty">{NA}</span>}
        </td>
        <td className="cs-cell cs-build-cell">
          {showAllFlex
            ? (
              <div className="cs-build-stack">
                {slot.roles.map((r, i) => (
                  <Fragment key={r.id}>
                    <BuildImgs r={r} lang={lang} />
                    {i < slot.roles.length - 1 && (
                      <div className="cs-build-or">
                        <span className="cs-build-or-line" />
                        <span className="cs-build-or-label">{t("escOr" as never)}</span>
                        <span className="cs-build-or-line" />
                      </div>
                    )}
                  </Fragment>
                ))}
              </div>
            )
            : (groups.length === 0
              ? <span className="cs-empty">{NA}</span>
              : <BuildImgs r={previewRole!} lang={lang} />)}
        </td>
        <td className="cs-cell">{previewRole?.play_style || NA}</td>
        <td className="cs-cell">{previewRole?.obs || NA}</td>
      </tr>
    );
    if (isMine) {
      const detailRole = chosen ?? slot.roles[0];
      if (detailRole) {
        rows.push(
          <tr key={slot.id + "-detail"} className="cs-myrow-detail">
            <td colSpan={6}>
              <div className="cs-detail-inner">
                <div className="cs-detail-section">
                  <div className="cs-detail-label">{t("colBuild" as never)}</div>
                  <BuildImgs r={detailRole} lang={lang} />
                </div>
                <div className="cs-detail-section">
                  <div className="cs-detail-label">Skills</div>
                  <SkillsDetail role={detailRole} lang={lang} />
                </div>
              </div>
            </td>
          </tr>
        );
      }
    }
  }
  return (
    <>
      <tr><td colSpan={6} className="cs-ph"><div className="cs-ph-inner"><span className="cs-pname">{name}</span></div></td></tr>
      {rows}
    </>
  );
}

// Popover de candidatos por slot: clicar no slot vazio abre a lista de inscritos
// compatíveis. Clique num candidato atribui direto (slot de 1 role) ou abre o
// modal de escolha (slot flex). Fecha ao clicar fora, ESC ou ao escolher.
// ponytail: overlay simples com stopPropagation — sem portal, sem lib.
function SlotCandidatePicker({
  candidates, open, onToggle, onPick, dropHint,
}: {
  candidates: EscalationOut["enlisted"];
  open: boolean;
  onToggle: () => void;
  onPick: (userId: number) => void;
  dropHint: string;
}) {
  useEffect(() => {
    if (!open) return;
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") onToggle(); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [open, onToggle]);
  const count = candidates.length;
  return (
    <div className="cs-slot-pick">
      <button
        type="button"
        className={"cs-slot-pick-btn" + (open ? " cs-slot-pick-open" : "")}
        onClick={(e) => { e.stopPropagation(); onToggle(); }}
        title={count ? `${count} candidato(s)` : dropHint}
      >
        {count ? (
          <><i className="ti ti-user-plus" style={{ fontSize: 12, opacity: .6 }} /><span className="cs-slot-pick-count">{count}</span></>
        ) : (
          <span className="cs-empty">{dropHint}</span>
        )}
      </button>
      {open && (
        <>
          <div className="cs-slot-pick-overlay" onClick={onToggle} />
          <div className="cs-slot-pick-menu" onClick={(e) => e.stopPropagation()}>
            {count === 0
              ? <div className="hint cs-slot-pick-empty">Nenhum inscrito compatível</div>
              : candidates.map(s => (
                <button
                  key={s.user_id}
                  type="button"
                  className="cs-slot-pick-item"
                  onClick={() => onPick(s.user_id)}
                >
                  <span className="cs-slot-pick-name">{s.user_name || String(s.user_id)}</span>
                  {s.functions.length > 0 && (
                    <span className="cs-slot-pick-fns">
                      {s.functions.slice(0, 3).map(f => <span key={f} className="esc-role-chip">{f}</span>)}
                      {s.functions.length > 3 && <span className="cs-slot-pick-more">+{s.functions.length - 3}</span>}
                    </span>
                  )}
                </button>
              ))}
          </div>
        </>
      )}
    </div>
  );
}
