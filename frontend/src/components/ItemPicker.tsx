import { useEffect, useMemo, useRef, useState } from "react";
import { ALBION_ITEMS, itemRenderUrl, ICON_SIZE_SM, type ItemSlot } from "../data/albion-items";
import { imgRetry } from "../api";
import { useLang, useT, itemLocalName } from "../i18n";

interface Props {
  slot: ItemSlot;
  valueId: string;
  valueName: string;
  onChange: (id: string, name: string) => void;
  placeholder?: string;
  disabled?: boolean;
  excludeIds?: string[];
}

const MAX_RESULTS = 40;

export function ItemPicker({ slot, valueId, valueName, onChange, placeholder, disabled, excludeIds }: Props) {
  const { lang } = useLang();
  const t = useT();
  const [query, setQuery]   = useState("");
  const [open, setOpen]     = useState(false);
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef  = useRef<HTMLDivElement>(null);

  const excludeBases = useMemo(
    () => excludeIds?.map(id => id.replace(/^T\d+_/, "").replace(/@\d+$/, "")) ?? [],
    [excludeIds]
  );
  const slotItems = useMemo(
    () => ALBION_ITEMS.filter(i => {
      if (i.slot !== slot) return false;
      if (!excludeBases.length) return true;
      const base = i.id.replace(/^T\d+_/, "").replace(/@\d+$/, "");
      return !excludeBases.includes(base);
    }),
    [slot, excludeBases]
  );

  const selectedItem = useMemo(
    () => ALBION_ITEMS.find(i => i.id === valueId),
    [valueId]
  );

  // Resolve display name from id in current lang (falls back to valueName for unknown items)
  const resolvedName = useMemo(
    () => selectedItem ? itemLocalName(selectedItem, lang) : valueName,
    [selectedItem, lang, valueName]
  );

  const results = useMemo(() => {
    const raw = query.trim();
    if (!raw) return slotItems.slice(0, MAX_RESULTS);

    // Parse optional "t8" / "t8.1" tier+enchant prefix: captures tier and enchant
    const tierMatch = raw.match(/^t(\d+)(?:\.(\d))?(?:\s+(.*))?$/i);
    let tierFilter: number | null = null;
    let enchantFilter: number | null = null;
    let textQuery = raw;
    if (tierMatch) {
      tierFilter = parseInt(tierMatch[1]);
      enchantFilter = tierMatch[2] != null ? parseInt(tierMatch[2]) : null;
      textQuery = tierMatch[3] ?? "";
    }

    const words = textQuery.toLowerCase().split(/\s+/).filter(Boolean);
    return slotItems
      .filter(i => {
        const idTierMatch = i.id.match(/^T(\d+)_/);
        const idTier = idTierMatch ? parseInt(idTierMatch[1]) : 0;
        const idEnchant = parseInt(i.id.match(/@(\d+)$/)?.[1] ?? "0");
        if (tierFilter !== null && idTier !== tierFilter) return false;
        if (enchantFilter !== null && idEnchant !== enchantFilter) return false;
        if (!words.length) return true;
        // Always include EN name in search so EN queries work in any lang
        const localName = itemLocalName(i, lang);
        const text = `${localName} ${i.nameEn ?? ""}`.toLowerCase();
        return words.every(w => text.includes(w));
      })
      .slice(0, MAX_RESULTS);
  }, [slotItems, query]);

  // Fecha dropdown ao clicar fora
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (
        dropRef.current && !dropRef.current.contains(e.target as Node) &&
        inputRef.current && !inputRef.current.closest(".item-picker")?.contains(e.target as Node)
      ) {
        setOpen(false);
        setFocused(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  function handleFocus() {
    setFocused(true);
    setQuery("");
    setOpen(true);
  }

  function handleBlur() {
    // pequeno delay para permitir click no dropdown
    setTimeout(() => {
      if (!dropRef.current?.matches(":hover")) {
        setFocused(false);
        setOpen(false);
      }
    }, 150);
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    setQuery(e.target.value);
    setOpen(true);
  }

  function select(id: string, name: string, item?: typeof slotItems[0]) {
    onChange(id, item ? itemLocalName(item, lang) : name);
    setOpen(false);
    setFocused(false);
    setQuery("");
    inputRef.current?.blur();
  }

  function clear(e: React.MouseEvent) {
    e.stopPropagation();
    onChange("", "");
    setQuery("");
  }

  const displayValue = focused ? query : (resolvedName || "");

  return (
    <div className="item-picker">
      <div className="item-picker-field" onClick={() => !disabled && inputRef.current?.focus()}>
        {valueId && !focused && (
          <img
            className="item-picker-icon"
            src={itemRenderUrl(selectedItem ?? valueId)}
            alt=""
            onError={imgRetry(img => { img.style.display = "none"; })}
          />
        )}
        <input
          ref={inputRef}
          className="item-picker-input"
          value={displayValue}
          placeholder={disabled ? "—" : (placeholder ?? t("search"))}
          disabled={disabled}
          onFocus={handleFocus}
          onBlur={handleBlur}
          onChange={handleChange}
        />
        {valueId && !disabled && (
          <button className="item-picker-clear" onMouseDown={clear} title={t("remove")}>×</button>
        )}
      </div>

      {open && !disabled && (
        <div className="item-picker-dropdown" ref={dropRef}>
          {results.length === 0 ? (
            <div className="item-picker-empty">{t("noItems")}</div>
          ) : (
            results.map(item => (
              <div
                key={item.id}
                className={"item-picker-option" + (item.id === valueId ? " selected" : "")}
                onMouseDown={e => { e.preventDefault(); select(item.id, item.name, item); }}
              >
                <img
                  src={itemRenderUrl(item, 0, ICON_SIZE_SM)}
                  alt=""
                  loading="lazy"
                  onError={imgRetry(img => { img.style.opacity = "0.15"; })}
                />
                <span className="item-picker-option-name">{itemLocalName(item, lang)}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
