"""
Bootstrap de schema para DEV — cria todas as tabelas direto dos modelos.

    python -m scripts.init_db

Atalho enquanto a 1ª migração Alembic não é gerada (autogenerate precisa de um
Postgres no ar). Em produção, use Alembic. Para um seed mínimo de teste, passe
--seed: cria uma guilda e algumas armas/funções de exemplo.
"""
from __future__ import annotations

import sys

from app.db import engine
from app.models import Base


def main() -> None:
    Base.metadata.create_all(engine)
    print(f"OK: {len(Base.metadata.tables)} tabelas criadas/conferidas.")

    if "--seed" in sys.argv:
        _seed()


def _seed() -> None:
    from sqlalchemy.orm import Session
    from app.models.tenancy import Guild, PremiumTier
    from app.models.catalog import Weapon, GameRole
    from app.models.comps import Comp, CompParty, CompSlot, CompSlotRole

    with Session(engine) as db:
        if db.get(Guild, 1) is not None:
            print("seed: guilda 1 já existe, nada a fazer.")
            return

        db.add(Guild(id=1, name="Guilda de Teste", premium_tier=PremiumTier.PREMIUM))

        # Armas com função invisível (dirige a sugestão de build).
        weapons = {
            "bruxa": Weapon(item_id="T8_2H_CURSEDSTAFF_AVALON", name="Cajado da Bruxa", invisible_function="tank"),
            "locus": Weapon(item_id="T8_MAIN_ENIGMATICSTAFF", name="Locus", invisible_function="support"),
            "oculto": Weapon(item_id="T8_MAIN_ARCANESTAFF", name="Cetro Oculto", invisible_function="support"),
            "sagrado": Weapon(item_id="T8_MAIN_HOLYSTAFF", name="Cajado Sagrado", invisible_function="healer"),
            "aguia": Weapon(item_id="T8_2H_ENIGMATICSTAFF", name="Cajado da Águia", invisible_function="dps_ranged"),
        }
        for w in weapons.values():
            db.add(w)
        db.flush()

        # Funções (com build). 'oculto' fica quase vazia de propósito p/ mostrar a sugestão.
        roles = {
            "bruxa": GameRole(guild_id=1, name="Bruxa", weapon_id=weapons["bruxa"].id,
                              helmet="8.4 Capuz de Aço", armor="8.4 Armadura de Guardião",
                              boots="8.4 Botas de Aço", cape="8.3 Capa de Lymhurst",
                              food="8.0 Ensopado de Carne", play_style="Segura a frente"),
            "locus": GameRole(guild_id=1, name="Locus", weapon_id=weapons["locus"].id,
                              helmet="8.4 Capuz do Asceta", armor="8.4 Túnica do Clérigo",
                              boots="8.4 Sandálias do Clérigo", cape="8.3 Capa de Thetford",
                              food="8.0 Torta de Peixe", play_style="Fica atrás do grupo"),
            "oculto": GameRole(guild_id=1, name="Oculto", weapon_id=weapons["oculto"].id),
            "sagrado": GameRole(guild_id=1, name="Cajado Sagrado", weapon_id=weapons["sagrado"].id,
                                helmet="8.4 Capuz do Clérigo", armor="8.4 Túnica do Clérigo",
                                boots="8.4 Sandálias do Clérigo", cape="8.3 Capa de Martlock",
                                food="8.0 Sopa de Algas", play_style="Cura a zerg"),
            "aguia": GameRole(guild_id=1, name="Águia", weapon_id=weapons["aguia"].id,
                              helmet="8.4 Capuz de Mago", armor="8.4 Túnica de Mago",
                              boots="8.4 Sapatos de Mago", cape="8.3 Capa de Bridgewatch",
                              food="8.0 Omelete", play_style="Foca aglomerados"),
        }
        for r in roles.values():
            db.add(r)
        db.flush()

        def slot(pos, label, *rids):
            s = CompSlot(position=pos, label=label)
            for i, rid in enumerate(rids):
                s.roles.append(CompSlotRole(game_role_id=roles[rid].id, position=i))
            return s

        comp = Comp(guild_id=1, name="ZvZ Padrão", description="Composição padrão de ZvZ")
        comp.parties.append(CompParty(position=0, name="Party 1", slots=[
            slot(0, "Tank", "bruxa"), slot(1, "Suporte (flex)", "locus", "oculto"),
            slot(2, "Healer", "sagrado"), slot(3, "DPS", "aguia"),
        ]))
        comp.parties.append(CompParty(position=1, name="Party 2", slots=[
            slot(0, "Tank", "bruxa"), slot(1, "Suporte (flex)", "oculto", "locus"),
            slot(2, "Healer", "sagrado"), slot(3, "DPS (flex)", "aguia", "oculto"),
        ]))
        db.add(comp)
        db.commit()
        print("seed: guilda 1 + 5 armas/funções + comp 'ZvZ Padrão' criados.")


if __name__ == "__main__":
    main()
