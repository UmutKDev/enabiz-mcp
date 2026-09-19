"""İki adımlı giriş akışı (`login_start` / `login_verify`) — ağsız, sentetik.

`auth.py`'nin SMS OTP yolu suite'te HİÇ kapsanmıyordu: `login_start`, `login_verify`,
`get_antiforgery` ve adımlar arası `pending.json` round-trip'i tek bir testle bile
sınanmamıştı. Bedeli 2026-08-31'de ölçüldü — portal `GetSmsOnayKontrol`'e bu sürümün
tanımadığı bir akış kodu (`77`) döndürdü; kod yalnız `22`/`87` biliyordu ve kullanıcı
"giriş çalışmıyor"dan başka bir şey göremedi. Nedeni ancak CANLI bir çağrıyla
görülebildi, çünkü hiçbir test bu dalı tutmuyordu.

Buradaki kimlik/token/kod değerlerinin hepsi UYDURMA; hiçbiri gerçek bir hesaba ait
değil ve `test_no_secrets.py` kalıplarına takılmayacak şekilde seçildi (gerçek
antiforgery değerleri `CfDJ8...`, gerçek TCKN 11 hanedir — ikisi de burada yok).
"""

from __future__ import annotations

import stat
import urllib.parse

import httpx
import pytest
from conftest import use_mock_transport

from enabiz_mcp import auth
from enabiz_mcp.config import Config

_TOKEN = "antiforgery-token-sentetik"
_XSRF_COOKIE = ".AspNetCore.Antiforgery.Sentetik"

_LOGIN_HTML = (
    "<html><body><form>"
    '<input name="TCKimlikNo" type="text">'
    f'<input name="__RequestVerificationToken" type="hidden" value="{_TOKEN}">'
    "</form></body></html>"
)

_JSON = {"content-type": "application/json; charset=utf-8"}

CHECK_PATH = "/Account/GetSmsOnayKontrol"
VERIFY_PATH = "/Account/GetSmsOnayGirisYap"


def _cfg(tmp_path) -> Config:
    return Config(
        tc_kimlik_no="tc-sentetik",
        sifre="sifre-sentetik",
        session_path=tmp_path / "session.json",
        min_interval=0.0,
    )


@pytest.fixture(autouse=True)
def _reset_pending():
    """`auth._pending` modül-geneli bir global — testler arası SIZMASIN."""
    auth._pending = None
    yield
    auth._pending = None


