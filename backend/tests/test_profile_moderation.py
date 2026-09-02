import io
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.audit import AuditLog
from app.models.base import Base
from app.models.profile_media import ProfileMediaSubmission
from app.models.tenancy import User
from app.services import user_profile


def _png(w: int, h: int) -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (w, h), (200, 50, 50)).save(data, "PNG")
    return data.getvalue()


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        User.__table__, ProfileMediaSubmission.__table__, AuditLog.__table__,
    ])
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_pending_stays_private_until_approval(tmp_path):
    old_dir = user_profile._IMAGES_DIR
    user_profile._IMAGES_DIR = str(tmp_path)
    try:
        db = _db()
        user = User(id=1, username="test", craft_settings={})
        db.add(user)
        db.commit()
        user_profile.set_avatar(user, _png(200, 200))
        old_path = user.profile_avatar_path
        old_abs = user_profile.image_abs_path(old_path)
        submission = user_profile.submit_media(db, user, "avatar", _png(300, 300))
        db.commit()

        profile = user_profile.my_profile_dict(db, user)
        assert profile["pending_kinds"] == ["avatar"]
        assert user.profile_avatar_path == old_path and profile["avatar_url"]

        result, replaced = user_profile.approve_submission(db, submission.id, actor_id=9)
        db.commit()
        user_profile._remove_existing(replaced)
        assert result["decision"] == "approved"
        assert user.profile_avatar_path == submission.path
        assert not list(db.scalars(select(ProfileMediaSubmission)))
        assert not os.path.exists(old_abs)
        Image.open(user_profile.image_abs_path(user.profile_avatar_path)).verify()
    finally:
        user_profile._IMAGES_DIR = old_dir


def test_rejection_removes_every_image_and_blocks_90_days(tmp_path):
    old_dir = user_profile._IMAGES_DIR
    user_profile._IMAGES_DIR = str(tmp_path)
    try:
        db = _db()
        user = User(id=2, username="test", craft_settings={})
        db.add(user)
        db.commit()
        user_profile.set_avatar(user, _png(200, 200))
        user_profile.set_banner(user, _png(400, 125))
        avatar = user_profile.submit_media(db, user, "avatar", _png(300, 300))
        user_profile.submit_media(db, user, "banner", _png(640, 200))
        db.commit()

        result = user_profile.reject_submission(db, avatar.id, actor_id=9)
        db.commit()
        user_profile.purge_user_images(user.id)

        assert result["decision"] == "rejected"
        assert user.profile_avatar_path is None and user.profile_banner_path is None
        assert not list(db.scalars(select(ProfileMediaSubmission)))
        assert not (tmp_path / str(user.id)).exists()
        blocked = user.profile_media_blocked_until.replace(tzinfo=timezone.utc)
        assert datetime.now(timezone.utc) + timedelta(days=89) < blocked
        try:
            user_profile.submit_media(db, user, "avatar", _png(200, 200))
        except user_profile.ProfileServiceError as error:
            assert "bloqueados" in str(error)
        else:
            raise AssertionError("upload durante bloqueio deveria falhar")
    finally:
        user_profile._IMAGES_DIR = old_dir


if __name__ == "__main__":
    for test in (test_pending_stays_private_until_approval, test_rejection_removes_every_image_and_blocks_90_days):
        tmp = Path(tempfile.mkdtemp())
        try:
            test(tmp)
            print(f"PASS {test.__name__}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
