"""Kimlik doğrulama: XSRF token alma, SMS OTP akışı ve oturum kalıcılığı.

İki-aşamalı giriş (bkz. docs/findings/auth-flow.md — canlı olarak doğrulandı):
  1. get_antiforgery()  → GET /Account/Login, __RequestVerificationToken kazı.
  2. login_start()      → POST /Account/GetSmsOnayKontrol {TCKimlikNo, Sifre}
                          → "22" = kimlik doğru, SMS onayına geç (SMS gönderilir)
                          → "87" = işlem hatası
  3. login_verify(otp)  → POST /Account/GetSmsOnayGirisYap {tc, onayKodu}
                          → "1"  = giriş başarılı (auth cookie set edilir)
                          → "2"  = SMS kodu eşleşmiyor

Adım 2 ile 3 arasında antiforgery cookie+token korunmalı. MCP sunucusu uzun-ömürlü
olduğundan süreç-içi `_pending` yeterli; ancak süreç yeniden başlarsa diye repo-dışı
`~/.config/enabiz-mcp/pending.json`'a da kalıcılaştırılır. reCAPTCHA/SMS ATLATILMAZ.
"""

from __future__ import annotations

import http.cookiejar
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .client import build_client, xhr_post
from .config import Config

LOGIN_PATH = "/Account/Login"
HOME_PATH = "/Home/Index"  # kimlikli olduğunu doğrulamak için

# GetSmsOnayKontrol yanıt kodları (login sayfası JS'inden).
CHECK_2FA_REQUIRED = "22"  # kimlik doğru → iki-aşamalı SMS onayına geç
CHECK_ERROR = "87"         # işlem sırasında hata
#
# ⚠️ Bu liste TAM DEĞİL. 2026-08-31'de canlıda `77` görüldü (HTTP 200,
# `application/json`, gövde `"77"`); anlamı BİLİNMİYOR ve buraya bir tahminle
# EKLENMEZ — sessiz yanlış-eşleme boş sonuçtan kötüdür (CLAUDE.md #2). Tanınmayan
# kod `step="unknown"` + `hint` ile modele/kullanıcıya olduğu gibi taşınır.
# Bkz. docs/findings/auth-flow.md "Bilinmeyen yanıt kodları".

# GetSmsOnayGirisYap yanıt kodları.
LOGIN_OK = "1"          # giriş başarılı
LOGIN_WRONG_CODE = "2"  # SMS onay kodu eşleşmiyor

# Kimlikli oturum cookie'lerinin olası adları (canlı: `.EnabizSESSIONID`).
AUTH_COOKIE_HINTS = (
    ".EnabizSESSIONID",
    ".AspNet.ApplicationCookie",
    ".AspNetCore.Cookies",
    ".ASPXAUTH",
)


# --------------------------------------------------------------------------- #
# Geçici (login_start → login_verify) durum
# --------------------------------------------------------------------------- #
@dataclass
class _Pending:
    client: httpx.Client
    token: str


_pending: _Pending | None = None


def _pending_path(cfg: Config) -> Path:
    return cfg.session_path.parent / "pending.json"


def _cookies_to_list(cookies: httpx.Cookies) -> list[dict]:
    return [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
        for c in cookies.jar
    ]


def _cookies_from_list(items: list[dict]) -> httpx.Cookies:
    cookies = httpx.Cookies(http.cookiejar.CookieJar())
    for c in items:
        cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
    return cookies


def _save_pending(cfg: Config, cookies: httpx.Cookies, token: str) -> None:
    path = _pending_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cookies": _cookies_to_list(cookies), "token": token}),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _load_pending(cfg: Config) -> _Pending | None:
    path = _pending_path(cfg)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    client = build_client(cfg, cookies=_cookies_from_list(data["cookies"]))
    return _Pending(client=client, token=data["token"])


def _clear_pending(cfg: Config) -> None:
    _pending_path(cfg).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# XSRF
# --------------------------------------------------------------------------- #
def get_antiforgery(client: httpx.Client) -> str:
    """`GET /Account/Login` yapar (cookie jar'ı doldurur) ve gizli
    `__RequestVerificationToken` değerini döndürür."""
    r = client.get(LOGIN_PATH)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    el = soup.select_one('input[name="__RequestVerificationToken"]')
    value = el.get("value") if el else None
    if not value:
        raise RuntimeError(
            "Antiforgery token bulunamadı — login sayfasının yapısı değişmiş olabilir."
        )
    return value


