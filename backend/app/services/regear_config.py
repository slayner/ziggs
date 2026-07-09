"""Config de regear da guilda — vive em `Guild.settings` (JSONB, sem migração).

Chaves:
  regear_channels            — lista de canais monitorados, cada um com sua
                               própria % de cobertura: [{"channel_id": str, "coverage_pct": int}, ...].
  regear_enabled_categories  — lista de categorias cobertas (weapon|offhand|helmet|armor|
                               boots|cape|mount|bag|food|potion). Default: food/potion off.
  regear_disabled_items      — item-base IDs (ex.: "MOUNT_OX") override, sempre off.
  regear_require_approval    — logística aprova antes de pagar (default true).
  regear_approver_role_ids   — cargos Discord que podem aprovar (vazio = events.manage).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.tenancy import Guild
from app.services.prices import REGEAR_CATEGORIES

_DEFAULT_CATEGORIES = ["weapon", "offhand", "helmet", "armor", "boots", "cape", "mount", "bag"]
KEY = "regear"  # namespace futuro: poderia ser settings["regear"] = {...}; por ora flat.


@dataclass
class RegearChannel:
    channel_id: str
    coverage_pct: int = 100

    def to_dict(self) -> dict:
        return {"channel_id": self.channel_id, "coverage_pct": self.coverage_pct}


@dataclass
class RegearSettings:
    enabled: bool = False
    channels: list[RegearChannel] = field(default_factory=list)
    enabled_categories: list[str] = field(default_factory=lambda: list(_DEFAULT_CATEGORIES))
    disabled_items: list[str] = field(default_factory=list)
    require_approval: bool = True
    approver_role_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "channels": [c.to_dict() for c in self.channels],
            "enabled_categories": self.enabled_categories,
            "disabled_items": self.disabled_items,
            "require_approval": self.require_approval,
            "approver_role_ids": self.approver_role_ids,
        }

    def coverage_for(self, channel_id: str | None) -> int:
        """% de cobertura do canal por onde a screenshot chegou. Sem canal
        conhecido (ou canal removido da config entretanto), cai no primeiro
        configurado; sem nenhum canal, 100 (mesmo default de sempre)."""
        for c in self.channels:
            if c.channel_id == channel_id:
                return c.coverage_pct
        return self.channels[0].coverage_pct if self.channels else 100


def _parse_channels(raw: object) -> list[RegearChannel]:
    out: list[RegearChannel] = []
    seen: set[str] = set()
    for entry in (raw or []):
        if not isinstance(entry, dict) or not entry.get("channel_id"):
            continue
        cid = str(entry["channel_id"])
        if cid in seen:
            continue
        seen.add(cid)
        pct = max(0, min(100, int(entry.get("coverage_pct", 100) or 0)))
        out.append(RegearChannel(channel_id=cid, coverage_pct=pct))
    return out


def get_regear_settings(guild: Guild) -> RegearSettings:
    s = (guild.settings or {}).get(KEY) or {}
    # Tolerância a 3 formatos: novo namespaced com "channels" (lista), o
    # namespaced antigo de canal único ({"channel_id", "coverage_pct"}), e o
    # flat pré-namespace (regear_channel_id na raiz de settings).
    if "channels" in s:
        channels = _parse_channels(s.get("channels"))
    elif s.get("channel_id"):
        channels = [RegearChannel(str(s["channel_id"]), max(0, min(100, int(s.get("coverage_pct", 100) or 100))))]
    else:
        flat = guild.settings or {}
        if flat.get("regear_channel_id"):
            channels = [RegearChannel(str(flat["regear_channel_id"]), max(0, min(100, int(flat.get("regear_coverage_pct", 100) or 100))))]
        else:
            channels = []
        if not s:
            s = {
                "enabled_categories": flat.get("regear_enabled_categories", _DEFAULT_CATEGORIES),
                "disabled_items": flat.get("regear_disabled_items", []),
                "require_approval": flat.get("regear_require_approval", True),
                "approver_role_ids": flat.get("regear_approver_role_ids", []),
            }

    cats = [c for c in (s.get("enabled_categories") or _DEFAULT_CATEGORIES) if c in REGEAR_CATEGORIES]
    if not cats:
        cats = list(_DEFAULT_CATEGORIES)
    # Back-compat: guildas que ainda não têm o flag `enabled` (pré-mudança) são
    # consideradas ligadas sse já tinham canais de cobertura configurados.
    enabled = bool(s.get("enabled", len(channels) > 0))
    return RegearSettings(
        enabled=enabled,
        channels=channels,
        enabled_categories=cats,
        disabled_items=[str(x) for x in (s.get("disabled_items") or [])],
        require_approval=bool(s.get("require_approval", True)),
        approver_role_ids=[int(r) for r in (s.get("approver_role_ids") or [])],
    )


def apply_regear_settings(guild: Guild, data: dict) -> RegearSettings:
    """Valida e grava em guild.settings[KEY]. Retorna o snapshot saneado."""
    settings = dict(guild.settings or {})
    cur = get_regear_settings(guild)
    if "enabled" in data:
        cur.enabled = bool(data["enabled"])
    if "channels" in data:
        cur.channels = _parse_channels(data["channels"])
    if "enabled_categories" in data:
        cur.enabled_categories = [c for c in data["enabled_categories"] if c in REGEAR_CATEGORIES] or list(_DEFAULT_CATEGORIES)
    if "disabled_items" in data:
        cur.disabled_items = [str(x) for x in (data.get("disabled_items") or [])]
    if "require_approval" in data:
        cur.require_approval = bool(data["require_approval"])
    if "approver_role_ids" in data:
        cur.approver_role_ids = [int(r) for r in (data.get("approver_role_ids") or [])]

    settings[KEY] = cur.to_dict()
    guild.settings = settings
    return cur


if __name__ == "__main__":
    # ponytail: self-check do parsing multi-formato (não tem framework de teste aqui).
    g = Guild(settings={"regear": {"channels": [
        {"channel_id": "1", "coverage_pct": 50}, {"channel_id": "2", "coverage_pct": 200},
    ]}})
    s = get_regear_settings(g)
    assert [c.channel_id for c in s.channels] == ["1", "2"]
    assert s.channels[1].coverage_pct == 100, "clamp 0..100"
    assert s.coverage_for("1") == 50 and s.coverage_for("2") == 100
    assert s.coverage_for("nao-existe") == 50, "fallback = primeiro canal"

    g2 = Guild(settings={"regear_channel_id": "9", "regear_coverage_pct": 42})
    s2 = get_regear_settings(g2)
    assert s2.channels == [RegearChannel("9", 42)], "migra formato flat legado"

    g3 = Guild(settings={})
    s3 = get_regear_settings(g3)
    assert s3.channels == [] and s3.coverage_for(None) == 100

    g4 = Guild(settings={})
    after = apply_regear_settings(g4, {"channels": [{"channel_id": "5", "coverage_pct": 30}, {"channel_id": "5", "coverage_pct": 99}]})
    assert len(after.channels) == 1, "dedup por channel_id, mantém a 1ª ocorrência"
    assert after.channels[0].coverage_pct == 30
    print("regear_config OK")
