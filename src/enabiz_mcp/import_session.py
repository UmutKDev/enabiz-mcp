"""Tarayıcı oturumunu içe aktarma — girişin yedek yolu.

Betikle giriş bir yerde takılırsa (reCAPTCHA, anti-otomasyon ya da
`GetSmsOnayKontrol`'ün tanınmayan bir akış kodu döndürmesi — bkz.
`docs/findings/auth-flow.md`), kullanıcı tarayıcıda **normal şekilde** giriş yapar ve
kimlikli cookie'yi buraya verir. MCP sunucusu onu yalnızca salt-okunur veri
çağrılarında kullanır.

Hiçbir güvenlik kontrolü atlatılmaz (invaryant #4): giriş yine insan-döngüdedir,
reCAPTCHA ve SMS OTP yine portalın kendi sayfasında, kullanıcının tarayıcısında
çözülür. Buraya gelen şey o girişin SONUCUDUR, kontrolün kendisi değil.

**Sır sohbete de panoya da değil, gizli isteme girer.** Değer `getpass` ile alınır:
ekrana basılmaz, `argv`'ye ve kabuk geçmişine düşmez, LLM bağlamına HİÇ girmez
(invaryant #3). Aynı gerekçe `mhrs/auth.py`'de JWT için de yazılı — iptal edilemez bir
bearer'ın panoya düşmesi korunan tek yüzeydir. Bu yüzden bilerek **ne bir MCP tool
argümanı, ne de bir ortam değişkeni** sunulur: ikisi de sırrı bir LLM bağlamına ya da
diskte duran bir istemci yapılandırmasına yazardı.

Kullanım (repo gerekmez):

    uvx --from enabiz-mcp enabiz-import-session
"""

from __future__ import annotations

import getpass
import http.cookiejar
import re
import sys

import httpx

from . import auth
from .config import Config

#: Girdide hiç `ad=değer` çifti yoksa değerin bu cookie'ye ait olduğu varsayılır —
#: DevTools'ta "Copy value" yalnız değeri verir, adı vermez.
DEFAULT_COOKIE_NAME = auth.AUTH_COOKIE_HINTS[0]

#: RFC 6265 token karakterleri. Baştaki nokta (`.EnabizSESSIONID`) portalın kendi
#: adlandırması olduğu için kabul edilir.
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

_HEADER_PREFIX_RE = re.compile(r"^cookie\s*:\s*", re.IGNORECASE)


class CookieImportError(ValueError):
    """Yapıştırılan değer okunamadı. Mesajı kullanıcıya gösterilir — DEĞER İÇERMEZ."""


def parse_cookie_header(raw: str) -> list[tuple[str, str]]:
    """`"a=1; b=2"` → `[("a", "1"), ("b", "2")]`.

    `Cookie:` öneki, satır sonları ve fazladan boşluk tolere edilir.

    Girdi tek parçaysa ve `ad=değer` şeklinde DEĞİLSE, tamamı
    `DEFAULT_COOKIE_NAME`'in değeri sayılır. Ayrım `=` var mı diye bakarak
    YAPILMAZ: base64 değerleri `==` ile biter (`abc==`), `partition("=")` onu
    `ad="abc"`, `değer="="` diye böler ve sessizce YANLIŞ bir cookie yazardı —
    gerçek değerin sonu kırpılmış hâlde. Bu yüzden `=` solundaki metnin geçerli bir
    cookie ADI olması şartı aranır.
    """
    text = _HEADER_PREFIX_RE.sub("", raw.strip())
    if not text:
        raise CookieImportError("Boş girdi.")

    parts = [p.strip() for p in text.split(";")]
    parts = [p for p in parts if p]
    if not parts:
        raise CookieImportError("Boş girdi.")

    pairs: list[tuple[str, str]] = []
    for part in parts:
        name, sep, value = part.partition("=")
        name = name.strip()
        if sep and _COOKIE_NAME_RE.match(name):
            pairs.append((name, value.strip()))
        elif len(parts) == 1:
            # Çıplak değer (DevTools "Copy value"): adı biz veriyoruz.
            return [(DEFAULT_COOKIE_NAME, part)]
        else:
            raise CookieImportError(
                f"{len(parts)} parçadan biri `ad=değer` biçiminde değil. "
                "Ya tek bir cookie DEĞERİ yapıştırın, ya da tam `Cookie:` başlığını."
            )
    return pairs