# --------------------------------------------------------------------------- #
# Oturum kalıcılığı
# --------------------------------------------------------------------------- #
def read_session_file(cfg: Config) -> dict:
    """Oturum dosyasının HAM sözlüğünü döndürür (yoksa/bozuksa `{}`).

    Dosya artık birden çok yazıcı tarafından paylaşılıyor: e-Nabız cookie'leri
    (`"cookies"`) ve MHRS JWT'si (`"mhrs"`). Bozuk JSON'da patlamak yerine `{}`
    dönmek doğru: kayıp oturum yeniden girişle çözülür, ama exception tüm tool'ları
    kilitler.
    """
    path = cfg.session_path
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_session_file(cfg: Config, data: dict) -> None:
    """Oturum sözlüğünü sıkı izinli yazar (chmod 600). Tüm yazıcılar bunu kullanır."""
    path = cfg.session_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)


def save_session(cfg: Config, cookies: httpx.Cookies) -> None:
    """Kimlikli cookie'leri sıkı izinli yerel dosyaya yazar (chmod 600).

    **Read-modify-write**: dosyayı ezmez. Eskiden `{"cookies": ...}` ile tüm dosyayı
    overwrite ediyordu; MHRS JWT'si kardeş anahtar olarak yanına yerleşince bu iki
    yazıcının birbirini silmesi demek olurdu (e-Nabız'a her girişte MHRS token'ı
    uçardı).
    """
    data = read_session_file(cfg)
    data["cookies"] = _cookies_to_list(cookies)
    write_session_file(cfg, data)


def load_session(cfg: Config) -> httpx.Cookies | None:
    """Kaydedilmiş oturumu yükler; yoksa None."""
    if not cfg.session_path.exists():
        return None
    return _cookies_from_list(read_session_file(cfg).get("cookies", []))


def has_auth_cookie(cookies: httpx.Cookies) -> bool:
    """Kimlik cookie'si ADEN var mı — geçerli olduğunu GÖSTERMEZ.

    Yalnız isim kesişimidir: süresi dolmuş ya da sunucuda öldürülmüş bir oturum için
    de True döner. "Gerçekten girişli miyim?" sorusunun cevabı `session_alive`.
    """
    names = {c.name for c in cookies.jar}
    return any(hint in names for hint in AUTH_COOKIE_HINTS)


def session_alive(cfg: Config) -> bool:
    """Kayıtlı oturumun sunucuda HÂLÂ geçerli olduğunu canlı bir GET ile doğrular.

    E-Nabız oturumu SUNUCU tarafında ölür (~30-60 dk); cookie yerel olarak dururken
    çoktan geçersiz olabilir. Bu yüzden tek doğru yöntem çekip login formuna
    yönlendik mi diye bakmaktır — hiçbir yerel `Expires` değeri bunu bilemez.
    """
    cookies = load_session(cfg)
    if not cookies or not has_auth_cookie(cookies):
        return False
    try:
        with session_scope(cfg) as client:
            return 'name="TCKimlikNo"' not in client.get(HOME_PATH).text
    except Exception:  # noqa: BLE001 — ağ/oturum hatası = canlı değil
        return False


class AuthRequired(RuntimeError):
    """Kimlikli oturum yok veya süresi dolmuş."""


def authed_client(cfg: Config) -> httpx.Client:
    """Kaydedilmiş kimlikli oturumla bir istemci döndürür; oturum yoksa AuthRequired."""
    cookies = load_session(cfg)
    if not cookies or not has_auth_cookie(cookies):
        raise AuthRequired(
            "Kimlikli oturum bulunamadı. Önce enabiz_login_start / enabiz_login_verify "
            "ile giriş yapın."
        )
    return build_client(cfg, cookies=cookies)


def scrape_token(client: httpx.Client, path: str) -> str:
    """Kimlikli bir sayfadan `__RequestVerificationToken` değerini kazır.

    Yanıt login sayfasına yönlendirilmişse oturum düşmüştür → AuthRequired.
    """
    r = client.get(path)
    html = r.text
    if 'name="TCKimlikNo"' in html:  # login formu → kimliksiz
        raise AuthRequired("Oturum düşmüş görünüyor (login sayfasına yönlendirildi).")
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one('input[name="__RequestVerificationToken"]')
    value = el.get("value") if el else None
    if not value:
        raise AuthRequired("Antiforgery token alınamadı (oturum geçersiz olabilir).")
    return value


