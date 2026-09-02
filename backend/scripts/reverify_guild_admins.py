"""
Remediação pós-fix da escalação de privilégio: qualquer row
`guild_members.is_guild_admin=True` pode ter sido forjada pelo cliente via
`is_admin` no body de POST /auth/select-guild (corrigido — agora deriva do
Discord, ver deps.verify_guild_membership). Reverifica cada admin marcado
contra o Discord real e rebaixa quem não confirma. Admin de verdade recupera
o flag no próximo select-guild/switch-guild.

    python -m scripts.reverify_guild_admins
"""
import sys
import time

sys.path.insert(0, ".")

from fastapi import HTTPException
from sqlalchemy import select

from app.api.deps import verify_guild_membership
from app.db import SessionLocal
from app.models.tenancy import GuildMember, User

db = SessionLocal()
try:
    admins = db.scalars(select(GuildMember).where(GuildMember.is_guild_admin.is_(True))).all()
    print(f"{len(admins)} membros marcados como admin — reverificando contra o Discord...")

    kept = demoted = skipped = 0
    for m in admins:
        user = db.scalar(select(User).where(User.id == m.user_id))
        if user is None:
            continue
        try:
            _, _, is_admin, role_ids = verify_guild_membership(user, m.guild_id)
        except HTTPException as ex:
            if ex.status_code == 403:
                m.is_guild_admin = False
                demoted += 1
                print(f"  REBAIXADO user={user.id} guild={m.guild_id} (não é mais membro)")
            else:
                skipped += 1
                print(f"  PULADO user={user.id} guild={m.guild_id} (Discord inalcançável: {ex.detail})")
            time.sleep(0.5)
            continue

        if is_admin:
            kept += 1
            if role_ids:
                m.discord_role_ids = role_ids
        else:
            m.is_guild_admin = False
            demoted += 1
            print(f"  REBAIXADO user={user.id} guild={m.guild_id} (não é admin no Discord)")
        time.sleep(0.5)

    db.commit()
    print(f"\nOK: {kept} confirmados, {demoted} rebaixados, {skipped} pulados (reverificar depois).")
finally:
    db.close()
