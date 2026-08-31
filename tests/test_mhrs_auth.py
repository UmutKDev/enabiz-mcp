"""MHRS auth — SSO devir zinciri, JWT saklama, zarf açma (ağ yok).

En kritik iki invaryant:
1. **`allow_write=False` client'ı yazma ucuna GİDEMEZ** — çalışma zamanı kapısı.
2. **Oturum dosyası iki yazıcıyı taşır** — e-Nabız'a giriş MHRS token'ını silmez.
"""

from __future__ import annotations

import base64
import json
import stat
import time

import httpx
import pytest
from conftest import use_mock_transport

from enabiz_mcp import auth as enabiz_auth
from enabiz_mcp.config import Config
from enabiz_mcp.mhrs import auth as mhrs_auth
from enabiz_mcp.mhrs.client import WriteNotAllowed, api_client, unwrap

_TOKEN = "00000000-1111-2222-3333-444444444444"  # SENTETİK — uydurma uuid


def _cfg(tmp_path) -> Config:
    return Config(
        tc_kimlik_no=None,
        sifre=None,
        session_path=tmp_path / "session.json",
        min_interval=0.0,
        mhrs_min_interval=0.0,
    )


def _jwt(exp: float) -> str:
    """SENTETİK JWT — imzasız, yalnız `exp` okunabilsin diye."""

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS512'})}.{b64({'exp': exp, 'sub': 'fake'})}.c2ln"


def _envelope(data: dict) -> dict:
    return {
        "lang": "tr-TR", "success": True, "infos": [], "warnings": [], "errors": [],
        "data": data,
    }


def _error(kodu: str, mesaj: str = "hata") -> dict:
    return {"lang": "tr-TR", "success": False, "infos": [], "warnings": [],
            "errors": [{"kodu": kodu, "mesaj": mesaj}], "data": None}


# --------------------------------------------------------------------------- #
# Yazma kapısı — çalışma zamanı invaryantı
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "kurum/randevu/randevu-ekle"),
        ("GET", "kurum/randevu/iptal-et/123"),  # GET ama YAZMA
        ("GET", "kurum/randevu/geri-al/9"),  # GET ile YAZAR
        ("DELETE", "kurum/randevu/slot-kilitleme"),
    ],
)
def test_readonly_client_refuses_write_endpoints(tmp_path, method, path):
    """Varsayılan client yazma ucuna GİDEMEZ — istek ağa hiç çıkmaz."""
    c = api_client(_cfg(tmp_path), "jwt")
    use_mock_transport(c, lambda r: pytest.fail(f"yazma ucuna istek gitti: {r.url}"))  # noqa: ARG005
    with pytest.raises(WriteNotAllowed):
        c.request(method, path)


def test_readonly_client_allows_read_endpoints(tmp_path):
    c = api_client(_cfg(tmp_path), "jwt")
    use_mock_transport(c, lambda r: httpx.Response(200, json=_envelope({"ok": 1})))
    assert c.get("kurum/randevu/yaklasan-randevularim").status_code == 200


def test_allow_write_opens_the_gate(tmp_path):
    """Kapı yasak değil, NİYET beyanı — randevu tool'ları bunu açıkça geçer."""
    c = api_client(_cfg(tmp_path), "jwt", allow_write=True)
    use_mock_transport(c, lambda r: httpx.Response(200, json=_envelope({"hrn": "X"})))
    assert c.post("kurum/randevu/randevu-ekle", json={}).status_code == 200


def test_api_client_sends_bearer(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_envelope({}))

    c = api_client(_cfg(tmp_path), "TOK123")
    use_mock_transport(c, handler)
    c.get("vatandas/dil")
    assert captured["auth"] == "Bearer TOK123"


# --------------------------------------------------------------------------- #
# Zarf
# --------------------------------------------------------------------------- #
def test_unwrap_returns_data():
    r = httpx.Response(200, json=_envelope({"jwt": "x"}))
    assert unwrap(r) == {"jwt": "x"}


