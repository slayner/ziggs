"""
Importa todos os modelos para que `Base.metadata` enxergue tudo (Alembic
autogenerate e create_all dependem disso).
"""
from app.models.base import Base  # noqa: F401
from app.models.tenancy import Guild, User, GuildMember, GuildRolePermission, PremiumTier  # noqa: F401
from app.models.catalog import Weapon, GameRole, WeaponSpell  # noqa: F401
from app.models.comps import (  # noqa: F401
    Comp, CompParty, CompSlot, CompSlotRole,
)
from app.models.events import (  # noqa: F401
    Event, EventParticipant, EventVerificationStep, EventStateTransition,
)
from app.models.audit import AuditLog  # noqa: F401
from app.models.loot import EventLootEntry, GuildChestEntry, ItemPriceCache  # noqa: F401
from app.models.prices import ItemPrice, ItemPriceLatest  # noqa: F401
from app.models.players import AlbionPlayer, PlayerSnapshot, PlayerKillEvent  # noqa: F401
from app.models.battles import (  # noqa: F401
    Battle, BattleGuild, BattleSide, BattleParticipant, BattleKillEvent,
    BattleGroup, BattleGroupMember,
)

__all__ = [
    "Base",
    "Guild", "User", "GuildMember", "PremiumTier",
    "Weapon", "GameRole", "WeaponSpell",
    "Comp", "CompParty", "CompSlot", "CompSlotRole",
    "Event", "EventParticipant", "EventVerificationStep", "EventStateTransition",
    "AuditLog",
    "EventLootEntry", "GuildChestEntry", "ItemPriceCache",
    "ItemPrice", "ItemPriceLatest",
    "AlbionPlayer", "PlayerSnapshot", "PlayerKillEvent",
    "Battle", "BattleGuild", "BattleSide", "BattleParticipant", "BattleKillEvent",
    "BattleGroup", "BattleGroupMember",
]
