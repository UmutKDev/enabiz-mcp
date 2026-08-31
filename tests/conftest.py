"""Testler için paylaşılan yardımcılar.

`FX = Path(__file__).parent / "fixtures"` 21 dosyada birebir tekrar ediyordu;
`scripts/` paket olmadığı için importlib makinesi de 2 dosyada kopyalanmıştı.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import httpx

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(name: str) -> str:
    """Sentetik fixture'ı okur (PHI yok). `.html` uzantısı isteğe bağlı."""
    if not name.endswith(".html"):
        name += ".html"
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_text(name: str) -> str:
    """Sentetik fixture'ı uzantısını TAHMİN ETMEDEN okur (PHI yok).

    `fixture_html` eksik uzantıyı `.html` sanar; MHRS fixture'ları `.js`/`.json`.
    """
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_script(name: str) -> ModuleType:
    """`scripts/` altındaki bir betiği dosya yolundan yükler (paket değil)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def use_mock_transport(
    client: httpx.Client, handler: Callable[[httpx.Request], httpx.Response]
) -> httpx.Client:
    """`client`'ı ağdan KOPARIR; tüm istekleri `handler` karşılar.

    `client._transport = MockTransport(...)` **tek başına yetmez.** Ortamda
    `HTTPS_PROXY`/`HTTP_PROXY` varsa `httpx.Client` (`trust_env=True`, varsayılan)
    proxy transport'larını `_mounts`'a kurar ve `_transport_for_url` mount'u
    `_transport`'tan ÖNCE seçer — mock SESSİZCE devre dışı kalır ve test gerçek ağa
    çıkar. Ölçüldü: proxy'li bir ortamda `test_mhrs_auth.py`'nin 4 testi canlı
    `prd.mhrs.gov.tr`'ye gidip `ProxyError` ile düştü; proxy'siz makinede aynı testler
    fark edilmeden GERÇEK istek atıyordu. Suite'in "ağsız" invaryantı (CLAUDE.md #5)
    `_mounts` da boşaltılmadan tutmaz.
    """
    client._transport = httpx.MockTransport(handler)
    client._mounts = {}
    return client