@pytest.mark.parametrize("kodu", ["RNDS1010", "RNDS1000"])
def test_unwrap_rate_limit_codes_are_not_generic_errors(kodu):
    """Aşırı-sorgu kodları kendi tipini alır — çağıran retry ETMEMELİ diye ayırt edilsin.

    `RNDS1010` bundle'da DOĞRULANMIŞ koddur (17 çağrı yeri); `RNDS1000` doğrulanmadı
    ama savunma amaçlı tutulur. Bir tur koruma YALNIZ `RNDS1000`'e bağlıydı — yani
    bundle'da hiç geçmeyen bir koda — ve `RNDS1010` jenerik `MhrsError`'a düşüyordu:
    "TEKRAR DENEMEYİN" ipucu modele ulaşmıyor, model retry edebiliyordu. Korumanın
    tam olarak engellemek için var olduğu senaryo.
    """
    with pytest.raises(mhrs_auth.MhrsRateLimited) as exc:
        unwrap(httpx.Response(200, json=_error(kodu, "çok fazla sorgulama")))
    assert exc.value.kodu == kodu
    assert "TEKRARLANMAYACAK" in str(exc.value)


def test_rate_limit_codes_include_the_one_actually_in_the_bundle():
    """`RNDS1010` listede OLMALI — kanıtı olan tek kod odur.

    Kapsam daralmasına karşı çıplak bir çapa: biri listeyi "sadeleştirip" bundle'da
    doğrulanmış kodu çıkarırsa burada patlar.
    """
    from enabiz_mcp.mhrs.client import RATE_LIMIT_CODES

    assert "RNDS1010" in RATE_LIMIT_CODES


@pytest.mark.parametrize("kodu", ["LGN1004", "LGN2001"])
def test_unwrap_session_codes_raise_auth_required(kodu):
    with pytest.raises(mhrs_auth.MhrsAuthRequired):
        unwrap(httpx.Response(200, json=_error(kodu)))


def test_unwrap_success_false_with_http_200_still_raises():
    """`success` HTTP durumundan BAĞIMSIZ — 200 ile hata dönebilir."""
    with pytest.raises(mhrs_auth.MhrsError):
        unwrap(httpx.Response(200, json=_error("RND4105", "slot dolu")))


def test_unwrap_401_raises_auth_required():
    with pytest.raises(mhrs_auth.MhrsAuthRequired):
        unwrap(httpx.Response(401, text="nope"))


def test_unwrap_non_json_raises_mhrs_error():
    with pytest.raises(mhrs_auth.MhrsError):
        unwrap(httpx.Response(200, text="<html>WAF</html>"))


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def test_jwt_expiry_reads_exp():
    assert mhrs_auth.jwt_expiry(_jwt(1784181826.0)) == 1784181826.0


@pytest.mark.parametrize("bad", ["", "a.b", "not-a-jwt", "a.!!!.c", "a." + "eyJ" + ".c"])
def test_jwt_expiry_returns_none_on_garbage(bad):
    """Bozuk token'da patlama — None dön (çağıran zinciri yeniden koşturur)."""
    assert mhrs_auth.jwt_expiry(bad) is None


def test_session_expired_uses_skew():
    """Süre dolmadan ÖNCE yenile — uçuş süresi + saat kayması payı."""
    assert not mhrs_auth.MhrsSession("j", time.time() + 3600).expired
    assert mhrs_auth.MhrsSession("j", time.time() + 30).expired  # skew penceresi içinde
    assert mhrs_auth.MhrsSession("j", time.time() - 1).expired


# --------------------------------------------------------------------------- #
# Oturum kalıcılığı — iki yazıcı, tek dosya
# --------------------------------------------------------------------------- #
def test_saving_enabiz_session_does_not_clobber_mhrs_token(tmp_path):
    """En kritik regresyon: e-Nabız'a her girişte MHRS token'ı UÇMAMALI.

    `save_session` eskiden `{"cookies": ...}` ile TÜM dosyayı overwrite ediyordu.
    """
    cfg = _cfg(tmp_path)
    exp = time.time() + 999
    mhrs_auth.save_mhrs_session(cfg, mhrs_auth.MhrsSession(_jwt(exp), exp))

    cookies = httpx.Cookies()
    cookies.set(".EnabizSESSIONID", "abc", domain="enabiz.gov.tr")
    enabiz_auth.save_session(cfg, cookies)  # e-Nabız yazıcısı

    assert mhrs_auth.load_mhrs_session(cfg) is not None, "MHRS token'ı silindi!"
    assert enabiz_auth.load_session(cfg) is not None