def _portal(
    *,
    check: str = auth.CHECK_2FA_REQUIRED,
    verify: str = auth.LOGIN_OK,
    login_html: str = _LOGIN_HTML,
    seen: list[httpx.Request] | None = None,
):
    """Portalın giriş uçlarını taklit eder. Gerçek portalın şekli: HTTP 200 + JSON
    gövdesinde ÇIPLAK bir kod (`"22"`), auth cookie'si adım 2'nin yanıtında."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == auth.LOGIN_PATH:
            return httpx.Response(
                200,
                html=login_html,
                headers={"set-cookie": f"{_XSRF_COOKIE}=xsrf-sentetik; path=/"},
            )
        if path == CHECK_PATH:
            return httpx.Response(200, text=check, headers=_JSON)
        if path == VERIFY_PATH:
            resp = httpx.Response(200, text=verify, headers=_JSON)
            if verify == auth.LOGIN_OK:
                resp.headers["set-cookie"] = ".EnabizSESSIONID=oturum-sentetik; path=/"
            return resp
        if path == auth.HOME_PATH:
            return httpx.Response(200, html="<html><body>Ana sayfa</body></html>")
        raise AssertionError(f"beklenmeyen yol: {path}")

    return handler


def _patch_build_client(monkeypatch, handler) -> None:
    """`auth.build_client`'ı mock'a bağlar — client'ı `login_start` KENDİ kurar.

    `_load_pending` de aynı adı çağırır, yani süreç-yeniden-başlama yolu da mock'lanır.
    """

    def fake(cfg, cookies=None, *, extra_headers=None):  # noqa: ARG001
        client = httpx.Client(base_url=cfg.base_url, cookies=cookies, follow_redirects=True)
        return use_mock_transport(client, handler)

    monkeypatch.setattr(auth, "build_client", fake)


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(request.content.decode()))


# --------------------------------------------------------------------------- #
# Adım 1 — login_start
# --------------------------------------------------------------------------- #
def test_login_start_22_moves_to_sms_step(tmp_path, monkeypatch):
    _patch_build_client(monkeypatch, _portal(check="22"))
    info = auth.login_start(_cfg(tmp_path))
    assert info["step"] == "sms_required"
    assert info["code"] == "22"


def test_login_start_sends_credentials_with_xsrf_header(tmp_path, monkeypatch):
    """Gövde `TCKimlikNo`/`Sifre`, başlık `XSRF-TOKEN` — sözleşme adları birebir.

    Bu adlar portalın sözleşmesidir (D6): yeniden adlandırılırsa giriş sessizce
    başarısız olur, çünkü portal eksik alanı 'hata' diye döndürür.
    """
    seen: list[httpx.Request] = []
    _patch_build_client(monkeypatch, _portal(seen=seen))
    cfg = _cfg(tmp_path)
    auth.login_start(cfg)

    post = next(r for r in seen if r.url.path == CHECK_PATH)
    assert post.headers["XSRF-TOKEN"] == _TOKEN
    assert post.headers["X-Requested-With"] == "XMLHttpRequest"
    assert _form(post) == {"TCKimlikNo": cfg.tc_kimlik_no, "Sifre": cfg.sifre}


def test_login_start_87_is_error(tmp_path, monkeypatch):
    _patch_build_client(monkeypatch, _portal(check="87"))
    cfg = _cfg(tmp_path)
    info = auth.login_start(cfg)
    assert info["step"] == "error"
    assert info["code"] == "87"
    assert not cfg.session_path.exists()


def test_login_start_unknown_code_does_not_claim_progress(tmp_path, monkeypatch):
    """Tanınmayan kod SESSİZCE 'oldu' sayılmaz — canlıda görülen `77` regresyonu.

    2026-08-31: portal `77` döndürdü. Kod yalnız `22`/`87` tanıyordu, dolayısıyla
    kullanıcıya `"Beklenmeyen yanıt: '77'"`den başka bir şey ulaşmadı: SMS gitti mi,
    ne yapmalı, tekrar denemek güvenli mi — hiçbiri belli değildi.

    Kilitlenen davranış: (a) adım ASLA `sms_required` olmaz, (b) ham kod yapısal bir
    alanda modele taşınır, (c) `hint` bir sonraki adımı UYDURMAYI engeller. Kodun
    ANLAMI hâlâ bilinmiyor ve buraya tahminle yazılmaz (CLAUDE.md #2).
    """
    _patch_build_client(monkeypatch, _portal(check="77"))
    cfg = _cfg(tmp_path)
    info = auth.login_start(cfg)

    assert info["step"] == "unknown"
    assert info["code"] == "77"
    assert "77" in info["message"]
    assert "enabiz_login_verify" in info["hint"]
    assert not cfg.session_path.exists(), "giriş olmadan oturum yazıldı"


def test_login_start_requires_credentials(tmp_path):
    """Kimlik `.env`'den gelir; yoksa AĞA HİÇ ÇIKMADAN patla."""
    cfg = Config(tc_kimlik_no=None, sifre=None, session_path=tmp_path / "session.json")
    with pytest.raises(RuntimeError, match="ENABIZ_TCKIMLIK"):
        auth.login_start(cfg)


def test_login_start_raises_when_antiforgery_token_missing(tmp_path, monkeypatch):
    """Login sayfası yeniden tasarlanırsa sessizce devam ETME — patla.

    Token'sız POST'u yine de göndermek, portalın anlamsız bir hata kodu döndürmesine
    ve teşhisin 'giriş bozuk'ta kalmasına yol açardı.
    """
    _patch_build_client(monkeypatch, _portal(login_html="<html><body>bakım</body></html>"))
    with pytest.raises(RuntimeError, match="Antiforgery token"):
        auth.login_start(_cfg(tmp_path))


# --------------------------------------------------------------------------- #
# Adım 2 — login_verify
# --------------------------------------------------------------------------- #
def test_login_verify_ok_saves_session_with_tight_permissions(tmp_path, monkeypatch):
    _patch_build_client(monkeypatch, _portal())
    cfg = _cfg(tmp_path)
    auth.login_start(cfg)
    info = auth.login_verify(cfg, "123456")

    assert info["step"] == "logged_in"
    assert info["has_auth_cookie"] is True
    saved = auth.load_session(cfg)
    assert saved is not None and auth.has_auth_cookie(saved)
    # PHI/oturum dosyası yalnız sahibine (invaryant #3).
    assert stat.S_IMODE(cfg.session_path.stat().st_mode) == 0o600
    # Başarıdan sonra geçici durum ORTADA KALMAZ.
    assert not (cfg.session_path.parent / "pending.json").exists()


def test_login_verify_sends_tc_and_stripped_code(tmp_path, monkeypatch):
    """Kullanıcı kodu boşlukla yapıştırırsa da geçmeli — `onayKodu` kırpılır."""
    seen: list[httpx.Request] = []
    _patch_build_client(monkeypatch, _portal(seen=seen))
    cfg = _cfg(tmp_path)
    auth.login_start(cfg)
    auth.login_verify(cfg, "  123456\n")

    post = next(r for r in seen if r.url.path == VERIFY_PATH)
    assert _form(post) == {"tc": cfg.tc_kimlik_no, "onayKodu": "123456"}


def test_login_verify_wrong_code_leaves_no_session(tmp_path, monkeypatch):
    _patch_build_client(monkeypatch, _portal(verify="2"))
    cfg = _cfg(tmp_path)
    auth.login_start(cfg)
    info = auth.login_verify(cfg, "000000")

    assert info["step"] == "wrong_code"
    assert not cfg.session_path.exists()


def test_login_verify_unknown_code_leaves_no_session(tmp_path, monkeypatch):
    """Adım 2'de de tanınmayan kod başarı SAYILMAZ."""
    _patch_build_client(monkeypatch, _portal(verify="77"))
    cfg = _cfg(tmp_path)
    auth.login_start(cfg)
    info = auth.login_verify(cfg, "123456")

    assert info["step"] == "unknown"
    assert not cfg.session_path.exists()


def test_login_verify_without_start_raises(tmp_path):
    with pytest.raises(RuntimeError, match="login_start"):
        auth.login_verify(_cfg(tmp_path), "123456")


def test_login_verify_survives_process_restart(tmp_path, monkeypatch):
    """Sunucu iki adım arasında yeniden başlarsa `pending.json` devralır.

    Antiforgery **cookie'si VE token'ı** birlikte korunmazsa portal OTP'yi reddeder;
    `_pending` yalnız süreç-içi olduğu için tek koruma bu dosya. Bellekteki durumu
    silip her ikisinin de isteğe geri döndüğünü kanıtlıyoruz.
    """
    _patch_build_client(monkeypatch, _portal())
    cfg = _cfg(tmp_path)
    auth.login_start(cfg)

    auth._pending = None  # süreç yeniden başladı — bellekteki durum GİTTİ

    seen: list[httpx.Request] = []
    _patch_build_client(monkeypatch, _portal(seen=seen))
    info = auth.login_verify(cfg, "123456")

    assert info["step"] == "logged_in"
    post = next(r for r in seen if r.url.path == VERIFY_PATH)
    assert post.headers["XSRF-TOKEN"] == _TOKEN, "token dosyadan geri gelmedi"
    assert _XSRF_COOKIE in post.headers.get("cookie", ""), "antiforgery cookie'si kayboldu"


def test_pending_file_is_owner_only(tmp_path, monkeypatch):
    """`pending.json` antiforgery oturumunu taşır — o da yalnız sahibine okunur."""
    _patch_build_client(monkeypatch, _portal())
    cfg = _cfg(tmp_path)
    auth.login_start(cfg)

    pending = cfg.session_path.parent / "pending.json"
    assert pending.exists()
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
