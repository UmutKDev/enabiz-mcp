# Bulgu: Endpoint kataloğu (canlı)

> Kimlikli oturumla keşfedildikçe genişletilir. Kaynak: login sayfası JS'inden
> statik grep (Faz 0) + dinamik network capture (Faz 2). Method/param/yanıt
> sütunları doğrulandıkça doldurulur. `?` = tahmin, doğrulanmadı.

## Legend
- **Auth:** `public` (girişsiz) · `xsrf` (antiforgery gerekli) · `session` (kimlikli oturum gerekli)
- **Durum:** `keşfedildi` (biliniyor) · `doğrulandı` (istek/yanıt görüldü) · `tool` (MCP tool'u yazıldı)

---

## Kimlik doğrulama / hesap

| Endpoint | Method | Params | Auth | Yanıt | Durum |
|---|---|---|---|---|---|
| `/Account/Login` | GET | — | public | HTML (antiforgery token + cookie) | doğrulandı |
| `/Account/GetSmsOnayKontrol` | POST | `TCKimlikNo`, `Sifre` | xsrf | `22`=OK→SMS · `87`=hata · `77`=**anlamı bilinmiyor** (2026-08-31 canlı) | **doğrulandı** (kod listesi eksik) |
| `/Account/GetSmsOnayGirisYap` | POST | `tc`, `onayKodu` | xsrf | `1`=giriş OK (`.EnabizSESSIONID`) · `2`=kod yanlış | **doğrulandı** |
| `/Account/Logout` · `/Account/LogoutWebSessions` | GET | — | session | çıkış | keşfedildi |
| `/Account/SmsGonderimKontrol` · `/Account/SMSOnayi` | POST | (ayrı telefon/şifre-SMS akışı, login değil) | xsrf | — | keşfedildi |
| `/Account/SifremiUnuttum` | POST? | ? | xsrf | ? | keşfedildi |
| `/Account/RitmikGiris` | ? | ? | ? | ? (alternatif giriş?) | keşfedildi |
| `/Account/SetLanguage` | POST? | dil | public | ? | keşfedildi |
| `/Account/Cookies` | GET? | — | public | çerez politikası | keşfedildi |
| `/Account/GetIlceler` | POST? | il | public | ilçe listesi | keşfedildi |

## e-Devlet / SSO (bu proje kapsamı dışı — kişisel şifre girişi kullanılıyor)

| Endpoint | Not |
|---|---|
| `/Account/LoginEDevlet`, `/Account/SSOLogin`, `/Account/LoginEDevletAileHekimi` | e-Devlet kapısı üzerinden giriş |

## Public araçlar (kimliksiz)

| Endpoint | Not |
|---|---|
| `/Account/KalpKriziHesapla`, `/Account/KalpKriziAyrintiliHesapla` | Kalp krizi risk hesaplayıcı |
| `/NobetciEczane/GetNobetciEczaneIlceler` | Nöbetçi eczane ilçeleri |
| `/Yardim/SecimleHastaneleriGetirAccount` | Hastane seçim listesi |

---

## Giriş sonrası — veri sayfaları (kimlikli menüden keşfedildi ✅)

Her veri kategorisi bir `/Home/<Ad>` sayfasıdır; veriler sayfa içi AJAX ile yüklenir.

| Alan | Sayfa | Veri AJAX endpoint'i | Durum |
|---|---|---|---|
| Ana sayfa | `/Home/Index` | `/Home/GetNeyimVarLogin`, `/Home/GetASMBilgileri`, `/Home/Favoriler` | doğrulandı |
| **Tahliller** | `/Home/Tahlillerim` | **`/Tahlil/Index`** (POST) | **tool** |
| **Randevular** | `/Home/Randevularim` | sayfa-içi tablo `#tblRandevuListesi` (salt-okunur) | **tool** |
| **Reçeteler** | `/Home/Recetelerim` | **`/Recete/Index`** (POST) + `/Recete/GetReceteDetay` | **tool** |
| **İlaçlar** | `/Home/Ilaclarim` | **`/Ilac/Index`** (POST) | **tool** |
| **Raporlar** | `/Home/Raporlarim` | **`/Rapor/Index`** (POST) | **tool** |
| **Radyolojik görüntüler** | `/Home/RadyolojikGoruntulerim` | sayfa-içi kartlar + **`/RadyolojikGoruntu/GetRaporByOrder`** | **tool** |
| Alerjiler | `/Home/Alerjilerim` | sayfa-içi tablo `#tblAlerjilerim` (AJAX yok) | **doğrulandı** |
| Hastalıklar | `/Home/Hastaliklarim` | sayfa `#tblHastaliklarim` (34 satır); `/Hastalik/Index` POST `{baslangicYili,bitisYili}` | **doğrulandı** |
| Hastalık takip | `/Home/HastalikTakip` | sayfa `#tblHastalikTakip`; `/HastalikTakip/detay` POST (paramsız) | **doğrulandı** |
| Aşı takvimi | `/Home/AsiTakvimi` | sayfa-içi tablo `#tblAsilar` (AJAX yok) | **doğrulandı** |
| Epikrizler | `/Home/Epikrizlerim` | **`/Epikriz/Index`** POST `{baslangicYil,bitisYil}` → `#tblEpikriz` (+ sayfada da render) | **doğrulandı** |
| Patolojiler | `/Home/Patolojilerim` | sayfa `#tblPatoloji`; `/Patoloji/Index` POST `{baslangicYili,bitisYili}` | **doğrulandı** |
| Ziyaretler | `/Home/Ziyaretlerim` | **`/Ziyaret/Index`** POST `{baslangicYil,bitisYil}` → `#dvZiyaretDetay` (~360 KB) | **doğrulandı** |
| Son hastane ziyareti | `/Home/Index` | **`/HastaneZiyaret/SonHastaneZiyareti`** POST `{__RequestVerificationToken}` (token GÖVDEDE) → `#HastaneZiyaret` | **doğrulandı** |
| Dokümanlar | `/Home/Dokumanlarim` | sayfa `#tblDokumanlarim`; detay `/Dokuman/DokumanDetayGetir` POST `{bakId}` | **doğrulandı** (sayfa) |
| Malzeme/cihaz | `/Home/MalzemeveCihazlarim` | sayfa-içi tablo `#tblMalzemeVeCihazlarimDiger` | **doğrulandı** |
| Sigortalar | `/Home/Sigortalarim` | sayfa-içi tablo `#tblSigorta` | **doğrulandı** |
| Profil | `/Home/ProfilBilgilerim` | veri SAYFADA (~185 KB); `/Profil/KullaniciBilgileriniDoldur` GET boş döner (4 B) — kaynak değil | **doğrulandı** (sayfa) |
| Acil durum notları | `/Home/AcilDurumNotlarim` | sayfa-içi tablo `#tblAcilDurum` | **doğrulandı** |
| Organ bağışı (durum) | `/Home/OrganBagisBeyan` | veri sayfada; `/OrganBagisBeyan/TamamlanmamisEskiOrganBagisiVarMi` POST → salt-okunur durum bayrağı | **doğrulandı** |

> **Keşif taraması (2026-07-13, `scripts/discover.py`):** 14 sayfa · 124 uç · 21 okuma
> replay · 103 atlandı (tüm yazma uçları güvenle atlandı). PHI-güvenli döküm:
> [`discovery-report.md`](discovery-report.md). Ham yakalamalar `raw/` (gitignored).
>
> **Desen:** Çoğu klinik alan **sunucu-render** — veri doğrudan `/Home/<Ad>` sayfasında
> (`#tbl<Ad>` tablosu), ayrı AJAX yok → tool'lar **randevu/radyoloji GET-sayfa desenini**
> izler. Epikriz & Ziyaret ayrıca yıl-filtreli `POST /<Alan>/Index` sunar.
>
> **Net-new not:** "Yakınlar/bağımlılar" için ayrı bir okuma-liste ucu YOK — yalnız
> profil-değiştir / şikâyet (`/Profil/CocuklarimiGoremiyorum`) ve organ-bağışı yakın
> doğrulama var. `enabiz_list_relatives` şimdilik **ertelendi** (uç yok).
> "Kan grubu" & "ölçümler" (boy/kilo/VKİ) ProfilBilgilerim sayfasında render ediliyor
> (`KullaniciBilgileriniDoldur` boş döndüğü için sayfadan parse edilecek).

### Tahlil alt-endpoint'leri (doğrulandı)
| Endpoint | Method | Params | Yanıt |
|---|---|---|---|
| `/Tahlil/Index` | POST | `baslangicTarihi`(yıl), `bitisTarihi`(yıl), `activeTab`(0/1) | HTML accordion partial |
| `/Tahlil/TahlillerRapor` | GET | `IslemTipi` | HTML (test trend/karşılaştırma) |
| `/Tahlil/TahlillerPdf` | GET | `dil`, `tarih`, `kurumkodu` | PDF |
| `/Tahlil/Covid` | POST | ? | HTML (covid testleri) |
| `/Tahlil/IslemRedGoruntule` | GET | `tarih`, `takipNo`, `islemKodu`, `islemAdi`, `kurum` | HTML detay |

### Reçete alt-endpoint'leri (doğrulandı)
| Endpoint | Method | Params | Yanıt |
|---|---|---|---|
| `/Recete/Index` | POST | `baslangicYil`, `bitisYil` | HTML tablo `#tbl-recetelerim` |
| `/Recete/GetReceteDetay` | POST | `?data={"SYSTakipNo":..,"ReceteNo":..}` | HTML: yazılan + satılan ilaç tabloları |
| `/Recete/GetOptikReceteler` · `/Recete/GetTibbiCihazReceteler` | POST | ? | optik / tıbbi cihaz reçeteleri |
| `/Recete/GetIlacProspektusBilgisi` · `/Recete/GetKareKodDetay` | POST | ? | ilaç prospektüs / karekod |

### Rapor alt-endpoint'i (doğrulandı)
| Endpoint | Method | Params | Yanıt |
|---|---|---|---|
| `/Rapor/Index` | POST | `startYear`, `endYear` | HTML tablo `#tblRaporlarim` (self-contained) |

> **⚠️ Yıl parametreleri endpoint'e göre DEĞİŞİR** — her tool'da ayrı ayrı doğrulanmalı:
> - Tahlil: `baslangicTarihi` / `bitisTarihi`
> - Reçete: `baslangicYil` / `bitisYil`
> - Rapor: `startYear` / `endYear`
> - İlaç: `baslangicYil` / `bitisYil` (reçete ile aynı)
> - Epikriz / Ziyaret: `baslangicYil` / `bitisYil`
> - **Hastalik / Patoloji: `baslangicYili` / `bitisYili`** ← YENİ varyant (sonu "i")

### İlaç alt-endpoint'i (doğrulandı)
| Endpoint | Method | Params | Yanıt |
|---|---|---|---|
| `/Ilac/Index` | POST | `baslangicYil`, `bitisYil` | HTML tablo `#tblIlaclarim` (13 kolon, self-contained) |

### Radyoloji alt-endpoint'leri (doğrulandı)
| Endpoint | Method | Params | Yanıt |
|---|---|---|---|
| `/Home/RadyolojikGoruntulerim` | GET | — | sayfa-içi `.radyolojiCardListe` kartları (tablo/`/Index` YOK) |
| `/RadyolojikGoruntu/GetRaporByOrder` | POST | `orderId` (kart onclick token'ı) | HTML radyoloji raporu |
| `/RadyolojikGoruntu/GetGoruntuLinkByOrder` | GET/POST | `orderId` | DICOM görüntüleyici linki (henüz tool değil) |
| `/RadyolojikGoruntu/GetRaporPdfByOrder` | GET | `orderId` | rapor PDF (henüz tool değil) |

> **Not:** Radyoloji, `RadyolojiApp` inline JS ile yönetilir; `Utils.request(url, {orderId})`
> ile çağrılır. Kartlar sayfada render edilir (canlıda kayıt gözlendi); yıl-filtresi
> UI select'te bağlıdır — liste tool'u sayfadaki kayıtları döndürür.

## Alt alan adları

| Host | Amaç |
|---|---|
| `enabiz.gov.tr` | Ana kişisel portal (hedef) |
| `enabiz.saglik.gov.tr` | Ayna? |
| `auth.enabiz.gov.tr` | e-İmza girişi |
| `recetem.enabiz.gov.tr` | Reçetem bilgi sistemi |
| `yonetim.enabiz.gov.tr` | Ortak giriş noktası |
| `rehber.enabiz.gov.tr` | Kurumsal entegrasyon (HL7/FHIR) — kişisel portaldan ayrı |
