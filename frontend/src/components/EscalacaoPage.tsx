import { useEffect, useRef, useState, Fragment } from "react";
import { api, type Me, type EscalationOut, type EscalationRole, type EscalationSlot, type EscalationSignup, type RegearItem } from "../api";
import { useT, useLang, itemLocalName, type Lang, type TKey } from "../i18n";
import { itemRenderUrl, ITEM_BY_ID, is2H } from "../data/albion-items";
import { EquipGrid } from "./comp/EquipGrid";
import { buildItemsToEquip, itemUrl, DEFAULT_FN_TYPES, getFnDef } from "./comp/helpers";
import type { FnTypeDef } from "./comp/types";
import AdBanner from "./AdBanner";
import AffiliateBanner from "./AffiliateBanner";

interface Props {
  token?: string;
  guildId?: string;
  eventId?: number;
  active?: boolean;
}

interface FlexPick {
  slotId: number;
  userId: number;
  userName: string | null;
  roles: EscalationRole[];
}

// Detalhe aberto pelo bracket do jogador escalado (manager e read-only).
interface Detail {
  userName: string | null;
  role: EscalationRole;
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

// --- Skills (bloco do modal de detalhe) ---
function spellUrl(id: string): string {
  return `/render/spell/${encodeURIComponent(id)}`;
}

// Render único da build (grupos por tipo, imagens, alts menores). Reusado na
// pilha "OU" do slot flex vazio.
function BuildImgs({ r, lang }: { r: EscalationRole; lang: Lang }) {
  const groups = buildGroups(r);
  if (groups.length === 0) return <span className="cs-empty">{NA}</span>;
  return (
    <div className="cs-build">
      {groups.map((g, i) => {
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



// fn_key espelha o backend (event_gates.fn_key) — casefold + collapse whitespace,
// fallback "other". Usado só pra casar pair keys do signup com pair keys do slot.
function fnKey(fn: string | null | undefined): string {
  const parts = (fn ?? "").toLowerCase().trim().split(/\s+/).filter(Boolean);
  return parts.length ? parts.join(" ") : "other";
}
function pairKey(weaponId: number, fn: string | null | undefined): string {
  return `w${weaponId}:${fnKey(fn)}`;
}

// weapon_id -> item_id: deriva das roles da comp (cada role com weapon_id tem
// build_items contendo o slot "weapon" com o item_id canônico do Albion).
// Sem backend novo — a UI monta o mapa uma vez por render.
function buildWeaponItemIdMap(data: EscalationOut): Map<number, string> {
  const m = new Map<number, string>();
  for (const p of data.parties) {
    for (const s of p.slots) {
      for (const r of s.roles) {
        if (r.weapon_id != null) {
          const w = r.build_items.find(bi => bi.slot === "weapon");
          if (w && !m.has(r.weapon_id)) m.set(r.weapon_id, w.item_id);
        }
      }
    }
  }
  return m;
}

export default function EscalacaoPage({ token, guildId: legacyGuildId, eventId: legacyEventId, active = true }: Props) {
  const t = useT();
  const { lang } = useLang();
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [data, setData] = useState<EscalationOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [flexPick, setFlexPick] = useState<FlexPick | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [popoverSlot, setPopoverSlot] = useState<number | null>(null);
  const [autoFillBusy, setAutoFillBusy] = useState(false);
  const [undoRunId, setUndoRunId] = useState<string | null>(null);
  const autoTried = useRef(false);
  const lastEnlistedKey = useRef<string>("");

  useEffect(() => { api.me().then(m => setMe(m)); }, []);

  const load = () => {
    const request = token
      ? api.publicEscalacao(token)
      : (legacyGuildId && legacyEventId ? api.escalacao(legacyGuildId, legacyEventId) : null);
    if (!request) return;
    request.then(async d => {
      // O token só libera leitura; controles administrativos vêm da rota protegida.
      // Não aplica a resposta pública antes dela, ou a rail some brevemente.
      if (token && me) {
        try { d = await api.escalacao(d.event.guild_id, d.event.id); } catch { /* fallback público */ }
      }
      setData(d);
      setErr(null);
    })
      .catch(async e => {
        const msg = String((e as Error)?.message ?? e);
        if (!token && !autoTried.current && /não encontrada|sem acesso/i.test(msg)) {
          autoTried.current = true;
          try {
            const dg = await api.myDiscordGuilds();
            const found = dg.find(x => x.id === String(legacyGuildId));
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
    if (!token && (me === undefined || me === null)) return;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me, token, legacyGuildId, legacyEventId]);

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
      const request = token
        ? api.publicEscalacao(token)
        : api.escalacao(data.event.guild_id, data.event.id);
      request.then(async d => {
        if (token && me) {
          try { d = await api.escalacao(d.event.guild_id, d.event.id); } catch { /* permanece somente leitura */ }
        }
        const key = d.enlisted.map(s => `${s.user_id}:${s.functions.join(",")}:${(s.keys ?? []).join(",")}`).sort().join("|")
          + `|assignments:${d.assignments.map(a => `${a.slot_id}:${a.user_id}:${a.game_role_id}:${a.locked}`).sort().join(",")}`
          + `|comp:${d.event.comp_id ?? 0}|state:${d.event.state}`;
        if (key !== lastEnlistedKey.current) {
          lastEnlistedKey.current = key;
          setData(d);
        }
      }).catch(() => {});
    }, 4000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me, token, legacyGuildId, legacyEventId, !!data]);

  if (!token && me === undefined) return null;

  // Links antigos continuam autenticados; o novo link por token é público.
  if (!token && me === null) {
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

  const guildId = data.event.guild_id;
  const eventId = data.event.id;
  const canManage = data.can_manage;
  const assignedBySlot = new Map(
    data.assignments.flatMap(a => a.slot_id == null ? [] : [[a.slot_id, a] as [number, typeof a]])
  );
  const assignedUserIds = new Set(data.assignments.map(a => a.user_id));
  const weaponItemId = buildWeaponItemIdMap(data);
  // Mapa: user_id -> item_id da arma da role em que foi escalado. Usa o slot
  // do assignment, pega a role cujo game_role_id bate (assignment.game_role_id)
  // ou a primeira role do slot, e resolve o weapon_id -> item_id.
  const slotById = new Map<number, EscalationSlot>();
  for (const p of data.parties) for (const s of p.slots) slotById.set(s.id, s);
  const assignedRoleRender = new Map<number, string>();
  for (const a of data.assignments) {
    if (a.slot_id == null) continue;
    const slot = slotById.get(a.slot_id);
    if (!slot) continue;
    const role = a.game_role_id != null
      ? slot.roles.find(r => r.id === a.game_role_id)
      : slot.roles[0];
    if (!role) continue;
    // weapon_id -> item_id (via mapa) ou direto do build_items da role
    let itemId: string | undefined;
    if (role.weapon_id != null) {
      itemId = weaponItemId.get(role.weapon_id);
    }
    if (!itemId) {
      const w = role.build_items.find(bi => bi.slot === "weapon");
      if (w) itemId = w.item_id;
    }
    if (itemId) assignedRoleRender.set(a.user_id, itemId);
  }
  // Slot onde o próprio usuário logado foi escalado (destaca a linha).
  const mySlotId = me ? (data.assignments.find(a => a.slot_id != null && String(a.user_id) === me.id)?.slot_id ?? null) : null;

  // Candidatos por slot: inscritos NÃO escalados cujas funções batem com alguma
  // role do slot. Já pré-computado uma vez por render — o popover só filtra por
  // slot_id. Slot flex (várias roles) lista quem bate com qualquer uma.
  const candidatesBySlot = new Map<number, EscalationSignup[]>();
  for (const slot of data.parties.flatMap(p => p.slots)) {
    const roleNames = new Set(slot.roles.map(r => r.name));
    // Pair keys que o slot aceita (weapon_id, fn de cada role).
    const slotPairs = new Set<string>();
    for (const r of slot.roles) {
      if (r.weapon_id != null) slotPairs.add(pairKey(r.weapon_id, slot.fn));
    }
    const cands = data.enlisted.filter(s =>
      !assignedUserIds.has(s.user_id) && (
        s.functions.some(f => roleNames.has(f)) ||
        (s.keys ?? []).some(k => slotPairs.has(k))
      )
    );
    if (cands.length) candidatesBySlot.set(slot.id, cands);
  }

  const doAssign = (slotId: number, userId: number, userName: string | null, gameRoleId: number) => {
    api.assignEscalacao(guildId, eventId, { slot_id: slotId, user_id: userId, user_name: userName, game_role_id: gameRoleId })
      .then(load).catch(e => alert(String((e as Error)?.message ?? e)));
  };

  // Atribui um signup a um slot. Slot de 1 role: direto. Slot flex (>1 role):
  // abre o modal de escolha já existente. Reusado pelo drag&drop e pelo popover
  // de candidatos por slot (clicar = atribuir, sem precisar arrastar).
  const assignSignupToSlot = (slotId: number, userId: number) => {
    const signup = data.enlisted.find(s => s.user_id === userId);
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
    <div className="esc-page">
      {data.parties.length === 0 && (
        <div className="login-gate">
          <i className="ti ti-layout-grid-off login-gate-icon" />
          <p className="login-gate-title">{t("escNoComp")}</p>
        </div>
      )}

      {data.parties.length > 0 && (
        <EscalationBoard
          data={data}
          canManage={canManage}
          mySlotId={mySlotId}
          weaponItemId={weaponItemId}
          assignedRoleRender={assignedRoleRender}
          assignedBySlot={assignedBySlot}
          candidatesBySlot={candidatesBySlot}
          popoverSlot={popoverSlot}
          setPopoverSlot={setPopoverSlot}
          onDropSlot={onDropSlot}
          onPickCandidate={(slotId, userId) => { setPopoverSlot(null); assignSignupToSlot(slotId, userId); }}
          onUnassignUser={(userId) => api.unassignUser(guildId, eventId, userId).then(load)}
          onOpenDetail={setDetail}
          onAutofill={async () => {
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
          autoFillBusy={autoFillBusy}
          undoRunId={undoRunId}
          onUndoAutofill={async () => {
            setAutoFillBusy(true);
            try {
              await api.undoAutofillEscalacao(guildId, eventId, undoRunId!);
              setUndoRunId(null);
              load();
            } catch (e) {
              alert(String((e as Error)?.message ?? e));
            } finally {
              setAutoFillBusy(false);
            }
          }}
          onToggleRelease={() => api.releaseFunctions(eventId, !data.event.functions_released, guildId)
            .then(load)
            .catch(e => alert(String((e as Error)?.message ?? e)))}
          onEnlistedDrop={(raw) => {
            const parsed = parseDragPayload(raw);
            // Só desaloca quando a origem é um SLOT (devolver pro pool é
            // deliberado). Arrastar um flexível da própria lista de inscritos
            // e soltar aqui de novo (sem acertar um slot) não faz nada.
            if (parsed && parsed.origin === "slot") api.unassignUser(guildId, eventId, parsed.userId).then(load);
          }}
          t={t}
          lang={lang}
        />
      )}

      {flexPick && (
        <div onClick={() => setFlexPick(null)} className="esc-modal-overlay">
          <div className="card esc-modal-card" onClick={e => e.stopPropagation()}>
            <div className="esc-modal-title">{t("escFlexPick")}</div>
            <div className="esc-flexpick-list">
              {flexPick.roles.map(r => (
                <button key={r.id} className="btn flexpick-btn"
                  onClick={() => { doAssign(flexPick.slotId, flexPick.userId, flexPick.userName, r.id); setFlexPick(null); }}>
                  <span className="flexpick-btn-head">
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

      {detail && (
        <DetailModal detail={detail} t={t} lang={lang} onClose={() => setDetail(null)} />
      )}
    </div>
  );
}

// ── Board: rail (manager) + faixa horizontal de colunas de party ──────────────
interface BoardProps {
  data: EscalationOut;
  canManage: boolean;
  mySlotId: number | null;
  weaponItemId: Map<number, string>;
  assignedRoleRender: Map<number, string>;
  assignedBySlot: Map<number, EscalationOut["assignments"][number]>;
  candidatesBySlot: Map<number, EscalationSignup[]>;
  popoverSlot: number | null;
  setPopoverSlot: (id: number | null) => void;
  onDropSlot: (slotId: number, e: React.DragEvent) => void;
  onPickCandidate: (slotId: number, userId: number) => void;
  onUnassignUser: (userId: number) => void;
  onOpenDetail: (d: Detail) => void;
  onAutofill: () => void;
  autoFillBusy: boolean;
  undoRunId: string | null;
  onUndoAutofill: () => void;
  onToggleRelease: () => void;
  onEnlistedDrop: (raw: string) => void;
  t: (k: TKey) => string;
  lang: Lang;
}

function EscalationBoard(p: BoardProps) {
  const { data, canManage, t } = p;
  const viewportRef = useRef<HTMLDivElement>(null);

  // Edge-driven horizontal autoscroll enquanto arrasta sobre a faixa de
  // parties. O scroll nativo por elemento não leva o cursor além da borda
  // direita/esquerda do viewport; sem isso, soltar um jogador numa party
  // distante exige rolar manualmente antes de começar a arrastar.
  const autoScrollRaf = useRef(0);
  const dragOverX = useRef<number | null>(null);
  const startEdgeScroll = (dir: number) => {
    cancelAnimationFrame(autoScrollRaf.current);
    const tick = () => {
      const vp = viewportRef.current;
      if (!vp || dragOverX.current === null) return;
      vp.scrollLeft += dir * 12;
      autoScrollRaf.current = requestAnimationFrame(tick);
    };
    autoScrollRaf.current = requestAnimationFrame(tick);
  };
  const stopEdgeScroll = () => { cancelAnimationFrame(autoScrollRaf.current); };

  return (
    <div className="esc-page-wrap">
      <div className={"esc-board-layout" + (canManage ? "" : " esc-board-readonly")}>
        <SignupRail
          data={data}
          weaponItemId={p.weaponItemId}
          assignedUserIds={new Set(data.assignments.map(a => a.user_id))}
          assignedRoleRender={p.assignedRoleRender}
          canManage={canManage}
          autoFillBusy={p.autoFillBusy}
          undoRunId={p.undoRunId}
          onAutofill={p.onAutofill}
          onUndoAutofill={p.onUndoAutofill}
          onToggleRelease={p.onToggleRelease}
          t={t}
          lang={p.lang}
          onDrop={(raw) => p.onEnlistedDrop(raw)}
        />
        <div className="esc-board-col">
          <div
            className="esc-board-viewport"
            ref={viewportRef}
          onDragOver={(e) => {
            // Só rola nas bordas se houver drag ativo (dataTransfer pode lançar
            // em alguns browsers quando não é drag válido — guardamos só o X).
            if (!canManage) return;
            e.preventDefault();
            const vp = viewportRef.current!;
            const r = vp.getBoundingClientRect();
            const x = e.clientX - r.left;
            dragOverX.current = x;
            const EDGE = 48;
            if (x < EDGE && vp.scrollLeft > 0) startEdgeScroll(-1);
            else if (x > r.width - EDGE && vp.scrollLeft < vp.scrollWidth - vp.clientWidth) startEdgeScroll(1);
            else stopEdgeScroll();
          }}
          onDragLeave={(e) => {
            // Só para ao sair da viewport inteira; cruzar entre slots também
            // dispara dragleave e interromperia o auto-scroll na borda.
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
              dragOverX.current = null;
              stopEdgeScroll();
            }
          }}
          onDrop={() => { dragOverX.current = null; stopEdgeScroll(); }}
          onDragEnd={() => { dragOverX.current = null; stopEdgeScroll(); }}
        >
          <div className="esc-board-strip">
            {data.parties.map((party, pi) => (
              <PartyColumn
                key={party.id}
                party={party}
                index={pi}
                assignedBySlot={p.assignedBySlot}
                canManage={canManage}
                mySlotId={p.mySlotId}
                candidatesBySlot={p.candidatesBySlot}
                popoverSlot={p.popoverSlot}
                setPopoverSlot={p.setPopoverSlot}
                onDropSlot={p.onDropSlot}
                onPickCandidate={p.onPickCandidate}
                onUnassignUser={p.onUnassignUser}
                onOpenDetail={p.onOpenDetail}
                t={t}
                lang={p.lang}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
    </div>
  );
}

// ── Rail de inscritos (manager) — colado à esquerda, fora do scroll horizontal.
// Cabeça do rail carrega o menu do evento (título, comp · estado, autofill,
// release) — morava numa barra própria acima do board; agora fica limitado ao
// quadrante da lista de inscritos.
// Mostra nome + até 5 renders de arma (não chips de texto). Extras viram uma
// reticência discreta (não +N). Clique expande/recolapse a lista completa.
// Identidade por par (weapon_id, fn): armas iguais com fn diferente (Longbow
// DPS vs Longbow Support) são renders distintos só quando a fn desambigua —
// o title/aria-label carrega o nome da role concreta. Escalados sempre verdes.
function SignupRail({
  data, weaponItemId, assignedUserIds, assignedRoleRender, canManage, autoFillBusy, undoRunId,
  onAutofill, onUndoAutofill, onToggleRelease, t, lang, onDrop,
}: {
  data: EscalationOut;
  weaponItemId: Map<number, string>;
  assignedUserIds: Set<number>;
  assignedRoleRender: Map<number, string>;
  canManage: boolean;
  autoFillBusy: boolean;
  undoRunId: string | null;
  onAutofill: () => void;
  onUndoAutofill: () => void;
  onToggleRelease: () => void;
  t: (k: TKey) => string;
  lang: Lang;
  onDrop: (raw: string) => void;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // Fn types da guilda (com emojis) — carregados uma vez. Usados no hover
  // expand pra mostrar o emote do tipo de função sobre o render da arma.
  const [fnTypes, setFnTypes] = useState<FnTypeDef[]>(DEFAULT_FN_TYPES);
  useEffect(() => {
    api.getCompFnTypes()
      .then(({ fn_types }) => {
        if (fn_types && fn_types.length > 0) setFnTypes(fn_types);
      })
      .catch(() => {});
  }, []);
  // Ordenação: não escalados primeiro, depois escalados (verdes) — sem amarelo.
  const sorted = [...data.enlisted].sort((a, b) => {
    const pa = assignedUserIds.has(a.user_id) ? 1 : 0;
    const pb = assignedUserIds.has(b.user_id) ? 1 : 0;
    return pa - pb;
  });
  // Assignments órfãos (escalados sem signup) — liberam o slot, ficam no fim.
  const enlistedIds = new Set(data.enlisted.map(s => s.user_id));
  const withdrawn = data.assignments
    .filter(a => !enlistedIds.has(a.user_id))
    .filter((a, i, arr) => arr.findIndex(x => x.user_id === a.user_id) === i);

  const toggle = (uid: number) => setExpanded(prev => {
    const n = new Set(prev); n.has(uid) ? n.delete(uid) : n.add(uid); return n;
  });

  return (
    <aside
      className="esc-rail"
      onDragOver={canManage ? (e) => e.preventDefault() : undefined}
      onDrop={canManage ? (e) => onDrop(e.dataTransfer.getData("text/plain")) : undefined}
    >
      <div className="esc-rail-event">
        <div className="esc-rail-event-title">{data.event.title || t("escTitle")}</div>
        <div className="hint esc-rail-event-sub">{data.event.comp_name || "—"} · {data.event.state}</div>
        {canManage && (
          <div className="esc-rail-event-actions">
            <button
              className="btn esc-autofill"
              disabled={autoFillBusy}
              onClick={onAutofill}
              title={t("escAutofill")}
            >
              <i className="ti ti-wand" />
              {t("escAutofill")}
            </button>
            {undoRunId && (
              <button className="btn esc-rail-event-btn" disabled={autoFillBusy} onClick={onUndoAutofill}>
                <i className="ti ti-arrow-back-up" /> {t("escAutofillUndo")}
              </button>
            )}
            {["scheduled", "in_progress"].includes(data.event.state) && (
              <button
                className={"btn esc-gatelock" + (data.event.functions_released ? " esc-gatelock-on" : "")}
                onClick={onToggleRelease}
                title={data.event.functions_released ? t("functionsReleasedOn") : t("releaseFunctionsBtn")}
              >
                <i className={"ti " + (data.event.functions_released ? "ti-lock-open" : "ti-lock")} />
                {data.event.functions_released ? t("functionsReleasedOn") : t("releaseFunctionsBtn")}
              </button>
            )}
          </div>
        )}
      </div>
      <div className="esc-rail-head">
        <i className="ti ti-list-check" /> {t("escEnlisted")} <span className="esc-count">{data.enlisted.length}</span>
      </div>
      <div className="esc-rail-list">
        {data.enlisted.length === 0 && withdrawn.length === 0 && <div className="hint esc-empty">{t("escEmpty")}</div>}
        {sorted.map(s => {
          const placed = assignedUserIds.has(s.user_id);
          const opts = signupWeaponOptions(s, weaponItemId, lang);
          const isExpanded = expanded.has(s.user_id);
          const shown = isExpanded ? opts : opts.slice(0, 5);
          const extra = opts.length - shown.length;
          const draggable = !placed;
          return (
            <div
              // O estado entra no key de propósito: ao ser desalocado (ou
              // alocado), o card remonta e a animação CSS de entrada roda —
              // é o "reaparecer" no pool de inscritos, sem timer nem JS.
              key={`${s.user_id}:${placed ? "p" : "u"}`}
              className={"esc-rail-card" + (placed ? " esc-rail-placed" : "")}
              draggable={canManage && !placed}
              onDragStart={(e) => { if (canManage && draggable) { e.dataTransfer.setData("text/plain", dragPayload("enlisted", s.user_id)); e.dataTransfer.effectAllowed = "move"; } }}
              onClick={canManage && opts.length > 5 ? () => toggle(s.user_id) : undefined}
              title={placed ? t("escUnassignDblClick") : (canManage && draggable ? t("escDragHint") : "")}
              onDoubleClick={canManage && placed ? () => onDrop(dragPayload("slot", s.user_id)) : undefined}
            >
              {draggable && <i className="ti ti-grip-horizontal esc-rail-grip" />}
              {placed && assignedRoleRender.has(s.user_id) && (
                <img
                  className="esc-rail-placedrender"
                  src={itemRenderUrl(assignedRoleRender.get(s.user_id)!, 1)}
                  title={t("escPlacedRole")}
                  alt={t("escPlacedRole")}
                />
              )}
              <span className="esc-rail-name">{s.user_name || String(s.user_id)}</span>
              <div className="esc-rail-weapons">
                {shown.map((o, i) => (
                  <img
                    key={i}
                    className="esc-rail-weapon"
                    src={itemRenderUrl(o.itemId, 1)}
                    title={o.label}
                    alt={o.label}
                    aria-label={o.label}
                  />
                ))}
                {extra > 0 && <span className="esc-rail-more" title={t("escMoreWeapons")}>…</span>}
              </div>
              {opts.length > 0 && (
                <div className="esc-rail-hover">
                  {opts.map((o, i) => {
                    const fnDef = getFnDef(o.fnKey, fnTypes);
                    return (
                      <div key={i} className="esc-rail-hover-render">
                        <img
                          src={itemRenderUrl(o.itemId, 1, 256)}
                          alt="" aria-hidden="true"
                        />
                        {fnDef?.emoji && (
                          <span className="esc-rail-hover-emoji">{fnDef.emoji}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
        {withdrawn.map(a => (
          <div
            key={`w-${a.user_id}`}
            className="esc-rail-card esc-rail-withdrawn"
            onDoubleClick={canManage ? () => onDrop(dragPayload("slot", a.user_id)) : undefined}
            title={t("escWithdrewHint")}
          >
            <span className="esc-rail-name">{a.user_name || String(a.user_id)}</span>
            <span className="badge esc-signup-wbadge">{t("escWithdrew")}</span>
          </div>
        ))}
      </div>
      <div className="esc-rail-ad">
        <AdBanner slot="escalacao-rail" variant="mediumRectangle" />
        <AffiliateBanner variant="rectangle" />
      </div>
    </aside>
  );
}

// Opções de arma de um signup — uma por par (weapon_id, fn). Mesma arma com
// fn diferente vira duas entradas (Longbow DPS e Longbow Support). O label
// carrega o nome localizado da arma + fn pra desambiguar pares de mesma arma.
// `fnKey` é a chave normalizada do fn (casefold/strip) pra casar com FnTypeDef.
function signupWeaponOptions(s: EscalationSignup, weaponItemId: Map<number, string>, lang: Lang): { itemId: string; label: string; fnKey: string }[] {
  const out: { itemId: string; label: string; fnKey: string }[] = [];
  const seen = new Set<string>();
  for (const wf of s.weapon_fns ?? []) {
    if (wf.weapon_id == null) continue;
    const itemId = (wf as any).item_id as string | undefined ?? weaponItemId.get(wf.weapon_id);
    if (!itemId) continue;
    const key = pairKey(wf.weapon_id, wf.fn);
    if (seen.has(key)) continue;
    seen.add(key);
    const item = ITEM_BY_ID.get(itemId);
    const weaponLabel = item ? itemLocalName(item, lang) : ((wf as any).weapon_name ?? itemId);
    const fnLabel = wf.fn || "—";
    const label = `${weaponLabel} · ${fnLabel}`;
    out.push({ itemId, label, fnKey: fnKey(wf.fn) });
  }
  // Fallback legado: sem weapon_fns (evento finalizado sem backfill). Sem
  // como mapear nome->weapon_id confiável aqui, ficamos sem renders e o nome
  // da role aparece só no title do card. ponytail: cobertura parcial é melhor
  // que render errado.
  return out;
}

// ── Coluna de party: slots verticais + célula final "PARTY N" (metade da
// altura de um slot) que limita a lista — a borda direita da coluna para
// ali em vez de esticar até o fim da faixa. Nunca quebra em grid.
function PartyColumn(p: {
  party: EscalationOut["parties"][number];
  index: number;
  assignedBySlot: Map<number, EscalationOut["assignments"][number]>;
  canManage: boolean;
  mySlotId: number | null;
  candidatesBySlot: Map<number, EscalationSignup[]>;
  popoverSlot: number | null;
  setPopoverSlot: (id: number | null) => void;
  onDropSlot: (slotId: number, e: React.DragEvent) => void;
  onPickCandidate: (slotId: number, userId: number) => void;
  onUnassignUser: (userId: number) => void;
  onOpenDetail: (d: Detail) => void;
  t: (k: TKey) => string;
  lang: Lang;
}) {
  const { party, index, t } = p;
  return (
    <section className="esc-party">
      <div className="esc-party-slots">
        {party.slots.map(slot => (
          <SlotRow
            key={slot.id}
            slot={slot}
            isMine={p.mySlotId === slot.id}
            assignedBySlot={p.assignedBySlot}
            canManage={p.canManage}
            candidatesBySlot={p.candidatesBySlot}
            popoverSlot={p.popoverSlot}
            setPopoverSlot={p.setPopoverSlot}
            onDropSlot={p.onDropSlot}
            onPickCandidate={p.onPickCandidate}
            onUnassignUser={p.onUnassignUser}
            onOpenDetail={p.onOpenDetail}
            t={t}
            lang={p.lang}
          />
        ))}
      </div>
      <div className="esc-party-foot">{t("escPartyLabel")} {index + 1}</div>
    </section>
  );
}

// ── Slot: render da arma E bracket do jogador NA MESMA LINHA. Slot flex vazio
// sem ninguém escalado mostra cluster compacto dos renders de todas as roles.
function SlotRow(p: {
  slot: EscalationSlot;
  isMine: boolean;
  assignedBySlot: Map<number, EscalationOut["assignments"][number]>;
  canManage: boolean;
  candidatesBySlot: Map<number, EscalationSignup[]>;
  popoverSlot: number | null;
  setPopoverSlot: (id: number | null) => void;
  onDropSlot: (slotId: number, e: React.DragEvent) => void;
  onPickCandidate: (slotId: number, userId: number) => void;
  onUnassignUser: (userId: number) => void;
  onOpenDetail: (d: Detail) => void;
  t: (k: TKey) => string;
  lang: Lang;
}) {
  const { slot, isMine, t, lang } = p;
  const a = p.assignedBySlot.get(slot.id);
  const chosen = a?.game_role_id ? slot.roles.find(r => r.id === a.game_role_id) : undefined;
  // Slot de 1 role: previewRole é a única role. Slot flex: chosen (depois de
  // atribuído) ou todas (vazio, mostra cluster compacto).
  const isFlex = slot.roles.length > 1;
  const previewRole = slot.roles.length === 1 ? slot.roles[0] : chosen;
  const dropProps = p.canManage
    ? { onDragOver: (e: React.DragEvent) => e.preventDefault(), onDrop: (e: React.DragEvent) => p.onDropSlot(slot.id, e) }
    : {};

  // Arma: a do chosen (se escalado), senão a da role única, senão cluster das
  // roles do flex vazio. Cluster compacto = só os renders das armas, lado a lado.
  const weaponImgs = chosen
    ? [weaponOf(chosen)].filter(Boolean)
    : isFlex && !chosen
      ? slot.roles.map(r => weaponOf(r)).filter(Boolean)
      : [weaponOf(previewRole)].filter(Boolean);

  const onBracketClick = () => {
    if (!p.canManage && a && chosen) {
      p.onOpenDetail({ userName: a.user_name, role: chosen });
    }
  };
  // Read-only: clicar na célula da função (slot) abre o modal de detalhe da
  // role — mesmo sem ninguém escalado, mostra a build da função.
  const onSlotClick = () => {
    if (!p.canManage) {
      const role = chosen ?? previewRole ?? slot.roles[0];
      if (role) {
        p.onOpenDetail({ userName: a?.user_name ?? null, role });
      }
    }
  };

  return (
    <div className={"esc-slot" + (a ? " esc-slot-filled" : "") + (isMine ? " esc-slot-mine" : "") + (!p.canManage ? " esc-slot-readonly" : "")} {...dropProps} onClick={!p.canManage ? onSlotClick : undefined} style={!p.canManage ? { cursor: "pointer" } : undefined}>
      <span className="esc-slot-weapons">
        {weaponImgs.map((w, i) => {
          const role = chosen ?? previewRole ?? slot.roles[i];
          const wi = w as RegearItem;
          const title = role ? role.name : localName(wi, lang);
          return (
            <img
              key={i}
              className="esc-slot-weapon"
              src={itemRenderUrl(wi.item_id, wi.quality || 1)}
              title={title}
              alt={title}
            />
          );
        })}
        {weaponImgs.length === 0 && <span className="esc-slot-empty-weapons">{NA}</span>}
      </span>
      <span className="esc-slot-player">
        {a ? (
          <button
            type="button"
            className="esc-bracket"
            draggable={p.canManage}
            onDragStart={(e) => { if (p.canManage) { e.dataTransfer.setData("text/plain", dragPayload("slot", a.user_id)); e.dataTransfer.effectAllowed = "move"; } }}
            onClick={p.canManage ? undefined : onBracketClick}
            onDoubleClick={p.canManage ? () => p.onUnassignUser(a.user_id) : undefined}
            title={p.canManage ? t("escDragMove") : ""}
          >
            <i className="ti ti-grip-horizontal esc-bracket-grip" />
            <span className="esc-bracket-name">{a.user_name || String(a.user_id)}</span>
          </button>
        ) : p.canManage ? (
          <SlotCandidatePicker
            candidates={p.candidatesBySlot.get(slot.id) ?? []}
            open={p.popoverSlot === slot.id}
            onToggle={() => p.setPopoverSlot(p.popoverSlot === slot.id ? null : slot.id)}
            onPick={(uid) => p.onPickCandidate(slot.id, uid)}
            dropHint={t("escDropHint")}
          />
        ) : (
          <span className="esc-bracket esc-bracket-empty">{NA}</span>
        )}
      </span>
    </div>
  );
}

// ── Modal de detalhe: jogador + role concreta + build completa + swaps +
// skills + estilo + notas. Manager e read-only abrem igual.
function DetailModal({ detail, t, lang, onClose }: { detail: Detail; t: (k: TKey) => string; lang: Lang; onClose: () => void }) {
  const { userName, role } = detail;
  const equip = buildItemsToEquip(role.build_items ?? []);
  const weaponIs2H = is2H(equip.weapon?.id);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Lista de itens da build principal com seus swaps e skills
  const buildSlots = ["weapon", "offhand", "helmet", "armor", "boots", "cape", "food", "potion"];
  const slotItems = buildSlots
    .map(slot => {
      const primary = role.build_items?.find(bi => bi.slot === slot);
      if (!primary) return null;
      const swaps = (role.build_items ?? []).filter(bi => bi.slot.startsWith(`${slot}_alt_`));
      const gearIds = Object.entries(role.gear_spells ?? {})
        .filter(([k]) => k.startsWith(`${slot}_`) && !k.startsWith(`${slot}_alt_`))
        .map(([, v]) => v)
        .filter(Boolean) as string[];
      // weapon tem Q/W/passive em vez de gear_spells
      const weaponSpells = slot === "weapon"
        ? [role.q_spell, role.w_spell, role.passive_spell].filter(Boolean) as string[]
        : [];
      const allSpells = slot === "weapon" ? weaponSpells : gearIds;
      return { slot, primary, swaps, allSpells };
    })
    .filter(Boolean) as { slot: string; primary: RegearItem; swaps: RegearItem[]; allSpells: string[] }[];

  // Filtra offhand se for 2H
  const visibleSlots = slotItems.filter(s => !(s.slot === "offhand" && weaponIs2H));

  return (
    <div className="esc-modal-overlay" onClick={onClose}>
      <div className="card esc-modal-card esc-detail-card" onClick={e => e.stopPropagation()}>
        <div className="esc-detail-head">
          <div className="esc-detail-id">
            <span className="esc-detail-label">{t("escDetailPlayer")}</span>
            <span className="esc-detail-value">{userName || "—"}</span>
          </div>
          <div className="esc-detail-id">
            <span className="esc-detail-label">{t("escDetailRole")}</span>
            <span className="esc-detail-value" style={{ color: role.color || "var(--text)" }}>{role.name}</span>
          </div>
          <button className="btn esc-detail-close" onClick={onClose}><i className="ti ti-x" /></button>
        </div>
        <div className="esc-detail-body">
          <div className="esc-detail-main">
            <div className="cs-detail-label">{t("escDetailBuild")}</div>
            <div className="esc-detail-equipgrid-wrap">
              <EquipGrid
                equip={equip}
                weaponIs2H={weaponIs2H}
                selectedQ={role.q_spell}
                selectedW={role.w_spell}
                selectedPassive={role.passive_spell}
                gearSpells={role.gear_spells}
                potionQty={role.build_items?.find(bi => bi.slot === "potion")?.quantity ?? 10}
                foodQty={role.build_items?.find(bi => bi.slot === "food")?.quantity ?? 1}
              />
            </div>
          </div>
          <div className="esc-detail-items-col">
            <div className="cs-detail-label">{t("escDetailItems")}</div>
            <div className="esc-detail-items-list">
              {visibleSlots.map(({ slot: slotName, primary, swaps, allSpells }) => {
                const name = localName(primary, lang);
                const hasSwaps = swaps.length > 0;
                return (
                  <div key={slotName} className={"esc-detail-item-row" + (hasSwaps ? " esc-detail-item-has-swaps" : "")}>
                    <img className="esc-detail-item-render" src={itemUrl(primary.item_id, primary.quality || 1)} alt={name} title={name} />
                    <div className="esc-detail-item-info">
                      <div className="esc-detail-item-name">
                        <span className="esc-detail-item-label">{name}</span>
                        {allSpells.length > 0 && (
                          <div className="esc-detail-item-spells">
                            {allSpells.map(sid => (
                              <img key={sid} src={spellUrl(sid)} alt="" className="esc-detail-spell-img" onError={e => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden"; }} />
                            ))}
                          </div>
                        )}
                      </div>
                      {hasSwaps && (
                        <div className="esc-detail-item-swaps">
                          {swaps.map((sw, j) => {
                            const altIdx = parseInt(sw.slot.match(/_alt_(\d+)$/)?.[1] ?? "0", 10);
                            const altGearIds = Object.entries(role.gear_spells ?? {})
                              .filter(([k]) => k.startsWith(`${slotName}_alt_${altIdx}_`))
                              .map(([, v]) => v)
                              .filter(Boolean) as string[];
                            const swName = localName(sw, lang);
                            return (
                              <div key={sw.item_id + j} className="esc-detail-swap-inline">
                                <img src={itemUrl(sw.item_id, sw.quality || 1)} alt={swName} title={swName} className="esc-detail-swap-render" />
                                <span className="esc-detail-swap-name">{swName}</span>
                                {altGearIds.length > 0 && (
                                  <div className="esc-detail-swap-spells">
                                    {altGearIds.map(sid => (
                                      <img key={sid} src={spellUrl(sid)} alt="" className="esc-detail-spell-img" onError={e => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden"; }} />
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
        {role.play_style && (
          <div className="esc-detail-footer">
            <span className="cs-detail-label">{t("escDetailStyle")}</span>
            <span className="esc-detail-text">{role.play_style}</span>
          </div>
        )}
        {role.obs && (
          <div className="esc-detail-footer">
            <span className="cs-detail-label">{t("escDetailObs")}</span>
            <span className="esc-detail-text">{role.obs}</span>
          </div>
        )}
      </div>
    </div>
  );
}

// Popover de candidatos por slot: clicar no slot vazio abre a lista de inscritos
// compatíveis. Clique num candidato atribui direto (slot de 1 role) ou abre o
// modal de escolha (slot flex). Fecha ao clicar fora, ESC ou ao escolher.
// ponytail: overlay simples com stopPropagation — sem portal, sem lib.
function SlotCandidatePicker({
  candidates, open, onToggle, onPick, dropHint,
}: {
  candidates: EscalationSignup[];
  open: boolean;
  onToggle: () => void;
  onPick: (userId: number) => void;
  dropHint: string;
}) {
  const btnRef = useRef<HTMLButtonElement>(null);
  // Coords do menu em position:fixed — escapa de qualquer overflow ancestral
  // (party-slots, board-viewport) que cortava o menu. Só renderiza com coords.
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  useEffect(() => {
    if (!open) { setMenuPos(null); return; }
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const MW = 280, MH = 280;
    let left = r.left;
    if (left + MW > window.innerWidth - 8) left = Math.max(8, window.innerWidth - MW - 8);
    let top = r.bottom + 4;
    if (top + MH > window.innerHeight - 8) top = Math.max(8, r.top - 4 - MH);
    setMenuPos({ top, left });
  }, [open]);
  // Rolar a faixa horizontal ou redimensionar desloca o botão: fecha o menu em
  // vez de tentar reposicionar (menu é curto, reabrir é barato).
  useEffect(() => {
    if (!open) return;
    const close = () => onToggle();
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => { window.removeEventListener("scroll", close, true); window.removeEventListener("resize", close); };
  }, [open, onToggle]);
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
        ref={btnRef}
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
      {open && menuPos && (
        <>
          <div className="cs-slot-pick-overlay" onClick={onToggle} />
          <div className="cs-slot-pick-menu" style={{ position: "fixed", top: menuPos.top, left: menuPos.left }} onClick={(e) => e.stopPropagation()}>
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
