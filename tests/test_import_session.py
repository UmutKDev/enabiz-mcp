"""Oturum içe aktarma (yedek giriş yolu) — ağsız, sentetik.

Bu yol bir SIR taşır (kimlikli oturum cookie'si). Testler üç şeyi kilitler:
1. Ayrıştırma, değerleri sessizce BOZMAZ (base64 `==` tuzağı).
2. Kimlik cookie'si yoksa kaydetmez; geçersiz oturum mevcut dosyayı EZMEZ.
3. Hiçbir hata mesajı cookie DEĞERİ içermez — mesajlar kullanıcıya gösterilir.

Değerlerin hepsi uydurma ve `test_no_secrets.py` kalıplarına takılmayacak şekilde
seçildi.
"""

from __future__ import annotations

import stat

import pytest

from enabiz_mcp import auth
from enabiz_mcp import import_session as imp
from enabiz_mcp.config import Config

_DOMAIN = "enabiz.gov.tr"
_AUTH = ".EnabizSESSIONID"
_VALUE = "oturum-degeri-sentetik"


def _cfg(tmp_path) -> Config:
    return Config(
        tc_kimlik_no=None,
        sifre=None,
        session_path=tmp_path / "session.json",
        min_interval=0.0,
    )


# --------------------------------------------------------------------------- #
# Ayrıştırma
# --------------------------------------------------------------------------- #
def test_parses_full_cookie_header():
    pairs = imp.parse_cookie_header(f"{_AUTH}={_VALUE}; SAGLIK00beef=waf-sentetik")
    assert pairs == [(_AUTH, _VALUE), ("SAGLIK00beef", "waf-sentetik")]


def test_tolerates_cookie_prefix_and_whitespace():
    raw = f"  Cookie: {_AUTH}={_VALUE} ;  SAGLIK00beef=waf-sentetik  \n"
    assert imp.parse_cookie_header(raw) == [
        (_AUTH, _VALUE),
        ("SAGLIK00beef", "waf-sentetik"),
    ]


def test_bare_value_gets_the_default_cookie_name():
    """DevTools'ta \"Copy value\" adı vermez — çıplak değeri kabul ediyoruz."""
    assert imp.parse_cookie_header(_VALUE) == [(imp.DEFAULT_COOKIE_NAME, _VALUE)]


def test_bare_base64_value_is_not_split_on_its_padding():
    """`abc==` çıplak bir DEĞERdir, `ad=değer` DEĞİL — regresyon.

    Saf `partition("=")` bunu `ad="oturum"`, `değer="="` diye böler ve sessizce
    kırpılmış, yanlış adlı bir cookie yazardı: oturum "içe aktarıldı" görünür ama
    hiçbir veri çağrısı çalışmazdı. Ayrım `=` VARLIĞINA değil, solundaki metnin
    geçerli bir cookie ADI olmasına bakar.
    """
    raw = "oturum+degeri/sentetik=="
    assert imp.parse_cookie_header(raw) == [(imp.DEFAULT_COOKIE_NAME, raw)]


def test_empty_input_is_rejected():
    for raw in ("", "   ", "Cookie:", " ; ; "):
        with pytest.raises(imp.CookieImportError):
            imp.parse_cookie_header(raw)


def test_multipart_garbage_is_rejected_not_guessed():
    """Birden çok parça varsa tahmin YOK — sessiz yanlış-eşlemeden iyisi hatadır."""
    with pytest.raises(imp.CookieImportError):
        imp.parse_cookie_header("bir sey; baska sey")


# --------------------------------------------------------------------------- #
# Doğrulama
# --------------------------------------------------------------------------- #
def test_build_requires_an_auth_cookie():
    """WAF cookie'si tek başına 'oturum' değildir."""
    with pytest.raises(imp.CookieImportError, match="Kimlik cookie'si yok"):
        imp.build_session_cookies("SAGLIK00beef=waf-sentetik", _DOMAIN)


