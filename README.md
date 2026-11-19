# PIT-Audit ENTSO-E toplayıcısı

Her 15 dakikada ENTSO-E Transparency API'sini sorgular. Her veri serisinin
**ilk görüldüğü anı** ve sonraki **revizyonlarını** kaydeder. Amaç, gün öncesi tahminlerin SDAC
kapanışından (D-1 12:00 Brüksel) önce gerçekten erişilebilir olup olmadığını ölçmek.

- **Bölgeler:** DE_LU, FR, NL, BE, DK1, ES
- **Değişkenler:** gün öncesi yük tahmini (6.1.B), gün öncesi toplam üretim tahmini (14.1.C),
  rüzgâr-güneş tahmini (gün öncesi, gün içi ve güncel; 14.1.D), gün öncesi fiyat (12.1.D, kontrol amaçlı)
- **Teslimat günleri:** her çalıştırmada bugün ve yarın (Brüksel saati)
- **Bir çalıştırma:** 72 istek, yaklaşık 1–2 dakika

Aynı kod iki yerde çalışır. Her toplayıcı kendi klasörüne yazar, bu yüzden birbiriyle çakışmaz:

| Toplayıcı | `COLLECTOR_ID` | Zamanlama | Veri klasörü |
|---|---|---|---|
| GitHub Actions | `gha` | :00, :15, :30, :45 | `data/gha/` |
| Yerel bilgisayar / sunucu | `kku-local` | :07, :22, :37, :52 | `data/kku-local/` |

İkisi birlikte çalıştığında zaman çözünürlüğü yaklaşık 7,5 dakikaya iner. Biri kesilirse diğeri devam eder.

---

## 1) GitHub Actions kurulumu (yaklaşık 15 dk)

1. GitHub'da yeni bir depo açın, örneğin `pit-entsoe-collector`.
   - **Herkese açık (public) olsun.** Public depolarda Actions dakikaları ücretsiz ve sınırsız.
   - Bu iş günde yaklaşık 96 çalıştırma yapar, ayda birkaç bin dakika eder. Private depoların ücretsiz kotası buna yetmez.
   - Toplanan veri zaten herkese açık ENTSO-E verisi. API anahtarı depoda görünmez, gizli değişken olarak saklanır.
2. Bu klasörün tüm içeriğini depoya yükleyin. `.github/workflows/collect.yml` dosyası da dahil olmalı.
   GitHub web arayüzünde "Add file → Upload files" ile sürükleyip bırakabilirsiniz.
   `.github` klasörü gizli olduğu için görünmüyorsa, `collect.yml` dosyasını
   "Add file → Create new file" ile `.github/workflows/collect.yml` adıyla oluşturup içeriğini yapıştırın.
3. Depoda **Settings → Secrets and variables → Actions → New repository secret** yolunu izleyin:
   - Name: `ENTSOE_API_KEY`
   - Secret: ENTSO-E API anahtarınız
4. **Settings → Actions → General → Workflow permissions** bölümünde **Read and write permissions** seçin ve kaydedin.
5. **Actions** sekmesine gidin. İş akışlarını etkinleştirmeniz istenirse onaylayın. Ardından
   **entsoe-pit-collector → Run workflow** ile bir kez elle çalıştırın.
   Birkaç dakika içinde `data/gha/` klasörü ve "poll ..." adlı bir commit görünmeli.

Bundan sonra iş her 15 dakikada kendiliğinden çalışır. Bilgisayarınızın açık olmasına gerek yoktur.

**Kontrol:** `data/gha/heartbeat/YYYY-MM.csv` dosyasında her çalıştırma için bir satır olmalı.
`n_error` sütunu sürekli 0'dan büyük çıkıyorsa haber verin.

---

## 2) Yerel bilgisayar / sunucu kurulumu (isteğe bağlı, yedek)

Python 3.10 veya üstü gerekir.

```bash
cd pit-entsoe-collector
python -m pip install -r requirements.txt
```

### Linux / macOS (cron)

`crontab -e` komutuyla açılan dosyaya şu satırı ekleyin (yolları kendinize göre düzeltin):

```
7,22,37,52 * * * * cd /home/KULLANICI/pit-entsoe-collector && ENTSOE_API_KEY=ANAHTAR COLLECTOR_ID=kku-local /usr/bin/python3 collector.py >> collector.log 2>&1
```

### Windows (Görev Zamanlayıcı)

1. `run_local.bat` dosyasını açın ve `ENTSOE_API_KEY` satırına anahtarınızı yazın.
2. Yönetici olarak açılmış bir komut isteminde şunu çalıştırın (klasör yolunu kendinize göre düzeltin):

```
schtasks /Create /TN "PIT-Collector" /TR "C:\pit-entsoe-collector\run_local.bat" /SC MINUTE /MO 15 /ST 00:07 /F
```

3. Uyku modunu kapatın (Ayarlar → Sistem → Güç → Uyku: Hiçbir zaman). Windows Update'te
   "etkin saatler" ayarını yapın, böylece otomatik yeniden başlatma gece yarısına kayar.

Yerel veri `data/kku-local/` klasöründe birikir. Analiz zamanı geldiğinde bu klasörü zipleyip gönderin.
GitHub deposuna push etmeniz gerekmez.

---

## Çıktılar

```
data/<toplayıcı>/events/YYYY-MM.csv      ilk görülme / revizyon / kaybolma olayları
data/<toplayıcı>/snapshots/<var>/<bölge>/<teslimat günü>/<zaman>.csv.gz   her sürümün değerleri
data/<toplayıcı>/heartbeat/YYYY-MM.csv   her çalıştırmanın kaydı (kesintisizlik kanıtı)
data/<toplayıcı>/errors/YYYY-MM.csv      API ve ağ hataları
state/<toplayıcı>.json                   son görülen sürümler (iç kullanım)
```

`events` dosyasındaki `prev_poll_utc` sütunu, veri yokken yapılan son sorgunun zamanıdır.
Yani yayım zamanı `(prev_poll_utc, poll_utc]` aralığındadır.

**Özet rapor:**

```bash
python first_seen_report.py --gate 11:45
```

Bu komut her bölge ve değişken için verinin kapanıştan önce mi sonra mı ilk görüldüğünü
ve kapanıştan sonraki revizyon sayısını özetler. Sonuç `reports/first_seen.csv` dosyasına yazılır.

**Not:** Toplama başlamadan önce zaten yayımlanmış olan günler "censored" olarak işaretlenir.
Yarının verisi için geçerli ölçüm, toplamanın başladığı ilk tam günden itibaren başlar.
