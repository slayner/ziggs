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
    Event, EventParticipant, EventVerificationStep, EventStateTransition, EventSignup,
    EventAssignment,
)
from app.models.audit import AuditLog  # noqa: F401
from app.models.loot import EventLootEntry, GuildChestEntry, ItemPriceCache  # noqa: F401
from app.models.prices import ItemPrice, ItemPriceLatest  # noqa: F401
from app.models.players import (  # noqa: F401
    AlbionPlayer, PlayerSnapshot, PlayerKillEvent, PlayerWeaponStat, PlayerCountSnapshot,
)
from app.models.battles import (  # noqa: F401
    Battle, BattleGuild, BattleSide, BattleParticipant, BattleKillEvent,
    BattleGroup, BattleGroupMember, ReprocessCampaign, BattleIdProbe,
)
from app.models.claims import CharacterClaim, RegisteredCharacter  # noqa: F401
from app.models.registration import BotRegistration  # noqa: F401
from app.models.economy import EconomyBalance, EconomyTransaction  # noqa: F401
from app.models.regear import RegearRequest  # noqa: F401
from app.models.lootlog import LootLogSubmission  # noqa: F401
from app.models.nodes import (  # noqa: F401
    NodeDef, NodeEvent, NodeEventLog, NodeMap, NodeMapExclusion, NodeCalendar,
)

__all__ = [
    "Base",
    "Guild", "User", "GuildMember", "PremiumTier",
    "Weapon", "GameRole", "WeaponSpell",
    "Comp", "CompParty", "CompSlot", "CompSlotRole",
    "Event", "EventParticipant", "EventVerificationStep", "EventStateTransition", "EventSignup", "EventAssignment",
    "AuditLog",
    "EventLootEntry", "GuildChestEntry", "ItemPriceCache",
    "ItemPrice", "ItemPriceLatest",
    "AlbionPlayer", "PlayerSnapshot", "PlayerKillEvent", "PlayerWeaponStat", "PlayerCountSnapshot",
    "Battle", "BattleGuild", "BattleSide", "BattleParticipant", "BattleKillEvent",
    "BattleGroup", "BattleGroupMember", "ReprocessCampaign", "BattleIdProbe",
    "CharacterClaim", "RegisteredCharacter",
    "BotRegistration",
    "EconomyBalance", "EconomyTransaction",
    "RegearRequest",
    "LootLogSubmission",
    "NodeDef", "NodeEvent", "NodeEventLog", "NodeMap", "NodeMapExclusion", "NodeCalendar",
]
