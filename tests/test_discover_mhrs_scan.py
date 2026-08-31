"""`scripts/discover_mhrs.py` entegrasyon testi — ağ yok (httpx.MockTransport).

Tüm hattı doğrular: index.html → main.js → chunk şablonu → chunk aralık taraması →
çıkarım → rapor.

En kritik iki invaryant:
1. **`/api/`'ye ASLA istek atılmaz** — mock handler çağrılırsa test patlar.
   (`test_discover_scan.py:44-45`'in MHRS karşılığı.)
2. **Chunk boşluğu taramayı durdurmaz** — canlıda id 44 yok ama 45..70 var; ilk
   boşlukta duran bir tarayıcı randevu API'sinin çoğunu kaçırırdı.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import fixture_html, fixture_text, load_script, use_mock_transport

from enabiz_mcp.config import Config
from enabiz_mcp.mhrs import discovery
from enabiz_mcp.mhrs.client import ApiBoundaryViolation, bundle_client

discover_mhrs = load_script("discover_mhrs")

_INDEX = fixture_html("mhrs_index_sample")
_MAIN = fixture_text("mhrs_bundle_sample.js")
_CHUNK = 'x.a.get("kurum/randevu/slot-sorgulama/randevu-bilgileri?fkSlotId=".concat(e));'

# Canlıdaki gerçek şekil: 44 YOK ama 45 VAR — boşluk taramayı durdurmamalı.
_EXISTING_CHUNKS = {0, 1, 45}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/api/"):
        raise AssertionError(f"/api/ ucu çağrıldı ({path}) — keşif kimliksiz olmalı!")
    if path == "/vatandas/":
        return httpx.Response(200, text=_INDEX)
    if path == "/vatandas/vatandas-main.js":
        return httpx.Response(200, text=_MAIN, headers={"content-type": "application/javascript"})
    if path.startswith("/vatandas/vatandas-") and path.endswith("-chunk.js"):
        cid = int(path.split("vatandas-")[-1].split("-chunk")[0])
        if cid in _EXISTING_CHUNKS:
            return httpx.Response(
                200, text=_CHUNK, headers={"content-type": "application/javascript"}
            )
        return httpx.Response(302, headers={"location": "/vatandas/"})  # yok → 302 (404 değil)
    return httpx.Response(404)


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(
        base_url="https://prd.mhrs.gov.tr", transport=httpx.MockTransport(_handler)
    )


@pytest.fixture
def sources(client, tmp_path):
    src, meta = discover_mhrs.fetch_bundle_sources(
        client, max_chunk_id=50, out_dir=tmp_path, save_raw=False
    )
    return src, meta


# --------------------------------------------------------------------------- #
# Hat
# --------------------------------------------------------------------------- #
def test_fetches_main_and_chunks(sources):
    src, meta = sources
    assert meta["build_version"] == "9.9.999"
    assert meta["chunks_found"] == len(_EXISTING_CHUNKS)
    assert [n for n, _ in src] == ["main", "chunk-0", "chunk-1", "chunk-45"]


def test_chunk_gap_does_not_stop_scan(sources):
    """Canlıda 44 yok ama 45 var — ilk boşlukta duran tarayıcı 45'i kaçırırdı."""
    src, _ = sources
    assert "chunk-45" in [n for n, _ in src]


def test_collect_calls_dedupes_across_sources(sources):
    """Aynı uç hem main'de hem 3 chunk'ta geçer → raporda TEK satır olmalı."""
    src, _ = sources
    shared = "kurum/randevu/slot-sorgulama/randevu-bilgileri?fkSlotId={p1}"

    # Önce dedup ÖNCESİ gerçekten birden fazla kaynakta olduğunu doğrula — yoksa
    # bu test hiçbir şey kanıtlamaz (boşlukta geçen bir assert'ten beteri yok).
    raw = [c for name, js in src for c in discovery.extract_calls(js, name) if c.path == shared]
    assert len(raw) > 1, "fixture bu ucu tek kaynakta veriyor — dedup test edilemiyor"

    keys = [(c.method, c.path) for c in discover_mhrs.collect_calls(src)]
    assert len(keys) == len(set(keys))
    assert keys.count(("GET", shared)) == 1


def test_report_lists_writes_without_calling_them(sources):
    src, meta = sources
    calls = discover_mhrs.collect_calls(src)
    rows = [
        discover_mhrs.MhrsReportRow(
            method=c.method, path=c.path, verdict=c.verdict, source=c.source
        )
        for c in calls
    ]
    out = discovery.build_mhrs_report(rows, meta)
    assert "kurum/randevu/iptal-et/{p1}" in out
    assert "ASLA çağrılmaz" in out


def test_raw_is_saved_chmod_600(client, tmp_path):
    discover_mhrs.fetch_bundle_sources(client, max_chunk_id=1, out_dir=tmp_path, save_raw=True)
    import stat

    main = tmp_path / "vatandas-main.js"
    assert main.exists()
    assert stat.S_IMODE(main.stat().st_mode) == 0o600


def test_missing_chunk_template_exits_rather_than_partial(tmp_path):
    """Şablon yoksa SESSİZCE devam etme — chunk'sız harita randevu API'sini kaçırır.

    İnvaryant #2'nin keşif hâli: eksik harita, boş haritadan kötüdür.
    """

    def bare(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/vatandas/":
            return httpx.Response(200, text=_INDEX)
        return httpx.Response(
            200, text="var x=1;", headers={"content-type": "application/javascript"}
        )

    c = httpx.Client(base_url="https://prd.mhrs.gov.tr", transport=httpx.MockTransport(bare))
    with pytest.raises(SystemExit):
        discover_mhrs.fetch_bundle_sources(c, max_chunk_id=1, out_dir=tmp_path, save_raw=False)


# --------------------------------------------------------------------------- #
# /api/ sınırı — mekanik koruma
# --------------------------------------------------------------------------- #
def test_bundle_client_refuses_api_path():
    """Bundle client'ı `/api/`'ye gitmeyi REDDEDER — yorum değil, invaryant."""
    cfg = Config(tc_kimlik_no=None, sifre=None, session_path=None, min_interval=0.0)
    c = bundle_client(cfg)
    use_mock_transport(c, _handler)
    with pytest.raises(ApiBoundaryViolation):
        c.get("/api/vatandas/dil")


def test_bundle_client_carries_no_credentials():
    """Kimliksiz: cookie yok, Authorization yok."""
    cfg = Config(tc_kimlik_no="x", sifre="y", session_path=None, min_interval=0.0)
    c = bundle_client(cfg)
    assert len(c.cookies.jar) == 0
    assert "authorization" not in {k.lower() for k in c.headers}
