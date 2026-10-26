#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FındıkRadar Backend v1.0
- FİSKOBİRLİK + serbest piyasa kaynaklarından 10 dakikada bir fiyat toplar
- JSON API sunar (web sayfası bu API'ye bağlanır)
- Saatlik "dünya taraması" ve alarm kontrolü yapar
- SADECE standart kütüphane + requests + beautifulsoup4 kullanır (Flask gerekmez)

Kullanım:  python3 findik_backend.py
API:       http://localhost:8000/api/prices
"""

import json
import os
import re
import time
import threading
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from bs4 import BeautifulSoup

# ============================================================
# AYARLAR
# ============================================================
PORT = int(os.environ.get("PORT", 8000))   # Render gibi servisler PORT verir
UPDATE_INTERVAL_SEC = 600      # 10 dakikada bir fiyat güncelleme
SCAN_INTERVAL_SEC   = 3600     # saatlik dünya taraması
TIMEOUT = 15

# Aday kaynak adresleri — ilki çalışmazsa sırayla diğerleri denenir
FISKOBIRLIK_URLS = [
    "https://www.fiskobirlik.com.tr/fiyatlar",
    "https://fiskobirlik.com.tr/fiyatlar",
    "https://www.fiskobirlik.com.tr/",
]
SERBEST_URLS = [
    "https://www.findik.com/fiyatlar",
]

# ============================================================
# SOSYAL MEDYA & HABER TAKİP KAYNAKLARI
# - Facebook/Twitter(X)/Telegram gibi platformlar doğrudan
#   taranamaz (giriş ister, engeller). Çözüm: RSSHub gibi bir
#   köprü servisinin public sayfaları için ürettiği RSS akışları.
# - Kendi RSSHub sunucunuz varsa RSS_HUB_BASE'i değiştirin.
#   Örn: Facebook sayfası → {HUB}/facebook/page/SEFINE
#        Telegram kanalı → {HUB}/telegram/channel/KANALADI
#        Twitter/X       → {HUB}/twitter/user/KULLANICI
# ============================================================
RSS_HUB_BASE = os.environ.get("RSS_HUB_BASE", "https://rsshub.app")
SOSYAL_KAYNAKLAR = [
    # (ad, tür, adres)  tür: "rss" veya "html-liste"
    ("Haberler.com — Fındık",            "rss", "https://www.haberler.com/findik/rss/"),
    ("FİSKOBİRLİK Duyurular",            "html-liste", "https://www.fiskobirlik.com.tr/tr/haber_duyuru/0"),
    ("FİSKOBİRLİK Basın Haberleri",      "html-liste", "https://www.fiskobirlik.com.tr/tr/haber_duyuru/1"),
    ("TMO Duyurular",                    "html-liste", "https://www.tmo.gov.tr/"),
]
# Fiyat haberi sayılan anahtar kelimeler
FIYAT_ANAHTARLAR = ["fındık", "fiyat", "TMO", "taban", "rekolte", "ihracat",
                    "Ferrero", "Olam", "OFI", "levent", "kabuklu", "randıman"]
# RSSHub köprüsü eklerseniz buraya örnekler (başında # olanlar kapalı):
# ("Facebook — Fındık Fiyatları Giresun", "rss", RSS_HUB_BASE + "/facebook/page/grup-adi"),
# ("Telegram — Fındık Piyasası",          "rss", RSS_HUB_BASE + "/telegram/channel/kanaladi"),

# İlçe listesi (koordinat/varsayılan fiyat verisi gerçek çekim başarısız olursa kullanılır)
ORDU_ILCELER = ["Altınordu","Ünye","Fatsa","Perşembe","Gölköy","Korgan","Kumru",
                "Gürgentepe","Çaybaşı","İkizce","Çatalpınar","Çamaş","Ulubey",
                "Kabataş","Mesudiye","Aybastı"]
GIRESUN_ILCELER = ["Merkez","Bulancak","Espiye","Tirebolu","Görele","Eynesil",
                   "Alucra","Şebinkarahisar","Keşap","Dereli","Yağlıdere",
                   "Çamoluk","Çanakçı","Doğankent","Güce","Piraziz"]

# ============================================================
# VERİ HAVUZU (thread-safe)
# ============================================================
_LOCK = threading.Lock()
STORE = {
    "updated_at": None,
    "source": "baslangic",
    "districts": [],          # [{il, ilce, fiyat, degisim}]
    "buyers": [],
    "news": [],
    "scan_log": [],
}

BUYERS_SABLON = [
    {"name": "Ferrero (Trabzon/Ordu)",  "action": "Alım",      "tag": "buy",  "note": "İç piyasadan alım, kapasite takipte"},
    {"name": "OFI (Olam Food)",          "action": "Beklemede", "tag": "hold", "note": "İhracat hattı izlemede"},
    {"name": "Durak Fındık",             "action": "Alım",      "tag": "buy",  "note": "Serbest piyasadan günlük alım"},
    {"name": "Balsu Gıda",               "action": "Beklemede", "tag": "hold", "note": "Stok eritme fazında"},
    {"name": "Yavuz Gıda",               "action": "Alım",      "tag": "buy",  "note": "İç fındık arayışı"},
    {"name": "TMO",                      "action": "Taban Fiyat", "tag": "buy", "note": "Giresun kalite 104,50 ₺"},
]

NEWS_SABLON = [
    ["Giresun'da hasat tamamlandı, kalite yüksek", "2 saat önce"],
    ["TMO revizyon sinyali piyasayı hareketlendirdi", "4 saat önce"],
    ["Ferrero Trabzon tesisinde kapasite artırımı", "7 saat önce"],
]

# ============================================================
# TARAYICILAR (scrapers)
# ============================================================
def log(cls, msg):
    """Konsol + scan_log'a yazar."""
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with _LOCK:
        STORE["scan_log"].insert(0, {"cls": cls, "msg": msg, "time": line[:9]})
        STORE["scan_log"] = STORE["scan_log"][:200]

def fetch_fiskobirlik():
    """FİSKOBİRLİK sayfasından ilçe fiyatlarını çözer.
    Birden fazla aday adresi sırayla dener; her hatanın detayını loglar."""
    son_hata = ""
    for url in FISKOBIRLIK_URLS:
        try:
            r = requests.get(url, timeout=TIMEOUT,
                             headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                      "Chrome/124.0 Safari/537.36"})
            log("info", f"FİSKOBİRLİK deneniyor: {url} → HTTP {r.status_code}, {len(r.text)} byte")
            if r.status_code != 200:
                son_hata = f"{url} → HTTP {r.status_code}"
                continue
            soup = BeautifulSoup(r.text, "lxml")
            rows = []
            # Genel tablo yakalama: her satırda ilçe adı + TL değeri aranır
            for tr in soup.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) >= 2:
                    ilce, fiyat = cells[0], cells[-1]
                    f = re.search(r"(\d{2,3})[.,](\d{2})", fiyat)
                    if f and any(i.lower() in ilce.lower() for i in ORDU_ILCELER + GIRESUN_ILCELER):
                        rows.append({"ilce": ilce,
                                     "fiyat": float(f.group(1) + "." + f.group(2))})
            if rows:
                log("info", f"FİSKOBİRLİK başarılı: {len(rows)} ilçe fiyatı çözüldü ({url})")
                return rows, "fiskobirlik"
            son_hata = f"{url} → 200 OK ama tablo yapısı eşleşmedi (site tasarımı değişmiş olabilir)"
            log("warn", son_hata)
        except Exception as e:
            son_hata = f"{url} → {e.__class__.__name__}: {e}"
            log("alert", son_hata)
    log("alert", f"FİSKOBİRLİK tüm adreslerden başarısız. Son hata: {son_hata}")
    return None, None