def cookies_from_pairs(pairs: list[tuple[str, str]], domain: str) -> httpx.Cookies:
    """Çiftleri portalın alan adına bağlı bir cookie jar'ına çevirir."""
    jar = httpx.Cookies(http.cookiejar.CookieJar())
    for name, value in pairs:
        jar.set(name, value, domain=domain, path="/")
    return jar


def build_session_cookies(raw: str, domain: str) -> httpx.Cookies:
    """Yapıştırılan metni doğrulanmış bir cookie jar'ına çevirir (ağ YOK).

    Kimlik cookie'si yoksa patlar: WAF cookie'sini (`SAGLIK…`) tek başına kaydetmek
    "oturum var" görüntüsü verip her veri çağrısını login sayfasına düşürürdü.
    """
    pairs = parse_cookie_header(raw)
    if any(not value for _, value in pairs):
        raise CookieImportError("Cookie değeri boş.")

    cookies = cookies_from_pairs(pairs, domain)
    if not auth.has_auth_cookie(cookies):
        # ADLARI basmak güvenli, DEĞERLERİ asla.
        got = ", ".join(name for name, _ in pairs)
        expected = ", ".join(auth.AUTH_COOKIE_HINTS)
        raise CookieImportError(
            f"Kimlik cookie'si yok. Okunan adlar: {got}. Beklenen adlardan biri: {expected}."
        )
    return cookies


_PROMPT = """\
E-Nabız oturumu içe aktarma (yedek giriş yolu)
──────────────────────────────────────────────
1. Tarayıcıda https://enabiz.gov.tr adresine NORMAL şekilde giriş yapın.
2. DevTools (F12) → Application → Cookies → https://enabiz.gov.tr
3. `{cookie}` satırındaki DEĞERİ kopyalayın.
   (Tam `Cookie:` başlığını yapıştırmanız da olur — hepsi saklanır.)

Girdi ekrana BASILMAZ, kabuk geçmişine ve komut satırına düşmez.
"""


def main() -> int:
    """Etkileşimli içe aktarma. Çıkış kodu: 0 başarı, 1 oturum geçersiz, 2 girdi hatası."""
    cfg = Config.from_env()
    domain = httpx.URL(cfg.base_url).host

    if not sys.stdin.isatty():
        print(
            "HATA: bu komut etkileşimli bir terminal gerektirir — sır boru/dosyadan "
            "okunmaz (kabuk geçmişine ve süreç listesine düşmesin diye).",
            file=sys.stderr,
        )
        return 2

    print(_PROMPT.format(cookie=DEFAULT_COOKIE_NAME))
    try:
        raw = getpass.getpass("Cookie (girdi gizli): ")
    except (EOFError, KeyboardInterrupt):
        print("\nİptal edildi.", file=sys.stderr)
        return 2

    try:
        cookies = build_session_cookies(raw, domain)
    except CookieImportError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2

    print("Oturum portalda doğrulanıyor…")
    if not auth.cookies_alive(cfg, cookies):
        print(
            "HATA: bu oturum portalda geçerli değil. Mevcut oturum dosyasına "
            "DOKUNULMADI.\n"
            "  · Değeri eksiksiz kopyaladınız mı? (uzun bir dizedir)\n"
            "  · Tarayıcıda hâlâ girişli misiniz? Çıkış yapmak oturumu sunucuda öldürür.",
            file=sys.stderr,
        )
        return 1

    auth.save_session(cfg, cookies)
    print(f"✓ Oturum kaydedildi (chmod 600): {cfg.session_path}")
    print(
        "\nNOT: E-Nabız oturumu SUNUCU tarafında kısa ömürlüdür (~30-60 dk) ve\n"
        "yenilenemez. Süresi dolunca tool'lar `auth_required` döner; bu komutu\n"
        "tekrar çalıştırın. MCP sunucusu bu dosyayı okuduğu için yeniden başlatmanız\n"
        "gerekmez."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