def test_saving_mhrs_session_does_not_clobber_cookies(tmp_path):
    """Ters yön: MHRS yazıcısı e-Nabız cookie'lerini silmemeli."""
    cfg = _cfg(tmp_path)
    cookies = httpx.Cookies()
    cookies.set(".EnabizSESSIONID", "abc", domain="enabiz.gov.tr")
    enabiz_auth.save_session(cfg, cookies)

    mhrs_auth.save_mhrs_session(cfg, mhrs_auth.MhrsSession("j", 1.0))

    loaded = enabiz_auth.load_session(cfg)
    assert loaded is not None and ".EnabizSESSIONID" in {c.name for c in loaded.jar}


def test_session_file_is_chmod_600(tmp_path):
    cfg = _cfg(tmp_path)
    mhrs_auth.save_mhrs_session(cfg, mhrs_auth.MhrsSession("j", 1.0))
    assert stat.S_IMODE(cfg.session_path.stat().st_mode) == 0o600


def test_clear_mhrs_session_keeps_cookies(tmp_path):
    cfg = _cfg(tmp_path)
    cookies = httpx.Cookies()
    cookies.set(".EnabizSESSIONID", "abc", domain="enabiz.gov.tr")
    enabiz_auth.save_session(cfg, cookies)
    mhrs_auth.save_mhrs_session(cfg, mhrs_auth.MhrsSession("j", 1.0))

    mhrs_auth.clear_mhrs_session(cfg)

    assert mhrs_auth.load_mhrs_session(cfg) is None
    assert enabiz_auth.load_session(cfg) is not None


def test_corrupt_session_file_returns_empty_not_crash(tmp_path):
    """Bozuk JSON tüm tool'ları kilitlememeli — kayıp oturum yeniden girişle çözülür."""
    cfg = _cfg(tmp_path)
    cfg.session_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.session_path.write_text("{bozuk", encoding="utf-8")
    assert enabiz_auth.read_session_file(cfg) == {}
    assert mhrs_auth.load_mhrs_session(cfg) is None


@pytest.mark.parametrize("raw", ['{"mhrs": "düz-string"}', '{"mhrs": {"jwt": 5}}', '{"mhrs": {}}'])
def test_malformed_mhrs_entry_returns_none(tmp_path, raw):
    cfg = _cfg(tmp_path)
    cfg.session_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.session_path.write_text(raw, encoding="utf-8")
    assert mhrs_auth.load_mhrs_session(cfg) is None


# --------------------------------------------------------------------------- #
# SSO zinciri
# --------------------------------------------------------------------------- #
# CANLI sayfanın GERÇEK şekli (SENTETİK id'lerle). Bir tur bu fixture
# `<a href="/Randevu/RandevuAl?ID=99999999&vasiOnay=False">` idi — yani kullanıcının
# yakaladığı curl'ün, İSTEĞİN şekli. O dizi sayfada HİÇ GEÇMİYOR; kaynak `onclick`
# handler'ı. Fixture yanlış varsayımı kodladığı için test yeşil kalırken canlı zincir
# ilk adımda kırılıyordu — fixture'ı gerçeğe çekmek bu yüzden testin kendisi kadar
# önemli.
_RANDEVULARIM = """<html>
  <table><tr><td>MHRS</td><td>
    <a href="javascript:linkAlTekrar('11111111','False','34','111111','123','456','78');"
       class="rtekrarlaBtn">Randevuyu Tekrarla</a>
  </td></tr></table>
  <div class="modalBaslikKucuk">Randevu Alınacak Kişi</div>
  <ul class="randevuAlListe">
    <li><a onclick="linkAl('99999999','False')"><span>Kendim</span></a></li>
  </ul>
  <script>
    function linkAl(id, vasiOnay) {
      $.ajax({ data: { ID: id, vasiOnay : vasiOnay }, url: "/Randevu/RandevuAl", type: "GET" });
    }
    function linkAlTekrar(id, vasiOnay, mhrsIlId) {
      $.ajax({ url: "/Randevu/RandevuAlTekrar", type: "GET" });
    }
  </script>
</html>"""
_MINT_BODY = f"https://prd.mhrs.gov.tr/vatandas/#/?enabizToken={_TOKEN}&lang=tr-TR"