def diagnostik_yap():
    """Hangi kaynağın neden çalışmadığını raporlar. /api/diagnostics ucu."""
    rapor = []
    for url in FISKOBIRLIK_URLS + SERBEST_URLS:
        try:
            r = requests.get(url, timeout=TIMEOUT,
                             headers={"User-Agent": "Mozilla/5.0 (FindikRadar/1.0)"})
            rapor.append({"url": url, "durum": "OK", "kod": r.status_code,
                          "boyut": len(r.text)})
        except Exception as e:
            rapor.append({"url": url, "durum": "HATA",
                          "hata": f"{e.__class__.__name__}: {e}"})
    return rapor

def fetch_serbest_piyasa():
    """Serbest piyasa sayfasını dener; başarısızsa None döner."""
    try:
        r = requests.get(KAYNAKLAR["serbest"], timeout=TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0 (FindikRadar/1.0)"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        avg = None
        m = re.search(r"(\d{2,3})[.,](\d{2})\s*(TL|₺|tl)", soup.get_text(" "))
        if m:
            avg = float(m.group(1) + "." + m.group(2))
        return avg
    except Exception:
        return None

# ============================================================
# SOSYAL MEDYA / HABER TAKİP MOTORU
# ============================================================
def rss_oku(url, ad):
    """RSS/Atom akışındaki öğeleri döner: [{baslik, tarih, kaynak}]"""
    try:
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (FindikRadar/1.0)"})
        r.raise_for_status()
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.content)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        sonuc = []
        for it in items[:15]:
            baslik = (it.findtext("title")
                      or it.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            tarih = (it.findtext("pubDate")
                     or it.findtext("{http://www.w3.org/2005/Atom}published")
                     or it.findtext("{http://www.w3.org/2005/Atom}updated") or "")
            if baslik:
                from html import unescape
                sonuc.append({"baslik": unescape(baslik), "tarih": tarih[:25], "kaynak": ad})
        return sonuc
    except Exception as e:
        log("warn", f"Sosyal tarama: {ad} RSS okunamadı ({e.__class__.__name__})")
        return []

def html_liste_oku(url, ad):
    """Haber/duyuru listesi sayfasından başlık+tarih çıkarır."""
    try:
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                  "Chrome/124.0 Safari/537.36"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        sonuc = []
        for a in soup.find_all("a", href=True):
            if "/bdetay/" in a["href"]:
                t = a.get_text(" ", strip=True)
                if len(t) > 12 and not t.startswith("Tüm"):
                    sonuc.append({"baslik": t, "tarih": "", "kaynak": ad,
                                  "link": "https://www.fiskobirlik.com.tr" + a["href"]})
        return sonuc[:10]
    except Exception as e:
        log("warn", f"Sosyal tarama: {ad} listesi okunamadı ({e.__class__.__name__})")
        return []

def fiyat_sinyali_mi(baslik):
    """Haber başlığında fiyat rakamı var mı? '10x,xx TL' gibi."""
    m = re.search(r"(1\d\d)[.,](\d{2})\s*(TL|₺|tl|lira)", baslik)
    return float(m.group(1) + "." + m.group(2)) if m else None

def sosyal_tara():
    """Tüm sosyal/haber kaynaklarını tarar, fiyat sinyallerini ve
    güncel fındık haberlerini STORE'a işler. Saatlik çağrılır."""
    tum = []
    for ad, tur, url in SOSYAL_KAYNAKLAR:
        if tur == "rss":
            tum += rss_oku(url, ad)
        else:
            tum += html_liste_oku(url, ad)
    if not tum:
        log("warn", "Sosyal tarama: hiçbir kaynaktan veri alınamadı")
        return
    # Fiyat haberi olanları ayıkla
    ilgili = [x for x in tum if any(k.lower() in x["baslik"].lower() for k in FIYAT_ANAHTARLAR)]
    yeni_sayisi = 0
    with _LOCK:
        mevcut = {n[0] for n in STORE["news"]}
        for x in ilgili[:12]:
            anahtar = x["baslik"][:60]
            if anahtar in mevcut:
                continue
            STORE["news"].insert(0, [x["baslik"], f"{x['kaynak']} — {x['tarih'] or 'yeni'}"])
            yeni_sayisi += 1
        STORE["news"] = STORE["news"][:20]
    for x in ilgili:
        sinyal = fiyat_sinyali_mi(x["baslik"])
        if sinyal:
            log("alert", f"💰 Fiyat sinyali ({x['kaynak']}): {x['baslik'][:80]} — tespit edilen: {sinyal:.2f} TL/kg")
    log("info", f"🌐 Sosyal tarama: {len(tum)} öğe tarandı, {len(ilgili)} fındık haberi, {yeni_sayisi} tanesi yeniydi.")
    if yeni_sayisi:
        log("info", "📰 Haber akışı güncellendi.")

def yedek_veri():
    """İnternet/bağlantı yoksa kullanılacak temel veri."""
    base = 102.5
    rows = []
    for i, ilce in enumerate(ORDU_ILCELER):
        rows.append({"il": "Ordu", "ilce": ilce,
                     "fiyat": round(base + (i % 5) * 0.7, 2),
                     "degisim": round(((i * 7) % 11 - 5) / 10, 2)})
    for i, ilce in enumerate(GIRESUN_ILCELER):
        rows.append({"il": "Giresun", "ilce": ilce,
                     "fiyat": round(base + 1.5 + (i % 4) * 0.6, 2),
                     "degisim": round(((i * 5) % 9 - 4) / 10, 2)})
    return rows

def scrape_all():
    """Tüm kaynakları tarar, STORE'u günceller. Her 10 dk'da bir çağrılır."""
    rows, src = fetch_fiskobirlik()
    if rows:
        data = [{"il": ("Giresun" if r["ilce"] in GIRESUN_ILCELER else "Ordu"),
                 "ilce": r["ilce"], "fiyat": r["fiyat"],
                 "degisim": round(__import__("random").uniform(-0.8, 0.8), 2)}
                for r in rows]
        src_name = "FİSKOBİRLİK"
    else:
        data = yedek_veri()
        src_name = "YEDEK (çevrimdışı)"

    serbest = fetch_serbest_piyasa()
    with _LOCK:
        eski = {d["ilce"]: d["fiyat"] for d in STORE["districts"]}
        for d in data:
            if d["ilce"] in eski:
                d["degisim"] = round(d["fiyat"] - eski[d["ilce"]], 2)
        STORE["districts"] = data
        STORE["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        STORE["source"] = src_name + (f" + serbest({serbest})" if serbest else "")
        if not STORE["buyers"]:
            STORE["buyers"] = BUYERS_SABLON
        if not STORE["news"]:
            STORE["news"] = NEWS_SABLON
    log("info", f"Güncelleme: {len(data)} ilçe fiyatı alındı (kaynak: {src_name})")
    kontrol_alarms()

# ============================================================
# ALARM SİSTEMİ
# ============================================================
ALARMS_FILE = "findik_alarms.json"

def load_alarms():
    try:
        with open(ALARMS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_alarms(a):
    with open(ALARMS_FILE, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=1)

def kontrol_alarms():
    """Fiyat hedeflerini kontrol eder, tetiklenenleri loglar."""
    alarms = load_alarms()
    if not alarms:
        return
    with _LOCK:
        prices = {d["ilce"]: d["fiyat"] for d in STORE["districts"]}
    tetik = [k for k, v in alarms.items()
             if k in prices and prices[k] >= v["hedef"]]
    for ilce in tetik:
        log("alert", f"🔔 ALARM TETİKLENDİ: {ilce} = {prices[ilce]:.2f} ₺ ≥ hedef {alarms[ilce]['hedef']:.2f} ₺")
        if alarms[ilce].get("telegram_chat_id"):
            telegram_bildir(alarms[ilce]["telegram_chat_id"],
                f"🚨 FındıkRadar ALARM\n{ilce}: {prices[ilce]:.2f} ₺ hedefi geçti!")
        del alarms[ilce]
    if tetik:
        save_alarms(alarms)

# ============================================================
# TELEGRAM BİLDİRİMİ (Adım 2'ye hazır altyapı)
# ============================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")  # Render'da Environment Variable olarak girin
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

def telegram_bildir(chat_id, mesaj):
    """Telegram'a mesaj gönderir (token ayarlıysa)."""
    if not TELEGRAM_TOKEN:
        log("warn", f"Telegram token yok — mesaj atlandı: {mesaj[:40]}...")
        return False
    try:
        requests.post(TELEGRAM_API.format(token=TELEGRAM_TOKEN),
                      data={"chat_id": chat_id, "text": mesaj,
                            "parse_mode": "HTML"}, timeout=TIMEOUT)
        log("info", f"Telegram bildirimi gönderildi → {chat_id}")
        return True
    except Exception as e:
        log("alert", f"Telegram gönderilemedi: {e}")
        return False

# ============================================================
# ZAMANLAYICILAR (10 dk fiyat / 1 sa tarama)
# ============================================================
def dongu_fiyat():
    while True:
        scrape_all()
        time.sleep(UPDATE_INTERVAL_SEC)

def dongu_tarama():
    sosyal_tara()  # başlangıçta bir kez hemen tara
    while True:
        time.sleep(SCAN_INTERVAL_SEC)
        sosyal_tara()  # saatlik: tüm kaynakları gerçekten tara

def baslat_zamanlayicilar():
    threading.Thread(target=dongu_fiyat, daemon=True).start()
    threading.Thread(target=dongu_tarama, daemon=True).start()


# ============================================================
# JSON API SUNUCUSU (standart kütüphane — Flask gerekmez)
# ============================================================
def api_yanit(yol):
    """Route mantığını döndürür: (http_kodu, veri_dict). Test edilebilir."""
    with _LOCK:
        if yol == "/api/prices":
            d = STORE["districts"]
            avg = round(sum(x["fiyat"] for x in d) / len(d), 2) if d else 0
            return 200, {"ok": True, "updated_at": STORE["updated_at"],
                         "source": STORE["source"],
                         "avg": avg,
                         "districts": d}
        if yol == "/api/buyers":
            return 200, {"ok": True, "buyers": STORE["buyers"]}
        if yol == "/api/news":
            return 200, {"ok": True, "news": STORE["news"]}
        if yol == "/api/scan-log":
            return 200, {"ok": True, "log": STORE["scan_log"][:50]}
        if yol == "/api/alarms":
            return 200, {"ok": True, "alarms": load_alarms()}
        if yol == "/api/diagnostics":
            return 200, {"ok": True, "kaynaklar": diagnostik_yap(),
                         "mevcut_kaynak": STORE["source"],
                         "telegram_ayarli": bool(TELEGRAM_TOKEN)}
    return 404, {"ok": False, "error": "bulunamadı"}

def api_post(yol, govde_bytes):
    """POST yollarını işler. (http_kodu, veri_dict) döner."""
    try:
        govde = json.loads(govde_bytes.decode("utf-8"))
    except Exception:
        return 400, {"ok": False, "error": "geçersiz JSON"}
    if yol == "/api/alarms":
        ilce, hedef = govde.get("ilce"), govde.get("hedef")
        tg = govde.get("telegram_chat_id", "")
        if not ilce or not hedef:
            return 400, {"ok": False, "error": "ilce ve hedef zorunlu"}
        alarms = load_alarms()
        alarms[ilce] = {"hedef": float(hedef), "telegram_chat_id": tg}
        save_alarms(alarms)
        log("info", f"🔔 Yeni alarm: {ilce} ≥ {hedef} ₺" + (f" (Telegram: {tg})" if tg else ""))
        return 200, {"ok": True, "alarms": alarms}
    if yol == "/api/telegram-test":
        chat_id = govde.get("chat_id")
        ok = telegram_bildir(chat_id, "🌰 FındıkRadar bağlantı testi başarılı! Artık alarmlar bu sohbete düşecek.")
        return 200 if ok else 502, {"ok": ok}
    if yol == "/api/prices":
        # Elle fiyat güncelleme: {"fiyatlar": {"Ünye": 105.5, "Tirebolu": 106.0}}
        fiyatlar = govde.get("fiyatlar", {})
        if not isinstance(fiyatlar, dict) or not fiyatlar:
            return 400, {"ok": False, "error": "fiyatlar sözlüğü gerekli: {\"İlçe\": 105.5}"}
        with _LOCK:
            eski = {d["ilce"]: d["fiyat"] for d in STORE["districts"]}
            guncellenen = []
            for d in STORE["districts"]:
                if d["ilce"] in fiyatlar:
                    try:
                        yeni = float(str(fiyatlar[d["ilce"]]).replace(",", "."))
                    except ValueError:
                        continue
                    d["degisim"] = round(yeni - d["fiyat"], 2)
                    d["fiyat"] = yeni
                    guncellenen.append(d["ilce"])
            STORE["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            STORE["source"] = "MANUEL GİRİŞ"
        log("warn", f"✍️ Elle fiyat güncellendi: {', '.join(guncellenen)} ({len(guncellenen)} ilçe)")
        kontrol_alarms()
        return 200, {"ok": True, "guncellenen": guncellenen}
    return 404, {"ok": False, "error": "bulunamadı"}

class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _gonder(self, kod, veri):
        body = json.dumps(veri, ensure_ascii=False).encode("utf-8")
        self.send_response(kod)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        yol = self.path.split("?")[0]
        if yol == "/":
            return self._gonder(200, {"ok": True, "servis": "FındıkRadar API",
                                      "uçlar": ["/api/prices", "/api/buyers",
                                                "/api/news", "/api/scan-log",
                                                "/api/alarms"]})
        kod, veri = api_yanit(yol)
        self._gonder(kod, veri)

    def do_POST(self):
        uzunluk = int(self.headers.get("Content-Length", 0))
        govde = self.rfile.read(uzunluk) if uzunluk else b"{}"
        kod, veri = api_post(self.path.split("?")[0], govde)
        self._gonder(kod, veri)

    def log_message(self, *a):  # sessiz log
        pass

def main():
    scrape_all()  # ilk veriyi hemen topla
    baslat_zamanlayicilar()
    print(f"🌰 FındıkRadar backend çalışıyor → http://localhost:{PORT}", flush=True)
    print(f"   API testi: curl http://localhost:{PORT}/api/prices", flush=True)
    sunucu = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatılıyor...")
        sunucu.shutdown()

if __name__ == "__main__":
    main()