def test_error_message_never_leaks_the_value():
    """Hata metni kullanıcıya basılır — DEĞER içermemeli, yalnız ADlar."""
    with pytest.raises(imp.CookieImportError) as exc:
        imp.build_session_cookies(f"SAGLIK00beef={_VALUE}", _DOMAIN)
    message = str(exc.value)
    assert "SAGLIK00beef" in message
    assert _VALUE not in message


def test_build_accepts_auth_cookie_and_keeps_the_value():
    cookies = imp.build_session_cookies(f"{_AUTH}={_VALUE}", _DOMAIN)
    assert auth.has_auth_cookie(cookies)
    assert cookies.get(_AUTH, domain=_DOMAIN) == _VALUE


def test_empty_value_is_rejected():
    with pytest.raises(imp.CookieImportError):
        imp.build_session_cookies(f"{_AUTH}=", _DOMAIN)


# --------------------------------------------------------------------------- #
# CLI — ağ `cookies_alive` üzerinden kesiliyor
# --------------------------------------------------------------------------- #
def _run_main(monkeypatch, tmp_path, *, raw: str, alive: bool) -> tuple[int, Config]:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(Config, "from_env", classmethod(lambda _c: cfg))
    monkeypatch.setattr(imp.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(imp.getpass, "getpass", lambda _p: raw)
    monkeypatch.setattr(auth, "cookies_alive", lambda _cfg, _cookies: alive)
    return imp.main(), cfg


def test_main_saves_verified_session_with_tight_permissions(monkeypatch, tmp_path):
    code, cfg = _run_main(monkeypatch, tmp_path, raw=f"{_AUTH}={_VALUE}", alive=True)

    assert code == 0
    saved = auth.load_session(cfg)
    assert saved is not None and auth.has_auth_cookie(saved)
    assert stat.S_IMODE(cfg.session_path.stat().st_mode) == 0o600


def test_main_does_not_clobber_a_working_session_when_paste_is_dead(monkeypatch, tmp_path):
    """Geçersiz yapıştırma ÇALIŞAN oturumu ezmemeli — doğrula, sonra kaydet.

    Sıra ters olsaydı bir yanlış kopyala-yapıştır kullanıcıyı tamamen dışarı atardı
    ve geri dönüşü yeni bir tarayıcı girişi olurdu.
    """
    cfg = _cfg(tmp_path)
    auth.write_session_file(cfg, {"cookies": [{"name": _AUTH, "value": "eski-ama-calisan"}]})

    code, cfg = _run_main(monkeypatch, tmp_path, raw=f"{_AUTH}=yeni-ama-olu", alive=False)

    assert code == 1
    kept = auth.read_session_file(cfg)["cookies"]
    assert kept == [{"name": _AUTH, "value": "eski-ama-calisan"}], "çalışan oturum ezildi"


def test_main_preserves_the_mhrs_token(monkeypatch, tmp_path):
    """Oturum dosyasını iki yazıcı paylaşır — içe aktarma MHRS JWT'sini silmemeli."""
    cfg = _cfg(tmp_path)
    auth.write_session_file(cfg, {"mhrs": {"jwt": "olu.jwt.imza", "exp": 9e9}})

    code, cfg = _run_main(monkeypatch, tmp_path, raw=f"{_AUTH}={_VALUE}", alive=True)

    assert code == 0
    assert auth.read_session_file(cfg).get("mhrs", {}).get("jwt") == "olu.jwt.imza"


def test_main_rejects_bad_input_without_touching_disk(monkeypatch, tmp_path):
    code, cfg = _run_main(monkeypatch, tmp_path, raw="SAGLIK00beef=waf-sentetik", alive=True)
    assert code == 2
    assert not cfg.session_path.exists()


def test_main_refuses_non_interactive_stdin(monkeypatch, tmp_path):
    """Sır boru/dosyadan okunmaz — kabuk geçmişine ve süreç listesine düşmesin."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(Config, "from_env", classmethod(lambda _c: cfg))
    monkeypatch.setattr(imp.sys.stdin, "isatty", lambda: False)

    def _boom(_prompt):
        raise AssertionError("etkileşimsiz girdide sır istenmemeliydi")

    monkeypatch.setattr(imp.getpass, "getpass", _boom)
    assert imp.main() == 2
    assert not cfg.session_path.exists()