def test_scrape_person_id_ignores_randevu_tekrarla_link():
    """`linkAlTekrar` aynı önekle başlar ama BAŞKA bir uca gider — kazınmamalı.

    Canlı sayfada 5 kez geçiyor ve `/Randevu/RandevuAlTekrar`'a gidiyor. Regex
    `linkAl`'dan hemen sonra `(` istemezse bu satırlardaki id kişi id'si sanılırdı.
    """
    c = httpx.Client(
        base_url="https://enabiz.gov.tr",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=_RANDEVULARIM)),
    )
    assert mhrs_auth.scrape_person_id(c) == "99999999"  # 11111111 DEĞİL


def test_scrape_person_id_refuses_when_multiple_non_vasi_people():
    """Birden fazla vasi-olmayan kişide sessizce ilkini SEÇME — hata ver.

    Belirsizlikte randevu YANLIŞ KİŞİYE yazılabilir; boş/hata dönmek daha ucuz.
    """
    html = (
        "<ul class='randevuAlListe'>"
        "<li><a onclick=\"linkAl('11111111','False')\">A</a></li>"
        "<li><a onclick=\"linkAl('22222222','False')\">B</a></li></ul>"
    )
    c = httpx.Client(
        base_url="https://enabiz.gov.tr",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)),
    )
    with pytest.raises(mhrs_auth.MhrsError, match="birden fazla"):
        mhrs_auth.scrape_person_id(c)


def test_scrape_person_id_skips_vasi_entries():
    """`vasiOnay=True` başkası adına randevudur — kapsam dışı, seçilmez."""
    html = (
        "<ul class='randevuAlListe'>"
        "<li><a onclick=\"linkAl('77777777','True')\">Vasi olduğum kişi</a></li>"
        "<li><a onclick=\"linkAl('99999999','False')\">Kendim</a></li></ul>"
    )
    c = httpx.Client(
        base_url="https://enabiz.gov.tr",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)),
    )
    assert mhrs_auth.scrape_person_id(c) == "99999999"


def test_scrape_person_id_and_mint_token():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Home/Randevularim":
            return httpx.Response(200, text=_RANDEVULARIM)
        if request.url.path == "/Randevu/RandevuAl":
            assert request.url.params["ID"] == "99999999"
            assert request.url.params["vasiOnay"] == "False"
            return httpx.Response(200, text=_MINT_BODY)
        return httpx.Response(404)

    c = httpx.Client(base_url="https://enabiz.gov.tr", transport=httpx.MockTransport(handler))
    assert mhrs_auth.scrape_person_id(c) == "99999999"
    assert mhrs_auth.mint_enabiz_token(c) == _TOKEN


def test_mint_raises_auth_required_when_session_dropped():
    """Portal login'e 200 ile yönlendirir — status değil, HTML sentinel'i bakılır."""
    c = httpx.Client(
        base_url="https://enabiz.gov.tr",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text='<input name="TCKimlikNo"/>')
        ),
    )
    with pytest.raises(enabiz_auth.AuthRequired):
        mhrs_auth.scrape_person_id(c)


def test_scrape_person_id_raises_when_link_missing():
    """ID uydurma — sayfa yapısı değiştiyse dürüstçe patla (invaryant #2)."""
    c = httpx.Client(
        base_url="https://enabiz.gov.tr",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>bomboş</html>")),
    )
    with pytest.raises(mhrs_auth.MhrsError):
        mhrs_auth.scrape_person_id(c)


def test_exchange_for_jwt_sends_correct_body(tmp_path, monkeypatch):
    captured = {}
    exp = time.time() + 72000

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_envelope({"jwt": _jwt(exp), "refreshToken": None}))

    from enabiz_mcp.mhrs import client as mhrs_client

    real = mhrs_client.anon_api_client

    def fake(cfg):
        return use_mock_transport(real(cfg), handler)

    monkeypatch.setattr(mhrs_client, "anon_api_client", fake)

    session = mhrs_auth.exchange_for_jwt(_cfg(tmp_path), _TOKEN)

    assert captured["path"] == "/api/vatandas/enabiz/login"
    assert captured["body"] == {"enabizToken": _TOKEN, "islemKanali": "VATANDAS_ENABIZ"}
    assert session.exp == pytest.approx(exp)
    assert not session.expired
