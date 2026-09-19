# Proje Durumu

Son güncelleme: 2026-09-19

## Nerede

Giriş (XSRF + SMS OTP) → oturum kalıcılığı → kimlikli veri çekme → HTML parse →
yapılandırılmış MCP çıktısı. **47 tool / 20 veri alanı**. Sağlık verisi salt-okunur;
yazma yalnız MHRS randevu alma/iptalinde (iki adımlı onaylı, D7).
Tüm alanlar canlı portala karşı doğrulandı — MHRS okuma tool'ları dahil.

| Alan | Durum |
|---|---|
| Kimlik doğrulama (XSRF + SMS OTP, oturum kalıcılığı) | 🟡 otomatik giriş tıkalı — `GetSmsOnayKontrol` tanınmayan `77` döndürüyor (anlamı açık) |
| Yedek giriş: tarayıcı oturumu içe aktarma (`enabiz-import-session`) | 🟢 uygulandı — sır `getpass` ile, doğrulanmadan kaydedilmez |
| Endpoint keşfi (14 sayfa · 124 uç · 21 okuma) | 🟢 tamam — `findings/discovery-report.md` |
| Klinik: tahlil, tanı, alerji, aşı, kronik takip, epikriz, patoloji | 🟢 canlı |
| Reçete/ilaç: reçete + detay, optik, cihaz, ilaç, prospektüs, kullanım geçmişi | 🟢 canlı |
| Ziyaret + profil + rapor + radyoloji + randevu | 🟢 canlı |
| İdari: sigorta, malzeme/cihaz, acil notlar | 🟢 parser'lar doğru (bu hesapta 0 kayıt) |
| Sağlık özeti (alanlar arası derleyici) | 🟢 canlı |
| Belge indirme (lab · patoloji · epikriz · radyoloji PDF) | 🟢 canlı |
| MHRS bundle keşfi (161 uç · 75 okuma) | 🟢 tamam — `findings/mhrs-discovery-report.md` |
| MHRS auth (e-Nabız SSO devri → JWT) | 🟢 canlı doğrulandı |
| MHRS okuma: il/ilçe/klinik + yaklaşan/geçmiş randevu | 🟢 canlı doğrulandı |
| MHRS slot arama (`kurum-rss/…/arama` + `/slot`) | 🟢 canlı doğrulandı |
| MHRS randevu talebi (yer açılınca haber ver) | 🟢 canlı doğrulandı |
| MHRS tekrar randevu (`rebook_criteria`) | 🟢 canlı doğrulandı |
| MHRS randevu alma/iptal (iki-adımlı onay) | 🟢 canlı doğrulandı (alındı + iptal edildi) |
| Canlı LLM-tool eval koşucusu | ⚪ yok (opsiyonel, ağır altyapı) |

**Canlı doğrulama:** tüm alanlar gerçek bir hesapta çalıştırıldı; parser'lar dolu
veri döndürdü. Kayıt sayıları burada YAZILMAZ — kardinalite de PHI'dir
(bkz. `tests/test_no_cardinality.py`).

## Tool yüzeyi (47)

Oturum (3) · sağlık özeti (1) · liste (18) · detay (9) · `enabiz_download_document` (1)
· MHRS okuma (10) · MHRS YAZMA (6: randevu 4 + talep 2). Veri alanı = 18 liste ucu + profil +
MHRS randevu = **20**. Tam liste: [README](../README.md#toollar-47).

MHRS tool'ları `enabiz_mhrs_*` ile ayrılır: `prd.mhrs.gov.tr` e-Nabız'dan AYRI bir
sistemdir ve SSO ile devredilir. `enabiz_list_appointments` e-Nabız'ın HTML
tablosunu okur; MHRS tool'ları API'yi okur ve `hrn` döndürür — tabloda yok,
iptalin anahtarı.

**Yazma yüzeyi:** 47 tool'un 8'i `readOnlyHint: False` — 2 login + 4 MHRS randevu
(`book_prepare`, `book_confirm`, `book_cancel_prepare`, `cancel`). `book_prepare`
randevu almaz ama `False`, çünkü MHRS o çağrıda slotu kilitliyor olabilir.
Çalışma-zamanı kapısı: `api_client` varsayılan olarak yazma sınıfı bir uca gitmeyi
`WriteNotAllowed` ile reddeder; `allow_write=True` açık niyet beyanıdır.

Liste tool'ları `limit` (varsayılan 50, `0` = sınırsız) alır ve
`count`/`total`/`truncated` döndürür. `enabiz_list_lab_tests`'te sınır TEST
sonucuna uygulanır — token yükü iç içe `results`'tadır (bir rapor çok sayıda test
taşır; sınırsız çağrı model bağlamını doldurabilir).

## Çekirdek garantiler

- **Salt-okunur:** hiçbir tool yazma ucuna dokunmaz (`test_discover_scan.py` yazma
  ucu çağrılırsa testi patlatır).
- **Gizlilik:** kimlik bilgileri yalnız `.env`; PDF'ler diske (chmod 600), içerik
  LLM'e verilmez — yalnız `{saved_path, byte_size, sha256, content_type}`.
  Ham portal HTML'i (`docs/findings/raw/`) gitignore'da.
- **Hız sınırı:** `_Throttle` süreç-genelinde paylaşılır (tool çağrıları arasında da
  geçerli), `threading.Lock` ile korunur.
- **Oturum düştüğünde:** `auth_required` + yeniden giriş yönergesi (tool çökmez).
- **Türkçe:** filtreler `_text.tr_lower` kullanır (`İ`→`i`, `I`→`ı`); düz `.lower()`
  portal verisi TÜMÜ BÜYÜK HARF olduğu için sessiz yanlış-negatif üretiyordu.

**208 birim testi yeşil** (ağ yok, sentetik fixture). `uv run ruff check` temiz.

## Bilinen sınırlar / ertelenenler

- **Organ bağışı durumu** ⏸ — temiz status yapısı yok (`OrganBagisBeyanOnayDetay`
  write/AJAX ile yükleniyor).
- **Cihaz reçete detayı** ⏸ — bu hesapta cihaz reçetesi yok, yapı yakalanamıyor.
- **COVID sekmesi** ❌ düşürüldü — `activeTab=1` ayrı yapı döndürmüyor (normal
  accordion ile birebir aynı; covid sonuçları `enabiz_list_lab_tests`'te).
- **Canlı LLM-tool eval koşucusu** ⚪ — `evals/` iskeleti kaldırıldı (hiçbir şey
  koşturmuyordu ve `test_parsers.py` assertion'larını elle kopyalıyordu). Gerçek
  koşucu yazılınca geri eklenir.

## Kullanıcı katılımı

Giriş SMS OTP gerektirir (insan-döngüde). Oturum sunucu tarafında ~30-60 dk sonra
ölür; `enabiz_session_status` canlı probe ile gerçek durumu söyler.

## Karar geçmişi

Bkz. [`notes/decisions.md`](notes/decisions.md) · bulgular: [`findings/`](findings/)
(endpoint kataloğu, veri modelleri, auth akışı — geri-mühendislik kaydı).
