"""Configuração de regear da guilda em `Guild.settings`."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.tenancy import Guild
from app.services.prices import REGEAR_CATEGORIES

EVERYONE_ROLE_ID = "@everyone"
_DEFAULT_CATEGORIES = ["weapon", "offhand", "helmet", "armor", "boots", "cape", "mount", "potion", "food"]


@dataclass
class RegearChannel:
    channel_id: str

    def to_dict(self) -> dict:
        return {"channel_id": self.channel_id}


@dataclass
class RegearSettings:
    enabled: bool = False
    event_thread_parent_channel_id: str | None = None
    payment_channel_id: str | None = None
    extra_channels: list[RegearChannel] = field(default_factory=list)
    payment_pct: int = 100
    enabled_categories: list[str] = field(default_factory=lambda: list(_DEFAULT_CATEGORIES))
    disabled_items: list[str] = field(default_factory=list)
    requester_role_ids: list[str] = field(default_factory=lambda: [EVERYONE_ROLE_ID])
    attendance_multiplier_enabled: bool = False
    require_approval: bool = True
    approver_role_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "event_thread_parent_channel_id": self.event_thread_parent_channel_id,
            "payment_channel_id": self.payment_channel_id,
            "extra_channels": [c.to_dict() for c in self.extra_channels],
            "payment_pct": self.payment_pct,
            "enabled_categories": self.enabled_categories,
            "disabled_items": self.disabled_items,
            "requester_role_ids": self.requester_role_ids,
            "attendance_multiplier_enabled": self.attendance_multiplier_enabled,
            "require_approval": self.require_approval,
            "approver_role_ids": self.approver_role_ids,
        }

    def accepts_requester_roles(self, role_ids: set[int]) -> bool:
        return EVERYONE_ROLE_ID in self.requester_role_ids or bool(role_ids.intersection(int(r) for r in self.requester_role_ids if r != EVERYONE_ROLE_ID))

    def coverage_for(self, _channel_id: str | None) -> int:
        return self.payment_pct


def _channel_list(raw: object) -> list[RegearChannel]:
    seen: set[str] = set()
    out: list[RegearChannel] = []
    for item in raw or []:
        value = item.get("channel_id") if isinstance(item, dict) else item
        if value is not None and str(value) not in seen:
            seen.add(str(value))
            out.append(RegearChannel(str(value)))
    return out


def _role_ids(raw: object) -> list[str]:
    values = [EVERYONE_ROLE_ID if str(v) == EVERYONE_ROLE_ID else str(int(v)) for v in (raw or [EVERYONE_ROLE_ID])]
    return list(dict.fromkeys(values)) or [EVERYONE_ROLE_ID]


def get_regear_settings(guild: Guild) -> RegearSettings:
    root = guild.settings or {}
    s = root.get("regear") or {}
    legacy_channels = _channel_list(s.get("channels"))
    extra = _channel_list(s.get("extra_channels", legacy_channels))
    categories = [c for c in (s.get("enabled_categories") or _DEFAULT_CATEGORIES) if c in REGEAR_CATEGORIES] or list(_DEFAULT_CATEGORIES)
    return RegearSettings(
        enabled=bool(s.get("enabled", bool(legacy_channels))),
        event_thread_parent_channel_id=str(s.get("event_thread_parent_channel_id") or root.get("regear_thread_channel_id")) if (s.get("event_thread_parent_channel_id") or root.get("regear_thread_channel_id")) else None,
        payment_channel_id=str(s["payment_channel_id"]) if s.get("payment_channel_id") else None,
        extra_channels=extra,
        payment_pct=max(0, min(100, int(s.get("payment_pct", s.get("coverage_pct", 100)) or 0))),
        enabled_categories=categories,
        disabled_items=[str(x) for x in (s.get("disabled_items") or [])],
        requester_role_ids=_role_ids(s.get("requester_role_ids")),
        attendance_multiplier_enabled=bool(s.get("attendance_multiplier_enabled", False)),
        require_approval=bool(s.get("require_approval", True)),
        approver_role_ids=[int(r) for r in (s.get("approver_role_ids") or [])],
    )


def apply_regear_settings(guild: Guild, data: dict) -> RegearSettings:
    settings = dict(guild.settings or {})
    cur = get_regear_settings(guild)
    for key in ("enabled", "event_thread_parent_channel_id", "payment_channel_id", "attendance_multiplier_enabled", "require_approval"):
        if key in data:
            setattr(cur, key, data[key] if key.endswith("_id") else bool(data[key]))
    if "extra_channels" in data:
        cur.extra_channels = _channel_list(data["extra_channels"])
    if "payment_pct" in data:
        cur.payment_pct = max(0, min(100, int(data["payment_pct"])))
    if "enabled_categories" in data:
        cur.enabled_categories = [c for c in data["enabled_categories"] if c in REGEAR_CATEGORIES] or list(_DEFAULT_CATEGORIES)
    if "disabled_items" in data:
        cur.disabled_items = [str(x) for x in (data["disabled_items"] or [])]
    if "requester_role_ids" in data:
        cur.requester_role_ids = _role_ids(data["requester_role_ids"])
    if "approver_role_ids" in data:
        cur.approver_role_ids = [int(r) for r in (data["approver_role_ids"] or [])]
    settings["regear"] = cur.to_dict()
    if "event_thread_parent_channel_id" in data:
        if cur.event_thread_parent_channel_id:
            settings["regear_thread_channel_id"] = cur.event_thread_parent_channel_id
        else:
            settings.pop("regear_thread_channel_id", None)
    guild.settings = settings
    return cur