@contextmanager
def session_scope(cfg: Config) -> Iterator[httpx.Client]:
    """Kimlikli istemciyi context manager olarak verir; çıkışta kapatır.

    Oturum yok/geçersizse `AuthRequired` fırlatır. Veri tool'larında tekrarı
    önlemek için `auth_guarded` ile birlikte kullanılır:

        with session_scope(cfg) as client:
            token = scrape_token(client, page)
            ...
    """
    client = authed_client(cfg)
    try:
        yield client
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# Giriş akışı (iki adımlı)
# --------------------------------------------------------------------------- #
def login_start(cfg: Config) -> dict:
    """Adım 1: kimlik bilgileriyle GetSmsOnayKontrol'e POST eder.

    "22" dönerse kimlik doğrudur ve SMS gönderilmiştir; kullanıcı kodu
    `login_verify` ile girmelidir. Antiforgery oturumu korunur.
    """
    global _pending
    if not cfg.credentials_configured:
        raise RuntimeError("ENABIZ_TCKIMLIK / ENABIZ_SIFRE ayarlı değil (.env).")

    client = build_client(cfg)
    token = get_antiforgery(client)
    r = xhr_post(
        client,
        "/Account/GetSmsOnayKontrol",
        token,
        {"TCKimlikNo": cfg.tc_kimlik_no, "Sifre": cfg.sifre},
    )
    _pending = _Pending(client=client, token=token)
    _save_pending(cfg, client.cookies, token)

    body = (r.text or "").strip()
    info = _describe(r)
    info["code"] = body  # portalın ham akış kodu — model buna göre dallanabilir
    if body == CHECK_2FA_REQUIRED:
        info["step"] = "sms_required"
        info["message"] = "Kimlik doğrulandı. Telefonunuza gelen SMS kodunu login_verify ile girin."
    elif body == CHECK_ERROR:
        info["step"] = "error"
        info["message"] = "İşlem sırasında hata (87). Kimlik bilgilerini kontrol edin."
    else:
        # Kodun anlamını UYDURMA. Kesin bilinen tek şey: `22` değil, yani portal SMS
        # adımına geçmedi ve giriş bu çağrıyla ilerlemedi. "SMS gitti mi" bilinmiyor.
        info["step"] = "unknown"
        info["message"] = (
            f"Portal bu sürümün tanımadığı bir akış kodu döndü: {body!r}. Giriş ilerlemedi."
        )
        info["hint"] = (
            "SMS gönderilip gönderilmediği BİLİNMİYOR — enabiz_login_verify ÇAĞIRMAYIN. "
            "Aynı kimlik bilgileriyle tarayıcıdan enabiz.gov.tr'ye girin ve portalın "
            "gösterdiği mesajı okuyun: şifre süresi dolmuş, hesap kilitli, onay bekleyen "
            "bir sözleşme/KVKK adımı veya telefon doğrulaması gibi durumların hepsi bu "
            "koda düşebilir. Girişi arka arkaya DENEMEYİN — başarısız denemeler hesabı "
            "kilitleyebilir."
        )
    return info


def login_verify(cfg: Config, otp_code: str) -> dict:
    """Adım 2: SMS OTP kodunu GetSmsOnayGirisYap'a gönderir; başarılıysa oturumu kaydeder."""
    global _pending
    pending = _pending or _load_pending(cfg)
    if pending is None:
        raise RuntimeError("Önce login_start() çağrılmalı (geçici oturum yok).")

    r = xhr_post(
        pending.client,
        "/Account/GetSmsOnayGirisYap",
        pending.token,
        {"tc": cfg.tc_kimlik_no, "onayKodu": otp_code.strip()},
    )
    info = _describe(r)
    body = (r.text or "").strip()

    if body == LOGIN_OK:
        # Auth cookie bu yanıtla set edilmiş olmalı; kimlikli ana sayfayla doğrula.
        home = pending.client.get(HOME_PATH)
        info["home_status"] = home.status_code
        info["has_auth_cookie"] = has_auth_cookie(pending.client.cookies)
        save_session(cfg, pending.client.cookies)
        _clear_pending(cfg)
        _pending = None
        info["step"] = "logged_in"
        info["session_saved"] = True
        info["message"] = "Giriş başarılı, oturum kaydedildi."
    elif body == LOGIN_WRONG_CODE:
        info["step"] = "wrong_code"
        info["message"] = "SMS onay kodu eşleşmiyor. Tekrar deneyin."
    else:
        info["step"] = "unknown"
        info["message"] = f"Beklenmeyen yanıt: {body!r}."
    return info


def _describe(r: httpx.Response) -> dict:
    """Yanıtı keşif/hata için özetler (ham PHI içermez, gövde kırpılır)."""
    body = r.text or ""
    return {
        "status_code": r.status_code,
        "content_type": r.headers.get("content-type", ""),
        "body_preview": body[:800],
        "set_cookie_names": [c.name for c in r.cookies.jar],
    }
