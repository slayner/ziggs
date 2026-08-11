import { useEffect, useState } from "react";
import { api, type Permissions } from "../../api";
import { useT } from "../../i18n";
import { compToDraft, decodeCompCode, encodeCompCode } from "./helpers";
import type { CompCode, Draft } from "./types";

// Lista de comps — extraído de CompBuilder.tsx (WS6 fase 1). Carrega o draft
// completo ANTES de avisar o pai (onOpen), pra manter o comportamento
// original de não trocar de tela até os dados estarem prontos (sem flash de
// "carregando" no editor).
//
// Remodelação UX (jul/2026): grid de cards em vez de lista linear. Cada card
// mostra nome + contagens (parties/roles) carregadas em paralelo, e a criação
// vira um card próprio no grid. Busca por nome filtra em memória.
type CompStats = { parties: number; roles: number };
type StatsState = Record<number, CompStats | null | undefined>;

export function CompList({ perms, compList, loadError, setCompList, onOpen }: {
  perms: Permissions;
  compList: { id: number; name: string }[] | null;
  loadError: boolean;
  setCompList: React.Dispatch<React.SetStateAction<{ id: number; name: string }[] | null>>;
  onOpen: (id: number, draft: Draft, startEditing: boolean, importCode?: CompCode | null) => void;
}) {
  const t = useT();
  const [creatingComp, setCreatingComp] = useState(false);
  const [importMode, setImportMode] = useState(false);
  const [newCompName, setNewCompName] = useState("");
  const [deletingCompId, setDeletingCompId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copyingId, setCopyingId] = useState<number | null>(null);
  const [codeMsg, setCodeMsg] = useState<string | null>(null);
  const [stats, setStats] = useState<StatsState>({});
  const [query, setQuery] = useState("");

  function flashCodeMsg(msg: string) {
    setCodeMsg(msg);
    setTimeout(() => setCodeMsg(null), 1500);
  }

  // Carrega contagens (parties/roles) de todas as comps da lista em paralelo,
  // só pra exibição no card. Não bloqueia a abertura (openComp busca o draft
  // completo na hora do clique). Roda quando a lista muda.
  useEffect(() => {
    if (!compList) return;
    for (const c of compList) {
      if (stats[c.id] !== undefined) continue;
      setStats(prev => ({ ...prev, [c.id]: null }));
      api.getComp(c.id)
        .then(full => {
          const parties = full.parties.length;
          const roles = full.parties.reduce((n, p) => n + p.slots.length, 0);
          setStats(prev => ({ ...prev, [c.id]: { parties, roles } }));
        })
        .catch(() => setStats(prev => ({ ...prev, [c.id]: undefined })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compList]);

  // Copia o código da comp INTEIRA (todas as parties/slots/roles) — não uma
  // build isolada. O código de uma build individual continua no menu de
  // flex (copiar slot existente).
  async function copyCompCodeFn(id: number) {
    setCopyingId(id);
    try {
      const c = await api.getComp(id);
      await navigator.clipboard.writeText(encodeCompCode(compToDraft(c)));
      flashCodeMsg(t("compCodeCopied"));
    } catch {
      flashCodeMsg(t("loadCompError"));
    } finally {
      setCopyingId(null);
    }
  }

  // Importar código ao criar: cola a comp INTEIRA colada na área de
  // transferência como ponto de partida da comp nova (ainda precisa Salvar
  // no editor pra persistir de verdade).
  async function importCompFn() {
    if (!newCompName.trim()) return;
    let text: string;
    try {
      text = await navigator.clipboard.readText();
    } catch {
      flashCodeMsg(t("buildCodePasteFail"));
      return;
    }
    const code = decodeCompCode(text);
    if (!code) {
      flashCodeMsg(t("buildCodeInvalid"));
      return;
    }
    try {
      const c = await api.createComp({ name: newCompName.trim() });
      setCompList(prev => [...(prev ?? []), { id: c.id, name: c.name }]);
      setCreatingComp(false); setImportMode(false); setNewCompName("");
      onOpen(c.id, compToDraft(c), true, code);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("createCompError"));
    }
  }

  async function openComp(id: number, startEditing: boolean) {
    try {
      const c = await api.getComp(id);
      onOpen(id, compToDraft(c), startEditing);
    } catch {
      setError(t("loadCompError"));
    }
  }

  async function createCompFn() {
    if (!newCompName.trim()) return;
    try {
      const c = await api.createComp({ name: newCompName.trim() });
      setCompList(prev => [...(prev ?? []), { id: c.id, name: c.name }]);
      setCreatingComp(false); setImportMode(false); setNewCompName("");
      await openComp(c.id, true);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("createCompError"));
    }
  }

  async function deleteCompFn(id: number) {
    try {
      await api.deleteComp(id);
      setCompList(prev => prev?.filter(c => c.id !== id) ?? prev);
      setDeletingCompId(null);
      setStats(prev => { const n = { ...prev }; delete n[id]; return n; });
    } catch (e) {
      setError(e instanceof Error ? e.message : t("deleteCompError"));
    }
  }

  const filtered = (compList ?? []).filter(c =>
    c.name.toLowerCase().includes(query.trim().toLowerCase())
  );
  const canCreate = !!perms["comps.create"];

  return (
    <div className="container comp-list-wrap">
      {/* Hero editorial — título da seção + busca + criação */}
      <div className="comp-list-hero">
        <div className="comp-list-hero-text">
          <small>{t("compListKicker")}</small>
          <h1>{t("compsTitle")}</h1>
          <p>{t("compListSub")}</p>
        </div>
        <div className="comp-list-hero-tools">
          <div className="item-picker-field comp-list-search">
            <i className="ti ti-search" aria-hidden style={{ opacity: 0.5, fontSize: 13 }} />
            <input className="item-picker-input"
              placeholder={t("compSearchPlaceholder")}
              value={query}
              onChange={e => setQuery(e.target.value)} />
          </div>
          {loadError && <span className="badge" style={{ color: "#e07a7a" }}>{t("loadCompError")}</span>}
        </div>
      </div>

      {codeMsg && <p className="comp-list-msg">{codeMsg}</p>}
      {error && <p className="comp-list-msg err">{error}</p>}

      {!compList && <p className="muted comp-list-loading">{t("loading")}</p>}

      {compList && (
        <div className="comp-grid">
          {/* Cards de criação — só aparecem quando não há busca ativa filtrando */}
          {canCreate && !query.trim() && (
            <>
              <button className="comp-card comp-card-create" onClick={() => { setCreatingComp(p => !p); setImportMode(false); setError(null); }}>
                <span className="comp-card-create-icon"><i className="ti ti-plus" aria-hidden /></span>
                <span className="comp-card-name">{t("compCreateCardTitle")}</span>
                <span className="comp-card-sub">{t("compCreateCardDesc")}</span>
              </button>
              <button className="comp-card comp-card-create" onClick={() => { setCreatingComp(true); setImportMode(true); setError(null); }}>
                <span className="comp-card-create-icon"><i className="ti ti-clipboard" aria-hidden /></span>
                <span className="comp-card-name">{t("compImportCardTitle")}</span>
                <span className="comp-card-sub">{t("compImportCardDesc")}</span>
              </button>
            </>
          )}

          {filtered.map(c => {
            const s = stats[c.id];
            const deleting = deletingCompId === c.id;
            return (
              <div key={c.id} className={"comp-card" + (deleting ? " comp-card-deleting" : "")}>
                <button className="comp-card-main"
                  onClick={() => { setDeletingCompId(null); openComp(c.id, false); }}>
                  <div className="comp-card-icon"><i className="ti ti-layout-list" aria-hidden /></div>
                  <span className="comp-card-name">{c.name}</span>
                  <div className="comp-card-stats">
                    {!s
                      ? <span className="comp-card-stat muted">{t("compLoadingStats")}</span>
                      : <>
                          <span className="comp-card-stat">
                            <i className="ti ti-layout-grid" aria-hidden />
                            {s.parties} {s.parties === 1 ? t("compPartyCountShort") : t("compPartyCountPlural")}
                          </span>
                          <span className="comp-card-stat">
                            <i className="ti ti-users" aria-hidden />
                            {s.roles} {s.roles === 1 ? t("compRoleCountShort") : t("compRoleCountPlural")}
                          </span>
                        </>
                    }
                  </div>
                </button>
                <div className="comp-card-actions">
                  <button className="cs-xbtn" title={t("copyCompCodeBtn")} disabled={copyingId === c.id}
                    onClick={e => { e.stopPropagation(); copyCompCodeFn(c.id); }}>
                    <i className="ti ti-copy" aria-hidden />
                  </button>
                  {perms["comps.manage"] && (
                    deleting ? (
                      <div className="comp-card-confirm">
                        <button className="cs-xbtn danger-act"
                          onClick={e => { e.stopPropagation(); deleteCompFn(c.id); }}>
                          <i className="ti ti-check" aria-hidden />
                        </button>
                        <button className="cs-xbtn"
                          onClick={e => { e.stopPropagation(); setDeletingCompId(null); }}>
                          <i className="ti ti-x" aria-hidden />
                        </button>
                      </div>
                    ) : (
                      <button className="cs-xbtn" title={t("deleteCompTitle")}
                        onClick={e => { e.stopPropagation(); setDeletingCompId(c.id); }}>
                        <i className="ti ti-trash" aria-hidden />
                      </button>
                    )
                  )}
                </div>
              </div>
            );
          })}

          {compList.length === 0 && !(canCreate && !query.trim()) && (
            <div className="comp-list-empty">
              <i className="ti ti-layout-list" aria-hidden />
              <h3>{t("compEmptyTitle")}</h3>
              <p>{t("compEmptyDesc")}</p>
            </div>
          )}
          {compList.length > 0 && filtered.length === 0 && (
            <p className="muted comp-list-loading">{t("noCompsYet")}</p>
          )}
        </div>
      )}

      {/* Modal de criação — sobreposto ao grid, mantém o fluxo antigo
          (nome + importar código) */}
      {creatingComp && (
        <div className="comp-create-modal" onClick={e => {
          if (e.target === e.currentTarget) { setCreatingComp(false); setImportMode(false); setNewCompName(""); }
        }}>
          <div className="comp-create-modal-card" onClick={e => e.stopPropagation()}>
            <div className="comp-create-modal-head">
              <span className="comp-create-modal-title">
                {importMode ? t("compImportCardTitle") : t("compCreateCardTitle")}
              </span>
              <button className="cs-xbtn" onClick={() => { setCreatingComp(false); setImportMode(false); setNewCompName(""); }}>
                <i className="ti ti-x" aria-hidden />
              </button>
            </div>
            <input className="input comp-create-modal-input"
              placeholder={t("compNamePlaceholder")}
              value={newCompName}
              autoFocus
              onChange={e => setNewCompName(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") importMode ? importCompFn() : createCompFn();
                if (e.key === "Escape") { setCreatingComp(false); setImportMode(false); setNewCompName(""); }
              }} />
            <div className="comp-create-modal-actions">
              {importMode ? (
                <button className="btn primary" onClick={importCompFn} disabled={!newCompName.trim()}>
                  <i className="ti ti-clipboard" aria-hidden /> {t("importCompCodeBtn")}
                </button>
              ) : (
                <button className="btn primary" onClick={createCompFn} disabled={!newCompName.trim()}>
                  {t("createBtn")}
                </button>
              )}
              <button className="btn" onClick={() => { setCreatingComp(false); setImportMode(false); setNewCompName(""); }}>
                {t("cancel")}
              </button>
              {!importMode && (
                <button className="btn" onClick={() => setImportMode(true)} disabled={!newCompName.trim()}
                  title={t("importCompCodeHint")}>
                  <i className="ti ti-clipboard" aria-hidden /> {t("importCompCodeBtn")}
                </button>
              )}
            </div>
            {importMode && (
              <p className="comp-create-modal-hint">{t("importCompCodeHint")}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}