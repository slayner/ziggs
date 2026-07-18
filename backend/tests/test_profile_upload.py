"""
Testes do upload de avatar/banner (limites, crop, GIF animado) — sem rede nem
banco, imagens geradas em memória e _IMAGES_DIR apontado pra pasta temporária.
Roda com pytest OU direto:
    PYTHONPATH=. python tests/test_profile_upload.py
"""
import io
import os
import shutil
import tempfile
from types import SimpleNamespace

from PIL import Image

from app.services import user_profile


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 50, 50)).save(buf, "PNG")
    return buf.getvalue()


def _gif(w: int, h: int, n_frames: int) -> bytes:
    frames = [Image.new("RGB", (w, h), (i * 40 % 256, 0, 0)) for i in range(n_frames)]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buf.getvalue()


def _user():
    return SimpleNamespace(id=1, profile_avatar_path=None, profile_banner_path=None)


def _in_tmp(fn):
    """Roda fn com _IMAGES_DIR apontando pra um diretório temporário."""
    tmp = tempfile.mkdtemp()
    old = user_profile._IMAGES_DIR
    user_profile._IMAGES_DIR = tmp
    try:
        fn(tmp)
    finally:
        user_profile._IMAGES_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def _raises(fn) -> str:
    try:
        fn()
    except user_profile.ProfileServiceError as e:
        return str(e)
    raise AssertionError("esperava ProfileServiceError")


def test_rejeita_acima_do_limite():
    def run(tmp):
        # checagem de tamanho vem ANTES da decodificação — bytes de lixo servem
        msg = _raises(lambda: user_profile.set_avatar(_user(), b"x" * (25 * 1024 * 1024 + 1)))
        assert "25 MB" in msg
        msg = _raises(lambda: user_profile.set_banner(_user(), b"x" * (100 * 1024 * 1024 + 1)))
        assert "100 MB" in msg
    _in_tmp(run)


def test_rejeita_menor_que_minimo():
    def run(tmp):
        msg = _raises(lambda: user_profile.set_avatar(_user(), _png(100, 100)))
        assert "128×128" in msg
        msg = _raises(lambda: user_profile.set_banner(_user(), _png(200, 80)))
        assert "320×100" in msg
    _in_tmp(run)


def test_rejeita_nao_imagem():
    def run(tmp):
        _raises(lambda: user_profile.set_avatar(_user(), b"definitivamente nao e uma imagem"))
    _in_tmp(run)


def test_crop_aplicado():
    def run(tmp):
        u = _user()
        user_profile.set_avatar(u, _png(400, 400), (0.25, 0.25, 0.5, 0.5))
        out = Image.open(user_profile.image_abs_path(u.profile_avatar_path))
        assert out.size == (200, 200)
        assert u.profile_avatar_path.endswith("avatar.jpg")
    _in_tmp(run)


def test_crop_invalido():
    def run(tmp):
        msg = _raises(lambda: user_profile.set_avatar(_user(), _png(400, 400), (0.5, 0.5, 0.0, 0.0)))
        assert "crop" in msg
    _in_tmp(run)


def test_gif_animado_preserva_animacao():
    def run(tmp):
        u = _user()
        user_profile.set_avatar(u, _gif(200, 200, 5), (0.25, 0.25, 0.5, 0.5))
        assert u.profile_avatar_path.endswith("avatar.gif")
        out = Image.open(user_profile.image_abs_path(u.profile_avatar_path))
        assert getattr(out, "is_animated", False)
        assert out.n_frames == 5
        assert out.size == (100, 100)
    _in_tmp(run)


def test_troca_jpg_gif_nao_deixa_orfao():
    def run(tmp):
        u = _user()
        user_profile.set_avatar(u, _png(200, 200))
        jpg = user_profile.image_abs_path(u.profile_avatar_path)
        assert os.path.isfile(jpg)
        user_profile.set_avatar(u, _gif(200, 200, 3))
        assert u.profile_avatar_path.endswith("avatar.gif")
        assert not os.path.isfile(jpg), "avatar.jpg órfão após trocar pra GIF"
    _in_tmp(run)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("todos passaram")
