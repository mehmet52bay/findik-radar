# 🌰 FındıkRadar — Bulut Kurulum Rehberi (Render.com, ücretsiz)

## 1. Backend'i Render'a kur (7/24 çalışsın)

1. https://render.com adresine GitHub hesabınızla giriş yapın (ücretsiz)
2. Bu klasördeki dosyaları bir **GitHub reposuna** yükleyin:
   - `findik_backend.py`, `requirements.txt`, `Procfile` (render.yaml da varsa otomatik tanınır)
   - HTML dosyasını da isterseniz yükleyin, ama zorunlu değil
3. Render panelinde **"New → Web Service"** → reponuzu seçin
4. Render otomatik algılar: Python + `pip install -r requirements.txt` + `python3 findik_backend.py`
5. **Deploy**'a basın. 2-3 dakika sonra size şöyle bir adres verir:
   `https://findik-radar.onrender.com` ✅

## 2. Telefondan test edin

Telefon tarayıcısında şu adrese gidin:
```
https://findik-radar.onrender.com/api/prices
```
JSON veri görürseniz backend canlı! 🎉

## 3. Web sayfasını buluta bağlayın

Telefonda `findik_takip.html` dosyasını açın ve tarayıcı konsolunda (ya da dosyayı düzenleyip):
```js
const API = localStorage.getItem('findik_api') || "http://localhost:8000";
```
satırını şöyle değiştirin:
```js
const API = localStorage.getItem('findik_api') || "https://findik-radar.onrender.com";
```
Alternatif: sayfayı açtıktan sonra konsola şunu yazın (kalıcı olur):
```js
localStorage.setItem('findik_api', 'https://findik-radar.onrender.com');
location.reload();
```

## 4. Telegram bildirimi (Adım 2 — telefona mesaj)

1. Telegram'da **@BotFather** → `/newbot` → isim verin → size TOKEN verir (örn. `7123456:AAHf...`)
2. Render panelinde: **Environment → Add Environment Variable**:
   - Key: `TELEGRAM_TOKEN`  Value: BotFather'ın verdiği token
   - **Save** → otomatik yeniden başlar
3. Telegram'da kendi botunuza bir mesaj atın, sonra şu adrese tarayıcıdan gidin:
   `https://api.telegram.org/botTOKENINIZ/getUpdates`
   - Çıkan `"chat":{"id":123456789` → bu numara sizin **chat ID**'niz
4. Sayfadaki alarm penceresine hedef fiyatın yanına chat ID'yi yazıp kaydedin
5. Fiyat hedefe ulaşınca mesaj anında telefonunuza düşer! 🚨

## 5. Sorun giderme

- **Backend uyanık kalmıyor:** Render ücretsiz planda 15 dk hareketsizlikte uyur.
  Çözüm: telefondan `https://findik-radar.onrender.com/api/prices` adresine
  10 dakikada bir istek atan küçük bir "canlı tutucu" yeterli (veya ücretli planda sleep yok).
- **CORS hatası:** Backend zaten `Access-Control-Allow-Origin: *` gönderiyor, sorun olmaz.
- **Telegram token girilince bildirim gitmiyor:** Render loglarından (Logs sekmesi)
  `Telegram gönderilemedi` satırını kontrol edin; token/chat ID yanlış olabilir.