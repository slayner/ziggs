import { useState } from "react";
import { api, type Permissions } from "../../api";
import { useLang, useT } from "../../i18n";
import { mockApiComp } from "../../mock";
import { compToDraft, decodeCompCode, encodeCompCode } from "./helpers";
import type { CompCode, Draft } from "./types";

// Lista de comps — extraído de CompBuilder.tsx (WS6 fase 1). Carrega o draft
// completo ANTES de avisar o pai (onOpen), pra manter o comportamento
// original de não trocar de tela até os dados estarem prontos (sem flash de
// "carregando" no editor).
export function CompList({ perms, offline, compList, setCompList, onOpen }: {
  perms: Permissions;
  offline: boolean;
  compList: { id: number; name: string }[] | null;
  setCompList: React.Dispatch<React.SetStateAction<{ id: number; name: string }[] | null>>;
  onOpen: (id: number, draft: Draft, startEditing: boolean, importCode?: CompCode | null) => void;
}) {
  const t = useT();
  const { lang } = useLang();
  const [creatingComp, setCreatingComp] = useState(false);
  const [newCompName, setNewCompName] = useState("");
  const [deletingCompId, setDeletingCompId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copyingId, setCopyingId] = useState<number | null>(null);
  const [codeMsg, setCodeMsg] = useState<string | null>(null);

  function flashCodeMsg(msg: string) {
    setCodeMsg(msg);
    setTimeout(() => setCodeMsg(null), 1500);
  }

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
      setCreatingComp(false); setNewCompName("");
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
      if (offline) {
        onOpen(id, compToDraft(mockApiComp(lang)), startEditing);
      } else {
        setError(t("loadCompError"));
      }
    }
  }

  async function createCompFn() {
    if (!newCompName.trim()) return;
    try {
      const c = await api.createComp({ name: newCompName.trim() });
      setCompList(prev => [...(prev ?? []), { id: c.id, name: c.name }]);
      setCreatingComp(false); setNewCompName("");
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
    } catch (e) {
      setError(e instanceof Error ? e.message : t("deleteCompError"));
    }
  }

  return (
    <div className="container">
      <div className="card">
        <div className="comp-header">
          <span style={{ fontWeight: 600, fontSize: 16 }}>{t("compsTitle")}</span>
          {offline && <span className="badge">{t("demoBadge")}</span>}
          {perms["comps.create"] && (
            <button className="btn" style={{ marginLeft: "auto" }}
              onClick={() => { setCreatingComp(p => !p); setError(null); }}>
              <i className="ti ti-plus" aria-hidden /> {t("newCompBtn")}
            </button>
          )}
        </div>

        {creatingComp && (
          <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)", display: "flex", gap: 8 }}>
            <input className="input" style={{ flex: 1, fontSize: 13, padding: "6px 10px" }}
              placeholder={t("compNamePlaceholder")}
              value={newCompName}
              autoFocus
              onChange={e => setNewCompName(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") createCompFn();
                if (e.key === "Escape") { setCreatingComp(false); setNewCompName(""); }
              }} />
            <button className="btn primary" onClick={createCompFn} disabled={!newCompName.trim()}>{t("createBtn")}</button>
            <button className="btn" style={{ flexShrink: 0 }} onClick={importCompFn} disabled={!newCompName.trim()}
              title={t("importCompCodeHint")}>
              <i className="ti ti-clipboard" aria-hidden /> {t("importCompCodeBtn")}
            </button>
            <button className="btn" onClick={() => { setCreatingComp(false); setNewCompName(""); }}>{t("cancel")}</button>
          </div>
        )}

        {codeMsg && <p style={{ color: "var(--hint)", padding: "0 16px", margin: 0, fontSize: 12 }}>{codeMsg}</p>}
        {error && <p style={{ color: "#e07a7a", padding: "8px 16px", margin: 0, fontSize: 12 }}>{error}</p>}

        {!compList && <p className="muted" style={{ padding: "16px 14px" }}>{t("loading")}</p>}
        {compList?.length === 0 && !creatingComp && (
          <p className="muted" style={{ padding: "16px 14px" }}>
            {t("noCompsYet")}{perms["comps.create"] ? ` ${t("createFirstOneSuffix")}` : ""}
          </p>
        )}
        {compList?.map(c => (
          <div key={c.id} className="comp-list-item" style={{ display: "flex", alignItems: "center" }}>
            <button style={{ flex: 1, display: "flex", alignItems: "center", gap: 8, background: "none", border: "none", cursor: "pointer", padding: "10px 14px", color: "inherit", textAlign: "left" }}
              onClick={() => { setDeletingCompId(null); openComp(c.id, false); }}>
              <i className="ti ti-layout-list" style={{ color: "var(--hint)", flexShrink: 0 }} aria-hidden />
              <span style={{ flex: 1 }}>{c.name}</span>
              <i className="ti ti-chevron-right" style={{ color: "var(--hint)" }} aria-hidden />
            </button>
            <button className="btn" style={{ fontSize: 12, padding: "3px 8px", marginRight: 10, flexShrink: 0, opacity: 0.5 }}
              title={t("copyCompCodeBtn")} disabled={copyingId === c.id}
              onClick={e => { e.stopPropagation(); copyCompCodeFn(c.id); }}>
              <i className="ti ti-copy" aria-hidden />
            </button>
            {perms["comps.manage"] && (
              deletingCompId === c.id ? (
                <div style={{ display: "flex", gap: 6, padding: "0 10px", flexShrink: 0 }}>
                  <button className="btn" style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a", borderColor: "#e07a7a" }}
                    onClick={() => deleteCompFn(c.id)}>
                    {t("confirmBtn")}
                  </button>
                  <button className="btn" style={{ fontSize: 11, padding: "3px 8px" }}
                    onClick={() => setDeletingCompId(null)}>
                    {t("cancel")}
                  </button>
                </div>
              ) : (
                <button className="btn" style={{ fontSize: 12, padding: "3px 8px", marginRight: 10, flexShrink: 0, opacity: 0.5 }}
                  title={t("deleteCompTitle")}
                  onClick={e => { e.stopPropagation(); setDeletingCompId(c.id); }}>
                  <i className="ti ti-trash" />
                </button>
              )
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
