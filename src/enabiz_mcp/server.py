"""E-Nabız MCP sunucusu (yerel stdio).

Kimlik doğrulama tool'ları (iki-adımlı SMS OTP girişi + oturum durumu) ve salt-okunur
veri tool'ları (tahliller, reçeteler, raporlar, ilaçlar, radyoloji, randevular; klinik
çekirdek: alerjiler, tanılar, kronik takip, aşılar, epikrizler, patoloji).
"""

from __future__ import annotations

from fastmcp import FastMCP

from . import auth
from .config import Config
from .tools import (
    administrative,
    allergies,
    appointments,
    chronic_followups,
    diagnoses,
    discharge_summaries,
    download,
    hospital_visits,
    labs,
    medications,
    mhrs_appointments,
    mhrs_booking,
    mhrs_requests,
    mhrs_search,
    mhrs_slots,
    pathology,
    prescription_types,
    prescriptions,
    profile,
    radiology,
    reports,
    summary,
    vaccinations,
)

mcp: FastMCP = FastMCP(
    name="enabiz",
    instructions=(
        "E-Nabız kişisel sağlık verisine erişim için yerel MCP sunucusu. "
        "Giriş insan-döngüdedir: önce enabiz_login_start ile SMS tetiklenir, "
        "kullanıcının telefonuna gelen kod enabiz_login_verify ile girilir. "
        "Oturum yerel olarak saklanır ve süresi dolana dek yeniden kullanılır. "
        "Tüm veri tool'ları salt-okunurdur."
    ),
)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
def enabiz_session_status() -> dict:
    """Mevcut E-Nabız oturum ve yapılandırma durumunu döndürür.

    `authenticated`, oturumun sunucuda GERÇEKTEN geçerli olduğunu tek bir hafif
    istekle doğrular. `false` ise `enabiz_login_start` → `enabiz_login_verify`;
    o yol tanınmayan bir akış koduyla tıkanıyorsa yedek olarak kullanıcı kendi
    terminalinde `uvx --from enabiz-mcp enabiz-import-session` çalıştırabilir.
    """
    cfg = Config.from_env()
    return {
        "session_file": str(cfg.session_path),
        # Dosya var demek girişli demek DEĞİL — oturum sunucu tarafında ölmüş olabilir.
        "session_exists": cfg.session_path.exists(),
        "authenticated": auth.session_alive(cfg),
        "credentials_configured": cfg.credentials_configured,
    }


@mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
def enabiz_login_start() -> dict:
    """E-Nabız girişini başlatır (adım 1/2).

    `.env`'deki kimlik bilgilerini doğrular ve kayıtlı telefona **SMS onay kodu**
    gönderilmesini tetikler. Dönen `step == "sms_required"` ise, kullanıcının
    telefonuna gelen kodu `enabiz_login_verify` ile girin.
    """
    cfg = Config.from_env()
    return auth.login_start(cfg)


@mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
def enabiz_login_verify(otp_code: str) -> dict:
    """E-Nabız girişini tamamlar (adım 2/2).

    Telefona gelen SMS onay kodunu (`otp_code`) gönderir. Başarılıysa kimlikli
    oturum yerel dosyaya kaydedilir ve sonraki çağrılarda yeniden kullanılır.
    Önce `enabiz_login_start` çağrılmış olmalıdır.
    """
    cfg = Config.from_env()
    return auth.login_verify(cfg, otp_code)


# Veri tool'larını kaydet — alan bazlı gruplanır. (Sıra eskiden geliştirme fazlarını
# yansıtıyordu: prescription_types, prescriptions'tan 16 satır uzaktaydı çünkü sonra
# yazılmıştı. Faz numaraları kod değil tarih; roadmap docs/STATUS.md'de.)
summary.register(mcp)  # alanlar arası derleyici — genellikle ilk çağrılan

# Klinik çekirdek
allergies.register(mcp)
diagnoses.register(mcp)
chronic_followups.register(mcp)
vaccinations.register(mcp)
labs.register(mcp)
pathology.register(mcp)

# Reçete ve ilaç
prescriptions.register(mcp)
prescription_types.register(mcp)
medications.register(mcp)

# Ziyaret, rapor, görüntüleme
hospital_visits.register(mcp)
discharge_summaries.register(mcp)
reports.register(mcp)
radiology.register(mcp)
appointments.register(mcp)
profile.register(mcp)

# MHRS (randevu sistemi) — e-Nabız'dan AYRI bir API; SSO ile devredilir.
# `appointments` e-Nabız'ın HTML tablosunu okur, bunlar MHRS API'sini: farkı `hrn`
# (hasta randevu numarası), tabloda yok, iptalin anahtarı. Şu an hepsi salt-okunur.
mhrs_search.register(mcp)
mhrs_appointments.register(mcp)
mhrs_slots.register(mcp)
mhrs_requests.register(mcp)
# YAZMA — projenin tek yazma yüzeyi (login dışında). D7.
mhrs_booking.register(mcp)

# İdari + belge indirme
administrative.register(mcp)
download.register(mcp)


def main() -> None:
    """Konsol giriş noktası (`enabiz-mcp`). stdio üzerinden çalışır."""
    mcp.run()


if __name__ == "__main__":
    main()
